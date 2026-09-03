from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Literal


Vector3 = tuple[float, float, float]
EvidenceBasis = Literal[
    "manufacturer_spec",
    "physical_measurement",
    "derived_from_measured",
    "planning_allowance",
]
QualificationState = Literal["digital_only", "profile_qualified"]

_SAFE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,95}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9-]*/v[1-9][0-9]*$")
_EVIDENCE_BASES = frozenset(
    {
        "manufacturer_spec",
        "physical_measurement",
        "derived_from_measured",
        "planning_allowance",
    }
)
_QUALIFYING_GEOMETRY_BASES = frozenset(
    {"physical_measurement", "derived_from_measured"}
)


def _require_safe_id(value: str, field: str) -> None:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise ValueError(f"{field} must be a safe identifier")


def _require_finite(value: float, field: str) -> None:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{field} must be finite")


def _require_positive(value: float, field: str) -> None:
    _require_finite(value, field)
    if value <= 0:
        raise ValueError(f"{field} must be positive")


def _require_vector(value: Vector3, field: str, *, positive: bool = False) -> None:
    if not isinstance(value, tuple) or len(value) != 3:
        raise ValueError(f"{field} must contain exactly three coordinates")
    for coordinate in value:
        if positive:
            _require_positive(coordinate, field)
        else:
            _require_finite(coordinate, field)


def _require_unique(values: tuple[str, ...], field: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field} must be unique")
    for value in values:
        _require_safe_id(value, field)


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """Traceable source for one catalog fact.

    The digest identifies the immutable source file or measurement record, not the
    rendered value. Planning numbers without a captured source remain ``None`` at
    the field that consumes evidence; they must not be given a synthetic record.
    """

    evidence_id: str
    basis: EvidenceBasis
    source_ref: str
    sha256: str

    def __post_init__(self) -> None:
        _require_safe_id(self.evidence_id, "evidence_id")
        if self.basis not in _EVIDENCE_BASES:
            raise ValueError("basis is not a supported evidence basis")
        if not self.source_ref.strip():
            raise ValueError("source_ref must not be empty")
        if _SHA256.fullmatch(self.sha256) is None:
            raise ValueError("sha256 must be a lowercase SHA-256 digest")

    @property
    def supports_measured_geometry(self) -> bool:
        return self.basis in _QUALIFYING_GEOMETRY_BASES

    @property
    def is_load_test(self) -> bool:
        return self.basis == "physical_measurement"


@dataclass(frozen=True, slots=True)
class ScalarEvidence:
    value: float | None
    unit: str
    evidence: EvidenceRef | None = None

    def __post_init__(self) -> None:
        if not self.unit.strip():
            raise ValueError("unit must not be empty")
        if self.value is None:
            if self.evidence is not None:
                raise ValueError("unknown scalar values cannot cite evidence")
            return
        _require_finite(self.value, "scalar evidence value")
        if self.value < 0:
            raise ValueError("scalar evidence value cannot be negative")

    @property
    def is_known(self) -> bool:
        return self.value is not None

    @property
    def is_measured(self) -> bool:
        return (
            self.value is not None
            and self.evidence is not None
            and self.evidence.supports_measured_geometry
        )


@dataclass(frozen=True, slots=True)
class VectorEvidence:
    value: Vector3 | None
    unit: str
    evidence: EvidenceRef | None = None

    def __post_init__(self) -> None:
        if not self.unit.strip():
            raise ValueError("unit must not be empty")
        if self.value is None:
            if self.evidence is not None:
                raise ValueError("unknown vector values cannot cite evidence")
            return
        _require_vector(self.value, "vector evidence value")

    @property
    def is_measured(self) -> bool:
        return (
            self.value is not None
            and self.evidence is not None
            and self.evidence.supports_measured_geometry
        )


@dataclass(frozen=True, slots=True)
class CatalogAabb:
    center_mm: Vector3
    size_mm: Vector3
    evidence: EvidenceRef | None = None

    def __post_init__(self) -> None:
        _require_vector(self.center_mm, "AABB center")
        _require_vector(self.size_mm, "AABB size", positive=True)

    @property
    def minimum_mm(self) -> Vector3:
        return tuple(
            center - size / 2
            for center, size in zip(self.center_mm, self.size_mm, strict=True)
        )  # type: ignore[return-value]

    @property
    def maximum_mm(self) -> Vector3:
        return tuple(
            center + size / 2
            for center, size in zip(self.center_mm, self.size_mm, strict=True)
        )  # type: ignore[return-value]

    @property
    def has_measured_geometry(self) -> bool:
        return self.evidence is not None and self.evidence.supports_measured_geometry

    def minimum_containment_clearance_mm(self, inner: CatalogAabb) -> float:
        clearances = tuple(
            value
            for axis in range(3)
            for value in (
                inner.minimum_mm[axis] - self.minimum_mm[axis],
                self.maximum_mm[axis] - inner.maximum_mm[axis],
            )
        )
        return min(clearances)

    def overlaps(self, other: CatalogAabb, *, tolerance_mm: float = 1e-6) -> bool:
        _require_finite(tolerance_mm, "overlap tolerance")
        if tolerance_mm < 0:
            raise ValueError("overlap tolerance cannot be negative")
        return all(
            min(self.maximum_mm[axis], other.maximum_mm[axis])
            - max(self.minimum_mm[axis], other.minimum_mm[axis])
            > tolerance_mm
            for axis in range(3)
        )


@dataclass(frozen=True, slots=True)
class MountingHole:
    hole_id: str
    component_id: str
    center_mm: Vector3
    diameter_mm: float
    evidence: EvidenceRef | None = None

    def __post_init__(self) -> None:
        _require_safe_id(self.hole_id, "hole_id")
        _require_safe_id(self.component_id, "component_id")
        _require_vector(self.center_mm, "mounting-hole center")
        _require_positive(self.diameter_mm, "mounting-hole diameter")

    @property
    def has_measured_geometry(self) -> bool:
        return self.evidence is not None and self.evidence.supports_measured_geometry


@dataclass(frozen=True, slots=True)
class ConnectorPort:
    connector_id: str
    component_id: str
    mating_volume: CatalogAabb
    access_volume: CatalogAabb

    def __post_init__(self) -> None:
        _require_safe_id(self.connector_id, "connector_id")
        _require_safe_id(self.component_id, "component_id")

    @property
    def has_measured_geometry(self) -> bool:
        return (
            self.mating_volume.has_measured_geometry
            and self.access_volume.has_measured_geometry
        )


@dataclass(frozen=True, slots=True)
class CatalogComponent:
    component_id: str
    envelope: CatalogAabb
    mass_g: ScalarEvidence
    mounting_holes: tuple[MountingHole, ...] = ()
    connectors: tuple[ConnectorPort, ...] = ()

    def __post_init__(self) -> None:
        _require_safe_id(self.component_id, "component_id")
        if self.mass_g.value is not None and self.mass_g.value <= 0:
            raise ValueError("known component mass must be positive")
        if any(hole.component_id != self.component_id for hole in self.mounting_holes):
            raise ValueError("mounting hole component IDs must match their owner")
        if any(
            connector.component_id != self.component_id for connector in self.connectors
        ):
            raise ValueError("connector component IDs must match their owner")
        _require_unique(
            tuple(hole.hole_id for hole in self.mounting_holes),
            "mounting-hole IDs",
        )
        _require_unique(
            tuple(connector.connector_id for connector in self.connectors),
            "connector IDs",
        )


@dataclass(frozen=True, slots=True)
class MotionEnvelope:
    motion_id: str
    swept_volume: CatalogAabb

    def __post_init__(self) -> None:
        _require_safe_id(self.motion_id, "motion_id")


@dataclass(frozen=True, slots=True)
class MassProperties:
    complete_assembly_mass_g: ScalarEvidence
    center_of_gravity_mm: VectorEvidence

    def __post_init__(self) -> None:
        if (
            self.complete_assembly_mass_g.value is not None
            and self.complete_assembly_mass_g.value <= 0
        ):
            raise ValueError("known complete assembly mass must be positive")


@dataclass(frozen=True, slots=True)
class PowerProperties:
    minimum_operating_voltage_v: ScalarEvidence
    supply_current_limit_a: ScalarEvidence
    measured_peak_current_a: ScalarEvidence
    observed_min_voltage_v: ScalarEvidence

    def __post_init__(self) -> None:
        values = (
            self.minimum_operating_voltage_v,
            self.supply_current_limit_a,
            self.measured_peak_current_a,
            self.observed_min_voltage_v,
        )
        if any(value.value is not None and value.value <= 0 for value in values):
            raise ValueError("known power values must be positive")


@dataclass(frozen=True, slots=True)
class QualificationPolicy:
    required_component_ids: tuple[str, ...]
    required_hole_ids: tuple[str, ...]
    required_connector_ids: tuple[str, ...]
    required_motion_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.required_component_ids:
            raise ValueError("qualification requires at least one component")
        _require_unique(self.required_component_ids, "required component IDs")
        _require_unique(self.required_hole_ids, "required hole IDs")
        _require_unique(self.required_connector_ids, "required connector IDs")
        _require_unique(self.required_motion_ids, "required motion IDs")


@dataclass(frozen=True, slots=True)
class QualificationCatalog:
    profile_id: str
    catalog_version: str
    declared_state: QualificationState
    components: tuple[CatalogComponent, ...]
    motion_envelopes: tuple[MotionEnvelope, ...]
    mass: MassProperties
    power: PowerProperties
    policy: QualificationPolicy

    def __post_init__(self) -> None:
        if _PROFILE_ID.fullmatch(self.profile_id) is None:
            raise ValueError("profile_id must be a versioned profile ID")
        if self.declared_state not in {"digital_only", "profile_qualified"}:
            raise ValueError("declared_state is not a supported qualification state")
        _require_safe_id(self.catalog_version, "catalog_version")
        _require_unique(
            tuple(component.component_id for component in self.components),
            "component IDs",
        )
        _require_unique(
            tuple(motion.motion_id for motion in self.motion_envelopes),
            "motion IDs",
        )
        _require_unique(
            tuple(
                hole.hole_id
                for component in self.components
                for hole in component.mounting_holes
            ),
            "global mounting-hole IDs",
        )
        _require_unique(
            tuple(
                connector.connector_id
                for component in self.components
                for connector in component.connectors
            ),
            "global connector IDs",
        )


@dataclass(frozen=True, slots=True)
class QualificationAssessment:
    effective_state: QualificationState
    eligible_for_profile_qualification: bool
    missing_or_unqualified: tuple[str, ...]


def assess_profile_qualification(
    catalog: QualificationCatalog,
) -> QualificationAssessment:
    """Evaluate evidence completeness without trusting a declared label.

    A catalog cannot promote itself merely by setting ``declared_state``. The
    effective state is profile-qualified only when every policy item is present,
    all physical geometry and mass properties are traceable to measurements, and
    the power envelope includes a physical peak-load voltage/current observation.
    """

    components = {component.component_id: component for component in catalog.components}
    holes = {
        hole.hole_id: hole
        for component in catalog.components
        for hole in component.mounting_holes
    }
    connectors = {
        connector.connector_id: connector
        for component in catalog.components
        for connector in component.connectors
    }
    motions = {motion.motion_id: motion for motion in catalog.motion_envelopes}
    missing: list[str] = []

    for component_id in catalog.policy.required_component_ids:
        component = components.get(component_id)
        if component is None:
            missing.append(f"component.{component_id}")
            continue
        if not component.envelope.has_measured_geometry:
            missing.append(f"component.{component_id}.envelope")
        if not component.mass_g.is_measured:
            missing.append(f"component.{component_id}.mass")

    for hole_id in catalog.policy.required_hole_ids:
        hole = holes.get(hole_id)
        if hole is None or not hole.has_measured_geometry:
            missing.append(f"mounting_hole.{hole_id}")

    for connector_id in catalog.policy.required_connector_ids:
        connector = connectors.get(connector_id)
        if connector is None or not connector.has_measured_geometry:
            missing.append(f"connector.{connector_id}")

    for motion_id in catalog.policy.required_motion_ids:
        motion = motions.get(motion_id)
        if motion is None or not motion.swept_volume.has_measured_geometry:
            missing.append(f"motion.{motion_id}")

    if not catalog.mass.complete_assembly_mass_g.is_measured:
        missing.append("mass.complete_assembly")
    if not catalog.mass.center_of_gravity_mm.is_measured:
        missing.append("mass.center_of_gravity")

    minimum_voltage = catalog.power.minimum_operating_voltage_v
    supply_limit = catalog.power.supply_current_limit_a
    peak_current = catalog.power.measured_peak_current_a
    observed_voltage = catalog.power.observed_min_voltage_v
    if (
        not minimum_voltage.is_known
        or minimum_voltage.evidence is None
        or minimum_voltage.evidence.basis == "planning_allowance"
    ):
        missing.append("power.minimum_operating_voltage")
    if (
        not supply_limit.is_known
        or supply_limit.evidence is None
        or supply_limit.evidence.basis == "planning_allowance"
    ):
        missing.append("power.supply_current_limit")
    if not peak_current.is_measured or not peak_current.evidence.is_load_test:
        missing.append("power.measured_peak_current")
    if not observed_voltage.is_measured or not observed_voltage.evidence.is_load_test:
        missing.append("power.observed_min_voltage")

    ordered_missing = tuple(sorted(set(missing)))
    eligible = not ordered_missing
    effective_state: QualificationState = (
        "profile_qualified"
        if eligible and catalog.declared_state == "profile_qualified"
        else "digital_only"
    )
    return QualificationAssessment(
        effective_state=effective_state,
        eligible_for_profile_qualification=eligible,
        missing_or_unqualified=ordered_missing,
    )


__all__ = [
    "CatalogAabb",
    "CatalogComponent",
    "ConnectorPort",
    "EvidenceBasis",
    "EvidenceRef",
    "MassProperties",
    "MotionEnvelope",
    "MountingHole",
    "PowerProperties",
    "QualificationAssessment",
    "QualificationCatalog",
    "QualificationPolicy",
    "QualificationState",
    "ScalarEvidence",
    "Vector3",
    "VectorEvidence",
    "assess_profile_qualification",
]
