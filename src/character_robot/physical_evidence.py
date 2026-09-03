from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Literal, Mapping, Protocol


EvidenceLevel = Literal[
    "concept_only",
    "digital_checks_passed",
    "within_qualified_profile",
    "exact_build_verified",
]
ProfileQualification = Literal[
    "digital_only", "profile_qualified", "exact_build_verified"
]
EvidenceSubject = Literal["profile", "exact_build"]
RejectionReason = Literal[
    "signature_invalid",
    "test_failed",
    "profile_mismatch",
    "catalog_mismatch",
    "subject_mismatch",
    "spec_mismatch",
]
EvidenceTest = Literal[
    "component_dimensions",
    "electrical_limits",
    "wiring_continuity",
    "motion_envelope",
    "mass_properties",
    "thermal_run",
    "runtime_install",
    "exact_print",
    "assembly_completion",
    "standard_motion_100_cycles",
    "emergency_stop",
]


_SAFE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9-]*/v[1-9][0-9]*$")

PROFILE_REQUIRED_TESTS: frozenset[EvidenceTest] = frozenset(
    {
        "component_dimensions",
        "electrical_limits",
        "wiring_continuity",
        "motion_envelope",
        "mass_properties",
        "thermal_run",
        "runtime_install",
    }
)
EXACT_BUILD_REQUIRED_TESTS: frozenset[EvidenceTest] = frozenset(
    {
        "exact_print",
        "assembly_completion",
        "standard_motion_100_cycles",
        "emergency_stop",
    }
)
_REQUIRED_METRICS: Mapping[EvidenceTest, frozenset[str]] = MappingProxyType(
    {
        "component_dimensions": frozenset({"components_measured_count"}),
        "electrical_limits": frozenset(
            {"peak_current_a", "minimum_operating_voltage_v"}
        ),
        "wiring_continuity": frozenset({"connections_tested_count"}),
        "motion_envelope": frozenset({"sweeps_tested_count", "minimum_clearance_mm"}),
        "mass_properties": frozenset({"assembly_mass_g", "cog_height_mm"}),
        "thermal_run": frozenset({"maximum_temperature_c", "duration_s"}),
        "runtime_install": frozenset({"successful_boots_count"}),
        "exact_print": frozenset({"parts_inspected_count"}),
        "assembly_completion": frozenset({"assembly_time_min"}),
        "standard_motion_100_cycles": frozenset({"completed_cycles"}),
        "emergency_stop": frozenset({"stop_time_ms"}),
    }
)


def _require_safe_id(value: str, label: str) -> None:
    if _SAFE_ID.fullmatch(value) is None:
        raise ValueError(f"{label} must be a safe identifier")


def _require_sha256(value: str, label: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


@dataclass(frozen=True, slots=True)
class Measurement:
    metric: str
    value: float
    unit: str

    def __post_init__(self) -> None:
        _require_safe_id(self.metric, "measurement metric")
        if not isinstance(self.value, (int, float)) or not math.isfinite(self.value):
            raise ValueError("measurement value must be finite")
        if (
            not isinstance(self.unit, str)
            or not 1 <= len(self.unit) <= 24
            or any(character in self.unit for character in ("\x00", "\n", "\r"))
        ):
            raise ValueError("measurement unit is invalid")

    def to_dict(self) -> dict[str, object]:
        return {"metric": self.metric, "unit": self.unit, "value": self.value}


@dataclass(frozen=True, slots=True)
class PhysicalEvidenceRecord:
    record_id: str
    subject: EvidenceSubject
    subject_sha256: str
    spec_sha256: str | None
    profile_id: str
    catalog_version: str
    test: EvidenceTest
    performed_at: str
    measurements: tuple[Measurement, ...]
    passed: bool
    signer_id: str
    signature_sha256: str

    def __post_init__(self) -> None:
        _require_safe_id(self.record_id, "record ID")
        _require_sha256(self.subject_sha256, "subject digest")
        if self.spec_sha256 is not None:
            _require_sha256(self.spec_sha256, "spec digest")
        if _PROFILE_ID.fullmatch(self.profile_id) is None:
            raise ValueError("profile ID is invalid")
        _require_safe_id(self.catalog_version, "catalog version")
        _require_safe_id(self.signer_id, "signer ID")
        _require_sha256(self.signature_sha256, "signature")
        try:
            timestamp = datetime.fromisoformat(self.performed_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("performed_at must be an ISO-8601 timestamp") from error
        if timestamp.tzinfo is None or timestamp.utcoffset() != UTC.utcoffset(
            timestamp
        ):
            raise ValueError("performed_at must use UTC")
        if not self.measurements:
            raise ValueError("physical evidence must contain measurements")
        metrics = {measurement.metric for measurement in self.measurements}
        missing = _REQUIRED_METRICS[self.test].difference(metrics)
        if missing:
            raise ValueError(
                f"{self.test} evidence is missing metrics: {', '.join(sorted(missing))}"
            )
        if self.test == "standard_motion_100_cycles":
            cycles = next(
                item.value
                for item in self.measurements
                if item.metric == "completed_cycles"
            )
            if cycles < 100:
                raise ValueError(
                    "standard motion evidence requires at least 100 cycles"
                )
        if self.subject == "profile" and self.spec_sha256 is not None:
            raise ValueError("profile evidence must not be bound to one spec")
        if self.subject == "exact_build" and self.spec_sha256 is None:
            raise ValueError("exact-build evidence must be bound to a spec")

    def signing_payload(self) -> bytes:
        return _canonical_json_bytes(
            {
                "catalog_version": self.catalog_version,
                "measurements": [item.to_dict() for item in self.measurements],
                "passed": self.passed,
                "performed_at": self.performed_at,
                "profile_id": self.profile_id,
                "record_id": self.record_id,
                "signer_id": self.signer_id,
                "spec_sha256": self.spec_sha256,
                "subject": self.subject,
                "subject_sha256": self.subject_sha256,
                "test": self.test,
            }
        )

    def to_dict(self) -> dict[str, object]:
        return {
            **json.loads(self.signing_payload()),
            "signature_sha256": self.signature_sha256,
        }


class EvidenceSignatureVerifier(Protocol):
    def verify(self, record: PhysicalEvidenceRecord) -> bool: ...


class RejectAllEvidenceVerifier:
    """Safe default used until a trusted lab signer is configured."""

    def verify(self, record: PhysicalEvidenceRecord) -> bool:
        return False


class HmacSha256EvidenceVerifier:
    """Verifier for locally controlled lab signers.

    Public deployments should replace this boundary adapter with asymmetric
    verification.  Signing keys are copied on construction and never serialized.
    """

    def __init__(self, signer_keys: Mapping[str, bytes]) -> None:
        copied: dict[str, bytes] = {}
        for signer_id, key in signer_keys.items():
            _require_safe_id(signer_id, "signer ID")
            if not isinstance(key, bytes) or len(key) < 32:
                raise ValueError("evidence signing keys must contain at least 32 bytes")
            copied[signer_id] = bytes(key)
        self._signer_keys = MappingProxyType(copied)

    def verify(self, record: PhysicalEvidenceRecord) -> bool:
        key = self._signer_keys.get(record.signer_id)
        if key is None:
            return False
        expected = hmac.new(key, record.signing_payload(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, record.signature_sha256)


def sign_evidence_record(
    record: PhysicalEvidenceRecord, *, key: bytes
) -> PhysicalEvidenceRecord:
    """Sign an already validated record in a trusted measurement process."""

    if not isinstance(key, bytes) or len(key) < 32:
        raise ValueError("evidence signing keys must contain at least 32 bytes")
    signature = hmac.new(key, record.signing_payload(), hashlib.sha256).hexdigest()
    return replace(record, signature_sha256=signature)


@dataclass(frozen=True, slots=True)
class RejectedEvidence:
    record_id: str
    reason: RejectionReason

    def to_dict(self) -> dict[str, str]:
        return {"record_id": self.record_id, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class EvidenceEvaluation:
    evidence_level: EvidenceLevel
    accepted_record_ids: tuple[str, ...]
    rejected: tuple[RejectedEvidence, ...]
    missing_profile_tests: tuple[EvidenceTest, ...]
    missing_exact_build_tests: tuple[EvidenceTest, ...]
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "accepted_record_ids": list(self.accepted_record_ids),
            "blockers": list(self.blockers),
            "evidence_level": self.evidence_level,
            "missing_exact_build_tests": list(self.missing_exact_build_tests),
            "missing_profile_tests": list(self.missing_profile_tests),
            "rejected": [item.to_dict() for item in self.rejected],
        }


def evaluate_physical_evidence(
    *,
    digital_checks_passed: bool,
    profile_qualification: ProfileQualification,
    profile_id: str,
    catalog_version: str,
    profile_sha256: str,
    spec_sha256: str,
    exact_build_subject_sha256: str | None,
    records: tuple[PhysicalEvidenceRecord, ...] = (),
    verifier: EvidenceSignatureVerifier | None = None,
) -> EvidenceEvaluation:
    """Return the strongest evidence level justified by exact, signed inputs."""

    if _PROFILE_ID.fullmatch(profile_id) is None:
        raise ValueError("profile ID is invalid")
    _require_safe_id(catalog_version, "catalog version")
    _require_sha256(profile_sha256, "profile digest")
    _require_sha256(spec_sha256, "spec digest")
    if exact_build_subject_sha256 is not None:
        _require_sha256(exact_build_subject_sha256, "exact build subject digest")

    if not digital_checks_passed:
        return EvidenceEvaluation(
            evidence_level="concept_only",
            accepted_record_ids=(),
            rejected=(),
            missing_profile_tests=tuple(sorted(PROFILE_REQUIRED_TESTS)),
            missing_exact_build_tests=tuple(sorted(EXACT_BUILD_REQUIRED_TESTS)),
            blockers=("digital_checks_not_passed",),
        )

    verifier = verifier or RejectAllEvidenceVerifier()
    accepted: list[PhysicalEvidenceRecord] = []
    rejected: list[RejectedEvidence] = []
    for record in records:
        reason: RejectionReason | None = None
        if record.profile_id != profile_id:
            reason = "profile_mismatch"
        elif record.catalog_version != catalog_version:
            reason = "catalog_mismatch"
        elif not verifier.verify(record):
            reason = "signature_invalid"
        elif not record.passed:
            reason = "test_failed"
        elif record.subject == "profile" and record.subject_sha256 != profile_sha256:
            reason = "subject_mismatch"
        elif record.subject == "exact_build" and (
            exact_build_subject_sha256 is None
            or record.subject_sha256 != exact_build_subject_sha256
        ):
            reason = "subject_mismatch"
        elif record.subject == "exact_build" and record.spec_sha256 != spec_sha256:
            reason = "spec_mismatch"
        if reason is None:
            accepted.append(record)
        else:
            rejected.append(RejectedEvidence(record.record_id, reason))

    profile_tests = {record.test for record in accepted if record.subject == "profile"}
    exact_tests = {
        record.test for record in accepted if record.subject == "exact_build"
    }
    missing_profile = tuple(sorted(PROFILE_REQUIRED_TESTS.difference(profile_tests)))
    missing_exact = tuple(sorted(EXACT_BUILD_REQUIRED_TESTS.difference(exact_tests)))
    blockers: list[str] = []
    if profile_qualification == "digital_only":
        blockers.append("profile_catalog_is_digital_only")
    if missing_profile:
        blockers.append("signed_profile_evidence_incomplete")

    level: EvidenceLevel = "digital_checks_passed"
    if profile_qualification != "digital_only" and not missing_profile:
        level = "within_qualified_profile"
        if exact_build_subject_sha256 is None:
            blockers.append("exact_build_subject_missing")
        elif missing_exact:
            blockers.append("signed_exact_build_evidence_incomplete")
        else:
            level = "exact_build_verified"

    return EvidenceEvaluation(
        evidence_level=level,
        accepted_record_ids=tuple(sorted(record.record_id for record in accepted)),
        rejected=tuple(sorted(rejected, key=lambda item: item.record_id)),
        missing_profile_tests=missing_profile,
        missing_exact_build_tests=missing_exact,
        blockers=tuple(blockers),
    )


__all__ = [
    "EXACT_BUILD_REQUIRED_TESTS",
    "PROFILE_REQUIRED_TESTS",
    "EvidenceEvaluation",
    "EvidenceSignatureVerifier",
    "HmacSha256EvidenceVerifier",
    "Measurement",
    "PhysicalEvidenceRecord",
    "RejectAllEvidenceVerifier",
    "RejectedEvidence",
    "evaluate_physical_evidence",
    "sign_evidence_record",
]
