from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from asset_autopsy.fixture import CASE_ID, clean_end_effector_position
from asset_autopsy.mujoco_client import (
    SAFE_NEXT_ACTION,
    UPSTREAM_TIMEOUT,
    UpstreamToolError,
)
from asset_autopsy.patcher import apply_one_attribute_patch
from asset_autopsy.runner import PartialRunError, RunRecord, SegmentRecord
from asset_autopsy.schemas import (
    ActuatorControl,
    AxisPatch,
    BodyPositionObservable,
    CompetingExplanation,
    ConstantControlSegment,
    CreateRevisionInput,
    ElementReference,
    ExpectedEffect,
    Hypothesis,
    InspectAssetInput,
    JointPosition,
    OpenCaseInput,
    PatchTarget,
    Predicate,
    PublishRevisionInput,
    QposObservable,
    QvelObservable,
    RunExperimentInput,
    RunTaskInput,
    ScalarPatch,
    VerifyRevisionInput,
)
from asset_autopsy.service import AssetAutopsyService, DomainError
from asset_autopsy.storage import EvidenceStore, canonical_json_bytes


class DeterministicFakeRunner:
    def __init__(self, *, require_damping: bool = True) -> None:
        self.validation_result = True
        self.validation_error: Exception | None = None
        self.validated_xml: list[str] = []
        self.require_damping = require_damping

    async def validate(self, xml_string: str) -> bool:
        self.validated_xml.append(xml_string)
        if self.validation_error is not None:
            raise self.validation_error
        return self.validation_result

    async def run(self, configuration):
        root = ET.fromstring(configuration.xml_string)
        joints = {joint.attrib["name"]: joint for joint in root.findall(".//joint")}
        axis_repaired = tuple(
            float(item) for item in joints["joint_b"].attrib["axis"].split()
        ) == (0.0, 1.0, 0.0)
        damping_repaired = float(joints["joint_c"].attrib["damping"]) >= 0.4
        rows_by_segment = []
        elapsed = 0
        for segment in configuration.segments:
            rows = []
            for _ in range(segment.n_steps):
                elapsed += 1
                target = tuple(float(item) for item in segment.ctrl)
                if segment.label in {"public_center", "qualification"}:
                    if not axis_repaired:
                        error, speed, q_offset = 0.1, 0.1, 0.1
                    elif self.require_damping and not damping_repaired:
                        error, speed, q_offset = 0.001, 0.08, 0.02
                    else:
                        error, speed, q_offset = 0.0, 0.0, 0.0
                    qpos = (target[0], target[1], target[2] + q_offset)
                    body = clean_end_effector_position(target)
                    body = (body[0] + error, body[1], body[2])
                    qvel = (speed, speed, speed)
                else:
                    qpos = target
                    qvel = (0.0, 0.0, 0.0)
                    body = clean_end_effector_position(qpos)
                row = {
                    "t": 0.002 * elapsed,
                    "E_pot": 0.0,
                    "E_kin": sum(item * item for item in qvel),
                    "qpos": qpos,
                    "qvel": qvel,
                    "ctrl": target,
                }
                if "contact_count" in configuration.track:
                    row["ncon"] = 0
                for selected in configuration.track:
                    if selected.startswith("body_xpos:"):
                        row[selected] = body
                rows.append(row)
            rows_by_segment.append(
                SegmentRecord(segment.label, segment.n_steps, segment.ctrl, tuple(rows))
            )
        return RunRecord(
            step_count=elapsed,
            segments=tuple(rows_by_segment),
            image_png=None,
            render_fallback=configuration.render,
        )


class NonfiniteExperimentRunner(DeterministicFakeRunner):
    async def run(self, configuration):
        record = await super().run(configuration)
        if configuration.segments[0].label != "discriminate":
            return record
        segment = record.segments[0]
        rows = [dict(row) for row in segment.timeseries]
        rows[17]["qvel"] = (0.0, float("inf"), 0.0)
        return RunRecord(
            step_count=record.step_count,
            segments=(
                SegmentRecord(
                    segment.label, segment.step_count, segment.ctrl, tuple(rows)
                ),
            ),
        )


class UpstreamFailingExperimentRunner(DeterministicFakeRunner):
    def __init__(self, completed_segments: int) -> None:
        super().__init__()
        self.completed_segments = completed_segments

    async def run(self, configuration):
        record = await super().run(configuration)
        if configuration.segments[0].label == "public_center":
            return record
        error = UpstreamToolError(
            UPSTREAM_TIMEOUT,
            "private upstream failure detail",
            True,
            SAFE_NEXT_ACTION,
        )
        if self.completed_segments == 0:
            raise error
        completed = record.segments[: self.completed_segments]
        raise PartialRunError(
            error,
            RunRecord(
                step_count=sum(segment.step_count for segment in completed),
                segments=completed,
            ),
        )


def hypothesis(
    primary: str, attribute: str, competing: str, competing_attribute: str
) -> Hypothesis:
    return Hypothesis(
        claim=f"The response is controlled by {primary} {attribute}.",
        suspected_elements=[
            ElementReference(kind="joint", name=primary, attributes=[attribute])
        ],
        competing_explanation=CompetingExplanation(
            claim=f"The response instead comes from {competing} {competing_attribute}.",
            suspected_elements=[
                ElementReference(
                    kind="joint", name=competing, attributes=[competing_attribute]
                )
            ],
            discriminating_reason="Position direction and velocity decay provide different evidence.",
        ),
        prediction="The selected signals will change in the predicted direction.",
        falsifier="The predicted signal separation will be absent.",
    )


def experiment(
    revision_id: str, claim: Hypothesis, *, motor_b: float = 0.2
) -> RunExperimentInput:
    return RunExperimentInput(
        case_id=CASE_ID,
        revision_id=revision_id,
        hypothesis=claim,
        initial_joint_positions=[
            JointPosition(joint_name="joint_a", position_rad=0.0),
            JointPosition(joint_name="joint_b", position_rad=0.0),
            JointPosition(joint_name="joint_c", position_rad=0.0),
        ],
        segments=[
            ConstantControlSegment(
                label="discriminate",
                n_steps=256,
                controls=[
                    ActuatorControl(actuator_name="motor_a", value=0.0),
                    ActuatorControl(actuator_name="motor_b", value=motor_b),
                    ActuatorControl(actuator_name="motor_c", value=0.0),
                ],
            )
        ],
        observables=[
            QposObservable(kind="qpos"),
            QvelObservable(kind="qvel"),
            BodyPositionObservable(kind="body_position", body_name="end_effector"),
        ],
    )


def multi_segment_experiment(revision_id: str, claim: Hypothesis) -> RunExperimentInput:
    value = experiment(revision_id, claim)
    segment = value.segments[0]
    return value.model_copy(
        update={
            "segments": [
                segment.model_copy(update={"label": "first", "n_steps": 128}),
                segment.model_copy(update={"label": "second", "n_steps": 128}),
            ],
            "capture_final_snapshot": True,
        }
    )


def test_service_scalar_preflight_counts_the_segment_control_surface(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AssetAutopsyService(tmp_path, runner=DeterministicFakeRunner())
    value = experiment(
        "r000",
        hypothesis("joint_b", "axis", "joint_c", "damping"),
    )
    monkeypatch.setattr("asset_autopsy.service.MAX_TRACE_SCALARS", 3_840)

    with pytest.raises(DomainError) as caught:
        service._validate_experiment(value, "req_scalar_budget")

    assert caught.value.code == "EXPERIMENT_SCALAR_BUDGET_EXCEEDED"
    assert caught.value.safe_message == (
        "The requested experiment exceeds the numeric record budget."
    )


def run(coroutine):
    return asyncio.run(coroutine)


def files(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def prepare_axis_revision(service: AssetAutopsyService) -> CreateRevisionInput:
    opened = run(service.open_case(OpenCaseInput(case_id=CASE_ID)))
    run(
        service.run_task(
            RunTaskInput(
                case_id=CASE_ID,
                revision_id="r000",
                scenario_id="public_center",
                capture="metrics",
            )
        )
    )
    basis = run(
        service.run_experiment(
            experiment(
                "r000",
                hypothesis("joint_b", "axis", "joint_c", "damping"),
            )
        )
    )
    return CreateRevisionInput(
        case_id=CASE_ID,
        base_revision_id="r000",
        expected_base_sha256=opened.original_asset_sha256,
        basis_hypothesis_id=basis.hypothesis_id,
        basis_experiment_run_id=basis.run_id,
        patch=AxisPatch(
            target=PatchTarget(kind="joint", name="joint_b"),
            attribute="axis",
            expected_old_value=(0.0, 0.0, 1.0),
            new_value=(0.0, 1.0, 0.0),
        ),
        rationale="The registered direction experiment separates the axis explanation.",
        expected_effect=ExpectedEffect(
            scenario_id="public_center",
            predicates=[Predicate(metric="hold_error_p95_m", op="lt", value=0.03)],
        ),
    )


def create_evidence_backed_revision_chain(
    service: AssetAutopsyService, revision_count: int
):
    r001 = run(service.create_revision(prepare_axis_revision(service)))
    if revision_count == 1:
        return r001

    run(
        service.run_task(
            RunTaskInput(
                case_id=CASE_ID,
                revision_id="r001",
                scenario_id="public_center",
                capture="metrics",
            )
        )
    )
    damping_run = run(
        service.run_experiment(
            experiment(
                "r001",
                hypothesis("joint_c", "damping", "joint_b", "axis"),
                motor_b=-0.2,
            )
        )
    )
    return run(
        service.create_revision(
            CreateRevisionInput(
                case_id=CASE_ID,
                base_revision_id="r001",
                expected_base_sha256=r001.asset_sha256,
                basis_hypothesis_id=damping_run.hypothesis_id,
                basis_experiment_run_id=damping_run.run_id,
                patch=ScalarPatch(
                    target=PatchTarget(kind="joint", name="joint_c"),
                    attribute="damping",
                    expected_old_value=0.01,
                    new_value=0.4,
                ),
                rationale="The registered decay experiment isolates insufficient damping.",
                expected_effect=ExpectedEffect(
                    scenario_id="public_center",
                    predicates=[
                        Predicate(metric="joint_speed_rms_rad_s", op="lt", value=0.05)
                    ],
                ),
            )
        )
    )


def service_state(service: AssetAutopsyService) -> dict[str, object]:
    opened = run(service.open_case(OpenCaseInput(case_id=CASE_ID)))
    case = service.store.get_case(CASE_ID)
    return {
        "head": case.head_revision_id,
        "revisions": service.store.list_revisions(CASE_ID),
        "ledger": service.store.ledger_events(CASE_ID),
        "budgets": opened.remaining_budgets.model_dump(),
        "objects": files(service.store.objects.root),
    }


def expected_patched_xml(
    service: AssetAutopsyService, request: CreateRevisionInput
) -> str:
    return apply_one_attribute_patch(
        base_xml=service.store.objects.read_bytes(request.expected_base_sha256),
        expected_base_sha256=request.expected_base_sha256,
        patch=request.patch,
    ).xml.decode("utf-8")


def test_compile_rejection_preserves_all_revision_state_and_validates_exact_bytes(
    tmp_path,
) -> None:
    runner = DeterministicFakeRunner()
    runner.validation_result = False
    service = AssetAutopsyService(tmp_path, runner=runner)
    request = prepare_axis_revision(service)
    expected_xml = expected_patched_xml(service, request)
    before = service_state(service)

    with pytest.raises(DomainError) as caught:
        run(service.create_revision(request))

    assert caught.value.code == "PATCHED_ASSET_INVALID"
    assert caught.value.safe_message == (
        "The patched asset was rejected by the pinned simulation runtime."
    )
    assert caught.value.retryable is False
    assert runner.validated_xml == [expected_xml]
    assert service_state(service) == before


def test_valid_compile_creates_one_revision_and_committed_retry_skips_validation(
    tmp_path,
) -> None:
    runner = DeterministicFakeRunner()
    service = AssetAutopsyService(tmp_path, runner=runner)
    request = prepare_axis_revision(service)
    expected_xml = expected_patched_xml(service, request)

    created = run(service.create_revision(request))

    assert created.status == "created"
    assert created.revision_id == "r001"
    assert runner.validated_xml == [expected_xml]
    assert [
        revision.revision_id for revision in service.store.list_revisions(CASE_ID)
    ] == [
        "r000",
        "r001",
    ]
    assert (
        sum(
            event.event_type == "REVISION_CREATED"
            for event in service.store.ledger_events(CASE_ID)
        )
        == 1
    )

    runner.validation_error = AssertionError("committed retry revalidated upstream")
    retried = run(service.create_revision(request))

    assert retried.status == "already_exists"
    assert retried.revision_id == "r001"
    assert runner.validated_xml == [expected_xml]


def test_transient_compile_failure_preserves_primary_upstream_failure_and_state(
    tmp_path,
) -> None:
    runner = DeterministicFakeRunner()
    runner.validation_error = UpstreamToolError(
        UPSTREAM_TIMEOUT,
        "private compiler timeout at /tmp/secret.xml",
        True,
        SAFE_NEXT_ACTION,
    )
    service = AssetAutopsyService(tmp_path, runner=runner)
    request = prepare_axis_revision(service)
    expected_xml = expected_patched_xml(service, request)
    before = service_state(service)

    with pytest.raises(DomainError) as caught:
        run(service.create_revision(request))

    assert caught.value.code == UPSTREAM_TIMEOUT
    assert caught.value.retryable is True
    assert "private" not in caught.value.safe_message
    assert "secret" not in caught.value.safe_message
    assert runner.validated_xml == [expected_xml]
    assert service_state(service) == before


def test_persisted_task_tampering_is_rejected_after_ledger_rehash(tmp_path) -> None:
    service = AssetAutopsyService(tmp_path, runner=DeterministicFakeRunner())
    public = run(
        service.run_task(
            RunTaskInput(
                case_id=CASE_ID,
                revision_id="r000",
                scenario_id="public_center",
                capture="metrics",
            )
        )
    )
    assert public.result == "fail"
    event = next(
        event
        for event in service.store.ledger_events(CASE_ID)
        if event.event_type == "TASK_COMPLETED"
    )
    tampered = json.loads(json.dumps(event.payload))
    tampered["evaluation"]["passed"] = True
    with sqlite3.connect(tmp_path / "evidence.sqlite") as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            "UPDATE ledger_events SET payload_json = ? WHERE event_id = ?",
            (canonical_json_bytes(tampered).decode(), event.event_id),
        )
        row = connection.execute(
            "SELECT * FROM ledger_events WHERE event_id = ?", (event.event_id,)
        ).fetchone()
        assert row is not None
        event_hash = hashlib.sha256(
            bytes.fromhex(row["prev_hash"])
            + canonical_json_bytes(EvidenceStore._event_without_hash(row))
        ).hexdigest()
        connection.execute(
            "UPDATE ledger_events SET event_hash = ? WHERE event_id = ?",
            (event_hash, event.event_id),
        )
        connection.commit()

    with pytest.raises(DomainError) as caught:
        service._latest_task_evaluation(CASE_ID, "r000", "req_read_tamper")

    assert caught.value.code == "EVIDENCE_INTEGRITY_FAILED"
    assert caught.value.retryable is False


@pytest.mark.parametrize("revision_count", [1, 2])
def test_current_evidence_backed_public_passing_head_qualifies_within_budget(
    tmp_path, revision_count: int
) -> None:
    service = AssetAutopsyService(
        tmp_path,
        runner=DeterministicFakeRunner(require_damping=revision_count == 2),
    )
    head = create_evidence_backed_revision_chain(service, revision_count)
    public = run(
        service.run_task(
            RunTaskInput(
                case_id=CASE_ID,
                revision_id=head.revision_id,
                scenario_id="public_center",
                capture="metrics",
            )
        )
    )
    assert public.result == "pass"
    assert public.behavior_diff.verdict == "public_pass"

    verified = run(
        service.verify_revision(
            VerifyRevisionInput(
                case_id=CASE_ID,
                revision_id=head.revision_id,
                expected_asset_sha256=head.asset_sha256,
            )
        )
    )

    assert verified.public_result.model_dump() == {
        "passed": 1,
        "total": 1,
        "violated_clause_ids": [],
    }
    assert verified.holdout_result.model_dump() == {
        "passed": 3,
        "total": 3,
        "violated_clause_ids": [],
    }
    assert verified.promotion_ticket is not None
    assert verified.promotion_ticket.revision_id == head.revision_id
    assert verified.promotion_ticket.asset_sha256 == head.asset_sha256
    assert len(verified.promotion_ticket.canonical_diff) == revision_count
    assert "scenario" not in verified.model_dump_json().lower()

    repeated = run(
        service.verify_revision(
            VerifyRevisionInput(
                case_id=CASE_ID,
                revision_id=head.revision_id,
                expected_asset_sha256=head.asset_sha256,
            )
        )
    )
    assert repeated.promotion_ticket == verified.promotion_ticket
    assert (
        sum(
            event.event_type == "QUALIFICATION_RESERVED"
            for event in service.store.ledger_events(CASE_ID)
        )
        == 1
    )


def test_qualification_rejects_the_root_without_consuming_hidden_budget(
    tmp_path,
) -> None:
    service = AssetAutopsyService(
        tmp_path, runner=DeterministicFakeRunner(require_damping=False)
    )
    opened = run(service.open_case(OpenCaseInput(case_id=CASE_ID)))

    with pytest.raises(DomainError) as caught:
        run(
            service.verify_revision(
                VerifyRevisionInput(
                    case_id=CASE_ID,
                    revision_id="r000",
                    expected_asset_sha256=opened.original_asset_sha256,
                )
            )
        )

    assert caught.value.code == "QUALIFICATION_NOT_READY"
    assert service.store.get_case(CASE_ID).qualification_state == "unused"
    assert not any(
        event.event_type == "QUALIFICATION_RESERVED"
        for event in service.store.ledger_events(CASE_ID)
    )


def test_qualification_rejects_a_public_failing_child_without_hidden_execution(
    tmp_path,
) -> None:
    service = AssetAutopsyService(tmp_path, runner=DeterministicFakeRunner())
    head = create_evidence_backed_revision_chain(service, 1)
    public = run(
        service.run_task(
            RunTaskInput(
                case_id=CASE_ID,
                revision_id=head.revision_id,
                scenario_id="public_center",
                capture="metrics",
            )
        )
    )
    assert public.result == "fail"

    with pytest.raises(DomainError) as caught:
        run(
            service.verify_revision(
                VerifyRevisionInput(
                    case_id=CASE_ID,
                    revision_id=head.revision_id,
                    expected_asset_sha256=head.asset_sha256,
                )
            )
        )

    assert caught.value.code == "PUBLIC_PASS_REQUIRED"
    assert service.store.get_case(CASE_ID).qualification_state == "unused"
    assert not any(
        event.event_type == "QUALIFICATION_RESERVED"
        for event in service.store.ledger_events(CASE_ID)
    )


def test_qualification_rejects_a_stale_non_head_revision(tmp_path) -> None:
    service = AssetAutopsyService(tmp_path, runner=DeterministicFakeRunner())
    create_evidence_backed_revision_chain(service, 2)
    stale = service.store.get_revision(CASE_ID, "r001")

    with pytest.raises(DomainError) as caught:
        run(
            service.verify_revision(
                VerifyRevisionInput(
                    case_id=CASE_ID,
                    revision_id=stale.revision_id,
                    expected_asset_sha256=stale.asset_sha256,
                )
            )
        )

    assert caught.value.code == "QUALIFICATION_NOT_READY"
    assert service.store.get_case(CASE_ID).qualification_state == "unused"
    assert not any(
        event.event_type == "QUALIFICATION_RESERVED"
        for event in service.store.ledger_events(CASE_ID)
    )


def test_third_child_revision_is_rejected_at_the_budget_boundary(tmp_path) -> None:
    runner = DeterministicFakeRunner()
    service = AssetAutopsyService(tmp_path, runner=runner)
    head = create_evidence_backed_revision_chain(service, 2)
    run(
        service.run_task(
            RunTaskInput(
                case_id=CASE_ID,
                revision_id=head.revision_id,
                scenario_id="public_center",
                capture="metrics",
            )
        )
    )
    basis = run(
        service.run_experiment(
            experiment(
                head.revision_id,
                hypothesis("joint_a", "armature", "joint_c", "damping"),
            )
        )
    )

    before = service_state(service)
    with pytest.raises(DomainError) as caught:
        run(
            service.create_revision(
                CreateRevisionInput(
                    case_id=CASE_ID,
                    base_revision_id=head.revision_id,
                    expected_base_sha256=head.asset_sha256,
                    basis_hypothesis_id=basis.hypothesis_id,
                    basis_experiment_run_id=basis.run_id,
                    patch=ScalarPatch(
                        target=PatchTarget(kind="joint", name="joint_a"),
                        attribute="armature",
                        expected_old_value=0.01,
                        new_value=0.02,
                    ),
                    rationale="The revision budget must reject a third child.",
                    expected_effect=ExpectedEffect(
                        scenario_id="public_center",
                        predicates=[
                            Predicate(metric="hold_error_p95_m", op="lt", value=0.03)
                        ],
                    ),
                )
            )
        )

    assert caught.value.code == "REVISION_BUDGET_EXHAUSTED"
    assert service_state(service) == before
    assert len(runner.validated_xml) == 2


def test_full_two_revision_service_flow_qualifies_and_publication_is_deferred_without_side_effects(
    tmp_path,
) -> None:
    service = AssetAutopsyService(tmp_path, runner=DeterministicFakeRunner())
    opened = run(service.open_case(OpenCaseInput(case_id=CASE_ID)))
    assert opened.original_revision_id == "r000"

    root_task = run(
        service.run_task(
            RunTaskInput(
                case_id=CASE_ID,
                revision_id="r000",
                scenario_id="public_center",
                capture="metrics",
            )
        )
    )
    assert root_task.result == "fail"
    assert root_task.behavior_diff is None
    assert root_task.trace == []
    assert any(artifact.kind == "trace_json" for artifact in root_task.artifacts)

    axis_run = run(
        service.run_experiment(
            experiment(
                "r000",
                hypothesis("joint_b", "axis", "joint_c", "damping"),
            )
        )
    )
    assert axis_run.outcome.kind == "completed"
    assert len(axis_run.trace.rows) == 256
    assert len(axis_run.model_dump_json()) >= 24_000
    assert len(axis_run.segment_boundaries) == 1
    replay = run(
        service.run_experiment(
            experiment(
                "r000",
                hypothesis("joint_b", "axis", "joint_a", "armature"),
            )
        )
    )
    assert replay.condition_sha256 == axis_run.condition_sha256
    assert replay.execution_fingerprint_sha256 == axis_run.execution_fingerprint_sha256
    assert replay.trace == axis_run.trace

    axis_revision_request = CreateRevisionInput(
        case_id=CASE_ID,
        base_revision_id="r000",
        expected_base_sha256=opened.original_asset_sha256,
        basis_hypothesis_id=axis_run.hypothesis_id,
        basis_experiment_run_id=axis_run.run_id,
        patch=AxisPatch(
            target=PatchTarget(kind="joint", name="joint_b"),
            attribute="axis",
            expected_old_value=(0.0, 0.0, 1.0),
            new_value=(0.0, 1.0, 0.0),
        ),
        rationale="The registered direction experiment separates the axis explanation.",
        expected_effect=ExpectedEffect(
            scenario_id="public_center",
            predicates=[Predicate(metric="hold_error_p95_m", op="lt", value=0.03)],
        ),
    )
    unrelated_request = axis_revision_request.model_copy(
        update={
            "patch": ScalarPatch(
                target=PatchTarget(kind="joint", name="joint_a"),
                attribute="armature",
                expected_old_value=0.01,
                new_value=0.02,
            )
        }
    )
    before_events = len(service.store.ledger_events(CASE_ID))
    with pytest.raises(DomainError) as exc_info:
        run(service.create_revision(unrelated_request))
    assert exc_info.value.code == "CAUSAL_PATCH_UNBOUND"
    assert len(service.store.ledger_events(CASE_ID)) == before_events
    assert len(service.store.list_revisions(CASE_ID)) == 1
    wrong_kind_run = run(
        service.run_experiment(
            experiment(
                "r000",
                hypothesis("joint_b", "axis", "joint_b", "axis").model_copy(
                    update={
                        "suspected_elements": [
                            ElementReference(
                                kind="body", name="end_effector", attributes=["axis"]
                            )
                        ],
                        "competing_explanation": CompetingExplanation(
                            claim="The response instead comes from body end_effector axis.",
                            suspected_elements=[
                                ElementReference(
                                    kind="body",
                                    name="end_effector",
                                    attributes=["axis"],
                                )
                            ],
                            discriminating_reason="The element kind distinguishes the explanations.",
                        ),
                    }
                ),
            )
        )
    )
    wrong_kind_request = axis_revision_request.model_copy(
        update={
            "basis_hypothesis_id": wrong_kind_run.hypothesis_id,
            "basis_experiment_run_id": wrong_kind_run.run_id,
            "patch": AxisPatch(
                target=PatchTarget(kind="joint", name="end_effector"),
                attribute="axis",
                expected_old_value=(0.0, 0.0, 1.0),
                new_value=(0.0, 1.0, 0.0),
            ),
        }
    )
    before_events = len(service.store.ledger_events(CASE_ID))
    with pytest.raises(DomainError) as exc_info:
        run(service.create_revision(wrong_kind_request))
    assert exc_info.value.code == "CAUSAL_PATCH_UNBOUND"
    assert len(service.store.ledger_events(CASE_ID)) == before_events
    assert len(service.store.list_revisions(CASE_ID)) == 1
    r001 = run(service.create_revision(axis_revision_request))
    assert r001.revision_id == "r001"
    assert (
        run(service.create_revision(axis_revision_request)).status == "already_exists"
    )
    task_1 = run(
        service.run_task(
            RunTaskInput(
                case_id=CASE_ID,
                revision_id="r001",
                scenario_id="public_center",
                capture="metrics",
            )
        )
    )
    assert task_1.result == "fail"
    assert task_1.behavior_diff.changed is True

    damping_run = run(
        service.run_experiment(
            experiment(
                "r001",
                hypothesis("joint_c", "damping", "joint_b", "axis"),
                motor_b=-0.2,
            )
        )
    )
    r002 = run(
        service.create_revision(
            CreateRevisionInput(
                case_id=CASE_ID,
                base_revision_id="r001",
                expected_base_sha256=r001.asset_sha256,
                basis_hypothesis_id=damping_run.hypothesis_id,
                basis_experiment_run_id=damping_run.run_id,
                patch=ScalarPatch(
                    target=PatchTarget(kind="joint", name="joint_c"),
                    attribute="damping",
                    expected_old_value=0.01,
                    new_value=0.4,
                ),
                rationale="The registered decay experiment isolates insufficient damping.",
                expected_effect=ExpectedEffect(
                    scenario_id="public_center",
                    predicates=[
                        Predicate(metric="joint_speed_rms_rad_s", op="lt", value=0.05)
                    ],
                ),
            )
        )
    )
    task_2 = run(
        service.run_task(
            RunTaskInput(
                case_id=CASE_ID,
                revision_id="r002",
                scenario_id="public_center",
                capture="metrics",
            )
        )
    )
    assert task_2.result == "pass"
    assert task_2.behavior_diff.verdict == "public_pass"

    verified = run(
        service.verify_revision(
            VerifyRevisionInput(
                case_id=CASE_ID,
                revision_id="r002",
                expected_asset_sha256=r002.asset_sha256,
            )
        )
    )
    assert verified.public_result.passed == 1
    assert verified.holdout_result.passed == 3
    assert len(verified.promotion_ticket.canonical_diff) == 2
    assert "-0.553127" not in verified.model_dump_json()
    ledger_text = json.dumps(
        [
            {
                "payload": dict(event.payload),
                "artifact_refs": [dict(item) for item in event.artifact_refs],
            }
            for event in service.store.ledger_events(CASE_ID)
        ],
        sort_keys=True,
    )
    qualification_bytes = service.store.objects.read_bytes(verified.artifacts[0].sha256)
    assert "-0.553127" not in ledger_text
    assert b"-0.553127" not in qualification_bytes
    assert service.publish_invocation_count == 0
    verified_retry = run(
        service.verify_revision(
            VerifyRevisionInput(
                case_id=CASE_ID,
                revision_id="r002",
                expected_asset_sha256=r002.asset_sha256,
            )
        )
    )
    assert verified_retry.promotion_ticket == verified.promotion_ticket
    assert (
        len(
            [
                event
                for event in service.store.ledger_events(CASE_ID)
                if event.event_type == "QUALIFICATION_RESERVED"
            ]
        )
        == 1
    )

    before_state = service_state(service)
    before_files = files(Path(tmp_path))
    with pytest.raises(DomainError) as deferred:
        run(
            service.publish_revision(
                PublishRevisionInput(
                    case_id=CASE_ID,
                    promotion_ticket=verified.promotion_ticket,
                )
            )
        )
    assert deferred.value.code == "PUBLICATION_DEFERRED"
    assert deferred.value.safe_message == (
        "Post-approval publication materialization is deferred for SC1."
    )
    assert deferred.value.retryable is False
    assert deferred.value.next_action == (
        "Do not retry publication; treat the approval request as the SC1 endpoint."
    )
    assert service.publish_invocation_count == 1
    assert files(Path(tmp_path)) == before_files
    assert service_state(service) == before_state
    assert service.store.verify_ledger()
    for event in service.store.ledger_events(CASE_ID):
        for artifact in event.artifact_refs:
            data = service.store.objects.read_bytes(artifact["sha256"])
            assert len(data) == artifact["size"]
    inspected = run(
        service.inspect_asset(
            InspectAssetInput(case_id=CASE_ID, revision_id="r002", view="both")
        )
    )
    assert inspected.joints[1].axis == (0.0, 1.0, 0.0)
    assert inspected.joints[2].damping == 0.4


def test_nonfinite_experiment_is_a_sanitized_budget_consuming_domain_outcome(
    tmp_path,
) -> None:
    service = AssetAutopsyService(tmp_path, runner=NonfiniteExperimentRunner())
    run(
        service.run_task(
            RunTaskInput(
                case_id=CASE_ID,
                revision_id="r000",
                scenario_id="public_center",
                capture="metrics",
            )
        )
    )

    outcome = run(
        service.run_experiment(
            experiment(
                "r000",
                hypothesis("joint_b", "axis", "joint_c", "damping"),
            )
        )
    )

    assert outcome.outcome.kind == "non_finite_state"
    assert outcome.outcome.budget_consumed is True
    assert outcome.outcome.first_bad_step == 17
    assert outcome.completed_steps == 17
    assert outcome.trace is None
    assert outcome.trace_sha256 is None
    assert outcome.final_snapshot is None
    assert "inf" not in outcome.model_dump_json().lower()
    opened = run(service.open_case(OpenCaseInput(case_id=CASE_ID)))
    assert opened.remaining_budgets.runs_remaining == 8
    assert opened.remaining_budgets.experiments_remaining == 4


@pytest.mark.parametrize(
    "violation",
    ["swapped_controls", "missing_observable", "extra_field", "duplicate_field"],
)
def test_service_rejects_trace_fields_unbound_from_the_accepted_experiment(
    tmp_path, monkeypatch: pytest.MonkeyPatch, violation: str
) -> None:
    from asset_autopsy.metrics import resample_experiment_trace as real_resample

    def unbound_trace(*args, **kwargs):
        trace = real_resample(*args, **kwargs)
        payload = trace.model_dump(mode="python")
        columns = payload["columns"]
        rows = payload["rows"]
        if violation == "swapped_controls":
            columns[-2], columns[-1] = columns[-1], columns[-2]
        elif violation == "missing_observable":
            columns[:] = [column for column in columns if column.get("kind") != "qvel"]
            for row in rows:
                row["values"] = {
                    key: item
                    for key, item in row["values"].items()
                    if not key.startswith("qvel:")
                }
        elif violation == "extra_field":
            columns.insert(-3, {"kind": "energy", "component": "potential"})
            for row in rows:
                row["values"]["energy:potential"] = 0.0
        else:
            duplicate_columns = list(trace.columns)
            duplicate_columns.insert(1, duplicate_columns[1])
            return trace.model_copy(update={"columns": duplicate_columns})
        return type(trace).model_validate(payload)

    monkeypatch.setattr(
        "asset_autopsy.service.resample_experiment_trace", unbound_trace
    )
    service = AssetAutopsyService(tmp_path, runner=DeterministicFakeRunner())
    run(
        service.run_task(
            RunTaskInput(
                case_id=CASE_ID,
                revision_id="r000",
                scenario_id="public_center",
                capture="metrics",
            )
        )
    )

    with pytest.raises(DomainError) as caught:
        run(
            service.run_experiment(
                experiment(
                    "r000",
                    hypothesis("joint_b", "axis", "joint_c", "damping"),
                )
            )
        )

    assert caught.value.code == "SIMULATION_RESULT_INVALID"
    events = service.store.ledger_events(CASE_ID)
    assert not any(
        event.event_type in {"EXPERIMENT_COMPLETED", "EXPERIMENT_FAILED"}
        for event in events
    )
    assert not any(
        reference["kind"] == "trace_json"
        for event in events
        for reference in event.artifact_refs
    )
    opened = run(service.open_case(OpenCaseInput(case_id=CASE_ID)))
    assert opened.remaining_budgets.runs_remaining == 9
    assert opened.remaining_budgets.experiments_remaining == 5


def test_upstream_failure_before_first_completed_segment_consumes_no_budget(
    tmp_path,
) -> None:
    service = AssetAutopsyService(
        tmp_path, runner=UpstreamFailingExperimentRunner(completed_segments=0)
    )
    run(
        service.run_task(
            RunTaskInput(
                case_id=CASE_ID,
                revision_id="r000",
                scenario_id="public_center",
                capture="metrics",
            )
        )
    )

    with pytest.raises(DomainError) as caught:
        run(
            service.run_experiment(
                multi_segment_experiment(
                    "r000", hypothesis("joint_b", "axis", "joint_c", "damping")
                )
            )
        )

    assert caught.value.code == UPSTREAM_TIMEOUT
    assert "private upstream" not in caught.value.safe_message
    events = service.store.ledger_events(CASE_ID)
    assert sum(event.event_type == "HYPOTHESIS_RECORDED" for event in events) == 1
    assert not any(event.event_type == "EXPERIMENT_FAILED" for event in events)
    opened = run(service.open_case(OpenCaseInput(case_id=CASE_ID)))
    assert opened.remaining_budgets.runs_remaining == 9
    assert opened.remaining_budgets.experiments_remaining == 5


def test_partial_experiment_failure_persists_bounded_evidence_and_budget_after_restart(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject_complete_trace(*args, **kwargs):
        raise AssertionError("partial failures must not validate a complete trace")

    monkeypatch.setattr(
        "asset_autopsy.service.validate_experiment_trace_contract",
        reject_complete_trace,
    )
    service = AssetAutopsyService(
        tmp_path, runner=UpstreamFailingExperimentRunner(completed_segments=1)
    )
    run(
        service.run_task(
            RunTaskInput(
                case_id=CASE_ID,
                revision_id="r000",
                scenario_id="public_center",
                capture="metrics",
            )
        )
    )

    with pytest.raises(DomainError) as caught:
        run(
            service.run_experiment(
                multi_segment_experiment(
                    "r000", hypothesis("joint_b", "axis", "joint_c", "damping")
                )
            )
        )

    assert caught.value.code == UPSTREAM_TIMEOUT
    assert caught.value.retryable is True
    assert "private upstream" not in caught.value.safe_message
    failed_events = [
        event
        for event in service.store.ledger_events(CASE_ID)
        if event.event_type == "EXPERIMENT_FAILED"
    ]
    assert len(failed_events) == 1
    failed = failed_events[0]
    assert failed.payload["outcome"] == {
        "kind": "upstream_failure",
        "budget_consumed": True,
    }
    assert failed.payload["failure_code"] == UPSTREAM_TIMEOUT
    assert failed.payload["requested_steps"] == 256
    assert failed.payload["completed_steps"] == 128
    assert failed.payload["completed_segment_boundaries"] == [
        {"segment_index": 0, "start_step": 0, "end_step": 128}
    ]
    stored_run = service.store.get_run(failed.payload["run_id"])
    assert stored_run.passed is False
    assert stored_run.trace_sha256 is None
    assert stored_run.metrics_sha256 is None
    assert {reference["kind"] for reference in failed.artifact_refs} == {
        "experiment_spec",
        "partial_experiment",
    }
    partial_ref = next(
        reference
        for reference in failed.artifact_refs
        if reference["kind"] == "partial_experiment"
    )
    partial = json.loads(service.store.objects.read_bytes(partial_ref["sha256"]))
    assert set(partial) == {
        "run_id",
        "requested_steps",
        "completed_steps",
        "segment_boundaries",
        "segments",
    }
    assert partial["requested_steps"] == 256
    assert partial["completed_steps"] == 128
    assert partial["segment_boundaries"] == [
        {"segment_index": 0, "start_step": 0, "end_step": 128}
    ]
    assert len(partial["segments"]) == 1
    assert partial["segments"][0]["label"] == "first"
    assert len(partial["segments"][0]["timeseries"]) == 128

    opened = run(service.open_case(OpenCaseInput(case_id=CASE_ID)))
    with pytest.raises(DomainError) as invalid_basis:
        run(
            service.create_revision(
                CreateRevisionInput(
                    case_id=CASE_ID,
                    base_revision_id="r000",
                    expected_base_sha256=opened.original_asset_sha256,
                    basis_hypothesis_id=failed.payload["hypothesis_id"],
                    basis_experiment_run_id=failed.payload["run_id"],
                    patch=AxisPatch(
                        target=PatchTarget(kind="joint", name="joint_b"),
                        attribute="axis",
                        expected_old_value=(0.0, 0.0, 1.0),
                        new_value=(0.0, 1.0, 0.0),
                    ),
                    rationale="The partial experiment must not authorize a repair.",
                    expected_effect=ExpectedEffect(
                        scenario_id="public_center",
                        predicates=[
                            Predicate(metric="hold_error_p95_m", op="lt", value=0.03)
                        ],
                    ),
                )
            )
        )
    assert invalid_basis.value.code == "CAUSAL_EXPERIMENT_INVALID"

    service.store.close()
    restarted = AssetAutopsyService(tmp_path, runner=DeterministicFakeRunner())
    reopened = run(restarted.open_case(OpenCaseInput(case_id=CASE_ID)))
    assert reopened.remaining_budgets.runs_remaining == 8
    assert reopened.remaining_budgets.experiments_remaining == 4
    assert restarted.store.get_run(failed.payload["run_id"]) == stored_run
    assert (
        len(
            [
                event
                for event in restarted.store.verify_ledger()
                if event.event_type == "EXPERIMENT_FAILED"
            ]
        )
        == 1
    )


def test_service_rejects_incomplete_named_topology_before_physics(tmp_path) -> None:
    runner = DeterministicFakeRunner()
    service = AssetAutopsyService(tmp_path, runner=runner)
    run(
        service.run_task(
            RunTaskInput(
                case_id=CASE_ID,
                revision_id="r000",
                scenario_id="public_center",
                capture="metrics",
            )
        )
    )
    invalid = experiment(
        "r000", hypothesis("joint_b", "axis", "joint_c", "damping")
    ).model_copy(
        update={
            "initial_joint_positions": [
                JointPosition(joint_name="joint_a", position_rad=0.0),
                JointPosition(joint_name="joint_b", position_rad=0.0),
            ]
        }
    )

    with pytest.raises(DomainError) as caught:
        run(service.run_experiment(invalid))

    assert caught.value.code == "EXPERIMENT_JOINT_SET_INVALID"
