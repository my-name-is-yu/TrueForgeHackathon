from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType

from .fixture import CompoundArmFixture, load_compound_arm_fixture
from .metrics import (
    evaluate_task,
    first_nonfinite_step,
    resample_experiment_trace,
)
from .mujoco_client import (
    MAX_TRACE_SCALARS,
    UPSTREAM_COMMIT,
    UpstreamToolError,
    _run_record_scalar_count,
)
from .patcher import PatcherError, apply_one_attribute_patch
from .qualification import (
    HiddenVerifier,
    build_promotion_ticket,
    validate_promotion_ticket,
)
from .runner import (
    ConstantSegment,
    DeterministicRunner,
    PartialRunError,
    RunConfiguration,
    RunRecord as PhysicsRunRecord,
)
from .schemas import (
    SCHEMA_VERSION,
    ActuatorSummary,
    AggregateResult,
    ArtifactRef,
    BodyPositionObservable,
    BodySummary,
    BudgetSummary,
    CanonicalDiffEntry,
    CompiledDimensions,
    ContactCountObservable,
    ContractClause,
    CreateRevisionInput,
    CreateRevisionOutput,
    ExperimentOutcome,
    FinalSnapshotMetadata,
    InspectAssetInput,
    InspectAssetOutput,
    IntegrityChecks,
    JointPosition,
    JointSummary,
    OpenCaseInput,
    OpenCaseOutput,
    PatchPolicy,
    PromotionTicket,
    PublicEventSummary,
    Range,
    RevisionSummary,
    RunExperimentInput,
    RunExperimentOutput,
    RunTaskInput,
    RunTaskOutput,
    ScenarioSummary,
    SegmentBoundary,
    VerifyRevisionInput,
    VerifyRevisionOutput,
    validate_experiment_trace_contract,
)
from .storage import (
    CaseAlreadyExistsError,
    CaseNotFoundError,
    EvidenceStore,
    IntegrityError,
    LedgerEvent,
    LedgerEventRecord,
    ObjectIntegrityError,
    QualificationConflictError,
    RevisionConflictError,
    RevisionNotFoundError,
    RevisionRecord,
    RunRecord as StoredRunRecord,
    StorageError,
    canonical_json_bytes,
)
from .task_evaluation import PASS_LIMITS, TASK_METRIC_ORDER, TaskEvaluation


TOTAL_RUN_BUDGET = 10
EXPERIMENT_BUDGET = 5
REVISION_BUDGET = 2
QUALIFICATION_BUDGET = 1
_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")


class DomainError(RuntimeError):
    __slots__ = ("code", "safe_message", "retryable", "request_id", "next_action")

    def __init__(
        self,
        *,
        code: str,
        safe_message: str,
        retryable: bool,
        request_id: str,
        next_action: str,
    ) -> None:
        if _CODE.fullmatch(code) is None:
            raise ValueError("domain error code is invalid")
        for value in (safe_message, next_action):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= 240
                or any(char in value for char in ("\x00", "\n", "\r"))
            ):
                raise ValueError("domain error text is invalid")
        if not request_id.startswith("req_"):
            raise ValueError("domain error request ID is invalid")
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = bool(retryable)
        self.request_id = request_id
        self.next_action = next_action


class AssetAutopsyService:
    def __init__(
        self,
        data_root: str | Path,
        *,
        runner: DeterministicRunner | None = None,
        fixture: CompoundArmFixture | None = None,
        evidence_store: EvidenceStore | None = None,
        hidden_verifier: HiddenVerifier | None = None,
    ) -> None:
        self.data_root = Path(data_root)
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.fixture = fixture or load_compound_arm_fixture()
        self.runner = runner or DeterministicRunner()
        self.store = evidence_store or EvidenceStore(
            self.data_root / "evidence.sqlite", self.data_root / "objects"
        )
        self.hidden_verifier = hidden_verifier or HiddenVerifier(
            runner=self.runner, fixture=self.fixture
        )
        self._lock = asyncio.Lock()
        self._invocations = {name: 0 for name in self.tool_names}
        self._provision_demo_case()

    @property
    def tool_names(self) -> tuple[str, ...]:
        return (
            "open_case",
            "inspect_asset",
            "run_task",
            "run_experiment",
            "create_revision",
            "verify_revision",
        )

    @property
    def invocation_counts(self) -> Mapping[str, int]:
        return MappingProxyType(dict(self._invocations))

    async def open_case(self, value: OpenCaseInput) -> OpenCaseOutput:
        request_id = self._begin("open_case", value, OpenCaseInput)
        async with self._lock:
            case = self._case(value.case_id, request_id)
            revisions = self.store.list_revisions(case.case_id)
            topology = self._inspect_revision(
                case.case_id, case.head_revision_id, request_id
            )
            events = self.store.ledger_events(case.case_id)
            scenario = self.fixture.public_scenario
            return OpenCaseOutput(
                schema_version=SCHEMA_VERSION,
                request_id=request_id,
                case_id=case.case_id,
                qualification_state=case.qualification_state,
                original_revision_id=case.root_revision_id,
                original_asset_sha256=case.source_asset_sha256,
                controller_sha256=case.controller_sha256,
                public_contract_sha256=case.public_contract_sha256,
                runner_sha256=case.runner_sha256,
                holdout_commitment_sha256=case.holdout_commitment_sha256,
                public_scenarios=[
                    ScenarioSummary(
                        scenario_id=scenario.scenario_id,
                        initial_joint_positions=[
                            JointPosition(joint_name=name, position_rad=position)
                            for name, position in zip(
                                self.fixture.joint_names,
                                scenario.initial_qpos,
                                strict=True,
                            )
                        ],
                        target_joint_positions=[
                            JointPosition(joint_name=name, position_rad=position)
                            for name, position in zip(
                                self.fixture.joint_names,
                                scenario.target_qpos,
                                strict=True,
                            )
                        ],
                        target_body_name="end_effector",
                        target_body_position_m=scenario.target_body_position,
                        observable_metrics=list(TASK_METRIC_ORDER),
                    )
                ],
                contract_clauses=_contract_clauses(),
                compiled_dimensions=topology[3],
                joints=topology[0],
                bodies=topology[1],
                actuators=topology[2],
                observable_metric_names=list(TASK_METRIC_ORDER),
                patch_policy=_patch_policy(),
                remaining_budgets=self._budgets(
                    case.case_id, events, revisions, case.qualification_state
                ),
                revision_history=[
                    self._revision_summary(revision) for revision in revisions
                ],
                event_tail=self._public_event_tail(events),
            )

    async def inspect_asset(self, value: InspectAssetInput) -> InspectAssetOutput:
        request_id = self._begin("inspect_asset", value, InspectAssetInput)
        async with self._lock:
            self._case(value.case_id, request_id)
            revision = self._revision(value.case_id, value.revision_id, request_id)
            joints, bodies, actuators, dimensions = self._inspect_revision(
                value.case_id, value.revision_id, request_id
            )
            return InspectAssetOutput(
                schema_version=SCHEMA_VERSION,
                request_id=request_id,
                case_id=value.case_id,
                revision_id=value.revision_id,
                asset_sha256=revision.asset_sha256,
                view=value.view,
                joints=joints,
                bodies=bodies,
                actuators=actuators,
                compiled_dimensions=dimensions,
            )

    async def run_task(self, value: RunTaskInput) -> RunTaskOutput:
        request_id = self._begin("run_task", value, RunTaskInput)
        async with self._lock:
            case = self._case(value.case_id, request_id)
            revision = self._revision(value.case_id, value.revision_id, request_id)
            self._require_run_budget(case.case_id, request_id)
            parent_evaluation = None
            if revision.parent_revision_id is not None:
                parent_evaluation = self._latest_task_evaluation(
                    case.case_id, revision.parent_revision_id, request_id
                )
            asset_xml = self._asset_bytes(revision.asset_sha256, request_id)
            try:
                physics = await self._run_public(
                    asset_xml, render=value.capture == "metrics_and_filmstrip"
                )
                evaluation = evaluate_task(physics, self.fixture.public_scenario)
            except UpstreamToolError as exc:
                raise self._upstream_error(request_id, exc) from None
            except ValueError:
                raise self._error(
                    request_id,
                    "SIMULATION_RESULT_INVALID",
                    "The public task returned an invalid numeric result.",
                    False,
                    "Start a fresh case after checking the pinned simulation runtime.",
                ) from None

            run_id = _new_id("run")
            condition_hash = self._public_condition_hash()
            execution = self._execution_fingerprint(
                condition_hash,
                revision.asset_sha256,
                ("contact_count", "body_xpos:end_effector"),
                value.capture != "metrics",
            )
            result_payload = {
                "run_id": run_id,
                "result": evaluation.result,
                "evaluation": {
                    "observations": [
                        item.model_dump(mode="json") for item in evaluation.observations
                    ],
                    "trace": [
                        item.model_dump(mode="json") for item in evaluation.trace
                    ],
                    "passed": evaluation.passed,
                },
            }
            result_bytes = canonical_json_bytes(result_payload)
            trace_ref, trace_internal = self._store_artifact(
                result_bytes,
                case_id=case.case_id,
                logical_name=f"{run_id}-task.json",
                public_kind="trace_json",
                internal_kind="task_result",
                media_type="application/json",
            )
            artifacts = [trace_ref]
            internal_refs = [trace_internal]
            warnings: list[str] = []
            if physics.image_png is not None:
                image_ref, image_internal = self._store_artifact(
                    physics.image_png,
                    case_id=case.case_id,
                    logical_name=f"{run_id}-filmstrip.png",
                    public_kind="filmstrip",
                    internal_kind="filmstrip",
                    media_type="image/png",
                )
                artifacts.append(image_ref)
                internal_refs.append(image_internal)
            elif physics.render_fallback:
                warnings.append(
                    "The numeric task completed, but the optional image was unavailable."
                )
            event_id = _new_id("evt")
            output = RunTaskOutput.from_evaluation(
                request_id=request_id,
                case_id=case.case_id,
                event_ids=[event_id],
                warnings=warnings,
                artifacts=artifacts,
                revision_id=revision.revision_id,
                evaluation=evaluation,
                parent_evaluation=parent_evaluation,
            )
            stored_run = StoredRunRecord(
                run_id=run_id,
                case_id=case.case_id,
                revision_id=revision.revision_id,
                run_kind="task",
                probe_kind=None,
                condition_hash=condition_hash,
                execution_fingerprint=execution,
                trace_sha256=trace_ref.sha256,
                metrics_sha256=trace_ref.sha256,
                passed=evaluation.passed,
            )
            try:
                self.store.record_run(
                    run=stored_run,
                    event=LedgerEventRecord(
                        event_id=event_id,
                        request_id=request_id,
                        case_id=case.case_id,
                        revision_id=revision.revision_id,
                        event_type="TASK_COMPLETED",
                        payload=result_payload,
                        artifact_refs=internal_refs,
                    ),
                )
            except StorageError:
                raise self._integrity_error(request_id) from None
            return output

    async def run_experiment(self, value: RunExperimentInput) -> RunExperimentOutput:
        request_id = self._begin("run_experiment", value, RunExperimentInput)
        async with self._lock:
            case = self._case(value.case_id, request_id)
            revision = self._revision(value.case_id, value.revision_id, request_id)
            self._require_run_budget(case.case_id, request_id)
            self._require_experiment_budget(case.case_id, request_id)
            self._latest_task_evaluation(case.case_id, revision.revision_id, request_id)
            initial_qpos, segment_controls, track = self._validate_experiment(
                value, request_id
            )
            hypothesis_id = _new_id("hyp")
            hypothesis_event_id = _new_id("evt")
            hypothesis_payload = {
                "hypothesis_id": hypothesis_id,
                "hypothesis": value.hypothesis.model_dump(mode="json"),
            }
            try:
                self.store.append_event(
                    LedgerEventRecord(
                        event_id=hypothesis_event_id,
                        request_id=request_id,
                        case_id=case.case_id,
                        revision_id=revision.revision_id,
                        event_type="HYPOTHESIS_RECORDED",
                        payload=hypothesis_payload,
                    )
                )
            except StorageError:
                raise self._integrity_error(request_id) from None

            requested_steps = sum(segment.n_steps for segment in value.segments)
            segments = tuple(
                ConstantSegment(
                    ctrl=controls,
                    n_steps=segment.n_steps,
                    label=segment.label or "",
                )
                for segment, controls in zip(value.segments, segment_controls)
            )
            boundaries = _segment_boundaries(value)
            condition_hash = self._experiment_condition_hash(
                initial_qpos, value, segment_controls
            )
            execution = self._execution_fingerprint(
                condition_hash,
                revision.asset_sha256,
                track,
                value.capture_final_snapshot,
            )
            run_id = _new_id("run")
            spec_bytes = canonical_json_bytes(
                {
                    "hypothesis_id": hypothesis_id,
                    "experiment": value.model_dump(mode="json"),
                    "condition_sha256": condition_hash,
                    "execution_fingerprint_sha256": execution,
                }
            )
            _, spec_internal = self._store_artifact(
                spec_bytes,
                case_id=case.case_id,
                logical_name=f"{run_id}-spec.json",
                public_kind="trace_json",
                internal_kind="experiment_spec",
                media_type="application/json",
            )
            asset_xml = self._asset_bytes(revision.asset_sha256, request_id)
            try:
                physics = await self.runner.run(
                    RunConfiguration(
                        xml_string=asset_xml.decode("utf-8"),
                        segments=segments,
                        initial_qpos=initial_qpos,
                        initial_qvel=(0.0,) * len(self.fixture.joint_names),
                        initial_ctrl=segment_controls[0],
                        track=track,
                        render=value.capture_final_snapshot,
                        render_width=160,
                        render_height=120,
                    )
                )
            except PartialRunError as exc:
                completed_boundaries = boundaries[: len(exc.partial_record.segments)]
                partial_bytes = canonical_json_bytes(
                    {
                        "run_id": run_id,
                        "requested_steps": requested_steps,
                        "completed_steps": exc.partial_record.step_count,
                        "segment_boundaries": [
                            boundary.model_dump(mode="json")
                            for boundary in completed_boundaries
                        ],
                        "segments": [
                            segment.as_dict() for segment in exc.partial_record.segments
                        ],
                    }
                )
                try:
                    _, partial_internal = self._store_artifact(
                        partial_bytes,
                        case_id=case.case_id,
                        logical_name=f"{run_id}-partial.json",
                        public_kind="trace_json",
                        internal_kind="partial_experiment",
                        media_type="application/json",
                    )
                    self.store.record_run(
                        run=StoredRunRecord(
                            run_id=run_id,
                            case_id=case.case_id,
                            revision_id=revision.revision_id,
                            run_kind="probe",
                            probe_kind="agent_defined",
                            condition_hash=condition_hash,
                            execution_fingerprint=execution,
                            trace_sha256=None,
                            metrics_sha256=None,
                            passed=False,
                        ),
                        event=LedgerEventRecord(
                            event_id=_new_id("evt"),
                            request_id=request_id,
                            case_id=case.case_id,
                            revision_id=revision.revision_id,
                            event_type="EXPERIMENT_FAILED",
                            payload={
                                "hypothesis_id": hypothesis_id,
                                "hypothesis_event_id": hypothesis_event_id,
                                "run_id": run_id,
                                "outcome": {
                                    "kind": "upstream_failure",
                                    "budget_consumed": True,
                                },
                                "failure_code": exc.code,
                                "requested_steps": requested_steps,
                                "completed_steps": exc.partial_record.step_count,
                                "completed_segment_boundaries": [
                                    boundary.model_dump(mode="json")
                                    for boundary in completed_boundaries
                                ],
                                "condition_sha256": condition_hash,
                                "execution_fingerprint_sha256": execution,
                            },
                            artifact_refs=[spec_internal, partial_internal],
                        ),
                    )
                except StorageError:
                    raise self._integrity_error(request_id) from None
                raise self._upstream_error(request_id, exc) from None
            except UpstreamToolError as exc:
                raise self._upstream_error(request_id, exc) from None
            except ValueError:
                raise self._error(
                    request_id,
                    "SIMULATION_RESULT_INVALID",
                    "The experiment returned an invalid numeric result.",
                    False,
                    "Start a fresh case after checking the pinned simulation runtime.",
                ) from None

            bad_step = first_nonfinite_step(physics)
            public_artifacts: list[ArtifactRef] = []
            internal_refs: list[Mapping[str, object]] = [spec_internal]
            warnings: list[str] = []
            trace = None
            trace_sha = None
            snapshot = None
            if bad_step is None:
                try:
                    trace = resample_experiment_trace(
                        physics,
                        observables=value.observables,
                        joint_names=self.fixture.joint_names,
                        actuator_names=self.fixture.actuator_names,
                    )
                    trace = validate_experiment_trace_contract(
                        trace,
                        observables=value.observables,
                        joint_names=self.fixture.joint_names,
                        actuator_names=self.fixture.actuator_names,
                    )
                except ValueError:
                    raise self._error(
                        request_id,
                        "SIMULATION_RESULT_INVALID",
                        "The experiment returned an invalid numeric result.",
                        False,
                        "Start a fresh case after checking the pinned simulation runtime.",
                    ) from None
                trace_bytes = canonical_json_bytes(trace.model_dump(mode="json"))
                trace_ref, trace_internal = self._store_artifact(
                    trace_bytes,
                    case_id=case.case_id,
                    logical_name=f"{run_id}-trace.json",
                    public_kind="trace_json",
                    internal_kind="trace_json",
                    media_type="application/json",
                )
                trace_sha = trace_ref.sha256
                public_artifacts.append(trace_ref)
                internal_refs.append(trace_internal)
                if physics.image_png is not None:
                    image_ref, image_internal = self._store_artifact(
                        physics.image_png,
                        case_id=case.case_id,
                        logical_name=f"{run_id}-final.png",
                        public_kind="filmstrip",
                        internal_kind="final_snapshot",
                        media_type="image/png",
                    )
                    public_artifacts.append(image_ref)
                    internal_refs.append(image_internal)
                    snapshot = FinalSnapshotMetadata(
                        artifact_id=image_ref.artifact_id,
                        uri=image_ref.uri,
                        sha256=image_ref.sha256,
                        bytes=image_ref.bytes,
                        step=requested_steps - 1,
                        width_px=160,
                        height_px=120,
                    )
                elif physics.render_fallback:
                    warnings.append(
                        "The numeric experiment completed, but the optional final image was unavailable."
                    )
                outcome = ExperimentOutcome(kind="completed", budget_consumed=True)
                completed_steps = requested_steps
                event_type = "EXPERIMENT_COMPLETED"
            else:
                outcome = ExperimentOutcome(
                    kind="non_finite_state",
                    budget_consumed=True,
                    first_bad_step=bad_step,
                )
                completed_steps = bad_step
                event_type = "EXPERIMENT_FAILED"
            stored_run = StoredRunRecord(
                run_id=run_id,
                case_id=case.case_id,
                revision_id=revision.revision_id,
                run_kind="probe",
                probe_kind="agent_defined",
                condition_hash=condition_hash,
                execution_fingerprint=execution,
                trace_sha256=trace_sha,
                metrics_sha256=None,
                passed=bad_step is None,
            )
            event_id = _new_id("evt")
            event_payload = {
                "hypothesis_id": hypothesis_id,
                "hypothesis_event_id": hypothesis_event_id,
                "run_id": run_id,
                "outcome": outcome.model_dump(mode="json"),
                "requested_steps": requested_steps,
                "completed_steps": completed_steps,
                "condition_sha256": condition_hash,
                "execution_fingerprint_sha256": execution,
            }
            try:
                self.store.record_run(
                    run=stored_run,
                    event=LedgerEventRecord(
                        event_id=event_id,
                        request_id=request_id,
                        case_id=case.case_id,
                        revision_id=revision.revision_id,
                        event_type=event_type,
                        payload=event_payload,
                        artifact_refs=internal_refs,
                    ),
                )
            except StorageError:
                raise self._integrity_error(request_id) from None
            return RunExperimentOutput(
                schema_version=SCHEMA_VERSION,
                request_id=request_id,
                case_id=case.case_id,
                event_ids=[hypothesis_event_id, event_id],
                warnings=warnings,
                artifacts=public_artifacts,
                revision_id=revision.revision_id,
                hypothesis_id=hypothesis_id,
                run_id=run_id,
                asset_sha256=revision.asset_sha256,
                condition_sha256=condition_hash,
                execution_fingerprint_sha256=execution,
                trace_sha256=trace_sha,
                outcome=outcome,
                requested_steps=requested_steps,
                completed_steps=completed_steps,
                segment_boundaries=boundaries,
                trace=trace,
                final_snapshot=snapshot,
            )

    async def create_revision(self, value: CreateRevisionInput) -> CreateRevisionOutput:
        request_id = self._begin("create_revision", value, CreateRevisionInput)
        async with self._lock:
            case = self._case(value.case_id, request_id)
            revisions = self.store.list_revisions(case.case_id)
            existing = self._existing_revision_retry(value, revisions, request_id)
            if existing is not None:
                revision, canonical_diff, event_id = existing
                return CreateRevisionOutput(
                    schema_version=SCHEMA_VERSION,
                    request_id=request_id,
                    case_id=case.case_id,
                    event_ids=[event_id],
                    revision_id=revision.revision_id,
                    parent_revision_id=revision.parent_revision_id,
                    asset_sha256=revision.asset_sha256,
                    canonical_diff=canonical_diff,
                    status="already_exists",
                )
            if case.head_revision_id != value.base_revision_id:
                raise self._error(
                    request_id,
                    "STALE_REVISION_HEAD",
                    "The requested base is not the current case head.",
                    False,
                    "Open the case again and cite the current head.",
                )
            if len(revisions) - 1 >= REVISION_BUDGET:
                raise self._error(
                    request_id,
                    "REVISION_BUDGET_EXHAUSTED",
                    "The child revision budget is exhausted.",
                    False,
                    "Verify the current head or start a fresh case.",
                )
            base = self._revision(case.case_id, value.base_revision_id, request_id)
            if base.asset_sha256 != value.expected_base_sha256:
                raise self._error(
                    request_id,
                    "BASE_HASH_MISMATCH",
                    "The expected base asset hash does not match the current revision.",
                    False,
                    "Inspect the current revision and retry with its asset hash.",
                )
            run, hypothesis_event = self._causal_experiment(value, request_id)
            base_xml = self._asset_bytes(base.asset_sha256, request_id)
            try:
                patched = apply_one_attribute_patch(
                    base_xml=base_xml,
                    expected_base_sha256=value.expected_base_sha256,
                    patch=value.patch,
                )
            except PatcherError as exc:
                raise self._error(
                    request_id,
                    exc.code,
                    "The requested single-attribute patch was rejected.",
                    False,
                    "Inspect the authored value and submit one permitted attribute change.",
                ) from None
            try:
                patched_valid = await self.runner.validate(patched.xml.decode("utf-8"))
            except UpstreamToolError as exc:
                raise self._upstream_error(request_id, exc) from None
            if not patched_valid:
                raise self._error(
                    request_id,
                    "PATCHED_ASSET_INVALID",
                    "The patched asset was rejected by the pinned simulation runtime.",
                    False,
                    "Revise the proposed attribute value and retry from the same base revision.",
                )
            child_id = f"r{base.ordinal + 1:03d}"
            canonical_diff = CanonicalDiffEntry(
                target=value.patch.target.name,
                attribute=value.patch.attribute,
                before=patched.canonical_diff[0].before,
                after=patched.canonical_diff[0].after,
            )
            patch_manifest = {
                "case_id": case.case_id,
                "revision_id": child_id,
                "parent_revision_id": base.revision_id,
                "asset_sha256": patched.asset_sha256,
                "basis_hypothesis_id": value.basis_hypothesis_id,
                "basis_experiment_run_id": value.basis_experiment_run_id,
                "patch": value.patch.model_dump(mode="json"),
                "rationale": value.rationale,
                "expected_effect": value.expected_effect.model_dump(mode="json"),
                "canonical_diff": [canonical_diff.model_dump(mode="json")],
            }
            manifest_bytes = canonical_json_bytes(patch_manifest)
            manifest_ref = self.store.objects.put_bytes(manifest_bytes)
            asset_ref = self.store.objects.put_bytes(
                patched.xml, expected_sha256=patched.asset_sha256
            )
            revision = RevisionRecord(
                case_id=case.case_id,
                revision_id=child_id,
                parent_revision_id=base.revision_id,
                ordinal=base.ordinal + 1,
                asset_sha256=asset_ref.sha256,
                patch_manifest_sha256=manifest_ref.sha256,
                hypothesis_event_id=hypothesis_event.event_id,
                probe_run_id=run.run_id,
            )
            event_id = _new_id("evt")
            probe_payload = {
                "run_id": run.run_id,
                "case_id": run.case_id,
                "revision_id": run.revision_id,
                "run_kind": run.run_kind,
                "probe_kind": run.probe_kind,
                "condition_hash": run.condition_hash,
                "execution_fingerprint": run.execution_fingerprint,
                "trace_sha256": run.trace_sha256,
                "metrics_sha256": run.metrics_sha256,
                "passed": run.passed,
            }
            revision_payload = {
                "parent_revision_id": revision.parent_revision_id,
                "ordinal": revision.ordinal,
                "asset_sha256": revision.asset_sha256,
                "patch_manifest_sha256": revision.patch_manifest_sha256,
                "hypothesis_event_id": revision.hypothesis_event_id,
                "probe_run_id": revision.probe_run_id,
                "probe_run": probe_payload,
                "canonical_diff": [canonical_diff.model_dump(mode="json")],
            }
            try:
                self.store.commit_revision_with_event(
                    revision=revision,
                    event=LedgerEventRecord(
                        event_id=event_id,
                        request_id=request_id,
                        case_id=case.case_id,
                        revision_id=child_id,
                        event_type="REVISION_CREATED",
                        payload=revision_payload,
                        artifact_refs=(
                            {
                                "sha256": manifest_ref.sha256,
                                "kind": "patch_manifest",
                                "size": manifest_ref.bytes,
                                "media_type": "application/json",
                            },
                            {
                                "sha256": asset_ref.sha256,
                                "kind": "repaired_mjcf",
                                "size": asset_ref.bytes,
                                "media_type": "application/xml",
                            },
                        ),
                    ),
                    expected_head_revision_id=base.revision_id,
                )
            except (RevisionConflictError, StorageError):
                raise self._error(
                    request_id,
                    "REVISION_CONFLICT",
                    "The revision could not advance the current linear head.",
                    False,
                    "Open the case again and retry from the current head.",
                ) from None
            return CreateRevisionOutput(
                schema_version=SCHEMA_VERSION,
                request_id=request_id,
                case_id=case.case_id,
                event_ids=[event_id],
                revision_id=child_id,
                parent_revision_id=base.revision_id,
                asset_sha256=patched.asset_sha256,
                canonical_diff=[canonical_diff],
                status="created",
            )

    async def verify_revision(self, value: VerifyRevisionInput) -> VerifyRevisionOutput:
        request_id = self._begin("verify_revision", value, VerifyRevisionInput)
        async with self._lock:
            case = self._case(value.case_id, request_id)
            revision = self._revision(case.case_id, value.revision_id, request_id)
            revisions = self.store.list_revisions(case.case_id)
            if (
                case.head_revision_id != revision.revision_id
                or not 1 <= revision.ordinal <= REVISION_BUDGET
                or len(revisions) != revision.ordinal + 1
            ):
                raise self._error(
                    request_id,
                    "QUALIFICATION_NOT_READY",
                    "Qualification requires a current child head within the revision budget.",
                    False,
                    "Run the public task for the current evidence-backed child head, then retry.",
                )
            if revision.asset_sha256 != value.expected_asset_sha256:
                raise self._error(
                    request_id,
                    "ASSET_HASH_MISMATCH",
                    "The expected asset hash does not match the qualification revision.",
                    False,
                    "Open the case and retry with the current asset hash.",
                )
            if case.qualification_state != "unused":
                stored = self._stored_qualification_output(case, revision, request_id)
                if stored is not None:
                    return stored
                raise self._error(
                    request_id,
                    "QUALIFICATION_ALREADY_USED",
                    "The qualification budget for this case has already been used.",
                    False,
                    "Use a fresh pre-provisioned case for another qualification attempt.",
                )
            previous = self._latest_task_evaluation(
                case.case_id, revision.revision_id, request_id
            )
            if not previous.passed:
                raise self._error(
                    request_id,
                    "PUBLIC_PASS_REQUIRED",
                    "The current head must pass the public task before qualification.",
                    False,
                    "Run the public task and repair any remaining contract failures.",
                )
            asset_xml = self._asset_bytes(revision.asset_sha256, request_id)
            try:
                public_record = await self._run_public(asset_xml, render=False)
                public_evaluation = evaluate_task(
                    public_record, self.fixture.public_scenario
                )
            except UpstreamToolError as exc:
                raise self._upstream_error(request_id, exc) from None
            except ValueError:
                raise self._error(
                    request_id,
                    "PUBLIC_RECHECK_INVALID",
                    "The independent public recheck returned an invalid result.",
                    False,
                    "Start a fresh case after checking the pinned simulation runtime.",
                ) from None
            if not public_evaluation.passed:
                raise self._error(
                    request_id,
                    "PUBLIC_RECHECK_FAILED",
                    "The independent public recheck did not pass.",
                    False,
                    "Do not qualify this revision; inspect the current public behavior.",
                )
            integrity = self._integrity_checks(case, revisions, request_id)
            if not all(integrity.model_dump().values()):
                raise self._integrity_error(request_id)
            public_result = AggregateResult(
                passed=int(public_evaluation.passed),
                total=1,
                violated_clause_ids=list(public_evaluation.violated_clause_ids),
            )
            attempt_id = _new_id("attempt")
            commitments = self._commitments(case)
            try:
                self.store.reserve_qualification(
                    case_id=case.case_id,
                    revision_id=revision.revision_id,
                    attempt_id=attempt_id,
                    suite_commitment_sha256=self.hidden_verifier.suite_commitment_sha256,
                    scenario_hashes=self.hidden_verifier.scenario_hashes,
                    expected_head_revision_id=revision.revision_id,
                    **commitments,
                )
            except QualificationConflictError:
                raise self._error(
                    request_id,
                    "QUALIFICATION_CONFLICT",
                    "The qualification reservation was not available.",
                    False,
                    "Use a fresh case for another qualification attempt.",
                ) from None
            reservation_event = self.store.ledger_events(case.case_id)[-1].event_id
            try:
                hidden = await self.hidden_verifier.verify(asset_xml)
            except BaseException as exc:
                failure_result = {
                    "public_result": public_result.model_dump(mode="json"),
                    "holdout_result": {
                        "passed": 0,
                        "total": 3,
                        "violated_clause_ids": ["finite_state"],
                    },
                    "failure": "qualification_execution_failed",
                }
                self.store.record_qualification_terminal(
                    case_id=case.case_id,
                    attempt_id=attempt_id,
                    revision_id=revision.revision_id,
                    suite_commitment_sha256=self.hidden_verifier.suite_commitment_sha256,
                    scenario_hashes=self.hidden_verifier.scenario_hashes,
                    state="FAILED",
                    result=failure_result,
                )
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise self._error(
                    request_id,
                    "QUALIFICATION_EXECUTION_FAILED",
                    "The private qualification suite did not complete.",
                    False,
                    "Use a fresh case after checking the pinned simulation runtime.",
                ) from None
            holdout_result = hidden.aggregate()
            cumulative_diff = [
                entry
                for item in revisions[1:]
                for entry in self._revision_summary(item).canonical_diff
            ]
            ticket = None
            passed = hidden.passed == hidden.total and not hidden.violated_clause_ids
            if passed:
                ticket = build_promotion_ticket(
                    ticket_id=_new_id("evt"),
                    case_id=case.case_id,
                    revision_id=revision.revision_id,
                    asset_sha256=revision.asset_sha256,
                    canonical_diff=cumulative_diff,
                    public_result=public_result,
                    holdout_result=holdout_result,
                    commitment_hashes=commitments,
                )
            terminal_payload = {
                "integrity": integrity.model_dump(mode="json"),
                "public_result": public_result.model_dump(mode="json"),
                "holdout_result": holdout_result.model_dump(mode="json"),
                "ticket": ticket.model_dump(mode="json")
                if ticket is not None
                else None,
            }
            qualification_bytes = canonical_json_bytes(terminal_payload)
            qualification_ref, _ = self._store_artifact(
                qualification_bytes,
                case_id=case.case_id,
                logical_name=f"{revision.revision_id}-qualification.json",
                public_kind="qualification",
                internal_kind="qualification",
                media_type="application/json",
            )
            try:
                self.store.record_qualification_terminal(
                    case_id=case.case_id,
                    attempt_id=attempt_id,
                    revision_id=revision.revision_id,
                    suite_commitment_sha256=self.hidden_verifier.suite_commitment_sha256,
                    scenario_hashes=self.hidden_verifier.scenario_hashes,
                    state="PASSED" if passed else "FAILED",
                    result=terminal_payload,
                )
            except StorageError:
                raise self._integrity_error(request_id) from None
            terminal_event = self.store.ledger_events(case.case_id)[-1].event_id
            return VerifyRevisionOutput(
                schema_version=SCHEMA_VERSION,
                request_id=request_id,
                case_id=case.case_id,
                event_ids=[reservation_event, terminal_event],
                artifacts=[qualification_ref],
                revision_id=revision.revision_id,
                asset_sha256=revision.asset_sha256,
                integrity=integrity,
                public_result=public_result,
                holdout_result=holdout_result,
                promotion_ticket=ticket,
            )

    async def validate_promotion_acceptance(self, ticket: PromotionTicket) -> bool:
        if not isinstance(ticket, PromotionTicket):
            return False
        async with self._lock:
            try:
                case = self.store.get_case(ticket.case_id)
            except StorageError:
                return False
            if (
                case.qualification_state != "passed"
                or case.head_revision_id != ticket.revision_id
            ):
                return False
            try:
                revision = self.store.get_revision(ticket.case_id, ticket.revision_id)
            except StorageError:
                return False
            return (
                revision.asset_sha256 == ticket.asset_sha256
                and validate_promotion_ticket(
                    ticket, commitment_hashes=self._commitments(case)
                )
            )

    def _provision_demo_case(self) -> None:
        source = self.store.objects.put_bytes(
            self.fixture.asset_xml, expected_sha256=self.fixture.source_asset_sha256
        )
        commitments = {
            "source_asset_sha256": source.sha256,
            "controller_sha256": self.fixture.controller_sha256,
            "public_contract_sha256": self.fixture.public_contract_sha256,
            "runner_sha256": self.fixture.runner_sha256,
            "holdout_commitment_sha256": self.hidden_verifier.holdout_commitment_sha256,
        }
        try:
            self.store.create_preprovisioned_case(
                case_id=self.fixture.case_id,
                root_revision_id=self.fixture.root_revision_id,
                **commitments,
            )
        except CaseAlreadyExistsError:
            case = self.store.get_case(self.fixture.case_id)
            if (
                self._commitments(case) != commitments
                or case.root_revision_id != self.fixture.root_revision_id
            ):
                raise IntegrityError(
                    "existing demo case commitments do not match the immutable fixture"
                )
            revision = self.store.get_revision(case.case_id, case.root_revision_id)
            if revision.asset_sha256 != source.sha256:
                raise IntegrityError(
                    "existing demo root does not match the immutable fixture"
                )

    async def _run_public(self, asset_xml: bytes, *, render: bool) -> PhysicsRunRecord:
        scenario = self.fixture.public_scenario
        return await self.runner.run(
            RunConfiguration(
                xml_string=asset_xml.decode("utf-8"),
                initial_qpos=scenario.initial_qpos,
                initial_qvel=(0.0,) * len(self.fixture.joint_names),
                initial_ctrl=scenario.target_qpos,
                segments=(
                    ConstantSegment(
                        ctrl=scenario.target_qpos,
                        n_steps=scenario.duration_steps,
                        label="public_center",
                    ),
                ),
                track=("contact_count", "body_xpos:end_effector"),
                render=render,
                render_width=160,
                render_height=120,
            )
        )

    def _validate_experiment(
        self, value: RunExperimentInput, request_id: str
    ) -> tuple[tuple[float, ...], tuple[tuple[float, ...], ...], tuple[str, ...]]:
        initial = {
            item.joint_name: item.position_rad for item in value.initial_joint_positions
        }
        if set(initial) != set(self.fixture.joint_names) or len(initial) != len(
            self.fixture.joint_names
        ):
            raise self._error(
                request_id,
                "EXPERIMENT_JOINT_SET_INVALID",
                "Initial positions must name every hinge joint exactly once.",
                False,
                "Use the joint names returned by open_case.",
            )
        if any(
            not self.fixture.joint_range[0] <= item <= self.fixture.joint_range[1]
            for item in initial.values()
        ):
            raise self._error(
                request_id,
                "EXPERIMENT_JOINT_RANGE_INVALID",
                "An initial joint position is outside its permitted range.",
                False,
                "Use the joint ranges returned by open_case.",
            )
        ordered_initial = tuple(initial[name] for name in self.fixture.joint_names)
        segment_controls = []
        for segment in value.segments:
            controls = {item.actuator_name: item.value for item in segment.controls}
            if set(controls) != set(self.fixture.actuator_names) or len(
                controls
            ) != len(self.fixture.actuator_names):
                raise self._error(
                    request_id,
                    "EXPERIMENT_ACTUATOR_SET_INVALID",
                    "Every segment must name every position actuator exactly once.",
                    False,
                    "Use the actuator names returned by open_case.",
                )
            if any(
                not self.fixture.control_range[0]
                <= item
                <= self.fixture.control_range[1]
                for item in controls.values()
            ):
                raise self._error(
                    request_id,
                    "EXPERIMENT_CONTROL_RANGE_INVALID",
                    "A segment control is outside its permitted range.",
                    False,
                    "Use the actuator control ranges returned by open_case.",
                )
            segment_controls.append(
                tuple(controls[name] for name in self.fixture.actuator_names)
            )
        topology = {
            "joint": set(self.fixture.joint_names),
            "actuator": set(self.fixture.actuator_names),
            "body": set(self.fixture.body_names),
            "site": set(),
        }
        references = [
            *value.hypothesis.suspected_elements,
            *value.hypothesis.competing_explanation.suspected_elements,
        ]
        if any(
            reference.name not in topology[reference.kind] for reference in references
        ):
            raise self._error(
                request_id,
                "EXPERIMENT_ELEMENT_UNKNOWN",
                "A suspected element is not present in this asset.",
                False,
                "Use names returned by open_case or inspect_asset.",
            )
        track = []
        for observable in value.observables:
            if isinstance(observable, ContactCountObservable):
                track.append("contact_count")
            elif isinstance(observable, BodyPositionObservable):
                if observable.body_name not in self.fixture.body_names:
                    raise self._error(
                        request_id,
                        "EXPERIMENT_BODY_UNKNOWN",
                        "A requested body position is not present in this asset.",
                        False,
                        "Use body names returned by open_case.",
                    )
                track.append(f"body_xpos:{observable.body_name}")
        projected_scalars = _run_record_scalar_count(
            segments=tuple(
                (segment.n_steps, len(controls))
                for segment, controls in zip(
                    value.segments, segment_controls, strict=True
                )
            ),
            nq=len(self.fixture.joint_names),
            nv=len(self.fixture.joint_names),
            track=tuple(track),
        )
        if projected_scalars > MAX_TRACE_SCALARS:
            raise self._error(
                request_id,
                "EXPERIMENT_SCALAR_BUDGET_EXCEEDED",
                "The requested experiment exceeds the numeric record budget.",
                False,
                "Reduce the step count or number of body-position observables.",
            )
        return ordered_initial, tuple(segment_controls), tuple(track)

    def _causal_experiment(
        self, value: CreateRevisionInput, request_id: str
    ) -> tuple[StoredRunRecord, LedgerEvent]:
        try:
            run = self.store.get_run(value.basis_experiment_run_id)
        except StorageError:
            raise self._error(
                request_id,
                "CAUSAL_EXPERIMENT_NOT_FOUND",
                "The cited experiment run was not found.",
                False,
                "Cite a completed experiment from the current base revision.",
            ) from None
        if (
            run.case_id != value.case_id
            or run.revision_id != value.base_revision_id
            or run.run_kind != "probe"
            or run.probe_kind != "agent_defined"
            or not run.passed
            or run.trace_sha256 is None
        ):
            raise self._error(
                request_id,
                "CAUSAL_EXPERIMENT_INVALID",
                "The cited run is not a completed experiment on this base revision.",
                False,
                "Cite a completed finite experiment from the current base revision.",
            )
        experiments = [
            event
            for event in self.store.ledger_events(value.case_id)
            if event.event_type == "EXPERIMENT_COMPLETED"
            and event.payload.get("run_id") == run.run_id
        ]
        if (
            len(experiments) != 1
            or experiments[0].payload.get("hypothesis_id") != value.basis_hypothesis_id
        ):
            raise self._error(
                request_id,
                "CAUSAL_HYPOTHESIS_INVALID",
                "The cited hypothesis does not own the completed experiment.",
                False,
                "Use the hypothesis ID returned with that experiment run.",
            )
        event_id = experiments[0].payload.get("hypothesis_event_id")
        hypothesis = [
            event
            for event in self.store.ledger_events(value.case_id)
            if event.event_id == event_id
        ]
        if len(hypothesis) != 1 or hypothesis[0].event_type != "HYPOTHESIS_RECORDED":
            raise self._integrity_error(request_id)
        hypothesis_payload = hypothesis[0].payload.get("hypothesis")
        candidate_attributes = {
            (element.get("kind"), element.get("name"), attribute)
            for container in (
                hypothesis_payload,
                hypothesis_payload.get("competing_explanation")
                if isinstance(hypothesis_payload, Mapping)
                else None,
            )
            if isinstance(container, Mapping)
            for element in container.get("suspected_elements", [])
            if isinstance(element, Mapping)
            for attribute in element.get("attributes", [])
        }
        patch_attribute = (
            value.patch.target.kind,
            value.patch.target.name,
            value.patch.attribute,
        )
        if patch_attribute not in candidate_attributes:
            raise self._error(
                request_id,
                "CAUSAL_PATCH_UNBOUND",
                "The requested patch is not bound to the cited hypothesis.",
                False,
                "Patch only a joint attribute preregistered by the cited experiment.",
            )
        return run, hypothesis[0]

    def _existing_revision_retry(
        self,
        value: CreateRevisionInput,
        revisions: Sequence[RevisionRecord],
        request_id: str,
    ) -> tuple[RevisionRecord, list[CanonicalDiffEntry], str] | None:
        expected = {
            "basis_hypothesis_id": value.basis_hypothesis_id,
            "basis_experiment_run_id": value.basis_experiment_run_id,
            "patch": value.patch.model_dump(mode="json"),
            "rationale": value.rationale,
            "expected_effect": value.expected_effect.model_dump(mode="json"),
        }
        base = next(
            (item for item in revisions if item.revision_id == value.base_revision_id),
            None,
        )
        if base is None or base.asset_sha256 != value.expected_base_sha256:
            return None
        for revision in revisions:
            if (
                revision.parent_revision_id != value.base_revision_id
                or revision.patch_manifest_sha256 is None
            ):
                continue
            try:
                manifest = json.loads(
                    self.store.objects.read_bytes(revision.patch_manifest_sha256)
                )
            except (ObjectIntegrityError, json.JSONDecodeError):
                raise self._integrity_error(request_id) from None
            if not all(manifest.get(key) == item for key, item in expected.items()):
                continue
            diffs = [
                CanonicalDiffEntry.model_validate(item)
                for item in manifest.get("canonical_diff", [])
            ]
            events = [
                event
                for event in self.store.ledger_events(value.case_id)
                if event.event_type == "REVISION_CREATED"
                and event.revision_id == revision.revision_id
            ]
            if len(diffs) != 1 or len(events) != 1:
                raise self._integrity_error(request_id)
            return revision, diffs, events[0].event_id
        return None

    def _stored_qualification_output(
        self, case, revision: RevisionRecord, request_id: str
    ) -> VerifyRevisionOutput | None:
        qualification = self.store.get_qualification(case.case_id)
        if (
            qualification is None
            or qualification.revision_id != revision.revision_id
            or qualification.state not in {"PASSED", "FAILED"}
            or not isinstance(qualification.result, Mapping)
        ):
            return None
        result = qualification.result
        if not all(
            key in result
            for key in ("integrity", "public_result", "holdout_result", "ticket")
        ):
            return None
        try:
            integrity = IntegrityChecks.model_validate(result["integrity"])
            public_result = AggregateResult.model_validate(result["public_result"])
            holdout_result = AggregateResult.model_validate(result["holdout_result"])
            ticket = (
                PromotionTicket.model_validate(result["ticket"])
                if result["ticket"] is not None
                else None
            )
        except ValueError:
            raise self._integrity_error(request_id) from None
        payload_bytes = canonical_json_bytes(dict(result))
        artifact, _ = self._store_artifact(
            payload_bytes,
            case_id=case.case_id,
            logical_name=f"{revision.revision_id}-qualification.json",
            public_kind="qualification",
            internal_kind="qualification",
            media_type="application/json",
        )
        events = [
            event.event_id
            for event in self.store.ledger_events(case.case_id)
            if event.revision_id == revision.revision_id
            and event.event_type
            in {
                "QUALIFICATION_RESERVED",
                "QUALIFICATION_PASSED",
                "QUALIFICATION_FAILED",
            }
        ]
        if len(events) != 2:
            raise self._integrity_error(request_id)
        return VerifyRevisionOutput(
            schema_version=SCHEMA_VERSION,
            request_id=request_id,
            case_id=case.case_id,
            event_ids=events,
            artifacts=[artifact],
            revision_id=revision.revision_id,
            asset_sha256=revision.asset_sha256,
            integrity=integrity,
            public_result=public_result,
            holdout_result=holdout_result,
            promotion_ticket=ticket,
        )

    def _latest_task_evaluation(
        self, case_id: str, revision_id: str, request_id: str
    ) -> TaskEvaluation:
        for event in reversed(self.store.ledger_events(case_id)):
            if event.event_type != "TASK_COMPLETED" or event.revision_id != revision_id:
                continue
            try:
                event_payload = event.payload
                if set(event_payload) != {"run_id", "result", "evaluation"}:
                    raise ValueError("task event payload fields are invalid")
                payload = event_payload["evaluation"]
                if not isinstance(payload, Mapping) or set(payload) != {
                    "observations",
                    "trace",
                    "passed",
                }:
                    raise ValueError("stored task evaluation fields are invalid")
                from .schemas import MetricObservation, TracePoint

                observations = tuple(
                    MetricObservation.model_validate(item)
                    for item in payload["observations"]
                )
                if tuple(item.metric for item in observations) != TASK_METRIC_ORDER:
                    raise ValueError("stored task metrics are invalid")
                trace = tuple(
                    TracePoint.model_validate(item) for item in payload["trace"]
                )
                if len(trace) not in {0, 51}:
                    raise ValueError("stored task trace length is invalid")
                evaluation = TaskEvaluation(observations=observations, trace=trace)
                if (
                    type(payload["passed"]) is not bool
                    or payload["passed"] != evaluation.passed
                ):
                    raise ValueError("stored task pass state is invalid")
                if event_payload["result"] != evaluation.result:
                    raise ValueError("stored task result is invalid")
                run = self.store.get_run(event_payload["run_id"])
                if (
                    run.case_id != case_id
                    or run.revision_id != revision_id
                    or run.run_kind != "task"
                    or run.probe_kind is not None
                    or run.passed != evaluation.passed
                    or run.trace_sha256 is None
                    or run.trace_sha256 != run.metrics_sha256
                ):
                    raise ValueError("stored task run is inconsistent")
                task_refs = [
                    reference
                    for reference in event.artifact_refs
                    if reference["kind"] == "task_result"
                ]
                if (
                    len(task_refs) != 1
                    or task_refs[0]["sha256"] != run.trace_sha256
                    or self.store.objects.read_bytes(run.trace_sha256)
                    != canonical_json_bytes(event_payload)
                ):
                    raise ValueError("stored task artifact is inconsistent")
                return evaluation
            except (KeyError, TypeError, ValueError, StorageError):
                raise self._integrity_error(request_id) from None
        raise self._error(
            request_id,
            "BASELINE_REQUIRED",
            "A completed public baseline is required for this revision.",
            False,
            "Run the fixed public task for this revision first.",
        )

    def _integrity_checks(
        self, case, revisions: Sequence[RevisionRecord], request_id: str
    ) -> IntegrityChecks:
        try:
            original = (
                hashlib.sha256(
                    self.store.objects.read_bytes(case.source_asset_sha256)
                ).hexdigest()
                == case.source_asset_sha256
            )
            controller = case.controller_sha256 == self.fixture.controller_sha256
            contract = (
                case.public_contract_sha256 == self.fixture.public_contract_sha256
            )
            runner = case.runner_sha256 == self.fixture.runner_sha256
            lineage = (
                1 <= len(revisions) - 1 <= REVISION_BUDGET
                and revisions[0].revision_id == "r000"
                and revisions[0].ordinal == 0
                and revisions[-1].revision_id == case.head_revision_id
            )
            for parent, child in zip(revisions, revisions[1:]):
                lineage = (
                    lineage
                    and child.parent_revision_id == parent.revision_id
                    and child.ordinal == parent.ordinal + 1
                )
                lineage = lineage and child.patch_manifest_sha256 is not None
                self.store.objects.read_bytes(child.asset_sha256)
                self.store.objects.read_bytes(child.patch_manifest_sha256 or "")
            self.store.verify_ledger()
        except (StorageError, ValueError):
            raise self._integrity_error(request_id) from None
        return IntegrityChecks(
            original=original,
            controller=controller,
            contract=contract,
            runner=runner,
            lineage=lineage,
        )

    def _inspect_revision(self, case_id: str, revision_id: str, request_id: str):
        revision = self._revision(case_id, revision_id, request_id)
        source = self._asset_bytes(revision.asset_sha256, request_id)
        try:
            root = ET.fromstring(source)
            option = root.find("option")
            timestep = (
                float(option.attrib["timestep"])
                if option is not None
                else self.fixture.timestep_s
            )
            body_parent = {"world": None}
            for body in root.findall(".//body"):
                parent = "world"
                for candidate in root.findall(".//body"):
                    if body in list(candidate):
                        parent = candidate.attrib["name"]
                        break
                body_parent[body.attrib["name"]] = parent
            joint_parent = {}
            for body in root.findall(".//body"):
                for joint in body.findall("joint"):
                    joint_parent[joint.attrib["name"]] = body.attrib["name"]
            joints = []
            for joint in root.findall(".//joint"):
                axis = tuple(float(item) for item in joint.attrib["axis"].split())
                position_range = tuple(
                    float(item) for item in joint.attrib["range"].split()
                )
                joints.append(
                    JointSummary(
                        name=joint.attrib["name"],
                        axis=axis,
                        damping=float(joint.attrib["damping"]),
                        armature=float(joint.attrib["armature"]),
                        frictionloss=float(joint.attrib["frictionloss"]),
                        position_range=position_range,
                        body_parent=joint_parent[joint.attrib["name"]],
                    )
                )
            bodies = [
                BodySummary(name=name, parent=parent)
                for name, parent in body_parent.items()
            ]
            actuators = []
            actuator_root = root.find("actuator")
            if actuator_root is not None:
                for actuator in actuator_root.findall("position"):
                    control_range = tuple(
                        float(item) for item in actuator.attrib["ctrlrange"].split()
                    )
                    actuators.append(
                        ActuatorSummary(
                            name=actuator.attrib["name"],
                            joint_name=actuator.attrib["joint"],
                            control_kind="position",
                            control_range=control_range,
                        )
                    )
        except (ET.ParseError, KeyError, ValueError):
            raise self._integrity_error(request_id) from None
        return (
            joints,
            bodies,
            actuators,
            CompiledDimensions(
                nq=len(joints), nv=len(joints), nu=len(actuators), timestep_s=timestep
            ),
        )

    def _revision_summary(self, revision: RevisionRecord) -> RevisionSummary:
        diffs = []
        if revision.patch_manifest_sha256 is not None:
            manifest = json.loads(
                self.store.objects.read_bytes(revision.patch_manifest_sha256)
            )
            diffs = [
                CanonicalDiffEntry.model_validate(item)
                for item in manifest["canonical_diff"]
            ]
        return RevisionSummary(
            revision_id=revision.revision_id,
            asset_sha256=revision.asset_sha256,
            parent_revision_id=revision.parent_revision_id,
            canonical_diff=diffs,
        )

    def _public_event_tail(
        self, events: Sequence[LedgerEvent]
    ) -> list[PublicEventSummary]:
        mapping = {
            "CASE_CREATED": (
                "CASE_OPENED",
                "The immutable repair case was provisioned.",
            ),
            "TASK_COMPLETED": ("TASK_COMPLETED", "The fixed public task completed."),
            "HYPOTHESIS_RECORDED": (
                "HYPOTHESIS_RECORDED",
                "An experiment hypothesis was registered before execution.",
            ),
            "EXPERIMENT_COMPLETED": (
                "EXPERIMENT_COMPLETED",
                "A finite agent-defined experiment completed.",
            ),
            "EXPERIMENT_FAILED": (
                "EXPERIMENT_FAILED",
                "An agent-defined experiment did not complete.",
            ),
            "REVISION_CREATED": (
                "REVISION_CREATED",
                "A single-attribute child revision was created.",
            ),
            "QUALIFICATION_PASSED": (
                "QUALIFICATION_PASSED",
                "The fixed qualification suite passed.",
            ),
            "QUALIFICATION_FAILED": (
                "QUALIFICATION_FAILED",
                "The fixed qualification suite did not pass.",
            ),
        }
        public = []
        for event in events:
            if event.event_type in mapping:
                kind, summary = mapping[event.event_type]
                public.append(
                    PublicEventSummary(
                        event_id=event.event_id, kind=kind, summary=summary
                    )
                )
        return public[-20:]

    def _budgets(
        self,
        case_id: str,
        events: Sequence[LedgerEvent],
        revisions: Sequence[RevisionRecord],
        qualification_state: str,
    ) -> BudgetSummary:
        run_count = sum(
            event.event_type
            in {"TASK_COMPLETED", "EXPERIMENT_COMPLETED", "EXPERIMENT_FAILED"}
            for event in events
        )
        experiment_count = sum(
            event.event_type in {"EXPERIMENT_COMPLETED", "EXPERIMENT_FAILED"}
            for event in events
        )
        return BudgetSummary(
            runs_remaining=max(0, TOTAL_RUN_BUDGET - run_count),
            experiments_remaining=max(0, EXPERIMENT_BUDGET - experiment_count),
            revisions_remaining=max(0, REVISION_BUDGET - (len(revisions) - 1)),
            qualification_remaining=QUALIFICATION_BUDGET
            if qualification_state == "unused"
            else 0,
        )

    def _require_run_budget(self, case_id: str, request_id: str) -> None:
        events = self.store.ledger_events(case_id)
        used = sum(
            event.event_type
            in {"TASK_COMPLETED", "EXPERIMENT_COMPLETED", "EXPERIMENT_FAILED"}
            for event in events
        )
        if used >= TOTAL_RUN_BUDGET:
            raise self._error(
                request_id,
                "RUN_BUDGET_EXHAUSTED",
                "The case run budget is exhausted.",
                False,
                "Start a fresh case.",
            )

    def _require_experiment_budget(self, case_id: str, request_id: str) -> None:
        events = self.store.ledger_events(case_id)
        used = sum(
            event.event_type in {"EXPERIMENT_COMPLETED", "EXPERIMENT_FAILED"}
            for event in events
        )
        if used >= EXPERIMENT_BUDGET:
            raise self._error(
                request_id,
                "EXPERIMENT_BUDGET_EXHAUSTED",
                "The case experiment budget is exhausted.",
                False,
                "Use existing evidence or start a fresh case.",
            )

    def _public_condition_hash(self) -> str:
        scenario = self.fixture.public_scenario
        return _sha256(
            {
                "initial_qpos": scenario.initial_qpos,
                "controls": scenario.target_qpos,
                "steps": scenario.duration_steps,
            }
        )

    def _experiment_condition_hash(
        self,
        initial_qpos: Sequence[float],
        value: RunExperimentInput,
        controls: Sequence[Sequence[float]],
    ) -> str:
        return _sha256(
            {
                "initial_qpos": list(initial_qpos),
                "initial_qvel": [0.0] * len(initial_qpos),
                "segments": [
                    {"n_steps": segment.n_steps, "controls": list(control)}
                    for segment, control in zip(value.segments, controls)
                ],
            }
        )

    def _execution_fingerprint(
        self, condition_hash: str, asset_sha256: str, track: Sequence[str], render: bool
    ) -> str:
        return _sha256(
            {
                "condition_sha256": condition_hash,
                "asset_sha256": asset_sha256,
                "runner_sha256": self.fixture.runner_sha256,
                "fixture_version": self.fixture.version,
                "mujoco_version": "3.5.0",
                "upstream_commit": UPSTREAM_COMMIT,
                "track": list(track),
                "render": render,
            }
        )

    def _store_artifact(
        self,
        data: bytes,
        *,
        case_id: str,
        logical_name: str,
        public_kind: str,
        internal_kind: str,
        media_type: str,
    ):
        reference = self.store.objects.put_bytes(data)
        public = ArtifactRef(
            artifact_id=f"art_{reference.sha256[:24]}",
            kind=public_kind,
            uri=f"autopsy://evidence/{case_id}/{reference.sha256}/{logical_name}",
            media_type=media_type,
            sha256=reference.sha256,
            bytes=reference.bytes,
        )
        internal = {
            "sha256": reference.sha256,
            "kind": internal_kind,
            "size": reference.bytes,
            "media_type": media_type,
        }
        return public, internal

    def _case(self, case_id: str, request_id: str):
        try:
            return self.store.get_case(case_id)
        except CaseNotFoundError:
            raise self._error(
                request_id,
                "CASE_NOT_FOUND",
                "The requested repair case was not found.",
                False,
                "Use the pre-provisioned case ID.",
            ) from None
        except StorageError:
            raise self._integrity_error(request_id) from None

    def _revision(
        self, case_id: str, revision_id: str, request_id: str
    ) -> RevisionRecord:
        try:
            return self.store.get_revision(case_id, revision_id)
        except RevisionNotFoundError:
            raise self._error(
                request_id,
                "REVISION_NOT_FOUND",
                "The requested revision was not found in this case.",
                False,
                "Open the case and use a listed revision ID.",
            ) from None
        except StorageError:
            raise self._integrity_error(request_id) from None

    def _asset_bytes(self, sha256: str, request_id: str) -> bytes:
        try:
            return self.store.objects.read_bytes(sha256)
        except ObjectIntegrityError:
            raise self._integrity_error(request_id) from None

    def _commitments(self, case) -> dict[str, str]:
        return {
            "source_asset_sha256": case.source_asset_sha256,
            "controller_sha256": case.controller_sha256,
            "public_contract_sha256": case.public_contract_sha256,
            "runner_sha256": case.runner_sha256,
            "holdout_commitment_sha256": case.holdout_commitment_sha256,
        }

    def _begin(self, tool: str, value: object, expected: type) -> str:
        if not isinstance(value, expected):
            request_id = _new_id("req")
            raise self._error(
                request_id,
                "INVALID_TOOL_INPUT",
                "The tool input did not match its strict schema model.",
                False,
                "Validate the request against the advertised tool schema.",
            )
        self._invocations[tool] += 1
        return _new_id("req")

    def _upstream_error(self, request_id: str, error: UpstreamToolError) -> DomainError:
        return self._error(
            request_id,
            error.code,
            "The pinned simulation runtime did not complete the requested operation.",
            error.retryable,
            "Reload the immutable model and retry once."
            if error.retryable
            else "Start a fresh case after checking the pinned runtime.",
        )

    def _integrity_error(self, request_id: str) -> DomainError:
        return self._error(
            request_id,
            "EVIDENCE_INTEGRITY_FAILED",
            "Stored evidence failed an integrity check.",
            False,
            "Do not continue this case; preserve it for review and start a fresh case.",
        )

    @staticmethod
    def _error(
        request_id: str, code: str, message: str, retryable: bool, next_action: str
    ) -> DomainError:
        return DomainError(
            code=code,
            safe_message=message,
            retryable=retryable,
            request_id=request_id,
            next_action=next_action,
        )


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _segment_boundaries(value: RunExperimentInput) -> list[SegmentBoundary]:
    start = 0
    boundaries = []
    for index, segment in enumerate(value.segments):
        end = start + segment.n_steps
        boundaries.append(
            SegmentBoundary(segment_index=index, start_step=start, end_step=end)
        )
        start = end
    return boundaries


def _contract_clauses() -> list[ContractClause]:
    return [
        ContractClause(
            clause_id="reach_error",
            description=(
                f"Hold error p95 must not exceed {PASS_LIMITS['hold_error_p95_m']:g} m."
            ),
        ),
        ContractClause(
            clause_id="stable_hold",
            description=(
                "Joint-speed RMS must not exceed "
                f"{PASS_LIMITS['joint_speed_rms_rad_s']:g} rad/s."
            ),
        ),
        ContractClause(
            clause_id="settling",
            description=(
                f"Settling time must not exceed {PASS_LIMITS['settling_time_s']:.1f} s."
            ),
        ),
        ContractClause(
            clause_id="finite_state",
            description="Every observed numeric state must remain finite.",
        ),
        ContractClause(
            clause_id="joint_limits",
            description="No hinge joint may leave its advertised range.",
        ),
    ]


def _patch_policy() -> PatchPolicy:
    return PatchPolicy(
        editable_attributes=("axis", "damping", "armature", "frictionloss"),
        axis_unit_vector=True,
        damping=Range(minimum=0.0, maximum=100.0),
        armature=Range(minimum=0.0, maximum=10.0),
        frictionloss=Range(minimum=0.0, maximum=100.0),
    )


__all__ = ["AssetAutopsyService", "DomainError"]
