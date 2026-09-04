from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError

from character_robot.v2 import (
    AdvisoryData,
    BooleanClaim,
    CatalogEntry,
    CatalogFact,
    CatalogQuery,
    CatalogSnapshot,
    CatalogSource,
    EligibilityReason,
    EvidenceRef,
    NumericClaim,
    OFFICIAL_CATALOG_V2,
    POLOLU_CASTER_950,
    POLOLU_WHEEL_1087,
    TextClaim,
    assess_eligibility,
    catalog_digest,
    query_catalog,
)


def _source(
    source_id: str = "maker-doc",
    *,
    url: str = "https://manufacturer.example/data-sheet.pdf",
    document_sha256: str = "a" * 64,
    evidence_date: str = "2026-09-04",
) -> CatalogSource:
    return CatalogSource(
        source_id=source_id,
        manufacturer="Example Manufacturer",
        title="Example data sheet",
        url=url,
        media_type="application/pdf",
        document_sha256=document_sha256,
        evidence_date=evidence_date,
    )


def _evidence(source: CatalogSource, locator: str = "page 1") -> EvidenceRef:
    return EvidenceRef(
        source_id=source.source_id,
        source_url=source.url,
        locator=locator,
        document_sha256=source.document_sha256,
        evidence_date=source.evidence_date,
    )


def _mass_fact(
    source: CatalogSource,
    value: float = 10.0,
    *,
    basis: str = "manufacturer_stated",
    evidence: EvidenceRef | None = None,
) -> CatalogFact:
    claim_evidence = (
        (evidence if evidence is not None else _evidence(source))
        if basis in {"manufacturer_stated", "converted"}
        else None
    )
    claim = NumericClaim(
        original_value=value,
        original_unit="g",
        canonical_value=value,
        canonical_unit="g",
        basis=basis,
        conversion_rule="identity",
        evidence=claim_evidence,
    )
    return CatalogFact(fact_key="mass_g", claims=(claim,))


def _numeric_fact(
    source: CatalogSource, fact_key: str, value: float, *, unit: str = "V"
) -> CatalogFact:
    claim = NumericClaim(
        original_value=value,
        original_unit=unit,  # type: ignore[arg-type]
        canonical_value=value,
        canonical_unit=unit,  # type: ignore[arg-type]
        basis="manufacturer_stated",
        conversion_rule="identity",
        evidence=_evidence(source, fact_key),
    )
    return CatalogFact(fact_key=fact_key, claims=(claim,))  # type: ignore[arg-type]


def _entry(
    source: CatalogSource,
    *,
    entry_id: str = "example-part",
    category: str = "motor",
    facts: tuple[CatalogFact, ...] = (),
    manufacturer_sku: str = "EX-1",
    variant: str = "rev-a",
    capabilities: tuple[str, ...] = (),
) -> CatalogEntry:
    return CatalogEntry(
        entry_id=entry_id,
        manufacturer="Example Manufacturer",
        manufacturer_sku=manufacturer_sku,
        variant=variant,
        category=category,
        capabilities=capabilities,
        facts=facts,
    )


def _snapshot(
    source: CatalogSource,
    *entries: CatalogEntry,
) -> CatalogSnapshot:
    return CatalogSnapshot(
        catalog_version="test-catalog",
        sources=(source,),
        entries=entries,
    )


def test_official_seed_identities_facts_locators_and_document_digests() -> None:
    assert [entry.identity for entry in OFFICIAL_CATALOG_V2.entries] == [
        ("M5Stack", "K128", "CoreS3"),
        ("M5Stack", "M025-B", "Module13.2 GoPlus2"),
        ("Pololu", "#1087", "Wheel 32x7mm Pair - Black"),
        ("Pololu", "#950", "Ball Caster with 3/8in Plastic Ball, body-only install"),
    ]
    assert len(OFFICIAL_CATALOG_V2.sources) == 9
    source_by_id = {source.source_id: source for source in OFFICIAL_CATALOG_V2.sources}
    assert source_by_id["cores3-page"].document_sha256 == (
        "9a24d4201e8e04bb384ccea8dbc6a232613579f4efbf935f130d9323d78500b5"
    )
    assert source_by_id["goplus2-compatibility"].document_sha256 == (
        "d1485770966f92bc869f14f37013ca29dd670a10fd47ea89e204fd4da7ef4cb3"
    )

    cores3 = OFFICIAL_CATALOG_V2.entry("m5stack-cores3-k128")
    assert cores3.numeric("envelope_x_mm") == 54.0
    assert cores3.numeric("envelope_y_mm") == 54.0
    assert cores3.numeric("envelope_z_mm") == 15.5
    assert cores3.numeric("mass_g") is None
    assert cores3.fact_state("mass_g") == "unknown"
    assert cores3.fact("product_set_mass_g").claims[0].scope == "whole-set"  # type: ignore[union-attr]
    assert cores3.fact("product_set_mass_g").claims[0].evidence.locator  # type: ignore[union-attr]
    assert cores3.fact("connector_family").claims[0].evidence.document_sha256 == (  # type: ignore[union-attr]
        source_by_id["cores3-page"].document_sha256
    )

    goplus2 = OFFICIAL_CATALOG_V2.entry("m5stack-goplus2-m025-b")
    assert goplus2.fact_state("envelope_z_mm") == "conflict"
    assert goplus2.fact_state("ir_pin_map") == "conflict"
    assert goplus2.numeric("mass_g") == 38.0
    assert goplus2.text("motor_driver_model") == "DRV8833"
    assert goplus2.fact_state("operating_voltage_min_v") == "unknown"
    goplus2_envelope_claims = goplus2.fact("envelope_z_mm").claims  # type: ignore[union-attr]
    assert goplus2_envelope_claims[1].evidence.locator == (  # type: ignore[union-attr]
        "Model size drawing > thickness callout 13.2 mm"
    )
    goplus2_ir_claims = goplus2.fact("ir_pin_map").claims  # type: ignore[union-attr]
    assert goplus2_ir_claims[1].evidence.source_id == (  # type: ignore[union-attr]
        "goplus2-compatibility"
    )
    assert goplus2_ir_claims[1].original_value == (  # type: ignore[union-attr]
        "IR_RX pin 2; IR_TX pin 20"
    )

    wheel = OFFICIAL_CATALOG_V2.entry("pololu-wheel-1087")
    wheel_mass = wheel.fact("mass_g").claims[0]  # type: ignore[union-attr]
    assert wheel_mass.basis == "converted"
    assert wheel_mass.original_value == 0.11
    assert wheel_mass.original_unit == "oz"
    assert wheel_mass.conversion_rule == "oz_to_g"
    assert wheel_mass.canonical_unit == "g"
    assert wheel.numeric("shaft_diameter_mm") == 3.0
    assert wheel.text("shaft_profile") == "3mm D-shaft press-fit"
    assert wheel.text("material") == "ABS hub and silicone tire"
    assert wheel.fact("material").claims[0].evidence.source_id == (  # type: ignore[union-attr]
        "wheel-1087-drawing"
    )

    caster = OFFICIAL_CATALOG_V2.entry("pololu-ball-caster-950")
    ball = caster.fact("ball_diameter_mm").claims[0]  # type: ignore[union-attr]
    assert ball.basis == "converted"
    assert ball.original_value == 0.375
    assert ball.original_unit == "in"
    assert ball.canonical_value == pytest.approx(9.525)
    assert caster.numeric("mount_hole_spacing_mm") == 13.5
    assert caster.text("material") == "plastic ball"


def test_seed_digest_is_stable_and_excludes_advisory_data() -> None:
    expected = "7ff65dbcb220c0b387ea1d060b9b5d9fd3cbd8f5d75dd2213118340d2f15f45d"
    assert OFFICIAL_CATALOG_V2.content_digest == expected

    advisory = AdvisoryData(
        price_amount=1999.0,
        price_currency="JPY",
        availability="out_of_stock",
        observed_at="2026-09-04",
    )
    changed_entries = tuple(
        entry.model_copy(update={"advisory": advisory})
        for entry in OFFICIAL_CATALOG_V2.entries
    )
    changed = OFFICIAL_CATALOG_V2.model_copy(update={"entries": changed_entries})
    assert catalog_digest(changed) == expected
    assert changed.entry("m5stack-cores3-k128").advisory == advisory


def test_digest_is_independent_of_source_entry_fact_and_claim_order() -> None:
    entries = tuple(
        entry.model_copy(
            update={
                "facts": tuple(reversed(entry.facts)),
                "capabilities": tuple(reversed(entry.capabilities)),
            }
        )
        for entry in reversed(OFFICIAL_CATALOG_V2.entries)
    )
    reordered = CatalogSnapshot(
        catalog_version=OFFICIAL_CATALOG_V2.catalog_version,
        sources=tuple(reversed(OFFICIAL_CATALOG_V2.sources)),
        entries=entries,
    )
    assert reordered.content_digest == OFFICIAL_CATALOG_V2.content_digest

    with pytest.raises((ValidationError, TypeError)):
        OFFICIAL_CATALOG_V2.catalog_version = "mutated"  # type: ignore[misc]


def test_digest_is_independent_of_set_like_capability_and_derived_source_order() -> (
    None
):
    first = CatalogEntry(
        entry_id="derived-order",
        manufacturer="Example Manufacturer",
        manufacturer_sku="EX-DERIVED",
        variant="rev-a",
        category="motor",
        capabilities=("z-capability", "a-capability"),
        facts=(
            CatalogFact(
                fact_key="mass_g",
                claims=(
                    NumericClaim(
                        original_value=10.0,
                        original_unit="g",
                        canonical_value=10.0,
                        canonical_unit="g",
                        basis="derived",
                        conversion_rule="identity",
                        derived_from=("source-b", "source-a"),
                    ),
                ),
            ),
        ),
    )
    second = CatalogEntry(
        entry_id="derived-order",
        manufacturer="Example Manufacturer",
        manufacturer_sku="EX-DERIVED",
        variant="rev-a",
        category="motor",
        capabilities=("a-capability", "z-capability"),
        facts=(
            CatalogFact(
                fact_key="mass_g",
                claims=(
                    NumericClaim(
                        original_value=10.0,
                        original_unit="g",
                        canonical_value=10.0,
                        canonical_unit="g",
                        basis="derived",
                        conversion_rule="identity",
                        derived_from=("source-a", "source-b"),
                    ),
                ),
            ),
        ),
    )
    first_catalog = CatalogSnapshot(
        catalog_version="test-catalog", sources=(), entries=(first,)
    )
    second_catalog = CatalogSnapshot(
        catalog_version="test-catalog", sources=(), entries=(second,)
    )
    assert first_catalog.content_digest == second_catalog.content_digest


def test_query_results_are_sorted_by_entry_id_independent_of_snapshot_order() -> None:
    source = _source()
    z_entry = _entry(
        source,
        entry_id="z-entry",
        variant="rev-z",
        capabilities=("shared-capability",),
    )
    a_entry = _entry(
        source,
        entry_id="a-entry",
        variant="rev-a",
        capabilities=("shared-capability",),
    )
    first = _snapshot(source, z_entry, a_entry)
    second = _snapshot(source, a_entry, z_entry)
    assert first.content_digest == second.content_digest

    query = CatalogQuery(category="motor", capability="shared-capability")
    assert [match.entry.entry_id for match in query_catalog(first, query).matches] == [
        "a-entry",
        "z-entry",
    ]
    assert [match.entry.entry_id for match in query_catalog(second, query).matches] == [
        "a-entry",
        "z-entry",
    ]


def test_seed_eligibility_keeps_core_and_goplus_gaps_ineligible() -> None:
    cores3 = OFFICIAL_CATALOG_V2.entry("m5stack-cores3-k128")
    isolated = assess_eligibility(cores3, "controller_isolated")
    assert isolated.eligible is True
    assert isolated.blocking_reasons == ()

    motor_stage = assess_eligibility(cores3, "board_motor_stage")
    assert motor_stage.eligible is False
    assert {
        "MISSING_MASS",
        "MISSING_CURRENT_LIMIT",
        "MISSING_THERMAL_LIMIT",
        "MISSING_CONNECTOR",
        "MISSING_REVISION",
        "MISSING_POWER_ISOLATION",
    } <= set(motor_stage.blocking_reasons)
    assert "mass_g" in motor_stage.blocking_facts
    assert "current_continuous_a" in motor_stage.blocking_facts

    goplus2 = OFFICIAL_CATALOG_V2.entry("m5stack-goplus2-m025-b")
    goplus_assessment = assess_eligibility(goplus2, "board_motor_stage")
    assert goplus_assessment.eligible is False
    assert {
        "CONFLICTING_ENVELOPE",
        "MISSING_OPERATING_VOLTAGE",
        "MISSING_CURRENT_LIMIT",
        "MISSING_THERMAL_LIMIT",
        "MISSING_CONNECTOR",
        "MISSING_REVISION",
        "MISSING_POWER_ISOLATION",
    } <= set(goplus_assessment.blocking_reasons)
    assert POLOLU_WHEEL_1087.entry_id == "pololu-wheel-1087"
    assert assess_eligibility(POLOLU_WHEEL_1087, "wheel_drive").eligible is True
    assert POLOLU_CASTER_950.entry_id == "pololu-ball-caster-950"
    assert assess_eligibility(POLOLU_CASTER_950, "caster").eligible is True


@pytest.mark.parametrize(
    ("minimum", "nominal", "maximum"),
    [(24.0, None, 9.0), (24.0, 12.0, 24.0), (9.0, 30.0, 24.0)],
)
def test_known_operating_voltage_bounds_must_be_ordered(
    minimum: float, nominal: float | None, maximum: float
) -> None:
    source = _source()
    facts = [
        _numeric_fact(source, "operating_voltage_min_v", minimum),
        _numeric_fact(source, "operating_voltage_max_v", maximum),
    ]
    if nominal is not None:
        facts.append(_numeric_fact(source, "operating_voltage_nominal_v", nominal))
    with pytest.raises(ValidationError):
        _entry(source, facts=tuple(facts))


def test_voltage_query_requires_documented_interval_to_cover_requested_bounds() -> None:
    source = _source()
    wide = _entry(
        source,
        entry_id="wide-voltage",
        facts=(
            _numeric_fact(source, "operating_voltage_min_v", 9.0),
            _numeric_fact(source, "operating_voltage_max_v", 24.0),
        ),
    )
    narrow = _entry(
        source,
        entry_id="narrow-voltage",
        variant="rev-b",
        facts=(
            _numeric_fact(source, "operating_voltage_min_v", 9.0),
            _numeric_fact(source, "operating_voltage_max_v", 12.0),
        ),
    )
    nominal_only = _entry(
        source,
        entry_id="nominal-voltage",
        variant="rev-c",
        facts=(_numeric_fact(source, "operating_voltage_nominal_v", 12.0),),
    )
    conflicting_min = CatalogFact(
        fact_key="operating_voltage_min_v",
        claims=(
            _numeric_fact(source, "operating_voltage_min_v", 9.0).claims[0],
            _numeric_fact(source, "operating_voltage_min_v", 15.0).claims[0],
        ),
    )
    conflicting = _entry(
        source,
        entry_id="conflicting-voltage",
        variant="rev-d",
        facts=(
            conflicting_min,
            _numeric_fact(source, "operating_voltage_nominal_v", 12.0),
            _numeric_fact(source, "operating_voltage_max_v", 24.0),
        ),
    )
    catalog = _snapshot(source, wide, narrow, nominal_only, conflicting)

    requested_range = CatalogQuery(min_voltage_v=10.0, max_voltage_v=20.0)
    assert [
        match.entry.entry_id
        for match in query_catalog(catalog, requested_range).matches
    ] == ["wide-voltage"]

    assert [
        match.entry.entry_id
        for match in query_catalog(catalog, CatalogQuery(min_voltage_v=20.0)).matches
    ] == ["wide-voltage"]


def test_generic_rating_queries_apply_bounds_to_all_known_ratings() -> None:
    source = _source()
    motor = _entry(
        source,
        entry_id="multi-rating-motor",
        facts=(
            _numeric_fact(source, "current_continuous_a", 2.0, unit="A"),
            _numeric_fact(source, "current_stall_a", 10.0, unit="A"),
            _numeric_fact(source, "torque_continuous_nm", 0.2, unit="N*m"),
            _numeric_fact(source, "torque_stall_nm", 0.8, unit="N*m"),
            _numeric_fact(source, "speed_nominal_rpm", 100.0, unit="rpm"),
            _numeric_fact(source, "speed_max_rpm", 1000.0, unit="rpm"),
        ),
    )
    catalog = _snapshot(source, motor)

    assert not query_catalog(catalog, CatalogQuery(max_current_a=3.0)).matches
    assert not query_catalog(catalog, CatalogQuery(max_torque_nm=0.5)).matches
    assert not query_catalog(catalog, CatalogQuery(max_speed_rpm=500.0)).matches
    assert query_catalog(catalog, CatalogQuery(min_current_a=2.0)).matches


def test_each_required_use_has_stable_reason_codes() -> None:
    source = _source()
    uses = (
        "controller_auxiliary",
        "board_motor_stage",
        "motor_drive",
        "wheel_drive",
        "head_servo",
        "head_horn",
        "caster",
        "battery",
        "charger",
        "regulator",
        "motor_driver",
        "protection",
        "main_switch",
        "e_stop",
        "connector",
        "wire",
        "fastener",
        "insert",
        "spacer",
    )
    observed: set[str] = set()
    for index, use in enumerate(uses):
        entry = _entry(source, entry_id=f"empty-{index}", category="controller")
        assessment = assess_eligibility(entry, use)  # type: ignore[arg-type]
        assert assessment.eligible is False
        observed.update(assessment.blocking_reasons)

    assert {
        "CATEGORY_MISMATCH",
        "MISSING_ENVELOPE",
        "MISSING_MASS",
        "MISSING_OPERATING_VOLTAGE",
        "MISSING_CURRENT_LIMIT",
        "MISSING_TORQUE",
        "MISSING_SPEED",
        "MISSING_MOUNT_GEOMETRY",
        "MISSING_SHAFT_GEOMETRY",
        "MISSING_CONNECTOR",
        "MISSING_REVISION",
        "MISSING_POWER_ISOLATION",
        "MISSING_THERMAL_LIMIT",
        "MISSING_PROTECTION",
        "MISSING_CAPACITY",
        "MISSING_CHEMISTRY",
        "MISSING_CONTACT_RATING",
        "MISSING_INTERFACE",
    } <= observed


@pytest.mark.parametrize(
    ("unknown_reason",),
    [
        ("UNKNOWN_OFFICIAL_FACT",),
        ("UNKNOWN_ASSEMBLY_SCOPE",),
    ],
)
def test_explicit_unknown_reason_is_preserved_as_a_blocker(
    unknown_reason: str,
) -> None:
    source = _source()
    entry = _entry(
        source,
        facts=(CatalogFact(fact_key="mass_g", unknown_reason=unknown_reason),),
    )
    assessment = assess_eligibility(entry, "motor_drive")
    assert unknown_reason in assessment.blocking_reasons
    assert "mass_g" in assessment.blocking_facts


def test_untrusted_basis_and_conflict_reason_codes_are_not_promoted() -> None:
    source = _source()
    assumption = CatalogFact(
        fact_key="mass_g",
        claims=(
            NumericClaim(
                original_value=10.0,
                original_unit="g",
                canonical_value=10.0,
                canonical_unit="g",
                basis="assumption",
                conversion_rule="identity",
            ),
        ),
    )
    derived = CatalogFact(
        fact_key="torque_continuous_nm",
        claims=(
            NumericClaim(
                original_value=0.2,
                original_unit="N*m",
                canonical_value=0.2,
                canonical_unit="N*m",
                basis="derived",
                conversion_rule="identity",
                derived_from=("measured-torque",),
            ),
        ),
    )
    conflicting_revision = CatalogFact(
        fact_key="revision",
        claims=(
            TextClaim(
                original_value="rev-a",
                canonical_value="rev-a",
                basis="manufacturer_stated",
                evidence=_evidence(source, "revision A"),
            ),
            TextClaim(
                original_value="rev-b",
                canonical_value="rev-b",
                basis="manufacturer_stated",
                evidence=_evidence(source, "revision B"),
            ),
        ),
    )
    conflicting_shaft = CatalogFact(
        fact_key="shaft_profile",
        claims=(
            TextClaim(
                original_value="D",
                canonical_value="D",
                basis="manufacturer_stated",
                evidence=_evidence(source, "shaft A"),
            ),
            TextClaim(
                original_value="round",
                canonical_value="round",
                basis="manufacturer_stated",
                evidence=_evidence(source, "shaft B"),
            ),
        ),
    )
    conflict_current = CatalogFact(
        fact_key="current_continuous_a",
        claims=(
            NumericClaim(
                original_value=1.0,
                original_unit="A",
                canonical_value=1.0,
                canonical_unit="A",
                basis="manufacturer_stated",
                conversion_rule="identity",
                evidence=_evidence(source, "current A"),
            ),
            NumericClaim(
                original_value=2.0,
                original_unit="A",
                canonical_value=2.0,
                canonical_unit="A",
                basis="manufacturer_stated",
                conversion_rule="identity",
                evidence=_evidence(source, "current B"),
            ),
        ),
    )
    assert assumption.state == "assumption"
    assert derived.state == "derived"
    assert conflicting_revision.state == "conflict"
    assert conflicting_shaft.state == "conflict"
    assert conflict_current.state == "conflict"

    motor = _entry(
        source,
        category="motor",
        facts=(
            assumption,
            derived,
            conflict_current,
        ),
    )
    assessment = assess_eligibility(motor, "motor_drive")
    assert "UNTRUSTED_FACT_BASIS" in assessment.blocking_reasons
    assert "CONFLICTING_FACT" in assessment.blocking_reasons

    revision_entry = _entry(
        source,
        entry_id="revision-conflict",
        category="controller",
        facts=(conflicting_revision,),
    )
    assert (
        "CONFLICTING_REVISION"
        in assess_eligibility(revision_entry, "controller_auxiliary").blocking_reasons
    )

    shaft_entry = _entry(
        source,
        entry_id="shaft-conflict",
        category="wheel",
        facts=(conflicting_shaft,),
    )
    assert (
        "CONFLICTING_INTERFACE"
        in assess_eligibility(shaft_entry, "wheel_drive").blocking_reasons
    )


def test_exact_conversion_and_fact_type_boundaries_are_strict() -> None:
    source = _source()
    converted = NumericClaim(
        original_value=2.0,
        original_unit="in",
        canonical_value=50.8,
        canonical_unit="mm",
        basis="converted",
        conversion_rule="in_to_mm",
        evidence=_evidence(source),
    )
    assert CatalogFact(
        fact_key="envelope_x_mm", claims=(converted,)
    ).canonical_value == pytest.approx(50.8)

    with pytest.raises(ValidationError):
        NumericClaim(
            original_value=1_000_000_000_000.0,
            original_unit="kg",
            canonical_value=1_000_000_000_000_100.0,
            canonical_unit="g",
            basis="converted",
            conversion_rule="kg_to_g",
            evidence=_evidence(source),
        )
    with pytest.raises(ValidationError):
        NumericClaim(
            original_value=1e308,
            original_unit="kg",
            canonical_value=1e308,
            canonical_unit="g",
            basis="converted",
            conversion_rule="kg_to_g",
            evidence=_evidence(source),
        )
    with pytest.raises(ValidationError):
        NumericClaim(
            original_value=2.0,
            original_unit="in",
            canonical_value=50.0,
            canonical_unit="mm",
            basis="converted",
            conversion_rule="in_to_mm",
            evidence=_evidence(source),
        )
    with pytest.raises(ValidationError):
        NumericClaim(
            original_value=1.0,
            original_unit="bananas",  # type: ignore[arg-type]
            canonical_value=1.0,
            canonical_unit="mm",
            basis="manufacturer_stated",
            conversion_rule="identity",
            evidence=_evidence(source),
        )
    with pytest.raises(ValidationError):
        NumericClaim(
            original_value=1_000_000_000_000.0,
            original_unit="g",
            canonical_value=1_000_000_000_050.0,
            canonical_unit="g",
            basis="manufacturer_stated",
            conversion_rule="identity",
            evidence=_evidence(source),
        )
    with pytest.raises(ValidationError):
        NumericClaim(
            original_value=True,  # type: ignore[arg-type]
            original_unit="g",
            canonical_value=1.0,
            canonical_unit="g",
            basis="manufacturer_stated",
            conversion_rule="identity",
            evidence=_evidence(source),
        )
    with pytest.raises(ValidationError):
        NumericClaim(
            original_value=float("nan"),
            original_unit="g",
            canonical_value=1.0,
            canonical_unit="g",
            basis="manufacturer_stated",
            conversion_rule="identity",
            evidence=_evidence(source),
        )


def test_text_and_boolean_identity_claims_cannot_change_canonical_value() -> None:
    source = _source()
    with pytest.raises(ValidationError):
        TextClaim(
            original_value="manufacturer label",
            canonical_value="normalized label",
            basis="manufacturer_stated",
            evidence=_evidence(source),
        )
    with pytest.raises(ValidationError):
        BooleanClaim(
            original_value=True,
            canonical_value=False,
            basis="manufacturer_stated",
            evidence=_evidence(source),
        )


def test_negative_quantities_and_invalid_count_are_rejected_but_temperature_can_be_signed() -> (
    None
):
    source = _source()
    negative_mass = NumericClaim(
        original_value=-1.0,
        original_unit="g",
        canonical_value=-1.0,
        canonical_unit="g",
        basis="manufacturer_stated",
        conversion_rule="identity",
        evidence=_evidence(source),
    )
    with pytest.raises(ValidationError):
        CatalogFact(fact_key="mass_g", claims=(negative_mass,))

    negative_temperature = NumericClaim(
        original_value=-20.0,
        original_unit="C",
        canonical_value=-20.0,
        canonical_unit="C",
        basis="manufacturer_stated",
        conversion_rule="identity",
        evidence=_evidence(source),
    )
    assert (
        CatalogFact(fact_key="thermal_limit_c", claims=(negative_temperature,)).state
        == "known"
    )

    zero_temperature = NumericClaim(
        original_value=0.0,
        original_unit="C",
        canonical_value=0.0,
        canonical_unit="C",
        basis="manufacturer_stated",
        conversion_rule="identity",
        evidence=_evidence(source),
    )
    assert (
        CatalogFact(fact_key="thermal_limit_c", claims=(zero_temperature,)).state
        == "known"
    )

    zero_awg = NumericClaim(
        original_value=0.0,
        original_unit="none",
        canonical_value=0.0,
        canonical_unit="none",
        basis="manufacturer_stated",
        conversion_rule="identity",
        evidence=_evidence(source),
    )
    assert CatalogFact(fact_key="wire_gauge_awg", claims=(zero_awg,)).state == "known"

    with pytest.raises(ValidationError):
        CatalogFact(
            fact_key="quantity_per_pack",
            claims=(
                NumericClaim(
                    original_value=1.5,
                    original_unit="none",
                    canonical_value=1.5,
                    canonical_unit="none",
                    basis="manufacturer_stated",
                    conversion_rule="identity",
                    evidence=_evidence(source),
                ),
            ),
        )
    with pytest.raises(ValidationError):
        CatalogFact(
            fact_key="envelope_x_mm",
            claims=(
                NumericClaim(
                    original_value=0.0,
                    original_unit="mm",
                    canonical_value=0.0,
                    canonical_unit="mm",
                    basis="manufacturer_stated",
                    conversion_rule="identity",
                    evidence=_evidence(source),
                ),
            ),
        )
    with pytest.raises(ValidationError):
        CatalogFact(
            fact_key="capacity_mah",
            claims=(
                NumericClaim(
                    original_value=0.0,
                    original_unit="mAh",
                    canonical_value=0.0,
                    canonical_unit="mAh",
                    basis="manufacturer_stated",
                    conversion_rule="identity",
                    evidence=_evidence(source),
                ),
            ),
        )


@pytest.mark.parametrize(
    ("fact_key", "unit"),
    [
        ("current_continuous_a", "A"),
        ("current_peak_a", "A"),
        ("current_stall_a", "A"),
        ("current_limit_a", "A"),
        ("rail_current_limit_a", "A"),
        ("contact_rating_a", "A"),
        ("torque_continuous_nm", "N*m"),
        ("torque_stall_nm", "N*m"),
        ("speed_nominal_rpm", "rpm"),
        ("speed_no_load_rpm", "rpm"),
        ("speed_max_rpm", "rpm"),
    ],
)
def test_zero_required_capability_values_cannot_become_eligible(
    fact_key: str, unit: str
) -> None:
    source = _source()
    with pytest.raises(ValidationError):
        _numeric_fact(source, fact_key, 0.0, unit=unit)


def test_claim_basis_evidence_and_duplicate_claim_rules() -> None:
    source = _source()
    claim = NumericClaim(
        original_value=10.0,
        original_unit="g",
        canonical_value=10.0,
        canonical_unit="g",
        basis="manufacturer_stated",
        conversion_rule="identity",
        evidence=_evidence(source),
    )
    with pytest.raises(ValidationError):
        CatalogFact(fact_key="mass_g", claims=(claim, claim))
    with pytest.raises(ValidationError):
        NumericClaim(
            original_value=10.0,
            original_unit="g",
            canonical_value=10.0,
            canonical_unit="g",
            basis="manufacturer_stated",
            conversion_rule="identity",
        )
    with pytest.raises(ValidationError):
        TextClaim(
            original_value="guess",
            canonical_value="guess",
            basis="assumption",
            evidence=_evidence(source),
        )
    with pytest.raises(ValidationError):
        BooleanClaim(
            original_value=True,
            canonical_value=True,
            basis="converted",  # type: ignore[arg-type]
            evidence=_evidence(source),
        )
    with pytest.raises(ValidationError):
        CatalogFact(fact_key="mass_g", unknown_reason="MISSING_CONNECTOR")
    with pytest.raises(ValidationError):
        CatalogFact(
            fact_key="mass_g",
            claims=(claim,),
            unknown_evidence=(_evidence(source, "no mass field"),),
        )


def test_duplicate_entries_source_digest_and_evidence_references_are_rejected() -> None:
    source = _source()
    duplicate_a = _entry(source, entry_id="duplicate-a")
    duplicate_b = duplicate_a.model_copy(update={"entry_id": "duplicate-b"})
    with pytest.raises(ValidationError):
        _snapshot(source, duplicate_a, duplicate_b)

    conflicting_source = _source(document_sha256="b" * 64)
    with pytest.raises(ValidationError):
        CatalogSnapshot(
            catalog_version="test-catalog",
            sources=(source, conflicting_source),
            entries=(),
        )

    foreign_evidence = EvidenceRef(
        source_id="other-doc",
        source_url="https://manufacturer.example/other.pdf",
        locator="page 1",
        document_sha256="b" * 64,
        evidence_date=source.evidence_date,
    )
    entry = _entry(source, facts=(_mass_fact(source, evidence=foreign_evidence),))
    with pytest.raises(ValidationError):
        _snapshot(source, entry)

    mismatched_manufacturer = CatalogEntry(
        entry_id="wrong-manufacturer",
        manufacturer="Different Manufacturer",
        manufacturer_sku="EX-1",
        variant="rev-a",
        category="motor",
        facts=(_mass_fact(source),),
    )
    with pytest.raises(ValidationError):
        _snapshot(source, mismatched_manufacturer)


def test_query_supports_identity_geometry_capability_eligibility_and_blockers() -> None:
    wheel_query = CatalogQuery(
        category="wheel",
        capability="d-shaft-3mm",
        max_envelope_x_mm=32.0,
        max_envelope_y_mm=32.0,
        max_envelope_z_mm=7.0,
        min_mass_g=3.0,
        max_mass_g=4.0,
        shaft_profile="3mm D-shaft press-fit",
        min_shaft_diameter_mm=3.0,
        max_shaft_diameter_mm=3.0,
        eligible_for="wheel_drive",
        eligible_only=True,
    )
    wheel_result = query_catalog(OFFICIAL_CATALOG_V2, wheel_query)
    assert [match.entry.entry_id for match in wheel_result.matches] == [
        "pololu-wheel-1087"
    ]
    assert wheel_result.matches[0].eligibility.eligible is True  # type: ignore[union-attr]

    caster_query = CatalogQuery(
        category="caster",
        mount_pattern="2x #2/M2 screw holes",
        max_envelope_z_mm=11.0,
        eligible_for="caster",
        eligible_only=True,
    )
    assert [
        match.entry.entry_id
        for match in query_catalog(OFFICIAL_CATALOG_V2, caster_query).matches
    ] == ["pololu-ball-caster-950"]

    blocked_query = CatalogQuery(
        category="controller",
        eligible_for="board_motor_stage",
        blocking_reason="MISSING_MASS",
    )
    blocked = query_catalog(OFFICIAL_CATALOG_V2, blocked_query)
    assert [match.entry.entry_id for match in blocked.matches] == [
        "m5stack-cores3-k128"
    ]
    assert blocked.matches[0].eligibility.eligible is False  # type: ignore[union-attr]

    with pytest.raises(ValidationError):
        CatalogQuery(eligible_only=True)
    with pytest.raises(ValidationError):
        CatalogQuery(blocking_reason="MISSING_MASS")
    with pytest.raises(ValidationError):
        CatalogQuery(min_mass_g=3.0, max_mass_g=2.0)


def test_date_and_url_boundaries_are_strict() -> None:
    with pytest.raises(ValidationError):
        _source(evidence_date="2026-02-30")
    with pytest.raises(ValidationError):
        _source(url="http://manufacturer.example/data-sheet.pdf")
    with pytest.raises(ValidationError):
        CatalogSource(
            source_id="maker-doc",
            manufacturer="Example Manufacturer",
            title="Example data sheet",
            url="https://manufacturer.example/data-sheet.pdf",
            media_type="application/pdf",
            document_sha256="not-a-digest",
            evidence_date="2026-09-04",
        )


def test_every_declared_reason_is_observable_or_explicitly_provenance_backed() -> None:
    declared = set(get_args(EligibilityReason))
    observed = {
        reason
        for entry in OFFICIAL_CATALOG_V2.entries
        for use in (
            "controller_isolated",
            "controller_auxiliary",
            "board_motor_stage",
            "wheel_drive",
            "caster",
        )
        for reason in assess_eligibility(entry, use).blocking_reasons
    }
    source = _source()
    all_uses = (
        "controller_auxiliary",
        "board_motor_stage",
        "motor_drive",
        "wheel_drive",
        "head_servo",
        "head_horn",
        "caster",
        "battery",
        "charger",
        "regulator",
        "motor_driver",
        "protection",
        "main_switch",
        "e_stop",
        "connector",
        "wire",
        "fastener",
        "insert",
        "spacer",
    )
    for index, use in enumerate(all_uses):
        observed.update(
            assess_eligibility(
                _entry(source, entry_id=f"empty-reason-{index}", category="controller"),
                use,  # type: ignore[arg-type]
            ).blocking_reasons
        )

    unknown = _entry(
        source,
        entry_id="unknown-reason",
        facts=(CatalogFact(fact_key="mass_g", unknown_reason="UNKNOWN_OFFICIAL_FACT"),),
    )
    unknown_scope = _entry(
        source,
        entry_id="unknown-scope-reason",
        facts=(
            CatalogFact(fact_key="mass_g", unknown_reason="UNKNOWN_ASSEMBLY_SCOPE"),
        ),
    )
    assumption = _entry(
        source, entry_id="basis-reason", facts=(_mass_fact(source, basis="assumption"),)
    )
    observed.update(assess_eligibility(unknown, "motor_drive").blocking_reasons)
    observed.update(assess_eligibility(unknown_scope, "motor_drive").blocking_reasons)
    observed.update(assess_eligibility(assumption, "motor_drive").blocking_reasons)

    conflicting_revision = _entry(
        source,
        entry_id="revision-reason",
        category="controller",
        facts=(
            CatalogFact(
                fact_key="revision",
                claims=(
                    TextClaim(
                        original_value="rev-a",
                        canonical_value="rev-a",
                        basis="manufacturer_stated",
                        evidence=_evidence(source, "revision A"),
                    ),
                    TextClaim(
                        original_value="rev-b",
                        canonical_value="rev-b",
                        basis="manufacturer_stated",
                        evidence=_evidence(source, "revision B"),
                    ),
                ),
            ),
        ),
    )
    conflicting_interface = _entry(
        source,
        entry_id="interface-reason",
        category="wheel",
        facts=(
            CatalogFact(
                fact_key="shaft_profile",
                claims=(
                    TextClaim(
                        original_value="D",
                        canonical_value="D",
                        basis="manufacturer_stated",
                        evidence=_evidence(source, "shaft A"),
                    ),
                    TextClaim(
                        original_value="round",
                        canonical_value="round",
                        basis="manufacturer_stated",
                        evidence=_evidence(source, "shaft B"),
                    ),
                ),
            ),
        ),
    )
    conflicting_fact = _entry(
        source,
        entry_id="fact-reason",
        category="motor",
        facts=(
            CatalogFact(
                fact_key="current_continuous_a",
                claims=(
                    NumericClaim(
                        original_value=1.0,
                        original_unit="A",
                        canonical_value=1.0,
                        canonical_unit="A",
                        basis="manufacturer_stated",
                        conversion_rule="identity",
                        evidence=_evidence(source, "current A"),
                    ),
                    NumericClaim(
                        original_value=2.0,
                        original_unit="A",
                        canonical_value=2.0,
                        canonical_unit="A",
                        basis="manufacturer_stated",
                        conversion_rule="identity",
                        evidence=_evidence(source, "current B"),
                    ),
                ),
            ),
        ),
    )
    observed.update(
        assess_eligibility(
            conflicting_revision, "controller_auxiliary"
        ).blocking_reasons
    )
    observed.update(
        assess_eligibility(conflicting_interface, "wheel_drive").blocking_reasons
    )
    observed.update(
        assess_eligibility(conflicting_fact, "motor_drive").blocking_reasons
    )
    assert declared <= observed
