from __future__ import annotations

from dataclasses import replace

import pytest

from character_robot.physical_evidence import (
    EXACT_BUILD_REQUIRED_TESTS,
    PROFILE_REQUIRED_TESTS,
    HmacSha256EvidenceVerifier,
    Measurement,
    PhysicalEvidenceRecord,
    evaluate_physical_evidence,
    sign_evidence_record,
)
from character_robot.profiles import ProfileRegistry


_KEY = b"trusted-lab-key-material-000000000"
_PROFILE_SHA = "a" * 64
_SPEC_SHA = "b" * 64
_BUILD_SUBJECT_SHA = "c" * 64
_METRICS = {
    "component_dimensions": (("components_measured_count", 9, "count"),),
    "electrical_limits": (
        ("peak_current_a", 1.8, "A"),
        ("minimum_operating_voltage_v", 4.9, "V"),
    ),
    "wiring_continuity": (("connections_tested_count", 8, "count"),),
    "motion_envelope": (
        ("sweeps_tested_count", 6, "count"),
        ("minimum_clearance_mm", 1.2, "mm"),
    ),
    "mass_properties": (
        ("assembly_mass_g", 612, "g"),
        ("cog_height_mm", 58, "mm"),
    ),
    "thermal_run": (
        ("maximum_temperature_c", 47, "degC"),
        ("duration_s", 1800, "s"),
    ),
    "runtime_install": (("successful_boots_count", 10, "count"),),
    "exact_print": (("parts_inspected_count", 8, "count"),),
    "assembly_completion": (("assembly_time_min", 92, "min"),),
    "standard_motion_100_cycles": (("completed_cycles", 100, "count"),),
    "emergency_stop": (("stop_time_ms", 82, "ms"),),
}


def _record(test: str, subject: str) -> PhysicalEvidenceRecord:
    record = PhysicalEvidenceRecord(
        record_id=f"{test}-record",
        subject=subject,
        subject_sha256=_PROFILE_SHA if subject == "profile" else _BUILD_SUBJECT_SHA,
        spec_sha256=None if subject == "profile" else _SPEC_SHA,
        profile_id="m5-cores3-goplus2/v1",
        catalog_version="hardware-catalog-v1",
        test=test,
        performed_at="2026-09-03T00:00:00Z",
        measurements=tuple(Measurement(*values) for values in _METRICS[test]),
        passed=True,
        signer_id="local-lab",
        signature_sha256="0" * 64,
    )
    return sign_evidence_record(record, key=_KEY)


def _all_records() -> tuple[PhysicalEvidenceRecord, ...]:
    return tuple(
        [*(_record(test, "profile") for test in sorted(PROFILE_REQUIRED_TESTS))]
        + [
            *(
                _record(test, "exact_build")
                for test in sorted(EXACT_BUILD_REQUIRED_TESTS)
            )
        ]
    )


def _evaluate(*, qualification: str, records=(), subject=_BUILD_SUBJECT_SHA):
    return evaluate_physical_evidence(
        digital_checks_passed=True,
        profile_qualification=qualification,
        profile_id="m5-cores3-goplus2/v1",
        catalog_version="hardware-catalog-v1",
        profile_sha256=_PROFILE_SHA,
        spec_sha256=_SPEC_SHA,
        exact_build_subject_sha256=subject,
        records=tuple(records),
        verifier=HmacSha256EvidenceVerifier({"local-lab": _KEY}),
    )


def test_digital_only_catalog_cannot_be_promoted_by_supplied_evidence() -> None:
    result = _evaluate(qualification="digital_only", records=_all_records())

    assert result.evidence_level == "digital_checks_passed"
    assert result.missing_profile_tests == ()
    assert "profile_catalog_is_digital_only" in result.blockers


@pytest.mark.parametrize(
    "profile_id",
    ["m5-cores3-goplus2/v1", "pi-zero2wh-crickit-ws2/v1"],
)
def test_current_profiles_cannot_be_promoted_even_with_complete_signed_evidence(
    profile_id: str,
) -> None:
    profile = ProfileRegistry().get_profile(profile_id)
    records = tuple(
        sign_evidence_record(replace(record, profile_id=profile_id), key=_KEY)
        for record in _all_records()
    )

    result = evaluate_physical_evidence(
        digital_checks_passed=True,
        profile_qualification=profile.qualification,
        profile_id=profile_id,
        catalog_version="hardware-catalog-v1",
        profile_sha256=_PROFILE_SHA,
        spec_sha256=_SPEC_SHA,
        exact_build_subject_sha256=_BUILD_SUBJECT_SHA,
        records=records,
        verifier=HmacSha256EvidenceVerifier({"local-lab": _KEY}),
    )

    assert result.evidence_level == "digital_checks_passed"
    assert result.missing_profile_tests == ()
    assert result.missing_exact_build_tests == ()
    assert result.blockers == ("profile_catalog_is_digital_only",)


def test_complete_signed_exact_evidence_can_reach_exact_build_only_for_qualified_profile() -> (
    None
):
    result = _evaluate(qualification="profile_qualified", records=_all_records())

    assert result.evidence_level == "exact_build_verified"
    assert result.blockers == ()
    assert result.missing_profile_tests == ()
    assert result.missing_exact_build_tests == ()


def test_tampered_and_wrong_subject_evidence_is_rejected() -> None:
    unsigned = replace(
        _record("component_dimensions", "profile"), signature_sha256="0" * 64
    )
    wrong_subject = sign_evidence_record(
        replace(_record("electrical_limits", "profile"), subject_sha256="d" * 64),
        key=_KEY,
    )

    result = _evaluate(
        qualification="profile_qualified", records=(unsigned, wrong_subject)
    )

    assert result.evidence_level == "digital_checks_passed"
    assert [item.reason for item in result.rejected] == [
        "signature_invalid",
        "subject_mismatch",
    ]


def test_failed_digital_checks_override_physical_records() -> None:
    result = evaluate_physical_evidence(
        digital_checks_passed=False,
        profile_qualification="profile_qualified",
        profile_id="m5-cores3-goplus2/v1",
        catalog_version="hardware-catalog-v1",
        profile_sha256=_PROFILE_SHA,
        spec_sha256=_SPEC_SHA,
        exact_build_subject_sha256=_BUILD_SUBJECT_SHA,
        records=_all_records(),
        verifier=HmacSha256EvidenceVerifier({"local-lab": _KEY}),
    )

    assert result.evidence_level == "concept_only"
    assert result.accepted_record_ids == ()
    assert result.blockers == ("digital_checks_not_passed",)


def test_cycle_evidence_requires_at_least_100_observed_cycles() -> None:
    with pytest.raises(ValueError, match="at least 100 cycles"):
        PhysicalEvidenceRecord(
            record_id="short-cycle-record",
            subject="exact_build",
            subject_sha256=_BUILD_SUBJECT_SHA,
            spec_sha256=_SPEC_SHA,
            profile_id="m5-cores3-goplus2/v1",
            catalog_version="hardware-catalog-v1",
            test="standard_motion_100_cycles",
            performed_at="2026-09-03T00:00:00Z",
            measurements=(Measurement("completed_cycles", 99, "count"),),
            passed=True,
            signer_id="local-lab",
            signature_sha256="0" * 64,
        )


@pytest.mark.parametrize(
    ("metric", "value", "unit", "message"),
    [
        ("stop_time_ms", -50, "ms", "strictly positive"),
        ("stop_time_ms", 50, "bananas", "canonical unit"),
        ("completed_cycles", True, "count", "booleans"),
        ("completed_cycles", 1.5, "count", "non-negative integer"),
    ],
)
def test_signed_malformed_measurements_cannot_be_created_for_promotion(
    metric: str, value: object, unit: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        PhysicalEvidenceRecord(
            record_id="malformed-record",
            subject="exact_build",
            subject_sha256=_BUILD_SUBJECT_SHA,
            spec_sha256=_SPEC_SHA,
            profile_id="m5-cores3-goplus2/v1",
            catalog_version="hardware-catalog-v1",
            test="emergency_stop"
            if metric == "stop_time_ms"
            else "standard_motion_100_cycles",
            performed_at="2026-09-03T00:00:00Z",
            measurements=(Measurement(metric, value, unit),),
            passed=True,
            signer_id="local-lab",
            signature_sha256="0" * 64,
        )


def test_duplicate_metric_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate metrics"):
        PhysicalEvidenceRecord(
            record_id="duplicate-record",
            subject="exact_build",
            subject_sha256=_BUILD_SUBJECT_SHA,
            spec_sha256=_SPEC_SHA,
            profile_id="m5-cores3-goplus2/v1",
            catalog_version="hardware-catalog-v1",
            test="emergency_stop",
            performed_at="2026-09-03T00:00:00Z",
            measurements=(
                Measurement("stop_time_ms", 50, "ms"),
                Measurement("stop_time_ms", 60, "ms"),
            ),
            passed=True,
            signer_id="local-lab",
            signature_sha256="0" * 64,
        )


def test_temperature_can_be_signed_with_a_negative_value() -> None:
    record = _record("thermal_run", "profile")
    record = replace(
        record,
        measurements=(
            Measurement("maximum_temperature_c", -10, "degC"),
            Measurement("duration_s", 1, "s"),
        ),
    )
    assert sign_evidence_record(record, key=_KEY).measurements[0].value == -10
