from __future__ import annotations

import pytest
from pydantic import ValidationError

from character_robot.v2 import (
    REFERENCE_STACK,
    REFERENCE_STACK_CATALOG,
    REQUIRED_STACK_ROLES,
    ReferenceStackSnapshot,
    assess_reference_stack,
)
from character_robot.v2.catalog import assess_eligibility


def test_reference_stack_has_exact_roles_and_catalog_identities() -> None:
    selections = REFERENCE_STACK.selections
    assert tuple(selection.role for selection in selections) == REQUIRED_STACK_ROLES
    entries = {entry.entry_id: entry for entry in REFERENCE_STACK.catalog.entries}

    for selection in selections:
        entry = entries[selection.entry_id]
        assert selection.manufacturer == entry.manufacturer
        assert selection.manufacturer_sku == entry.manufacturer_sku
        assert selection.variant == entry.variant
        assert selection.quantity > 0
        assert selection.evidence_source_ids

    assert not any(
        selection.entry_id == "m5stack-goplus2-m025-b" for selection in selections
    )
    assert all(
        entry_id != "m5stack-goplus2-m025-b"
        for entry_id in REFERENCE_STACK.topology.branches[0].energy_path_entry_ids
    )


def test_reference_stack_readiness_keeps_digital_and_physical_status_separate() -> None:
    readiness = REFERENCE_STACK.readiness

    assert readiness.stack_definition_complete is True
    assert readiness.datasheet_candidate is True
    assert readiness.datasheet_eligible is False
    assert readiness.datasheet_checked is False
    assert readiness.physically_qualified is False
    assert readiness.physical_verification_pending is True
    assert {
        "missing-cores3-power-endpoint",
        "missing-xl430-continuous-duty",
        "missing-mkr-rating-and-endpoint",
        "missing-bioenno-pp30-mating-mpn",
        "missing-fuse-inrush-coordination",
        "missing-wire-ampacity",
        "missing-sr6-electronic-load-class",
        "missing-robotis-commercial-mpn-evidence",
        "wheel-shaft-adapter-unresolved",
    } <= set(readiness.blocking_codes)


def test_power_topology_is_controller_plus_two_nonparallel_estop_branches() -> None:
    topology = REFERENCE_STACK.topology
    branches = {branch.branch_id: branch for branch in topology.branches}

    assert topology.controller_branch_id == "controller-branch"
    assert topology.actuator_branch_ids == (
        "drive-actuator-branch",
        "head-actuator-branch",
    )
    assert topology.contacts_are_not_parallel is True
    assert branches["controller-branch"].opens_on_estop is False
    assert branches["controller-branch"].controller_survives_estop is True
    assert all(
        branches[branch_id].opens_on_estop
        and branches[branch_id].controller_survives_estop
        for branch_id in topology.actuator_branch_ids
    )
    contacts = [
        branches[branch_id].relay_contact for branch_id in topology.actuator_branch_ids
    ]
    assert len(set(contacts)) == 2


def test_every_source_has_digest_observation_and_dynamic_html_is_explicit() -> None:
    sources = {source.source_id: source for source in REFERENCE_STACK.catalog.sources}
    observations = {
        observation.source_id: observation
        for observation in REFERENCE_STACK.source_observations
    }

    assert set(sources) == set(observations)
    for source_id, source in sources.items():
        assert len(source.document_sha256) == 64
        if source.media_type == "text/html":
            assert observations[source_id].digest_kind == "dynamic_html_observation"
            assert "dynamic" in observations[source_id].note.lower()
        else:
            assert observations[source_id].digest_kind == "retrieved_bytes"
            assert "sha-256" in observations[source_id].note.lower()
    unavailable = {
        source_id
        for source_id, observation in observations.items()
        if not observation.usable_for_claims
    }
    assert unavailable == {
        "robotis-xl430-shop",
        "robotis-tb3-shop",
        "robotis-tb3-download",
        "robotis-mkr-shop",
    }


def test_unavailable_product_pages_can_explain_unknowns_but_not_claims() -> None:
    entries = {entry.entry_id: entry for entry in REFERENCE_STACK.catalog.entries}
    tb3 = entries["robotis-tb3-wheel-isw01"]
    unknown_source_ids = {
        reference.source_id for fact in tb3.facts for reference in fact.unknown_evidence
    }
    assert {
        "robotis-tb3-shop",
        "robotis-tb3-download",
    } <= unknown_source_ids

    payload = REFERENCE_STACK.model_dump(mode="json")
    payload["selections"][0]["evidence_source_ids"] = ["robotis-xl430-shop"]
    with pytest.raises(ValidationError, match="unavailable for claims"):
        ReferenceStackSnapshot.model_validate(payload)


def test_calculations_use_only_known_catalog_facts() -> None:
    entries = {entry.entry_id: entry for entry in REFERENCE_STACK.catalog.entries}
    for calculation in REFERENCE_STACK.calculations:
        assert calculation.basis == "published_values_only"
        for input_ref in calculation.inputs:
            assert entries[input_ref.entry_id].fact_state(input_ref.fact_key) == "known"

    values = {
        calculation.calculation_id: calculation.value
        for calculation in REFERENCE_STACK.calculations
    }
    assert values["xl430-head-stall-current"] == pytest.approx(2.8)
    assert values["pololu-drive-stall-current"] == pytest.approx(3.6)
    assert values["actuator-stall-current-total"] == pytest.approx(6.4)
    assert values["battery-to-stall-current-ratio"] == pytest.approx(1.875)
    assert values["indicative-wheel-speed"] == pytest.approx(0.0586430629)


def test_xl430_operating_voltage_uses_the_manual_input_bounds() -> None:
    entry = next(
        item
        for item in REFERENCE_STACK.catalog.entries
        if item.entry_id == "robotis-xl430-w250-t"
    )

    assert entry.numeric("operating_voltage_min_v") == pytest.approx(6.5)
    assert entry.numeric("operating_voltage_nominal_v") == pytest.approx(11.1)
    assert entry.numeric("operating_voltage_max_v") == pytest.approx(12.0)


def test_tampered_published_calculation_value_is_rejected() -> None:
    payload = REFERENCE_STACK.model_dump(mode="json")
    payload["calculations"][0]["value"] = 999.0

    with pytest.raises(ValidationError, match="calculation value"):
        ReferenceStackSnapshot.model_validate(payload)


@pytest.mark.parametrize(
    ("collection", "match"),
    (
        ("selections", "canonical role order"),
        ("source_observations", "canonical source order"),
        ("planning_assumptions", "canonical order"),
        ("unresolved_gates", "canonical order"),
        ("calculations", "canonical order"),
    ),
)
def test_snapshot_rejects_semantically_reordered_collections(
    collection: str, match: str
) -> None:
    payload = REFERENCE_STACK.model_dump(mode="json")
    payload[collection].reverse()

    with pytest.raises(ValidationError, match=match):
        ReferenceStackSnapshot.model_validate(payload)


def test_catalog_entry_reordering_keeps_snapshot_digest_stable() -> None:
    payload = REFERENCE_STACK.model_dump(mode="json")
    payload["catalog"]["entries"].reverse()

    reordered = ReferenceStackSnapshot.model_validate(payload)

    assert reordered.catalog_digest == REFERENCE_STACK.catalog_digest
    assert reordered.content_digest == REFERENCE_STACK.content_digest


def test_snapshot_rejects_source_digest_kind_media_mismatch() -> None:
    payload = REFERENCE_STACK.model_dump(mode="json")
    observation = next(
        item
        for item in payload["source_observations"]
        if item["source_id"] == "te-sr6-brochure"
    )
    observation["digest_kind"] = "dynamic_html_observation"
    observation["note"] = "Dynamic HTML observation: deliberately mismatched media type"

    with pytest.raises(ValidationError, match="digest kind does not match media type"):
        ReferenceStackSnapshot.model_validate(payload)


def test_inactive_selection_is_limited_to_off_topology_roles() -> None:
    payload = REFERENCE_STACK.model_dump(mode="json")
    drive = next(
        selection
        for selection in payload["selections"]
        if selection["role"] == "drive_motor"
    )
    drive["active"] = False

    with pytest.raises(ValidationError, match="only off-topology"):
        ReferenceStackSnapshot.model_validate(payload)

    assert not next(
        selection
        for selection in REFERENCE_STACK.selections
        if selection.role == "charger"
    ).active
    assert not next(
        selection
        for selection in REFERENCE_STACK.selections
        if selection.role == "regulator"
    ).active


def test_catalog_assessment_is_not_replaced_by_stack_status() -> None:
    entries = {entry.entry_id: entry for entry in REFERENCE_STACK.catalog.entries}
    for selection in REFERENCE_STACK.selections:
        direct = assess_eligibility(entries[selection.entry_id], selection.catalog_use)
        assert direct == next(
            assessment
            for assessment in REFERENCE_STACK.selection_assessments()
            if assessment.entry_id == selection.entry_id
            and assessment.use == selection.catalog_use
        )
    assert assess_reference_stack(REFERENCE_STACK).datasheet_eligible is False


def test_snapshot_rejects_catalog_digest_injection() -> None:
    payload = REFERENCE_STACK.model_dump(mode="json")
    payload["catalog_digest"] = "0" * 64

    with pytest.raises(ValidationError, match="catalog_digest"):
        ReferenceStackSnapshot.model_validate(payload)


def test_snapshot_rejects_identity_or_unknown_reference_mutation() -> None:
    payload = REFERENCE_STACK.model_dump(mode="json")
    payload["selections"][0]["manufacturer_sku"] = "invented-sku"

    with pytest.raises(ValidationError, match="selection identity"):
        ReferenceStackSnapshot.model_validate(payload)

    payload = REFERENCE_STACK.model_dump(mode="json")
    payload["topology"]["branches"][1]["energy_path_entry_ids"].append("unknown-entry")
    with pytest.raises(ValidationError, match="unknown catalog entry"):
        ReferenceStackSnapshot.model_validate(payload)


def test_parallel_relay_contacts_are_rejected() -> None:
    payload = REFERENCE_STACK.model_dump(mode="json")
    payload["topology"]["contacts_are_not_parallel"] = False

    with pytest.raises(ValidationError, match="not declare relay contacts in parallel"):
        ReferenceStackSnapshot.model_validate(payload)


def test_topology_rejects_non_controller_or_actuator_branch_shapes() -> None:
    payload = REFERENCE_STACK.model_dump(mode="json")
    payload["topology"]["branches"][1]["kind"] = "actuator_head"

    with pytest.raises(ValidationError, match="exactly one drive and one head"):
        ReferenceStackSnapshot.model_validate(payload)

    payload = REFERENCE_STACK.model_dump(mode="json")
    payload["topology"]["branches"][0]["controller_survives_estop"] = False

    with pytest.raises(ValidationError, match="controller alive"):
        ReferenceStackSnapshot.model_validate(payload)

    payload = REFERENCE_STACK.model_dump(mode="json")
    payload["topology"]["controller_branch_independence"] = "unknown"

    with pytest.raises(ValidationError, match="independence"):
        ReferenceStackSnapshot.model_validate(payload)


def test_topology_binds_sources_paths_and_estop_to_selected_roles() -> None:
    mutations = (
        ("source_entry_id", "robotis-xl430-w250-t", "power branch source"),
        ("energy_path_entry_ids", ["pololu-wheel-1087"], "energy path"),
    )
    for field, value, match in mutations:
        payload = REFERENCE_STACK.model_dump(mode="json")
        payload["topology"]["branches"][1][field] = value
        with pytest.raises(ValidationError, match=match):
            ReferenceStackSnapshot.model_validate(payload)

    for field, value, match in (
        ("estop_selection_id", "controller", "E-stop"),
        ("relay_selection_id", "controller", "topology relay"),
    ):
        payload = REFERENCE_STACK.model_dump(mode="json")
        payload["topology"][field] = value
        with pytest.raises(ValidationError, match=match):
            ReferenceStackSnapshot.model_validate(payload)

    payload = REFERENCE_STACK.model_dump(mode="json")
    payload["topology"]["relay_rating_evidence"]["relay_entry_id"] = "pololu-4869"
    with pytest.raises(ValidationError, match="relay rating evidence"):
        ReferenceStackSnapshot.model_validate(payload)


def test_relay_contact_mapping_and_rating_purpose_are_explicit() -> None:
    topology = REFERENCE_STACK.topology
    assert {
        branch.relay_contact for branch in topology.branches if branch.relay_contact
    } == {"A", "B"}
    assert topology.relay_rating_evidence.contact_current_a == pytest.approx(8.0)
    assert topology.relay_rating_evidence.contact_voltage_v == pytest.approx(250.0)
    assert topology.relay_rating_evidence.contact_voltage_type == "AC"
    assert topology.relay_rating_evidence.coil_voltage_v == pytest.approx(12.0)
    assert topology.relay_rating_evidence.coil_voltage_type == "DC"
    assert (
        topology.relay_rating_evidence.intended_12vdc_electronic_contact_current_a
        is None
    )
    assert all(
        calculation.unit != "A" or "relay" not in calculation.calculation_id
        for calculation in REFERENCE_STACK.calculations
    )

    payload = REFERENCE_STACK.model_dump(mode="json")
    payload["topology"]["branches"][1]["relay_contact"] = "B"
    with pytest.raises(ValidationError, match="relay contacts A and B once"):
        ReferenceStackSnapshot.model_validate(payload)


def test_sr6_contact_and_coil_values_do_not_become_generic_load_facts() -> None:
    entry = next(
        item
        for item in REFERENCE_STACK.catalog.entries
        if item.entry_id == "te-sr6b4012-1393260-4"
    )

    assert entry.fact("contact_rating_a") is None
    assert entry.fact("operating_voltage_nominal_v") is None
    assert entry.fact("current_limit_a").state == "unknown"
    assert entry.fact("operating_voltage_max_v").state == "unknown"
    assert not any(
        "relay" in item.calculation_id for item in REFERENCE_STACK.calculations
    )


def test_reference_catalog_digest_is_persisted_and_stable() -> None:
    assert REFERENCE_STACK.catalog_digest == REFERENCE_STACK_CATALOG.content_digest
    assert REFERENCE_STACK.catalog_digest == (
        "aec326815809cbe60e0ab7d95140259688364279bb3f15f75bcfee3969c5a143"
    )
    assert REFERENCE_STACK.content_digest == (
        "8e04d095dd7d9eaecb34e54429dde1054440e663af579d013998a8e6f600cb78"
    )
