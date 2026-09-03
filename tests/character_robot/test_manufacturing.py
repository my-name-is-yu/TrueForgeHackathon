from __future__ import annotations

from dataclasses import replace

import pytest

from character_robot.manufacturing import (
    ComponentPlacementProbe,
    ConnectorAccessProbe,
    FitProbe,
    HoleProbe,
    ManufacturingObservations,
    ManufacturingRequirements,
    PrintablePartProbe,
    SupportFootprint,
    SweptVolumeProbe,
    validate_manufacturing,
)
from character_robot.qualification import (
    CatalogAabb,
    CatalogComponent,
    ConnectorPort,
    EvidenceRef,
    MassProperties,
    MotionEnvelope,
    MountingHole,
    PowerProperties,
    QualificationCatalog,
    QualificationPolicy,
    ScalarEvidence,
    VectorEvidence,
)


def _evidence(evidence_id: str, basis: str = "physical_measurement") -> EvidenceRef:
    return EvidenceRef(
        evidence_id=evidence_id,
        basis=basis,  # type: ignore[arg-type]
        source_ref=f"measurements/{evidence_id}.json",
        sha256="b" * 64,
    )


def _catalog(*, declared_state: str = "profile_qualified") -> QualificationCatalog:
    geometry = _evidence("geometry")
    mass = _evidence("mass")
    specification = _evidence("electrical", "manufacturer_spec")
    load = _evidence("load")
    return QualificationCatalog(
        profile_id="measured-character/v1",
        catalog_version="hardware-catalog-v2",
        declared_state=declared_state,  # type: ignore[arg-type]
        components=(
            CatalogComponent(
                component_id="controller",
                envelope=CatalogAabb(
                    center_mm=(0.0, 0.0, 10.0),
                    size_mm=(50.0, 30.0, 20.0),
                    evidence=geometry,
                ),
                mass_g=ScalarEvidence(80.0, "g", mass),
                mounting_holes=(
                    MountingHole(
                        hole_id="controller_mount",
                        component_id="controller",
                        center_mm=(20.0, 10.0, 2.0),
                        diameter_mm=3.2,
                        evidence=geometry,
                    ),
                ),
                connectors=(
                    ConnectorPort(
                        connector_id="controller_usb",
                        component_id="controller",
                        mating_volume=CatalogAabb(
                            center_mm=(0.0, -18.0, 10.0),
                            size_mm=(12.0, 8.0, 6.0),
                            evidence=geometry,
                        ),
                        access_volume=CatalogAabb(
                            center_mm=(0.0, -35.0, 10.0),
                            size_mm=(20.0, 30.0, 14.0),
                            evidence=geometry,
                        ),
                    ),
                ),
            ),
        ),
        motion_envelopes=(
            MotionEnvelope(
                motion_id="neck_pan",
                swept_volume=CatalogAabb(
                    center_mm=(0.0, 0.0, 75.0),
                    size_mm=(70.0, 70.0, 60.0),
                    evidence=geometry,
                ),
            ),
        ),
        mass=MassProperties(
            complete_assembly_mass_g=ScalarEvidence(650.0, "g", mass),
            center_of_gravity_mm=VectorEvidence((0.0, 0.0, 25.0), "mm", mass),
        ),
        power=PowerProperties(
            minimum_operating_voltage_v=ScalarEvidence(4.5, "V", specification),
            supply_current_limit_a=ScalarEvidence(3.0, "A", specification),
            measured_peak_current_a=ScalarEvidence(1.8, "A", load),
            observed_min_voltage_v=ScalarEvidence(4.8, "V", load),
        ),
        policy=QualificationPolicy(
            required_component_ids=("controller",),
            required_hole_ids=("controller_mount",),
            required_connector_ids=("controller_usb",),
            required_motion_ids=("neck_pan",),
        ),
    )


def _requirements() -> ManufacturingRequirements:
    return ManufacturingRequirements(
        printer_volume_mm=(120.0, 120.0, 120.0),
        minimum_wall_mm=1.6,
        minimum_fit_clearance_mm=0.3,
        maximum_mass_g=1000.0,
        minimum_cog_margin_mm=5.0,
        required_part_ids=("chassis",),
        required_hole_ids=("controller_mount",),
        required_fit_ids=("head_joint",),
        required_component_ids=("controller",),
        required_connector_ids=("controller_usb",),
        required_motion_ids=("neck_pan",),
    )


def _observations() -> ManufacturingObservations:
    return ManufacturingObservations(
        printable_parts=(
            PrintablePartProbe(
                part_id="chassis",
                print_bounds=CatalogAabb(
                    center_mm=(0.0, 0.0, 50.0), size_mm=(100.0, 90.0, 100.0)
                ),
                minimum_wall_mm=1.8,
                solid_count=1,
                is_manifold=True,
            ),
        ),
        holes=(
            HoleProbe(
                hole_id="controller_mount",
                observed_diameter_mm=3.4,
                mating_diameter_mm=3.0,
                maximum_clearance_mm=0.6,
            ),
        ),
        fits=(
            FitProbe(
                fit_id="head_joint",
                observed_male_size_mm=10.0,
                observed_female_size_mm=10.4,
                maximum_clearance_mm=0.6,
            ),
        ),
        component_placements=(
            ComponentPlacementProbe(
                component_id="controller",
                cavity=CatalogAabb(
                    center_mm=(0.0, 0.0, 10.0), size_mm=(52.0, 32.0, 22.0)
                ),
            ),
        ),
        connector_access=(
            ConnectorAccessProbe(
                connector_id="controller_usb",
                obstruction_volumes=(
                    CatalogAabb(
                        center_mm=(50.0, -35.0, 10.0),
                        size_mm=(10.0, 10.0, 10.0),
                    ),
                ),
            ),
        ),
        swept_volumes=(
            SweptVolumeProbe(
                motion_id="neck_pan",
                obstacle_volumes=(
                    CatalogAabb(
                        center_mm=(80.0, 0.0, 75.0),
                        size_mm=(20.0, 20.0, 20.0),
                    ),
                ),
            ),
        ),
        support_footprint=SupportFootprint(
            center_xy_mm=(0.0, 0.0), size_xy_mm=(80.0, 60.0)
        ),
    )


def test_complete_measured_inputs_reach_within_qualified_profile() -> None:
    report = validate_manufacturing(_catalog(), _requirements(), _observations())

    assert report == validate_manufacturing(
        _catalog(), _requirements(), _observations()
    )
    assert report.digital_checks_passed is True
    assert report.qualification_state == "profile_qualified"
    assert report.within_qualified_profile is True
    assert report.evidence_level == "within_qualified_profile"
    assert report.issues == ()


def test_complete_checks_do_not_promote_a_digital_only_catalog() -> None:
    report = validate_manufacturing(
        _catalog(declared_state="digital_only"), _requirements(), _observations()
    )

    assert report.digital_checks_passed is True
    assert report.qualification_state == "digital_only"
    assert report.within_qualified_profile is False
    assert report.evidence_level == "digital_checks_passed"


def test_manufacturing_failures_report_measurements_and_do_not_qualify() -> None:
    catalog = _catalog()
    defective_mass = replace(
        catalog.mass,
        complete_assembly_mass_g=replace(
            catalog.mass.complete_assembly_mass_g, value=1200.0
        ),
        center_of_gravity_mm=replace(
            catalog.mass.center_of_gravity_mm, value=(49.0, 0.0, 25.0)
        ),
    )
    defective_power = replace(
        catalog.power,
        measured_peak_current_a=replace(
            catalog.power.measured_peak_current_a, value=4.0
        ),
        observed_min_voltage_v=replace(catalog.power.observed_min_voltage_v, value=4.0),
    )
    catalog = replace(catalog, mass=defective_mass, power=defective_power)
    observations = replace(
        _observations(),
        printable_parts=(
            PrintablePartProbe(
                part_id="chassis",
                print_bounds=CatalogAabb(
                    center_mm=(0.0, 0.0, 50.0), size_mm=(130.0, 90.0, 100.0)
                ),
                minimum_wall_mm=1.0,
                solid_count=2,
                is_manifold=False,
            ),
        ),
        holes=(
            HoleProbe(
                hole_id="controller_mount",
                observed_diameter_mm=3.1,
                mating_diameter_mm=3.0,
            ),
        ),
        fits=(
            FitProbe(
                fit_id="head_joint",
                observed_male_size_mm=10.0,
                observed_female_size_mm=10.1,
            ),
        ),
        component_placements=(
            ComponentPlacementProbe(
                component_id="controller",
                cavity=CatalogAabb(
                    center_mm=(0.0, 0.0, 10.0), size_mm=(50.0, 30.0, 20.0)
                ),
            ),
        ),
        connector_access=(
            ConnectorAccessProbe(
                connector_id="controller_usb",
                obstruction_volumes=(
                    CatalogAabb(
                        center_mm=(0.0, -35.0, 10.0),
                        size_mm=(4.0, 4.0, 4.0),
                    ),
                ),
            ),
        ),
        swept_volumes=(
            SweptVolumeProbe(
                motion_id="neck_pan",
                obstacle_volumes=(
                    CatalogAabb(
                        center_mm=(0.0, 0.0, 75.0),
                        size_mm=(4.0, 4.0, 4.0),
                    ),
                ),
            ),
        ),
    )

    report = validate_manufacturing(catalog, _requirements(), observations)

    assert report.digital_checks_passed is False
    assert report.within_qualified_profile is False
    assert report.evidence_level == "concept_only"
    issues = {issue.code: issue for issue in report.issues}
    assert set(issues) == {
        "assembly_mass_exceeded",
        "center_of_gravity_margin_below_minimum",
        "component_clearance_below_minimum",
        "connector_access_blocked",
        "fit_clearance_below_minimum",
        "hole_clearance_below_minimum",
        "load_voltage_below_minimum",
        "peak_current_exceeds_supply",
        "printer_volume_exceeded",
        "printable_part_not_manifold",
        "printable_part_not_single_solid",
        "swept_volume_interference",
        "wall_thickness_below_minimum",
    }
    assert issues["wall_thickness_below_minimum"].measured_value == 1.0
    assert issues["wall_thickness_below_minimum"].limit_value == 1.6
    assert issues["component_clearance_below_minimum"].measured_value == 0.0


def test_required_missing_probes_are_hard_failures_in_stable_order() -> None:
    empty = ManufacturingObservations(
        printable_parts=(),
        holes=(),
        fits=(),
        component_placements=(),
        connector_access=(),
        swept_volumes=(),
        support_footprint=None,
    )

    report = validate_manufacturing(_catalog(), _requirements(), empty)

    assert report.digital_checks_passed is False
    assert [issue.code for issue in report.issues] == [
        "printable_part_probe_missing",
        "hole_probe_missing",
        "fit_probe_missing",
        "component_placement_missing",
        "connector_access_probe_missing",
        "swept_volume_probe_missing",
        "support_footprint_missing",
    ]


def test_touching_access_and_swept_bounds_are_not_false_collisions() -> None:
    observations = replace(
        _observations(),
        connector_access=(
            ConnectorAccessProbe(
                connector_id="controller_usb",
                obstruction_volumes=(
                    CatalogAabb(
                        center_mm=(15.0, -35.0, 10.0),
                        size_mm=(10.0, 10.0, 10.0),
                    ),
                ),
            ),
        ),
        swept_volumes=(
            SweptVolumeProbe(
                motion_id="neck_pan",
                obstacle_volumes=(
                    CatalogAabb(
                        center_mm=(45.0, 0.0, 75.0),
                        size_mm=(20.0, 20.0, 20.0),
                    ),
                ),
            ),
        ),
    )

    report = validate_manufacturing(_catalog(), _requirements(), observations)

    assert report.within_qualified_profile is True
    assert report.issues == ()


def test_nonfinite_and_duplicate_boundary_values_are_rejected() -> None:
    with pytest.raises(ValueError, match="finite"):
        CatalogAabb(center_mm=(0.0, 0.0, 0.0), size_mm=(1.0, float("nan"), 1.0))
    with pytest.raises(ValueError, match="unique"):
        replace(_observations(), holes=(_observations().holes[0],) * 2)


def test_qualification_policy_items_cannot_be_omitted_from_validation_scope() -> None:
    requirements = replace(
        _requirements(),
        required_component_ids=(),
        required_hole_ids=(),
        required_connector_ids=(),
        required_motion_ids=(),
    )

    report = validate_manufacturing(_catalog(), requirements, _observations())

    assert report.digital_checks_passed is False
    assert report.within_qualified_profile is False
    assert [
        issue.subject
        for issue in report.issues
        if issue.code == "validation_scope_incomplete"
    ] == [
        "component.controller",
        "mounting_hole.controller_mount",
        "connector.controller_usb",
        "motion.neck_pan",
    ]
