from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .fixture import PublicScenario
from .runner import RunRecord
from .schemas import (
    ActuatorControlTraceColumn,
    BehaviorDiff,
    BodyPositionObservable,
    BodyPositionTraceColumn,
    ClauseResult,
    ContactCountObservable,
    ContactCountTraceColumn,
    EnergyObservable,
    EnergyTraceColumn,
    ExperimentObservable,
    ExperimentTrace,
    FirstDivergence,
    JointTraceColumn,
    MetricDelta,
    MetricObservation,
    QposObservable,
    QvelObservable,
    TimeTraceColumn,
    TracePoint,
    experiment_trace_value_key,
)


TASK_METRIC_ORDER = (
    "final_target_error_m",
    "hold_error_p95_m",
    "joint_speed_rms_rad_s",
    "settling_time_s",
    "peak_energy_j",
    "joint_limit_violation_count",
    "non_finite_count",
)
CLAUSE_METRIC = {
    "reach_error": "hold_error_p95_m",
    "stable_hold": "joint_speed_rms_rad_s",
    "settling": "settling_time_s",
    "finite_state": "non_finite_count",
    "joint_limits": "joint_limit_violation_count",
}
PASS_LIMITS = {
    "hold_error_p95_m": 0.03,
    "joint_speed_rms_rad_s": 0.05,
    "settling_time_s": 2.0,
    "joint_limit_violation_count": 0.0,
    "non_finite_count": 0.0,
}


@dataclass(frozen=True, slots=True)
class TaskEvaluation:
    observations: tuple[MetricObservation, ...]
    trace: tuple[TracePoint, ...]
    passed: bool

    @property
    def values(self) -> dict[str, float | None]:
        return {
            observation.metric: observation.value for observation in self.observations
        }


def flatten_rows(record: RunRecord) -> tuple[Mapping[str, Any], ...]:
    return tuple(row for segment in record.segments for row in segment.timeseries)


def _finite_scalars(value: Any) -> Iterable[float]:
    if type(value) in (int, float):
        yield float(value)
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _finite_scalars(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _finite_scalars(item)


def first_nonfinite_step(record: RunRecord) -> int | None:
    for step, row in enumerate(flatten_rows(record)):
        if any(not math.isfinite(value) for value in _finite_scalars(row)):
            return step
    return None


def _vector(row: Mapping[str, Any], key: str) -> tuple[float, ...]:
    value = row[key]
    if not isinstance(value, (tuple, list)):
        raise ValueError(f"{key} must be a numeric vector")
    return tuple(float(item) for item in value)


def _distance(left: Sequence[float], right: Sequence[float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))


def _percentile95(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("percentile requires observations")
    ordered = sorted(values)
    rank = 0.95 * (len(ordered) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _sample_times(rows: Sequence[Mapping[str, Any]], count: int) -> tuple[float, ...]:
    start = float(rows[0]["t"])
    end = float(rows[-1]["t"])
    if count < 2 or end <= start:
        raise ValueError("uniform sampling requires a positive time span")
    return tuple(start + (end - start) * index / (count - 1) for index in range(count))


def _linear_value(
    rows: Sequence[Mapping[str, Any]],
    times: Sequence[float],
    target: float,
    getter,
) -> tuple[float, ...]:
    right = bisect.bisect_left(times, target)
    if right <= 0:
        return tuple(float(value) for value in getter(rows[0]))
    if right >= len(rows):
        return tuple(float(value) for value in getter(rows[-1]))
    if times[right] == target:
        return tuple(float(value) for value in getter(rows[right]))
    left = right - 1
    fraction = (target - times[left]) / (times[right] - times[left])
    before = getter(rows[left])
    after = getter(rows[right])
    return tuple(
        float(a) + (float(b) - float(a)) * fraction for a, b in zip(before, after)
    )


def _zoh_row(
    rows: Sequence[Mapping[str, Any]], times: Sequence[float], target: float
) -> Mapping[str, Any]:
    return rows[max(0, bisect.bisect_right(times, target) - 1)]


def evaluate_task(record: RunRecord, scenario: PublicScenario) -> TaskEvaluation:
    rows = flatten_rows(record)
    if len(rows) != scenario.duration_steps:
        raise ValueError("task run did not return the fixed scenario step count")
    bad_step = first_nonfinite_step(record)
    if bad_step is not None:
        observations = _observations(
            {
                "final_target_error_m": 1e9,
                "hold_error_p95_m": 1e9,
                "joint_speed_rms_rad_s": 1e9,
                "settling_time_s": None,
                "peak_energy_j": 1e9,
                "joint_limit_violation_count": 0.0,
                "non_finite_count": 1.0,
            }
        )
        return TaskEvaluation(observations, (), False)

    positions = [_vector(row, "body_xpos:end_effector") for row in rows]
    errors = [
        _distance(position, scenario.target_body_position) for position in positions
    ]
    hold_rows = rows[-scenario.hold_steps :]
    hold_errors = errors[-scenario.hold_steps :]
    qvel_sq = [value * value for row in hold_rows for value in _vector(row, "qvel")]
    joint_speed_rms = math.sqrt(sum(qvel_sq) / len(qvel_sq))
    settling = None
    instantaneous_speed_rms = [
        math.sqrt(
            sum(value * value for value in _vector(row, "qvel"))
            / len(_vector(row, "qvel"))
        )
        for row in rows
    ]
    last_outside = -1
    for index, (error, speed) in enumerate(zip(errors, instantaneous_speed_rms)):
        if (
            error > PASS_LIMITS["hold_error_p95_m"]
            or speed > PASS_LIMITS["joint_speed_rms_rad_s"]
        ):
            last_outside = index
    if last_outside < len(rows) - 1:
        settling = float(rows[last_outside + 1]["t"])

    joint_limit_violations = sum(
        1
        for row in rows
        for value in _vector(row, "qpos")
        if value < -1.2 or value > 1.2
    )
    peak_energy = max(
        abs(float(row["E_pot"])) + abs(float(row["E_kin"])) for row in rows
    )
    values: dict[str, float | None] = {
        "final_target_error_m": errors[-1],
        "hold_error_p95_m": _percentile95(hold_errors),
        "joint_speed_rms_rad_s": joint_speed_rms,
        "settling_time_s": settling,
        "peak_energy_j": peak_energy,
        "joint_limit_violation_count": float(joint_limit_violations),
        "non_finite_count": 0.0,
    }
    observations = _observations(values)
    passed = all(
        values[metric] is not None and values[metric] <= limit
        for metric, limit in PASS_LIMITS.items()
    )
    trace = resample_task_trace(rows, count=51)
    return TaskEvaluation(observations, trace, passed)


def _observations(values: Mapping[str, float | None]) -> tuple[MetricObservation, ...]:
    return tuple(
        MetricObservation(metric=metric, value=values[metric])
        for metric in TASK_METRIC_ORDER
    )


def resample_task_trace(
    rows: Sequence[Mapping[str, Any]], *, count: int = 51
) -> tuple[TracePoint, ...]:
    sample_times = _sample_times(rows, count)
    source_times = tuple(float(row["t"]) for row in rows)
    points = []
    for target in sample_times:
        position = _linear_value(
            rows, source_times, target, lambda row: row["body_xpos:end_effector"]
        )
        qpos = _linear_value(rows, source_times, target, lambda row: row["qpos"])
        qvel = _linear_value(rows, source_times, target, lambda row: row["qvel"])
        points.append(TracePoint(time_s=target, values=position + qpos + qvel))
    return tuple(points)


def behavior_diff(before: TaskEvaluation, after: TaskEvaluation) -> BehaviorDiff:
    before_values = before.values
    after_values = after.values
    deltas = []
    for metric in TASK_METRIC_ORDER:
        old = before_values[metric]
        new = after_values[metric]
        delta = None if old is None or new is None else new - old
        deltas.append(MetricDelta(metric=metric, before=old, after=new, delta=delta))

    clause_results = []
    for clause_id, metric in CLAUSE_METRIC.items():
        old = before_values[metric]
        new = after_values[metric]
        limit = PASS_LIMITS[metric]
        before_pass = old is not None and old <= limit
        after_pass = new is not None and new <= limit
        outcome = (
            "improved"
            if not before_pass and after_pass
            else "regressed"
            if before_pass and not after_pass
            else "unchanged"
        )
        clause_results.append(ClauseResult(clause_id=clause_id, outcome=outcome))

    first = _first_divergence(before.trace, after.trace)
    changed = first is not None
    outcomes = {result.outcome for result in clause_results}
    if after.passed:
        verdict = "public_pass"
    elif "improved" in outcomes and "regressed" in outcomes:
        verdict = "changed"
    elif "improved" in outcomes:
        verdict = "improved"
    elif "regressed" in outcomes:
        verdict = "regressed"
    elif changed:
        verdict = "changed"
    else:
        verdict = "unchanged_failure"
    if verdict not in {"public_pass", "unchanged_failure"} and not changed:
        raise ValueError("metric change lacks trace divergence evidence")
    return BehaviorDiff(
        changed=changed,
        first_divergence=first,
        metric_deltas=deltas,
        clause_outcomes=clause_results,
        verdict=verdict,
    )


def _first_divergence(
    before: Sequence[TracePoint], after: Sequence[TracePoint]
) -> FirstDivergence | None:
    if len(before) != len(after) or not before:
        return None
    for step, (old, new) in enumerate(zip(before, after)):
        body = _distance(old.values[:3], new.values[:3])
        if body > 1e-4:
            return FirstDivergence(
                step=step,
                time_s=new.time_s,
                signal="end_effector_position",
                magnitude=body,
            )
        qpos = max(abs(a - b) for a, b in zip(old.values[3:6], new.values[3:6]))
        if qpos > 1e-4:
            return FirstDivergence(
                step=step, time_s=new.time_s, signal="qpos", magnitude=qpos
            )
        qvel = max(abs(a - b) for a, b in zip(old.values[6:], new.values[6:]))
        if qvel > 1e-3:
            return FirstDivergence(
                step=step, time_s=new.time_s, signal="qvel", magnitude=qvel
            )
    return None


def resample_experiment_trace(
    record: RunRecord,
    *,
    observables: Sequence[ExperimentObservable],
    joint_names: Sequence[str],
    actuator_names: Sequence[str],
) -> ExperimentTrace:
    rows = flatten_rows(record)
    if not rows:
        raise ValueError("experiment returned no samples")
    if first_nonfinite_step(record) is not None:
        raise ValueError("non-finite experiments cannot be resampled")
    source_times = tuple(float(row["t"]) for row in rows)
    sample_times = _sample_times(rows, 256)
    columns: list[Any] = [TimeTraceColumn(kind="time")]
    extractors: list[tuple[str, Any]] = []
    for observable in observables:
        if isinstance(observable, QposObservable):
            for index, name in enumerate(joint_names):
                columns.append(JointTraceColumn(kind="qpos", joint_name=name))
                extractors.append(("linear", lambda row, i=index: (row["qpos"][i],)))
        elif isinstance(observable, QvelObservable):
            for index, name in enumerate(joint_names):
                columns.append(JointTraceColumn(kind="qvel", joint_name=name))
                extractors.append(("linear", lambda row, i=index: (row["qvel"][i],)))
        elif isinstance(observable, EnergyObservable):
            columns.extend(
                (
                    EnergyTraceColumn(kind="energy", component="potential"),
                    EnergyTraceColumn(kind="energy", component="kinetic"),
                )
            )
            extractors.extend(
                (
                    ("linear", lambda row: (row["E_pot"],)),
                    ("linear", lambda row: (row["E_kin"],)),
                )
            )
        elif isinstance(observable, ContactCountObservable):
            columns.append(ContactCountTraceColumn(kind="contact_count"))
            extractors.append(("zoh", lambda row: (row["ncon"],)))
        elif isinstance(observable, BodyPositionObservable):
            for axis_index, axis in enumerate(("x", "y", "z")):
                columns.append(
                    BodyPositionTraceColumn(
                        kind="body_position", body_name=observable.body_name, axis=axis
                    )
                )
                key = f"body_xpos:{observable.body_name}"
                extractors.append(
                    ("linear", lambda row, i=axis_index, k=key: (row[k][i],))
                )
        else:
            raise ValueError("unsupported experiment observable")
    for index, name in enumerate(actuator_names):
        columns.append(ActuatorControlTraceColumn(kind="control", actuator_name=name))
        extractors.append(("zoh", lambda row, i=index: (row["ctrl"][i],)))

    output_rows: list[dict[str, Any]] = []
    for target in sample_times:
        values: dict[str, float] = {}
        for interpolation, getter in extractors:
            if interpolation == "linear":
                sampled = _linear_value(rows, source_times, target, getter)
            else:
                sampled = tuple(
                    float(item) for item in getter(_zoh_row(rows, source_times, target))
                )
            column = columns[len(values) + 1]
            values[experiment_trace_value_key(column)] = sampled[0]
        output_rows.append({"time_s": target, "values": values})
    return ExperimentTrace(columns=columns, rows=output_rows)


__all__ = [
    "PASS_LIMITS",
    "TASK_METRIC_ORDER",
    "TaskEvaluation",
    "behavior_diff",
    "evaluate_task",
    "first_nonfinite_step",
    "flatten_rows",
    "resample_experiment_trace",
]
