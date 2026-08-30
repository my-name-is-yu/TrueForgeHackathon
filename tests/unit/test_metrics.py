from __future__ import annotations

import math

from asset_autopsy.fixture import load_compound_arm_fixture
from asset_autopsy.metrics import (
    evaluate_task,
    resample_experiment_trace,
)
from asset_autopsy.runner import RunRecord, SegmentRecord
from asset_autopsy.schemas import (
    BodyPositionObservable,
    ContactCountObservable,
    QposObservable,
    RunTaskOutput,
    validate_experiment_trace_contract,
)
from asset_autopsy.task_evaluation import TASK_METRIC_ORDER


def make_record(*, error: float, speed: float, steps: int = 2_000) -> RunRecord:
    fixture = load_compound_arm_fixture()
    target = fixture.public_scenario.target_body_position
    rows = tuple(
        {
            "t": 0.002 * (index + 1),
            "E_pot": 0.0,
            "E_kin": speed * speed,
            "qpos": (0.0, 0.0, 0.0),
            "qvel": (speed, speed, speed),
            "ncon": 0,
            "body_xpos:end_effector": (target[0] + error, target[1], target[2]),
            "ctrl": (0.1, 0.2, 0.3),
        }
        for index in range(steps)
    )
    return RunRecord(
        step_count=steps,
        segments=(SegmentRecord("one", steps, (0.1, 0.2, 0.3), rows),),
    )


def test_task_evaluation_derives_every_fixed_contract_view() -> None:
    fixture = load_compound_arm_fixture()
    before = evaluate_task(make_record(error=0.1, speed=0.1), fixture.public_scenario)
    after = evaluate_task(make_record(error=0.0, speed=0.0), fixture.public_scenario)
    diff = after.behavior_diff_from(before)
    output = RunTaskOutput.from_evaluation(
        request_id="req_metrics",
        case_id="case_metrics",
        event_ids=[],
        warnings=[],
        artifacts=[],
        revision_id="r001",
        evaluation=after,
        parent_evaluation=before,
    )

    assert before.passed is False
    assert after.passed is True
    assert after.result == output.result == "pass"
    assert tuple(after.values) == TASK_METRIC_ORDER
    assert {item.metric: item.value for item in output.observations} == after.values
    assert {item.metric: item.after for item in diff.metric_deltas} == after.values
    before_clauses = {clause.clause_id: clause.passed for clause in before.clauses}
    after_clauses = {clause.clause_id: clause.passed for clause in after.clauses}
    expected_outcomes = {
        clause_id: (
            "improved"
            if not before_clauses[clause_id] and passed
            else "regressed"
            if before_clauses[clause_id] and not passed
            else "unchanged"
        )
        for clause_id, passed in after_clauses.items()
    }
    assert {item.clause_id: item.outcome for item in diff.clause_outcomes} == (
        expected_outcomes
    )
    assert output.behavior_diff == diff
    assert after.values["hold_error_p95_m"] == 0.0
    assert after.values["joint_speed_rms_rad_s"] == 0.0
    assert diff.verdict == "public_pass"
    assert diff.changed is True
    assert diff.first_divergence.signal == "end_effector_position"
    assert len(diff.metric_deltas) == 7
    assert len(diff.clause_outcomes) == 5


def test_experiment_resampling_is_uniform_and_uses_zoh_for_control_and_contacts() -> (
    None
):
    first = make_record(error=0.0, speed=0.0, steps=128).segments[0]
    second_rows = tuple(
        {
            **dict(row),
            "t": 0.002 * (128 + index + 1),
            "ncon": 2,
            "ctrl": (-0.1, -0.2, -0.3),
        }
        for index, row in enumerate(first.timeseries)
    )
    record = RunRecord(
        step_count=256,
        segments=(
            first,
            SegmentRecord("two", 128, (-0.1, -0.2, -0.3), second_rows),
        ),
    )
    trace = resample_experiment_trace(
        record,
        observables=(
            QposObservable(kind="qpos"),
            ContactCountObservable(kind="contact_count"),
            BodyPositionObservable(kind="body_position", body_name="end_effector"),
        ),
        joint_names=("joint_a", "joint_b", "joint_c"),
        actuator_names=("motor_a", "motor_b", "motor_c"),
    )

    assert len(trace.rows) == 256
    intervals = [
        right.time_s - left.time_s for left, right in zip(trace.rows, trace.rows[1:])
    ]
    assert all(
        math.isclose(item, intervals[0], rel_tol=1e-9, abs_tol=1e-12)
        for item in intervals
    )
    kinds = [column.kind for column in trace.columns]
    assert kinds[0] == "time"
    assert kinds.count("control") == 3
    assert trace.rows[0].values["contact_count"] == 0.0
    assert trace.rows[-1].values["contact_count"] == 2.0
    assert trace.rows[0].values["control:motor_a"] == 0.1
    assert trace.rows[127].values["control:motor_a"] == 0.1
    assert trace.rows[128].values["control:motor_a"] == -0.1
    assert trace.rows[-1].values["control:motor_a"] == -0.1
    assert (
        validate_experiment_trace_contract(
            trace,
            observables=(
                QposObservable(kind="qpos"),
                ContactCountObservable(kind="contact_count"),
                BodyPositionObservable(
                    kind="body_position", body_name="end_effector"
                ),
            ),
            joint_names=("joint_a", "joint_b", "joint_c"),
            actuator_names=("motor_a", "motor_b", "motor_c"),
        )
        == trace
    )
