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

AllowedAttribute: TypeAlias = Literal["axis", "damping", "armature", "frictionloss"]
HypothesisAttribute: TypeAlias = Literal[
    "axis", "damping", "armature", "frictionloss", "joint"
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


class Prediction(StrictModel):
    rationale: SafeText
    all_of: list[Predicate] = Field(min_length=1, max_length=16)


class Falsifier(StrictModel):
    rationale: SafeText
    any_of: list[Predicate] = Field(min_length=1, max_length=16)


class Hypothesis(StrictModel):
    claim: SafeText
    suspected_elements: list[ElementReference] = Field(min_length=1, max_length=8)
    competing_explanation: CompetingExplanation
    prediction: Prediction
    falsifier: Falsifier


class JointPulseProbe(StrictModel):
    kind: Literal["joint_pulse"]
    joint_name: ElementName
    direction: StrictInt
    amplitude_rad: StrictFiniteFloat
    duration_s: StrictFiniteFloat
    observe_body: ElementName

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, value: int) -> int:
        if value not in (-1, 1):
            raise ValueError("direction must be -1 or 1")
        return value

    @field_validator("amplitude_rad")
    @classmethod
    def validate_amplitude(cls, value: float) -> float:
        if not 0.0 < value <= 1.0:
            raise ValueError("amplitude_rad is outside the safety range")
        return value

    @field_validator("duration_s")
    @classmethod
    def validate_duration(cls, value: float) -> float:
        if not 0.0 < value <= 10.0:
            raise ValueError("duration_s is outside the safety range")
        return value


class PoseHoldProbe(StrictModel):
    kind: Literal["pose_hold"]


Probe: TypeAlias = Annotated[JointPulseProbe | PoseHoldProbe, Field(discriminator="kind")]
CaptureMode: TypeAlias = Literal["metrics_and_filmstrip", "analysis_trace"]


class ExpectedEffect(StrictModel):
    scenario_id: ElementName
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
    scenario_id: ElementName
    capture: Literal["metrics", "metrics_and_filmstrip"]


class RunProbeInput(StrictModel):
    case_id: CaseId
    revision_id: RevisionId
    hypothesis: Hypothesis
    probe: Probe
    capture: Literal["analysis_trace"]


class CreateRevisionInput(StrictModel):
    case_id: CaseId
    base_revision_id: RevisionId
    expected_base_sha256: AssetHash
    basis_hypothesis_id: HypothesisId
    basis_probe_run_id: RunId
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
    canonical_diff: list["CanonicalDiffEntry"] = Field(min_length=1, max_length=1)
    public_result: "AggregateResult"
    holdout_result: "AggregateResult"
    export_name: Annotated[
        str,
        StringConstraints(strict=True, min_length=1, max_length=96, pattern=r"^[a-z0-9][a-z0-9-]*$"),
    ]
    qualified_core_sha256: AssetHash
    ticket_digest: AssetHash


class PublishRevisionInput(StrictModel):
    case_id: CaseId
    promotion_ticket: PromotionTicket


class ArtifactRef(StrictModel):
    artifact_id: ArtifactId
    kind: Literal["trace_json", "filmstrip", "repaired_mjcf", "patch_manifest", "qualification"]
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


class BodySummary(StrictModel):
    name: ElementName
    parent: ElementName | None = None


class ActuatorSummary(StrictModel):
    name: ElementName
    joint_name: ElementName


class ScenarioSummary(StrictModel):
    scenario_id: ElementName
    observable_metrics: list[MetricName] = Field(min_length=1, max_length=32)


class ContractClause(StrictModel):
    clause_id: ElementName
    description: SafeText


class RevisionSummary(StrictModel):
    revision_id: RevisionId
    asset_sha256: AssetHash
    parent_revision_id: RevisionId | None = None
    canonical_diff: list["CanonicalDiffEntry"] = Field(default_factory=list, max_length=1)


class PublicEventSummary(StrictModel):
    event_id: EventId
    kind: Literal[
        "CASE_OPENED",
        "TASK_COMPLETED",
        "PROBE_COMPLETED",
        "PROBE_FAILED",
        "REVISION_CREATED",
        "REVISION_REJECTED",
        "QUALIFICATION_PASSED",
        "QUALIFICATION_FAILED",
        "PROMOTED",
    ]
    summary: SafeText


class OpenCaseOutput(CommonOutput):
    promotion_state: Literal["open", "promoted"]
    qualification_state: Literal["unused", "running", "recovering", "passed", "failed"]
    original_revision_id: RevisionId
    original_asset_sha256: AssetHash
    public_scenarios: list[ScenarioSummary] = Field(min_length=1, max_length=16)
    contract_clauses: list[ContractClause] = Field(min_length=1, max_length=32)
    compiled_dimensions: CompiledDimensions
    joints: list[JointSummary] = Field(min_length=1, max_length=32)
    bodies: list[BodySummary] = Field(min_length=1, max_length=64)
    actuators: list[ActuatorSummary] = Field(min_length=1, max_length=64)
    available_probe_kinds: tuple[Literal["joint_pulse", "pose_hold"], ...] = Field(
        min_length=1, max_length=2
    )
    observable_metric_names: list[MetricName] = Field(min_length=1, max_length=64)
    patch_policy: PatchPolicy
    remaining_budgets: "BudgetSummary"
    revision_history: list[RevisionSummary] = Field(min_length=1, max_length=32)
    event_tail: list[PublicEventSummary] = Field(default_factory=list, max_length=20)


class MetricObservation(StrictModel):
    metric: MetricName
    value: StrictFiniteFloat


class TracePoint(StrictModel):
    time_s: StrictFiniteFloat = Field(ge=0.0)
    values: tuple[StrictFiniteFloat, ...] = Field(min_length=1, max_length=64)


class BehaviorDiff(StrictModel):
    changed: StrictBool
    metrics: list[MetricObservation] = Field(default_factory=list, max_length=64)


class RunTaskOutput(CommonOutput):
    revision_id: RevisionId
    scenario_id: ElementName
    result: Literal["pass", "fail"]
    observations: list[MetricObservation] = Field(min_length=1, max_length=64)
    trace: list[TracePoint] = Field(default_factory=list, max_length=51)
    behavior_diff: BehaviorDiff | None = None


class ProbeObservation(StrictModel):
    metric: MetricName
    value: StrictFiniteFloat


class RunProbeOutput(CommonOutput):
    revision_id: RevisionId
    run_id: RunId
    prediction_matched: StrictBool
    falsifier_triggered: StrictBool
    inconclusive: StrictBool
    conflicting: StrictBool
    observations: list[ProbeObservation] = Field(min_length=1, max_length=128)
    trace: list[TracePoint] = Field(default_factory=list, max_length=256)


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


class IntegrityChecks(StrictModel):
    original: StrictBool
    controller: StrictBool
    contract: StrictBool
    runner: StrictBool
    lineage: StrictBool


class AggregateResult(StrictModel):
    passed: StrictInt = Field(ge=0)
    total: StrictInt = Field(ge=0)
    violated_clause_ids: list[ElementName] = Field(default_factory=list, max_length=32)

    @field_validator("passed")
    @classmethod
    def validate_passed(cls, value: int, info) -> int:
        total = info.data.get("total")
        if total is not None and value > total:
            raise ValueError("passed must not exceed total")
        return value


class BudgetSummary(StrictModel):
    runs_remaining: StrictInt = Field(ge=0)
    probes_remaining: StrictInt = Field(ge=0)
    revisions_remaining: StrictInt = Field(ge=0)
    qualification_remaining: StrictInt = Field(ge=0, le=1)


class VerifyRevisionOutput(CommonOutput):
    revision_id: RevisionId
    asset_sha256: AssetHash
    integrity: IntegrityChecks
    public_result: AggregateResult
    holdout_result: AggregateResult
    promotion_ticket: PromotionTicket | None = None


class PublishRevisionOutput(CommonOutput):
    revision_id: RevisionId
    status: Literal["published", "already_published"]


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
    RunProbeInput,
    CreateRevisionInput,
    VerifyRevisionInput,
    PublishRevisionInput,
)
TOOL_OUTPUT_MODELS = (
    OpenCaseOutput,
    InspectAssetOutput,
    RunTaskOutput,
    RunProbeOutput,
    CreateRevisionOutput,
    VerifyRevisionOutput,
    PublishRevisionOutput,
)


OpenCaseOutput.model_rebuild()
InspectAssetOutput.model_rebuild()
RunTaskOutput.model_rebuild()
RunProbeOutput.model_rebuild()
CreateRevisionOutput.model_rebuild()
VerifyRevisionOutput.model_rebuild()
PublishRevisionOutput.model_rebuild()


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
    "PublishRevisionOutput",
    "RunProbeInput",
    "RunProbeOutput",
    "RunTaskInput",
    "RunTaskOutput",
    "ScalarPatch",
    "SCHEMA_VERSION",
    "TOOL_INPUT_MODELS",
    "TOOL_OUTPUT_MODELS",
    "VerifyRevisionInput",
    "VerifyRevisionOutput",
]
