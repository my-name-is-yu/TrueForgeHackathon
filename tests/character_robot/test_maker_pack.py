from __future__ import annotations

import json
import hashlib
import zipfile
from dataclasses import replace
from io import BytesIO

import pytest

from character_robot.maker_pack import (
    MakerPackError,
    QualifiedBuildInstructions,
    generate_maker_pack_artifacts,
)
from character_robot.physical_evidence import (
    HmacSha256EvidenceVerifier,
    sign_evidence_record,
)
from character_robot.profiles import ProfileRegistry
from character_robot.runtime import (
    DEFAULT_RUNTIME_TARGETS,
    RuntimeCatalog,
    RuntimeRelease,
    compile_runtime_bundle,
)
from character_robot.schemas import CharacterRobotSpec, PositiveVec3, ValidationReport

from test_domain_schemas import _spec_payload
from test_physical_evidence import _KEY, _all_records


def _spec(profile_id: str = "m5-cores3-goplus2/v1") -> CharacterRobotSpec:
    payload = _spec_payload()
    payload["hardware_profile_id"] = profile_id
    return CharacterRobotSpec.model_validate(payload)


def _report(spec: CharacterRobotSpec, *, passed: bool = True) -> ValidationReport:
    spec_hash = compile_runtime_bundle(
        spec, ProfileRegistry().get_profile(spec.hardware_profile_id)
    ).spec_sha256
    return ValidationReport(
        spec_hash=spec_hash,
        evidence_level="digital_checks_passed" if passed else "concept_only",
        passed=passed,
        dimensions_mm=PositiveVec3(x=120.0, y=110.0, z=160.0),
        issues=[],
        report_hash="f" * 64,
    )


@pytest.mark.parametrize(
    "profile_id",
    ["m5-cores3-goplus2/v1", "pi-zero2wh-crickit-ws2/v1"],
)
def test_maker_pack_emits_deterministic_provisional_artifacts(profile_id: str) -> None:
    spec = _spec(profile_id)
    profile = ProfileRegistry().get_profile(profile_id)

    first = generate_maker_pack_artifacts(spec, profile, _report(spec))
    second = generate_maker_pack_artifacts(spec, profile, _report(spec))

    assert [artifact.kind for artifact in first.artifacts] == [
        "bom_json",
        "wiring_json",
        "firmware_config_json",
        "assembly_markdown",
        "runtime_bundle_zip",
        "calibration_json",
        "physical_evidence_json",
    ]
    assert [(item.sha256, item.byte_size) for item in first.artifacts] == [
        (item.sha256, item.byte_size) for item in second.artifacts
    ]
    assert first.replication_ready is False
    assert first.evidence_level == "digital_checks_passed"
    assert "runtime_release_not_published" in first.blockers
    assert "profile_catalog_is_digital_only" in first.blockers
    assert "provisional_bom_incomplete" in first.blockers
    assert "provisional_wiring_incomplete" in first.blockers
    assert "calibration_unmeasured" in first.blockers
    assert all(artifact.experimental for artifact in first.artifacts)

    by_kind = {artifact.kind: artifact for artifact in first.artifacts}
    bom = json.loads(by_kind["bom_json"].content)
    assert bom["completeness"] == "provisional"
    assert bom["procurement_ready"] is False
    assert any(
        component["selection_status"] == "unselected" for component in bom["components"]
    )
    wiring = json.loads(by_kind["wiring_json"].content)
    assert wiring["complete"] is False
    assert wiring["energize_ready"] is False
    calibration = json.loads(by_kind["calibration_json"].content)
    assert calibration["status"] == "unmeasured"
    assert calibration["required_before_motion"] is True
    assert all(value is None for value in calibration["measurements"].values())
    assert b"not ready to energize or reproduce" in by_kind["assembly_markdown"].content

    with zipfile.ZipFile(BytesIO(by_kind["runtime_bundle_zip"].content)) as archive:
        assert "character.json" in archive.namelist()
        assert "runtime-lock.json" in archive.namelist()


def test_maker_pack_rejects_validation_for_a_different_spec() -> None:
    spec = _spec()
    profile = ProfileRegistry().get_profile(spec.hardware_profile_id)
    report = _report(spec).model_copy(update={"spec_hash": "e" * 64})

    with pytest.raises(MakerPackError) as caught:
        generate_maker_pack_artifacts(spec, profile, report)

    assert caught.value.code == "VALIDATION_SPEC_MISMATCH"


def test_qualified_inputs_have_a_real_non_experimental_completion_path() -> None:
    spec = _spec()
    profile = replace(
        ProfileRegistry().get_profile(spec.hardware_profile_id),
        qualification="profile_qualified",
        unknowns=(),
    )
    profile_sha = hashlib.sha256(
        json.dumps(
            profile.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    spec_sha = compile_runtime_bundle(spec, profile).spec_sha256
    exact_manifest_sha = "c" * 64
    records = tuple(
        sign_evidence_record(
            replace(
                record,
                subject_sha256=(
                    profile_sha if record.subject == "profile" else exact_manifest_sha
                ),
                spec_sha256=None if record.subject == "profile" else spec_sha,
                signature_sha256="0" * 64,
            ),
            key=_KEY,
        )
        for record in _all_records()
    )
    published_target = replace(
        DEFAULT_RUNTIME_TARGETS[0],
        release=RuntimeRelease(
            status="published",
            file_name="character-runtime-v1.bin",
            media_type="application/octet-stream",
            sha256="d" * 64,
        ),
    )
    instructions = QualifiedBuildInstructions(
        hardware_profile_id=profile.profile_id,
        catalog_version=spec.versions.catalog,
        runtime_version=spec.versions.firmware_runtime,
        bom={
            "hardware_profile_id": profile.profile_id,
            "catalog_version": spec.versions.catalog,
            "completeness": "complete",
            "procurement_ready": True,
            "components": [{"part_number": "verified-part", "quantity": 1}],
        },
        wiring={
            "hardware_profile_id": profile.profile_id,
            "runtime_version": spec.versions.firmware_runtime,
            "complete": True,
            "energize_ready": True,
            "connections": [{"from": "supply", "to": "controller"}],
        },
        calibration={
            "hardware_profile_id": profile.profile_id,
            "status": "measured",
            "required_before_motion": False,
            "measurements": {"axle_track_mm": 72.4, "pan_center_pulse_us": 1502},
        },
        assembly_markdown=b"# Verified assembly\n\nFollow the qualified lab procedure.\n",
    )

    result = generate_maker_pack_artifacts(
        spec,
        profile,
        _report(spec).model_copy(update={"evidence_level": "within_qualified_profile"}),
        physical_records=records,
        evidence_verifier=HmacSha256EvidenceVerifier({"local-lab": _KEY}),
        exact_build_manifest_sha256=exact_manifest_sha,
        runtime_catalog=RuntimeCatalog((published_target,)),
        qualified_instructions=instructions,
    )

    assert result.evidence_level == "exact_build_verified"
    assert result.replication_ready is True
    assert result.blockers == ()
    assert all(not artifact.experimental for artifact in result.artifacts)
    assert {artifact.file_name for artifact in result.artifacts}.issuperset(
        {"bom.json", "wiring.json", "calibration.json"}
    )
