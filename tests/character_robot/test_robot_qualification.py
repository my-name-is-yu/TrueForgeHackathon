from __future__ import annotations

import pytest

from character_robot.profiles import ProfileRegistry
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
    assess_profile_qualification,
)


def _evidence(evidence_id: str, basis: str = "physical_measurement") -> EvidenceRef:
    return EvidenceRef(
        evidence_id=evidence_id,
        basis=basis,  # type: ignore[arg-type]
        source_ref=f"measurements/{evidence_id}.json",
        sha256="a" * 64,
    )


def _catalog(
    *,
    declared_state: str = "profile_qualified",
    geometry_basis: str = "physical_measurement",
    load_basis: str = "physical_measurement",
) -> QualificationCatalog:
    geometry = _evidence("geometry_run", geometry_basis)
    mass = _evidence("mass_run")
    electrical_spec = _evidence("electrical_spec", "manufacturer_spec")
    load = _evidence("load_run", load_basis)
    component = CatalogComponent(
        component_id="controller",
        envelope=CatalogAabb(
            center_mm=(0.0, 0.0, 12.0),
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
                    center_mm=(0.0, -18.0, 12.0),
                    size_mm=(12.0, 8.0, 6.0),
                    evidence=geometry,
                ),
                access_volume=CatalogAabb(
                    center_mm=(0.0, -35.0, 12.0),
                    size_mm=(20.0, 30.0, 14.0),
                    evidence=geometry,
                ),
            ),
        ),
    )
    return QualificationCatalog(
        profile_id="measured-character/v1",
        catalog_version="hardware-catalog-v2",
        declared_state=declared_state,  # type: ignore[arg-type]
        components=(component,),
        motion_envelopes=(
            MotionEnvelope(
                motion_id="neck_pan",
                swept_volume=CatalogAabb(
                    center_mm=(0.0, 0.0, 70.0),
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
            minimum_operating_voltage_v=ScalarEvidence(4.5, "V", electrical_spec),
            supply_current_limit_a=ScalarEvidence(3.0, "A", electrical_spec),
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


def test_current_profiles_remain_digital_only() -> None:
    assert {
        profile.profile_id: profile.qualification
        for profile in ProfileRegistry().list_profiles()
    } == {
        "m5-cores3-goplus2/v1": "digital_only",
        "pi-zero2wh-crickit-ws2/v1": "digital_only",
    }


def test_complete_measured_catalog_is_eligible_but_does_not_self_promote() -> None:
    assessment = assess_profile_qualification(_catalog(declared_state="digital_only"))

    assert assessment.eligible_for_profile_qualification is True
    assert assessment.effective_state == "digital_only"
    assert assessment.missing_or_unqualified == ()


def test_published_catalog_requires_every_qualification_gate() -> None:
    assessment = assess_profile_qualification(_catalog())

    assert assessment.eligible_for_profile_qualification is True
    assert assessment.effective_state == "profile_qualified"
    assert assessment.missing_or_unqualified == ()


def test_planning_geometry_cannot_support_profile_qualification() -> None:
    assessment = assess_profile_qualification(
        _catalog(geometry_basis="planning_allowance")
    )

    assert assessment.eligible_for_profile_qualification is False
    assert assessment.effective_state == "digital_only"
    assert assessment.missing_or_unqualified == (
        "component.controller.envelope",
        "connector.controller_usb",
        "motion.neck_pan",
        "mounting_hole.controller_mount",
    )


def test_peak_current_and_voltage_must_come_from_a_physical_load_test() -> None:
    assessment = assess_profile_qualification(
        _catalog(load_basis="derived_from_measured")
    )

    assert assessment.effective_state == "digital_only"
    assert assessment.missing_or_unqualified == (
        "power.measured_peak_current",
        "power.observed_min_voltage",
    )


def test_missing_mass_and_cog_are_reported_independently() -> None:
    catalog = _catalog()
    catalog = QualificationCatalog(
        profile_id=catalog.profile_id,
        catalog_version=catalog.catalog_version,
        declared_state=catalog.declared_state,
        components=catalog.components,
        motion_envelopes=catalog.motion_envelopes,
        mass=MassProperties(
            complete_assembly_mass_g=ScalarEvidence(None, "g"),
            center_of_gravity_mm=VectorEvidence(None, "mm"),
        ),
        power=catalog.power,
        policy=catalog.policy,
    )

    assessment = assess_profile_qualification(catalog)

    assert assessment.effective_state == "digital_only"
    assert assessment.missing_or_unqualified == (
        "mass.center_of_gravity",
        "mass.complete_assembly",
    )


def test_unknown_evidence_basis_and_self_asserted_state_are_rejected() -> None:
    with pytest.raises(ValueError, match="evidence basis"):
        _evidence("invalid_basis", "self_asserted")
    with pytest.raises(ValueError, match="qualification state"):
        _catalog(declared_state="exact_build_verified")
