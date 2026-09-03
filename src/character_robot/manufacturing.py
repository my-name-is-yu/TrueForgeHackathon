from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from .qualification import (
    CatalogAabb,
    QualificationCatalog,
    QualificationState,
    Vector3,
    assess_profile_qualification,
)


Severity = Literal["info", "warning", "error"]
EvidenceLevel = Literal[
    "concept_only", "digital_checks_passed", "within_qualified_profile"
]


def _finite(value: float, field: str) -> None:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{field} must be finite")


def _positive(value: float, field: str, *, allow_zero: bool = False) -> None:
    _finite(value, field)
    if value < 0 or (value == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{field} must be {qualifier}")


def _safe_id(value: str, field: str) -> None:
    if (
        not value
        or not value[0].isalpha()
        or any(
            not (character.islower() or character.isdigit() or character in "_-")
            for character in value
        )
    ):
        raise ValueError(f"{field} must be a safe identifier")


def _unique_ids(values: tuple[str, ...], field: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field} must be unique")
    for value in values:
        _safe_id(value, field)


@dataclass(frozen=True, slots=True)
class ManufacturingRequirements:
    printer_volume_mm: Vector3
    minimum_wall_mm: float
    minimum_fit_clearance_mm: float
    maximum_mass_g: float
    minimum_cog_margin_mm: float
    required_part_ids: tuple[str, ...]
    required_hole_ids: tuple[str, ...]
    required_fit_ids: tuple[str, ...]
    required_component_ids: tuple[str, ...]
    required_connector_ids: tuple[str, ...]
    required_motion_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.printer_volume_mm) != 3:
            raise ValueError("printer_volume_mm must contain three axes")
        for value in self.printer_volume_mm:
            _positive(value, "printer volume")
        _positive(self.minimum_wall_mm, "minimum wall")
        _positive(
            self.minimum_fit_clearance_mm,
            "minimum fit clearance",
            allow_zero=True,
        )
        _positive(self.maximum_mass_g, "maximum mass")
        _positive(
            self.minimum_cog_margin_mm,
            "minimum center-of-gravity margin",
            allow_zero=True,
        )
        if not self.required_part_ids:
            raise ValueError("at least one printable part must be required")
        _unique_ids(self.required_part_ids, "required part IDs")
        _unique_ids(self.required_hole_ids, "required hole IDs")
        _unique_ids(self.required_fit_ids, "required fit IDs")
        _unique_ids(self.required_component_ids, "required component IDs")
        _unique_ids(self.required_connector_ids, "required connector IDs")
        _unique_ids(self.required_motion_ids, "required motion IDs")


@dataclass(frozen=True, slots=True)
class PrintablePartProbe:
    part_id: str
    print_bounds: CatalogAabb
    minimum_wall_mm: float | None
    solid_count: int | None
    is_manifold: bool | None

    def __post_init__(self) -> None:
        _safe_id(self.part_id, "part_id")
        if self.minimum_wall_mm is not None:
            _positive(self.minimum_wall_mm, "observed wall thickness")
        if self.solid_count is not None and (
            isinstance(self.solid_count, bool)
            or not isinstance(self.solid_count, int)
            or self.solid_count < 0
        ):
            raise ValueError("solid_count must be a non-negative integer")
        if self.is_manifold is not None and not isinstance(self.is_manifold, bool):
            raise TypeError("is_manifold must be a boolean")


@dataclass(frozen=True, slots=True)
class HoleProbe:
    hole_id: str
    observed_diameter_mm: float | None
    mating_diameter_mm: float
    maximum_clearance_mm: float | None = None

    def __post_init__(self) -> None:
        _safe_id(self.hole_id, "hole_id")
        if self.observed_diameter_mm is not None:
            _positive(self.observed_diameter_mm, "observed hole diameter")
        _positive(self.mating_diameter_mm, "mating diameter")
        if self.maximum_clearance_mm is not None:
            _positive(
                self.maximum_clearance_mm,
                "maximum hole clearance",
                allow_zero=True,
            )


@dataclass(frozen=True, slots=True)
class FitProbe:
    fit_id: str
    observed_male_size_mm: float | None
    observed_female_size_mm: float | None
    maximum_clearance_mm: float | None = None

    def __post_init__(self) -> None:
        _safe_id(self.fit_id, "fit_id")
        if self.observed_male_size_mm is not None:
            _positive(self.observed_male_size_mm, "observed male size")
        if self.observed_female_size_mm is not None:
            _positive(self.observed_female_size_mm, "observed female size")
        if self.maximum_clearance_mm is not None:
            _positive(
                self.maximum_clearance_mm,
                "maximum fit clearance",
                allow_zero=True,
            )


@dataclass(frozen=True, slots=True)
class ComponentPlacementProbe:
    component_id: str
    cavity: CatalogAabb

    def __post_init__(self) -> None:
        _safe_id(self.component_id, "component_id")


@dataclass(frozen=True, slots=True)
class ConnectorAccessProbe:
    connector_id: str
    obstruction_volumes: tuple[CatalogAabb, ...]

    def __post_init__(self) -> None:
        _safe_id(self.connector_id, "connector_id")


@dataclass(frozen=True, slots=True)
class SweptVolumeProbe:
    motion_id: str
    obstacle_volumes: tuple[CatalogAabb, ...]

    def __post_init__(self) -> None:
        _safe_id(self.motion_id, "motion_id")


@dataclass(frozen=True, slots=True)
class SupportFootprint:
    center_xy_mm: tuple[float, float]
    size_xy_mm: tuple[float, float]

    def __post_init__(self) -> None:
        if len(self.center_xy_mm) != 2 or len(self.size_xy_mm) != 2:
            raise ValueError("support footprint must contain two axes")
        for value in self.center_xy_mm:
            _finite(value, "support-footprint center")
        for value in self.size_xy_mm:
            _positive(value, "support-footprint size")

    def cog_margin_mm(self, center_of_gravity_mm: Vector3) -> float:
        return min(
            size / 2 - abs(cog - center)
            for cog, center, size in zip(
                center_of_gravity_mm[:2],
                self.center_xy_mm,
                self.size_xy_mm,
                strict=True,
            )
        )


@dataclass(frozen=True, slots=True)
class ManufacturingObservations:
    printable_parts: tuple[PrintablePartProbe, ...]
    holes: tuple[HoleProbe, ...]
    fits: tuple[FitProbe, ...]
    component_placements: tuple[ComponentPlacementProbe, ...]
    connector_access: tuple[ConnectorAccessProbe, ...]
    swept_volumes: tuple[SweptVolumeProbe, ...]
    support_footprint: SupportFootprint | None

    def __post_init__(self) -> None:
        collections = (
            (tuple(item.part_id for item in self.printable_parts), "part IDs"),
            (tuple(item.hole_id for item in self.holes), "hole probe IDs"),
            (tuple(item.fit_id for item in self.fits), "fit probe IDs"),
            (
                tuple(item.component_id for item in self.component_placements),
                "component placement IDs",
            ),
            (
                tuple(item.connector_id for item in self.connector_access),
                "connector-access IDs",
            ),
            (
                tuple(item.motion_id for item in self.swept_volumes),
                "swept-volume IDs",
            ),
        )
        for identifiers, field in collections:
            _unique_ids(identifiers, field)


@dataclass(frozen=True, slots=True)
class ManufacturingIssue:
    code: str
    severity: Severity
    subject: str
    message: str
    measured_value: float | None = None
    limit_value: float | None = None
    suggestion: str | None = None


@dataclass(frozen=True, slots=True)
class ManufacturingValidationReport:
    digital_checks_passed: bool
    qualification_state: QualificationState
    within_qualified_profile: bool
    evidence_level: EvidenceLevel
    issues: tuple[ManufacturingIssue, ...]


def _missing_probe(
    issues: list[ManufacturingIssue], code: str, subject: str, label: str
) -> None:
    issues.append(
        ManufacturingIssue(
            code=code,
            severity="error",
            subject=subject,
            message=f"Required {label} evidence is unavailable.",
            suggestion=f"Generate and record the {label} probe before build preparation.",
        )
    )


def _check_parts(
    requirements: ManufacturingRequirements,
    observations: ManufacturingObservations,
    issues: list[ManufacturingIssue],
) -> None:
    parts = {part.part_id: part for part in observations.printable_parts}
    for part_id in sorted(requirements.required_part_ids):
        part = parts.get(part_id)
        if part is None:
            _missing_probe(issues, "printable_part_probe_missing", part_id, "part")
            continue
        if part.solid_count is None:
            _missing_probe(issues, "solid_count_unavailable", part_id, "solid-count")
        elif part.solid_count != 1:
            issues.append(
                ManufacturingIssue(
                    code="printable_part_not_single_solid",
                    severity="error",
                    subject=part_id,
                    message="Each printable part must contain exactly one solid.",
                    measured_value=float(part.solid_count),
                    limit_value=1.0,
                    suggestion="Repair or separate the B-Rep and regenerate the printable part.",
                )
            )
        if part.is_manifold is None:
            _missing_probe(
                issues, "manifold_status_unavailable", part_id, "manifold-status"
            )
        elif not part.is_manifold:
            issues.append(
                ManufacturingIssue(
                    code="printable_part_not_manifold",
                    severity="error",
                    subject=part_id,
                    message="The printable part is not a closed manifold solid.",
                    suggestion="Repair open or non-manifold edges before export.",
                )
            )
        if part.minimum_wall_mm is None:
            _missing_probe(
                issues,
                "wall_thickness_unavailable",
                part_id,
                "wall-thickness",
            )
        elif part.minimum_wall_mm < requirements.minimum_wall_mm:
            issues.append(
                ManufacturingIssue(
                    code="wall_thickness_below_minimum",
                    severity="error",
                    subject=part_id,
                    message="The observed minimum wall is below the material requirement.",
                    measured_value=part.minimum_wall_mm,
                    limit_value=requirements.minimum_wall_mm,
                    suggestion="Thicken the reported region and re-run the B-Rep wall check.",
                )
            )
        for axis, (measured, limit) in enumerate(
            zip(part.print_bounds.size_mm, requirements.printer_volume_mm, strict=True)
        ):
            if measured > limit:
                issues.append(
                    ManufacturingIssue(
                        code="printer_volume_exceeded",
                        severity="error",
                        subject=f"{part_id}.axis_{axis}",
                        message="The part exceeds the printer volume in its declared orientation.",
                        measured_value=measured,
                        limit_value=limit,
                        suggestion="Split or reorient the part, then record new print bounds.",
                    )
                )


def _check_holes_and_fits(
    requirements: ManufacturingRequirements,
    observations: ManufacturingObservations,
    issues: list[ManufacturingIssue],
) -> None:
    holes = {hole.hole_id: hole for hole in observations.holes}
    for hole_id in sorted(requirements.required_hole_ids):
        hole = holes.get(hole_id)
        if hole is None or hole.observed_diameter_mm is None:
            _missing_probe(issues, "hole_probe_missing", hole_id, "hole-diameter")
            continue
        clearance = hole.observed_diameter_mm - hole.mating_diameter_mm
        if clearance < requirements.minimum_fit_clearance_mm:
            issues.append(
                ManufacturingIssue(
                    code="hole_clearance_below_minimum",
                    severity="error",
                    subject=hole_id,
                    message="The hole does not provide the required diametral clearance.",
                    measured_value=clearance,
                    limit_value=requirements.minimum_fit_clearance_mm,
                    suggestion="Increase the hole diameter or select a smaller mating feature.",
                )
            )
        elif (
            hole.maximum_clearance_mm is not None
            and clearance > hole.maximum_clearance_mm
        ):
            issues.append(
                ManufacturingIssue(
                    code="hole_clearance_above_maximum",
                    severity="error",
                    subject=hole_id,
                    message="The hole clearance exceeds the allowed fit range.",
                    measured_value=clearance,
                    limit_value=hole.maximum_clearance_mm,
                    suggestion="Reduce the hole diameter or select a larger mating feature.",
                )
            )

    fits = {fit.fit_id: fit for fit in observations.fits}
    for fit_id in sorted(requirements.required_fit_ids):
        fit = fits.get(fit_id)
        if (
            fit is None
            or fit.observed_male_size_mm is None
            or fit.observed_female_size_mm is None
        ):
            _missing_probe(issues, "fit_probe_missing", fit_id, "fit-clearance")
            continue
        clearance = fit.observed_female_size_mm - fit.observed_male_size_mm
        if clearance < requirements.minimum_fit_clearance_mm:
            issues.append(
                ManufacturingIssue(
                    code="fit_clearance_below_minimum",
                    severity="error",
                    subject=fit_id,
                    message="The mating features do not provide the required clearance.",
                    measured_value=clearance,
                    limit_value=requirements.minimum_fit_clearance_mm,
                    suggestion="Increase the female size or reduce the male size.",
                )
            )
        elif (
            fit.maximum_clearance_mm is not None
            and clearance > fit.maximum_clearance_mm
        ):
            issues.append(
                ManufacturingIssue(
                    code="fit_clearance_above_maximum",
                    severity="error",
                    subject=fit_id,
                    message="The mating features exceed the allowed clearance.",
                    measured_value=clearance,
                    limit_value=fit.maximum_clearance_mm,
                    suggestion="Tighten the mating dimensions and re-run the fit check.",
                )
            )


def _check_components_and_motion(
    catalog: QualificationCatalog,
    requirements: ManufacturingRequirements,
    observations: ManufacturingObservations,
    issues: list[ManufacturingIssue],
) -> None:
    components = {component.component_id: component for component in catalog.components}
    placements = {
        placement.component_id: placement
        for placement in observations.component_placements
    }
    for component_id in sorted(requirements.required_component_ids):
        component = components.get(component_id)
        placement = placements.get(component_id)
        if component is None:
            _missing_probe(
                issues, "catalog_component_missing", component_id, "catalog component"
            )
            continue
        if placement is None:
            _missing_probe(
                issues, "component_placement_missing", component_id, "component cavity"
            )
            continue
        clearance = placement.cavity.minimum_containment_clearance_mm(
            component.envelope
        )
        if clearance < requirements.minimum_fit_clearance_mm:
            issues.append(
                ManufacturingIssue(
                    code="component_clearance_below_minimum",
                    severity="error",
                    subject=component_id,
                    message="The component envelope does not fit its cavity with clearance.",
                    measured_value=clearance,
                    limit_value=requirements.minimum_fit_clearance_mm,
                    suggestion="Enlarge the cavity or move the component.",
                )
            )

    connectors = {
        connector.connector_id: connector
        for component in catalog.components
        for connector in component.connectors
    }
    access_probes = {
        probe.connector_id: probe for probe in observations.connector_access
    }
    for connector_id in sorted(requirements.required_connector_ids):
        connector = connectors.get(connector_id)
        probe = access_probes.get(connector_id)
        if connector is None:
            _missing_probe(
                issues, "catalog_connector_missing", connector_id, "catalog connector"
            )
            continue
        if probe is None:
            _missing_probe(
                issues,
                "connector_access_probe_missing",
                connector_id,
                "connector access",
            )
            continue
        if any(
            connector.access_volume.overlaps(obstruction)
            for obstruction in probe.obstruction_volumes
        ):
            issues.append(
                ManufacturingIssue(
                    code="connector_access_blocked",
                    severity="error",
                    subject=connector_id,
                    message="A static obstruction intersects the connector access volume.",
                    suggestion="Move the obstruction or add an accessible service opening.",
                )
            )

    motions = {motion.motion_id: motion for motion in catalog.motion_envelopes}
    sweep_probes = {probe.motion_id: probe for probe in observations.swept_volumes}
    for motion_id in sorted(requirements.required_motion_ids):
        motion = motions.get(motion_id)
        probe = sweep_probes.get(motion_id)
        if motion is None:
            _missing_probe(
                issues, "catalog_motion_missing", motion_id, "motion envelope"
            )
            continue
        if probe is None:
            _missing_probe(
                issues, "swept_volume_probe_missing", motion_id, "swept-volume"
            )
            continue
        if any(
            motion.swept_volume.overlaps(obstacle)
            for obstacle in probe.obstacle_volumes
        ):
            issues.append(
                ManufacturingIssue(
                    code="swept_volume_interference",
                    severity="error",
                    subject=motion_id,
                    message="The qualified motion envelope intersects a static obstacle.",
                    suggestion="Move the obstacle or reduce the allowed motion range.",
                )
            )


def _check_mass_and_power(
    catalog: QualificationCatalog,
    requirements: ManufacturingRequirements,
    observations: ManufacturingObservations,
    issues: list[ManufacturingIssue],
) -> None:
    mass = catalog.mass.complete_assembly_mass_g.value
    if mass is not None and mass > requirements.maximum_mass_g:
        issues.append(
            ManufacturingIssue(
                code="assembly_mass_exceeded",
                severity="error",
                subject="mass.complete_assembly",
                message="The complete assembly mass exceeds the design limit.",
                measured_value=mass,
                limit_value=requirements.maximum_mass_g,
                suggestion="Reduce mass or raise the explicit design limit.",
            )
        )

    cog = catalog.mass.center_of_gravity_mm.value
    if cog is not None and observations.support_footprint is not None:
        margin = observations.support_footprint.cog_margin_mm(cog)
        if margin < requirements.minimum_cog_margin_mm:
            issues.append(
                ManufacturingIssue(
                    code="center_of_gravity_margin_below_minimum",
                    severity="error",
                    subject="mass.center_of_gravity",
                    message="The center of gravity is outside the required support margin.",
                    measured_value=margin,
                    limit_value=requirements.minimum_cog_margin_mm,
                    suggestion="Move mass inward or widen the wheel support footprint.",
                )
            )
    elif cog is not None:
        _missing_probe(
            issues,
            "support_footprint_missing",
            "mass.center_of_gravity",
            "support-footprint",
        )

    power = catalog.power
    if (
        power.measured_peak_current_a.value is not None
        and power.supply_current_limit_a.value is not None
        and power.measured_peak_current_a.value > power.supply_current_limit_a.value
    ):
        issues.append(
            ManufacturingIssue(
                code="peak_current_exceeds_supply",
                severity="error",
                subject="power.measured_peak_current",
                message="Measured peak current exceeds the supply current limit.",
                measured_value=power.measured_peak_current_a.value,
                limit_value=power.supply_current_limit_a.value,
                suggestion="Use a qualified supply with more current headroom.",
            )
        )
    if (
        power.observed_min_voltage_v.value is not None
        and power.minimum_operating_voltage_v.value is not None
        and power.observed_min_voltage_v.value < power.minimum_operating_voltage_v.value
    ):
        issues.append(
            ManufacturingIssue(
                code="load_voltage_below_minimum",
                severity="error",
                subject="power.observed_min_voltage",
                message="The measured load voltage falls below the operating minimum.",
                measured_value=power.observed_min_voltage_v.value,
                limit_value=power.minimum_operating_voltage_v.value,
                suggestion="Reduce voltage drop or qualify a different supply and wiring path.",
            )
        )


def _check_validation_scope(
    catalog: QualificationCatalog,
    requirements: ManufacturingRequirements,
    issues: list[ManufacturingIssue],
) -> None:
    expected_sets = (
        (
            "component",
            catalog.policy.required_component_ids,
            requirements.required_component_ids,
        ),
        (
            "mounting_hole",
            catalog.policy.required_hole_ids,
            requirements.required_hole_ids,
        ),
        (
            "connector",
            catalog.policy.required_connector_ids,
            requirements.required_connector_ids,
        ),
        (
            "motion",
            catalog.policy.required_motion_ids,
            requirements.required_motion_ids,
        ),
    )
    for kind, policy_ids, validation_ids in expected_sets:
        for omitted in sorted(set(policy_ids).difference(validation_ids)):
            issues.append(
                ManufacturingIssue(
                    code="validation_scope_incomplete",
                    severity="error",
                    subject=f"{kind}.{omitted}",
                    message="A qualification-policy item is omitted from validation.",
                    suggestion="Include the policy item and its probe in the validation run.",
                )
            )


def validate_manufacturing(
    catalog: QualificationCatalog,
    requirements: ManufacturingRequirements,
    observations: ManufacturingObservations,
) -> ManufacturingValidationReport:
    """Run deterministic Maker Alpha checks over explicit catalog/probe inputs.

    This function does not inspect a mesh or infer missing physical facts. A CAD
    adapter must produce the wall, hole, fit, cavity, access, and collision probes;
    absent required probes are hard digital-check failures. Qualification evidence
    is evaluated separately, so planning AABBs can aid design without becoming a
    buildability claim.
    """

    issues: list[ManufacturingIssue] = []
    _check_validation_scope(catalog, requirements, issues)
    _check_parts(requirements, observations, issues)
    _check_holes_and_fits(requirements, observations, issues)
    _check_components_and_motion(catalog, requirements, observations, issues)
    _check_mass_and_power(catalog, requirements, observations, issues)

    assessment = assess_profile_qualification(catalog)
    for missing in assessment.missing_or_unqualified:
        issues.append(
            ManufacturingIssue(
                code="qualification_evidence_missing",
                severity="warning",
                subject=missing,
                message="This required profile fact lacks qualifying measured evidence.",
                suggestion="Capture a traceable physical measurement before qualification.",
            )
        )

    digital_checks_passed = not any(issue.severity == "error" for issue in issues)
    within_qualified_profile = (
        digital_checks_passed
        and assessment.effective_state == "profile_qualified"
        and assessment.eligible_for_profile_qualification
    )
    evidence_level: EvidenceLevel
    if within_qualified_profile:
        evidence_level = "within_qualified_profile"
    elif digital_checks_passed:
        evidence_level = "digital_checks_passed"
    else:
        evidence_level = "concept_only"

    return ManufacturingValidationReport(
        digital_checks_passed=digital_checks_passed,
        qualification_state=assessment.effective_state,
        within_qualified_profile=within_qualified_profile,
        evidence_level=evidence_level,
        issues=tuple(issues),
    )


__all__ = [
    "ComponentPlacementProbe",
    "ConnectorAccessProbe",
    "EvidenceLevel",
    "FitProbe",
    "HoleProbe",
    "ManufacturingIssue",
    "ManufacturingObservations",
    "ManufacturingRequirements",
    "ManufacturingValidationReport",
    "PrintablePartProbe",
    "Severity",
    "SupportFootprint",
    "SweptVolumeProbe",
    "validate_manufacturing",
]
