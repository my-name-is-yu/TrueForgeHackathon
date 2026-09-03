from __future__ import annotations

import math
from typing import Annotated, Literal, Self, TypeAlias

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


SCHEMA_VERSION = "character-robot/v1"

FiniteFloat: TypeAlias = Annotated[float, Strict(), AllowInfNan(False)]
SafeText: TypeAlias = Annotated[
    str,
    StringConstraints(
        strict=True, strip_whitespace=True, min_length=1, max_length=2000
    ),
]
ShortText: TypeAlias = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=120),
]
ArtifactFileName: TypeAlias = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    ),
]
SafeIdentifier: TypeAlias = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_-]*$",
    ),
]
ProfileId: TypeAlias = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=5,
        max_length=96,
        pattern=r"^[a-z0-9][a-z0-9-]*/v[1-9][0-9]*$",
    ),
]
RevisionId: TypeAlias = Annotated[
    str,
    StringConstraints(
        strict=True, min_length=4, max_length=16, pattern=r"^r[0-9]{3,}$"
    ),
]
Sha256: TypeAlias = Annotated[
    str,
    StringConstraints(
        strict=True, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    ),
]
ColorHex: TypeAlias = Annotated[
    str,
    StringConstraints(
        strict=True, min_length=7, max_length=7, pattern=r"^#[0-9A-Fa-f]{6}$"
    ),
]

EvidenceLevel: TypeAlias = Literal[
    "concept_only",
    "digital_checks_passed",
    "within_qualified_profile",
    "exact_build_verified",
]
QualificationState: TypeAlias = Literal[
    "digital_only", "profile_qualified", "exact_build_verified"
]
ScenarioId: TypeAlias = Literal["idle", "greet", "listen", "think", "delight", "sleep"]
ExpressionId: TypeAlias = Literal[
    "neutral",
    "happy",
    "listening",
    "thinking",
    "delighted",
    "sleepy",
    "concerned",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        validate_assignment=True,
    )


class Vec2(StrictModel):
    x: FiniteFloat = Field(ge=-500.0, le=500.0)
    y: FiniteFloat = Field(ge=-500.0, le=500.0)


class Vec3(StrictModel):
    x: FiniteFloat = Field(ge=-500.0, le=500.0)
    y: FiniteFloat = Field(ge=-500.0, le=500.0)
    z: FiniteFloat = Field(ge=-500.0, le=500.0)


class PositiveVec3(StrictModel):
    x: FiniteFloat = Field(gt=0.0, le=500.0)
    y: FiniteFloat = Field(gt=0.0, le=500.0)
    z: FiniteFloat = Field(gt=0.0, le=500.0)


class RotationDegrees(StrictModel):
    x: FiniteFloat = Field(ge=-360.0, le=360.0, default=0.0)
    y: FiniteFloat = Field(ge=-360.0, le=360.0, default=0.0)
    z: FiniteFloat = Field(ge=-360.0, le=360.0, default=0.0)


class AttachmentSpec(StrictModel):
    parent_node_id: SafeIdentifier
    parent_anchor: SafeIdentifier
    translation_mm: Vec3 = Field(default_factory=lambda: Vec3(x=0.0, y=0.0, z=0.0))
    rotation_deg: RotationDegrees = Field(default_factory=RotationDegrees)


MorphologyRole: TypeAlias = Literal[
    "chassis_shell",
    "head_shell",
    "face_bezel",
    "beak",
    "ear",
    "wing",
    "arm",
    "tail",
    "ornament",
    "wheel_cover",
    "neck_cover",
    "sensor_cover",
    "internal_mount",
]


class MorphologyNodeBase(StrictModel):
    node_id: SafeIdentifier
    role: MorphologyRole
    label: ShortText
    attachment: AttachmentSpec | None = None
    visible: StrictBool = True


class RoundedSolidNode(MorphologyNodeBase):
    kind: Literal["rounded_solid"]
    size_mm: PositiveVec3
    corner_radius_mm: FiniteFloat = Field(ge=0.0, le=100.0)

    @model_validator(mode="after")
    def validate_corner_radius(self) -> Self:
        maximum = min(self.size_mm.x, self.size_mm.y, self.size_mm.z) / 2.0
        if self.corner_radius_mm > maximum:
            raise ValueError("corner_radius_mm exceeds half the shortest side")
        return self


class RevolveNode(MorphologyNodeBase):
    kind: Literal["revolve"]
    profile_points_mm: tuple[Vec2, ...] = Field(min_length=3, max_length=24)
    angle_deg: FiniteFloat = Field(ge=30.0, le=360.0, default=360.0)


class LoftSection(StrictModel):
    z_mm: FiniteFloat = Field(ge=-250.0, le=250.0)
    radius_x_mm: FiniteFloat = Field(gt=0.0, le=250.0)
    radius_y_mm: FiniteFloat = Field(gt=0.0, le=250.0)


class LoftNode(MorphologyNodeBase):
    kind: Literal["loft"]
    sections: tuple[LoftSection, ...] = Field(min_length=2, max_length=8)

    @field_validator("sections")
    @classmethod
    def require_ordered_sections(
        cls, value: tuple[LoftSection, ...]
    ) -> tuple[LoftSection, ...]:
        positions = [section.z_mm for section in value]
        if positions != sorted(positions) or len(positions) != len(set(positions)):
            raise ValueError("loft sections must have unique ascending z_mm values")
        return value


class SweepNode(MorphologyNodeBase):
    kind: Literal["sweep"]
    path_points_mm: tuple[Vec3, ...] = Field(min_length=2, max_length=16)
    radius_mm: FiniteFloat = Field(gt=0.0, le=100.0)

    @field_validator("path_points_mm")
    @classmethod
    def require_nonzero_path(cls, value: tuple[Vec3, ...]) -> tuple[Vec3, ...]:
        for left, right in zip(value, value[1:]):
            distance = math.sqrt(
                (left.x - right.x) ** 2
                + (left.y - right.y) ** 2
                + (left.z - right.z) ** 2
            )
            if distance <= 1e-9:
                raise ValueError("sweep path contains a zero-length segment")
        return value


class CsgNode(MorphologyNodeBase):
    kind: Literal["csg"]
    operation: Literal["union", "subtract", "intersect"]
    operand_node_ids: tuple[SafeIdentifier, ...] = Field(min_length=2, max_length=8)

    @field_validator("operand_node_ids")
    @classmethod
    def require_unique_operands(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("CSG operands must be unique")
        return value


class MirrorNode(MorphologyNodeBase):
    kind: Literal["mirror"]
    source_node_id: SafeIdentifier
    plane: Literal["x", "y", "z"]
    offset_mm: FiniteFloat = Field(ge=-250.0, le=250.0, default=0.0)


MorphologyNode: TypeAlias = Annotated[
    RoundedSolidNode | RevolveNode | LoftNode | SweepNode | CsgNode | MirrorNode,
    Field(discriminator="kind"),
]


class MorphologyGraph(StrictModel):
    nodes: tuple[MorphologyNode, ...] = Field(min_length=1, max_length=48)

    @model_validator(mode="after")
    def validate_graph(self) -> Self:
        by_id = {node.node_id: node for node in self.nodes}
        if len(by_id) != len(self.nodes):
            raise ValueError("morphology node IDs must be unique")
        if not any(node.role == "chassis_shell" for node in self.nodes):
            raise ValueError("morphology requires a chassis_shell node")

        dependencies: dict[str, set[str]] = {}
        for node in self.nodes:
            current: set[str] = set()
            if node.attachment is not None:
                current.add(node.attachment.parent_node_id)
            if isinstance(node, CsgNode):
                current.update(node.operand_node_ids)
            elif isinstance(node, MirrorNode):
                current.add(node.source_node_id)
            missing = sorted(current.difference(by_id))
            if missing:
                raise ValueError(
                    f"node {node.node_id} references unknown nodes: {', '.join(missing)}"
                )
            if node.node_id in current:
                raise ValueError(f"node {node.node_id} cannot reference itself")
            dependencies[node.node_id] = current

        depths: dict[str, int] = {}
        active: set[str] = set()

        def dependency_depth(node_id: str) -> int:
            if node_id in depths:
                return depths[node_id]
            if node_id in active:
                raise ValueError("morphology dependencies contain a cycle")
            active.add(node_id)
            depth = 1
            for dependency in dependencies[node_id]:
                depth = max(depth, dependency_depth(dependency) + 1)
                if depth > 8:
                    raise ValueError("morphology dependency depth exceeds 8")
            active.remove(node_id)
            depths[node_id] = depth
            return depth

        for node_id in by_id:
            dependency_depth(node_id)
        return self


class RobotIdentity(StrictModel):
    name: ShortText
    role: ShortText
    motif: ShortText
    design_brief: SafeText


class AppearanceSpec(StrictModel):
    primary_color: ColorHex
    secondary_color: ColorHex
    accent_color: ColorHex
    eye_color: ColorHex = "#111111"
    finish: Literal["matte", "satin", "gloss"] = "matte"
    style_tags: tuple[SafeIdentifier, ...] = Field(default_factory=tuple, max_length=12)

    @field_validator("style_tags")
    @classmethod
    def require_unique_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("style tags must be unique")
        return value


class PersonalitySpec(StrictModel):
    curiosity: FiniteFloat = Field(ge=0.0, le=1.0)
    boldness: FiniteFloat = Field(ge=0.0, le=1.0)
    energy: FiniteFloat = Field(ge=0.0, le=1.0)
    sociability: FiniteFloat = Field(ge=0.0, le=1.0)
    voice_style: Literal["gentle", "playful", "bright", "calm", "shy"]
    motion_style: Literal["careful", "bouncy", "smooth", "curious", "sleepy"]


class FaceSpec(StrictModel):
    default_expression: ExpressionId = "neutral"
    supported_expressions: tuple[ExpressionId, ...] = Field(min_length=1, max_length=7)

    @field_validator("supported_expressions")
    @classmethod
    def require_unique_expressions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("supported expressions must be unique")
        return value

    @model_validator(mode="after")
    def require_default_expression(self) -> Self:
        if self.default_expression not in self.supported_expressions:
            raise ValueError("default expression must be supported")
        return self


class BehaviorKeyframe(StrictModel):
    at_ms: StrictInt = Field(ge=0, le=120_000)
    face_expression: ExpressionId
    wheel_left: FiniteFloat = Field(ge=-1.0, le=1.0, default=0.0)
    wheel_right: FiniteFloat = Field(ge=-1.0, le=1.0, default=0.0)
    head_pan_deg: FiniteFloat = Field(ge=-90.0, le=90.0, default=0.0)
    head_tilt_deg: FiniteFloat = Field(ge=-45.0, le=45.0, default=0.0)
    sound_cue: SafeIdentifier | None = None


class BehaviorScenario(StrictModel):
    scenario_id: ScenarioId
    duration_ms: StrictInt = Field(ge=100, le=120_000)
    keyframes: tuple[BehaviorKeyframe, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_timeline(self) -> Self:
        times = [frame.at_ms for frame in self.keyframes]
        if times[0] != 0:
            raise ValueError("scenario timeline must start at 0 ms")
        if times != sorted(times) or len(times) != len(set(times)):
            raise ValueError("scenario keyframe times must be unique and ascending")
        if times[-1] > self.duration_ms:
            raise ValueError("scenario keyframe exceeds duration_ms")
        return self


class BehaviorGraph(StrictModel):
    scenarios: tuple[BehaviorScenario, ...] = Field(min_length=1, max_length=6)

    @field_validator("scenarios")
    @classmethod
    def require_unique_scenarios(
        cls, value: tuple[BehaviorScenario, ...]
    ) -> tuple[BehaviorScenario, ...]:
        identifiers = [scenario.scenario_id for scenario in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("behavior scenario IDs must be unique")
        return value


class ManufacturingSpec(StrictModel):
    material: Literal["pla", "petg"] = "pla"
    nozzle_diameter_mm: FiniteFloat = Field(ge=0.2, le=1.0, default=0.4)
    layer_height_mm: FiniteFloat = Field(ge=0.08, le=0.4, default=0.2)
    minimum_wall_mm: FiniteFloat = Field(ge=0.8, le=6.0, default=1.6)
    fit_clearance_mm: FiniteFloat = Field(ge=0.1, le=1.5, default=0.3)
    printer_volume_mm: PositiveVec3


class RobotConstraints(StrictModel):
    maximum_dimensions_mm: PositiveVec3
    maximum_mass_g: FiniteFloat = Field(gt=0.0, le=5000.0)
    maximum_speed_m_s: FiniteFloat = Field(gt=0.0, le=1.0, default=0.35)
    indoor_only: Literal[True] = True
    low_voltage_only: Literal[True] = True


class CompilerVersions(StrictModel):
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    compiler: Literal["character-cad-v1"] = "character-cad-v1"
    catalog: Literal["hardware-catalog-v1"] = "hardware-catalog-v1"
    firmware_runtime: Literal["character-runtime-v1"] = "character-runtime-v1"


class CharacterRobotSpec(StrictModel):
    identity: RobotIdentity
    hardware_profile_id: ProfileId
    appearance: AppearanceSpec
    morphology: MorphologyGraph
    personality: PersonalitySpec
    face: FaceSpec
    behavior: BehaviorGraph
    manufacturing: ManufacturingSpec
    constraints: RobotConstraints
    versions: CompilerVersions

    @model_validator(mode="after")
    def require_behavior_expressions(self) -> Self:
        supported = set(self.face.supported_expressions)
        missing = sorted(
            {
                frame.face_expression
                for scenario in self.behavior.scenarios
                for frame in scenario.keyframes
            }.difference(supported)
        )
        if missing:
            raise ValueError(
                "behavior uses unsupported expressions: " + ", ".join(missing)
            )
        return self


class AddMorphologyNodeEdit(StrictModel):
    kind: Literal["add_morphology_node"]
    node: MorphologyNode


class ReplaceMorphologyNodeEdit(StrictModel):
    kind: Literal["replace_morphology_node"]
    node_id: SafeIdentifier
    expected_node_hash: Sha256
    node: MorphologyNode

    @model_validator(mode="after")
    def preserve_node_identity(self) -> Self:
        if self.node.node_id != self.node_id:
            raise ValueError("replacement node must preserve node_id")
        return self


class RemoveMorphologyNodeEdit(StrictModel):
    kind: Literal["remove_morphology_node"]
    node_id: SafeIdentifier
    expected_node_hash: Sha256


class SetHardwareProfileEdit(StrictModel):
    kind: Literal["set_hardware_profile"]
    hardware_profile_id: ProfileId


class SetIdentityEdit(StrictModel):
    kind: Literal["set_identity"]
    identity: RobotIdentity


class SetAppearanceEdit(StrictModel):
    kind: Literal["set_appearance"]
    appearance: AppearanceSpec


class SetPersonalityEdit(StrictModel):
    kind: Literal["set_personality"]
    personality: PersonalitySpec


class SetFaceEdit(StrictModel):
    kind: Literal["set_face"]
    face: FaceSpec


class SetBehaviorEdit(StrictModel):
    kind: Literal["set_behavior"]
    behavior: BehaviorGraph


class SetManufacturingEdit(StrictModel):
    kind: Literal["set_manufacturing"]
    manufacturing: ManufacturingSpec


class SetConstraintsEdit(StrictModel):
    kind: Literal["set_constraints"]
    constraints: RobotConstraints


SemanticEdit: TypeAlias = Annotated[
    AddMorphologyNodeEdit
    | ReplaceMorphologyNodeEdit
    | RemoveMorphologyNodeEdit
    | SetHardwareProfileEdit
    | SetIdentityEdit
    | SetAppearanceEdit
    | SetPersonalityEdit
    | SetFaceEdit
    | SetBehaviorEdit
    | SetManufacturingEdit
    | SetConstraintsEdit,
    Field(discriminator="kind"),
]


class ProfileSummary(StrictModel):
    profile_id: ProfileId
    display_name: ShortText
    qualification: QualificationState
    controller: ShortText
    minimum_enclosure_mm: PositiveVec3
    component_count: StrictInt = Field(ge=1, le=128)
    capabilities: list[SafeText] = Field(default_factory=list, max_length=32)
    unknowns: list[SafeText] = Field(default_factory=list, max_length=32)


class NodeSummary(StrictModel):
    node_id: SafeIdentifier
    role: MorphologyRole
    label: ShortText
    kind: Literal["rounded_solid", "revolve", "loft", "sweep", "csg", "mirror"]
    parent_node_id: SafeIdentifier | None
    node_hash: Sha256


class ValidationIssue(StrictModel):
    code: SafeIdentifier
    severity: Literal["info", "warning", "error"]
    path: Annotated[
        str,
        StringConstraints(
            strict=True, min_length=1, max_length=240, pattern=r"^[A-Za-z0-9_.\[\]-]+$"
        ),
    ]
    message: SafeText
    measured_value: FiniteFloat | None = None
    limit_value: FiniteFloat | None = None
    suggestion: SafeText | None = None


class ValidationReport(StrictModel):
    spec_hash: Sha256
    evidence_level: EvidenceLevel
    passed: StrictBool
    dimensions_mm: PositiveVec3 | None = None
    issues: list[ValidationIssue] = Field(default_factory=list, max_length=128)
    report_hash: Sha256


ArtifactKind: TypeAlias = Literal[
    "glb",
    "step",
    "stl",
    "3mf",
    "spec_json",
    "bom_json",
    "wiring_json",
    "firmware_config_json",
    "assembly_markdown",
    "validation_json",
    "runtime_bundle_zip",
    "calibration_json",
    "physical_evidence_json",
    "mjcf",
    "simulation_json",
    "project_snapshot_json",
    "build_pack_zip",
]


class ArtifactDescriptor(StrictModel):
    kind: ArtifactKind
    file_name: ArtifactFileName
    media_type: ShortText
    sha256: Sha256
    byte_size: StrictInt = Field(ge=0, le=64 * 1024 * 1024)
    experimental: StrictBool


class ArtifactManifest(StrictModel):
    revision_id: RevisionId
    spec_hash: Sha256
    build_subject_hash: Sha256
    geometry_sha256: Sha256
    profile_id: ProfileId
    profile_sha256: Sha256
    catalog_version: SafeIdentifier
    compiler_version: SafeIdentifier
    cad_engine_version: ShortText | None = None
    simulation_engine_version: ShortText | None = None
    firmware_runtime_version: SafeIdentifier
    evidence_level: EvidenceLevel
    artifacts: list[ArtifactDescriptor] = Field(min_length=1, max_length=32)
    manifest_hash: Sha256
    download_requires_human_action: Literal[True] = True


class RevisionSummary(StrictModel):
    revision_id: RevisionId
    parent_revision_id: RevisionId | None
    ordinal: StrictInt = Field(ge=0)
    spec_hash: Sha256
    note: SafeText
    created_at: SafeText


class StudioRunSummary(StrictModel):
    run_id: SafeIdentifier
    kind: Literal["compile", "simulation", "validation", "build_pack"]
    spec_hash: Sha256
    profile_id: ProfileId
    catalog_version: SafeIdentifier
    compiler_version: SafeIdentifier
    cad_engine_version: ShortText | None = None
    simulation_engine_version: ShortText | None = None
    firmware_runtime_version: SafeIdentifier
    duration_ms: FiniteFloat = Field(ge=0.0)
    cache_hit: StrictBool
    warning_codes: list[SafeIdentifier] = Field(default_factory=list, max_length=128)
    error_codes: list[SafeIdentifier] = Field(default_factory=list, max_length=128)


class ToolOutput(StrictModel):
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    request_id: Annotated[
        str,
        StringConstraints(
            strict=True, min_length=5, max_length=96, pattern=r"^req_[a-f0-9]{32}$"
        ),
    ]


class GetStudioContextInput(StrictModel):
    include_revision_history: StrictBool = True


class DraftSnapshot(StrictModel):
    base_revision_id: RevisionId | None
    draft_hash: Sha256
    spec_hash: Sha256
    spec: CharacterRobotSpec
    preview_artifact: ArtifactDescriptor | None


class GetStudioContextOutput(ToolOutput):
    project_id: SafeIdentifier
    project_generation: StrictInt = Field(ge=0)
    storage_mode: Literal["ephemeral", "durable"]
    artifact_manifest_count: StrictInt = Field(ge=0, le=512)
    tool_names: list[SafeIdentifier] = Field(min_length=8, max_length=8)
    head_revision_id: RevisionId | None
    head_spec_sha256: Sha256 | None
    current_spec: CharacterRobotSpec | None
    current_preview_artifact: ArtifactDescriptor | None
    draft: DraftSnapshot | None
    hardware_profiles: list[ProfileSummary]
    supported_scenarios: list[ScenarioId]
    maximum_morphology_nodes: Literal[48] = 48
    revision_history: list[RevisionSummary]
    latest_validation: ValidationReport | None
    recent_runs: list[StudioRunSummary] = Field(default_factory=list, max_length=64)
    selected_node_id: SafeIdentifier | None = None
    evidence_policy: dict[EvidenceLevel, SafeText]


class SetDesignDraftInput(StrictModel):
    expected_revision: RevisionId | None
    expected_draft_hash: Sha256 | None = None
    spec: CharacterRobotSpec


class DraftOutput(ToolOutput):
    base_revision_id: RevisionId | None
    draft_hash: Sha256
    spec_hash: Sha256
    spec: CharacterRobotSpec
    preview_artifact: ArtifactDescriptor | None
    changed_node_ids: list[SafeIdentifier]
    changed_sections: list[SafeIdentifier]
    warnings: list[SafeText] = Field(default_factory=list)


class SetDesignDraftOutput(DraftOutput):
    pass


class ReviseDesignDraftInput(StrictModel):
    draft_hash: Sha256
    edits: list[SemanticEdit] = Field(min_length=1, max_length=16)


class ReviseDesignDraftOutput(DraftOutput):
    pass


class DraftTarget(StrictModel):
    kind: Literal["draft"]
    draft_hash: Sha256


class RevisionTarget(StrictModel):
    kind: Literal["revision"]
    revision_id: RevisionId


DesignTarget: TypeAlias = Annotated[
    DraftTarget | RevisionTarget, Field(discriminator="kind")
]


class StudioSelectionInput(StrictModel):
    target: DesignTarget
    node_id: SafeIdentifier | None


class InspectDesignInput(StrictModel):
    target: DesignTarget


class InspectDesignOutput(ToolOutput):
    target: DesignTarget
    spec_hash: Sha256
    spec: CharacterRobotSpec
    nodes: list[NodeSummary]
    dimensions_mm: PositiveVec3 | None
    geometry_sha256: Sha256 | None
    warnings: list[SafeText] = Field(default_factory=list)


class PreviewScenarioInput(StrictModel):
    target: DesignTarget
    scenario_id: ScenarioId


class PreviewScenarioOutput(ToolOutput):
    target: DesignTarget
    spec_hash: Sha256
    scenario_id: ScenarioId
    duration_ms: StrictInt
    keyframes: list[BehaviorKeyframe]
    preview_artifact: ArtifactDescriptor | None
    evidence_level: Literal["concept_only"] = "concept_only"
    warnings: list[SafeText] = Field(default_factory=list)


class ValidateDesignInput(StrictModel):
    target: DesignTarget


class ValidateDesignOutput(ToolOutput):
    target: DesignTarget
    report: ValidationReport


class CreateRevisionFromDraftInput(StrictModel):
    expected_revision: RevisionId | None
    draft_hash: Sha256
    note: SafeText


class CreateRevisionFromDraftOutput(ToolOutput):
    revision: RevisionSummary
    head_revision_id: RevisionId
    draft_hash: Sha256


class PrepareBuildPackInput(StrictModel):
    revision_id: RevisionId
    expected_spec_hash: Sha256


class PrepareBuildPackOutput(ToolOutput):
    status: Literal["blocked", "experimental_ready", "ready"]
    manifest: ArtifactManifest | None
    blockers: list[ValidationIssue] = Field(default_factory=list)
    human_action_required: Literal[True] = True
    next_action: SafeText


TOOL_NAMES = (
    "get_studio_context",
    "set_design_draft",
    "revise_design_draft",
    "inspect_design",
    "preview_scenario",
    "validate_design",
    "create_revision_from_draft",
    "prepare_build_pack",
)

TOOL_INPUT_MODELS = (
    GetStudioContextInput,
    SetDesignDraftInput,
    ReviseDesignDraftInput,
    InspectDesignInput,
    PreviewScenarioInput,
    ValidateDesignInput,
    CreateRevisionFromDraftInput,
    PrepareBuildPackInput,
)

TOOL_OUTPUT_MODELS = (
    GetStudioContextOutput,
    SetDesignDraftOutput,
    ReviseDesignDraftOutput,
    InspectDesignOutput,
    PreviewScenarioOutput,
    ValidateDesignOutput,
    CreateRevisionFromDraftOutput,
    PrepareBuildPackOutput,
)


__all__ = [
    "SCHEMA_VERSION",
    "AddMorphologyNodeEdit",
    "AppearanceSpec",
    "ArtifactDescriptor",
    "ArtifactManifest",
    "BehaviorGraph",
    "BehaviorKeyframe",
    "BehaviorScenario",
    "CharacterRobotSpec",
    "CompilerVersions",
    "CreateRevisionFromDraftInput",
    "CreateRevisionFromDraftOutput",
    "CsgNode",
    "DesignTarget",
    "DraftSnapshot",
    "DraftTarget",
    "FaceSpec",
    "GetStudioContextInput",
    "GetStudioContextOutput",
    "InspectDesignInput",
    "InspectDesignOutput",
    "LoftNode",
    "LoftSection",
    "ManufacturingSpec",
    "MirrorNode",
    "MorphologyGraph",
    "MorphologyNode",
    "PersonalitySpec",
    "PositiveVec3",
    "PrepareBuildPackInput",
    "PrepareBuildPackOutput",
    "PreviewScenarioInput",
    "PreviewScenarioOutput",
    "ProfileSummary",
    "RemoveMorphologyNodeEdit",
    "ReplaceMorphologyNodeEdit",
    "RevisionTarget",
    "RevisionSummary",
    "RevolveNode",
    "RobotConstraints",
    "RobotIdentity",
    "RotationDegrees",
    "RoundedSolidNode",
    "SemanticEdit",
    "SetAppearanceEdit",
    "SetBehaviorEdit",
    "SetConstraintsEdit",
    "SetDesignDraftInput",
    "SetDesignDraftOutput",
    "SetFaceEdit",
    "SetHardwareProfileEdit",
    "SetIdentityEdit",
    "SetManufacturingEdit",
    "SetPersonalityEdit",
    "Sha256",
    "StudioSelectionInput",
    "SweepNode",
    "TOOL_INPUT_MODELS",
    "TOOL_NAMES",
    "TOOL_OUTPUT_MODELS",
    "ValidateDesignInput",
    "ValidateDesignOutput",
    "ValidationIssue",
    "ValidationReport",
    "Vec2",
    "Vec3",
]
