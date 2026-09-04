"""Machine-readable reference stack for the V2 character robot.

The stack is intentionally a design candidate, not a physical qualification
record.  It composes the provenance-aware catalog from :mod:`catalog` without
changing its eligibility rules.  Unknown and conflicting facts remain visible
on the component entries, while planning assumptions and physical gates are
separate records that downstream architecture work must carry forward.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Literal, Self, TypeAlias

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator

from .catalog import (
    CATALOG_SCHEMA_VERSION,
    CORES3_K128,
    CatalogEntry,
    CatalogFact,
    CatalogIdentity,
    CatalogSnapshot,
    CatalogSource,
    CatalogUse,
    ConversionRule,
    EligibilityAssessment,
    EligibilityReason,
    EvidenceRef,
    FactBasis,
    FactKey,
    NumericClaim,
    OFFICIAL_CATALOG_V2,
    POLOLU_CASTER_950,
    POLOLU_WHEEL_1087,
    TextClaim,
    Unit,
    assess_eligibility,
)
from .contract import FiniteFloat, SafeIdentifier, SafeText, Sha256, V2Model


REFERENCE_STACK_SCHEMA_VERSION = "character-reference-stack/v1"
REFERENCE_STACK_CATALOG_VERSION = "reference-stack-catalog-20260904"

SourceDigestKind: TypeAlias = Literal[
    "retrieved_bytes",
    "dynamic_html_observation",
]
RelayContact: TypeAlias = Literal["A", "B"]
RelayVoltageType: TypeAlias = Literal["AC", "DC"]
StackRole: TypeAlias = Literal[
    "controller",
    "drive_motor",
    "wheel_hub",
    "head_actuator",
    "head_horn",
    "caster",
    "battery",
    "charger",
    "regulator",
    "actuator_interface",
    "motor_driver",
    "fuse",
    "fuse_holder",
    "main_switch",
    "force_guided_relay",
    "physical_estop",
    "battery_connector_housing",
    "battery_connector_contact",
    "actuator_cable",
    "wire",
    "fastener",
    "insert",
    "spacer",
]
BranchKind: TypeAlias = Literal["controller", "actuator_drive", "actuator_head"]
IndependenceBasis: TypeAlias = Literal[
    "manufacturer_stated", "planning_assumption", "unknown"
]
GateStage: TypeAlias = Literal["digital", "physical"]
GateTarget: TypeAlias = Literal["datasheet_eligible", "physically_qualified"]
CalculationUnit: TypeAlias = Literal["A", "ratio", "m/s", "g"]
CalculationBasis: TypeAlias = Literal["published_values_only", "planning_assumption"]
CalculationOperation: TypeAlias = Literal[
    "sum_quantity_weighted",
    "ratio",
    "wheel_speed",
]
VoltageFactKey: TypeAlias = Literal[
    "operating_voltage_nominal_v",
    "operating_voltage_max_v",
]
SelectionEvidenceBasis: TypeAlias = Literal[
    "manufacturer_stated",
    "manufacturer_stated_with_conversions",
    "mixed_known_and_unknown",
    "conflict_preserved",
]


REQUIRED_STACK_ROLES: tuple[StackRole, ...] = (
    "controller",
    "drive_motor",
    "wheel_hub",
    "head_actuator",
    "head_horn",
    "caster",
    "battery",
    "charger",
    "regulator",
    "actuator_interface",
    "motor_driver",
    "fuse",
    "fuse_holder",
    "main_switch",
    "force_guided_relay",
    "physical_estop",
    "battery_connector_housing",
    "battery_connector_contact",
    "actuator_cable",
    "wire",
    "fastener",
    "insert",
    "spacer",
)
_OFF_TOPOLOGY_ROLES = frozenset({"charger", "regulator"})
_TOPOLOGY_PATH_ROLES = frozenset(REQUIRED_STACK_ROLES) - _OFF_TOPOLOGY_ROLES
_CURRENT_CALCULATION_FACT_KEYS = frozenset(
    {
        "current_continuous_a",
        "current_peak_a",
        "current_stall_a",
        "current_limit_a",
        "rail_current_limit_a",
        "contact_rating_a",
    }
)


class SourceObservation(V2Model):
    """How the source bytes behind a catalog digest were observed."""

    source_id: SafeIdentifier
    digest_kind: SourceDigestKind
    note: SafeText
    usable_for_claims: StrictBool = True

    @model_validator(mode="after")
    def validate_digest_note(self) -> Self:
        lower_note = self.note.lower()
        if (
            self.digest_kind == "dynamic_html_observation"
            and "dynamic" not in lower_note
        ):
            raise ValueError(
                "dynamic HTML observations must say that the digest is dynamic"
            )
        if self.digest_kind == "retrieved_bytes" and "sha-256" not in lower_note:
            raise ValueError("retrieved-byte observations must describe the SHA-256")
        if not self.usable_for_claims and "unavailable" not in lower_note:
            raise ValueError(
                "unusable source observations must say that product evidence is unavailable"
            )
        return self


class RelayRatingEvidence(V2Model):
    """Purpose-qualified relay ratings, kept out of generic voltage facts."""

    relay_entry_id: SafeIdentifier
    source_refs: tuple[EvidenceRef, ...] = Field(min_length=1)
    contact_current_a: FiniteFloat = Field(gt=0.0)
    contact_voltage_v: FiniteFloat = Field(gt=0.0)
    contact_voltage_type: RelayVoltageType
    coil_voltage_v: FiniteFloat = Field(gt=0.0)
    coil_voltage_type: RelayVoltageType
    intended_12vdc_electronic_contact_current_a: Literal[None] = None
    intended_12vdc_electronic_contact_rating_status: Literal["unknown"] = "unknown"
    note: SafeText

    @field_validator("source_refs")
    @classmethod
    def require_unique_source_refs(
        cls, value: tuple[EvidenceRef, ...]
    ) -> tuple[EvidenceRef, ...]:
        source_ids = tuple(reference.source_id for reference in value)
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("relay evidence source references must be unique")
        if source_ids != tuple(sorted(source_ids)):
            raise ValueError("relay evidence source references must be canonical")
        return value


class VoltageCompatibilityGuard(V2Model):
    """A typed blocker for an over-voltage source-to-load path."""

    guard_id: SafeIdentifier
    source_selection_id: SafeIdentifier
    source_entry_id: SafeIdentifier
    source_voltage_fact_key: VoltageFactKey
    source_upper_bound_v: FiniteFloat = Field(gt=0.0)
    load_selection_id: SafeIdentifier
    load_entry_id: SafeIdentifier
    load_voltage_fact_key: Literal["operating_voltage_max_v"]
    load_upper_bound_v: FiniteFloat = Field(gt=0.0)
    compatible: Literal[False] = False
    note: SafeText

    @model_validator(mode="after")
    def validate_incompatibility_shape(self) -> Self:
        if self.source_upper_bound_v <= self.load_upper_bound_v:
            raise ValueError(
                "voltage compatibility guard must represent source over-voltage"
            )
        if self.compatible:
            raise ValueError("over-voltage guard cannot be marked compatible")
        return self


class StackSelection(V2Model):
    """One exact catalog identity selected for a stack role."""

    selection_id: SafeIdentifier
    role: StackRole
    entry_id: SafeIdentifier
    manufacturer: SafeText
    manufacturer_sku: SafeText
    variant: SafeText
    catalog_use: CatalogUse
    quantity: StrictInt = Field(gt=0, le=100)
    package_scope: SafeText
    evidence_source_ids: tuple[SafeIdentifier, ...] = Field(min_length=1)
    evidence_basis: tuple[SelectionEvidenceBasis, ...] = Field(min_length=1)
    # False is reserved for off-robot charger/fallback records.  Every entry
    # that can energize or carry the controller/actuator topology stays active.
    active: StrictBool = True
    planning_assumption_ids: tuple[SafeIdentifier, ...] = ()

    @field_validator("evidence_source_ids", "evidence_basis", "planning_assumption_ids")
    @classmethod
    def require_unique_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("stack selection collections must be unique")
        return value


class StackFactRef(V2Model):
    """Reference to a catalog fact used by a stack calculation."""

    entry_id: SafeIdentifier
    fact_key: FactKey


class StackCalculation(V2Model):
    """A published-value calculation kept separate from source claims."""

    calculation_id: SafeIdentifier
    operation: CalculationOperation
    expression: SafeText
    value: FiniteFloat
    unit: CalculationUnit
    basis: CalculationBasis
    inputs: tuple[StackFactRef, ...] = Field(min_length=1)
    input_selection_ids: tuple[SafeIdentifier, ...] = Field(min_length=1)
    note: SafeText
    blocking: StrictBool = False

    @field_validator("inputs")
    @classmethod
    def require_unique_inputs(
        cls, value: tuple[StackFactRef, ...]
    ) -> tuple[StackFactRef, ...]:
        identifiers = [(item.entry_id, item.fact_key) for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("calculation inputs must be unique")
        return value

    @field_validator("input_selection_ids")
    @classmethod
    def require_unique_selection_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("calculation selection inputs must be unique")
        return value

    @model_validator(mode="after")
    def validate_operation_shape(self) -> Self:
        if len(self.input_selection_ids) != len(self.inputs):
            raise ValueError(
                "calculation input_selection_ids must align one-to-one with inputs"
            )
        expected_unit = {
            "sum_quantity_weighted": "A",
            "ratio": "ratio",
            "wheel_speed": "m/s",
        }[self.operation]
        if self.unit != expected_unit:
            raise ValueError(
                f"{self.operation} calculations must use unit {expected_unit}"
            )
        if self.operation == "ratio" and len(self.inputs) < 2:
            raise ValueError("ratio calculations need a numerator and denominator")
        if self.operation == "wheel_speed" and len(self.inputs) != 2:
            raise ValueError("wheel_speed calculations need diameter and rpm inputs")
        return self


class PlanningAssumption(V2Model):
    """An explicit design choice that is not promoted to a catalog fact."""

    assumption_id: SafeIdentifier
    statement: SafeText
    basis: Literal["design_choice", "source_gap"]
    status: Literal["accepted_for_planning", "open"]
    related_selection_ids: tuple[SafeIdentifier, ...] = ()

    @field_validator("related_selection_ids")
    @classmethod
    def require_unique_selection_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("planning assumption references must be unique")
        return value


class UnresolvedGate(V2Model):
    """A stable unresolved digital or physical qualification gate."""

    gate_id: SafeIdentifier
    stage: GateStage
    target: GateTarget
    description: SafeText
    blocking: StrictBool = True
    related_selection_ids: tuple[SafeIdentifier, ...] = ()
    related_fact_keys: tuple[FactKey, ...] = ()

    @field_validator("related_selection_ids", "related_fact_keys")
    @classmethod
    def require_unique_references(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("unresolved gate references must be unique")
        return value


class PowerBranch(V2Model):
    """One typed source-to-load energy branch."""

    branch_id: SafeIdentifier
    kind: BranchKind
    source_entry_id: SafeIdentifier
    source_description: SafeText
    energy_path_entry_ids: tuple[SafeIdentifier, ...] = Field(min_length=1)
    relay_contact: RelayContact | None = None
    opens_on_estop: StrictBool
    controller_survives_estop: StrictBool
    independence_basis: IndependenceBasis
    planning_assumption_ids: tuple[SafeIdentifier, ...] = ()

    @field_validator("energy_path_entry_ids", "planning_assumption_ids")
    @classmethod
    def require_unique_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("power branch IDs must be unique")
        return value

    @model_validator(mode="after")
    def validate_branch_safety_shape(self) -> Self:
        if self.kind == "controller":
            if self.opens_on_estop:
                raise ValueError("controller branch must remain powered on E-stop")
            if self.relay_contact is not None:
                raise ValueError(
                    "controller branch must not use actuator relay contact"
                )
        else:
            if not self.opens_on_estop:
                raise ValueError("actuator branches must open on E-stop")
            if self.relay_contact is None:
                raise ValueError("actuator branches must name a relay contact")
        return self


class PowerTopology(V2Model):
    """Controller branch plus independent drive/head actuator branches."""

    controller_branch_id: SafeIdentifier
    actuator_branch_ids: tuple[SafeIdentifier, ...] = Field(min_length=2, max_length=2)
    branches: tuple[PowerBranch, ...] = Field(min_length=3, max_length=3)
    estop_selection_id: SafeIdentifier
    relay_selection_id: SafeIdentifier
    relay_rating_evidence: RelayRatingEvidence
    voltage_compatibility_guard: VoltageCompatibilityGuard
    contacts_are_not_parallel: StrictBool
    controller_branch_independence: IndependenceBasis
    estop_control_path: SafeText
    planning_assumption_ids: tuple[SafeIdentifier, ...] = ()

    @model_validator(mode="after")
    def validate_topology(self) -> Self:
        branch_by_id = {branch.branch_id: branch for branch in self.branches}
        if len(branch_by_id) != len(self.branches):
            raise ValueError("power branch IDs must be unique")
        expected_branch_order = (
            self.controller_branch_id,
            *self.actuator_branch_ids,
        )
        if tuple(branch.branch_id for branch in self.branches) != expected_branch_order:
            raise ValueError(
                "power branches must use canonical controller/actuator order"
            )
        if self.actuator_branch_ids != tuple(sorted(self.actuator_branch_ids)):
            raise ValueError("actuator branch IDs must be canonical")
        if set(branch_by_id) != {
            self.controller_branch_id,
            *self.actuator_branch_ids,
        }:
            raise ValueError("topology branch IDs do not match controller/actuator IDs")
        if branch_by_id[self.controller_branch_id].kind != "controller":
            raise ValueError("controller_branch_id must identify the controller branch")
        actuator_kinds = {
            branch_by_id[branch_id].kind for branch_id in self.actuator_branch_ids
        }
        if actuator_kinds != {"actuator_drive", "actuator_head"}:
            raise ValueError("topology must have exactly one drive and one head branch")
        for branch_id in self.actuator_branch_ids:
            if branch_by_id[branch_id].kind == "controller":
                raise ValueError("actuator branch IDs must not include controller")
        if any(not branch.controller_survives_estop for branch in self.branches):
            raise ValueError("every branch must keep the controller alive on E-stop")
        controller_branch = branch_by_id[self.controller_branch_id]
        if self.controller_branch_independence != controller_branch.independence_basis:
            raise ValueError(
                "controller_branch_independence must match the controller branch"
            )
        contacts = [
            branch_by_id[branch_id].relay_contact
            for branch_id in self.actuator_branch_ids
        ]
        if set(contacts) != {"A", "B"}:
            raise ValueError("actuator branches must use relay contacts A and B once")
        if not self.contacts_are_not_parallel:
            raise ValueError("stack cannot declare relay contacts in parallel")
        if (
            self.relay_rating_evidence.intended_12vdc_electronic_contact_current_a
            is not None
        ):
            raise ValueError(
                "12 VDC electronic actuator-contact applicability must remain unknown"
            )
        return self


class StackConstraints(V2Model):
    """Robot-level constraints that still require physical verification."""

    indoor_only: StrictBool = True
    max_overall_dimension_mm: FiniteFloat = Field(gt=0.0, le=5000.0)
    max_speed_m_s: FiniteFloat = Field(gt=0.0, le=10.0)
    note: SafeText


class ReferenceStackReadiness(V2Model):
    """Derived status; callers cannot set an eligible flag on the snapshot."""

    stack_definition_complete: StrictBool
    datasheet_candidate: StrictBool
    datasheet_eligible: StrictBool
    datasheet_checked: StrictBool
    physically_qualified: StrictBool = False
    physical_verification_pending: Literal[True] = True
    blocking_codes: tuple[SafeText, ...] = ()

    @model_validator(mode="after")
    def validate_status_separation(self) -> Self:
        if self.datasheet_eligible and not self.datasheet_checked:
            raise ValueError("datasheet eligibility requires datasheet_checked")
        if self.physically_qualified:
            raise ValueError("physical qualification is out of scope for this snapshot")
        return self


class ReferenceStackSnapshot(V2Model):
    """The complete digital reference-stack snapshot."""

    schema_version: Literal[REFERENCE_STACK_SCHEMA_VERSION] = (
        REFERENCE_STACK_SCHEMA_VERSION
    )
    stack_id: SafeIdentifier
    catalog_schema_version: Literal[CATALOG_SCHEMA_VERSION] = CATALOG_SCHEMA_VERSION
    catalog: CatalogSnapshot
    catalog_digest: Sha256 | None = None
    selections: tuple[StackSelection, ...] = Field(min_length=1)
    topology: PowerTopology
    constraints: StackConstraints
    planning_assumptions: tuple[PlanningAssumption, ...] = ()
    unresolved_gates: tuple[UnresolvedGate, ...] = ()
    calculations: tuple[StackCalculation, ...] = ()
    source_observations: tuple[SourceObservation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        expected_catalog_digest = self.catalog.content_digest
        if self.catalog_digest is None:
            object.__setattr__(self, "catalog_digest", expected_catalog_digest)
        elif self.catalog_digest != expected_catalog_digest:
            raise ValueError("reference stack catalog_digest does not match catalog")

        entries = {entry.entry_id: entry for entry in self.catalog.entries}
        selections = {
            selection.selection_id: selection for selection in self.selections
        }
        if len(selections) != len(self.selections):
            raise ValueError("stack selection IDs must be unique")
        selected_roles = [selection.role for selection in self.selections]
        if len(selected_roles) != len(set(selected_roles)):
            raise ValueError("each required stack role must be selected once")
        if set(selected_roles) != set(REQUIRED_STACK_ROLES):
            missing = sorted(set(REQUIRED_STACK_ROLES) - set(selected_roles))
            extra = sorted(set(selected_roles) - set(REQUIRED_STACK_ROLES))
            raise ValueError(
                f"stack roles do not match required roles; missing={missing}, extra={extra}"
            )
        if tuple(selected_roles) != REQUIRED_STACK_ROLES:
            raise ValueError("stack selections must use canonical role order")
        for selection in self.selections:
            if not selection.active and selection.role not in _OFF_TOPOLOGY_ROLES:
                raise ValueError(
                    "only off-topology charger/regulator selections may be inactive"
                )
        if any(
            not selection.active
            for selection in self.selections
            if selection.role in _TOPOLOGY_PATH_ROLES
        ):
            raise ValueError("all topology-required selections must be active")

        source_by_id = {source.source_id: source for source in self.catalog.sources}
        observation_by_id = {
            observation.source_id: observation
            for observation in self.source_observations
        }
        if len(observation_by_id) != len(self.source_observations):
            raise ValueError("source observation IDs must be unique")
        if set(observation_by_id) != set(source_by_id):
            raise ValueError("every catalog source needs one digest observation")
        canonical_source_ids = tuple(sorted(source_by_id))
        if tuple(source_by_id) != canonical_source_ids:
            raise ValueError("catalog sources must use canonical source order")
        if tuple(observation_by_id) != canonical_source_ids:
            raise ValueError("source observations must use canonical source order")
        for source_id, source in source_by_id.items():
            observation = observation_by_id[source_id]
            expected_digest_kind = (
                "dynamic_html_observation"
                if source.media_type == "text/html"
                else "retrieved_bytes"
            )
            if observation.digest_kind != expected_digest_kind:
                raise ValueError(
                    f"source observation digest kind does not match media type: {source_id}"
                )

        assumption_ids = {
            assumption.assumption_id for assumption in self.planning_assumptions
        }
        if len(assumption_ids) != len(self.planning_assumptions):
            raise ValueError("planning assumption IDs must be unique")
        gate_ids = {gate.gate_id for gate in self.unresolved_gates}
        if len(gate_ids) != len(self.unresolved_gates):
            raise ValueError("unresolved gate IDs must be unique")
        if tuple(
            assumption.assumption_id for assumption in self.planning_assumptions
        ) != tuple(sorted(assumption_ids)):
            raise ValueError("planning assumptions must use canonical order")
        if tuple(gate.gate_id for gate in self.unresolved_gates) != tuple(
            sorted(gate_ids)
        ):
            raise ValueError("unresolved gates must use canonical order")
        calculation_ids = tuple(
            calculation.calculation_id for calculation in self.calculations
        )
        if len(calculation_ids) != len(set(calculation_ids)):
            raise ValueError("calculation IDs must be unique")
        if calculation_ids != tuple(sorted(calculation_ids)):
            raise ValueError("calculations must use canonical order")

        def check_usable_source(source_id: str) -> None:
            if not observation_by_id[source_id].usable_for_claims:
                raise ValueError(
                    f"source evidence is unavailable for claims: {source_id}"
                )

        for entry in entries.values():
            for fact in entry.facts:
                # An unavailable response may remain attached to an explicit
                # unknown_evidence record: it proves that the source was
                # reviewed for the missing fact, not that it supports a claim.
                for claim in fact.claims:
                    if claim.evidence is not None:
                        check_usable_source(claim.evidence.source_id)

        for selection in self.selections:
            entry = entries.get(selection.entry_id)
            if entry is None:
                raise ValueError(
                    f"selection references unknown catalog entry: {selection.entry_id}"
                )
            if (
                selection.manufacturer,
                selection.manufacturer_sku,
                selection.variant,
            ) != entry.identity:
                raise ValueError("selection identity must match its catalog entry")
            if not set(selection.evidence_source_ids) <= set(source_by_id):
                raise ValueError("selection references unknown evidence source")
            for source_id in selection.evidence_source_ids:
                check_usable_source(source_id)
            entry_source_ids = {
                reference.source_id
                for fact in entry.facts
                for claim in fact.claims
                for reference in (claim.evidence,)
                if reference is not None
            }
            entry_source_ids.update(
                reference.source_id
                for fact in entry.facts
                for reference in fact.unknown_evidence
            )
            if not set(selection.evidence_source_ids) <= entry_source_ids:
                raise ValueError(
                    "selection evidence source is not attached to its entry"
                )
            if not set(selection.planning_assumption_ids) <= assumption_ids:
                raise ValueError("selection references unknown planning assumption")

        for assumption in self.planning_assumptions:
            if not set(assumption.related_selection_ids) <= set(selections):
                raise ValueError("planning assumption references unknown selection")
        for gate in self.unresolved_gates:
            if not set(gate.related_selection_ids) <= set(selections):
                raise ValueError("unresolved gate references unknown selection")

        topology_entry_ids = {
            entry_id
            for branch in self.topology.branches
            for entry_id in branch.energy_path_entry_ids
        }
        topology_entry_ids.update(
            branch.source_entry_id for branch in self.topology.branches
        )
        if not topology_entry_ids <= set(entries):
            raise ValueError("power topology references unknown catalog entry")
        if self.topology.estop_selection_id not in selections:
            raise ValueError("topology references unknown E-stop selection")
        if self.topology.relay_selection_id not in selections:
            raise ValueError("topology references unknown relay selection")
        role_to_selection = {selection.role: selection for selection in self.selections}
        if (
            self.topology.estop_selection_id
            != role_to_selection["physical_estop"].selection_id
        ):
            raise ValueError("topology E-stop must use the physical_estop selection")
        relay_selection = role_to_selection["force_guided_relay"]
        if self.topology.relay_selection_id != relay_selection.selection_id:
            raise ValueError("topology relay must use the force_guided_relay selection")
        if (
            self.topology.relay_rating_evidence.relay_entry_id
            != relay_selection.entry_id
        ):
            raise ValueError("relay rating evidence must identify the selected relay")
        for reference in self.topology.relay_rating_evidence.source_refs:
            source = source_by_id.get(reference.source_id)
            if source is None:
                raise ValueError("relay rating evidence references unknown source")
            if (
                reference.source_url,
                reference.document_sha256,
                reference.evidence_date,
            ) != (source.url, source.document_sha256, source.evidence_date):
                raise ValueError("relay rating evidence source metadata does not match")
            check_usable_source(reference.source_id)

        voltage_guard = self.topology.voltage_compatibility_guard
        if voltage_guard.guard_id not in gate_ids:
            raise ValueError("voltage compatibility guard must have a matching gate")
        voltage_gate = next(
            gate
            for gate in self.unresolved_gates
            if gate.gate_id == voltage_guard.guard_id
        )
        if (
            voltage_gate.stage != "digital"
            or voltage_gate.target != "datasheet_eligible"
            or not voltage_gate.blocking
        ):
            raise ValueError(
                "voltage compatibility gate must block datasheet eligibility"
            )
        charger_selection = role_to_selection["charger"]
        head_selection = role_to_selection["head_actuator"]
        if (
            voltage_guard.source_selection_id != charger_selection.selection_id
            or voltage_guard.source_entry_id != charger_selection.entry_id
            or voltage_guard.load_selection_id != head_selection.selection_id
            or voltage_guard.load_entry_id != head_selection.entry_id
        ):
            raise ValueError(
                "voltage compatibility guard must bind selected charger and head actuator"
            )
        if not {
            charger_selection.selection_id,
            head_selection.selection_id,
        } <= set(voltage_gate.related_selection_ids):
            raise ValueError(
                "voltage compatibility gate must name its source and load selections"
            )
        if not {
            voltage_guard.source_voltage_fact_key,
            voltage_guard.load_voltage_fact_key,
        } <= set(voltage_gate.related_fact_keys):
            raise ValueError(
                "voltage compatibility gate must name its source and load facts"
            )
        source_voltage = entries[voltage_guard.source_entry_id].numeric(
            voltage_guard.source_voltage_fact_key
        )
        load_voltage = entries[voltage_guard.load_entry_id].numeric(
            voltage_guard.load_voltage_fact_key
        )
        if source_voltage is None or load_voltage is None:
            raise ValueError("voltage compatibility guard needs known voltage bounds")
        if not math.isclose(
            voltage_guard.source_upper_bound_v,
            source_voltage,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError("voltage guard source bound does not match catalog fact")
        if not math.isclose(
            voltage_guard.load_upper_bound_v,
            load_voltage,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError("voltage guard load bound does not match catalog fact")
        if source_voltage <= load_voltage:
            raise ValueError("voltage guard source bound must exceed load maximum")

        expected_paths = {
            "controller": (role_to_selection["controller"].entry_id,),
            "actuator_drive": tuple(
                role_to_selection[role].entry_id
                for role in (
                    "battery",
                    "battery_connector_housing",
                    "battery_connector_contact",
                    "wire",
                    "fuse",
                    "fuse_holder",
                    "main_switch",
                    "force_guided_relay",
                    "motor_driver",
                    "drive_motor",
                )
            ),
            "actuator_head": tuple(
                role_to_selection[role].entry_id
                for role in (
                    "battery",
                    "battery_connector_housing",
                    "battery_connector_contact",
                    "wire",
                    "fuse",
                    "fuse_holder",
                    "main_switch",
                    "force_guided_relay",
                    "actuator_interface",
                    "head_actuator",
                )
            ),
        }
        for branch in self.topology.branches:
            expected_source = role_to_selection[
                "controller" if branch.kind == "controller" else "battery"
            ]
            if branch.source_entry_id != expected_source.entry_id:
                raise ValueError("power branch source must match its selected role")
            if branch.energy_path_entry_ids != expected_paths[branch.kind]:
                raise ValueError(
                    "power branch energy path must match selected role entries"
                )
            branch_roles = (
                ("controller",)
                if branch.kind == "controller"
                else (
                    "battery",
                    "battery_connector_housing",
                    "battery_connector_contact",
                    "wire",
                    "fuse",
                    "fuse_holder",
                    "main_switch",
                    "force_guided_relay",
                    "motor_driver"
                    if branch.kind == "actuator_drive"
                    else "actuator_interface",
                    "drive_motor"
                    if branch.kind == "actuator_drive"
                    else "head_actuator",
                )
            )
            if any(not role_to_selection[role].active for role in branch_roles):
                raise ValueError("power branch cannot use inactive selections")
        if {
            branch.relay_contact
            for branch in self.topology.branches
            if branch.kind == "actuator_drive"
        } != {"A"} or {
            branch.relay_contact
            for branch in self.topology.branches
            if branch.kind == "actuator_head"
        } != {"B"}:
            raise ValueError("drive/head branches must bind relay contacts A/B")
        if not set(self.topology.planning_assumption_ids) <= assumption_ids:
            raise ValueError("topology references unknown planning assumption")
        for branch in self.topology.branches:
            if not set(branch.planning_assumption_ids) <= assumption_ids:
                raise ValueError("power branch references unknown planning assumption")

        for calculation in self.calculations:
            if len(calculation.input_selection_ids) != len(calculation.inputs):
                raise ValueError("calculation selection inputs are not aligned")
            input_values: list[float] = []
            input_quantities: list[int] = []
            for input_ref, selection_id in zip(
                calculation.inputs, calculation.input_selection_ids, strict=True
            ):
                if (
                    calculation.operation
                    in {
                        "sum_quantity_weighted",
                        "ratio",
                    }
                    and input_ref.fact_key not in _CURRENT_CALCULATION_FACT_KEYS
                ):
                    raise ValueError(
                        "current calculations must use current/rating facts"
                    )
                entry = entries.get(input_ref.entry_id)
                if entry is None:
                    raise ValueError("calculation references unknown catalog entry")
                fact = entry.fact(input_ref.fact_key)
                if fact is None or fact.state == "unknown":
                    raise ValueError("published-value calculation input is unknown")
                if (
                    calculation.basis == "published_values_only"
                    and fact.state != "known"
                ):
                    raise ValueError(
                        "published-value calculations cannot consume conflict/derived/assumption facts"
                    )
                value = entry.numeric(input_ref.fact_key)
                if value is None:
                    raise ValueError("calculation input must be a known numeric fact")
                selection = selections.get(selection_id)
                if selection is None:
                    raise ValueError("calculation references unknown selection")
                if selection.entry_id != input_ref.entry_id:
                    raise ValueError(
                        "calculation input selection must identify the referenced entry"
                    )
                if not selection.active:
                    raise ValueError("calculations cannot use inactive selections")
                input_values.append(value)
                input_quantities.append(selection.quantity)

            if calculation.operation == "sum_quantity_weighted":
                expected_value = sum(
                    value * quantity
                    for value, quantity in zip(
                        input_values, input_quantities, strict=True
                    )
                )
            elif calculation.operation == "ratio":
                numerator = input_values[0] * input_quantities[0]
                denominator = sum(
                    value * quantity
                    for value, quantity in zip(
                        input_values[1:], input_quantities[1:], strict=True
                    )
                )
                if denominator <= 0.0:
                    raise ValueError("calculation ratio denominator must be positive")
                expected_value = numerator / denominator
            else:
                if (
                    calculation.inputs[0].fact_key != "outer_diameter_mm"
                    or calculation.inputs[1].fact_key != "speed_no_load_rpm"
                ):
                    raise ValueError(
                        "wheel_speed must use outer diameter and no-load rpm"
                    )
                expected_value = (
                    math.pi * (input_values[0] / 1000.0) * input_values[1] / 60.0
                )
            if not math.isclose(
                calculation.value, expected_value, rel_tol=1e-9, abs_tol=1e-12
            ):
                raise ValueError(
                    f"calculation value does not match its published inputs: {calculation.calculation_id}"
                )
        return self

    @property
    def readiness(self) -> ReferenceStackReadiness:
        return assess_reference_stack(self)

    def selection_assessments(self) -> tuple[EligibilityAssessment, ...]:
        entries = {entry.entry_id: entry for entry in self.catalog.entries}
        return tuple(
            assess_eligibility(entries[selection.entry_id], selection.catalog_use)
            for selection in self.selections
        )

    @property
    def content_digest(self) -> str:
        return reference_stack_digest(self)


def assess_reference_stack(
    snapshot: ReferenceStackSnapshot,
) -> ReferenceStackReadiness:
    """Derive stack readiness from catalog assessments and explicit gates."""

    blocking_codes: list[str] = []
    for assessment in snapshot.selection_assessments():
        blocking_codes.extend(assessment.blocking_reasons)
    blocking_codes.extend(
        gate.gate_id
        for gate in snapshot.unresolved_gates
        if gate.blocking and gate.stage == "digital"
    )
    unique_blockers = tuple(dict.fromkeys(blocking_codes))
    datasheet_eligible = not unique_blockers
    # All required roles, topology, source observations, assumptions, and gates
    # are present in this snapshot.  Unknown facts are explicit data, so the
    # stack definition is complete even though datasheet eligibility is blocked.
    stack_definition_complete = True
    return ReferenceStackReadiness(
        stack_definition_complete=stack_definition_complete,
        datasheet_candidate=stack_definition_complete and not datasheet_eligible,
        datasheet_eligible=datasheet_eligible,
        datasheet_checked=datasheet_eligible,
        physically_qualified=False,
        blocking_codes=unique_blockers,
    )


def reference_stack_digest(snapshot: ReferenceStackSnapshot) -> str:
    """Hash the full digital stack snapshot, including catalog provenance."""

    payload = snapshot.model_dump(mode="json")
    # CatalogSnapshot.content_digest already canonicalizes entry/fact/source
    # ordering.  Embed that verified digest instead of raw catalog lists so a
    # semantically identical catalog cannot change the stack identity merely
    # by reordering entries.
    catalog_payload = payload["catalog"]
    payload["catalog"] = {
        "schema_version": catalog_payload["schema_version"],
        "catalog_version": catalog_payload["catalog_version"],
        "content_digest": snapshot.catalog_digest,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


_FACT_UNITS: dict[FactKey, Unit] = {
    "envelope_x_mm": "mm",
    "envelope_y_mm": "mm",
    "envelope_z_mm": "mm",
    "mass_g": "g",
    "product_set_mass_g": "g",
    "outer_diameter_mm": "mm",
    "part_width_mm": "mm",
    "height_mm": "mm",
    "ball_diameter_mm": "mm",
    "mount_hole_spacing_mm": "mm",
    "mount_hole_diameter_mm": "mm",
    "shaft_diameter_mm": "mm",
    "operating_voltage_min_v": "V",
    "operating_voltage_nominal_v": "V",
    "operating_voltage_max_v": "V",
    "current_continuous_a": "A",
    "current_peak_a": "A",
    "current_stall_a": "A",
    "current_limit_a": "A",
    "rail_current_limit_a": "A",
    "contact_rating_a": "A",
    "thermal_limit_c": "C",
    "torque_continuous_nm": "N*m",
    "torque_stall_nm": "N*m",
    "speed_nominal_rpm": "rpm",
    "speed_no_load_rpm": "rpm",
    "speed_max_rpm": "rpm",
    "capacity_mah": "mAh",
    "quantity_per_pack": "none",
    "wire_gauge_awg": "none",
}


def _source(
    source_id: str,
    manufacturer: str,
    title: str,
    url: str,
    media_type: Literal["text/html", "application/pdf", "text/plain"],
    document_sha256: str,
    covered_identities: tuple[tuple[str, str], ...],
) -> CatalogSource:
    return CatalogSource(
        source_id=source_id,
        manufacturer=manufacturer,
        title=title,
        url=url,
        media_type=media_type,
        document_sha256=document_sha256,
        covered_identities=tuple(
            CatalogIdentity(manufacturer_sku=sku, variant=variant)
            for sku, variant in covered_identities
        ),
        evidence_date="2026-09-04",
    )


def _evidence(source_id: str, locator: str) -> EvidenceRef:
    source = _SOURCE_BY_ID[source_id]
    return EvidenceRef(
        source_id=source.source_id,
        source_url=source.url,
        locator=locator,
        document_sha256=source.document_sha256,
        evidence_date=source.evidence_date,
    )


def _numeric(
    fact_key: FactKey,
    value: float,
    unit: Unit,
    *,
    source_id: str,
    locator: str,
    scope: str = "component",
    basis: FactBasis = "manufacturer_stated",
    canonical_value: float | None = None,
    canonical_unit: Unit | None = None,
    conversion_rule: ConversionRule = "identity",
) -> CatalogFact:
    expected_unit = _FACT_UNITS[fact_key]
    return CatalogFact(
        fact_key=fact_key,
        claims=(
            NumericClaim(
                original_value=value,
                original_unit=unit,
                canonical_value=value if canonical_value is None else canonical_value,
                canonical_unit=expected_unit
                if canonical_unit is None
                else canonical_unit,
                basis=basis,
                conversion_rule=conversion_rule,
                scope=scope,
                evidence=_evidence(source_id, locator),
            ),
        ),
    )


def _text(
    fact_key: FactKey, value: str, *, source_id: str, locator: str
) -> CatalogFact:
    return CatalogFact(
        fact_key=fact_key,
        claims=(
            TextClaim(
                original_value=value,
                canonical_value=value,
                basis="manufacturer_stated",
                evidence=_evidence(source_id, locator),
            ),
        ),
    )


def _unknown(
    fact_key: FactKey,
    reason: EligibilityReason,
    *source_ids: str,
) -> CatalogFact:
    return CatalogFact(
        fact_key=fact_key,
        unknown_reason=reason,
        unknown_evidence=tuple(
            _evidence(
                source_id, "manufacturer document reviewed; exact fact not published"
            )
            for source_id in source_ids
        ),
    )


def _conflicting_numeric(
    fact_key: FactKey,
    first_value: float,
    first_source: str,
    first_locator: str,
    second_value: float,
    second_source: str,
    second_locator: str,
    unit: Unit,
) -> CatalogFact:
    canonical_unit = _FACT_UNITS[fact_key]
    return CatalogFact(
        fact_key=fact_key,
        claims=(
            NumericClaim(
                original_value=first_value,
                original_unit=unit,
                canonical_value=first_value,
                canonical_unit=canonical_unit,
                basis="manufacturer_stated",
                conversion_rule="identity",
                evidence=_evidence(first_source, first_locator),
            ),
            NumericClaim(
                original_value=second_value,
                original_unit=unit,
                canonical_value=second_value,
                canonical_unit=canonical_unit,
                basis="manufacturer_stated",
                conversion_rule="identity",
                evidence=_evidence(second_source, second_locator),
            ),
        ),
    )


def _conflicting_text(
    fact_key: FactKey,
    first_value: str,
    first_source: str,
    first_locator: str,
    second_value: str,
    second_source: str,
    second_locator: str,
) -> CatalogFact:
    return CatalogFact(
        fact_key=fact_key,
        claims=(
            TextClaim(
                original_value=first_value,
                canonical_value=first_value,
                basis="manufacturer_stated",
                evidence=_evidence(first_source, first_locator),
            ),
            TextClaim(
                original_value=second_value,
                canonical_value=second_value,
                basis="manufacturer_stated",
                evidence=_evidence(second_source, second_locator),
            ),
        ),
    )


def _entry(
    entry_id: str,
    manufacturer: str,
    sku: str,
    variant: str,
    category: Literal[
        "controller",
        "motor",
        "wheel",
        "hub",
        "shaft",
        "servo",
        "horn",
        "caster",
        "battery",
        "charger",
        "regulator",
        "motor_driver",
        "fuse",
        "switch",
        "e_stop",
        "connector",
        "wire",
        "fastener",
        "insert",
        "spacer",
    ],
    facts: tuple[CatalogFact, ...],
    capabilities: tuple[str, ...] = (),
) -> CatalogEntry:
    return CatalogEntry(
        entry_id=entry_id,
        manufacturer=manufacturer,
        manufacturer_sku=sku,
        variant=variant,
        category=category,
        capabilities=capabilities,
        facts=facts,
    )


_BASE_SOURCES = {
    source.source_id: source
    for source in OFFICIAL_CATALOG_V2.sources
    if source.source_id
    in {
        "cores3-page",
        "cores3-schematic",
        "wheel-1087-specs",
        "wheel-1087-drawing",
        "caster-950-specs",
        "caster-950-drawing",
    }
}
_BASE_SOURCES["caster-950-specs"] = _BASE_SOURCES["caster-950-specs"].model_copy(
    update={
        "covered_identities": (
            *_BASE_SOURCES["caster-950-specs"].covered_identities,
            CatalogIdentity(
                manufacturer_sku="#950",
                variant="included #2 screws/nuts; separate fastener MPN unpublished",
            ),
        )
    }
)

_NEW_SOURCES = (
    _source(
        "robotis-xl430-shop",
        "ROBOTIS",
        "XL430-W250-T official product page",
        "https://en.robotis.com/shop_en/item.php?it_id=902-0135-000",
        "text/html",
        "010a48a8ebe76af206ed505ea0b050b46b2812ed94544e43a3a151f91cdc8958",
        (("902-0135-000", "XL430-W250-T"),),
    ),
    _source(
        "robotis-xl430-manual",
        "ROBOTIS",
        "XL430-W250 e-Manual",
        "https://emanual.robotis.com/docs/en/dxl/x/xl430-w250/",
        "text/html",
        "49fed82a3b39539c8c65ee8be23013f06541a209adbeda28612cf269a6d4d52a",
        (
            ("902-0135-000", "XL430-W250-T"),
            ("HN11-N101", "HN11-N101 assembled horn for XL430-W250-T"),
            ("Robot Cable-X3P", "180 mm TTL JST-to-JST cable for XL430"),
        ),
    ),
    _source(
        "robotis-tb3-shop",
        "ROBOTIS",
        "TB3 Wheel/Tire Set-ISW-01 official product page",
        "https://en.robotis.com/shop_en/item.php?it_id=903-0260-000",
        "text/html",
        "010a48a8ebe76af206ed505ea0b050b46b2812ed94544e43a3a151f91cdc8958",
        (("903-0260-000", "TB3 Wheel/Tire Set-ISW-01"),),
    ),
    _source(
        "robotis-tb3-download",
        "ROBOTIS",
        "TB3 wheel drawing download index",
        "https://en.robotis.com/service/downloadpage.php?ca_id=70",
        "text/html",
        "010a48a8ebe76af206ed505ea0b050b46b2812ed94544e43a3a151f91cdc8958",
        (("903-0260-000", "TB3 Wheel/Tire Set-ISW-01"),),
    ),
    _source(
        "robotis-mkr-shop",
        "ROBOTIS",
        "DYNAMIXEL Shield for Arduino MKR official product page",
        "https://en.robotis.com/shop_en/item.php?it_id=902-0146-001",
        "text/html",
        "010a48a8ebe76af206ed505ea0b050b46b2812ed94544e43a3a151f91cdc8958",
        (("902-0146-001", "DYNAMIXEL Shield for Arduino MKR series"),),
    ),
    _source(
        "robotis-mkr-docs",
        "ROBOTIS",
        "DYNAMIXEL Shield for Arduino MKR documentation",
        "https://docs.robotis.com/docs/parts/interface/mkr_shield/",
        "text/html",
        "fbe6de5a399eb0fb1f2df17128177d00700df8bb850ec2c007ecff9d83af1805",
        (("902-0146-001", "DYNAMIXEL Shield for Arduino MKR series"),),
    ),
    _source(
        "robotis-mkr-emanual",
        "ROBOTIS",
        "DYNAMIXEL Shield for Arduino MKR e-Manual",
        "https://emanual.robotis.com/docs/en/parts/interface/mkr_shield/",
        "text/html",
        "f7a9f10afd838727277c71b3c5b3965518140aab2fb980e54d2b6dc2274e4745",
        (("902-0146-001", "DYNAMIXEL Shield for Arduino MKR series"),),
    ),
    _source(
        "pololu-4869-specs",
        "Pololu",
        "227:1 Metal Gearmotor 25Dx71L MP 12V with 48 CPR Encoder",
        "https://www.pololu.com/product/4869/specs",
        "text/html",
        "d9422600dd9e57ef2ae09d56dc27ff66bd6d32b41ebb6a6bd7ab6aad8e46ca74",
        (("#4869", "227:1 Metal Gearmotor 25Dx71L MP 12V with 48 CPR Encoder"),),
    ),
    _source(
        "pololu-25d-datasheet",
        "Pololu",
        "25D metal gearmotors datasheet",
        "https://www.pololu.com/file/0J1829/pololu-25d-metal-gearmotors.pdf",
        "application/pdf",
        "a2db2ebd88546f6bdbf0a3e2ee9a45e211151abed10f6748a8839be30a1d4f10",
        (("#4869", "227:1 Metal Gearmotor 25Dx71L MP 12V with 48 CPR Encoder"),),
    ),
    _source(
        "pololu-2520-specs",
        "Pololu",
        "Dual TB9051FTG Motor Driver Shield",
        "https://www.pololu.com/product/2520/specs",
        "text/html",
        "b6f5f06a981d7f029cf3add2e52c041c731c49dd8548671e9e1f0d0daa66e796",
        (("#2520", "Dual TB9051FTG Motor Driver Shield"),),
    ),
    _source(
        "pololu-2851-specs",
        "Pololu",
        "D24V50F5 5V step-down voltage regulator",
        "https://www.pololu.com/product/2851/specs",
        "text/html",
        "014da936e009686caa0c06fbaf935a036100e2622d04b9ab5bbfd96491483fa4",
        (("#2851", "D24V50F5 5V step-down voltage regulator"),),
    ),
    _source(
        "bioenno-blf1206a",
        "Bioenno Power",
        "BLF-1206A LiFePO4 battery",
        "https://www.bioennopower.com/en-gb/products/12v-6ah-lifepo4-battery-pvc",
        "text/html",
        "a0bc0a394f3f2f7ae92b6321cda0cc2b4cb9c9bd39e52cf5dd0d9857bdc5b02b",
        (("BLF-1206A", "12V 6Ah LiFePO4 battery PVC case"),),
    ),
    _source(
        "bioenno-bpc1502dc",
        "Bioenno Power",
        "BPC-1502DC LiFePO4 battery charger",
        "https://www.bioennopower.com/en-gb/products/lithium-12v-2a-amp-lifepo4-battery-charger",
        "text/html",
        "5c1e10c88c444ca603a1e9449f3ca31d9b4591716e45b98eb38641c624290a7d",
        (("BPC-1502DC", "14.6 V 2 A LiFePO4 AC-to-DC charger, DC plug"),),
    ),
    _source(
        "bioenno-bpc1502dc-us",
        "Bioenno Power",
        "BPC-1502DC localized US charger page",
        "https://www.bioennopower.com/products/lithium-12v-2a-amp-lifepo4-battery-charger",
        "text/html",
        "0e30361cf45376fb08d1c478de2ce4e1f332bbd1d2ac751fc15ab06708197f84",
        (("BPC-1502DC", "14.6 V 2 A LiFePO4 AC-to-DC charger, DC plug"),),
    ),
    _source(
        "littelfuse-0287020",
        "Littelfuse",
        "ATOF 20A fuse 0287020.U",
        "https://www.littelfuse.com/de/products/fuses-overcurrent-protection/fuses/automotive-fuses/blade-fuses-shunt/atof/287/0287020-u",
        "text/html",
        "d473a45e309fd67868a0535aacc3f560ce57f14abb4550d6862dcadcc96ff380",
        (("0287020.U", "ATOF 20A 32V blade fuse"),),
    ),
    _source(
        "littelfuse-ato-holder",
        "Littelfuse",
        "ATO FHA fuse holder datasheet",
        "https://www.littelfuse.com/assetdocs/littelfuse-fuse-holder-ato-fha-datasheet?assetguid=988addec-bfe3-4ea2-9204-e2982cbb488e",
        "application/pdf",
        "05b49feda42c6acf013d9246ce94a95c3add9a3d63d2b5c88dead6ab55b14a6a",
        (("0FHA0002ZXJA", "ATO FHA inline holder, 9 inch 12 AWG GXL leads"),),
    ),
    _source(
        "bluesea-6006",
        "Blue Sea Systems",
        "m-Series Mini On-Off Battery Switch 6006",
        "https://www.bluesea.com/products/6006/m-Series_Battery_Switch_-_On-Off",
        "text/html",
        "df6001c815a084467d79d4b499a5dc31562034e077a6da28fee920745bf6c923",
        (("6006", "m-Series Mini On-Off Battery Switch with Knob, red"),),
    ),
    _source(
        "bluesea-6006-drawing",
        "Blue Sea Systems",
        "M-Series switch knob dimensioned drawing",
        "https://d2pyqm2yd3fw2i.cloudfront.net/files/resources/dimensioned_drawing/M_Switch_Knob.pdf",
        "application/pdf",
        "7cc74f7fbfefeb04505edb6118d069b5733d724e912a46875f926bb305f3f980",
        (("6006", "m-Series Mini On-Off Battery Switch with Knob, red"),),
    ),
    _source(
        "te-sr6-product",
        "TE Connectivity",
        "SR6B4012 force-guided relay 1393260-4",
        "https://www.te.com/en/product-1393260-4.html",
        "text/html",
        "7ae9f7546a316882223f3c04c2ccbc917a260486780c1628fe6b67a2a934efd3",
        (("1393260-4", "SR6B4012 / V23050-A1012-A542 force-guided relay"),),
    ),
    _source(
        "te-sr6-datasheet",
        "TE Connectivity",
        "SR6 relay datasheet",
        "https://www.te.com/commerce/DocumentDelivery/DDEController?Action=srchrtrv&DocFormat=pdf&DocLang=English&DocNm=SR6&DocType=Data+Sheet&PartCntxt=1393260-4",
        "application/pdf",
        "7054dbcde9e6e2573020563cca0e487533c2c60764c00b7cf2898c2edc6aaa70",
        (("1393260-4", "SR6B4012 / V23050-A1012-A542 force-guided relay"),),
    ),
    _source(
        "te-sr6-brochure",
        "TE Connectivity",
        "SCHRACK force-guided relays brochure",
        "https://www.te.com/content/dam/te-com/documents/industrial/global/schrack-force-guided-relays.pdf",
        "application/pdf",
        "749d4bd0cc995f0048c623a8a8e819520e933ad61d4a279b25be69bd1628ad57",
        (("1393260-4", "SR6B4012 / V23050-A1012-A542 force-guided relay"),),
    ),
    _source(
        "schneider-xb5as8442",
        "Schneider Electric",
        "XB5AS8442 emergency stop pushbutton datasheet",
        "https://iportal.se.com/Contents/docs/SQD-XB5AS8442_DATA%20SHEET.PDF",
        "application/pdf",
        "2bf2454adb85e792c717537c71d32394b8b7e7535157648c45c56b73c1b48b8f",
        (("XB5AS8442", "red 40 mm mushroom emergency-stop, turn-to-release, 1NC"),),
    ),
    _source(
        "anderson-pp30-datasheet",
        "Anderson Power Products",
        "Powerpole PP30 P-S datasheet",
        "https://www.andersonpower.com/content/dam/app/ecommerce/product-pdfs/PP30-P-S/ds-pp30ps.pdf",
        "application/pdf",
        "ece5128dad70938d13b63f8cb08a069e9e1e6e079ff0e0d218000cde1f19f6aa",
        (
            ("1327", "Powerpole red PP30 housing"),
            ("1331-BK", "Powerpole 15-45 silver-plated contact, 16-12 AWG"),
        ),
    ),
    _source(
        "anderson-1327",
        "Anderson Power Products",
        "Powerpole red housing 1327",
        "https://www.andersonpower.com/product/powerpole-connector-housing-red/",
        "text/html",
        "b45087cb522410e70da5ebf79646d3af85fafc34fd0d57e4a6bd748fdf93796d",
        (("1327", "Powerpole red PP30 housing"),),
    ),
    _source(
        "anderson-1331-bk",
        "Anderson Power Products",
        "Powerpole 15-45 silver-plated contact 1331-BK",
        "https://www.andersonpower.com/product/powerpole-15-45-silver-plated-power-contacts-16-12-awg-bk/",
        "text/html",
        "c1c16afa7c698dea6f121c7750fd808834f34a8130dd170e6f1bf33188f9f89e",
        (("1331-BK", "Powerpole 15-45 silver-plated contact, 16-12 AWG"),),
    ),
    _source(
        "alpha-461219",
        "Alpha Wire",
        "Premium hook-up wire 461219",
        "https://www.alphawire.com/products/wire/hook-up-wire/premium/461219",
        "text/html",
        "1e72466b3bb3ef9b7e64e712c596c7ead7ed880d1bda100d52086b9b8e37ba17",
        (("461219", "12 AWG premium hook-up wire"),),
    ),
    _source(
        "spirol-151332",
        "SPIROL",
        "Series 10 M3 self-tapping insert 151332",
        "https://shop.spirol.com/item/self-tapping-inserts/series-10-thread-form-self-tapping-insert-metric/151332",
        "text/html",
        "a88bc9620b921ed0a68c3dc6aa41c8df1fab1f91ee547017164811ab6f23c06c",
        (("151332", "Series 10 M3x0.5 self-tapping thread-forming insert"),),
    ),
    _source(
        "essentra-13rs018725",
        "Essentra Components",
        "Round unthreaded PA spacer 13RS018725",
        "https://www.essentracomponents.com/en-gb/p/round-unthreaded-pa-spacers/13rs018725",
        "text/html",
        "9bc4eab6eac0c9f7fe76ca67abafe789d46dc1c5b3f2228bbce01872d58ab45b",
        (("13RS018725", "round unthreaded PA spacer, M3, 7.9 mm length"),),
    ),
)

_SOURCE_BY_ID: dict[str, CatalogSource] = {
    **_BASE_SOURCES,
    **{source.source_id: source for source in _NEW_SOURCES},
}


ROBOTIS_XL430_W250_T = _entry(
    "robotis-xl430-w250-t",
    "ROBOTIS",
    "902-0135-000",
    "XL430-W250-T",
    "servo",
    (
        _numeric(
            "envelope_x_mm",
            28.5,
            "mm",
            source_id="robotis-xl430-manual",
            locator="Specifications > Dimension > width",
        ),
        _numeric(
            "envelope_y_mm",
            46.5,
            "mm",
            source_id="robotis-xl430-manual",
            locator="Specifications > Dimension > length",
        ),
        _numeric(
            "envelope_z_mm",
            34.0,
            "mm",
            source_id="robotis-xl430-manual",
            locator="Specifications > Dimension > height",
        ),
        _numeric(
            "mass_g",
            57.2,
            "g",
            source_id="robotis-xl430-manual",
            locator="Specifications > Weight > 57.2 g",
        ),
        _numeric(
            "operating_voltage_min_v",
            6.5,
            "V",
            source_id="robotis-xl430-manual",
            locator="Specifications > Input Voltage > 6.5 ~ 12.0 V",
        ),
        _numeric(
            "operating_voltage_nominal_v",
            11.1,
            "V",
            source_id="robotis-xl430-manual",
            locator="Specifications > Input Voltage > recommended 11.1 V",
        ),
        _numeric(
            "operating_voltage_max_v",
            12.0,
            "V",
            source_id="robotis-xl430-manual",
            locator="Specifications > Input Voltage > 6.5 ~ 12.0 V",
        ),
        _numeric(
            "current_stall_a",
            1.4,
            "A",
            source_id="robotis-xl430-manual",
            locator="Specifications > Stall torque/current > 1.5 N*m at 12.0 V, 1.4 A",
        ),
        _numeric(
            "torque_stall_nm",
            1.50,
            "N*m",
            source_id="robotis-xl430-manual",
            locator="Specifications > Stall torque/current > 1.5 N*m at 12.0 V, 1.4 A",
        ),
        _numeric(
            "speed_no_load_rpm",
            61.0,
            "rpm",
            source_id="robotis-xl430-manual",
            locator="Specifications > No load speed > 61 rev/min at 12.0 V",
        ),
        _text(
            "mount_pattern",
            "M2.6x5, M2x5, and M2.5x14 case fastener pattern",
            source_id="robotis-xl430-manual",
            locator="Specifications > Mechanical drawing > case fasteners",
        ),
        _text(
            "shaft_profile",
            "DYNAMIXEL output spline with HN11-N101 horn",
            source_id="robotis-xl430-manual",
            locator="Specifications > Included items > HN11-N101 assembled horn",
        ),
        _text(
            "connector_family",
            "TTL JST EHR-03 / B3B-EH-A / SEH-001T-P0.6",
            source_id="robotis-xl430-manual",
            locator="Specifications > Connector > TTL 3-pin connector family",
        ),
        _text(
            "communication_protocol",
            "TTL half-duplex",
            source_id="robotis-xl430-manual",
            locator="Specifications > Communication > TTL half-duplex",
        ),
        _unknown(
            "current_continuous_a", "MISSING_CURRENT_LIMIT", "robotis-xl430-manual"
        ),
        _unknown("torque_continuous_nm", "MISSING_TORQUE", "robotis-xl430-manual"),
        _unknown("speed_max_rpm", "MISSING_SPEED", "robotis-xl430-manual"),
        _unknown("connector_mpn", "MISSING_CONNECTOR", "robotis-xl430-manual"),
        _unknown("revision", "MISSING_REVISION", "robotis-xl430-manual"),
    ),
    ("ttl-half-duplex", "xl430", "head-actuator"),
)


ROBOTIS_HN11_N101 = _entry(
    "robotis-hn11-n101",
    "ROBOTIS",
    "HN11-N101",
    "HN11-N101 assembled horn for XL430-W250-T",
    "horn",
    (
        _text(
            "shaft_profile",
            "DYNAMIXEL HN11 spline",
            source_id="robotis-xl430-manual",
            locator="Specifications > Included items > HN11-N101 assembled horn",
        ),
        _unknown("envelope_x_mm", "MISSING_ENVELOPE", "robotis-xl430-manual"),
        _unknown("envelope_y_mm", "MISSING_ENVELOPE", "robotis-xl430-manual"),
        _unknown("envelope_z_mm", "MISSING_ENVELOPE", "robotis-xl430-manual"),
        _unknown("mass_g", "MISSING_MASS", "robotis-xl430-manual"),
        _unknown("mount_pattern", "MISSING_MOUNT_GEOMETRY", "robotis-xl430-manual"),
    ),
    ("hn11", "xl430-horn"),
)


ROBOTIS_TB3_WHEEL_ISW01 = _entry(
    "robotis-tb3-wheel-isw01",
    "ROBOTIS",
    "903-0260-000",
    "TB3 Wheel/Tire Set-ISW-01",
    "wheel",
    (
        # The observed ROBOTIS shop/download responses were generic HTML, so
        # no affirmative product-specific claim is attached.  Unknown-evidence
        # references remain to record that those sources were reviewed and did
        # not supply the missing geometry.
        _unknown("quantity_per_pack", "UNKNOWN_OFFICIAL_FACT"),
        _unknown("material", "UNKNOWN_OFFICIAL_FACT"),
        _unknown(
            "envelope_x_mm",
            "MISSING_ENVELOPE",
            "robotis-tb3-shop",
            "robotis-tb3-download",
        ),
        _unknown(
            "envelope_y_mm",
            "MISSING_ENVELOPE",
            "robotis-tb3-shop",
            "robotis-tb3-download",
        ),
        _unknown(
            "envelope_z_mm",
            "MISSING_ENVELOPE",
            "robotis-tb3-shop",
            "robotis-tb3-download",
        ),
        _unknown("mass_g", "MISSING_MASS", "robotis-tb3-shop", "robotis-tb3-download"),
        _unknown(
            "shaft_diameter_mm",
            "MISSING_SHAFT_GEOMETRY",
            "robotis-tb3-shop",
            "robotis-tb3-download",
        ),
        _unknown(
            "shaft_profile",
            "MISSING_SHAFT_GEOMETRY",
            "robotis-tb3-shop",
            "robotis-tb3-download",
        ),
        _unknown(
            "mount_pattern",
            "MISSING_MOUNT_GEOMETRY",
            "robotis-tb3-shop",
            "robotis-tb3-download",
        ),
    ),
    ("dynamixel-horn-mount", "tb3", "rubber-tire"),
)


POLOLU_4869 = _entry(
    "pololu-4869",
    "Pololu",
    "#4869",
    "227:1 Metal Gearmotor 25Dx71L MP 12V with 48 CPR Encoder",
    "motor",
    (
        _numeric(
            "envelope_x_mm",
            25.0,
            "mm",
            source_id="pololu-4869-specs",
            locator="Dimensions > Gearmotor diameter > 25 mm",
        ),
        _numeric(
            "envelope_y_mm",
            25.0,
            "mm",
            source_id="pololu-4869-specs",
            locator="Dimensions > Gearmotor diameter > 25 mm",
        ),
        _numeric(
            "envelope_z_mm",
            71.0,
            "mm",
            source_id="pololu-4869-specs",
            locator="Dimensions > Gearmotor length > 71 mm",
        ),
        _numeric(
            "mass_g",
            107.0,
            "g",
            source_id="pololu-4869-specs",
            locator="Dimensions > Weight > 107 g",
        ),
        _numeric(
            "operating_voltage_nominal_v",
            12.0,
            "V",
            source_id="pololu-4869-specs",
            locator="Performance > Nominal voltage > 12 V",
        ),
        _numeric(
            "current_stall_a",
            1.8,
            "A",
            source_id="pololu-4869-specs",
            locator="Performance > Stall current (extrapolated) > 1.8 A",
        ),
        _numeric(
            "speed_no_load_rpm",
            35.0,
            "rpm",
            source_id="pololu-4869-specs",
            locator="Performance > No-load speed > 35 RPM",
        ),
        _numeric(
            "shaft_diameter_mm",
            4.0,
            "mm",
            source_id="pololu-25d-datasheet",
            locator="Mechanical drawing > output shaft > 4 mm D shaft",
        ),
        _text(
            "shaft_profile",
            "4 mm D output shaft",
            source_id="pololu-25d-datasheet",
            locator="Mechanical drawing > output shaft profile",
        ),
        _text(
            "mount_pattern",
            "25D gearbox mounting face",
            source_id="pololu-25d-datasheet",
            locator="Mechanical drawing > gearbox mounting face",
        ),
        _text(
            "connector_family",
            "1x6 female 2.54 mm encoder header; six 20 cm motor/encoder leads",
            source_id="pololu-25d-datasheet",
            locator="Electrical connections > six leads and 1x6 header",
        ),
        _unknown(
            "current_continuous_a",
            "MISSING_CURRENT_LIMIT",
            "pololu-4869-specs",
            "pololu-25d-datasheet",
        ),
        _unknown(
            "torque_continuous_nm",
            "MISSING_TORQUE",
            "pololu-4869-specs",
            "pololu-25d-datasheet",
        ),
        _unknown(
            "speed_nominal_rpm",
            "MISSING_SPEED",
            "pololu-4869-specs",
            "pololu-25d-datasheet",
        ),
        _unknown("revision", "MISSING_REVISION", "pololu-4869-specs"),
    ),
    ("brushed-dc", "forty-eight-cpr-encoder", "twelve-v"),
)


POLOLU_TB9051FTG = _entry(
    "pololu-tb9051ftg-shield-2520",
    "Pololu",
    "#2520",
    "Dual TB9051FTG Motor Driver Shield",
    "motor_driver",
    (
        _numeric(
            "envelope_x_mm",
            48.3,
            "mm",
            source_id="pololu-2520-specs",
            locator="Dimensions > 48.3 mm",
        ),
        _numeric(
            "envelope_y_mm",
            51.3,
            "mm",
            source_id="pololu-2520-specs",
            locator="Dimensions > 51.3 mm",
        ),
        _numeric(
            "envelope_z_mm",
            7.62,
            "mm",
            source_id="pololu-2520-specs",
            locator="Dimensions > height > 7.62 mm",
        ),
        _numeric(
            "mass_g",
            11.0,
            "g",
            source_id="pololu-2520-specs",
            locator="Dimensions > Weight > 11 g",
        ),
        _numeric(
            "operating_voltage_min_v",
            4.5,
            "V",
            source_id="pololu-2520-specs",
            locator="Specifications > Motor supply > 4.5 V minimum",
        ),
        _numeric(
            "operating_voltage_max_v",
            28.0,
            "V",
            source_id="pololu-2520-specs",
            locator="Specifications > Motor supply > 28 V maximum",
        ),
        _numeric(
            "current_continuous_a",
            2.6,
            "A",
            source_id="pololu-2520-specs",
            locator="Specifications > Output current > 2.6 A continuous per channel typical",
        ),
        _numeric(
            "current_peak_a",
            5.0,
            "A",
            source_id="pololu-2520-specs",
            locator="Specifications > Output current > 5 A peak per channel",
        ),
        _text(
            "motor_driver_model",
            "TB9051FTG",
            source_id="pololu-2520-specs",
            locator="Description > TB9051FTG motor driver",
        ),
        _unknown("thermal_limit_c", "MISSING_THERMAL_LIMIT", "pololu-2520-specs"),
        _unknown("connector_mpn", "MISSING_CONNECTOR", "pololu-2520-specs"),
        _unknown("revision", "MISSING_REVISION", "pololu-2520-specs"),
    ),
    ("dual-channel", "reverse-protection", "overtemperature-protection"),
)


ROBOTIS_MKR_SHIELD = _entry(
    "robotis-mkr-shield-902-0146-001",
    "ROBOTIS",
    "902-0146-001",
    "DYNAMIXEL Shield for Arduino MKR series",
    "motor_driver",
    (
        _numeric(
            "envelope_x_mm",
            65.0,
            "mm",
            source_id="robotis-mkr-docs",
            locator="Specifications > Board dimension > 65 mm",
        ),
        _numeric(
            "envelope_y_mm",
            25.0,
            "mm",
            source_id="robotis-mkr-docs",
            locator="Specifications > Board dimension > 25 mm",
        ),
        _numeric(
            "mass_g",
            11.0,
            "g",
            source_id="robotis-mkr-docs",
            locator="Specifications > Weight > 11 g",
        ),
        _numeric(
            "operating_voltage_min_v",
            3.5,
            "V",
            source_id="robotis-mkr-docs",
            locator="Specifications > VIN(DXL) > 3.5 V minimum",
        ),
        _numeric(
            "operating_voltage_max_v",
            24.0,
            "V",
            source_id="robotis-mkr-docs",
            locator="Specifications > VIN(DXL) > 24 V maximum",
        ),
        _text(
            "connector_mpn",
            "5268-03A",
            source_id="robotis-mkr-docs",
            locator="Specifications > Connectors > Molex 5268-03A",
        ),
        _text(
            "connector_family",
            "JST S3B-EH, Molex 5268, SMW250-02, and DG350-3.5-02P-14",
            source_id="robotis-mkr-docs",
            locator="Specifications > Connectors",
        ),
        _text(
            "communication_protocol",
            "TTL multidrop; 5 V level in legacy e-Manual",
            source_id="robotis-mkr-emanual",
            locator="Specifications > DYNAMIXEL signal > TTL multidrop / 5 V level",
        ),
        _unknown(
            "current_continuous_a",
            "MISSING_CURRENT_LIMIT",
            "robotis-mkr-docs",
        ),
        _unknown(
            "current_peak_a",
            "MISSING_CURRENT_LIMIT",
            "robotis-mkr-docs",
        ),
        _unknown(
            "thermal_limit_c",
            "MISSING_THERMAL_LIMIT",
            "robotis-mkr-docs",
        ),
        _unknown(
            "revision", "MISSING_REVISION", "robotis-mkr-docs", "robotis-mkr-emanual"
        ),
        _unknown(
            "power_isolation",
            "MISSING_POWER_ISOLATION",
            "robotis-mkr-docs",
        ),
    ),
    ("dynamixel-ttl-interface", "mkr-form-factor"),
)


BIOENNO_BLF1206A = _entry(
    "bioenno-blf-1206a",
    "Bioenno Power",
    "BLF-1206A",
    "12V 6Ah LiFePO4 battery PVC case",
    "battery",
    (
        _numeric(
            "envelope_x_mm",
            108.0,
            "mm",
            source_id="bioenno-blf1206a",
            locator="Specifications > Dimensions > 108 mm",
        ),
        _numeric(
            "envelope_y_mm",
            64.0,
            "mm",
            source_id="bioenno-blf1206a",
            locator="Specifications > Dimensions > 64 mm",
        ),
        _numeric(
            "envelope_z_mm",
            69.0,
            "mm",
            source_id="bioenno-blf1206a",
            locator="Specifications > Dimensions > 69 mm",
        ),
        _numeric(
            "mass_g",
            0.7,
            "kg",
            source_id="bioenno-blf1206a",
            locator="Specifications > Weight > 0.7 kg",
            basis="converted",
            canonical_value=700.0,
            canonical_unit="g",
            conversion_rule="kg_to_g",
        ),
        _numeric(
            "operating_voltage_nominal_v",
            12.0,
            "V",
            source_id="bioenno-blf1206a",
            locator="Specifications > Nominal voltage > 12 V",
        ),
        _numeric(
            "capacity_mah",
            6000.0,
            "mAh",
            source_id="bioenno-blf1206a",
            locator="Specifications > Capacity > 6 Ah",
        ),
        _numeric(
            "current_continuous_a",
            12.0,
            "A",
            source_id="bioenno-blf1206a",
            locator="Specifications > Maximum continuous discharge > 12 A",
        ),
        _numeric(
            "current_peak_a",
            24.0,
            "A",
            source_id="bioenno-blf1206a",
            locator="Specifications > Peak discharge > 24 A for 2 seconds",
        ),
        _text(
            "battery_chemistry",
            "LiFePO4",
            source_id="bioenno-blf1206a",
            locator="Product title/specifications > LiFePO4",
        ),
        _text(
            "battery_protection",
            "PCM with balance, overcurrent, under-voltage, over-voltage, and short-circuit protection",
            source_id="bioenno-blf1206a",
            locator="Specifications > Protection circuit module",
        ),
        _text(
            "connector_family",
            "Anderson Powerpole PP30 discharge; DC barrel charge",
            source_id="bioenno-blf1206a",
            locator="Specifications > Discharge connector and charging connector",
        ),
        _unknown("connector_mpn", "MISSING_CONNECTOR", "bioenno-blf1206a"),
        _unknown("revision", "MISSING_REVISION", "bioenno-blf1206a"),
    ),
    ("twelve-v", "lifepo4", "pcm-protected"),
)


BIOENNO_BPC1502DC = _entry(
    "bioenno-bpc-1502dc",
    "Bioenno Power",
    "BPC-1502DC",
    "14.6 V 2 A LiFePO4 AC-to-DC charger, DC plug",
    "charger",
    (
        _numeric(
            "mass_g",
            0.45,
            "kg",
            source_id="bioenno-bpc1502dc",
            locator="Specifications > Weight > 0.45 kg",
            basis="converted",
            canonical_value=450.0,
            canonical_unit="g",
            conversion_rule="kg_to_g",
        ),
        _numeric(
            "operating_voltage_nominal_v",
            14.6,
            "V",
            source_id="bioenno-bpc1502dc",
            locator="Product description > 14.6 V output",
        ),
        _numeric(
            "current_continuous_a",
            2.0,
            "A",
            source_id="bioenno-bpc1502dc",
            locator="Product description > 2 A output",
        ),
        _text(
            "battery_chemistry",
            "LiFePO4",
            source_id="bioenno-bpc1502dc",
            locator="Product description > for 12 V LiFePO4 batteries",
        ),
        _unknown(
            "connector_mpn",
            "MISSING_CONNECTOR",
            "bioenno-bpc1502dc",
            "bioenno-bpc1502dc-us",
        ),
        _unknown(
            "revision", "MISSING_REVISION", "bioenno-bpc1502dc", "bioenno-bpc1502dc-us"
        ),
    ),
    ("cc-cv", "fourteen-six-v-output", "dc-plug"),
)


POLOLU_D24V50F5 = _entry(
    "pololu-d24v50f5-2851",
    "Pololu",
    "#2851",
    "D24V50F5 5V step-down voltage regulator",
    "regulator",
    (
        _numeric(
            "envelope_x_mm",
            17.8,
            "mm",
            source_id="pololu-2851-specs",
            locator="Dimensions > 17.8 mm",
        ),
        _numeric(
            "envelope_y_mm",
            20.3,
            "mm",
            source_id="pololu-2851-specs",
            locator="Dimensions > 20.3 mm",
        ),
        _numeric(
            "envelope_z_mm",
            8.8,
            "mm",
            source_id="pololu-2851-specs",
            locator="Dimensions > 8.8 mm",
        ),
        _numeric(
            "mass_g",
            3.0,
            "g",
            source_id="pololu-2851-specs",
            locator="Dimensions > Weight > 3 g",
        ),
        _numeric(
            "operating_voltage_min_v",
            6.0,
            "V",
            source_id="pololu-2851-specs",
            locator="Specifications > Input voltage > 6 V minimum",
        ),
        _numeric(
            "operating_voltage_max_v",
            38.0,
            "V",
            source_id="pololu-2851-specs",
            locator="Specifications > Input voltage > 38 V maximum",
        ),
        _numeric(
            "current_limit_a",
            5.0,
            "A",
            source_id="pololu-2851-specs",
            locator="Specifications > Output current > 5 A typical thermal limit",
        ),
        _text(
            "battery_protection",
            "reverse-voltage, short-circuit, over-current, over-temperature, soft-start, and UVLO protections",
            source_id="pololu-2851-specs",
            locator="Features > protection list",
        ),
        _unknown("thermal_limit_c", "MISSING_THERMAL_LIMIT", "pololu-2851-specs"),
        _unknown("connector_mpn", "MISSING_CONNECTOR", "pololu-2851-specs"),
        _unknown("revision", "MISSING_REVISION", "pololu-2851-specs"),
    ),
    ("five-v-output", "thermal-limited", "controller-supply-fallback"),
)


LITTELFUSE_ATOF_0287020 = _entry(
    "littelfuse-atof-0287020-u",
    "Littelfuse",
    "0287020.U",
    "ATOF 20A 32V blade fuse",
    "fuse",
    (
        _numeric(
            "envelope_x_mm",
            19.1,
            "mm",
            source_id="littelfuse-0287020",
            locator="ATOF datasheet > Dimensions > length 19.1 mm",
        ),
        _numeric(
            "envelope_y_mm",
            18.8,
            "mm",
            source_id="littelfuse-0287020",
            locator="ATOF datasheet > Dimensions > width 18.8 mm",
        ),
        _numeric(
            "envelope_z_mm",
            5.1,
            "mm",
            source_id="littelfuse-0287020",
            locator="ATOF datasheet > Dimensions > height 5.1 mm",
        ),
        _numeric(
            "mass_g",
            1.4,
            "g",
            source_id="littelfuse-0287020",
            locator="ATOF datasheet > Weight > 1.4 g",
        ),
        _numeric(
            "operating_voltage_max_v",
            32.0,
            "V",
            source_id="littelfuse-0287020",
            locator="ATOF datasheet > Voltage rating > 32 VDC",
        ),
        _numeric(
            "current_limit_a",
            20.0,
            "A",
            source_id="littelfuse-0287020",
            locator="Product identity/datasheet > 20 A",
        ),
        _text(
            "revision",
            "ATOF datasheet R2.7, revised 2025-02-04",
            source_id="littelfuse-0287020",
            locator="ATOF datasheet > revision/date",
        ),
    ),
    ("twenty-a", "thirty-two-vdc", "thousand-a-interrupting"),
)


LITTELFUSE_ATO_HOLDER = _entry(
    "littelfuse-ato-holder-0fha0002zxja",
    "Littelfuse",
    "0FHA0002ZXJA",
    "ATO FHA inline holder, 9 inch 12 AWG GXL leads",
    "fuse",
    (
        _numeric(
            "operating_voltage_max_v",
            32.0,
            "V",
            source_id="littelfuse-ato-holder",
            locator="FHA datasheet > voltage rating > 32 VDC",
        ),
        _numeric(
            "current_limit_a",
            30.0,
            "A",
            source_id="littelfuse-ato-holder",
            locator="FHA datasheet > current rating > 30 A",
        ),
        _numeric(
            "wire_gauge_awg",
            12.0,
            "none",
            source_id="littelfuse-ato-holder",
            locator="FHA datasheet > lead option > 12 AWG GXL",
        ),
        _unknown("envelope_x_mm", "MISSING_ENVELOPE", "littelfuse-ato-holder"),
        _unknown("envelope_y_mm", "MISSING_ENVELOPE", "littelfuse-ato-holder"),
        _unknown("envelope_z_mm", "MISSING_ENVELOPE", "littelfuse-ato-holder"),
        _unknown("mass_g", "MISSING_MASS", "littelfuse-ato-holder"),
        _unknown("revision", "MISSING_REVISION", "littelfuse-ato-holder"),
    ),
    ("inline-holder", "twelve-awg-leads"),
)


BLUESEA_6006 = _entry(
    "bluesea-m-series-6006",
    "Blue Sea Systems",
    "6006",
    "m-Series Mini On-Off Battery Switch with Knob, red",
    "switch",
    (
        _numeric(
            "envelope_x_mm",
            84.34,
            "mm",
            source_id="bluesea-6006-drawing",
            locator="Dimensioned drawing > overall width 84.34 mm",
        ),
        _numeric(
            "envelope_y_mm",
            74.93,
            "mm",
            source_id="bluesea-6006-drawing",
            locator="Dimensioned drawing > overall height 74.93 mm",
        ),
        _numeric(
            "envelope_z_mm",
            45.58,
            "mm",
            source_id="bluesea-6006-drawing",
            locator="Dimensioned drawing > overall depth 45.58 mm",
        ),
        _numeric(
            "mass_g",
            0.29,
            "kg",
            source_id="bluesea-6006",
            locator="Product specifications > Weight > 0.29 kg",
            basis="converted",
            canonical_value=290.0,
            canonical_unit="g",
            conversion_rule="kg_to_g",
        ),
        _numeric(
            "operating_voltage_max_v",
            48.0,
            "V",
            source_id="bluesea-6006",
            locator="Electrical specifications > max voltage 48 VDC",
        ),
        _numeric(
            "current_limit_a",
            300.0,
            "A",
            source_id="bluesea-6006",
            locator="Electrical specifications > continuous 300 A",
        ),
        _numeric(
            "contact_rating_a",
            25.0,
            "A",
            source_id="bluesea-6006",
            locator="Electrical specifications > switching 25 A",
        ),
        _text(
            "mount_pattern",
            "two #10 mounting screws; 3/8-16 (M10) studs",
            source_id="bluesea-6006-drawing",
            locator="Dimensioned drawing > mounting holes and studs",
        ),
        _unknown(
            "revision", "MISSING_REVISION", "bluesea-6006", "bluesea-6006-drawing"
        ),
    ),
    ("manual-battery-isolation", "three-hundred-a-continuous"),
)


TE_SR6B4012 = _entry(
    "te-sr6b4012-1393260-4",
    "TE Connectivity",
    "1393260-4",
    "SR6B4012 / V23050-A1012-A542 force-guided relay",
    "e_stop",
    (
        _numeric(
            "envelope_x_mm",
            55.0,
            "mm",
            source_id="te-sr6-datasheet",
            locator="SR6 datasheet > dimensions > 55 mm",
        ),
        _numeric(
            "envelope_y_mm",
            16.5,
            "mm",
            source_id="te-sr6-datasheet",
            locator="SR6 datasheet > dimensions > 16.5 mm",
        ),
        _numeric(
            "envelope_z_mm",
            16.5,
            "mm",
            source_id="te-sr6-datasheet",
            locator="SR6 datasheet > dimensions > 16.5 mm",
        ),
        _numeric(
            "mass_g",
            30.0,
            "g",
            source_id="te-sr6-datasheet",
            locator="SR6 datasheet > weight > 30 g",
        ),
        _text(
            "communication_protocol",
            "force-guided EN 61810-3; 4 NO + 2 NC",
            source_id="te-sr6-datasheet",
            locator="SR6 datasheet > contact arrangement and force-guided classification",
        ),
        _unknown(
            "operating_voltage_max_v",
            "MISSING_OPERATING_VOLTAGE",
            "te-sr6-product",
            "te-sr6-datasheet",
        ),
        _unknown(
            "current_limit_a",
            "MISSING_CURRENT_LIMIT",
            "te-sr6-product",
            "te-sr6-datasheet",
        ),
        _unknown("revision", "MISSING_REVISION", "te-sr6-product", "te-sr6-datasheet"),
    ),
    ("force-guided", "twelve-v-coil", "four-no-two-nc"),
)


SCHNEIDER_XB5AS8442 = _entry(
    "schneider-xb5as8442",
    "Schneider Electric",
    "XB5AS8442",
    "red 40 mm mushroom emergency-stop, turn-to-release, 1NC",
    "e_stop",
    (
        _numeric(
            "envelope_x_mm",
            40.0,
            "mm",
            source_id="schneider-xb5as8442",
            locator="Datasheet > dimensions > mushroom diameter 40 mm",
        ),
        _numeric(
            "envelope_y_mm",
            43.0,
            "mm",
            source_id="schneider-xb5as8442",
            locator="Datasheet > dimensions > body height 43 mm",
        ),
        _numeric(
            "envelope_z_mm",
            82.0,
            "mm",
            source_id="schneider-xb5as8442",
            locator="Datasheet > dimensions > body depth 82 mm",
        ),
        _numeric(
            "operating_voltage_max_v",
            24.0,
            "V",
            source_id="schneider-xb5as8442",
            locator="Datasheet > electrical > DC13 24 V",
        ),
        _numeric(
            "current_limit_a",
            0.5,
            "A",
            source_id="schneider-xb5as8442",
            locator="Datasheet > electrical > DC13 0.5 A",
        ),
        _numeric(
            "contact_rating_a",
            0.5,
            "A",
            source_id="schneider-xb5as8442",
            locator="Datasheet > electrical > DC13 0.5 A",
        ),
        _text(
            "mount_pattern",
            "22 mm panel mount; 40 mm mushroom; turn-to-release",
            source_id="schneider-xb5as8442",
            locator="Datasheet > product description and dimensions",
        ),
        _unknown("mass_g", "MISSING_MASS", "schneider-xb5as8442"),
        _unknown("revision", "MISSING_REVISION", "schneider-xb5as8442"),
    ),
    ("positive-opening", "one-nc", "turn-to-release"),
)


ANDERSON_PP30_HOUSING_1327 = _entry(
    "anderson-powerpole-1327",
    "Anderson Power Products",
    "1327",
    "Powerpole red PP30 housing",
    "connector",
    (
        _numeric(
            "envelope_x_mm",
            7.9,
            "mm",
            source_id="anderson-pp30-datasheet",
            locator="PP30 datasheet > housing dimensions > width 7.9 mm",
        ),
        _numeric(
            "envelope_y_mm",
            7.9,
            "mm",
            source_id="anderson-pp30-datasheet",
            locator="PP30 datasheet > housing dimensions > height 7.9 mm",
        ),
        _numeric(
            "envelope_z_mm",
            24.6,
            "mm",
            source_id="anderson-pp30-datasheet",
            locator="PP30 datasheet > housing dimensions > length 24.6 mm",
        ),
        _text(
            "connector_mpn",
            "1327",
            source_id="anderson-1327",
            locator="Product identity > red Powerpole housing 1327",
        ),
        _text(
            "connector_family",
            "Powerpole PP30",
            source_id="anderson-pp30-datasheet",
            locator="PP30 datasheet > product family",
        ),
        _unknown("mass_g", "MISSING_MASS", "anderson-1327", "anderson-pp30-datasheet"),
        _unknown(
            "revision", "MISSING_REVISION", "anderson-1327", "anderson-pp30-datasheet"
        ),
    ),
    ("pp30", "red-housing", "keyed-mating"),
)


ANDERSON_PP30_CONTACT_1331 = _entry(
    "anderson-powerpole-1331-bk",
    "Anderson Power Products",
    "1331-BK",
    "Powerpole 15-45 silver-plated contact, 16-12 AWG",
    "connector",
    (
        _text(
            "connector_mpn",
            "1331-BK",
            source_id="anderson-1331-bk",
            locator="Product identity > 15-45 silver-plated power contact 1331-BK",
        ),
        _text(
            "connector_family",
            "Powerpole PP30 15-45 A contact",
            source_id="anderson-pp30-datasheet",
            locator="PP30 datasheet > 15-45 A contact family",
        ),
        _unknown(
            "envelope_x_mm",
            "MISSING_ENVELOPE",
            "anderson-1331-bk",
            "anderson-pp30-datasheet",
        ),
        _unknown(
            "envelope_y_mm",
            "MISSING_ENVELOPE",
            "anderson-1331-bk",
            "anderson-pp30-datasheet",
        ),
        _unknown(
            "envelope_z_mm",
            "MISSING_ENVELOPE",
            "anderson-1331-bk",
            "anderson-pp30-datasheet",
        ),
        _unknown(
            "mass_g", "MISSING_MASS", "anderson-1331-bk", "anderson-pp30-datasheet"
        ),
        _unknown(
            "revision",
            "MISSING_REVISION",
            "anderson-1331-bk",
            "anderson-pp30-datasheet",
        ),
    ),
    ("pp30", "silver-contact", "sixteen-twelve-awg"),
)


ROBOTIS_ROBOT_CABLE_X3P = _entry(
    "robotis-robot-cable-x3p-180mm",
    "ROBOTIS",
    "Robot Cable-X3P",
    "180 mm TTL JST-to-JST cable for XL430",
    "connector",
    (
        _numeric(
            "envelope_x_mm",
            180.0,
            "mm",
            source_id="robotis-xl430-manual",
            locator="Specifications > Included cable > 180 mm cable length",
        ),
        _text(
            "connector_family",
            "JST EHR-03 / B3B-EH-A / SEH-001T-P0.6 TTL",
            source_id="robotis-xl430-manual",
            locator="Specifications > Connector > TTL 3-pin cable family",
        ),
        _unknown("envelope_y_mm", "MISSING_ENVELOPE", "robotis-xl430-manual"),
        _unknown("envelope_z_mm", "MISSING_ENVELOPE", "robotis-xl430-manual"),
        _unknown("mass_g", "MISSING_MASS", "robotis-xl430-manual"),
        _unknown("connector_mpn", "MISSING_CONNECTOR", "robotis-xl430-manual"),
        _unknown("revision", "MISSING_REVISION", "robotis-xl430-manual"),
    ),
    ("ttl", "jst-3p", "one-hundred-eighty-mm"),
)


ALPHA_461219 = _entry(
    "alpha-wire-461219",
    "Alpha Wire",
    "461219",
    "12 AWG premium hook-up wire",
    "wire",
    (
        _numeric(
            "wire_gauge_awg",
            12.0,
            "none",
            source_id="alpha-461219",
            locator="Product specifications > conductor > 12 AWG",
        ),
        _numeric(
            "operating_voltage_max_v",
            600.0,
            "V",
            source_id="alpha-461219",
            locator="Product specifications > voltage rating > 600 V",
        ),
        _unknown("mass_g", "MISSING_MASS", "alpha-461219"),
        _unknown("current_continuous_a", "MISSING_CURRENT_LIMIT", "alpha-461219"),
        _unknown("revision", "MISSING_REVISION", "alpha-461219"),
    ),
    ("twelve-awg", "stranded-tinned-copper", "six-hundred-v"),
)


POLOLU_950_FASTENER_SCOPE = _entry(
    "pololu-caster-950-included-fasteners",
    "Pololu",
    "#950",
    "included #2 screws/nuts; separate fastener MPN unpublished",
    "fastener",
    (
        _text(
            "mount_pattern",
            "included #2 screws and nuts for ball caster",
            source_id="caster-950-specs",
            locator="Included hardware > two #2 screws and nuts",
        ),
        _unknown("mass_g", "MISSING_MASS", "caster-950-specs"),
        _unknown("revision", "MISSING_REVISION", "caster-950-specs"),
    ),
    ("caster-hardware", "mpn-unpublished"),
)


SPIROL_151332 = _entry(
    "spirol-insert-151332",
    "SPIROL",
    "151332",
    "Series 10 M3x0.5 self-tapping thread-forming insert",
    "insert",
    (
        _numeric(
            "envelope_x_mm",
            4.78,
            "mm",
            source_id="spirol-151332",
            locator="Product specifications > outside diameter > 4.78 mm",
        ),
        _numeric(
            "envelope_y_mm",
            4.78,
            "mm",
            source_id="spirol-151332",
            locator="Product specifications > outside diameter > 4.78 mm",
        ),
        _numeric(
            "envelope_z_mm",
            6.35,
            "mm",
            source_id="spirol-151332",
            locator="Product specifications > length > 6.35 mm",
        ),
        _numeric(
            "mass_g",
            0.388,
            "g",
            source_id="spirol-151332",
            locator="Product specifications > weight > 0.388 g",
        ),
        _text(
            "mount_pattern",
            "M3x0.5 self-tapping insert",
            source_id="spirol-151332",
            locator="Product identity > M3x0.5",
        ),
        _unknown("revision", "MISSING_REVISION", "spirol-151332"),
    ),
    ("m3x05", "brass", "thread-forming"),
)


ESSENTRA_13RS018725 = _entry(
    "essentra-spacer-13rs018725",
    "Essentra Components",
    "13RS018725",
    "round unthreaded PA spacer, M3, 7.9 mm length",
    "spacer",
    (
        _numeric(
            "envelope_x_mm",
            4.7,
            "mm",
            source_id="essentra-13rs018725",
            locator="Product specifications > outside diameter > 4.7 mm",
        ),
        _numeric(
            "envelope_y_mm",
            4.7,
            "mm",
            source_id="essentra-13rs018725",
            locator="Product specifications > outside diameter > 4.7 mm",
        ),
        _numeric(
            "envelope_z_mm",
            7.9,
            "mm",
            source_id="essentra-13rs018725",
            locator="Product specifications > length > 7.9 mm",
        ),
        _text(
            "mount_pattern",
            "M3 clearance, 3 mm ID",
            source_id="essentra-13rs018725",
            locator="Product specifications > bore > 3 mm",
        ),
        _unknown("mass_g", "MISSING_MASS", "essentra-13rs018725"),
        _unknown("revision", "MISSING_REVISION", "essentra-13rs018725"),
    ),
    ("m3", "nylon-6-6", "unthreaded"),
)


REFERENCE_STACK_CATALOG = CatalogSnapshot(
    catalog_version=REFERENCE_STACK_CATALOG_VERSION,
    sources=tuple(sorted(_SOURCE_BY_ID.values(), key=lambda item: item.source_id)),
    entries=(
        CORES3_K128,
        POLOLU_WHEEL_1087,
        POLOLU_CASTER_950,
        ROBOTIS_XL430_W250_T,
        ROBOTIS_HN11_N101,
        ROBOTIS_TB3_WHEEL_ISW01,
        POLOLU_4869,
        POLOLU_TB9051FTG,
        ROBOTIS_MKR_SHIELD,
        BIOENNO_BLF1206A,
        BIOENNO_BPC1502DC,
        POLOLU_D24V50F5,
        LITTELFUSE_ATOF_0287020,
        LITTELFUSE_ATO_HOLDER,
        BLUESEA_6006,
        TE_SR6B4012,
        SCHNEIDER_XB5AS8442,
        ANDERSON_PP30_HOUSING_1327,
        ANDERSON_PP30_CONTACT_1331,
        ROBOTIS_ROBOT_CABLE_X3P,
        ALPHA_461219,
        POLOLU_950_FASTENER_SCOPE,
        SPIROL_151332,
        ESSENTRA_13RS018725,
    ),
)


_SELECTIONS = (
    StackSelection(
        selection_id="controller",
        role="controller",
        entry_id=CORES3_K128.entry_id,
        manufacturer=CORES3_K128.manufacturer,
        manufacturer_sku=CORES3_K128.manufacturer_sku,
        variant=CORES3_K128.variant,
        catalog_use="controller_isolated",
        quantity=1,
        package_scope="CoreS3 K128 main unit with DinBase whole-set context; controller branch only",
        evidence_source_ids=("cores3-page", "cores3-schematic"),
        evidence_basis=("manufacturer_stated", "mixed_known_and_unknown"),
        planning_assumption_ids=("pa-cores3-controller-branch",),
    ),
    StackSelection(
        selection_id="drive-motors",
        role="drive_motor",
        entry_id=POLOLU_4869.entry_id,
        manufacturer=POLOLU_4869.manufacturer,
        manufacturer_sku=POLOLU_4869.manufacturer_sku,
        variant=POLOLU_4869.variant,
        catalog_use="motor_drive",
        quantity=2,
        package_scope="two wheel-drive gearmotors, one per side",
        evidence_source_ids=("pololu-4869-specs", "pololu-25d-datasheet"),
        evidence_basis=("manufacturer_stated", "mixed_known_and_unknown"),
        planning_assumption_ids=("pa-wheel-shaft-adapter",),
    ),
    StackSelection(
        selection_id="wheel-hubs",
        role="wheel_hub",
        entry_id=POLOLU_WHEEL_1087.entry_id,
        manufacturer=POLOLU_WHEEL_1087.manufacturer,
        manufacturer_sku=POLOLU_WHEEL_1087.manufacturer_sku,
        variant=POLOLU_WHEEL_1087.variant,
        catalog_use="wheel_drive",
        quantity=1,
        package_scope="one two-wheel pair",
        evidence_source_ids=("wheel-1087-specs", "wheel-1087-drawing"),
        evidence_basis=("manufacturer_stated_with_conversions",),
        planning_assumption_ids=("pa-wheel-shaft-adapter",),
    ),
    StackSelection(
        selection_id="head-actuators",
        role="head_actuator",
        entry_id=ROBOTIS_XL430_W250_T.entry_id,
        manufacturer=ROBOTIS_XL430_W250_T.manufacturer,
        manufacturer_sku=ROBOTIS_XL430_W250_T.manufacturer_sku,
        variant=ROBOTIS_XL430_W250_T.variant,
        catalog_use="head_servo",
        quantity=2,
        package_scope="two head/flipper actuators",
        evidence_source_ids=("robotis-xl430-manual",),
        evidence_basis=("manufacturer_stated", "mixed_known_and_unknown"),
        planning_assumption_ids=(
            "pa-robotis-commercial-mpn",
            "pa-xl430-head-duty",
        ),
    ),
    StackSelection(
        selection_id="head-horns",
        role="head_horn",
        entry_id=ROBOTIS_HN11_N101.entry_id,
        manufacturer=ROBOTIS_HN11_N101.manufacturer,
        manufacturer_sku=ROBOTIS_HN11_N101.manufacturer_sku,
        variant=ROBOTIS_HN11_N101.variant,
        catalog_use="head_horn",
        quantity=2,
        package_scope="one assembled horn per head actuator",
        evidence_source_ids=("robotis-xl430-manual",),
        evidence_basis=("manufacturer_stated", "mixed_known_and_unknown"),
        planning_assumption_ids=("pa-xl430-horn-fit",),
    ),
    StackSelection(
        selection_id="caster",
        role="caster",
        entry_id=POLOLU_CASTER_950.entry_id,
        manufacturer=POLOLU_CASTER_950.manufacturer,
        manufacturer_sku=POLOLU_CASTER_950.manufacturer_sku,
        variant=POLOLU_CASTER_950.variant,
        catalog_use="caster",
        quantity=1,
        package_scope="body-only caster geometry; included hardware selected separately",
        evidence_source_ids=("caster-950-specs", "caster-950-drawing"),
        evidence_basis=("manufacturer_stated_with_conversions",),
    ),
    StackSelection(
        selection_id="battery",
        role="battery",
        entry_id=BIOENNO_BLF1206A.entry_id,
        manufacturer=BIOENNO_BLF1206A.manufacturer,
        manufacturer_sku=BIOENNO_BLF1206A.manufacturer_sku,
        variant=BIOENNO_BLF1206A.variant,
        catalog_use="battery",
        quantity=1,
        package_scope="one protected actuator battery",
        evidence_source_ids=("bioenno-blf1206a",),
        evidence_basis=(
            "manufacturer_stated",
            "manufacturer_stated_with_conversions",
            "mixed_known_and_unknown",
        ),
        planning_assumption_ids=("pa-bioenno-pp30-mating",),
    ),
    StackSelection(
        selection_id="charger",
        role="charger",
        entry_id=BIOENNO_BPC1502DC.entry_id,
        manufacturer=BIOENNO_BPC1502DC.manufacturer,
        manufacturer_sku=BIOENNO_BPC1502DC.manufacturer_sku,
        variant=BIOENNO_BPC1502DC.variant,
        catalog_use="charger",
        quantity=1,
        package_scope="one AC-to-DC LiFePO4 charger; not part of energized robot topology",
        evidence_source_ids=("bioenno-bpc1502dc", "bioenno-bpc1502dc-us"),
        evidence_basis=("manufacturer_stated", "mixed_known_and_unknown"),
        active=False,
    ),
    StackSelection(
        selection_id="controller-regulator-fallback",
        role="regulator",
        entry_id=POLOLU_D24V50F5.entry_id,
        manufacturer=POLOLU_D24V50F5.manufacturer,
        manufacturer_sku=POLOLU_D24V50F5.manufacturer_sku,
        variant=POLOLU_D24V50F5.variant,
        catalog_use="regulator",
        quantity=1,
        package_scope="fallback only; not energized while CoreS3 internal battery branch is retained",
        evidence_source_ids=("pololu-2851-specs",),
        evidence_basis=("manufacturer_stated", "mixed_known_and_unknown"),
        active=False,
    ),
    StackSelection(
        selection_id="dynamixel-interface",
        role="actuator_interface",
        entry_id=ROBOTIS_MKR_SHIELD.entry_id,
        manufacturer=ROBOTIS_MKR_SHIELD.manufacturer,
        manufacturer_sku=ROBOTIS_MKR_SHIELD.manufacturer_sku,
        variant=ROBOTIS_MKR_SHIELD.variant,
        catalog_use="motor_driver",
        quantity=1,
        package_scope="one TTL DYNAMIXEL interface board for head branch",
        evidence_source_ids=(
            "robotis-mkr-docs",
            "robotis-mkr-emanual",
        ),
        evidence_basis=("manufacturer_stated", "mixed_known_and_unknown"),
        planning_assumption_ids=(
            "pa-mkr-cores3-endpoint",
            "pa-robotis-commercial-mpn",
        ),
    ),
    StackSelection(
        selection_id="wheel-motor-driver",
        role="motor_driver",
        entry_id=POLOLU_TB9051FTG.entry_id,
        manufacturer=POLOLU_TB9051FTG.manufacturer,
        manufacturer_sku=POLOLU_TB9051FTG.manufacturer_sku,
        variant=POLOLU_TB9051FTG.variant,
        catalog_use="motor_driver",
        quantity=1,
        package_scope="one dual-channel driver for two wheel motors",
        evidence_source_ids=("pololu-2520-specs",),
        evidence_basis=("manufacturer_stated", "mixed_known_and_unknown"),
    ),
    StackSelection(
        selection_id="actuator-fuse",
        role="fuse",
        entry_id=LITTELFUSE_ATOF_0287020.entry_id,
        manufacturer=LITTELFUSE_ATOF_0287020.manufacturer,
        manufacturer_sku=LITTELFUSE_ATOF_0287020.manufacturer_sku,
        variant=LITTELFUSE_ATOF_0287020.variant,
        catalog_use="protection",
        quantity=1,
        package_scope="one positive actuator-branch fuse candidate",
        evidence_source_ids=("littelfuse-0287020",),
        evidence_basis=("manufacturer_stated",),
        planning_assumption_ids=("pa-fuse-inrush-coordination",),
    ),
    StackSelection(
        selection_id="actuator-fuse-holder",
        role="fuse_holder",
        entry_id=LITTELFUSE_ATO_HOLDER.entry_id,
        manufacturer=LITTELFUSE_ATO_HOLDER.manufacturer,
        manufacturer_sku=LITTELFUSE_ATO_HOLDER.manufacturer_sku,
        variant=LITTELFUSE_ATO_HOLDER.variant,
        catalog_use="protection",
        quantity=1,
        package_scope="one inline ATO holder with 12 AWG leads",
        evidence_source_ids=("littelfuse-ato-holder",),
        evidence_basis=("manufacturer_stated", "mixed_known_and_unknown"),
        planning_assumption_ids=("pa-fuse-inrush-coordination",),
    ),
    StackSelection(
        selection_id="main-switch",
        role="main_switch",
        entry_id=BLUESEA_6006.entry_id,
        manufacturer=BLUESEA_6006.manufacturer,
        manufacturer_sku=BLUESEA_6006.manufacturer_sku,
        variant=BLUESEA_6006.variant,
        catalog_use="main_switch",
        quantity=1,
        package_scope="one manual battery isolation switch; not the E-stop contact",
        evidence_source_ids=("bluesea-6006", "bluesea-6006-drawing"),
        evidence_basis=(
            "manufacturer_stated",
            "manufacturer_stated_with_conversions",
            "mixed_known_and_unknown",
        ),
    ),
    StackSelection(
        selection_id="force-guided-relay",
        role="force_guided_relay",
        entry_id=TE_SR6B4012.entry_id,
        manufacturer=TE_SR6B4012.manufacturer,
        manufacturer_sku=TE_SR6B4012.manufacturer_sku,
        variant=TE_SR6B4012.variant,
        catalog_use="protection",
        quantity=2,
        package_scope="two relays, one independent NO contact set per drive/head branch",
        evidence_source_ids=("te-sr6-product", "te-sr6-datasheet"),
        evidence_basis=("manufacturer_stated", "mixed_known_and_unknown"),
        planning_assumption_ids=("pa-sr6-electronic-load",),
    ),
    StackSelection(
        selection_id="physical-estop",
        role="physical_estop",
        entry_id=SCHNEIDER_XB5AS8442.entry_id,
        manufacturer=SCHNEIDER_XB5AS8442.manufacturer,
        manufacturer_sku=SCHNEIDER_XB5AS8442.manufacturer_sku,
        variant=SCHNEIDER_XB5AS8442.variant,
        catalog_use="e_stop",
        quantity=1,
        package_scope="one physical turn-to-release mushroom NC control contact",
        evidence_source_ids=("schneider-xb5as8442",),
        evidence_basis=("manufacturer_stated", "mixed_known_and_unknown"),
        planning_assumption_ids=("pa-estop-coil-path",),
    ),
    StackSelection(
        selection_id="battery-connector-housing",
        role="battery_connector_housing",
        entry_id=ANDERSON_PP30_HOUSING_1327.entry_id,
        manufacturer=ANDERSON_PP30_HOUSING_1327.manufacturer,
        manufacturer_sku=ANDERSON_PP30_HOUSING_1327.manufacturer_sku,
        variant=ANDERSON_PP30_HOUSING_1327.variant,
        catalog_use="connector",
        quantity=1,
        package_scope="one PP30 red housing; battery-side color/mating endpoint unresolved",
        evidence_source_ids=("anderson-1327", "anderson-pp30-datasheet"),
        evidence_basis=("manufacturer_stated", "mixed_known_and_unknown"),
        planning_assumption_ids=("pa-bioenno-pp30-mating",),
    ),
    StackSelection(
        selection_id="battery-connector-contact",
        role="battery_connector_contact",
        entry_id=ANDERSON_PP30_CONTACT_1331.entry_id,
        manufacturer=ANDERSON_PP30_CONTACT_1331.manufacturer,
        manufacturer_sku=ANDERSON_PP30_CONTACT_1331.manufacturer_sku,
        variant=ANDERSON_PP30_CONTACT_1331.variant,
        catalog_use="connector",
        quantity=2,
        package_scope="two 16-12 AWG PP30 contacts",
        evidence_source_ids=("anderson-1331-bk", "anderson-pp30-datasheet"),
        evidence_basis=("manufacturer_stated", "mixed_known_and_unknown"),
        planning_assumption_ids=("pa-bioenno-pp30-mating",),
    ),
    StackSelection(
        selection_id="actuator-cables",
        role="actuator_cable",
        entry_id=ROBOTIS_ROBOT_CABLE_X3P.entry_id,
        manufacturer=ROBOTIS_ROBOT_CABLE_X3P.manufacturer,
        manufacturer_sku=ROBOTIS_ROBOT_CABLE_X3P.manufacturer_sku,
        variant=ROBOTIS_ROBOT_CABLE_X3P.variant,
        catalog_use="connector",
        quantity=4,
        package_scope="one TTL cable per XL430 actuator; exact connector MPN not published",
        evidence_source_ids=("robotis-xl430-manual",),
        evidence_basis=("manufacturer_stated", "mixed_known_and_unknown"),
        planning_assumption_ids=("pa-mkr-cores3-endpoint",),
    ),
    StackSelection(
        selection_id="actuator-wire",
        role="wire",
        entry_id=ALPHA_461219.entry_id,
        manufacturer=ALPHA_461219.manufacturer,
        manufacturer_sku=ALPHA_461219.manufacturer_sku,
        variant=ALPHA_461219.variant,
        catalog_use="wire",
        quantity=1,
        package_scope="12 AWG high-current branch wire candidate; cut lengths not selected",
        evidence_source_ids=("alpha-461219",),
        evidence_basis=("manufacturer_stated", "mixed_known_and_unknown"),
        planning_assumption_ids=("pa-wire-ampacity",),
    ),
    StackSelection(
        selection_id="fasteners",
        role="fastener",
        entry_id=POLOLU_950_FASTENER_SCOPE.entry_id,
        manufacturer=POLOLU_950_FASTENER_SCOPE.manufacturer,
        manufacturer_sku=POLOLU_950_FASTENER_SCOPE.manufacturer_sku,
        variant=POLOLU_950_FASTENER_SCOPE.variant,
        catalog_use="fastener",
        quantity=1,
        package_scope="caster included screw/nut package; separate MPN and mass unknown",
        evidence_source_ids=("caster-950-specs",),
        evidence_basis=("manufacturer_stated", "mixed_known_and_unknown"),
        planning_assumption_ids=("pa-chassis-fastener-strength",),
    ),
    StackSelection(
        selection_id="chassis-inserts",
        role="insert",
        entry_id=SPIROL_151332.entry_id,
        manufacturer=SPIROL_151332.manufacturer,
        manufacturer_sku=SPIROL_151332.manufacturer_sku,
        variant=SPIROL_151332.variant,
        catalog_use="insert",
        quantity=4,
        package_scope="four M3 chassis inserts, count provisional",
        evidence_source_ids=("spirol-151332",),
        evidence_basis=("manufacturer_stated", "mixed_known_and_unknown"),
        planning_assumption_ids=("pa-chassis-fastener-strength",),
    ),
    StackSelection(
        selection_id="chassis-spacers",
        role="spacer",
        entry_id=ESSENTRA_13RS018725.entry_id,
        manufacturer=ESSENTRA_13RS018725.manufacturer,
        manufacturer_sku=ESSENTRA_13RS018725.manufacturer_sku,
        variant=ESSENTRA_13RS018725.variant,
        catalog_use="spacer",
        quantity=4,
        package_scope="four M3 spacers, count provisional",
        evidence_source_ids=("essentra-13rs018725",),
        evidence_basis=("manufacturer_stated", "mixed_known_and_unknown"),
        planning_assumption_ids=("pa-chassis-fastener-strength",),
    ),
)


_ASSUMPTIONS = (
    PlanningAssumption(
        assumption_id="pa-cores3-controller-branch",
        statement="Keep CoreS3 on its internal battery or another separately documented controller supply; never use its unknown rail as actuator power.",
        basis="source_gap",
        status="accepted_for_planning",
        related_selection_ids=("controller",),
    ),
    PlanningAssumption(
        assumption_id="pa-wheel-shaft-adapter",
        statement="A future mechanical adapter may be required because the selected Pololu wheel has a 3 mm D interface while motor #4869 publishes a 4 mm D shaft.",
        basis="source_gap",
        status="open",
        related_selection_ids=("drive-motors", "wheel-hubs"),
    ),
    PlanningAssumption(
        assumption_id="pa-xl430-head-duty",
        statement="XL430 stall/no-load values are planning bounds only until a manufacturer continuous-duty/thermal specification is available.",
        basis="source_gap",
        status="open",
        related_selection_ids=("head-actuators",),
    ),
    PlanningAssumption(
        assumption_id="pa-xl430-voltage-limit",
        statement="Do not connect the 14.6 V charger/battery charge upper bound directly to XL430; a future active regulator or source change must keep the actuator bus at or below the documented 12.0 V maximum.",
        basis="source_gap",
        status="open",
        related_selection_ids=(
            "charger",
            "head-actuators",
            "controller-regulator-fallback",
        ),
    ),
    PlanningAssumption(
        assumption_id="pa-robotis-commercial-mpn",
        statement="The selected ROBOTIS commercial MPNs remain provisional until a usable product-specific official page or equivalent manufacturer identity record is available; the observed shop responses were generic HTML.",
        basis="source_gap",
        status="open",
        related_selection_ids=("head-actuators", "dynamixel-interface"),
    ),
    PlanningAssumption(
        assumption_id="pa-xl430-horn-fit",
        statement="The HN11-N101 horn is treated as the intended XL430 horn package, but its hole geometry must be verified from an official drawing or physically.",
        basis="source_gap",
        status="open",
        related_selection_ids=("head-horns",),
    ),
    PlanningAssumption(
        assumption_id="pa-bioenno-pp30-mating",
        statement="The actuator battery branch will use an Anderson PP30 mating assembly only after the Bioenno battery-side housing/contact MPN and gauge are identified.",
        basis="source_gap",
        status="open",
        related_selection_ids=(
            "battery",
            "battery-connector-housing",
            "battery-connector-contact",
        ),
    ),
    PlanningAssumption(
        assumption_id="pa-mkr-cores3-endpoint",
        statement="The CoreS3-to-MKR UART/TTL endpoint and level path must be explicitly documented before the DYNAMIXEL interface is treated as connected.",
        basis="source_gap",
        status="open",
        related_selection_ids=("dynamixel-interface", "actuator-cables"),
    ),
    PlanningAssumption(
        assumption_id="pa-fuse-inrush-coordination",
        statement="The 20 A ATOF fuse and holder remain provisional until actuator-bus inrush, wire ampacity, and fault-clearing coordination are documented.",
        basis="source_gap",
        status="open",
        related_selection_ids=("actuator-fuse", "actuator-fuse-holder"),
    ),
    PlanningAssumption(
        assumption_id="pa-sr6-electronic-load",
        statement="SR6 NO contacts are not accepted for the electronic DYNAMIXEL load until an official DC electronic-load/inrush class or a physical test closes the gate.",
        basis="source_gap",
        status="open",
        related_selection_ids=("force-guided-relay",),
    ),
    PlanningAssumption(
        assumption_id="pa-estop-coil-path",
        statement="The Schneider NC contact will interrupt only the SR6 12 V coil/control path; it will not carry motor or servo current.",
        basis="design_choice",
        status="accepted_for_planning",
        related_selection_ids=("physical-estop", "force-guided-relay"),
    ),
    PlanningAssumption(
        assumption_id="pa-wire-ampacity",
        statement="Use only wire ampacity and termination data from the selected wire/connector manufacturers; do not infer ampacity from AWG alone.",
        basis="source_gap",
        status="open",
        related_selection_ids=("actuator-wire",),
    ),
    PlanningAssumption(
        assumption_id="pa-chassis-fastener-strength",
        statement="Chassis substrate, insert pull-out, fastener strength, and mounting torque remain physical design inputs, not catalog defaults.",
        basis="source_gap",
        status="open",
        related_selection_ids=("fasteners", "chassis-inserts", "chassis-spacers"),
    ),
)


_GATES = (
    UnresolvedGate(
        gate_id="missing-cores3-power-endpoint",
        stage="digital",
        target="datasheet_eligible",
        description="CoreS3 bare mass, rail/current limits, exact mating endpoint, revision, and documented power isolation are not published as a complete controller interface.",
        related_selection_ids=("controller",),
        related_fact_keys=(
            "mass_g",
            "current_limit_a",
            "rail_current_limit_a",
            "connector_mpn",
            "power_isolation",
            "revision",
        ),
    ),
    UnresolvedGate(
        gate_id="missing-xl430-continuous-duty",
        stage="digital",
        target="datasheet_eligible",
        description="XL430 continuous current/torque/speed and duty/thermal values are absent; stall and no-load values cannot be promoted.",
        related_selection_ids=("head-actuators",),
        related_fact_keys=(
            "current_continuous_a",
            "torque_continuous_nm",
            "speed_max_rpm",
        ),
    ),
    UnresolvedGate(
        gate_id="head-actuator-voltage-incompatibility",
        stage="digital",
        target="datasheet_eligible",
        description="The selected 14.6 V charger output can appear on the battery charge path while XL430 input is specified for a maximum of 12.0 V; no active regulator or alternate source currently limits the head actuator bus.",
        related_selection_ids=(
            "charger",
            "controller-regulator-fallback",
            "head-actuators",
        ),
        related_fact_keys=(
            "operating_voltage_nominal_v",
            "operating_voltage_max_v",
        ),
    ),
    UnresolvedGate(
        gate_id="missing-xl430-horn-geometry",
        stage="digital",
        target="datasheet_eligible",
        description="HN11-N101 envelope, mass, and mounting-hole geometry are not present in the reviewed official XL430 source.",
        related_selection_ids=("head-horns",),
        related_fact_keys=(
            "envelope_x_mm",
            "envelope_y_mm",
            "envelope_z_mm",
            "mass_g",
            "mount_pattern",
        ),
    ),
    UnresolvedGate(
        gate_id="missing-mkr-rating-and-endpoint",
        stage="digital",
        target="datasheet_eligible",
        description="MKR board current/thermal rating and CoreS3 mating endpoint are unpublished; official connector and logic-level documents conflict.",
        related_selection_ids=("dynamixel-interface", "actuator-cables"),
        related_fact_keys=(
            "current_continuous_a",
            "current_peak_a",
            "thermal_limit_c",
            "connector_mpn",
            "revision",
        ),
    ),
    UnresolvedGate(
        gate_id="missing-robotis-commercial-mpn-evidence",
        stage="digital",
        target="datasheet_eligible",
        description="The observed ROBOTIS shop responses for XL430 and MKR were generic HTML with no usable product-specific evidence; their selected commercial MPN mappings remain open even though the e-Manual/docs identify the product variants.",
        related_selection_ids=("head-actuators", "dynamixel-interface"),
        related_fact_keys=("revision",),
    ),
    UnresolvedGate(
        gate_id="missing-bioenno-pp30-mating-mpn",
        stage="digital",
        target="datasheet_eligible",
        description="Bioenno identifies PP30 only as a family; the battery-side mating housing/contact MPN and conductor gauge are not published.",
        related_selection_ids=(
            "battery",
            "battery-connector-housing",
            "battery-connector-contact",
        ),
        related_fact_keys=("connector_mpn",),
    ),
    UnresolvedGate(
        gate_id="missing-fuse-inrush-coordination",
        stage="digital",
        target="datasheet_eligible",
        description="Fuse time-current coordination, actuator-bus inrush, holder thermal behavior, and fault clearing are not established from the selected manufacturer documents.",
        related_selection_ids=("actuator-fuse", "actuator-fuse-holder"),
        related_fact_keys=("current_continuous_a",),
    ),
    UnresolvedGate(
        gate_id="missing-wire-ampacity",
        stage="digital",
        target="datasheet_eligible",
        description="The selected Alpha 12 AWG wire publishes gauge and voltage but no usable continuous ampacity for this duty.",
        related_selection_ids=("actuator-wire",),
        related_fact_keys=("current_continuous_a",),
    ),
    UnresolvedGate(
        gate_id="missing-sr6-electronic-load-class",
        stage="digital",
        target="datasheet_eligible",
        description="SR6 publishes 8 A at 250 VAC for contacts and a 12 VDC coil, but no 12 VDC electronic actuator-contact or inrush class; suitability for DYNAMIXEL bus capacitance remains unknown.",
        related_selection_ids=("force-guided-relay",),
        related_fact_keys=("current_limit_a", "operating_voltage_max_v"),
    ),
    UnresolvedGate(
        gate_id="missing-fastener-identity",
        stage="digital",
        target="datasheet_eligible",
        description="The caster package publishes included #2 screws/nuts but no separate fastener MPN, mass, or revision.",
        related_selection_ids=("fasteners",),
        related_fact_keys=("mass_g", "revision"),
    ),
    UnresolvedGate(
        gate_id="wheel-shaft-adapter-unresolved",
        stage="digital",
        target="datasheet_eligible",
        description="The selected wheel has a 3 mm D hole while the selected #4869 motor has a 4 mm D output shaft; no exact adapter is selected.",
        related_selection_ids=("drive-motors", "wheel-hubs"),
        related_fact_keys=("shaft_diameter_mm", "shaft_profile"),
    ),
    UnresolvedGate(
        gate_id="physical-180mm-envelope",
        stage="physical",
        target="physically_qualified",
        description="The assembled two-wheel character robot must be measured against the 180 mm overall envelope after placement and hardware are fixed.",
        related_selection_ids=("drive-motors", "wheel-hubs", "caster", "battery"),
    ),
    UnresolvedGate(
        gate_id="physical-estop-interruption",
        stage="physical",
        target="physically_qualified",
        description="Physical test must show both actuator branches de-energize on the mushroom E-stop while CoreS3 remains powered and reports the fault.",
        related_selection_ids=("physical-estop", "force-guided-relay", "controller"),
    ),
    UnresolvedGate(
        gate_id="physical-inrush-thermal-duty",
        stage="physical",
        target="physically_qualified",
        description="Inrush, fuse clearing, relay contact stress, wire temperature, and continuous low-speed duty require a physical test plan and observation.",
        related_selection_ids=(
            "battery",
            "actuator-fuse",
            "force-guided-relay",
            "actuator-wire",
        ),
    ),
    UnresolvedGate(
        gate_id="physical-chassis-strength",
        stage="physical",
        target="physically_qualified",
        description="Chassis substrate, insert pull-out, spacer compression, fastener torque, and caster load must be physically qualified.",
        related_selection_ids=(
            "fasteners",
            "chassis-inserts",
            "chassis-spacers",
            "caster",
        ),
    ),
)


_CALCULATIONS = (
    StackCalculation(
        calculation_id="xl430-head-stall-current",
        operation="sum_quantity_weighted",
        expression="2 head actuators x 1.4 A published XL430 stall current",
        value=2.8,
        unit="A",
        basis="published_values_only",
        inputs=(
            StackFactRef(
                entry_id=ROBOTIS_XL430_W250_T.entry_id, fact_key="current_stall_a"
            ),
        ),
        input_selection_ids=("head-actuators",),
        note="Stall is a peak/worst-case planning bound, not a continuous-duty rating.",
    ),
    StackCalculation(
        calculation_id="pololu-drive-stall-current",
        operation="sum_quantity_weighted",
        expression="2 wheel motors x 1.8 A published extrapolated stall current",
        value=3.6,
        unit="A",
        basis="published_values_only",
        inputs=(
            StackFactRef(entry_id=POLOLU_4869.entry_id, fact_key="current_stall_a"),
        ),
        input_selection_ids=("drive-motors",),
        note="Pololu labels this stall current extrapolated; continuous current remains unknown.",
    ),
    StackCalculation(
        calculation_id="actuator-stall-current-total",
        operation="sum_quantity_weighted",
        expression="2.8 A head stall + 3.6 A wheel stall",
        value=6.4,
        unit="A",
        basis="published_values_only",
        inputs=(
            StackFactRef(
                entry_id=ROBOTIS_XL430_W250_T.entry_id, fact_key="current_stall_a"
            ),
            StackFactRef(entry_id=POLOLU_4869.entry_id, fact_key="current_stall_a"),
        ),
        input_selection_ids=("head-actuators", "drive-motors"),
        note="Composition is a derived bound; it does not assert simultaneous physical stall.",
    ),
    StackCalculation(
        calculation_id="battery-to-stall-current-ratio",
        operation="ratio",
        expression="12 A Bioenno continuous discharge / 6.4 A composed stall bound",
        value=1.875,
        unit="ratio",
        basis="published_values_only",
        inputs=(
            StackFactRef(
                entry_id=BIOENNO_BLF1206A.entry_id, fact_key="current_continuous_a"
            ),
            StackFactRef(
                entry_id=ROBOTIS_XL430_W250_T.entry_id, fact_key="current_stall_a"
            ),
            StackFactRef(entry_id=POLOLU_4869.entry_id, fact_key="current_stall_a"),
        ),
        input_selection_ids=("battery", "head-actuators", "drive-motors"),
        note="Preliminary source-current ratio only; inrush, wire, and thermal coordination remain gates.",
    ),
    StackCalculation(
        calculation_id="indicative-wheel-speed",
        operation="wheel_speed",
        expression="pi x 0.031 m wheel outer diameter x 35 rpm no-load / 60",
        value=0.0568104672,
        unit="m/s",
        basis="published_values_only",
        inputs=(
            StackFactRef(
                entry_id=POLOLU_WHEEL_1087.entry_id, fact_key="outer_diameter_mm"
            ),
            StackFactRef(entry_id=POLOLU_4869.entry_id, fact_key="speed_no_load_rpm"),
        ),
        input_selection_ids=("wheel-hubs", "drive-motors"),
        note="Indicative no-load speed only; loaded nominal speed and physical traction remain unknown.",
    ),
)


_RELAY_RATING_EVIDENCE = RelayRatingEvidence(
    relay_entry_id=TE_SR6B4012.entry_id,
    source_refs=(
        _evidence(
            "te-sr6-brochure",
            "SR6 with 6 contacts > Contact data > rated voltage 250 VAC; rated current 8 A; coil data > rated coil voltage 5 to 110 VDC",
        ),
        _evidence(
            "te-sr6-product",
            "Product Features & Characteristics > Contact Current Rating 8 A; Contact Voltage Rating (VAC) 250; Coil Voltage Rating (VDC) 12",
        ),
    ),
    contact_current_a=8.0,
    contact_voltage_v=250.0,
    contact_voltage_type="AC",
    coil_voltage_v=12.0,
    coil_voltage_type="DC",
    note=(
        "TE publishes 8 A at 250 VAC for the contact and a 12 VDC coil for this exact part. "
        "The intended 12 VDC electronic actuator-contact current/inrush applicability is unknown; "
        "these values are not generic catalog operating-voltage or current-limit facts."
    ),
)


_SOURCE_OBSERVATIONS = tuple(
    SourceObservation(
        source_id=source.source_id,
        digest_kind=(
            "dynamic_html_observation"
            if source.media_type == "text/html"
            else "retrieved_bytes"
        ),
        note=(
            "Dynamic HTML observation: SHA-256 is the exact response bytes observed on 2026-09-04 and may change with server-rendered content."
            + (
                " Product-specific evidence is unavailable because the observed response was a generic site page; no claims may cite it."
                if source.source_id
                in {
                    "robotis-xl430-shop",
                    "robotis-tb3-shop",
                    "robotis-tb3-download",
                    "robotis-mkr-shop",
                }
                else ""
            )
            if source.media_type == "text/html"
            else "Retrieved source bytes: SHA-256 is the exact response bytes observed on 2026-09-04."
        ),
        usable_for_claims=source.source_id
        not in {
            "robotis-xl430-shop",
            "robotis-tb3-shop",
            "robotis-tb3-download",
            "robotis-mkr-shop",
        },
    )
    for source in sorted(_SOURCE_BY_ID.values(), key=lambda item: item.source_id)
)


REFERENCE_STACK = ReferenceStackSnapshot(
    stack_id="cores3-12v-conditional-reference-stack",
    catalog=REFERENCE_STACK_CATALOG,
    selections=_SELECTIONS,
    topology=PowerTopology(
        controller_branch_id="controller-branch",
        actuator_branch_ids=("drive-actuator-branch", "head-actuator-branch"),
        branches=(
            PowerBranch(
                branch_id="controller-branch",
                kind="controller",
                source_entry_id=CORES3_K128.entry_id,
                source_description="CoreS3 internal 500 mAh battery or separately documented controller supply",
                energy_path_entry_ids=(CORES3_K128.entry_id,),
                opens_on_estop=False,
                controller_survives_estop=True,
                independence_basis="planning_assumption",
                planning_assumption_ids=("pa-cores3-controller-branch",),
            ),
            PowerBranch(
                branch_id="drive-actuator-branch",
                kind="actuator_drive",
                source_entry_id=BIOENNO_BLF1206A.entry_id,
                source_description="BLF-1206A 12 V protected battery positive through fuse, switch, SR6 NO-A, and wheel driver",
                energy_path_entry_ids=(
                    BIOENNO_BLF1206A.entry_id,
                    ANDERSON_PP30_HOUSING_1327.entry_id,
                    ANDERSON_PP30_CONTACT_1331.entry_id,
                    ALPHA_461219.entry_id,
                    LITTELFUSE_ATOF_0287020.entry_id,
                    LITTELFUSE_ATO_HOLDER.entry_id,
                    BLUESEA_6006.entry_id,
                    TE_SR6B4012.entry_id,
                    POLOLU_TB9051FTG.entry_id,
                    POLOLU_4869.entry_id,
                ),
                relay_contact="A",
                opens_on_estop=True,
                controller_survives_estop=True,
                independence_basis="planning_assumption",
                planning_assumption_ids=(
                    "pa-fuse-inrush-coordination",
                    "pa-sr6-electronic-load",
                    "pa-estop-coil-path",
                ),
            ),
            PowerBranch(
                branch_id="head-actuator-branch",
                kind="actuator_head",
                source_entry_id=BIOENNO_BLF1206A.entry_id,
                source_description="BLF-1206A 12 V protected battery positive through fuse, switch, SR6 NO-B, MKR interface, and XL430 actuators",
                energy_path_entry_ids=(
                    BIOENNO_BLF1206A.entry_id,
                    ANDERSON_PP30_HOUSING_1327.entry_id,
                    ANDERSON_PP30_CONTACT_1331.entry_id,
                    ALPHA_461219.entry_id,
                    LITTELFUSE_ATOF_0287020.entry_id,
                    LITTELFUSE_ATO_HOLDER.entry_id,
                    BLUESEA_6006.entry_id,
                    TE_SR6B4012.entry_id,
                    ROBOTIS_MKR_SHIELD.entry_id,
                    ROBOTIS_XL430_W250_T.entry_id,
                ),
                relay_contact="B",
                opens_on_estop=True,
                controller_survives_estop=True,
                independence_basis="planning_assumption",
                planning_assumption_ids=(
                    "pa-fuse-inrush-coordination",
                    "pa-sr6-electronic-load",
                    "pa-estop-coil-path",
                    "pa-xl430-voltage-limit",
                ),
            ),
        ),
        estop_selection_id="physical-estop",
        relay_selection_id="force-guided-relay",
        relay_rating_evidence=_RELAY_RATING_EVIDENCE,
        voltage_compatibility_guard=VoltageCompatibilityGuard(
            guard_id="head-actuator-voltage-incompatibility",
            source_selection_id="charger",
            source_entry_id=BIOENNO_BPC1502DC.entry_id,
            source_voltage_fact_key="operating_voltage_nominal_v",
            source_upper_bound_v=14.6,
            load_selection_id="head-actuators",
            load_entry_id=ROBOTIS_XL430_W250_T.entry_id,
            load_voltage_fact_key="operating_voltage_max_v",
            load_upper_bound_v=12.0,
            note="The 14.6 V charge upper bound is above the XL430 12.0 V specified maximum; direct head connection is prohibited until an active regulator or source change is selected and verified.",
        ),
        contacts_are_not_parallel=True,
        controller_branch_independence="planning_assumption",
        estop_control_path="XB5AS8442 1NC opens the two SR6 12 V coil/control circuits; SR6 NO contacts remove drive/head actuator energy while CoreS3 branch remains powered.",
        planning_assumption_ids=(
            "pa-cores3-controller-branch",
            "pa-estop-coil-path",
            "pa-sr6-electronic-load",
            "pa-xl430-voltage-limit",
        ),
    ),
    constraints=StackConstraints(
        max_overall_dimension_mm=180.0,
        max_speed_m_s=0.25,
        note="Indoor low-speed two-wheel character robot; overall fit and speed remain physical gates.",
    ),
    planning_assumptions=tuple(
        sorted(_ASSUMPTIONS, key=lambda item: item.assumption_id)
    ),
    unresolved_gates=tuple(sorted(_GATES, key=lambda item: item.gate_id)),
    calculations=tuple(sorted(_CALCULATIONS, key=lambda item: item.calculation_id)),
    source_observations=_SOURCE_OBSERVATIONS,
)


__all__ = [
    "ALPHA_461219",
    "ANDERSON_PP30_CONTACT_1331",
    "ANDERSON_PP30_HOUSING_1327",
    "BIOENNO_BLF1206A",
    "BIOENNO_BPC1502DC",
    "BLUESEA_6006",
    "CalculationOperation",
    "ESSENTRA_13RS018725",
    "LITTELFUSE_ATOF_0287020",
    "LITTELFUSE_ATO_HOLDER",
    "POLOLU_4869",
    "POLOLU_D24V50F5",
    "POLOLU_950_FASTENER_SCOPE",
    "POLOLU_TB9051FTG",
    "PlanningAssumption",
    "PowerBranch",
    "PowerTopology",
    "RelayRatingEvidence",
    "ReferenceStackReadiness",
    "ReferenceStackSnapshot",
    "REFERENCE_STACK",
    "REFERENCE_STACK_CATALOG",
    "REFERENCE_STACK_CATALOG_VERSION",
    "REFERENCE_STACK_SCHEMA_VERSION",
    "REQUIRED_STACK_ROLES",
    "ROBOTIS_HN11_N101",
    "ROBOTIS_MKR_SHIELD",
    "ROBOTIS_ROBOT_CABLE_X3P",
    "ROBOTIS_TB3_WHEEL_ISW01",
    "ROBOTIS_XL430_W250_T",
    "SCHNEIDER_XB5AS8442",
    "SPIROL_151332",
    "SourceObservation",
    "StackCalculation",
    "StackFactRef",
    "StackSelection",
    "StackRole",
    "StackConstraints",
    "TE_SR6B4012",
    "UnresolvedGate",
    "VoltageFactKey",
    "VoltageCompatibilityGuard",
    "assess_reference_stack",
    "reference_stack_digest",
]
