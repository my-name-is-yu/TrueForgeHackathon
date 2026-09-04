"""Strict, persistence-friendly foundations for Character Robot Studio V2.

This module intentionally contains only the project shell.  Later V2 issues can
add typed design domains without changing the requirements, target, or
readiness contracts defined here.
"""

from __future__ import annotations

import hashlib
import json
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


PROJECT_SCHEMA_VERSION = "character-project/v2"
SPEC_SCHEMA_VERSION = "character-robot/v2"
V2_STORE_NAMESPACE = "character-robot/v2"
SAFE_TEXT_MAX_LENGTH = 4000

READINESS_DOMAINS: tuple[str, ...] = (
    "requirements",
    "visual_design",
    "component_selection",
    "mechanical_assembly",
    "spatial_layout",
    "electrical_design",
    "runtime_binding",
    "manufacturing_plan",
    "verification_plan",
    "artifact_manifest",
)
READINESS_STATES: tuple[str, ...] = ("missing", "dirty", "blocked", "checked")
_MAX_TEXT_COLLECTION_ITEMS = 128
_MAX_READINESS_OUTPUT_ITEMS = len(READINESS_DOMAINS) * _MAX_TEXT_COLLECTION_ITEMS

SafeText: TypeAlias = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=SAFE_TEXT_MAX_LENGTH,
    ),
]
ShortText: TypeAlias = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=240,
    ),
]
SafeIdentifier: TypeAlias = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=96,
        pattern=r"^[a-z][a-z0-9_-]*$",
    ),
]
Sha256: TypeAlias = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    ),
]
FiniteFloat: TypeAlias = Annotated[float, Strict(), AllowInfNan(False)]


class V2Model(BaseModel):
    """Frozen models with no silent field or type coercion."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        revalidate_instances="always",
        validate_assignment=True,
        validate_default=True,
    )


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: V2Model) -> str:
    return hashlib.sha256(
        _canonical_json_bytes(value.model_dump(mode="json"))
    ).hexdigest()


class EnvironmentConstraints(V2Model):
    description: ShortText
    indoor_only: StrictBool
    surface: ShortText | None = None


class DimensionConstraints(V2Model):
    max_height_mm: FiniteFloat | None = Field(default=None, gt=0.0, le=5000.0)
    max_width_mm: FiniteFloat | None = Field(default=None, gt=0.0, le=5000.0)
    max_depth_mm: FiniteFloat | None = Field(default=None, gt=0.0, le=5000.0)
    min_height_mm: FiniteFloat | None = Field(default=None, gt=0.0, le=5000.0)
    min_width_mm: FiniteFloat | None = Field(default=None, gt=0.0, le=5000.0)
    min_depth_mm: FiniteFloat | None = Field(default=None, gt=0.0, le=5000.0)
    description: ShortText | None = None

    @model_validator(mode="after")
    def validate_ranges(self) -> Self:
        for axis in ("height", "width", "depth"):
            minimum = getattr(self, f"min_{axis}_mm")
            maximum = getattr(self, f"max_{axis}_mm")
            if minimum is not None and maximum is not None and minimum > maximum:
                raise ValueError(f"min_{axis}_mm cannot exceed max_{axis}_mm")
        if all(
            value is None
            for value in (
                self.max_height_mm,
                self.max_width_mm,
                self.max_depth_mm,
                self.min_height_mm,
                self.min_width_mm,
                self.min_depth_mm,
                self.description,
            )
        ):
            raise ValueError("at least one dimension constraint is required")
        return self


class SpeedConstraints(V2Model):
    max_m_s: FiniteFloat | None = Field(default=None, gt=0.0, le=10.0)
    min_m_s: FiniteFloat | None = Field(default=None, ge=0.0, le=10.0)
    description: ShortText | None = None

    @model_validator(mode="after")
    def validate_ranges(self) -> Self:
        if (
            self.min_m_s is not None
            and self.max_m_s is not None
            and self.min_m_s > self.max_m_s
        ):
            raise ValueError("min_m_s cannot exceed max_m_s")
        if self.max_m_s is None and self.min_m_s is None and self.description is None:
            raise ValueError("at least one speed constraint is required")
        return self


class VoltageConstraints(V2Model):
    nominal_v: FiniteFloat | None = Field(default=None, gt=0.0, le=1000.0)
    min_v: FiniteFloat | None = Field(default=None, gt=0.0, le=1000.0)
    max_v: FiniteFloat | None = Field(default=None, gt=0.0, le=1000.0)
    description: ShortText | None = None

    @model_validator(mode="after")
    def validate_ranges(self) -> Self:
        if (
            self.min_v is not None
            and self.max_v is not None
            and self.min_v > self.max_v
        ):
            raise ValueError("min_v cannot exceed max_v")
        if self.nominal_v is None and self.min_v is None and self.max_v is None:
            if self.description is None:
                raise ValueError("at least one voltage constraint is required")
        elif (
            self.nominal_v is not None
            and self.min_v is not None
            and self.nominal_v < self.min_v
        ):
            raise ValueError("nominal_v cannot be below min_v")
        elif (
            self.nominal_v is not None
            and self.max_v is not None
            and self.nominal_v > self.max_v
        ):
            raise ValueError("nominal_v cannot exceed max_v")
        return self


def _tuple_of_text(
    value: tuple[str, ...], *, max_items: int = _MAX_TEXT_COLLECTION_ITEMS
) -> tuple[str, ...]:
    if len(value) > max_items:
        raise ValueError(f"text collection cannot contain more than {max_items} values")
    if len(set(value)) != len(value):
        raise ValueError("text collection values must be unique")
    return value


class Requirements(V2Model):
    """The immutable user goal and its explicitly separated interpretation."""

    original_request: SafeText
    environment: EnvironmentConstraints
    dimensions: DimensionConstraints
    speed: SpeedConstraints
    voltage: VoltageConstraints
    required_behavior: tuple[SafeText, ...] = Field(
        default=(),
    )
    safety_constraints: tuple[SafeText, ...] = Field(default=())
    user_must_haves: tuple[SafeText, ...] = Field(
        default=(),
    )
    assumptions: tuple[SafeText, ...] = Field(default=())
    unresolved_questions: tuple[SafeText, ...] = Field(default=())

    @field_validator(
        "required_behavior",
        "safety_constraints",
        "user_must_haves",
        "assumptions",
        "unresolved_questions",
    )
    @classmethod
    def validate_text_collections(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _tuple_of_text(value)


DomainName: TypeAlias = Literal[
    "requirements",
    "visual_design",
    "component_selection",
    "mechanical_assembly",
    "spatial_layout",
    "electrical_design",
    "runtime_binding",
    "manufacturing_plan",
    "verification_plan",
    "artifact_manifest",
]


class DomainRecord(V2Model):
    """Small shell record used to reserve a future domain's durable identity.

    A shell can identify content, blockers, and unknowns, but cannot assert that
    its content has passed a trusted check.  Future typed domain adapters own
    that server-side transition while retaining these stable identity fields.
    """

    domain_id: DomainName
    entity_ids: tuple[SafeIdentifier, ...] = Field(default=())
    content_digest: Sha256 | None = None
    blockers: tuple[SafeText, ...] = Field(default=())
    unknowns: tuple[SafeText, ...] = Field(default=())
    evidence_ids: tuple[SafeIdentifier, ...] = Field(default=())

    @field_validator("entity_ids", "blockers", "unknowns", "evidence_ids")
    @classmethod
    def validate_collections(cls, value: tuple[object, ...]) -> tuple[object, ...]:
        return _tuple_of_text(value)  # type: ignore[arg-type]


class RobotSystemSpec(V2Model):
    """Strict V2 system shell; typed design domains arrive in later issues."""

    schema_version: Literal[SPEC_SCHEMA_VERSION] = SPEC_SCHEMA_VERSION
    project_id: SafeIdentifier = "studio"
    requirements: Requirements
    domains: tuple[DomainRecord, ...] = Field(default=())
    committed_head_id: SafeIdentifier | None = None
    committed_head_digest: Sha256 | None = None
    active_draft_id: SafeIdentifier | None = None
    active_draft_digest: Sha256 | None = None

    @field_validator("domains")
    @classmethod
    def validate_domain_ids(
        cls, value: tuple[DomainRecord, ...]
    ) -> tuple[DomainRecord, ...]:
        identifiers = [record.domain_id for record in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("domain IDs must be unique")
        return value

    @model_validator(mode="after")
    def validate_head_pairs(self) -> Self:
        if (self.committed_head_id is None) != (self.committed_head_digest is None):
            raise ValueError("committed head ID and digest must be provided together")
        if (self.active_draft_id is None) != (self.active_draft_digest is None):
            raise ValueError("active draft ID and digest must be provided together")
        return self

    @property
    def requirements_hash(self) -> str:
        return canonical_sha256(self.requirements)


class DomainReadiness(V2Model):
    domain_id: DomainName
    state: Literal["missing", "dirty", "blocked", "checked"]
    blockers: tuple[SafeText, ...] = Field(default=())
    unknowns: tuple[SafeText, ...] = Field(default=())

    @field_validator("blockers", "unknowns")
    @classmethod
    def validate_text_collections(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _tuple_of_text(value)


class ReadinessMatrix(V2Model):
    """Server-derived, complete readiness for every required domain."""

    domains: tuple[DomainReadiness, ...]
    design_complete: StrictBool
    datasheet_checked: StrictBool
    physical_verification_pending: Literal[True] = True

    @model_validator(mode="after")
    def validate_complete_domain_set(self) -> Self:
        actual = [domain.domain_id for domain in self.domains]
        if len(actual) != len(set(actual)):
            raise ValueError("readiness domains must be unique")
        if tuple(actual) != READINESS_DOMAINS:
            raise ValueError("readiness matrix must contain every required domain once")
        if self.design_complete and any(
            domain.state != "checked" for domain in self.domains
        ):
            raise ValueError("design_complete requires every domain to be checked")
        return self

    def for_domain(self, domain_id: str) -> DomainReadiness:
        for domain in self.domains:
            if domain.domain_id == domain_id:
                return domain
        raise KeyError(domain_id)


def derive_readiness(spec: RobotSystemSpec) -> ReadinessMatrix:
    """Derive readiness exclusively from the current requirements and shell.

    No readiness claim is read from ``RobotSystemSpec``.  The requirements
    domain is checked only when it has no unresolved questions; an unresolved
    user question is a blocker rather than an invented assumption.
    """

    records = {record.domain_id: record for record in spec.domains}
    derived: list[DomainReadiness] = []
    for domain_id in READINESS_DOMAINS:
        if domain_id == "requirements":
            blockers = tuple(spec.requirements.unresolved_questions)
            state = "blocked" if blockers else "checked"
            derived.append(
                DomainReadiness(
                    domain_id=domain_id,
                    state=state,
                    blockers=blockers,
                    unknowns=tuple(spec.requirements.assumptions),
                )
            )
            continue
        record = records.get(domain_id)
        if record is None:
            derived.append(DomainReadiness(domain_id=domain_id, state="missing"))
            continue
        if record.blockers:
            state = "blocked"
        elif record.content_digest is None:
            state = "missing"
        elif record.unknowns:
            state = "blocked"
        else:
            # The shell has no trusted check adapter yet.  A digest proves
            # identity only; it cannot be promoted to a readiness claim by a
            # model-supplied field or write payload.
            state = "dirty"
        derived.append(
            DomainReadiness(
                domain_id=domain_id,
                state=state,
                blockers=tuple(record.blockers),
                unknowns=tuple(record.unknowns),
            )
        )
    states = {domain.domain_id: domain.state for domain in derived}
    return ReadinessMatrix(
        domains=tuple(derived),
        design_complete=all(state == "checked" for state in states.values()),
        datasheet_checked=states["component_selection"] == "checked",
        physical_verification_pending=True,
    )


class V2ProjectSnapshot(V2Model):
    schema_version: Literal[PROJECT_SCHEMA_VERSION] = PROJECT_SCHEMA_VERSION
    project_id: SafeIdentifier
    generation: StrictInt = Field(ge=0)
    spec: RobotSystemSpec

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.spec.project_id != self.project_id:
            raise ValueError("project_id must match the RobotSystemSpec project_id")
        return self

    @property
    def requirements_hash(self) -> str:
        return self.spec.requirements_hash

    @property
    def active_target_token(self) -> str:
        return calculate_active_target_token(
            schema_version=self.schema_version,
            project_id=self.project_id,
            project_generation=self.generation,
            requirements_hash=self.requirements_hash,
            committed_head_id=self.spec.committed_head_id,
            committed_head_digest=self.spec.committed_head_digest,
            active_draft_id=self.spec.active_draft_id,
            active_draft_digest=self.spec.active_draft_digest,
        )

    @property
    def readiness(self) -> ReadinessMatrix:
        return derive_readiness(self.spec)


class WriteResult(V2Model):
    state: V2ProjectSnapshot
    changed_entities: tuple[SafeIdentifier, ...] = Field(default=())
    invalidated_domains: tuple[DomainName, ...] = Field(default=())
    invalidated_artifacts: tuple[SafeIdentifier, ...] = Field(default=())
    invalidated_evidence: tuple[SafeIdentifier, ...] = Field(default=())
    blockers: tuple[SafeText, ...] = Field(default=())
    next_actions: tuple[SafeText, ...] = Field(default=())

    @field_validator("blockers", "next_actions")
    @classmethod
    def validate_output_collections(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _tuple_of_text(value, max_items=_MAX_READINESS_OUTPUT_ITEMS)

    next_target_token: Sha256

    @model_validator(mode="after")
    def validate_next_token(self) -> Self:
        if self.next_target_token != self.state.active_target_token:
            raise ValueError("next_target_token must match returned state")
        return self

    @property
    def active_target_token(self) -> str:
        return self.next_target_token


def calculate_active_target_token(
    *,
    schema_version: str = PROJECT_SCHEMA_VERSION,
    project_id: str,
    project_generation: int,
    requirements_hash: str,
    committed_head_id: str | None = None,
    committed_head_digest: str | None = None,
    active_draft_id: str | None = None,
    active_draft_digest: str | None = None,
) -> str:
    """Return a deterministic SHA-256 token over every concurrency identity."""

    if (
        not isinstance(schema_version, str)
        or not isinstance(project_id, str)
        or not isinstance(project_generation, int)
        or isinstance(project_generation, bool)
        or not isinstance(requirements_hash, str)
    ):
        raise TypeError("target token fields have invalid types")
    if project_generation < 0:
        raise ValueError("project_generation must be non-negative")
    payload = {
        "schema_version": schema_version,
        "project_id": project_id,
        "project_generation": project_generation,
        "requirements_hash": requirements_hash,
        "committed_head_id": committed_head_id,
        "committed_head_digest": committed_head_digest,
        "active_draft_id": active_draft_id,
        "active_draft_digest": active_draft_digest,
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


__all__ = [
    "DimensionConstraints",
    "DomainName",
    "DomainReadiness",
    "DomainRecord",
    "EnvironmentConstraints",
    "FiniteFloat",
    "PROJECT_SCHEMA_VERSION",
    "READINESS_DOMAINS",
    "READINESS_STATES",
    "Requirements",
    "ReadinessMatrix",
    "RobotSystemSpec",
    "SAFE_TEXT_MAX_LENGTH",
    "SafeIdentifier",
    "SafeText",
    "Sha256",
    "SPEC_SCHEMA_VERSION",
    "SpeedConstraints",
    "V2Model",
    "V2ProjectSnapshot",
    "V2_STORE_NAMESPACE",
    "VoltageConstraints",
    "WriteResult",
    "calculate_active_target_token",
    "canonical_sha256",
    "derive_readiness",
]
