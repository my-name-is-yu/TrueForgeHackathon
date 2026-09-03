from __future__ import annotations

import asyncio
import hashlib
import inspect
import io
import json
import math
import re
import tempfile
import time
import uuid
import zipfile
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from asset_autopsy.storage import MAX_OBJECT_BYTES

from .artifacts import ArtifactStoreError, SessionArtifactStore
from .maker_pack import (
    MakerPackResult,
    QualifiedBuildInstructions,
    generate_maker_pack_artifacts,
)
from .physical_evidence import EvidenceSignatureVerifier, PhysicalEvidenceRecord
from .project_store import (
    PROJECT_MANIFEST_LIMIT,
    PROJECT_REVISION_LIMIT,
    ProjectAlreadyExistsError,
    PersistedDraft,
    PersistedRevision,
    ProjectConflictError,
    ProjectNotFoundError,
    ProjectSnapshot,
    ProjectStore,
    ProjectStoreError,
    validate_portable_project_bytes,
)
from .runtime import RuntimeCatalog
from .schemas import (
    SCHEMA_VERSION,
    TOOL_NAMES,
    AddMorphologyNodeEdit,
    ArtifactDescriptor,
    ArtifactManifest,
    CharacterRobotSpec,
    CreateRevisionFromDraftInput,
    CreateRevisionFromDraftOutput,
    DraftSnapshot,
    DraftTarget,
    GetStudioContextInput,
    GetStudioContextOutput,
    InspectDesignInput,
    InspectDesignOutput,
    NodeSummary,
    PositiveVec3,
    PrepareBuildPackInput,
    PrepareBuildPackOutput,
    PreviewScenarioInput,
    PreviewScenarioOutput,
    ProfileSummary,
    RemoveMorphologyNodeEdit,
    ReplaceMorphologyNodeEdit,
    RevisionSummary,
    RevisionTarget,
    ReviseDesignDraftInput,
    ReviseDesignDraftOutput,
    SetAppearanceEdit,
    SetBehaviorEdit,
    SetConstraintsEdit,
    SetDesignDraftInput,
    SetDesignDraftOutput,
    SetFaceEdit,
    SetHardwareProfileEdit,
    SetIdentityEdit,
    SetManufacturingEdit,
    SetPersonalityEdit,
    StudioRunSummary,
    StudioSelectionInput,
    ValidateDesignInput,
    ValidateDesignOutput,
    ValidationIssue,
    ValidationReport,
)
from .simulation import (
    SIMULATION_COMPILER_VERSION,
    MotionSimulationResult,
    SimulationError,
    run_motion_checks,
)


_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_ISSUE_CODE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ISSUE_PATH = re.compile(r"^[A-Za-z0-9_.\[\]-]+$")
_MODEL = TypeVar("_MODEL", bound=BaseModel)
_DEFAULT_CAD_COMPILER = object()
_MAX_COMPILE_CACHE_ENTRIES = 32
_EVIDENCE_POLICY = {
    "concept_only": "The design is a bounded concept and has not passed digital checks.",
    "digital_checks_passed": "The exact digital inputs passed the available deterministic checks.",
    "within_qualified_profile": "The design stays within a physically tested hardware profile.",
    "exact_build_verified": "This exact build-affecting artifact subject has recorded physical verification.",
}


class ProfileRegistryProtocol(Protocol):
    def list_profiles(self) -> Sequence[object]: ...

    def get_profile(self, profile_id: str) -> object: ...


class CadCompilerProtocol(Protocol):
    def compile(
        self, spec: CharacterRobotSpec, profile: object | None = None
    ) -> object: ...


class DomainError(RuntimeError):
    __slots__ = (
        "code",
        "safe_message",
        "retryable",
        "request_id",
        "http_status",
        "next_action",
    )

    def __init__(
        self,
        *,
        code: str,
        safe_message: str,
        retryable: bool,
        request_id: str,
        http_status: int,
        next_action: str,
    ) -> None:
        if _ERROR_CODE.fullmatch(code) is None:
            raise ValueError("domain error code is invalid")
        if not request_id.startswith("req_"):
            raise ValueError("domain error request ID is invalid")
        if http_status < 400 or http_status > 599:
            raise ValueError("domain error HTTP status is invalid")
        for value in (safe_message, next_action):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= 240
                or any(char in value for char in ("\x00", "\n", "\r"))
            ):
                raise ValueError("domain error text is invalid")
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = bool(retryable)
        self.request_id = request_id
        self.http_status = http_status
        self.next_action = next_action

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.safe_message,
            "retryable": self.retryable,
            "request_id": self.request_id,
            "next_action": self.next_action,
        }


@dataclass(frozen=True, slots=True)
class _Draft:
    spec: CharacterRobotSpec
    base_revision_id: str | None
    draft_hash: str
    spec_hash: str


@dataclass(frozen=True, slots=True)
class _Revision:
    summary: RevisionSummary
    spec: CharacterRobotSpec


@dataclass(frozen=True, slots=True)
class _CompiledArtifact:
    descriptor: ArtifactDescriptor
    content: bytes


@dataclass(frozen=True, slots=True)
class _DriveWheelGeometry:
    track_mm: float
    width_mm: float
    radius_mm: float


@dataclass(frozen=True, slots=True)
class _CompileView:
    dimensions_mm: PositiveVec3
    drive_wheels: _DriveWheelGeometry
    geometry_sha256: str
    compiler_version: str | None
    cad_engine_version: str | None
    profile_id: str | None
    artifacts: tuple[ArtifactDescriptor, ...]
    issues: tuple[ValidationIssue, ...]


class _BuildPackArchiveTooLarge(ValueError):
    pass


class _BoundedBytesIO(io.BytesIO):
    def __init__(self, maximum_bytes: int) -> None:
        super().__init__()
        if maximum_bytes < 0:
            raise ValueError("archive byte budget cannot be negative")
        self.maximum_bytes = maximum_bytes

    def write(self, data: bytes) -> int:
        if self.tell() + len(data) > self.maximum_bytes:
            raise _BuildPackArchiveTooLarge(
                "normalized Build Pack exceeds its artifact byte budget"
            )
        return super().write(data)


class CharacterRobotService:
    """Pure, session-scoped Character Robot Studio domain service.

    Generated bytes use a content-addressed ObjectStore. When a ProjectStore is
    supplied, the canonical draft, immutable revisions, run summaries, and artifact
    manifests are atomically durable while downloads remain human-initiated.
    """

    def __init__(
        self,
        data_root: Path | None = None,
        *,
        project_id: str = "studio",
        profile_registry: ProfileRegistryProtocol | None = None,
        cad_compiler: CadCompilerProtocol | None | object = _DEFAULT_CAD_COMPILER,
        project_store: ProjectStore | None = None,
        manufacturing_validator: (
            Callable[[CharacterRobotSpec, object], object] | None
        ) = None,
        physical_records: Sequence[PhysicalEvidenceRecord] = (),
        evidence_verifier: EvidenceSignatureVerifier | None = None,
        exact_build_subject_sha256: str | None = None,
        runtime_catalog: RuntimeCatalog | None = None,
        qualified_build_instructions: QualifiedBuildInstructions | None = None,
    ) -> None:
        self._temporary_data_root: tempfile.TemporaryDirectory[str] | None = None
        if data_root is None:
            self._temporary_data_root = tempfile.TemporaryDirectory(
                prefix="character-robot-service-"
            )
            data_root = Path(self._temporary_data_root.name)
        self.data_root = Path(data_root)
        self.project_id = project_id
        if profile_registry is None:
            from .profiles import ProfileRegistry

            profile_registry = ProfileRegistry()
        if cad_compiler is _DEFAULT_CAD_COMPILER:
            from .cad import CadCompiler

            cad_compiler = CadCompiler(profile_registry)
        self.profile_registry = profile_registry
        self.cad_compiler = cad_compiler
        self.project_store = project_store
        self.manufacturing_validator = manufacturing_validator
        self.physical_records = tuple(physical_records)
        self.evidence_verifier = evidence_verifier
        self.exact_build_subject_sha256 = exact_build_subject_sha256
        self.runtime_catalog = runtime_catalog
        self.qualified_build_instructions = qualified_build_instructions
        self._lock = asyncio.Lock()
        self._draft: _Draft | None = None
        self._head_revision_id: str | None = None
        self._revisions: list[_Revision] = []
        self._compile_cache: OrderedDict[str, _CompileView] = OrderedDict()
        self._simulation_cache: OrderedDict[str, MotionSimulationResult] = OrderedDict()
        self._artifacts = SessionArtifactStore(self.data_root)
        self._latest_validation: ValidationReport | None = None
        self._recent_runs: list[StudioRunSummary] = []
        self._artifact_manifests: list[ArtifactManifest] = []
        self._project_generation = 0
        self._persisted_snapshot: ProjectSnapshot | None = None
        self._invocations = {name: 0 for name in TOOL_NAMES}
        if self.project_store is not None:
            self._load_or_create_project()

    @property
    def tool_names(self) -> tuple[str, ...]:
        return TOOL_NAMES

    @property
    def invocation_counts(self) -> Mapping[str, int]:
        return MappingProxyType(dict(self._invocations))

    @property
    def project_generation(self) -> int:
        return self._project_generation

    def _load_or_create_project(self) -> None:
        assert self.project_store is not None
        try:
            snapshot = self.project_store.load_project(self.project_id)
        except ProjectNotFoundError:
            try:
                snapshot = self.project_store.create_project(self.project_id)
            except ProjectAlreadyExistsError:
                snapshot = self.project_store.load_project(self.project_id)
        self._hydrate(snapshot)

    def _hydrate(self, snapshot: ProjectSnapshot) -> None:
        self._persisted_snapshot = snapshot
        self._project_generation = snapshot.generation
        self._head_revision_id = snapshot.head_revision_id
        self._draft = (
            _Draft(
                spec=snapshot.draft.spec,
                base_revision_id=snapshot.draft.base_revision_id,
                draft_hash=snapshot.draft.draft_hash,
                spec_hash=snapshot.draft.spec_hash,
            )
            if snapshot.draft is not None
            else None
        )
        self._revisions = [
            _Revision(summary=revision.summary, spec=revision.spec)
            for revision in snapshot.revisions
        ]
        self._recent_runs = list(snapshot.recent_runs)
        self._artifact_manifests = list(snapshot.artifact_manifests)
        self._latest_validation = None
        self._compile_cache.clear()
        self._simulation_cache.clear()
        self._artifacts.restore(
            [
                descriptor
                for manifest in self._artifact_manifests
                for descriptor in manifest.artifacts
            ]
        )

    def _snapshot(self) -> ProjectSnapshot:
        return ProjectSnapshot(
            project_id=self.project_id,
            generation=self._project_generation,
            head_revision_id=self._head_revision_id,
            draft=(
                PersistedDraft(
                    base_revision_id=self._draft.base_revision_id,
                    draft_hash=self._draft.draft_hash,
                    spec_hash=self._draft.spec_hash,
                    spec=self._draft.spec,
                )
                if self._draft is not None
                else None
            ),
            revisions=[
                PersistedRevision(summary=revision.summary, spec=revision.spec)
                for revision in self._revisions
            ],
            recent_runs=list(self._recent_runs),
            artifact_manifests=list(self._artifact_manifests),
        )

    def _persist(self, request_id: str) -> None:
        if self.project_store is None:
            self._project_generation += 1
            return
        try:
            saved = self.project_store.save_project(
                self._snapshot(), expected_generation=self._project_generation
            )
        except ProjectConflictError:
            try:
                self._hydrate(self.project_store.load_project(self.project_id))
            except ProjectStoreError:
                pass
            raise self._error(
                request_id,
                "STALE_PROJECT",
                "The durable project changed after this Studio instance read it.",
                True,
                409,
                "Refresh the Studio and reapply the intended change to the current project generation.",
            ) from None
        except ProjectStoreError:
            if self._persisted_snapshot is not None:
                self._hydrate(self._persisted_snapshot)
            raise self._error(
                request_id,
                "PROJECT_STORAGE_FAILED",
                "The durable Character Robot project could not be saved.",
                True,
                503,
                "Keep this Studio open and retry after checking its project storage.",
            ) from None
        self._project_generation = saved.generation
        self._persisted_snapshot = saved

    def artifact_bytes(self, sha256: str) -> bytes:
        """Return an artifact to a human-authorized transport outside WebMCP."""

        return self.read_artifact(sha256)[0]

    def read_artifact(self, sha256: str) -> tuple[bytes, str, str]:
        """Read bytes and safe metadata for a visible human download endpoint."""

        request_id = f"req_{uuid.uuid4().hex}"
        try:
            content, descriptor = self._artifacts.read(sha256)
        except (ArtifactStoreError, TypeError, ValueError):
            raise self._error(
                request_id,
                "ARTIFACT_NOT_FOUND",
                "The requested artifact is not available in this Studio session.",
                False,
                404,
                "Regenerate the preview or build manifest and use its current digest.",
            ) from None
        return content, descriptor.media_type, descriptor.file_name

    def restore_portable_project(self, snapshot: ProjectSnapshot) -> ProjectSnapshot:
        """Restore a validated human import into a newly created blank session."""

        if self._draft is not None or self._revisions or self._artifact_manifests:
            raise ProjectStoreError("portable imports require a blank project session")
        candidate = snapshot.model_copy(
            update={
                "project_id": self.project_id,
                "generation": self._project_generation,
            }
        )
        if self.project_store is not None:
            saved = self.project_store.save_project(
                candidate, expected_generation=self._project_generation
            )
            self._hydrate(saved)
            return saved
        self._hydrate(candidate)
        return candidate

    async def get_studio_context(
        self, value: GetStudioContextInput
    ) -> GetStudioContextOutput:
        request_id = self._begin("get_studio_context", value, GetStudioContextInput)
        async with self._lock:
            profiles = [
                self._profile_summary(profile, request_id)
                for profile in self._list_profiles(request_id)
            ]
            current_spec = self._head_revision().spec if self._revisions else None
            head_spec_sha256 = (
                self._head_revision().summary.spec_hash if self._revisions else None
            )
            active_spec = self._draft.spec if self._draft is not None else current_spec
            active_spec_hash = (
                _model_hash(active_spec) if active_spec is not None else None
            )
            active_preview = (
                self._preview_artifact(active_spec, request_id)
                if active_spec is not None
                else None
            )
            if active_spec is not None and active_preview is None:
                try:
                    restored_compile = await self._compile(active_spec, request_id)
                except DomainError as error:
                    if error.code in {"PROJECT_STORAGE_FAILED", "STALE_PROJECT"}:
                        raise
                else:
                    active_preview = self._artifact_of_kind(restored_compile, "glb")
                    self._persist(request_id)
            draft = None
            if self._draft is not None:
                draft = DraftSnapshot(
                    base_revision_id=self._draft.base_revision_id,
                    draft_hash=self._draft.draft_hash,
                    spec_hash=self._draft.spec_hash,
                    spec=self._draft.spec,
                    preview_artifact=active_preview,
                )
            history = (
                [revision.summary for revision in self._revisions]
                if value.include_revision_history
                else []
            )
            latest_validation = (
                self._latest_validation
                if self._latest_validation is not None
                and self._latest_validation.spec_hash == active_spec_hash
                else None
            )
            return GetStudioContextOutput(
                schema_version=SCHEMA_VERSION,
                request_id=request_id,
                project_id=self.project_id,
                project_generation=self._project_generation,
                storage_mode=(
                    "durable" if self.project_store is not None else "ephemeral"
                ),
                artifact_manifest_count=len(self._artifact_manifests),
                tool_names=list(TOOL_NAMES),
                head_revision_id=self._head_revision_id,
                head_spec_sha256=head_spec_sha256,
                current_spec=current_spec,
                current_preview_artifact=(
                    active_preview
                    if current_spec is not None
                    and _model_hash(current_spec) == active_spec_hash
                    else (
                        self._preview_artifact(current_spec, request_id)
                        if current_spec is not None
                        else None
                    )
                ),
                draft=draft,
                hardware_profiles=profiles,
                supported_scenarios=[
                    "idle",
                    "greet",
                    "listen",
                    "think",
                    "delight",
                    "sleep",
                ],
                revision_history=history,
                latest_validation=latest_validation,
                recent_runs=list(self._recent_runs),
                selected_node_id=None,
                evidence_policy=dict(_EVIDENCE_POLICY),
            )

    async def set_design_draft(
        self, value: SetDesignDraftInput
    ) -> SetDesignDraftOutput:
        request_id = self._begin("set_design_draft", value, SetDesignDraftInput)
        async with self._lock:
            self._require_head(value.expected_revision, request_id)
            self._require_draft_replacement(value.expected_draft_hash, request_id)
            compiled = await self._compile(value.spec, request_id)
            # Compile telemetry is an independent durable record when a later
            # design constraint rejects the candidate.
            self._persist(request_id)
            self._require_within_maximum_dimensions(value.spec, compiled, request_id)
            self._draft = self._make_draft(value.spec)
            self._latest_validation = None
            self._persist(request_id)
            return SetDesignDraftOutput(
                **self._draft_output(
                    self._draft,
                    request_id,
                    changed_node_ids=[
                        node.node_id for node in value.spec.morphology.nodes
                    ],
                    changed_sections=[
                        "identity",
                        "hardware_profile",
                        "appearance",
                        "morphology",
                        "personality",
                        "face",
                        "behavior",
                        "manufacturing",
                        "constraints",
                    ],
                    compiled=compiled,
                )
            )

    async def revise_design_draft(
        self, value: ReviseDesignDraftInput
    ) -> ReviseDesignDraftOutput:
        request_id = self._begin("revise_design_draft", value, ReviseDesignDraftInput)
        async with self._lock:
            draft = self._require_draft(value.draft_hash, request_id)
            payload = draft.spec.model_dump(mode="json")
            nodes = {
                node.node_id: node.model_dump(mode="json")
                for node in draft.spec.morphology.nodes
            }
            changed_nodes: list[str] = []
            changed_sections: list[str] = []

            for edit in value.edits:
                section, node_id = self._apply_edit(
                    payload, nodes, edit, draft.spec, request_id
                )
                if section not in changed_sections:
                    changed_sections.append(section)
                if node_id is not None and node_id not in changed_nodes:
                    changed_nodes.append(node_id)
            payload["morphology"] = {"nodes": list(nodes.values())}
            try:
                candidate = CharacterRobotSpec.model_validate(payload)
            except ValidationError:
                raise self._error(
                    request_id,
                    "INVALID_SEMANTIC_EDIT",
                    "The semantic edits would create an invalid robot specification.",
                    False,
                    422,
                    "Inspect the current design and submit a smaller bounded edit.",
                ) from None

            compiled = await self._compile(candidate, request_id)
            self._persist(request_id)
            self._require_within_maximum_dimensions(candidate, compiled, request_id)
            self._draft = self._make_draft(candidate)
            self._latest_validation = None
            self._persist(request_id)
            return ReviseDesignDraftOutput(
                **self._draft_output(
                    self._draft,
                    request_id,
                    changed_node_ids=changed_nodes,
                    changed_sections=changed_sections,
                    compiled=compiled,
                )
            )

    async def inspect_design(self, value: InspectDesignInput) -> InspectDesignOutput:
        request_id = self._begin("inspect_design", value, InspectDesignInput)
        async with self._lock:
            spec = self._target_spec(value.target, request_id)
            compiled = await self._compile(spec, request_id)
            nodes = [
                NodeSummary(
                    node_id=node.node_id,
                    role=node.role,
                    label=node.label,
                    kind=node.kind,
                    parent_node_id=(
                        node.attachment.parent_node_id
                        if node.attachment is not None
                        else None
                    ),
                    node_hash=_model_hash(node),
                )
                for node in spec.morphology.nodes
            ]
            result = InspectDesignOutput(
                schema_version=SCHEMA_VERSION,
                request_id=request_id,
                target=value.target,
                spec_hash=_model_hash(spec),
                spec=spec,
                nodes=nodes,
                dimensions_mm=compiled.dimensions_mm if compiled else None,
                geometry_sha256=compiled.geometry_sha256 if compiled else None,
                warnings=(
                    []
                    if compiled is not None
                    else ["CAD preview is unavailable until a compiler is connected."]
                ),
            )
            self._persist(request_id)
            return result

    async def preview_scenario(
        self, value: PreviewScenarioInput
    ) -> PreviewScenarioOutput:
        request_id = self._begin("preview_scenario", value, PreviewScenarioInput)
        async with self._lock:
            spec = self._target_spec(value.target, request_id)
            scenario = next(
                (
                    candidate
                    for candidate in spec.behavior.scenarios
                    if candidate.scenario_id == value.scenario_id
                ),
                None,
            )
            if scenario is None:
                raise self._error(
                    request_id,
                    "SCENARIO_NOT_DEFINED",
                    "The selected design does not define that preview scenario.",
                    False,
                    404,
                    "Inspect the behavior graph and add the scenario before previewing it.",
                )
            compiled = await self._compile(spec, request_id)
            preview = self._artifact_of_kind(compiled, "glb")
            result = PreviewScenarioOutput(
                schema_version=SCHEMA_VERSION,
                request_id=request_id,
                target=value.target,
                spec_hash=_model_hash(spec),
                scenario_id=scenario.scenario_id,
                duration_ms=scenario.duration_ms,
                keyframes=scenario.keyframes,
                preview_artifact=preview,
                warnings=(
                    []
                    if preview is not None
                    else [
                        "The motion timeline is available, but no GLB preview was compiled."
                    ]
                ),
            )
            self._persist(request_id)
            return result

    async def validate_design(self, value: ValidateDesignInput) -> ValidateDesignOutput:
        request_id = self._begin("validate_design", value, ValidateDesignInput)
        async with self._lock:
            spec = self._target_spec(value.target, request_id)
            report, _compiled, _simulation = await self._validate_spec(spec, request_id)
            result = ValidateDesignOutput(
                schema_version=SCHEMA_VERSION,
                request_id=request_id,
                target=value.target,
                report=report,
            )
            self._persist(request_id)
            return result

    async def create_revision_from_draft(
        self, value: CreateRevisionFromDraftInput
    ) -> CreateRevisionFromDraftOutput:
        request_id = self._begin(
            "create_revision_from_draft", value, CreateRevisionFromDraftInput
        )
        async with self._lock:
            self._require_head(value.expected_revision, request_id)
            draft = self._require_draft(value.draft_hash, request_id)
            if draft.base_revision_id != self._head_revision_id:
                raise self._stale_revision(request_id)
            if (
                self._revisions
                and self._revisions[-1].summary.spec_hash == draft.spec_hash
            ):
                raise self._error(
                    request_id,
                    "REVISION_HAS_NO_CHANGES",
                    "The draft is identical to the current committed revision.",
                    False,
                    409,
                    "Revise the draft before creating another revision.",
                )
            if len(self._revisions) >= PROJECT_REVISION_LIMIT:
                raise self._error(
                    request_id,
                    "REVISION_LIMIT_REACHED",
                    "This project has reached its immutable revision limit.",
                    False,
                    409,
                    "Export this project and start a new Studio project before committing more revisions.",
                )

            ordinal = len(self._revisions)
            revision_id = f"r{ordinal:03d}"
            summary = RevisionSummary(
                revision_id=revision_id,
                parent_revision_id=self._head_revision_id,
                ordinal=ordinal,
                spec_hash=draft.spec_hash,
                note=value.note,
                created_at=datetime.now(UTC).isoformat(timespec="microseconds"),
            )
            self._revisions.append(_Revision(summary=summary, spec=draft.spec))
            self._head_revision_id = revision_id
            self._draft = self._make_draft(draft.spec)
            result = CreateRevisionFromDraftOutput(
                schema_version=SCHEMA_VERSION,
                request_id=request_id,
                revision=summary,
                head_revision_id=revision_id,
                draft_hash=self._draft.draft_hash,
            )
            self._persist(request_id)
            return result

    async def validate_selection(self, value: StudioSelectionInput) -> None:
        """Validate a human UI selection without adding a WebMCP operation."""

        request_id = f"req_{uuid.uuid4().hex}"
        if not isinstance(value, StudioSelectionInput):
            raise self._error(
                request_id,
                "INVALID_SELECTION_INPUT",
                "The selection did not match its strict public schema.",
                False,
                400,
                "Refresh the Studio and select a part from the current design.",
            )
        async with self._lock:
            active_target = self._active_target()
            if active_target is None or value.target != active_target:
                raise self._error(
                    request_id,
                    "SELECTION_TARGET_STALE",
                    "The selected design target is no longer active.",
                    True,
                    409,
                    "Refresh the Studio and select a part from the current design.",
                )
            spec = self._target_spec(value.target, request_id)
            if value.node_id is not None and not any(
                node.node_id == value.node_id for node in spec.morphology.nodes
            ):
                raise self._error(
                    request_id,
                    "NODE_NOT_FOUND",
                    "The selected morphology node does not exist in the active design.",
                    False,
                    404,
                    "Refresh the Studio and choose one of its current semantic parts.",
                )

    async def prepare_build_pack(
        self, value: PrepareBuildPackInput
    ) -> PrepareBuildPackOutput:
        request_id = self._begin("prepare_build_pack", value, PrepareBuildPackInput)
        pack_started = time.perf_counter()
        async with self._lock:
            revision = self._revision(value.revision_id, request_id)
            if revision.summary.spec_hash != value.expected_spec_hash:
                raise self._error(
                    request_id,
                    "STALE_SPEC",
                    "The expected spec hash does not match the committed revision.",
                    False,
                    409,
                    "Inspect the revision and retry with its current spec hash.",
                )
            report, compiled, simulation = await self._validate_spec(
                revision.spec, request_id
            )
            # Keep validation/run telemetry even when later package generation
            # is rejected or its state mutation cannot be stored.
            self._persist(request_id)
            blockers = [issue for issue in report.issues if issue.severity == "error"]
            if not report.passed or compiled is None:
                result = PrepareBuildPackOutput(
                    schema_version=SCHEMA_VERSION,
                    request_id=request_id,
                    status="blocked",
                    manifest=None,
                    blockers=blockers,
                    next_action=(
                        "Resolve the reported digital validation errors and commit a new revision."
                    ),
                )
                self._persist(request_id)
                return result

            required_cad = {"glb", "step", "stl", "3mf"}
            available = {artifact.kind for artifact in compiled.artifacts}
            missing = sorted(required_cad.difference(available))
            if missing:
                missing_issue = ValidationIssue(
                    code="required_artifact_missing",
                    severity="error",
                    path="artifacts",
                    message="The CAD compiler did not produce: " + ", ".join(missing),
                    suggestion="Reconnect a compiler that supports every required CAD export.",
                )
                result = PrepareBuildPackOutput(
                    schema_version=SCHEMA_VERSION,
                    request_id=request_id,
                    status="blocked",
                    manifest=None,
                    blockers=[missing_issue],
                    next_action="Restore the missing CAD exporter and validate the revision again.",
                )
                self._persist(request_id)
                return result

            profile = self._get_profile(revision.spec.hardware_profile_id, request_id)
            try:
                # Physical records are excluded while deriving their build subject;
                # otherwise the evidence artifact would make the digest self-referential.
                provisional_maker_pack = generate_maker_pack_artifacts(
                    revision.spec,
                    profile,
                    report,
                    physical_records=self.physical_records,
                    evidence_verifier=self.evidence_verifier,
                    exact_build_subject_sha256=None,
                    runtime_catalog=self.runtime_catalog,
                    qualified_instructions=self.qualified_build_instructions,
                )
                provisional_artifacts = self._build_pack_constituents(
                    revision,
                    report,
                    simulation,
                    compiled,
                    provisional_maker_pack,
                    request_id,
                )
                build_subject_hash = self._build_subject_hash(
                    revision,
                    compiled,
                    simulation,
                    provisional_artifacts,
                    provisional_maker_pack.profile_sha256,
                )
                exact_subject_matches = (
                    self.exact_build_subject_sha256 == build_subject_hash
                )
                if exact_subject_matches:
                    maker_pack = generate_maker_pack_artifacts(
                        revision.spec,
                        profile,
                        report,
                        physical_records=self.physical_records,
                        evidence_verifier=self.evidence_verifier,
                        exact_build_subject_sha256=build_subject_hash,
                        runtime_catalog=self.runtime_catalog,
                        qualified_instructions=self.qualified_build_instructions,
                    )
                    artifacts = self._build_pack_constituents(
                        revision,
                        report,
                        simulation,
                        compiled,
                        maker_pack,
                        request_id,
                    )
                    if (
                        self._build_subject_hash(
                            revision,
                            compiled,
                            simulation,
                            artifacts,
                            maker_pack.profile_sha256,
                        )
                        != build_subject_hash
                    ):
                        raise ValueError(
                            "exact evidence changed the immutable build subject"
                        )
                else:
                    maker_pack = provisional_maker_pack
                    artifacts = provisional_artifacts
            except Exception as error:
                code = str(
                    getattr(error, "code", "MAKER_PACK_GENERATION_FAILED")
                ).lower()
                code = re.sub(r"[^a-z0-9_-]+", "_", code).strip("_")
                issue = ValidationIssue(
                    code=code
                    if _ISSUE_CODE.fullmatch(code)
                    else "maker_pack_generation_failed",
                    severity="error",
                    path="build_pack",
                    message=str(
                        getattr(
                            error,
                            "safe_message",
                            "The fixed runtime or maker package could not be generated.",
                        )
                    )[:2000],
                    suggestion=str(
                        getattr(
                            error,
                            "suggestion",
                            "Repair the pinned runtime/profile contract and retry the exact revision.",
                        )
                    )[:2000],
                )
                result = PrepareBuildPackOutput(
                    schema_version=SCHEMA_VERSION,
                    request_id=request_id,
                    status="blocked",
                    manifest=None,
                    blockers=[issue],
                    next_action=str(
                        getattr(
                            error,
                            "next_action",
                            "Restore the fixed runtime and measured catalog contracts before preparing this pack.",
                        )
                    )[:2000],
                )
                self._persist(request_id)
                return result

            packaging_warnings: list[ValidationIssue] = []
            constituent_bytes = sum(
                artifact.descriptor.byte_size for artifact in artifacts
            )
            constituent_count = len(
                {artifact.descriptor.sha256 for artifact in artifacts}
            )
            capacity_issue: ValidationIssue | None = None
            if constituent_bytes > self._artifacts.maximum_bytes:
                capacity_issue = ValidationIssue(
                    code="build_pack_artifacts_too_large",
                    severity="error",
                    path="build_pack",
                    message="The Build Pack constituents exceed this session's artifact budget.",
                    measured_value=float(constituent_bytes),
                    limit_value=float(self._artifacts.maximum_bytes),
                    suggestion="Reduce the generated artifacts or use a future streaming artifact store.",
                )
            elif constituent_count > self._artifacts.maximum_artifacts:
                capacity_issue = ValidationIssue(
                    code="build_pack_artifacts_too_many",
                    severity="error",
                    path="build_pack",
                    message="The Build Pack constituents exceed this session's artifact count budget.",
                    measured_value=float(constituent_count),
                    limit_value=float(self._artifacts.maximum_artifacts),
                    suggestion="Reduce the generated artifact set or use a future streaming artifact store.",
                )
            if capacity_issue is not None:
                self._record_run(
                    kind="build_pack",
                    spec=revision.spec,
                    started=pack_started,
                    cache_hit=False,
                    cad_engine_version=compiled.cad_engine_version,
                    simulation_engine_version=(
                        simulation.engine_version if simulation is not None else None
                    ),
                    error_codes=[capacity_issue.code],
                )
                result = PrepareBuildPackOutput(
                    schema_version=SCHEMA_VERSION,
                    request_id=request_id,
                    status="blocked",
                    manifest=None,
                    blockers=[capacity_issue],
                    next_action="Reduce the exact artifact set before preparing another Build Pack.",
                )
                self._persist(request_id)
                return result

            archive_budget = min(
                MAX_OBJECT_BYTES,
                self._artifacts.maximum_bytes - constituent_bytes,
            )
            try:
                archive_content = self._normalized_build_pack_bytes(
                    artifacts,
                    maximum_bytes=archive_budget,
                )
            except _BuildPackArchiveTooLarge:
                packaging_warnings.append(
                    ValidationIssue(
                        code="aggregate_build_pack_omitted",
                        severity="warning",
                        path="build_pack",
                        message="The aggregate ZIP exceeds the bounded object or session budget; every constituent remains available separately.",
                        measured_value=None,
                        limit_value=float(archive_budget),
                        suggestion="Download the manifest's constituent artifacts individually.",
                    )
                )
            else:
                archive = self._bytes_artifact(
                    "build_pack_zip",
                    "character-robot-build-pack.zip",
                    "application/zip",
                    archive_content,
                    experimental=not maker_pack.replication_ready,
                )
                manifest_artifact_count = len(
                    {
                        *(artifact.descriptor.sha256 for artifact in artifacts),
                        archive.descriptor.sha256,
                    }
                )
                if manifest_artifact_count > self._artifacts.maximum_artifacts:
                    packaging_warnings.append(
                        ValidationIssue(
                            code="aggregate_build_pack_omitted",
                            severity="warning",
                            path="build_pack",
                            message="The aggregate ZIP exceeds the bounded artifact count budget; every constituent remains available separately.",
                            measured_value=float(manifest_artifact_count),
                            limit_value=float(self._artifacts.maximum_artifacts),
                            suggestion="Download the manifest's constituent artifacts individually.",
                        )
                    )
                else:
                    artifacts.append(archive)
            manifest_payload = {
                "revision_id": revision.summary.revision_id,
                "spec_hash": revision.summary.spec_hash,
                "build_subject_hash": build_subject_hash,
                "geometry_sha256": compiled.geometry_sha256,
                "profile_id": revision.spec.hardware_profile_id,
                "profile_sha256": maker_pack.profile_sha256,
                "catalog_version": revision.spec.versions.catalog,
                "compiler_version": revision.spec.versions.compiler,
                "cad_engine_version": compiled.cad_engine_version,
                "simulation_engine_version": (
                    simulation.engine_version if simulation is not None else None
                ),
                "firmware_runtime_version": revision.spec.versions.firmware_runtime,
                "evidence_level": maker_pack.evidence_level,
                "artifacts": [
                    artifact.descriptor.model_dump(mode="json")
                    for artifact in artifacts
                ],
                "download_requires_human_action": True,
            }
            manifest = ArtifactManifest(
                **manifest_payload,
                manifest_hash=_hash_json(manifest_payload),
            )
            is_new_manifest = all(
                existing.manifest_hash != manifest.manifest_hash
                for existing in self._artifact_manifests
            )
            if (
                is_new_manifest
                and len(self._artifact_manifests) >= PROJECT_MANIFEST_LIMIT
            ):
                capacity_issue = ValidationIssue(
                    code="artifact_manifest_limit_reached",
                    severity="error",
                    path="build_pack",
                    message="This Studio project has reached its immutable Build Pack manifest limit.",
                    measured_value=float(len(self._artifact_manifests) + 1),
                    limit_value=float(PROJECT_MANIFEST_LIMIT),
                    suggestion="Start a new Studio project before preparing a distinct Build Pack.",
                )
                self._record_run(
                    kind="build_pack",
                    spec=revision.spec,
                    started=pack_started,
                    cache_hit=False,
                    cad_engine_version=compiled.cad_engine_version,
                    simulation_engine_version=(
                        simulation.engine_version if simulation is not None else None
                    ),
                    error_codes=[capacity_issue.code],
                )
                result = PrepareBuildPackOutput(
                    schema_version=SCHEMA_VERSION,
                    request_id=request_id,
                    status="blocked",
                    manifest=None,
                    blockers=[capacity_issue],
                    next_action="Start a new Studio project before preparing another distinct Build Pack.",
                )
                self._persist(request_id)
                return result
            for artifact in artifacts:
                self._store_artifact(artifact, request_id)
            if is_new_manifest:
                self._artifact_manifests.append(manifest)
            maker_blocker_codes = list(maker_pack.blockers)
            if (
                self.exact_build_subject_sha256 is not None
                and not exact_subject_matches
            ):
                maker_blocker_codes = [
                    code
                    for code in maker_blocker_codes
                    if code != "exact_build_subject_missing"
                ]
                maker_blocker_codes.append("exact_build_subject_mismatch")
            physical_blockers = [
                ValidationIssue(
                    code=code,
                    severity="warning",
                    path="build_pack",
                    message=self._maker_blocker_message(code),
                    suggestion="Complete and sign the corresponding physical evidence before claiming a reproducible build.",
                )
                for code in maker_blocker_codes
            ]
            result_warnings = [*physical_blockers, *packaging_warnings]
            warning_codes = [issue.code for issue in result_warnings]
            self._record_run(
                kind="build_pack",
                spec=revision.spec,
                started=pack_started,
                cache_hit=False,
                cad_engine_version=compiled.cad_engine_version,
                simulation_engine_version=(
                    simulation.engine_version if simulation is not None else None
                ),
                warning_codes=warning_codes,
            )
            result = PrepareBuildPackOutput(
                schema_version=SCHEMA_VERSION,
                request_id=request_id,
                status=(
                    "ready" if maker_pack.replication_ready else "experimental_ready"
                ),
                manifest=manifest,
                blockers=result_warnings,
                next_action=(
                    (
                        "Download the exact verified constituent artifacts individually; the aggregate ZIP exceeded this session's bounded storage."
                        if packaging_warnings
                        else "Download the exact verified Build Pack; installation and hardware actions remain human-only."
                    )
                    if maker_pack.replication_ready
                    else (
                        "Download the experimental constituent artifacts individually; resolve every physical evidence blocker before printing, energizing, or claiming replication."
                        if packaging_warnings
                        else "Download the experimental pack for review only; resolve every physical evidence blocker before printing, energizing, or claiming replication."
                    )
                ),
            )
            self._persist(request_id)
            return result

    def _begin(self, name: str, value: object, expected: type[_MODEL]) -> str:
        request_id = f"req_{uuid.uuid4().hex}"
        self._invocations[name] += 1
        if not isinstance(value, expected):
            raise self._error(
                request_id,
                "INVALID_TOOL_INPUT",
                "The tool input did not match its strict public schema.",
                False,
                400,
                "Validate the request against the advertised input schema and retry.",
            )
        return request_id

    def _make_draft(self, spec: CharacterRobotSpec) -> _Draft:
        spec_hash = _model_hash(spec)
        base_revision_id = self._head_revision_id
        return _Draft(
            spec=spec,
            base_revision_id=base_revision_id,
            draft_hash=_hash_json(
                {
                    "base_revision_id": base_revision_id,
                    "spec_hash": spec_hash,
                }
            ),
            spec_hash=spec_hash,
        )

    def _draft_output(
        self,
        draft: _Draft,
        request_id: str,
        *,
        changed_node_ids: list[str],
        changed_sections: list[str],
        compiled: _CompileView | None,
    ) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "request_id": request_id,
            "base_revision_id": draft.base_revision_id,
            "draft_hash": draft.draft_hash,
            "spec_hash": draft.spec_hash,
            "spec": draft.spec,
            "preview_artifact": self._artifact_of_kind(compiled, "glb"),
            "changed_node_ids": changed_node_ids,
            "changed_sections": changed_sections,
            "warnings": (
                []
                if compiled is not None
                else ["The draft is valid, but no CAD compiler is connected."]
            ),
        }

    def _require_head(self, expected: str | None, request_id: str) -> None:
        if expected != self._head_revision_id:
            raise self._stale_revision(request_id)

    def _require_draft_replacement(
        self, expected_draft_hash: str | None, request_id: str
    ) -> None:
        if self._draft is None:
            if expected_draft_hash is not None:
                raise self._stale_draft(request_id)
            return
        if expected_draft_hash != self._draft.draft_hash:
            raise self._stale_draft(request_id)

    def _require_draft(self, draft_hash: str, request_id: str) -> _Draft:
        if self._draft is None:
            raise self._error(
                request_id,
                "DRAFT_NOT_FOUND",
                "There is no active design draft in this session.",
                False,
                404,
                "Create a bounded design draft before using this operation.",
            )
        if draft_hash != self._draft.draft_hash:
            raise self._stale_draft(request_id)
        return self._draft

    def _active_target(self) -> DraftTarget | RevisionTarget | None:
        if self._draft is not None:
            return DraftTarget(kind="draft", draft_hash=self._draft.draft_hash)
        if self._head_revision_id is not None:
            return RevisionTarget(kind="revision", revision_id=self._head_revision_id)
        return None

    def _target_spec(self, target: object, request_id: str) -> CharacterRobotSpec:
        if isinstance(target, DraftTarget):
            return self._require_draft(target.draft_hash, request_id).spec
        if isinstance(target, RevisionTarget):
            return self._revision(target.revision_id, request_id).spec
        raise AssertionError("validated DesignTarget has an unknown variant")

    def _revision(self, revision_id: str, request_id: str) -> _Revision:
        for revision in self._revisions:
            if revision.summary.revision_id == revision_id:
                return revision
        raise self._error(
            request_id,
            "REVISION_NOT_FOUND",
            "The requested design revision does not exist in this session.",
            False,
            404,
            "Read the Studio context and choose an available revision.",
        )

    def _head_revision(self) -> _Revision:
        if not self._revisions:
            raise AssertionError("head revision requested for an empty service")
        return self._revisions[-1]

    def _apply_edit(
        self,
        payload: dict[str, Any],
        nodes: dict[str, dict[str, Any]],
        edit: object,
        original: CharacterRobotSpec,
        request_id: str,
    ) -> tuple[str, str | None]:
        if isinstance(edit, AddMorphologyNodeEdit):
            if edit.node.node_id in nodes:
                raise self._error(
                    request_id,
                    "NODE_ALREADY_EXISTS",
                    "A morphology node with that ID already exists.",
                    False,
                    409,
                    "Choose a new semantic node ID or replace the existing node.",
                )
            nodes[edit.node.node_id] = edit.node.model_dump(mode="json")
            return "morphology", edit.node.node_id
        if isinstance(edit, (ReplaceMorphologyNodeEdit, RemoveMorphologyNodeEdit)):
            node = next(
                (
                    candidate
                    for candidate in original.morphology.nodes
                    if candidate.node_id == edit.node_id
                ),
                None,
            )
            if node is None or edit.node_id not in nodes:
                raise self._error(
                    request_id,
                    "NODE_NOT_FOUND",
                    "The selected morphology node does not exist.",
                    False,
                    404,
                    "Inspect the design and use a current node ID.",
                )
            if _model_hash(node) != edit.expected_node_hash:
                raise self._error(
                    request_id,
                    "STALE_NODE",
                    "The selected morphology node changed after it was inspected.",
                    True,
                    409,
                    "Inspect the node and regenerate the semantic edit.",
                )
            if isinstance(edit, ReplaceMorphologyNodeEdit):
                nodes[edit.node_id] = edit.node.model_dump(mode="json")
            else:
                del nodes[edit.node_id]
            return "morphology", edit.node_id
        if isinstance(edit, SetHardwareProfileEdit):
            payload["hardware_profile_id"] = edit.hardware_profile_id
            return "hardware_profile", None
        replacements = (
            (SetIdentityEdit, "identity"),
            (SetAppearanceEdit, "appearance"),
            (SetPersonalityEdit, "personality"),
            (SetFaceEdit, "face"),
            (SetBehaviorEdit, "behavior"),
            (SetManufacturingEdit, "manufacturing"),
            (SetConstraintsEdit, "constraints"),
        )
        for model, field in replacements:
            if isinstance(edit, model):
                payload[field] = getattr(edit, field).model_dump(mode="json")
                return field, None
        raise AssertionError("validated SemanticEdit has an unknown variant")

    async def _validate_spec(
        self, spec: CharacterRobotSpec, request_id: str
    ) -> tuple[ValidationReport, _CompileView | None, MotionSimulationResult | None]:
        started = time.perf_counter()
        try:
            compiled = await self._compile(spec, request_id)
        except DomainError as error:
            self._record_run(
                kind="validation",
                spec=spec,
                started=started,
                cache_hit=False,
                cad_engine_version=None,
                error_codes=[error.code],
            )
            self._persist(request_id)
            raise
        issues: list[ValidationIssue] = []
        dimensions = None
        simulation: MotionSimulationResult | None = None
        manufacturing_evidence_level = "digital_checks_passed"
        if compiled is None:
            issues.append(
                ValidationIssue(
                    code="cad_compiler_unavailable",
                    severity="error",
                    path="compiler",
                    message="No deterministic CAD compiler is connected.",
                    suggestion="Connect the pinned Studio CAD compiler and retry.",
                )
            )
        else:
            dimensions = compiled.dimensions_mm
            issues.extend(compiled.issues)
            issues.extend(self._dimension_issues(spec, compiled.dimensions_mm))
            profile = self._get_profile(spec.hardware_profile_id, request_id)
            mass = _optional_value(profile, "mass")
            complete_assembly_mass_g = _optional_value(mass, "complete_assembly_mass_g")
            known_component_mass_g = _optional_value(mass, "known_component_mass_g")
            if (
                complete_assembly_mass_g is not None
                and complete_assembly_mass_g > spec.constraints.maximum_mass_g
            ):
                issues.append(
                    ValidationIssue(
                        code="complete_assembly_mass_exceeded",
                        severity="error",
                        path="constraints.maximum_mass_g",
                        message="The design mass limit is below the complete assembly mass of the selected profile.",
                        measured_value=complete_assembly_mass_g,
                        limit_value=spec.constraints.maximum_mass_g,
                        suggestion="Raise the mass limit or choose a profile whose complete assembly fits it.",
                    )
                )
            elif (
                known_component_mass_g is not None
                and known_component_mass_g > spec.constraints.maximum_mass_g
            ):
                issues.append(
                    ValidationIssue(
                        code="known_component_mass_exceeded",
                        severity="error",
                        path="constraints.maximum_mass_g",
                        message="The design mass limit is below the known mass of the selected profile components.",
                        measured_value=known_component_mass_g,
                        limit_value=spec.constraints.maximum_mass_g,
                        suggestion="Raise the mass limit or choose a profile whose known components fit it.",
                    )
                )
            try:
                simulation = await self._simulate(spec, compiled, profile)
            except SimulationError as error:
                issues.append(
                    ValidationIssue(
                        code=error.code.lower(),
                        severity="error",
                        path="simulation",
                        message=error.safe_message,
                        suggestion="Restore the pinned MuJoCo runtime and rerun validation.",
                    )
                )
            else:
                for check in simulation.checks:
                    if not check.passed:
                        issues.append(
                            ValidationIssue(
                                code=f"simulation_{check.code}",
                                severity="error",
                                path="simulation",
                                message=check.message,
                                measured_value=check.measured_value,
                                limit_value=check.limit_value,
                                suggestion="Revise the design or measured dynamics inputs until this check passes, then rerun validation.",
                            )
                        )
                if simulation.assumption_level == "planning_only":
                    issues.append(
                        ValidationIssue(
                            code="simulation_planning_assumptions",
                            severity="warning",
                            path="simulation",
                            message="MuJoCo uses planning geometry, mass distribution, inertia, actuator response, backlash, latency, and friction; a measured total mass alone does not qualify the dynamics model.",
                            suggestion="Record a versioned measured dynamics profile before treating simulation as profile evidence.",
                        )
                    )

            if self.manufacturing_validator is None:
                issues.append(
                    ValidationIssue(
                        code="manufacturing_evidence_unavailable",
                        severity="warning",
                        path="manufacturing",
                        message="No measured catalog and B-Rep manufacturing probe set is connected for this profile.",
                        suggestion="Measure components, holes, fits, connectors, motion envelopes, mass, and loaded power before qualification.",
                    )
                )
            else:
                try:
                    manufacturing = self.manufacturing_validator(spec, compiled)
                    requested_evidence_level = str(
                        _value(manufacturing, "evidence_level")
                    )
                    profile_qualification = str(
                        _optional_value(profile, "qualification", "digital_only")
                    )
                    if (
                        requested_evidence_level == "within_qualified_profile"
                        and profile_qualification
                        in {"profile_qualified", "exact_build_verified"}
                    ):
                        manufacturing_evidence_level = requested_evidence_level
                    elif requested_evidence_level == "within_qualified_profile":
                        issues.append(
                            ValidationIssue(
                                code="qualification_state_mismatch",
                                severity="warning",
                                path="hardware_profile_id",
                                message="Manufacturing probes passed, but the versioned hardware catalog is still digital-only.",
                                suggestion="Publish a measured, versioned profile qualification before raising the evidence level.",
                            )
                        )
                    for issue in _value(manufacturing, "issues"):
                        subject = re.sub(
                            r"[^A-Za-z0-9_.\[\]-]+",
                            "_",
                            str(_value(issue, "subject")),
                        ).strip("_")
                        issues.append(
                            ValidationIssue(
                                code=str(_value(issue, "code")),
                                severity=str(_value(issue, "severity")),
                                path=f"manufacturing.{subject or 'probe'}",
                                message=str(_value(issue, "message")),
                                measured_value=_optional_value(issue, "measured_value"),
                                limit_value=_optional_value(issue, "limit_value"),
                                suggestion=_optional_value(issue, "suggestion"),
                            )
                        )
                except Exception:
                    issues.append(
                        ValidationIssue(
                            code="manufacturing_validation_failed",
                            severity="error",
                            path="manufacturing",
                            message="The trusted manufacturing evidence adapter returned invalid data.",
                            suggestion="Repair the versioned catalog and probe adapter before qualification.",
                        )
                    )

        passed = compiled is not None and not any(
            issue.severity == "error" for issue in issues
        )
        evidence_level = "concept_only"
        if passed:
            evidence_level = (
                "within_qualified_profile"
                if manufacturing_evidence_level == "within_qualified_profile"
                else "digital_checks_passed"
            )
        report_payload = {
            "spec_hash": _model_hash(spec),
            "evidence_level": evidence_level,
            "passed": passed,
            "dimensions_mm": (
                dimensions.model_dump(mode="json") if dimensions is not None else None
            ),
            "issues": [issue.model_dump(mode="json") for issue in issues],
        }
        report = ValidationReport(
            **report_payload,
            report_hash=_hash_json(report_payload),
        )
        self._latest_validation = report
        self._record_run(
            kind="validation",
            spec=spec,
            started=started,
            cache_hit=False,
            cad_engine_version=(
                compiled.cad_engine_version if compiled is not None else None
            ),
            warning_codes=[issue.code for issue in issues if issue.severity != "error"],
            error_codes=[issue.code for issue in issues if issue.severity == "error"],
        )
        return report, compiled, simulation

    async def _simulate(
        self,
        spec: CharacterRobotSpec,
        compiled: _CompileView,
        profile: object,
    ) -> MotionSimulationResult:
        mass = _optional_value(
            _optional_value(profile, "mass"), "complete_assembly_mass_g"
        )
        cache_key = _hash_json(
            {
                "dimensions_mm": compiled.dimensions_mm.model_dump(mode="json"),
                "drive_wheels": asdict(compiled.drive_wheels),
                "assembly_mass_g": mass,
                "simulation_compiler": SIMULATION_COMPILER_VERSION,
            }
        )
        started = time.perf_counter()
        if cached := self._simulation_cache.get(cache_key):
            self._simulation_cache.move_to_end(cache_key)
            self._record_run(
                kind="simulation",
                spec=spec,
                started=started,
                cache_hit=True,
                cad_engine_version=compiled.cad_engine_version,
                simulation_engine_version=cached.engine_version,
                error_codes=[check.code for check in cached.checks if not check.passed],
            )
            return cached
        try:
            result = await asyncio.to_thread(
                run_motion_checks,
                (
                    compiled.dimensions_mm.x,
                    compiled.dimensions_mm.y,
                    compiled.dimensions_mm.z,
                ),
                wheel_track_mm=compiled.drive_wheels.track_mm,
                wheel_width_mm=compiled.drive_wheels.width_mm,
                wheel_radius_mm=compiled.drive_wheels.radius_mm,
                assembly_mass_g=mass,
            )
        except SimulationError as error:
            self._record_run(
                kind="simulation",
                spec=spec,
                started=started,
                cache_hit=False,
                cad_engine_version=compiled.cad_engine_version,
                error_codes=[error.code],
            )
            raise
        self._simulation_cache[cache_key] = result
        self._simulation_cache.move_to_end(cache_key)
        while len(self._simulation_cache) > _MAX_COMPILE_CACHE_ENTRIES:
            self._simulation_cache.popitem(last=False)
        self._record_run(
            kind="simulation",
            spec=spec,
            started=started,
            cache_hit=False,
            cad_engine_version=compiled.cad_engine_version,
            simulation_engine_version=result.engine_version,
            warning_codes=(
                ["simulation_planning_assumptions"]
                if result.assumption_level == "planning_only"
                else []
            ),
            error_codes=[check.code for check in result.checks if not check.passed],
        )
        return result

    def _dimension_issues(
        self, spec: CharacterRobotSpec, dimensions: PositiveVec3
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        maximum = spec.constraints.maximum_dimensions_mm
        for axis in ("x", "y", "z"):
            measured = getattr(dimensions, axis)
            limit = getattr(maximum, axis)
            if measured > limit:
                issues.append(
                    ValidationIssue(
                        code="maximum_dimension_exceeded",
                        severity="error",
                        path=f"constraints.maximum_dimensions_mm.{axis}",
                        message=f"Compiled {axis}-dimension exceeds the user maximum.",
                        measured_value=measured,
                        limit_value=limit,
                        suggestion=f"Increase the {axis} limit or reduce the affected parts.",
                    )
                )
        return issues

    def _require_within_maximum_dimensions(
        self,
        spec: CharacterRobotSpec,
        compiled: _CompileView | None,
        request_id: str,
    ) -> None:
        if compiled is None:
            return
        issues = self._dimension_issues(spec, compiled.dimensions_mm)
        if not issues:
            return
        required = compiled.dimensions_mm
        maximum = spec.constraints.maximum_dimensions_mm
        overages = [
            max(0.0, getattr(required, axis) - getattr(maximum, axis))
            for axis in ("x", "y", "z")
        ]
        raise self._error(
            request_id,
            "MAXIMUM_DIMENSIONS_EXCEEDED",
            "The compiled design exceeds its maximum size constraint by "
            f"{overages[0]:.1f} x {overages[1]:.1f} x {overages[2]:.1f} mm.",
            False,
            422,
            "Reduce the affected morphology or explicitly raise the maximum dimensions.",
        )

    async def _compile(
        self, spec: CharacterRobotSpec, request_id: str
    ) -> _CompileView | None:
        started = time.perf_counter()
        profile = self._get_profile(spec.hardware_profile_id, request_id)
        if self.cad_compiler is None:
            self._record_run(
                kind="compile",
                spec=spec,
                started=started,
                cache_hit=False,
                cad_engine_version=None,
                error_codes=["cad_compiler_unavailable"],
            )
            return None
        cache_key = _cad_cache_key(spec, profile)
        if cached := self._compile_cache.get(cache_key):
            if self._compile_artifacts_available(cached):
                self._compile_cache.move_to_end(cache_key)
                self._record_run(
                    kind="compile",
                    spec=spec,
                    started=started,
                    cache_hit=True,
                    cad_engine_version=cached.cad_engine_version,
                    warning_codes=[
                        issue.code
                        for issue in cached.issues
                        if issue.severity != "error"
                    ],
                    error_codes=[
                        issue.code
                        for issue in cached.issues
                        if issue.severity == "error"
                    ],
                )
                return cached
            self._compile_cache.pop(cache_key, None)
        try:
            result = self.cad_compiler.compile(spec, profile)
            if inspect.isawaitable(result):
                result = await result
            compiled, artifact_payloads = self._compile_view(result)
            if (
                compiled.compiler_version is not None
                and compiled.compiler_version != spec.versions.compiler
            ):
                raise self._error(
                    request_id,
                    "COMPILER_VERSION_MISMATCH",
                    "The CAD result was produced by a different compiler version.",
                    False,
                    409,
                    "Reconnect the pinned compiler advertised by CharacterRobotSpec.",
                )
            if (
                compiled.profile_id is not None
                and compiled.profile_id != spec.hardware_profile_id
            ):
                raise self._error(
                    request_id,
                    "CAD_PROFILE_MISMATCH",
                    "The CAD result was produced for a different hardware profile.",
                    False,
                    409,
                    "Compile the design against its exact hardware profile.",
                )
        except DomainError as error:
            self._record_run(
                kind="compile",
                spec=spec,
                started=started,
                cache_hit=False,
                cad_engine_version=None,
                error_codes=[error.code],
            )
            self._persist(request_id)
            raise
        except Exception as error:
            code = getattr(error, "code", "CAD_COMPILE_FAILED")
            safe_message = getattr(
                error,
                "safe_message",
                "The deterministic CAD compiler could not compile this design.",
            )
            retryable = bool(getattr(error, "retryable", False))
            if not isinstance(code, str) or _ERROR_CODE.fullmatch(code) is None:
                code = "CAD_COMPILE_FAILED"
            if not isinstance(safe_message, str) or not safe_message:
                safe_message = (
                    "The deterministic CAD compiler could not compile this design."
                )
            safe_message = safe_message.replace("\n", " ").replace("\r", " ")[:240]
            self._record_run(
                kind="compile",
                spec=spec,
                started=started,
                cache_hit=False,
                cad_engine_version=None,
                error_codes=[code],
            )
            domain_error = self._error(
                request_id,
                code,
                safe_message,
                retryable,
                503 if retryable else 422,
                "Revise the bounded design or reconnect the pinned compiler and retry.",
            )
            self._persist(request_id)
            raise domain_error from None
        for artifact in artifact_payloads:
            self._store_artifact(artifact, request_id)
        self._compile_cache[cache_key] = compiled
        self._compile_cache.move_to_end(cache_key)
        while len(self._compile_cache) > _MAX_COMPILE_CACHE_ENTRIES:
            self._compile_cache.popitem(last=False)
        self._record_run(
            kind="compile",
            spec=spec,
            started=started,
            cache_hit=False,
            cad_engine_version=compiled.cad_engine_version,
            warning_codes=[
                issue.code for issue in compiled.issues if issue.severity != "error"
            ],
            error_codes=[
                issue.code for issue in compiled.issues if issue.severity == "error"
            ],
        )
        return compiled

    def _compile_artifacts_available(self, compiled: _CompileView) -> bool:
        try:
            for descriptor in compiled.artifacts:
                self._artifacts.read(descriptor.sha256)
        except ArtifactStoreError:
            return False
        return True

    def _store_artifact(self, artifact: _CompiledArtifact, request_id: str) -> None:
        try:
            self._artifacts.put(artifact.descriptor, artifact.content)
        except ArtifactStoreError:
            raise self._error(
                request_id,
                "ARTIFACT_STORAGE_FAILED",
                "A generated artifact could not be stored with its declared digest.",
                False,
                500,
                "Regenerate the exact design after checking the private session storage.",
            ) from None

    def _record_run(
        self,
        *,
        kind: str,
        spec: CharacterRobotSpec,
        started: float,
        cache_hit: bool,
        cad_engine_version: str | None,
        simulation_engine_version: str | None = None,
        warning_codes: Sequence[str] = (),
        error_codes: Sequence[str] = (),
    ) -> str:
        def safe_code(value: str) -> str:
            normalized = re.sub(r"[^a-z0-9_-]+", "_", value.lower()).strip("_")
            return normalized if _ISSUE_CODE.fullmatch(normalized) else "run_issue"

        run_id = f"run_{uuid.uuid4().hex}"
        summary = StudioRunSummary(
            run_id=run_id,
            kind=kind,
            spec_hash=_model_hash(spec),
            profile_id=spec.hardware_profile_id,
            catalog_version=spec.versions.catalog,
            compiler_version=spec.versions.compiler,
            cad_engine_version=cad_engine_version,
            simulation_engine_version=simulation_engine_version,
            firmware_runtime_version=spec.versions.firmware_runtime,
            duration_ms=float(round((time.perf_counter() - started) * 1000.0, 3)),
            cache_hit=cache_hit,
            warning_codes=list(
                dict.fromkeys(safe_code(code) for code in warning_codes)
            ),
            error_codes=list(dict.fromkeys(safe_code(code) for code in error_codes)),
        )
        self._recent_runs.append(summary)
        del self._recent_runs[:-64]
        return run_id

    def _compile_view(
        self, result: object
    ) -> tuple[_CompileView, tuple[_CompiledArtifact, ...]]:
        dimensions_value = _value(result, "dimensions_mm")
        if isinstance(dimensions_value, PositiveVec3):
            dimensions = dimensions_value
        elif isinstance(dimensions_value, Mapping):
            dimensions = PositiveVec3.model_validate(dimensions_value)
        else:
            x, y, z = dimensions_value
            dimensions = PositiveVec3(x=float(x), y=float(y), z=float(z))
        drive_wheels = _drive_wheel_geometry(_value(result, "parts"))

        geometry_sha256 = str(_value(result, "geometry_sha256"))
        if re.fullmatch(r"[0-9a-f]{64}", geometry_sha256) is None:
            raise ValueError("compiled geometry hash is invalid")
        artifacts: list[_CompiledArtifact] = []
        for raw in _value(result, "artifacts"):
            content = _value(raw, "content")
            if not isinstance(content, bytes):
                raise TypeError("compiled artifact content must be bytes")
            digest = hashlib.sha256(content).hexdigest()
            claimed_digest = _optional_value(raw, "sha256")
            if claimed_digest is not None and claimed_digest != digest:
                raise ValueError("compiled artifact hash does not match its content")
            descriptor = ArtifactDescriptor(
                kind=_value(raw, "kind"),
                file_name=_value(raw, "file_name"),
                media_type=_value(raw, "media_type"),
                sha256=digest,
                byte_size=len(content),
                experimental=bool(_optional_value(raw, "experimental", True)),
            )
            artifacts.append(_CompiledArtifact(descriptor=descriptor, content=content))

        issues = tuple(self._coerce_issue(issue) for issue in _value(result, "issues"))
        return (
            _CompileView(
                dimensions_mm=dimensions,
                drive_wheels=drive_wheels,
                geometry_sha256=geometry_sha256,
                compiler_version=_optional_value(result, "compiler_version"),
                cad_engine_version=_optional_value(result, "build123d_version"),
                profile_id=_optional_value(result, "profile_id"),
                artifacts=tuple(artifact.descriptor for artifact in artifacts),
                issues=issues,
            ),
            tuple(artifacts),
        )

    def _coerce_issue(self, issue: object) -> ValidationIssue:
        if isinstance(issue, ValidationIssue):
            return issue
        raw_code = str(_value(issue, "code")).lower()
        code = re.sub(r"[^a-z0-9_-]+", "_", raw_code).strip("_")
        if not code or _ISSUE_CODE.fullmatch(code) is None:
            code = "cad_validation_issue"
        path = str(_optional_value(issue, "path", "morphology"))
        if _ISSUE_PATH.fullmatch(path) is None:
            path = "morphology"
        severity = str(_optional_value(issue, "severity", "error")).lower()
        if severity not in {"info", "warning", "error"}:
            severity = "error"
        return ValidationIssue(
            code=code,
            severity=severity,
            path=path,
            message=str(_value(issue, "message"))[:2000],
            measured_value=_optional_value(issue, "measured_value"),
            limit_value=_optional_value(issue, "limit_value"),
            suggestion=_optional_value(issue, "suggestion"),
        )

    def _artifact_of_kind(
        self, compiled: _CompileView | None, kind: str
    ) -> ArtifactDescriptor | None:
        if compiled is None:
            return None
        return next(
            (artifact for artifact in compiled.artifacts if artifact.kind == kind),
            None,
        )

    def _preview_artifact(
        self, spec: CharacterRobotSpec, request_id: str
    ) -> ArtifactDescriptor | None:
        profile = self._get_profile(spec.hardware_profile_id, request_id)
        compiled = self._compile_cache.get(_cad_cache_key(spec, profile))
        cached = self._artifact_of_kind(compiled, "glb")
        if cached is not None and cached.sha256 in self._artifacts:
            return cached
        spec_hash = _model_hash(spec)
        return next(
            (
                artifact
                for manifest in reversed(self._artifact_manifests)
                if manifest.spec_hash == spec_hash
                for artifact in manifest.artifacts
                if artifact.kind == "glb" and artifact.sha256 in self._artifacts
            ),
            None,
        )

    def _bytes_artifact(
        self,
        kind: str,
        file_name: str,
        media_type: str,
        content: bytes,
        *,
        experimental: bool = True,
    ) -> _CompiledArtifact:
        digest = hashlib.sha256(content).hexdigest()
        return _CompiledArtifact(
            descriptor=ArtifactDescriptor(
                kind=kind,
                file_name=file_name,
                media_type=media_type,
                sha256=digest,
                byte_size=len(content),
                experimental=experimental,
            ),
            content=content,
        )

    def _build_pack_constituents(
        self,
        revision: _Revision,
        report: ValidationReport,
        simulation: MotionSimulationResult | None,
        compiled: _CompileView,
        maker_pack: MakerPackResult,
        request_id: str,
    ) -> list[_CompiledArtifact]:
        pack_experimental = not maker_pack.replication_ready
        generated = [
            self._bytes_artifact(
                artifact.kind,
                artifact.file_name,
                artifact.media_type,
                artifact.content,
                experimental=artifact.experimental,
            )
            for artifact in maker_pack.artifacts
        ]
        generated.extend(
            [
                self._bytes_artifact(
                    "spec_json",
                    "character-robot-spec.json",
                    "application/json",
                    _canonical_json_bytes(revision.spec.model_dump(mode="json")),
                    experimental=pack_experimental,
                ),
                self._bytes_artifact(
                    "validation_json",
                    "validation-report.json",
                    "application/json",
                    _canonical_json_bytes(report.model_dump(mode="json")),
                    experimental=pack_experimental,
                ),
                self._bytes_artifact(
                    "project_snapshot_json",
                    "character-robot-project.json",
                    "application/json",
                    self._portable_project_bytes(revision.summary.revision_id),
                    experimental=pack_experimental,
                ),
            ]
        )
        if simulation is not None:
            generated.extend(
                [
                    self._bytes_artifact(
                        "mjcf",
                        "simulation.mjcf",
                        "application/xml",
                        simulation.model_xml,
                        experimental=pack_experimental,
                    ),
                    self._bytes_artifact(
                        "simulation_json",
                        "simulation-report.json",
                        "application/json",
                        simulation.canonical_report(),
                        experimental=pack_experimental,
                    ),
                ]
            )
        cad_artifacts: list[_CompiledArtifact] = []
        for descriptor in compiled.artifacts:
            try:
                content, _ = self._artifacts.read(descriptor.sha256)
            except ArtifactStoreError:
                raise self._error(
                    request_id,
                    "ARTIFACT_NOT_FOUND",
                    "A compiled CAD artifact is no longer available for this Build Pack.",
                    True,
                    503,
                    "Regenerate the exact revision and prepare its Build Pack again.",
                ) from None
            cad_artifacts.append(
                _CompiledArtifact(
                    descriptor=descriptor.model_copy(
                        update={"experimental": pack_experimental}
                    ),
                    content=content,
                )
            )
        return [*cad_artifacts, *generated]

    @staticmethod
    def _build_subject_hash(
        revision: _Revision,
        compiled: _CompileView,
        simulation: MotionSimulationResult | None,
        artifacts: Sequence[_CompiledArtifact],
        profile_sha256: str,
    ) -> str:
        artifact_subjects = [
            {
                "kind": artifact.descriptor.kind,
                "file_name": artifact.descriptor.file_name,
                "media_type": artifact.descriptor.media_type,
                "sha256": artifact.descriptor.sha256,
                "byte_size": artifact.descriptor.byte_size,
            }
            for artifact in artifacts
            if artifact.descriptor.kind != "physical_evidence_json"
        ]
        payload = {
            "schema_version": "character-build-subject/v1",
            "revision_id": revision.summary.revision_id,
            "spec_hash": revision.summary.spec_hash,
            "geometry_sha256": compiled.geometry_sha256,
            "profile_id": revision.spec.hardware_profile_id,
            "profile_sha256": profile_sha256,
            "catalog_version": revision.spec.versions.catalog,
            "compiler_version": revision.spec.versions.compiler,
            "cad_engine_version": compiled.cad_engine_version,
            "simulation_engine_version": (
                simulation.engine_version if simulation is not None else None
            ),
            "firmware_runtime_version": revision.spec.versions.firmware_runtime,
            "artifacts": sorted(
                artifact_subjects,
                key=lambda artifact: (artifact["file_name"], artifact["kind"]),
            ),
        }
        return _hash_json(payload)

    def _portable_project_bytes(self, revision_id: str) -> bytes:
        """Return deterministic source history scoped to one exact revision."""

        target = next(
            revision
            for revision in self._revisions
            if revision.summary.revision_id == revision_id
        )
        revisions = self._revisions[: target.summary.ordinal + 1]
        payload = {
            "schema_version": "character-project-export/v1",
            "project_id": self.project_id,
            "head_revision_id": revision_id,
            "draft": None,
            "revisions": [
                {
                    "summary": revision.summary.model_dump(mode="json"),
                    "spec": revision.spec.model_dump(mode="json"),
                }
                for revision in revisions
            ],
            "import_requires_human_action": True,
        }
        return validate_portable_project_bytes(_canonical_json_bytes(payload))

    @staticmethod
    def _normalized_build_pack_bytes(
        artifacts: Sequence[_CompiledArtifact],
        *,
        maximum_bytes: int = MAX_OBJECT_BYTES,
    ) -> bytes:
        file_names = [artifact.descriptor.file_name for artifact in artifacts]
        if any(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", file_name) is None
            for file_name in file_names
        ):
            raise ValueError("Build Pack artifact names must be safe basenames")
        if len(file_names) != len(set(file_names)):
            raise ValueError("Build Pack artifact names must be unique")
        index = _canonical_json_bytes(
            {
                "schema_version": "character-build-pack-index/v1",
                "artifacts": [
                    artifact.descriptor.model_dump(mode="json")
                    for artifact in artifacts
                ],
            }
        )
        stream = _BoundedBytesIO(maximum_bytes)
        with zipfile.ZipFile(
            stream,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as package:
            entries = [
                (artifact.descriptor.file_name, artifact.content)
                for artifact in artifacts
            ]
            entries.append(("BUILD-PACK-INDEX.json", index))
            for file_name, content in sorted(entries):
                info = zipfile.ZipInfo(file_name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                package.writestr(
                    info,
                    content,
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=9,
                )
        return stream.getvalue()

    @staticmethod
    def _maker_blocker_message(code: str) -> str:
        messages = {
            "runtime_release_not_published": "The fixed runtime release has no published, digest-pinned binary yet.",
            "profile_catalog_is_digital_only": "The selected hardware catalog remains digital-only.",
            "signed_profile_evidence_incomplete": "Required signed profile measurements and load tests are incomplete.",
            "exact_build_subject_missing": "No exact-build subject is bound to this revision.",
            "exact_build_subject_mismatch": "The configured exact-build digest does not match this generated build subject.",
            "signed_exact_build_evidence_incomplete": "The exact print, assembly, 100-cycle, and emergency-stop evidence is incomplete.",
            "provisional_bom_incomplete": "The BOM still contains unselected motors, servos, battery, and fasteners.",
            "provisional_wiring_incomplete": "Wiring assignments and electrical limits still require measurement.",
            "calibration_unmeasured": "Wheel and pan/tilt calibration values are unmeasured.",
            "manufacturing_validation_not_qualified": "The exact revision has not passed the connected measured manufacturing validator.",
        }
        return messages.get(code, "A physical Build Pack evidence gate is incomplete.")

    def _list_profiles(self, request_id: str) -> Sequence[object]:
        try:
            method = getattr(self.profile_registry, "list_profiles", None)
            if method is None:
                method = getattr(self.profile_registry, "list")
            profiles = tuple(method())
        except Exception as error:
            raise self._profile_error(request_id, error) from None
        return profiles

    def _get_profile(self, profile_id: str, request_id: str) -> object:
        try:
            method = getattr(self.profile_registry, "get_profile", None)
            if method is None:
                method = getattr(self.profile_registry, "get")
            return method(profile_id)
        except Exception as error:
            raise self._profile_error(request_id, error) from None

    def _profile_summary(self, profile: object, request_id: str) -> ProfileSummary:
        try:
            dimensions = _value(profile, "dimensions_mm")
            if isinstance(dimensions, PositiveVec3):
                minimum = dimensions
            elif isinstance(dimensions, Mapping):
                minimum = PositiveVec3.model_validate(dimensions)
            else:
                x, y, z = dimensions
                minimum = PositiveVec3(x=float(x), y=float(y), z=float(z))
            components = tuple(_value(profile, "components"))
            controller = next(
                (
                    str(_value(component, "display_name"))
                    for component in components
                    if str(_value(_value(component, "envelope"), "role"))
                    == "controller"
                ),
                str(_value(profile, "display_name")),
            )
            component_count = sum(
                int(_optional_value(component, "quantity", 1))
                for component in components
            )
            return ProfileSummary(
                profile_id=str(
                    _optional_value(
                        profile,
                        "profile_id",
                        _optional_value(profile, "id"),
                    )
                ),
                display_name=str(_value(profile, "display_name")),
                qualification=str(
                    _optional_value(
                        profile,
                        "qualification",
                        _optional_value(profile, "qualification_level", "digital_only"),
                    )
                ),
                controller=controller,
                minimum_enclosure_mm=minimum,
                component_count=component_count,
                capabilities=list(_optional_value(profile, "capabilities", ())),
                unknowns=list(_optional_value(profile, "unknowns", ())),
            )
        except Exception as error:
            raise self._profile_error(request_id, error) from None

    def _profile_error(self, request_id: str, error: Exception) -> DomainError:
        code = getattr(error, "code", "HARDWARE_PROFILE_INVALID")
        if code == "HARDWARE_PROFILE_NOT_FOUND":
            return self._error(
                request_id,
                code,
                "The requested hardware profile is not available.",
                False,
                404,
                "Read the Studio context and choose an advertised hardware profile.",
            )
        return self._error(
            request_id,
            "HARDWARE_PROFILE_INVALID",
            "The hardware profile registry returned invalid data.",
            False,
            500,
            "Restore the pinned profile catalog before designing a robot.",
        )

    def _stale_revision(self, request_id: str) -> DomainError:
        return self._error(
            request_id,
            "STALE_REVISION",
            "The committed design changed after the supplied revision was read.",
            True,
            409,
            "Read the current Studio context and reapply the intended change.",
        )

    def _stale_draft(self, request_id: str) -> DomainError:
        return self._error(
            request_id,
            "STALE_DRAFT",
            "The draft changed after the supplied hash was read.",
            True,
            409,
            "Inspect the current draft and reapply the intended change.",
        )

    @staticmethod
    def _error(
        request_id: str,
        code: str,
        message: str,
        retryable: bool,
        http_status: int,
        next_action: str,
    ) -> DomainError:
        return DomainError(
            code=code,
            safe_message=message,
            retryable=retryable,
            request_id=request_id,
            http_status=http_status,
            next_action=next_action,
        )


def _value(value: object, field: str) -> Any:
    if isinstance(value, Mapping):
        return value[field]
    return getattr(value, field)


def _optional_value(value: object, field: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(field, default)
    return getattr(value, field, default)


def _bounds_vector(value: object, label: str) -> tuple[float, float, float]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) != 3
    ):
        raise ValueError(f"compiled {label} must contain three coordinates")
    coordinates = tuple(float(coordinate) for coordinate in value)
    if not all(math.isfinite(coordinate) for coordinate in coordinates):
        raise ValueError(f"compiled {label} must be finite")
    return coordinates  # type: ignore[return-value]


def _drive_wheel_geometry(parts: object) -> _DriveWheelGeometry:
    if not isinstance(parts, Sequence) or isinstance(parts, (str, bytes, bytearray)):
        raise ValueError("compiled CAD parts must be a sequence")
    wheels = [part for part in parts if str(_value(part, "role")) == "drive_wheel"]
    if len(wheels) != 2:
        raise ValueError("compiled CAD must contain exactly two drive wheels")

    wheel_bounds: list[
        tuple[tuple[float, float, float], tuple[float, float, float]]
    ] = []
    for index, wheel in enumerate(wheels):
        bounds = _value(wheel, "bounds")
        minimum = _bounds_vector(
            _value(bounds, "minimum_mm"), f"drive wheel {index + 1} minimum"
        )
        maximum = _bounds_vector(
            _value(bounds, "maximum_mm"), f"drive wheel {index + 1} maximum"
        )
        if any(maximum[axis] <= minimum[axis] for axis in range(3)):
            raise ValueError("compiled drive-wheel bounds are empty or inverted")
        wheel_bounds.append((minimum, maximum))

    (left_minimum, left_maximum), (right_minimum, right_maximum) = sorted(
        wheel_bounds, key=lambda bounds: (bounds[0][0] + bounds[1][0]) / 2.0
    )
    left_center_x = (left_minimum[0] + left_maximum[0]) / 2.0
    right_center_x = (right_minimum[0] + right_maximum[0]) / 2.0
    track = abs(right_center_x - left_center_x)
    widths = (
        left_maximum[0] - left_minimum[0],
        right_maximum[0] - right_minimum[0],
    )
    diameters = (
        left_maximum[1] - left_minimum[1],
        left_maximum[2] - left_minimum[2],
        right_maximum[1] - right_minimum[1],
        right_maximum[2] - right_minimum[2],
    )
    if not math.isclose(widths[0], widths[1], rel_tol=1e-6, abs_tol=1e-6):
        raise ValueError("compiled drive-wheel widths do not match")
    if not all(
        math.isclose(diameter, diameters[0], rel_tol=1e-6, abs_tol=1e-6)
        for diameter in diameters[1:]
    ):
        raise ValueError("compiled drive-wheel diameters do not match")
    if not all(
        math.isfinite(value) and value > 0 for value in (track, *widths, *diameters)
    ):
        raise ValueError("compiled drive-wheel geometry must be positive and finite")
    return _DriveWheelGeometry(
        track_mm=track,
        width_mm=sum(widths) / len(widths),
        radius_mm=sum(diameters) / (2.0 * len(diameters)),
    )


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _hash_json(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _model_hash(value: BaseModel) -> str:
    return _hash_json(value.model_dump(mode="json"))


def _cad_cache_key(spec: CharacterRobotSpec, profile: object) -> str:
    """Hash only inputs consumed by the current deterministic CAD compiler.

    Identity, personality, face timelines, and manufacturing guidance remain in
    the canonical Spec and Build Pack, but changing them must not rebuild an
    untouched B-Rep. Constraints are checked against cached compiled bounds at
    the service boundary.
    """

    to_dict = getattr(profile, "to_dict", None)
    if callable(to_dict):
        profile_payload = to_dict()
    elif is_dataclass(profile):
        profile_payload = asdict(profile)
    else:
        profile_payload = {
            "profile_id": _value(profile, "profile_id"),
            "qualification": _optional_value(profile, "qualification"),
            "dimensions_mm": _value(profile, "dimensions_mm"),
        }
    return _hash_json(
        {
            "hardware_profile": profile_payload,
            "appearance": spec.appearance.model_dump(mode="json"),
            "morphology": spec.morphology.model_dump(mode="json"),
            "catalog_version": spec.versions.catalog,
            "compiler_version": spec.versions.compiler,
        }
    )


__all__ = [
    "CadCompilerProtocol",
    "CharacterRobotService",
    "DomainError",
    "ProfileRegistryProtocol",
]
