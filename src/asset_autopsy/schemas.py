from __future__ import annotations

import math
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    AllowInfNan,
    BaseModel,
    ConfigDict,
    Field,
    Strict,
    StrictBool,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)


SCHEMA_VERSION = "asset-autopsy/v1"

CaseId: TypeAlias = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=6,
        max_length=96,
        pattern=r"^case_[A-Za-z0-9][A-Za-z0-9_-]*$",
    ),
]
RevisionId: TypeAlias = Annotated[
    str,
    StringConstraints(strict=True, min_length=2, max_length=32, pattern=r"^r[0-9]+$"),
]
RequestId: TypeAlias = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=5,
        max_length=96,
        pattern=r"^req_[A-Za-z0-9][A-Za-z0-9_-]*$",
    ),
]
EventId: TypeAlias = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=5,
        max_length=96,
        pattern=r"^evt_[A-Za-z0-9][A-Za-z0-9_-]*$",
    ),
]
ArtifactId: TypeAlias = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=5,
        max_length=96,
        pattern=r"^art_[A-Za-z0-9][A-Za-z0-9_-]*$",
    ),
]
HypothesisId: TypeAlias = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=5,
        max_length=96,
        pattern=r"^hyp_[A-Za-z0-9][A-Za-z0-9_-]*$",
    ),
]
RunId: TypeAlias = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=5,
        max_length=96,
        pattern=r"^run_[A-Za-z0-9][A-Za-z0-9_-]*$",
    ),
]
AssetHash: TypeAlias = Annotated[
    str,
    StringConstraints(strict=True, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
]
ElementName: TypeAlias = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$",
    ),
]
SafeText: TypeAlias = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=2000)]
MetricName: TypeAlias = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=96, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$"),
]
StrictFiniteFloat: TypeAlias = Annotated[float, Strict(), AllowInfNan(False)]
AxisVector: TypeAlias = tuple[StrictFiniteFloat, StrictFiniteFloat, StrictFiniteFloat]
RunTaskMetricName: TypeAlias = Literal[
    "final_target_error_m",
    "hold_error_p95_m",
    "joint_speed_rms_rad_s",
    "settling_time_s",
    "peak_energy_j",
    "joint_limit_violation_count",
    "non_finite_count",
]
ContractClauseId: TypeAlias = Literal[
    "reach_error", "stable_hold", "settling", "finite_state", "joint_limits"
]
_RUN_TASK_METRICS = frozenset(
    {
        "final_target_error_m",
        "hold_error_p95_m",
        "joint_speed_rms_rad_s",
        "settling_time_s",
        "peak_energy_j",
        "joint_limit_violation_count",
        "non_finite_count",
    }
)
_METRIC_DELTA_REL_TOLERANCE = 1e-9
_METRIC_DELTA_ABS_TOLERANCE = 1e-12
_PASS_METRIC_LIMITS = {
    "hold_error_p95_m": 0.03,
    "joint_speed_rms_rad_s": 0.05,
    "settling_time_s": 2.0,
    "joint_limit_violation_count": 0.0,
    "non_finite_count": 0.0,
}
_CONTRACT_CLAUSE_IDS = frozenset(
    {"reach_error", "stable_hold", "settling", "finite_state", "joint_limits"}
)
_CLAUSE_METRICS = {
    "reach_error": "hold_error_p95_m",
    "stable_hold": "joint_speed_rms_rad_s",
    "settling": "settling_time_s",
    "finite_state": "non_finite_count",
    "joint_limits": "joint_limit_violation_count",
}

AllowedAttribute: TypeAlias = Literal["axis", "damping", "armature", "frictionloss"]
HypothesisAttribute: TypeAlias = Literal[
    "axis", "damping", "armature", "frictionloss", "joint"
]
SegmentLabel: TypeAlias = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=64),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)


class PatchTarget(StrictModel):
    kind: Literal["joint"]
    name: ElementName


class AxisPatch(StrictModel):
    target: PatchTarget
    attribute: Literal["axis"]
    expected_old_value: AxisVector
    new_value: AxisVector

    @field_validator("expected_old_value", "new_value")
    @classmethod
    def normalize_axis(cls, value: AxisVector) -> AxisVector:
        length = math.sqrt(sum(component * component for component in value))
        if length <= 0.0 or not math.isfinite(length):
            raise ValueError("axis must be non-zero")
        return tuple(component / length for component in value)


class ScalarPatch(StrictModel):
    target: PatchTarget
    attribute: Literal["damping", "armature", "frictionloss"]
    expected_old_value: StrictFiniteFloat
    new_value: StrictFiniteFloat

    @field_validator("expected_old_value", "new_value")
    @classmethod
    def validate_range(cls, value: float, info) -> float:
        attribute = info.data.get("attribute")
        if attribute == "damping" and not 0.0 <= value <= 100.0:
            raise ValueError("damping is outside the family safety range")
        if attribute == "armature" and not 0.0 <= value <= 10.0:
            raise ValueError("armature is outside the family safety range")
        if attribute == "frictionloss" and not 0.0 <= value <= 100.0:
            raise ValueError("frictionloss is outside the family safety range")
        return value


AttributePatch: TypeAlias = Annotated[AxisPatch | ScalarPatch, Field(discriminator="attribute")]


class ElementReference(StrictModel):
    kind: Literal["joint", "actuator", "body", "site"]
    name: ElementName
    attributes: list[HypothesisAttribute] = Field(min_length=1, max_length=4)


class CompetingExplanation(StrictModel):
    claim: SafeText
    suspected_elements: list[ElementReference] = Field(min_length=1, max_length=8)
    discriminating_reason: SafeText


PredicateOperator: TypeAlias = Literal["lt", "lte", "eq", "gte", "gt"]


class Predicate(StrictModel):
    metric: MetricName
    op: PredicateOperator
    value: StrictFiniteFloat


class Hypothesis(StrictModel):
    claim: SafeText
    suspected_elements: list[ElementReference] = Field(min_length=1, max_length=8)
    competing_explanation: CompetingExplanation
    prediction: SafeText
    falsifier: SafeText


class JointPosition(StrictModel):
    joint_name: ElementName
    position_rad: StrictFiniteFloat


class ActuatorControl(StrictModel):
    actuator_name: ElementName
    value: StrictFiniteFloat


class ConstantControlSegment(StrictModel):
    label: SegmentLabel | None = None
    n_steps: StrictInt = Field(ge=1)
    controls: list[ActuatorControl] = Field(min_length=1, max_length=64)

    @field_validator("controls")
    @classmethod
    def validate_unique_controls(
        cls, value: list[ActuatorControl]
    ) -> list[ActuatorControl]:
        names = [control.actuator_name for control in value]
        if len(names) != len(set(names)):
            raise ValueError("segment controls must not repeat actuator names")
        return value


class QposObservable(StrictModel):
    kind: Literal["qpos"]


class QvelObservable(StrictModel):
    kind: Literal["qvel"]


class EnergyObservable(StrictModel):
    kind: Literal["energy"]


class ContactCountObservable(StrictModel):
    kind: Literal["contact_count"]


class BodyPositionObservable(StrictModel):
    kind: Literal["body_position"]
    body_name: ElementName


ExperimentObservable: TypeAlias = Annotated[
    QposObservable
    | QvelObservable
    | EnergyObservable
    | ContactCountObservable
    | BodyPositionObservable,
    Field(discriminator="kind"),
]


class ExpectedEffect(StrictModel):
    scenario_id: Literal["public_center"]
    predicates: list[Predicate] = Field(min_length=1, max_length=16)


class OpenCaseInput(StrictModel):
    case_id: CaseId


class InspectAssetInput(StrictModel):
    case_id: CaseId
    revision_id: RevisionId
    view: Literal["authored", "compiled", "both"]


class RunTaskInput(StrictModel):
    case_id: CaseId
    revision_id: RevisionId
    scenario_id: Literal["public_center"]
    capture: Literal["metrics", "metrics_and_filmstrip"]


class RunExperimentInput(StrictModel):
    case_id: CaseId
    revision_id: RevisionId
    hypothesis: Hypothesis
    initial_joint_positions: list[JointPosition] = Field(min_length=1, max_length=64)
    segments: list[ConstantControlSegment] = Field(min_length=1, max_length=16)
    observables: list[ExperimentObservable] = Field(min_length=1, max_length=8)
    capture_final_snapshot: StrictBool = False

    @model_validator(mode="after")
    def validate_experiment(self) -> RunExperimentInput:
        joint_names = [position.joint_name for position in self.initial_joint_positions]
        if len(joint_names) != len(set(joint_names)):
            raise ValueError("initial joint positions must not repeat joint names")

        actuator_names = {
            control.actuator_name for control in self.segments[0].controls
        }
        if any(
            {control.actuator_name for control in segment.controls} != actuator_names
            for segment in self.segments[1:]
        ):
            raise ValueError("every segment must control the same position actuators")

        total_steps = sum(segment.n_steps for segment in self.segments)
        if not 256 <= total_steps <= 100_000:
            raise ValueError("experiment total steps must be between 256 and 100000")

        observable_keys = [
            (observable.kind, getattr(observable, "body_name", None))
            for observable in self.observables
        ]
        if len(observable_keys) != len(set(observable_keys)):
            raise ValueError("experiment observables must be unique")
        return self


class CreateRevisionInput(StrictModel):
    case_id: CaseId
    base_revision_id: RevisionId
    expected_base_sha256: AssetHash
    basis_hypothesis_id: HypothesisId
    basis_experiment_run_id: RunId
    patch: AttributePatch
    rationale: SafeText
    expected_effect: ExpectedEffect


class VerifyRevisionInput(StrictModel):
    case_id: CaseId
    revision_id: RevisionId
    expected_asset_sha256: AssetHash


class PromotionTicket(StrictModel):
    ticket_id: EventId
    case_id: CaseId
    revision_id: RevisionId
    asset_sha256: AssetHash
    canonical_diff: list["CanonicalDiffEntry"] = Field(min_length=1, max_length=2)
    public_result: "AggregateResult"
    holdout_result: "AggregateResult"
    export_name: Annotated[
        str,
        StringConstraints(strict=True, min_length=1, max_length=96, pattern=r"^[a-z0-9][a-z0-9-]*$"),
    ]
    qualified_core_sha256: AssetHash
    ticket_digest: AssetHash

    @model_validator(mode="after")
    def validate_qualification(self) -> PromotionTicket:
        if (
            self.public_result.passed != 1
            or self.public_result.total != 1
            or self.public_result.violated_clause_ids
            or self.holdout_result.passed != 3
            or self.holdout_result.total != 3
            or self.holdout_result.violated_clause_ids
        ):
            raise ValueError("promotion ticket requires successful fixed-suite qualification")
        return self


class PublishRevisionInput(StrictModel):
    case_id: CaseId
    promotion_ticket: PromotionTicket

    @model_validator(mode="after")
    def validate_ticket_case(self) -> PublishRevisionInput:
        if self.promotion_ticket.case_id != self.case_id:
            raise ValueError("promotion ticket case must match publication case")
        return self


class ArtifactRef(StrictModel):
    artifact_id: ArtifactId
    kind: Literal[
        "trace_json",
        "filmstrip",
        "qualification",
    ]
    uri: Annotated[
        str,
        StringConstraints(strict=True, min_length=10, max_length=160, pattern=r"^autopsy://[A-Za-z0-9_./-]+$"),
    ]
    media_type: Annotated[
        str,
        StringConstraints(strict=True, min_length=3, max_length=96, pattern=r"^[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+$"),
    ]
    sha256: AssetHash
    bytes: StrictInt = Field(ge=0)


class CommonOutput(StrictModel):
    schema_version: Literal[SCHEMA_VERSION]
    request_id: RequestId
    case_id: CaseId
    event_ids: list[EventId] = Field(default_factory=list, max_length=20)
    warnings: list[SafeText] = Field(default_factory=list, max_length=8)
    artifacts: list[ArtifactRef] = Field(default_factory=list, max_length=8)


class Range(StrictModel):
    minimum: StrictFiniteFloat
    maximum: StrictFiniteFloat

    @field_validator("maximum")
    @classmethod
    def validate_order(cls, value: float, info) -> float:
        minimum = info.data.get("minimum")
        if minimum is not None and value < minimum:
            raise ValueError("maximum must not be less than minimum")
        return value


class PatchPolicy(StrictModel):
    editable_attributes: tuple[AllowedAttribute, ...] = Field(min_length=4, max_length=4)
    axis_unit_vector: StrictBool
    damping: Range
    armature: Range
    frictionloss: Range

    @field_validator("editable_attributes")
    @classmethod
    def validate_allowlist(cls, value: tuple[AllowedAttribute, ...]) -> tuple[AllowedAttribute, ...]:
        if set(value) != {"axis", "damping", "armature", "frictionloss"}:
            raise ValueError("patch allowlist must contain exactly the four editable attributes")
        return value

    @model_validator(mode="after")
    def validate_enforced_policy(self) -> PatchPolicy:
        if not self.axis_unit_vector:
            raise ValueError("axis_unit_vector must be true")

        enforced_ranges = {
            "damping": (0.0, 100.0),
            "armature": (0.0, 10.0),
            "frictionloss": (0.0, 100.0),
        }
        for attribute, (minimum, maximum) in enforced_ranges.items():
            advertised = getattr(self, attribute)
            if (advertised.minimum, advertised.maximum) != (minimum, maximum):
                raise ValueError(f"{attribute} range must match the enforced safety range")
        return self


class CompiledDimensions(StrictModel):
    nq: StrictInt = Field(ge=0)
    nv: StrictInt = Field(ge=0)
    nu: StrictInt = Field(ge=0)
    timestep_s: StrictFiniteFloat = Field(gt=0.0)


class JointSummary(StrictModel):
    name: ElementName
    axis: AxisVector
    damping: StrictFiniteFloat = Field(ge=0.0, le=100.0)
    armature: StrictFiniteFloat = Field(ge=0.0, le=10.0)
    frictionloss: StrictFiniteFloat = Field(ge=0.0, le=100.0)
    position_range: tuple[StrictFiniteFloat, StrictFiniteFloat] | None = None
    body_parent: ElementName

    @field_validator("axis")
    @classmethod
    def normalize_summary_axis(cls, value: AxisVector) -> AxisVector:
        length = math.sqrt(sum(component * component for component in value))
        if length <= 0.0 or not math.isfinite(length):
            raise ValueError("axis must be non-zero")
        return tuple(component / length for component in value)

    @field_validator("position_range")
    @classmethod
    def validate_position_range(
        cls, value: tuple[StrictFiniteFloat, StrictFiniteFloat] | None
    ) -> tuple[StrictFiniteFloat, StrictFiniteFloat] | None:
        if value is not None and value[0] > value[1]:
            raise ValueError("position range lower bound cannot exceed upper bound")
        return value


class BodySummary(StrictModel):
    name: ElementName
    parent: ElementName | None = None


class ActuatorSummary(StrictModel):
    name: ElementName
    joint_name: ElementName
    control_kind: Literal["position"]
    control_range: tuple[StrictFiniteFloat, StrictFiniteFloat]

    @field_validator("control_range")
    @classmethod
    def validate_control_range(
        cls, value: tuple[StrictFiniteFloat, StrictFiniteFloat]
    ) -> tuple[StrictFiniteFloat, StrictFiniteFloat]:
        if value[0] > value[1]:
            raise ValueError("control range lower bound cannot exceed upper bound")
        return value


class ScenarioSummary(StrictModel):
    scenario_id: Literal["public_center"]
    observable_metrics: list[RunTaskMetricName] = Field(min_length=7, max_length=7)

    @field_validator("observable_metrics")
    @classmethod
    def validate_metric_set(
        cls, value: list[RunTaskMetricName]
    ) -> list[RunTaskMetricName]:
        if set(value) != _RUN_TASK_METRICS:
            raise ValueError("public scenario must advertise each fixed metric exactly once")
        return value


class ContractClause(StrictModel):
    clause_id: ContractClauseId
    description: SafeText


class RevisionSummary(StrictModel):
    revision_id: RevisionId
    asset_sha256: AssetHash
    parent_revision_id: RevisionId | None = None
    canonical_diff: list["CanonicalDiffEntry"] = Field(default_factory=list, max_length=1)

    @model_validator(mode="after")
    def validate_lineage(self) -> RevisionSummary:
        if self.revision_id == "r000":
            if self.parent_revision_id is not None or self.canonical_diff:
                raise ValueError("root revision cannot have parent or diff provenance")
            return self
        if self.parent_revision_id is None or len(self.canonical_diff) != 1:
            raise ValueError("child revision requires one parent and one canonical diff")
        if self.parent_revision_id == self.revision_id:
            raise ValueError("revision cannot be its own parent")
        return self


class PublicEventSummary(StrictModel):
    event_id: EventId
    kind: Literal[
        "CASE_OPENED",
        "TASK_COMPLETED",
        "HYPOTHESIS_RECORDED",
        "EXPERIMENT_COMPLETED",
        "EXPERIMENT_FAILED",
        "REVISION_CREATED",
        "REVISION_REJECTED",
        "QUALIFICATION_PASSED",
        "QUALIFICATION_FAILED",
    ]
    summary: SafeText


class OpenCaseOutput(CommonOutput):
    qualification_state: Literal["unused", "running", "passed", "failed"]
    original_revision_id: RevisionId
    original_asset_sha256: AssetHash
    controller_sha256: AssetHash
    public_contract_sha256: AssetHash
    runner_sha256: AssetHash
    holdout_commitment_sha256: AssetHash
    public_scenarios: list[ScenarioSummary] = Field(min_length=1, max_length=16)
    contract_clauses: list[ContractClause] = Field(min_length=1, max_length=32)
    compiled_dimensions: CompiledDimensions
    joints: list[JointSummary] = Field(min_length=1, max_length=32)
    bodies: list[BodySummary] = Field(min_length=1, max_length=64)
    actuators: list[ActuatorSummary] = Field(min_length=1, max_length=64)
    observable_metric_names: list[MetricName] = Field(min_length=1, max_length=64)
    patch_policy: PatchPolicy
    remaining_budgets: "BudgetSummary"
    revision_history: list[RevisionSummary] = Field(min_length=1, max_length=32)
    event_tail: list[PublicEventSummary] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_fixed_contract_and_lifecycle(self) -> OpenCaseOutput:
        if len(self.public_scenarios) != 1:
            raise ValueError("open case must advertise the one fixed public scenario")
        clause_ids = [clause.clause_id for clause in self.contract_clauses]
        if len(clause_ids) != len(_CONTRACT_CLAUSE_IDS) or set(clause_ids) != _CONTRACT_CLAUSE_IDS:
            raise ValueError("open case must advertise each fixed contract clause exactly once")
        if len(self.observable_metric_names) != len(set(self.observable_metric_names)):
            raise ValueError("observable metric names must be unique")
        if not _RUN_TASK_METRICS.issubset(self.observable_metric_names):
            raise ValueError("observable metrics must include every fixed task metric")
        expected_qualification_budget = 1 if self.qualification_state == "unused" else 0
        if self.remaining_budgets.qualification_remaining != expected_qualification_budget:
            raise ValueError("qualification budget must match qualification lifecycle")
        if self.original_revision_id != "r000":
            raise ValueError("original revision must be r000")
        if self.revision_history[0].revision_id != self.original_revision_id:
            raise ValueError("revision history must begin with the original revision")
        if self.revision_history[0].asset_sha256 != self.original_asset_sha256:
            raise ValueError("original asset hash must match revision history")
        revision_ids = [revision.revision_id for revision in self.revision_history]
        if len(revision_ids) != len(set(revision_ids)):
            raise ValueError("revision history IDs must be unique")
        for parent, child in zip(self.revision_history, self.revision_history[1:]):
            if child.parent_revision_id != parent.revision_id:
                raise ValueError("revision history must form one linear chain")
        return self


class MetricObservation(StrictModel):
    metric: RunTaskMetricName
    value: StrictFiniteFloat | None

    @model_validator(mode="after")
    def validate_nullable_value(self) -> MetricObservation:
        if self.value is None and self.metric != "settling_time_s":
            raise ValueError("only settling_time_s may have a null value")
        if self.value is not None and self.value < 0.0:
            raise ValueError("task observations must be nonnegative")
        if self.metric in {"joint_limit_violation_count", "non_finite_count"}:
            if self.value is None or not self.value.is_integer():
                raise ValueError("count observations must be nonnegative integers")
        return self


class TracePoint(StrictModel):
    time_s: StrictFiniteFloat = Field(ge=0.0)
    values: tuple[StrictFiniteFloat, ...] = Field(min_length=1, max_length=64)


class FirstDivergence(StrictModel):
    step: StrictInt = Field(ge=0)
    time_s: StrictFiniteFloat = Field(ge=0.0)
    signal: Literal["end_effector_position", "qpos", "qvel"]
    magnitude: StrictFiniteFloat = Field(ge=0.0)

    @model_validator(mode="after")
    def validate_threshold(self) -> FirstDivergence:
        thresholds = {
            "end_effector_position": 1e-4,
            "qpos": 1e-4,
            "qvel": 1e-3,
        }
        if self.magnitude <= thresholds[self.signal]:
            raise ValueError("first divergence magnitude must exceed the signal threshold")
        return self


class MetricDelta(StrictModel):
    metric: RunTaskMetricName
    before: StrictFiniteFloat | None
    after: StrictFiniteFloat | None
    delta: StrictFiniteFloat | None

    @model_validator(mode="after")
    def validate_delta(self) -> MetricDelta:
        for endpoint in (self.before, self.after):
            if endpoint is not None and endpoint < 0.0:
                raise ValueError("metric delta endpoints must be nonnegative")
        if self.metric in {"joint_limit_violation_count", "non_finite_count"}:
            if self.before is None or self.after is None:
                raise ValueError("count metric endpoints cannot be null")
            if not self.before.is_integer() or not self.after.is_integer():
                raise ValueError("count metric endpoints must be integers")
        if self.before is None or self.after is None:
            if self.metric != "settling_time_s":
                raise ValueError("only settling_time_s may have null metric endpoints")
            if self.delta is not None:
                raise ValueError("a null settling-time transition must have a null delta")
            return self
        if self.delta is None:
            raise ValueError("finite metric endpoints require a finite delta")
        expected = self.after - self.before
        if not math.isclose(
            self.delta,
            expected,
            rel_tol=_METRIC_DELTA_REL_TOLERANCE,
            abs_tol=_METRIC_DELTA_ABS_TOLERANCE,
        ):
            raise ValueError("delta must equal after minus before")
        return self


class ClauseResult(StrictModel):
    clause_id: ContractClauseId
    outcome: Literal["improved", "regressed", "unchanged"]


class BehaviorDiff(StrictModel):
    changed: StrictBool
    first_divergence: FirstDivergence | None = None
    metric_deltas: list[MetricDelta] = Field(min_length=1, max_length=64)
    clause_outcomes: list[ClauseResult] = Field(min_length=1, max_length=32)
    verdict: Literal[
        "regressed", "changed", "improved", "public_pass", "unchanged_failure"
    ]

    @model_validator(mode="after")
    def validate_evidence_state(self) -> BehaviorDiff:
        metrics = [delta.metric for delta in self.metric_deltas]
        if len(metrics) != len(_RUN_TASK_METRICS) or set(metrics) != _RUN_TASK_METRICS:
            raise ValueError("behavior diff must contain each fixed metric exactly once")
        clauses = [result.clause_id for result in self.clause_outcomes]
        if len(clauses) != len(_CONTRACT_CLAUSE_IDS) or set(clauses) != _CONTRACT_CLAUSE_IDS:
            raise ValueError("behavior diff must contain each fixed contract clause exactly once")
        deltas = {delta.metric: delta for delta in self.metric_deltas}
        for result in self.clause_outcomes:
            delta = deltas[_CLAUSE_METRICS[result.clause_id]]
            limit = _PASS_METRIC_LIMITS[delta.metric]
            before_passed = delta.before is not None and delta.before <= limit
            after_passed = delta.after is not None and delta.after <= limit
            if not before_passed and after_passed:
                expected_outcome = "improved"
            elif before_passed and not after_passed:
                expected_outcome = "regressed"
            else:
                expected_outcome = "unchanged"
            if result.outcome != expected_outcome:
                raise ValueError("clause outcome must match its contract-state transition")
        outcomes = {result.outcome for result in self.clause_outcomes}
        all_clauses_pass = all(
            deltas[metric].after is not None and deltas[metric].after <= limit
            for metric, limit in _PASS_METRIC_LIMITS.items()
        )
        if all_clauses_pass:
            expected_verdict = "public_pass"
        elif "improved" in outcomes and "regressed" in outcomes:
            expected_verdict = "changed"
        elif "improved" in outcomes:
            expected_verdict = "improved"
        elif "regressed" in outcomes:
            expected_verdict = "regressed"
        elif self.changed:
            expected_verdict = "changed"
        else:
            expected_verdict = "unchanged_failure"
        if self.verdict != expected_verdict:
            raise ValueError("verdict must match the contract clause outcomes")
        if self.changed and self.first_divergence is None:
            raise ValueError("changed behavior requires first divergence evidence")
        if self.changed and self.verdict == "unchanged_failure":
            raise ValueError("changed behavior cannot have an unchanged verdict")
        if not self.changed:
            if self.first_divergence is not None:
                raise ValueError("unchanged behavior cannot report first divergence")
            if self.verdict not in {"public_pass", "unchanged_failure"}:
                raise ValueError("unchanged behavior requires an unchanged verdict")
        return self


class RunTaskOutput(CommonOutput):
    revision_id: RevisionId
    scenario_id: Literal["public_center"]
    result: Literal["pass", "fail"]
    observations: list[MetricObservation] = Field(min_length=1, max_length=64)
    trace: list[TracePoint] = Field(default_factory=list, max_length=51)
    behavior_diff: BehaviorDiff | None = None

    @model_validator(mode="after")
    def validate_observation_set(self) -> RunTaskOutput:
        metrics = [observation.metric for observation in self.observations]
        if len(metrics) != len(_RUN_TASK_METRICS) or len(set(metrics)) != len(metrics):
            raise ValueError("run task observations must contain each fixed metric exactly once")
        if set(metrics) != _RUN_TASK_METRICS:
            raise ValueError("run task observations must contain the fixed metric set")
        return self

    @model_validator(mode="after")
    def validate_behavior_diff_result(self) -> RunTaskOutput:
        if self.revision_id == "r000":
            if self.behavior_diff is not None:
                raise ValueError("root task output cannot have behavior diff evidence")
            return self
        if self.behavior_diff is None:
            raise ValueError("child task output requires behavior diff evidence")
        if self.result == "pass" and self.behavior_diff.verdict != "public_pass":
            raise ValueError("a passing task must have the public_pass verdict")
        if self.result == "fail" and self.behavior_diff.verdict == "public_pass":
            raise ValueError("a failing task cannot have the public_pass verdict")
        if self.behavior_diff.changed:
            return self
        if self.result == "fail" and self.behavior_diff.verdict != "unchanged_failure":
            raise ValueError("a failing unchanged task must have the unchanged_failure verdict")
        return self

    @model_validator(mode="after")
    def validate_result_against_contract(self) -> RunTaskOutput:
        values = {observation.metric: observation.value for observation in self.observations}
        contract_passed = all(
            values[metric] is not None and values[metric] <= limit
            for metric, limit in _PASS_METRIC_LIMITS.items()
        )
        if (self.result == "pass") != contract_passed:
            raise ValueError("task result must match the fixed contract clauses")
        return self

    @model_validator(mode="after")
    def validate_behavior_diff_observations(self) -> RunTaskOutput:
        if self.behavior_diff is None:
            return self
        observed = {observation.metric: observation.value for observation in self.observations}
        for delta in self.behavior_diff.metric_deltas:
            if delta.after != observed[delta.metric]:
                raise ValueError("behavior diff after values must match task observations")
        return self

    @model_validator(mode="after")
    def validate_trace_sampling(self) -> RunTaskOutput:
        intervals = [
            current.time_s - previous.time_s
            for previous, current in zip(self.trace, self.trace[1:])
        ]
        if not intervals:
            return self
        if intervals[0] <= 0.0 or any(
            interval <= 0.0
            or not math.isclose(
                interval,
                intervals[0],
                rel_tol=_METRIC_DELTA_REL_TOLERANCE,
                abs_tol=_METRIC_DELTA_ABS_TOLERANCE,
            )
            for interval in intervals[1:]
        ):
            raise ValueError("task trace timestamps must be uniformly sampled")
        return self


class SegmentBoundary(StrictModel):
    segment_index: StrictInt = Field(ge=0)
    start_step: StrictInt = Field(ge=0)
    end_step: StrictInt = Field(gt=0)

    @model_validator(mode="after")
    def validate_order(self) -> SegmentBoundary:
        if self.end_step <= self.start_step:
            raise ValueError("segment boundary end must follow start")
        return self


class JointTraceColumn(StrictModel):
    kind: Literal["qpos", "qvel"]
    joint_name: ElementName


class TimeTraceColumn(StrictModel):
    kind: Literal["time"]


class EnergyTraceColumn(StrictModel):
    kind: Literal["energy"]
    component: Literal["potential", "kinetic"]


class ContactCountTraceColumn(StrictModel):
    kind: Literal["contact_count"]


class BodyPositionTraceColumn(StrictModel):
    kind: Literal["body_position"]
    body_name: ElementName
    axis: Literal["x", "y", "z"]


class ActuatorControlTraceColumn(StrictModel):
    kind: Literal["control"]
    actuator_name: ElementName


ExperimentTraceColumn: TypeAlias = Annotated[
    TimeTraceColumn
    | JointTraceColumn
    | EnergyTraceColumn
    | ContactCountTraceColumn
    | BodyPositionTraceColumn
    | ActuatorControlTraceColumn,
    Field(discriminator="kind"),
]

TraceValueKey: TypeAlias = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=3,
        max_length=96,
        pattern=(
            r"^(contact_count|"
            r"(?:qpos|qvel|energy|body_position|control):"
            r"[A-Za-z0-9_.:-]+)$"
        ),
    ),
]


def experiment_trace_value_key(column: ExperimentTraceColumn) -> str:
    if isinstance(column, JointTraceColumn):
        return f"{column.kind}:{column.joint_name}"
    if isinstance(column, EnergyTraceColumn):
        return f"{column.kind}:{column.component}"
    if isinstance(column, ContactCountTraceColumn):
        return column.kind
    if isinstance(column, BodyPositionTraceColumn):
        return f"{column.kind}:{column.body_name}:{column.axis}"
    if isinstance(column, ActuatorControlTraceColumn):
        return f"{column.kind}:{column.actuator_name}"
    raise ValueError("time is represented by experiment trace row time_s")


class ExperimentTraceRow(StrictModel):
    time_s: StrictFiniteFloat = Field(ge=0.0)
    values: dict[TraceValueKey, StrictFiniteFloat] = Field(
        min_length=2, max_length=192
    )


class ExperimentTrace(StrictModel):
    columns: list[ExperimentTraceColumn] = Field(min_length=3, max_length=193)
    rows: list[ExperimentTraceRow] = Field(min_length=256, max_length=256)

    @model_validator(mode="after")
    def validate_trace(self) -> ExperimentTrace:
        if not isinstance(self.columns[0], TimeTraceColumn):
            raise ValueError("the first experiment trace column must be time")
        if any(isinstance(column, TimeTraceColumn) for column in self.columns[1:]):
            raise ValueError("experiment trace must contain exactly one time column")
        if not any(isinstance(column, ActuatorControlTraceColumn) for column in self.columns):
            raise ValueError("experiment trace must contain actuator control columns")
        if not any(
            not isinstance(column, (TimeTraceColumn, ActuatorControlTraceColumn))
            for column in self.columns
        ):
            raise ValueError("experiment trace must contain selected signal columns")

        time_s = [row.time_s for row in self.rows]
        intervals = [
            current - previous
            for previous, current in zip(time_s, time_s[1:])
        ]
        if time_s[0] < 0.0 or intervals[0] <= 0.0 or any(
            interval <= 0.0
            or not math.isclose(
                interval,
                intervals[0],
                rel_tol=_METRIC_DELTA_REL_TOLERANCE,
                abs_tol=_METRIC_DELTA_ABS_TOLERANCE,
            )
            for interval in intervals[1:]
        ):
            raise ValueError("experiment trace timestamps must be uniformly sampled")

        column_keys: list[tuple[str, ...]] = []
        for column in self.columns:
            if isinstance(column, JointTraceColumn):
                column_keys.append((column.kind, column.joint_name))
            elif isinstance(column, EnergyTraceColumn):
                column_keys.append((column.kind, column.component))
            elif isinstance(column, BodyPositionTraceColumn):
                column_keys.append((column.kind, column.body_name, column.axis))
            elif isinstance(column, ActuatorControlTraceColumn):
                column_keys.append((column.kind, column.actuator_name))
            else:
                column_keys.append((column.kind,))
        if len(column_keys) != len(set(column_keys)):
            raise ValueError("experiment trace columns must be unique")

        expected_value_keys = [
            experiment_trace_value_key(column) for column in self.columns[1:]
        ]
        expected_value_key_set = set(expected_value_keys)
        if any(
            len(row.values) != len(expected_value_keys)
            or set(row.values) != expected_value_key_set
            for row in self.rows
        ):
            raise ValueError(
                "experiment trace row values must match the named columns"
            )
        return self


class FinalSnapshotMetadata(StrictModel):
    artifact_id: ArtifactId
    uri: Annotated[
        str,
        StringConstraints(
            strict=True,
            min_length=10,
            max_length=160,
            pattern=r"^autopsy://[A-Za-z0-9_./-]+$",
        ),
    ]
    sha256: AssetHash
    bytes: StrictInt = Field(ge=0)
    step: StrictInt = Field(ge=0)
    width_px: Literal[160]
    height_px: Literal[120]


class ExperimentOutcome(StrictModel):
    kind: Literal["completed", "non_finite_state"]
    budget_consumed: Literal[True]
    first_bad_step: StrictInt | None = Field(default=None, ge=0)


class RunExperimentOutput(CommonOutput):
    revision_id: RevisionId
    hypothesis_id: HypothesisId
    run_id: RunId
    asset_sha256: AssetHash
    condition_sha256: AssetHash
    execution_fingerprint_sha256: AssetHash
    trace_sha256: AssetHash | None = None
    outcome: ExperimentOutcome
    requested_steps: StrictInt = Field(ge=256, le=100_000)
    completed_steps: StrictInt = Field(ge=0, le=100_000)
    segment_boundaries: list[SegmentBoundary] = Field(min_length=1, max_length=16)
    trace: ExperimentTrace | None = None
    final_snapshot: FinalSnapshotMetadata | None = None

    @model_validator(mode="after")
    def validate_boundaries_and_trace(self) -> RunExperimentOutput:
        for index, boundary in enumerate(self.segment_boundaries):
            if boundary.segment_index != index:
                raise ValueError("segment boundary indices must be contiguous")
            expected_start = (
                0 if index == 0 else self.segment_boundaries[index - 1].end_step
            )
            if boundary.start_step != expected_start:
                raise ValueError("segment boundaries must be contiguous")
        if self.segment_boundaries[-1].end_step != self.requested_steps:
            raise ValueError("segment boundaries must cover the requested steps")
        if self.completed_steps > self.requested_steps:
            raise ValueError("completed steps cannot exceed requested steps")
        if self.outcome.kind == "completed":
            if self.completed_steps != self.requested_steps:
                raise ValueError("completed experiments must execute every requested step")
            if self.outcome.first_bad_step is not None:
                raise ValueError("completed experiments cannot report a bad step")
            if self.trace_sha256 is None or self.trace is None:
                raise ValueError("completed experiments require a finite trace and hash")
            if (
                self.final_snapshot is not None
                and self.final_snapshot.step >= self.completed_steps
            ):
                raise ValueError("final snapshot step must remain inside experiment boundaries")
        else:
            if self.outcome.first_bad_step is None:
                raise ValueError("non-finite outcomes require the first bad step")
            if self.outcome.first_bad_step != self.completed_steps:
                raise ValueError("the first bad step must follow the completed finite steps")
            if (
                self.trace_sha256 is not None
                or self.trace is not None
                or self.final_snapshot is not None
            ):
                raise ValueError("non-finite outcomes cannot expose a trace or snapshot")
        return self


class CanonicalDiffEntry(StrictModel):
    target: ElementName
    attribute: AllowedAttribute
    before: SafeText
    after: SafeText


class CreateRevisionOutput(CommonOutput):
    revision_id: RevisionId
    parent_revision_id: RevisionId
    asset_sha256: AssetHash
    canonical_diff: list[CanonicalDiffEntry] = Field(min_length=1, max_length=1)
    status: Literal["created", "already_exists"]

    @model_validator(mode="after")
    def validate_lineage(self) -> CreateRevisionOutput:
        if self.revision_id == "r000":
            raise ValueError("create revision cannot return the pre-provisioned root")
        if self.revision_id == self.parent_revision_id:
            raise ValueError("created revision cannot be its own parent")
        return self


class IntegrityChecks(StrictModel):
    original: StrictBool
    controller: StrictBool
    contract: StrictBool
    runner: StrictBool
    lineage: StrictBool


class AggregateResult(StrictModel):
    passed: StrictInt = Field(ge=0)
    total: StrictInt = Field(ge=0)
    violated_clause_ids: list[ContractClauseId] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validate_passed(self) -> AggregateResult:
        if self.passed > self.total:
            raise ValueError("passed must not exceed total")
        if len(self.violated_clause_ids) != len(set(self.violated_clause_ids)):
            raise ValueError("violated clause IDs must be unique")
        if self.total > 0 and (self.passed < self.total) != bool(self.violated_clause_ids):
            raise ValueError("aggregate counts and violated clauses must agree")
        return self


class BudgetSummary(StrictModel):
    runs_remaining: StrictInt = Field(ge=0)
    experiments_remaining: StrictInt = Field(ge=0)
    revisions_remaining: StrictInt = Field(ge=0)
    qualification_remaining: StrictInt = Field(ge=0, le=1)


class VerifyRevisionOutput(CommonOutput):
    revision_id: RevisionId
    asset_sha256: AssetHash
    integrity: IntegrityChecks
    public_result: AggregateResult
    holdout_result: AggregateResult
    promotion_ticket: PromotionTicket | None = None

    @field_validator("public_result")
    @classmethod
    def validate_public_result_count(cls, value: AggregateResult) -> AggregateResult:
        if value.total != 1:
            raise ValueError("public qualification must contain exactly one scenario")
        return value

    @field_validator("holdout_result")
    @classmethod
    def validate_holdout_result_count(cls, value: AggregateResult) -> AggregateResult:
        if value.total != 3:
            raise ValueError("holdout qualification must contain exactly three scenarios")
        return value

    @model_validator(mode="after")
    def validate_promotion_ticket(self) -> VerifyRevisionOutput:
        qualification_passed = (
            all(
                (
                    self.integrity.original,
                    self.integrity.controller,
                    self.integrity.contract,
                    self.integrity.runner,
                    self.integrity.lineage,
                )
            )
            and self.public_result.passed == 1
            and self.public_result.total == 1
            and not self.public_result.violated_clause_ids
            and self.holdout_result.passed == 3
            and self.holdout_result.total == 3
            and not self.holdout_result.violated_clause_ids
        )
        if not qualification_passed:
            if self.promotion_ticket is not None:
                raise ValueError("promotion ticket requires successful qualification")
            return self
        if self.promotion_ticket is None:
            raise ValueError("successful qualification requires a promotion ticket")

        ticket = self.promotion_ticket
        if ticket.case_id != self.case_id:
            raise ValueError("promotion ticket case must match verification case")
        if ticket.revision_id != self.revision_id:
            raise ValueError("promotion ticket revision must match verification revision")
        if ticket.asset_sha256 != self.asset_sha256:
            raise ValueError("promotion ticket asset hash must match verification asset hash")
        if (
            ticket.public_result.passed,
            ticket.public_result.total,
        ) != (
            self.public_result.passed,
            self.public_result.total,
        ):
            raise ValueError("promotion ticket public counts must match verification counts")
        if (
            ticket.holdout_result.passed,
            ticket.holdout_result.total,
        ) != (
            self.holdout_result.passed,
            self.holdout_result.total,
        ):
            raise ValueError("promotion ticket holdout counts must match verification counts")
        if ticket.public_result.violated_clause_ids != self.public_result.violated_clause_ids:
            raise ValueError("promotion ticket public clauses must match verification clauses")
        if ticket.holdout_result.violated_clause_ids != self.holdout_result.violated_clause_ids:
            raise ValueError("promotion ticket holdout clauses must match verification clauses")
        return self


class InspectAssetOutput(CommonOutput):
    revision_id: RevisionId
    asset_sha256: AssetHash
    view: Literal["authored", "compiled", "both"]
    joints: list[JointSummary] = Field(min_length=1, max_length=32)
    bodies: list[BodySummary] = Field(min_length=1, max_length=64)
    actuators: list[ActuatorSummary] = Field(min_length=1, max_length=64)
    compiled_dimensions: CompiledDimensions


TOOL_INPUT_MODELS = (
    OpenCaseInput,
    InspectAssetInput,
    RunTaskInput,
    RunExperimentInput,
    CreateRevisionInput,
    VerifyRevisionInput,
    PublishRevisionInput,
)
TOOL_OUTPUT_MODELS = (
    OpenCaseOutput,
    InspectAssetOutput,
    RunTaskOutput,
    RunExperimentOutput,
    CreateRevisionOutput,
    VerifyRevisionOutput,
)


OpenCaseOutput.model_rebuild()
InspectAssetOutput.model_rebuild()
RunTaskOutput.model_rebuild()
RunExperimentOutput.model_rebuild()
CreateRevisionOutput.model_rebuild()
VerifyRevisionOutput.model_rebuild()


__all__ = [
    "AllowedAttribute",
    "ArtifactRef",
    "AssetHash",
    "AttributePatch",
    "AxisPatch",
    "AxisVector",
    "CaseId",
    "CreateRevisionInput",
    "CreateRevisionOutput",
    "InspectAssetInput",
    "InspectAssetOutput",
    "OpenCaseInput",
    "OpenCaseOutput",
    "PublishRevisionInput",
    "RunExperimentInput",
    "RunExperimentOutput",
    "RunTaskInput",
    "RunTaskOutput",
    "ScalarPatch",
    "SCHEMA_VERSION",
    "TOOL_INPUT_MODELS",
    "TOOL_OUTPUT_MODELS",
    "VerifyRevisionInput",
    "VerifyRevisionOutput",
]
