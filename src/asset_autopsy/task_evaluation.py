from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Mapping, Sequence

if TYPE_CHECKING:
    from .schemas import BehaviorDiff, MetricObservation, TracePoint


TASK_METRIC_ORDER = (
    "final_target_error_m",
    "hold_error_p95_m",
    "joint_speed_rms_rad_s",
    "settling_time_s",
    "peak_energy_j",
    "joint_limit_violation_count",
    "non_finite_count",
)
COUNT_METRICS = frozenset({"joint_limit_violation_count", "non_finite_count"})
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
TRACE_DIVERGENCE_LIMITS = {
    "end_effector_position": 1e-4,
    "qpos": 1e-4,
    "qvel": 1e-3,
}

ClauseOutcome = Literal["improved", "regressed", "unchanged"]
BehaviorVerdict = Literal[
    "regressed", "changed", "improved", "public_pass", "unchanged_failure"
]


def public_contract_limits() -> dict[str, float | int]:
    return {
        metric: int(limit) if metric in COUNT_METRICS else limit
        for metric, limit in PASS_LIMITS.items()
    }


@dataclass(frozen=True, slots=True)
class ClauseEvaluation:
    clause_id: str
    metric: str
    value: float | None
    limit: float

    @property
    def passed(self) -> bool:
        return self.value is not None and self.value <= self.limit


def evaluate_clauses(
    values: Mapping[str, float | None],
) -> tuple[ClauseEvaluation, ...]:
    return tuple(
        ClauseEvaluation(
            clause_id=clause_id,
            metric=metric,
            value=values[metric],
            limit=PASS_LIMITS[metric],
        )
        for clause_id, metric in CLAUSE_METRIC.items()
    )


def task_passed(values: Mapping[str, float | None]) -> bool:
    return all(clause.passed for clause in evaluate_clauses(values))


def compare_clause_outcomes(
    before: Mapping[str, float | None], after: Mapping[str, float | None]
) -> tuple[tuple[str, ClauseOutcome], ...]:
    before_clauses = {clause.clause_id: clause for clause in evaluate_clauses(before)}
    return tuple(
        (
            clause.clause_id,
            "improved"
            if not before_clauses[clause.clause_id].passed and clause.passed
            else "regressed"
            if before_clauses[clause.clause_id].passed and not clause.passed
            else "unchanged",
        )
        for clause in evaluate_clauses(after)
    )


def behavior_verdict(
    *,
    after_passed: bool,
    clause_outcomes: Sequence[ClauseOutcome],
    changed: bool,
) -> BehaviorVerdict:
    outcomes = set(clause_outcomes)
    if after_passed:
        return "public_pass"
    if "improved" in outcomes and "regressed" in outcomes:
        return "changed"
    if "improved" in outcomes:
        return "improved"
    if "regressed" in outcomes:
        return "regressed"
    if changed:
        return "changed"
    return "unchanged_failure"


@dataclass(frozen=True, slots=True)
class TaskEvaluation:
    observations: tuple[MetricObservation, ...]
    trace: tuple[TracePoint, ...]

    @property
    def values(self) -> dict[str, float | None]:
        return {
            observation.metric: observation.value for observation in self.observations
        }

    @property
    def clauses(self) -> tuple[ClauseEvaluation, ...]:
        return evaluate_clauses(self.values)

    @property
    def violated_clause_ids(self) -> tuple[str, ...]:
        return tuple(clause.clause_id for clause in self.clauses if not clause.passed)

    @property
    def passed(self) -> bool:
        return all(clause.passed for clause in self.clauses)

    @property
    def result(self) -> Literal["pass", "fail"]:
        return "pass" if self.passed else "fail"

    def behavior_diff_from(self, before: TaskEvaluation) -> BehaviorDiff:
        from .schemas import BehaviorDiff, ClauseResult, MetricDelta

        before_values = before.values
        after_values = self.values
        metric_deltas = []
        for metric in TASK_METRIC_ORDER:
            old = before_values[metric]
            new = after_values[metric]
            delta = None if old is None or new is None else new - old
            metric_deltas.append(
                MetricDelta(metric=metric, before=old, after=new, delta=delta)
            )

        clause_outcomes = [
            ClauseResult(clause_id=clause_id, outcome=outcome)
            for clause_id, outcome in compare_clause_outcomes(
                before_values, after_values
            )
        ]
        first = _first_divergence(before.trace, self.trace)
        changed = first is not None
        verdict = behavior_verdict(
            after_passed=self.passed,
            clause_outcomes=[result.outcome for result in clause_outcomes],
            changed=changed,
        )
        if verdict not in {"public_pass", "unchanged_failure"} and not changed:
            raise ValueError("metric change lacks trace divergence evidence")
        return BehaviorDiff(
            changed=changed,
            first_divergence=first,
            metric_deltas=metric_deltas,
            clause_outcomes=clause_outcomes,
            verdict=verdict,
        )


def _first_divergence(
    before: Sequence[TracePoint], after: Sequence[TracePoint]
):
    from .schemas import FirstDivergence

    if len(before) != len(after) or not before:
        return None
    for step, (old, new) in enumerate(zip(before, after)):
        body = math.dist(old.values[:3], new.values[:3])
        if body > TRACE_DIVERGENCE_LIMITS["end_effector_position"]:
            return FirstDivergence(
                step=step,
                time_s=new.time_s,
                signal="end_effector_position",
                magnitude=body,
            )
        qpos = max(abs(a - b) for a, b in zip(old.values[3:6], new.values[3:6]))
        if qpos > TRACE_DIVERGENCE_LIMITS["qpos"]:
            return FirstDivergence(
                step=step, time_s=new.time_s, signal="qpos", magnitude=qpos
            )
        qvel = max(abs(a - b) for a, b in zip(old.values[6:], new.values[6:]))
        if qvel > TRACE_DIVERGENCE_LIMITS["qvel"]:
            return FirstDivergence(
                step=step, time_s=new.time_s, signal="qvel", magnitude=qvel
            )
    return None


__all__ = [
    "CLAUSE_METRIC",
    "COUNT_METRICS",
    "PASS_LIMITS",
    "TASK_METRIC_ORDER",
    "TRACE_DIVERGENCE_LIMITS",
    "ClauseEvaluation",
    "TaskEvaluation",
    "behavior_verdict",
    "compare_clause_outcomes",
    "evaluate_clauses",
    "public_contract_limits",
    "task_passed",
]
