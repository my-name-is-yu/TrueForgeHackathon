from __future__ import annotations

import bisect
import math
from typing import Any, Iterable, Mapping, Sequence

from .fixture import JOINT_RANGE_RAD, PublicScenario
from .runner import RunRecord
from .schemas import (
    BodyPositionObservable,
    ContactCountObservable,
    EnergyObservable,
    ExperimentObservable,
    ExperimentTrace,
    MetricObservation,
    QposObservable,
    QvelObservable,
    TracePoint,
    experiment_trace_columns,
    experiment_trace_value_key,
)
from .task_evaluation import PASS_LIMITS, TASK_METRIC_ORDER, TaskEvaluation


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
        return TaskEvaluation(observations, ())

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
        if value < JOINT_RANGE_RAD[0] or value > JOINT_RANGE_RAD[1]
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
    trace = resample_task_trace(rows, count=51)
    return TaskEvaluation(observations, trace)


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
    columns = list(
        experiment_trace_columns(
            observables=observables,
            joint_names=joint_names,
            actuator_names=actuator_names,
        )
    )
    extractors: list[tuple[str, Any]] = []
    for observable in observables:
        if isinstance(observable, QposObservable):
            for index, _ in enumerate(joint_names):
                extractors.append(("linear", lambda row, i=index: (row["qpos"][i],)))
        elif isinstance(observable, QvelObservable):
            for index, _ in enumerate(joint_names):
                extractors.append(("linear", lambda row, i=index: (row["qvel"][i],)))
        elif isinstance(observable, EnergyObservable):
            extractors.extend(
                (
                    ("linear", lambda row: (row["E_pot"],)),
                    ("linear", lambda row: (row["E_kin"],)),
                )
            )
        elif isinstance(observable, ContactCountObservable):
            extractors.append(("zoh", lambda row: (row["ncon"],)))
        elif isinstance(observable, BodyPositionObservable):
            for axis_index in range(3):
                key = f"body_xpos:{observable.body_name}"
                extractors.append(
                    ("linear", lambda row, i=axis_index, k=key: (row[k][i],))
                )
        else:
            raise ValueError("unsupported experiment observable")
    for index, _ in enumerate(actuator_names):
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
    "evaluate_task",
    "first_nonfinite_step",
    "flatten_rows",
    "resample_experiment_trace",
]
