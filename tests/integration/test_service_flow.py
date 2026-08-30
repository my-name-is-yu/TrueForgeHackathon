from __future__ import annotations

import asyncio
import json
import xml.etree.ElementTree as ET

import pytest

from asset_autopsy.fixture import CASE_ID, clean_end_effector_position
from asset_autopsy.mujoco_client import (
    SAFE_NEXT_ACTION,
    UPSTREAM_TIMEOUT,
    UpstreamToolError,
)
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


class DeterministicFakeRunner:
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
                    elif not damping_repaired:
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


def run(coroutine):
    return asyncio.run(coroutine)


def test_full_two_revision_service_flow_qualifies_and_direct_publication_emits_one_bundle(
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
                                    kind="body", name="end_effector", attributes=["axis"]
                                )
                            ],
                            discriminating_reason="The element kind distinguishes the explanations.",
                        )
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
    assert service.publication_receipt_count == 0
    assert service.public_artifact_count == 0
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

    published = run(
        service.publish_revision(
            PublishRevisionInput(
                case_id=CASE_ID,
                promotion_ticket=verified.promotion_ticket,
            )
        )
    )
    assert published.status == "published"
    assert len(published.artifacts) == 4
    assert service.publish_invocation_count == 1
    assert service.publication_receipt_count == 1
    assert service.published_bundle_count == 1
    assert service.public_artifact_count == 4
    assert service.store.get_case(CASE_ID).promoted_revision_id == "r002"
    assert service.store.verify_ledger()
    for event in service.store.ledger_events(CASE_ID):
        for artifact in event.artifact_refs:
            data = service.store.objects.read_bytes(artifact["sha256"])
            assert len(data) == artifact["size"]
    repeated = run(
        service.publish_revision(
            PublishRevisionInput(
                case_id=CASE_ID,
                promotion_ticket=verified.promotion_ticket,
            )
        )
    )
    assert repeated.status == "already_published"
    assert service.publication_receipt_count == 1
    assert service.published_bundle_count == 1
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
    tmp_path,
) -> None:
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
                            Predicate(
                                metric="hold_error_p95_m", op="lt", value=0.03
                            )
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
    assert len(
        [
            event
            for event in restarted.store.verify_ledger()
            if event.event_type == "EXPERIMENT_FAILED"
        ]
    ) == 1


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
