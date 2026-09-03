from __future__ import annotations

import asyncio
import json
import hashlib
import zipfile
from dataclasses import dataclass
from dataclasses import replace
from io import BytesIO

import pytest

from character_robot.cad import CadCompiler
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
from character_robot.project_store import ProjectStore
from character_robot.runtime import (
    DEFAULT_RUNTIME_TARGETS,
    RuntimeCatalog,
    RuntimeRelease,
    compile_runtime_bundle,
)
from character_robot.schemas import (
    CharacterRobotSpec,
    CreateRevisionFromDraftInput,
    PositiveVec3,
    PrepareBuildPackInput,
    SetDesignDraftInput,
    ValidationReport,
)
from character_robot.service import CharacterRobotService

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


@dataclass(frozen=True)
class _ManufacturingResult:
    evidence_level: str = "within_qualified_profile"
    issues: tuple[object, ...] = ()


class _QualifiedProfiles:
    def __init__(self, profile: object) -> None:
        self.profile = profile

    def get_profile(self, profile_id: str) -> object:
        if profile_id != self.profile.profile_id:
            raise RuntimeError("profile not found")
        return self.profile

    def list_profiles(self) -> tuple[object, ...]:
        return (self.profile,)


def _qualified_inputs(spec: CharacterRobotSpec, exact_subject_sha: str):
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
    records = tuple(
        sign_evidence_record(
            replace(
                record,
                subject_sha256=(
                    profile_sha if record.subject == "profile" else exact_subject_sha
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
    return profile, records, RuntimeCatalog((published_target,)), instructions


def test_qualified_inputs_have_a_real_non_experimental_completion_path() -> None:
    spec = _spec()
    exact_subject_sha = "c" * 64
    profile, records, runtime_catalog, instructions = _qualified_inputs(
        spec, exact_subject_sha
    )

    result = generate_maker_pack_artifacts(
        spec,
        profile,
        _report(spec).model_copy(update={"evidence_level": "within_qualified_profile"}),
        physical_records=records,
        evidence_verifier=HmacSha256EvidenceVerifier({"local-lab": _KEY}),
        exact_build_subject_sha256=exact_subject_sha,
        runtime_catalog=runtime_catalog,
        qualified_instructions=instructions,
    )

    assert result.evidence_level == "exact_build_verified"
    assert result.replication_ready is True
    assert result.blockers == ()
    assert all(not artifact.experimental for artifact in result.artifacts)
    assert {artifact.file_name for artifact in result.artifacts}.issuperset(
        {"bom.json", "wiring.json", "calibration.json"}
    )


def test_service_only_promotes_evidence_for_its_derived_build_subject(tmp_path) -> None:
    spec = _spec()
    provisional_profile, _, runtime_catalog, instructions = _qualified_inputs(
        spec, "0" * 64
    )
    profiles = _QualifiedProfiles(provisional_profile)
    data_root = tmp_path / "qualified-project"
    database = data_root / "project.sqlite3"

    candidate = CharacterRobotService(
        data_root=data_root,
        project_id="qualified-project",
        profile_registry=profiles,
        cad_compiler=CadCompiler(profiles),
        project_store=ProjectStore(database),
        manufacturing_validator=lambda _spec, _compiled: _ManufacturingResult(),
        runtime_catalog=runtime_catalog,
        qualified_build_instructions=instructions,
    )
    draft = asyncio.run(
        candidate.set_design_draft(
            SetDesignDraftInput(expected_revision=None, spec=spec)
        )
    )
    committed = asyncio.run(
        candidate.create_revision_from_draft(
            CreateRevisionFromDraftInput(
                expected_revision=None,
                draft_hash=draft.draft_hash,
                note="Exact build candidate.",
            )
        )
    )
    request = PrepareBuildPackInput(
        revision_id="r000",
        expected_spec_hash=committed.revision.spec_hash,
    )
    provisional = asyncio.run(candidate.prepare_build_pack(request))
    assert provisional.manifest is not None
    build_subject_hash = provisional.manifest.build_subject_hash

    _, wrong_records, _, _ = _qualified_inputs(spec, "c" * 64)
    mismatched = CharacterRobotService(
        data_root=data_root,
        project_id="qualified-project",
        profile_registry=profiles,
        cad_compiler=CadCompiler(profiles),
        project_store=ProjectStore(database),
        manufacturing_validator=lambda _spec, _compiled: _ManufacturingResult(),
        physical_records=wrong_records,
        evidence_verifier=HmacSha256EvidenceVerifier({"local-lab": _KEY}),
        exact_build_subject_sha256="c" * 64,
        runtime_catalog=runtime_catalog,
        qualified_build_instructions=instructions,
    )
    rejected = asyncio.run(mismatched.prepare_build_pack(request))
    assert rejected.status == "experimental_ready"
    assert rejected.manifest is not None
    assert rejected.manifest.build_subject_hash == build_subject_hash
    assert "exact_build_subject_mismatch" in {issue.code for issue in rejected.blockers}

    _, exact_records, _, _ = _qualified_inputs(spec, build_subject_hash)
    verified = CharacterRobotService(
        data_root=data_root,
        project_id="qualified-project",
        profile_registry=profiles,
        cad_compiler=CadCompiler(profiles),
        project_store=ProjectStore(database),
        manufacturing_validator=lambda _spec, _compiled: _ManufacturingResult(),
        physical_records=exact_records,
        evidence_verifier=HmacSha256EvidenceVerifier({"local-lab": _KEY}),
        exact_build_subject_sha256=build_subject_hash,
        runtime_catalog=runtime_catalog,
        qualified_build_instructions=instructions,
    )
    ready = asyncio.run(verified.prepare_build_pack(request))

    assert ready.status == "ready"
    assert ready.manifest is not None
    assert ready.manifest.evidence_level == "exact_build_verified"
    assert ready.manifest.build_subject_hash == build_subject_hash
    assert all(not artifact.experimental for artifact in ready.manifest.artifacts)
