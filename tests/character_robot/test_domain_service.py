from __future__ import annotations

import asyncio
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass, replace

import pytest

from character_robot.schemas import (
    CharacterRobotSpec,
    CreateRevisionFromDraftInput,
    DraftTarget,
    GetStudioContextInput,
    InspectDesignInput,
    PositiveVec3,
    PrepareBuildPackInput,
    ReplaceMorphologyNodeEdit,
    ReviseDesignDraftInput,
    SetConstraintsEdit,
    SetDesignDraftInput,
    SetIdentityEdit,
    ValidateDesignInput,
)
from character_robot.project_store import ProjectStore, ProjectStoreError, spec_sha256
from character_robot.service import CharacterRobotService, DomainError
from character_robot.simulation import MotionSimulationResult, SimulationCheck

from test_domain_schemas import _spec_payload


@dataclass(frozen=True)
class _Envelope:
    role: str


@dataclass(frozen=True)
class _Component:
    component_id: str
    display_name: str
    quantity: int
    envelope: _Envelope
    included_in: str | None = None


@dataclass(frozen=True)
class _Profile:
    profile_id: str
    display_name: str
    qualification: str
    dimensions_mm: tuple[float, float, float]
    components: tuple[_Component, ...]
    capabilities: tuple[str, ...]
    unknowns: tuple[str, ...]


class _Profiles:
    def __init__(self) -> None:
        self.get_calls = 0
        common = (
            _Component(
                "controller", "Character controller", 1, _Envelope("controller")
            ),
            _Component("display", "Face display", 1, _Envelope("display")),
            _Component("driver", "Motor driver", 1, _Envelope("driver")),
        )
        self.values = {
            "m5-cores3-goplus2/v1": _Profile(
                "m5-cores3-goplus2/v1",
                "CoreS3 profile",
                "digital_only",
                (54.0, 48.0, 67.0),
                common,
                (
                    "differential_drive",
                    "head_pan_tilt",
                    "display:320x240_touch",
                    "speaker",
                ),
                ("Motors are not physically qualified.",),
            ),
            "pi-zero2wh-crickit-ws2/v1": _Profile(
                "pi-zero2wh-crickit-ws2/v1",
                "Pi profile",
                "digital_only",
                (65.0, 80.0, 71.0),
                common,
                (
                    "differential_drive",
                    "head_pan_tilt",
                    "display:320x240_spi",
                    "linux_runtime",
                ),
                ("Wiring is incomplete.",),
            ),
        }

    def list_profiles(self):
        return tuple(self.values.values())

    def get_profile(self, profile_id):
        self.get_calls += 1
        try:
            return self.values[profile_id]
        except KeyError:
            error = RuntimeError("missing")
            error.code = "HARDWARE_PROFILE_NOT_FOUND"
            raise error


@dataclass(frozen=True)
class _Artifact:
    kind: str
    file_name: str
    media_type: str
    content: bytes
    experimental: bool = True

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


@dataclass(frozen=True)
class _CompileResult:
    dimensions_mm: tuple[float, float, float]
    geometry_sha256: str
    artifacts: tuple[_Artifact, ...]
    issues: tuple[object, ...] = ()


class _Compiler:
    def __init__(self) -> None:
        self.calls = 0

    def compile(self, spec, profile=None):
        self.calls += 1
        width = 130.0 if spec.hardware_profile_id.startswith("pi-") else 120.0
        artifacts = tuple(
            _Artifact(kind, f"robot.{kind}", media, f"{kind}:{width}".encode())
            for kind, media in (
                ("glb", "model/gltf-binary"),
                ("step", "model/step"),
                ("stl", "model/stl"),
                ("3mf", "model/3mf"),
            )
        )
        return _CompileResult(
            dimensions_mm=(width, 110.0, 98.0),
            geometry_sha256=hashlib.sha256(f"geometry:{width}".encode()).hexdigest(),
            artifacts=artifacts,
        )


@dataclass(frozen=True)
class _ManufacturingReport:
    evidence_level: str = "within_qualified_profile"
    issues: tuple[object, ...] = ()


class _ManufacturingValidator:
    def validate(self, spec, compiled):
        return _ManufacturingReport()


class _ControlledProjectStore:
    def __init__(self, backing: ProjectStore) -> None:
        self.backing = backing
        self.save_calls = 0
        self.fail_on_save: int | None = None

    def create_project(self, project_id):
        return self.backing.create_project(project_id)

    def load_project(self, project_id):
        return self.backing.load_project(project_id)

    def save_project(self, snapshot, *, expected_generation):
        self.save_calls += 1
        if self.save_calls == self.fail_on_save:
            raise ProjectStoreError("injected save failure")
        return self.backing.save_project(
            snapshot, expected_generation=expected_generation
        )


class _FailingCompiler:
    def compile(self, spec, profile=None):
        error = RuntimeError("injected CAD failure")
        error.code = "CAD_COMPILE_FAILED"
        error.safe_message = "The injected compiler rejected this design."
        raise error


class _UnsafeFileNameCompiler(_Compiler):
    def compile(self, spec, profile=None):
        result = super().compile(spec, profile)
        return replace(
            result,
            artifacts=(
                replace(result.artifacts[0], file_name="../../payload.glb"),
                *result.artifacts[1:],
            ),
        )


def _service() -> CharacterRobotService:
    return CharacterRobotService(
        profile_registry=_Profiles(),
        cad_compiler=_Compiler(),
    )


def _spec() -> CharacterRobotSpec:
    return CharacterRobotSpec.model_validate(_spec_payload())


def test_blank_context_exposes_profiles_without_creating_a_baseline_revision() -> None:
    service = _service()
    context = asyncio.run(service.get_studio_context(GetStudioContextInput()))

    assert context.project_id == "studio"
    assert context.head_revision_id is None
    assert context.head_spec_sha256 is None
    assert context.current_spec is None
    assert context.draft is None
    assert [profile.profile_id for profile in context.hardware_profiles] == [
        "m5-cores3-goplus2/v1",
        "pi-zero2wh-crickit-ws2/v1",
    ]
    assert "head_pan_tilt" in context.hardware_profiles[0].capabilities
    assert context.recent_runs == []


def test_draft_mutations_resolve_the_hardware_profile_once() -> None:
    profiles = _Profiles()
    service = CharacterRobotService(
        profile_registry=profiles,
        cad_compiler=_Compiler(),
    )

    draft = asyncio.run(
        service.set_design_draft(
            SetDesignDraftInput(expected_revision=None, spec=_spec())
        )
    )
    assert profiles.get_calls == 1

    asyncio.run(
        service.revise_design_draft(
            ReviseDesignDraftInput(
                draft_hash=draft.draft_hash,
                edits=[
                    SetIdentityEdit(
                        kind="set_identity",
                        identity=draft.spec.identity.model_copy(
                            update={"name": "Pip revised"}
                        ),
                    )
                ],
            )
        )
    )
    assert profiles.get_calls == 2


def test_unknown_profile_is_rejected_without_a_cad_compiler() -> None:
    profiles = _Profiles()
    service = CharacterRobotService(profile_registry=profiles, cad_compiler=None)
    unknown = _spec().model_copy(update={"hardware_profile_id": "unknown-board/v1"})

    with pytest.raises(DomainError) as caught:
        asyncio.run(
            service.set_design_draft(
                SetDesignDraftInput(expected_revision=None, spec=unknown)
            )
        )

    assert caught.value.code == "HARDWARE_PROFILE_NOT_FOUND"
    assert profiles.get_calls == 1


def test_revision_spec_hash_cannot_be_changed_through_a_draft_output() -> None:
    service = _service()
    draft = asyncio.run(
        service.set_design_draft(
            SetDesignDraftInput(expected_revision=None, spec=_spec())
        )
    )

    with pytest.raises(AttributeError):
        draft.spec.appearance.style_tags.append("mutated")  # type: ignore[attr-defined]

    committed = asyncio.run(
        service.create_revision_from_draft(
            CreateRevisionFromDraftInput(
                expected_revision=None,
                draft_hash=draft.draft_hash,
                note="Immutable revision.",
            )
        )
    )
    context = asyncio.run(service.get_studio_context(GetStudioContextInput()))

    assert context.current_spec is not None
    assert committed.revision.spec_hash == spec_sha256(context.current_spec)


def test_failed_simulation_check_blocks_digital_validation(monkeypatch) -> None:
    model_xml = b'<mujoco model="failed-check"/>'
    failed = MotionSimulationResult(
        engine_version="3.5.0",
        compiler_version="character-sim-v1",
        assumption_level="planning_only",
        model_sha256=hashlib.sha256(model_xml).hexdigest(),
        model_xml=model_xml,
        checks=(
            SimulationCheck(
                code="step_contact",
                passed=False,
                measured_value=0.2,
                limit_value=0.7,
                unit="ratio",
                message="The planning model did not clear the step-contact threshold.",
            ),
        ),
        duration_ms=1.0,
    )
    monkeypatch.setattr(
        "character_robot.service.run_motion_checks",
        lambda _dimensions, **_kwargs: failed,
    )
    service = _service()
    draft = asyncio.run(
        service.set_design_draft(
            SetDesignDraftInput(expected_revision=None, spec=_spec())
        )
    )

    report = asyncio.run(
        service.validate_design(
            ValidateDesignInput(
                target=DraftTarget(kind="draft", draft_hash=draft.draft_hash)
            )
        )
    ).report

    failed_issue = next(
        issue for issue in report.issues if issue.code == "simulation_step_contact"
    )
    assert failed_issue.severity == "error"
    assert report.passed is False
    assert report.evidence_level == "concept_only"

    revision = asyncio.run(
        service.create_revision_from_draft(
            CreateRevisionFromDraftInput(
                expected_revision=None,
                draft_hash=draft.draft_hash,
                note="Simulation failure remains blocking.",
            )
        )
    )
    build_pack = asyncio.run(
        service.prepare_build_pack(
            PrepareBuildPackInput(
                revision_id=revision.revision.revision_id,
                expected_spec_hash=revision.revision.spec_hash,
            )
        )
    )
    assert build_pack.status == "blocked"
    assert [blocker.code for blocker in build_pack.blockers] == [
        "simulation_step_contact"
    ]


def test_context_records_compile_and_validation_provenance() -> None:
    service = _service()
    draft = asyncio.run(
        service.set_design_draft(
            SetDesignDraftInput(expected_revision=None, spec=_spec())
        )
    )
    asyncio.run(
        service.validate_design(
            ValidateDesignInput(
                target=DraftTarget(kind="draft", draft_hash=draft.draft_hash)
            )
        )
    )

    runs = asyncio.run(service.get_studio_context(GetStudioContextInput())).recent_runs

    assert [run.kind for run in runs] == [
        "compile",
        "compile",
        "simulation",
        "validation",
    ]
    assert runs[1].cache_hit is True
    assert runs[-1].spec_hash == draft.spec_hash
    assert runs[-1].profile_id == "m5-cores3-goplus2/v1"
    assert runs[-1].catalog_version == "hardware-catalog-v1"
    assert runs[-1].compiler_version == "character-cad-v1"
    assert runs[-1].firmware_runtime_version == "character-runtime-v1"
    assert runs[-1].duration_ms >= 0


def test_durable_service_rejects_and_rolls_back_a_stale_writer(tmp_path) -> None:
    database = tmp_path / "project.sqlite3"
    first = CharacterRobotService(
        data_root=tmp_path / "first",
        profile_registry=_Profiles(),
        cad_compiler=_Compiler(),
        project_store=ProjectStore(database),
    )
    second = CharacterRobotService(
        data_root=tmp_path / "second",
        profile_registry=_Profiles(),
        cad_compiler=_Compiler(),
        project_store=ProjectStore(database),
    )

    accepted = asyncio.run(
        first.set_design_draft(
            SetDesignDraftInput(expected_revision=None, spec=_spec())
        )
    )
    stale_spec = _spec().model_copy(
        update={
            "identity": _spec().identity.model_copy(update={"name": "Stale writer"})
        }
    )
    with pytest.raises(DomainError) as stale:
        asyncio.run(
            second.set_design_draft(
                SetDesignDraftInput(expected_revision=None, spec=stale_spec)
            )
        )

    assert stale.value.code == "STALE_PROJECT"
    restored = asyncio.run(second.get_studio_context(GetStudioContextInput()))
    assert restored.draft is not None
    assert restored.draft.draft_hash == accepted.draft_hash
    assert restored.draft.spec.identity.name == _spec().identity.name


def test_storage_failure_rolls_back_draft_revision_and_manifest_state(tmp_path) -> None:
    backing = ProjectStore(tmp_path / "project.sqlite3")
    controlled = _ControlledProjectStore(backing)
    service = CharacterRobotService(
        data_root=tmp_path / "artifacts",
        profile_registry=_Profiles(),
        cad_compiler=_Compiler(),
        project_store=controlled,  # type: ignore[arg-type]
    )

    controlled.fail_on_save = 2
    with pytest.raises(DomainError) as draft_failure:
        asyncio.run(
            service.set_design_draft(
                SetDesignDraftInput(expected_revision=None, spec=_spec())
            )
        )
    assert draft_failure.value.code == "PROJECT_STORAGE_FAILED"
    context = asyncio.run(service.get_studio_context(GetStudioContextInput()))
    assert context.draft is None
    assert context.head_revision_id is None
    assert backing.load_project("studio").draft is None
    assert [run.kind for run in context.recent_runs] == ["compile"]

    controlled.fail_on_save = None
    draft = asyncio.run(
        service.set_design_draft(
            SetDesignDraftInput(expected_revision=None, spec=_spec())
        )
    )
    controlled.fail_on_save = controlled.save_calls + 1
    with pytest.raises(DomainError) as revision_failure:
        asyncio.run(
            service.create_revision_from_draft(
                CreateRevisionFromDraftInput(
                    expected_revision=None,
                    draft_hash=draft.draft_hash,
                    note="Should roll back.",
                )
            )
        )
    assert revision_failure.value.code == "PROJECT_STORAGE_FAILED"
    context = asyncio.run(service.get_studio_context(GetStudioContextInput()))
    assert context.head_revision_id is None
    assert context.draft is not None
    assert context.draft.draft_hash == draft.draft_hash

    controlled.fail_on_save = None
    revision = asyncio.run(
        service.create_revision_from_draft(
            CreateRevisionFromDraftInput(
                expected_revision=None,
                draft_hash=draft.draft_hash,
                note="Persisted revision.",
            )
        )
    )
    controlled.fail_on_save = controlled.save_calls + 2
    with pytest.raises(DomainError) as pack_failure:
        asyncio.run(
            service.prepare_build_pack(
                PrepareBuildPackInput(
                    revision_id=revision.revision.revision_id,
                    expected_spec_hash=revision.revision.spec_hash,
                )
            )
        )
    assert pack_failure.value.code == "PROJECT_STORAGE_FAILED"
    assert service._artifacts.artifact_count == 0
    context = asyncio.run(service.get_studio_context(GetStudioContextInput()))
    assert context.head_revision_id == "r000"
    assert context.artifact_manifest_count == 0
    assert not any(run.kind == "build_pack" for run in context.recent_runs)
    assert backing.load_project("studio").artifact_manifests == []


def test_failed_compile_and_validation_runs_survive_restart(tmp_path) -> None:
    database = tmp_path / "project.sqlite3"
    first = CharacterRobotService(
        data_root=tmp_path / "first",
        profile_registry=_Profiles(),
        cad_compiler=_Compiler(),
        project_store=ProjectStore(database),
    )
    draft = asyncio.run(
        first.set_design_draft(
            SetDesignDraftInput(expected_revision=None, spec=_spec())
        )
    )
    failing = CharacterRobotService(
        data_root=tmp_path / "second",
        profile_registry=_Profiles(),
        cad_compiler=_FailingCompiler(),
        project_store=ProjectStore(database),
    )

    with pytest.raises(DomainError) as failure:
        asyncio.run(
            failing.validate_design(
                ValidateDesignInput(
                    target=DraftTarget(kind="draft", draft_hash=draft.draft_hash)
                )
            )
        )
    assert failure.value.code == "CAD_COMPILE_FAILED"

    reopened = CharacterRobotService(
        data_root=tmp_path / "third",
        profile_registry=_Profiles(),
        cad_compiler=_Compiler(),
        project_store=ProjectStore(database),
    )
    runs = asyncio.run(reopened.get_studio_context(GetStudioContextInput())).recent_runs
    failed_runs = [run for run in runs if run.error_codes]
    assert [run.kind for run in failed_runs[-2:]] == ["compile", "validation"]
    assert failed_runs[-2].error_codes == ["cad_compile_failed"]
    assert failed_runs[-1].error_codes == ["cad_compile_failed"]


def test_uncommitted_draft_preview_is_recompiled_after_restart(tmp_path) -> None:
    database = tmp_path / "project.sqlite3"
    artifact_root = tmp_path / "artifacts"
    first = CharacterRobotService(
        data_root=artifact_root,
        profile_registry=_Profiles(),
        cad_compiler=_Compiler(),
        project_store=ProjectStore(database),
    )
    created = asyncio.run(
        first.set_design_draft(
            SetDesignDraftInput(expected_revision=None, spec=_spec())
        )
    )
    reopened = CharacterRobotService(
        data_root=artifact_root,
        profile_registry=_Profiles(),
        cad_compiler=_Compiler(),
        project_store=ProjectStore(database),
    )

    context = asyncio.run(reopened.get_studio_context(GetStudioContextInput()))

    assert context.draft is not None
    assert context.draft.draft_hash == created.draft_hash
    assert context.draft.preview_artifact is not None
    assert context.draft.preview_artifact.kind == "glb"
    assert context.recent_runs[-1].kind == "compile"


def test_custom_compiler_cannot_insert_zip_slip_artifact_name() -> None:
    service = CharacterRobotService(
        profile_registry=_Profiles(), cad_compiler=_UnsafeFileNameCompiler()
    )

    with pytest.raises(DomainError) as failure:
        asyncio.run(
            service.set_design_draft(
                SetDesignDraftInput(expected_revision=None, spec=_spec())
            )
        )

    assert failure.value.code == "CAD_COMPILE_FAILED"
    context = asyncio.run(service.get_studio_context(GetStudioContextInput()))
    assert context.draft is None


def test_measured_probe_adapter_cannot_outvote_a_digital_only_catalog() -> None:
    digital_profiles = _Profiles()
    digital = CharacterRobotService(
        profile_registry=digital_profiles,
        cad_compiler=_Compiler(),
        manufacturing_validator=_ManufacturingValidator().validate,
    )
    digital_draft = asyncio.run(
        digital.set_design_draft(
            SetDesignDraftInput(expected_revision=None, spec=_spec())
        )
    )
    digital_report = asyncio.run(
        digital.validate_design(
            ValidateDesignInput(
                target=DraftTarget(kind="draft", draft_hash=digital_draft.draft_hash)
            )
        )
    ).report
    assert digital_report.evidence_level == "digital_checks_passed"
    assert "qualification_state_mismatch" in {
        issue.code for issue in digital_report.issues
    }

    qualified_profiles = _Profiles()
    current = qualified_profiles.values["m5-cores3-goplus2/v1"]
    qualified_profiles.values["m5-cores3-goplus2/v1"] = replace(
        current, qualification="profile_qualified"
    )
    qualified = CharacterRobotService(
        profile_registry=qualified_profiles,
        cad_compiler=_Compiler(),
        manufacturing_validator=_ManufacturingValidator().validate,
    )
    qualified_draft = asyncio.run(
        qualified.set_design_draft(
            SetDesignDraftInput(expected_revision=None, spec=_spec())
        )
    )
    qualified_report = asyncio.run(
        qualified.validate_design(
            ValidateDesignInput(
                target=DraftTarget(kind="draft", draft_hash=qualified_draft.draft_hash)
            )
        )
    ).report
    assert qualified_report.evidence_level == "within_qualified_profile"


def test_set_and_targeted_revision_preserve_unedited_sections() -> None:
    service = _service()
    created = asyncio.run(
        service.set_design_draft(
            SetDesignDraftInput(expected_revision=None, spec=_spec())
        )
    )
    inspected = asyncio.run(
        service.inspect_design(
            InspectDesignInput(
                target=DraftTarget(kind="draft", draft_hash=created.draft_hash)
            )
        )
    )
    head = inspected.spec.morphology.nodes[1]
    replacement = head.model_copy(
        update={
            "sections": [
                head.sections[0].model_copy(update={"radius_x_mm": 32.0}),
                head.sections[1].model_copy(update={"radius_x_mm": 30.0}),
            ]
        }
    )
    revised = asyncio.run(
        service.revise_design_draft(
            ReviseDesignDraftInput(
                draft_hash=created.draft_hash,
                edits=[
                    ReplaceMorphologyNodeEdit(
                        kind="replace_morphology_node",
                        node_id="head",
                        expected_node_hash=inspected.nodes[1].node_hash,
                        node=replacement,
                    )
                ],
            )
        )
    )

    assert revised.draft_hash != created.draft_hash
    assert revised.changed_node_ids == ["head"]
    assert revised.spec.identity == created.spec.identity
    assert revised.spec.morphology.nodes[0] == created.spec.morphology.nodes[0]
    assert revised.preview_artifact.kind == "glb"


def test_non_cad_semantic_edit_reuses_the_exact_compiled_artifacts() -> None:
    compiler = _Compiler()
    service = CharacterRobotService(
        profile_registry=_Profiles(),
        cad_compiler=compiler,
    )
    created = asyncio.run(
        service.set_design_draft(
            SetDesignDraftInput(expected_revision=None, spec=_spec())
        )
    )

    revised = asyncio.run(
        service.revise_design_draft(
            ReviseDesignDraftInput(
                draft_hash=created.draft_hash,
                edits=[
                    SetIdentityEdit(
                        kind="set_identity",
                        identity=created.spec.identity.model_copy(
                            update={"name": "Pip the careful guide"}
                        ),
                    )
                ],
            )
        )
    )

    assert compiler.calls == 1
    assert revised.preview_artifact.sha256 == created.preview_artifact.sha256
    assert revised.draft_hash != created.draft_hash


def test_stale_edit_and_oversized_edit_do_not_mutate_the_current_draft() -> None:
    service = _service()
    created = asyncio.run(
        service.set_design_draft(
            SetDesignDraftInput(expected_revision=None, spec=_spec())
        )
    )
    with pytest.raises(DomainError) as stale:
        asyncio.run(
            service.revise_design_draft(
                ReviseDesignDraftInput(
                    draft_hash="0" * 64,
                    edits=[
                        SetConstraintsEdit(
                            kind="set_constraints",
                            constraints=created.spec.constraints,
                        )
                    ],
                )
            )
        )
    assert stale.value.code == "STALE_DRAFT"
    assert stale.value.http_status == 409

    too_small = created.spec.constraints.model_copy(
        update={"maximum_dimensions_mm": PositiveVec3(x=50.0, y=50.0, z=50.0)}
    )
    with pytest.raises(DomainError) as oversized:
        asyncio.run(
            service.revise_design_draft(
                ReviseDesignDraftInput(
                    draft_hash=created.draft_hash,
                    edits=[
                        SetConstraintsEdit(
                            kind="set_constraints", constraints=too_small
                        )
                    ],
                )
            )
        )
    assert oversized.value.code == "MAXIMUM_DIMENSIONS_EXCEEDED"

    context = asyncio.run(service.get_studio_context(GetStudioContextInput()))
    assert context.draft.draft_hash == created.draft_hash


def test_full_draft_replacement_requires_the_current_draft_hash() -> None:
    service = _service()
    created = asyncio.run(
        service.set_design_draft(
            SetDesignDraftInput(expected_revision=None, spec=_spec())
        )
    )
    replacement = created.spec.model_copy(
        update={
            "identity": created.spec.identity.model_copy(update={"name": "Pip Two"})
        }
    )

    with pytest.raises(DomainError) as stale:
        asyncio.run(
            service.set_design_draft(
                SetDesignDraftInput(expected_revision=None, spec=replacement)
            )
        )

    assert stale.value.code == "STALE_DRAFT"
    context = asyncio.run(service.get_studio_context(GetStudioContextInput()))
    assert context.draft.draft_hash == created.draft_hash
    replaced = asyncio.run(
        service.set_design_draft(
            SetDesignDraftInput(
                expected_revision=None,
                expected_draft_hash=created.draft_hash,
                spec=replacement,
            )
        )
    )
    assert replaced.spec.identity.name == "Pip Two"
    assert replaced.draft_hash != created.draft_hash


def test_first_commit_is_r000_and_build_pack_is_manifest_only_experimental() -> None:
    service = _service()
    draft = asyncio.run(
        service.set_design_draft(
            SetDesignDraftInput(expected_revision=None, spec=_spec())
        )
    )
    committed = asyncio.run(
        service.create_revision_from_draft(
            CreateRevisionFromDraftInput(
                expected_revision=None,
                draft_hash=draft.draft_hash,
                note="First bounded duck design.",
            )
        )
    )
    assert committed.head_revision_id == "r000"
    assert committed.revision.parent_revision_id is None
    assert committed.draft_hash != draft.draft_hash

    with pytest.raises(DomainError) as stale_precommit_draft:
        asyncio.run(
            service.revise_design_draft(
                ReviseDesignDraftInput(
                    draft_hash=draft.draft_hash,
                    edits=[
                        SetIdentityEdit(
                            kind="set_identity",
                            identity=draft.spec.identity.model_copy(
                                update={"name": "Stale writer"}
                            ),
                        )
                    ],
                )
            )
        )
    assert stale_precommit_draft.value.code == "STALE_DRAFT"

    validated = asyncio.run(
        service.validate_design(
            ValidateDesignInput(target={"kind": "revision", "revision_id": "r000"})
        )
    )
    assert validated.report.evidence_level == "digital_checks_passed"

    prepared = asyncio.run(
        service.prepare_build_pack(
            PrepareBuildPackInput(
                revision_id="r000",
                expected_spec_hash=committed.revision.spec_hash,
            )
        )
    )
    assert prepared.status == "experimental_ready"
    assert prepared.human_action_required is True
    assert prepared.manifest.download_requires_human_action is True
    assert prepared.manifest.evidence_level == "digital_checks_passed"
    assert not hasattr(prepared.manifest.artifacts[0], "url")
    assert {artifact.kind for artifact in prepared.manifest.artifacts} >= {
        "glb",
        "step",
        "stl",
        "3mf",
        "bom_json",
        "wiring_json",
        "firmware_config_json",
    }

    glb = next(
        artifact for artifact in prepared.manifest.artifacts if artifact.kind == "glb"
    )
    content, media_type, file_name = service.read_artifact(glb.sha256)
    assert hashlib.sha256(content).hexdigest() == glb.sha256
    assert media_type == "model/gltf-binary"
    assert file_name == "robot.glb"

    bundle = next(
        artifact
        for artifact in prepared.manifest.artifacts
        if artifact.kind == "build_pack_zip"
    )
    bundle_bytes, bundle_media, bundle_name = service.read_artifact(bundle.sha256)
    assert bundle_media == "application/zip"
    assert bundle_name == "character-robot-build-pack.zip"
    with zipfile.ZipFile(io.BytesIO(bundle_bytes)) as package:
        index = json.loads(package.read("BUILD-PACK-INDEX.json"))
        assert package.testzip() is None
        assert {item["kind"] for item in index["artifacts"]} == {
            artifact.kind
            for artifact in prepared.manifest.artifacts
            if artifact.kind != "build_pack_zip"
        }

    newer_draft = asyncio.run(
        service.revise_design_draft(
            ReviseDesignDraftInput(
                draft_hash=committed.draft_hash,
                edits=[
                    SetIdentityEdit(
                        kind="set_identity",
                        identity=draft.spec.identity.model_copy(
                            update={"name": "A newer revision"}
                        ),
                    )
                ],
            )
        )
    )
    asyncio.run(
        service.create_revision_from_draft(
            CreateRevisionFromDraftInput(
                expected_revision="r000",
                draft_hash=newer_draft.draft_hash,
                note="A later project revision.",
            )
        )
    )
    regenerated_old_revision = asyncio.run(
        service.prepare_build_pack(
            PrepareBuildPackInput(
                revision_id="r000",
                expected_spec_hash=committed.revision.spec_hash,
            )
        )
    )
    assert regenerated_old_revision.manifest is not None
    assert regenerated_old_revision.manifest.manifest_hash == (
        prepared.manifest.manifest_hash
    )


def test_validation_and_build_pack_stay_blocked_without_a_compiler() -> None:
    service = CharacterRobotService(profile_registry=_Profiles(), cad_compiler=None)
    draft = asyncio.run(
        service.set_design_draft(
            SetDesignDraftInput(expected_revision=None, spec=_spec())
        )
    )
    commit = asyncio.run(
        service.create_revision_from_draft(
            CreateRevisionFromDraftInput(
                expected_revision=None,
                draft_hash=draft.draft_hash,
                note="Concept only.",
            )
        )
    )
    prepared = asyncio.run(
        service.prepare_build_pack(
            PrepareBuildPackInput(
                revision_id="r000",
                expected_spec_hash=commit.revision.spec_hash,
            )
        )
    )

    assert prepared.status == "blocked"
    assert prepared.manifest is None
    assert prepared.blockers[0].code == "cad_compiler_unavailable"


def test_context_never_binds_an_old_validation_to_a_new_draft() -> None:
    service = _service()
    draft = asyncio.run(
        service.set_design_draft(
            SetDesignDraftInput(expected_revision=None, spec=_spec())
        )
    )
    asyncio.run(
        service.validate_design(
            ValidateDesignInput(
                target=DraftTarget(kind="draft", draft_hash=draft.draft_hash)
            )
        )
    )
    assert (
        asyncio.run(
            service.get_studio_context(GetStudioContextInput())
        ).latest_validation.spec_hash
        == draft.spec_hash
    )
    committed = asyncio.run(
        service.create_revision_from_draft(
            CreateRevisionFromDraftInput(
                expected_revision=None,
                draft_hash=draft.draft_hash,
                note="Validated first revision.",
            )
        )
    )

    revised_identity = draft.spec.identity.model_copy(update={"name": "Pip Two"})
    revised = asyncio.run(
        service.revise_design_draft(
            ReviseDesignDraftInput(
                draft_hash=committed.draft_hash,
                edits=[SetIdentityEdit(kind="set_identity", identity=revised_identity)],
            )
        )
    )
    asyncio.run(
        service.validate_design(
            ValidateDesignInput(target={"kind": "revision", "revision_id": "r000"})
        )
    )
    context = asyncio.run(service.get_studio_context(GetStudioContextInput()))

    assert revised.spec_hash != draft.spec_hash
    assert context.latest_validation is None


def test_service_rejects_untyped_input_and_unknown_artifact_safely() -> None:
    service = _service()
    with pytest.raises(DomainError) as invalid:
        asyncio.run(service.get_studio_context({}))
    assert invalid.value.code == "INVALID_TOOL_INPUT"
    assert invalid.value.http_status == 400

    with pytest.raises(DomainError) as missing:
        service.read_artifact("0" * 64)
    assert missing.value.code == "ARTIFACT_NOT_FOUND"
    assert missing.value.http_status == 404
