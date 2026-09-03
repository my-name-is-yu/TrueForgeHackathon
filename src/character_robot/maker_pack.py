from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from typing import Literal, Mapping, Protocol

from .physical_evidence import (
    EvidenceSignatureVerifier,
    PhysicalEvidenceRecord,
    evaluate_physical_evidence,
)
from .runtime import (
    RuntimeBundle,
    RuntimeCatalog,
    compile_runtime_bundle,
    runtime_bundle_manifest,
)
from .schemas import CharacterRobotSpec, ValidationReport


MakerArtifactKind = Literal[
    "bom_json",
    "wiring_json",
    "firmware_config_json",
    "assembly_markdown",
    "runtime_bundle_zip",
    "calibration_json",
    "physical_evidence_json",
]


class MakerPackError(RuntimeError):
    def __init__(self, code: str, safe_message: str) -> None:
        self.code = code
        self.safe_message = safe_message
        self.retryable = False
        super().__init__(safe_message)


class MakerProfileLike(Protocol):
    profile_id: str
    display_name: str
    qualification: str
    capabilities: tuple[str, ...]
    components: tuple[object, ...]
    unknowns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MakerPackArtifact:
    kind: MakerArtifactKind
    file_name: str
    media_type: str
    content: bytes
    experimental: bool

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    @property
    def byte_size(self) -> int:
        return len(self.content)


@dataclass(frozen=True, slots=True)
class MakerPackResult:
    artifacts: tuple[MakerPackArtifact, ...]
    runtime_bundle: RuntimeBundle
    profile_sha256: str
    evidence_level: str
    replication_ready: bool
    blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QualifiedBuildInstructions:
    """Version-bound, complete replication instructions from a trusted catalog."""

    hardware_profile_id: str
    catalog_version: str
    runtime_version: str
    bom: Mapping[str, object]
    wiring: Mapping[str, object]
    calibration: Mapping[str, object]
    assembly_markdown: bytes

    def canonical_payloads(self) -> tuple[bytes, bytes, bytes, bytes]:
        bom = dict(self.bom)
        wiring = dict(self.wiring)
        calibration = dict(self.calibration)
        if (
            bom.get("hardware_profile_id") != self.hardware_profile_id
            or bom.get("catalog_version") != self.catalog_version
            or bom.get("completeness") != "complete"
            or bom.get("procurement_ready") is not True
        ):
            raise MakerPackError(
                "QUALIFIED_BOM_INVALID",
                "Qualified build instructions require a complete, version-bound BOM.",
            )
        if (
            wiring.get("hardware_profile_id") != self.hardware_profile_id
            or wiring.get("runtime_version") != self.runtime_version
            or wiring.get("complete") is not True
            or wiring.get("energize_ready") is not True
        ):
            raise MakerPackError(
                "QUALIFIED_WIRING_INVALID",
                "Qualified build instructions require complete, version-bound wiring.",
            )
        measurements = calibration.get("measurements")
        if (
            calibration.get("hardware_profile_id") != self.hardware_profile_id
            or calibration.get("status") != "measured"
            or calibration.get("required_before_motion") is not False
            or not isinstance(measurements, dict)
            or not measurements
            or any(value is None for value in measurements.values())
        ):
            raise MakerPackError(
                "QUALIFIED_CALIBRATION_INVALID",
                "Qualified build instructions require complete measured calibration values.",
            )
        if (
            not isinstance(self.assembly_markdown, bytes)
            or not 1 <= len(self.assembly_markdown) <= 1024 * 1024
        ):
            raise MakerPackError(
                "QUALIFIED_ASSEMBLY_INVALID",
                "Qualified build instructions require bounded assembly instructions.",
            )
        return (
            _canonical_json_bytes(bom),
            _canonical_json_bytes(wiring),
            _canonical_json_bytes(calibration),
            self.assembly_markdown,
        )


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _profile_payload(profile: MakerProfileLike) -> dict[str, object]:
    to_dict = getattr(profile, "to_dict", None)
    if callable(to_dict):
        value = to_dict()
    elif is_dataclass(profile):
        value = asdict(profile)
    else:
        value = {
            "capabilities": list(profile.capabilities),
            "components": [
                {
                    "component_id": getattr(component, "component_id"),
                    "display_name": getattr(component, "display_name"),
                    "included_in": getattr(component, "included_in", None),
                    "quantity": getattr(component, "quantity"),
                }
                for component in profile.components
            ],
            "display_name": profile.display_name,
            "profile_id": profile.profile_id,
            "qualification": profile.qualification,
            "unknowns": list(profile.unknowns),
        }
    if not isinstance(value, dict):
        raise MakerPackError(
            "HARDWARE_PROFILE_INVALID",
            "The hardware profile could not be serialized for evidence binding.",
        )
    return value


def _component_payload(component: object) -> dict[str, object]:
    return {
        "component_id": str(getattr(component, "component_id")),
        "display_name": str(getattr(component, "display_name")),
        "included_in": getattr(component, "included_in", None),
        "quantity": int(getattr(component, "quantity")),
        "selection_status": "profile_component",
    }


_SUPPLEMENTAL_BOM = (
    {
        "component_id": "wheel-motor",
        "display_name": "Qualified low-voltage wheel motor",
        "quantity": 2,
        "selection_status": "unselected",
    },
    {
        "component_id": "drive-wheel",
        "display_name": "Wheel matched to motor and axle",
        "quantity": 2,
        "selection_status": "unselected",
    },
    {
        "component_id": "pan-tilt-servo",
        "display_name": "Qualified pan/tilt servo",
        "quantity": 2,
        "selection_status": "unselected",
    },
    {
        "component_id": "battery-and-protection",
        "display_name": "Qualified battery, protection, and main switch",
        "quantity": 1,
        "selection_status": "unselected",
    },
    {
        "component_id": "printed-part-set",
        "display_name": "Exact revision printed parts",
        "quantity": 1,
        "selection_status": "generated_geometry_unverified",
    },
    {
        "component_id": "fastener-set",
        "display_name": "Measured fasteners and threaded inserts",
        "quantity": 1,
        "selection_status": "unselected",
    },
)


_WIRING_BY_PROFILE: dict[str, tuple[dict[str, object], ...]] = {
    "m5-cores3-goplus2/v1": (
        {
            "from": "cores3.internal-stack",
            "function": "controller_bus",
            "status": "catalog_documented_build_unverified",
            "to": "goplus2.mbus",
        },
        {
            "from": "goplus2.motor-1",
            "function": "left_wheel_drive",
            "status": "motor_and_polarity_unselected",
            "to": "left-wheel-motor",
        },
        {
            "from": "goplus2.motor-2",
            "function": "right_wheel_drive",
            "status": "motor_and_polarity_unselected",
            "to": "right-wheel-motor",
        },
        {
            "from": "goplus2.servo-1",
            "function": "head_pan",
            "status": "servo_and_limits_unselected",
            "to": "pan-servo",
        },
        {
            "from": "goplus2.servo-2",
            "function": "head_tilt",
            "status": "servo_and_limits_unselected",
            "to": "tilt-servo",
        },
        {
            "from": "battery-and-protection",
            "function": "main_power",
            "status": "voltage_current_and_connector_unselected",
            "to": "goplus2.power-input",
        },
    ),
    "pi-zero2wh-crickit-ws2/v1": (
        {
            "from": "pi-zero-2-wh.gpio-header",
            "function": "drive_io",
            "status": "catalog_documented_build_unverified",
            "to": "crickit-hat.header",
        },
        {
            "from": "pi-zero-2-wh.spi",
            "function": "face_display",
            "status": "gpio_assignment_and_level_check_required",
            "to": "waveshare-2inch-lcd.spi",
        },
        {
            "from": "crickit-hat.motor-1",
            "function": "left_wheel_drive",
            "status": "motor_and_polarity_unselected",
            "to": "left-wheel-motor",
        },
        {
            "from": "crickit-hat.motor-2",
            "function": "right_wheel_drive",
            "status": "motor_and_polarity_unselected",
            "to": "right-wheel-motor",
        },
        {
            "from": "crickit-hat.servo-1",
            "function": "head_pan",
            "status": "servo_and_limits_unselected",
            "to": "pan-servo",
        },
        {
            "from": "crickit-hat.servo-2",
            "function": "head_tilt",
            "status": "servo_and_limits_unselected",
            "to": "tilt-servo",
        },
        {
            "from": "battery-and-protection",
            "function": "main_power",
            "status": "voltage_current_grounding_and_connector_unselected",
            "to": "crickit-hat.power-input",
        },
    ),
}


def _bom_payload(
    spec: CharacterRobotSpec, profile: MakerProfileLike
) -> dict[str, object]:
    return {
        "catalog_version": spec.versions.catalog,
        "completeness": "provisional",
        "components": [
            *(_component_payload(component) for component in profile.components),
            *(_SUPPLEMENTAL_BOM),
        ],
        "hardware_profile_id": profile.profile_id,
        "procurement_ready": False,
        "qualification": profile.qualification,
        "unresolved": list(profile.unknowns),
    }


def _wiring_payload(
    spec: CharacterRobotSpec, profile: MakerProfileLike
) -> dict[str, object]:
    try:
        connections = _WIRING_BY_PROFILE[profile.profile_id]
    except KeyError:
        raise MakerPackError(
            "WIRING_PROFILE_UNSUPPORTED",
            "No fixed wiring contract exists for this hardware profile.",
        ) from None
    return {
        "complete": False,
        "connections": list(connections),
        "energize_ready": False,
        "hardware_profile_id": profile.profile_id,
        "runtime_version": spec.versions.firmware_runtime,
        "status": "provisional_requires_measurement",
        "warnings": [
            "Do not energize before motor, servo, battery, polarity, continuity, and current checks are recorded.",
            *profile.unknowns,
        ],
    }


def _assembly_markdown(
    spec: CharacterRobotSpec,
    profile: MakerProfileLike,
    runtime_bundle: RuntimeBundle,
) -> bytes:
    platform_step = (
        "Install the separately published CoreS3 firmware release whose digest matches runtime-lock.json."
        if runtime_bundle.target.platform == "esp32-s3"
        else "Install the separately published Pi system-service release whose digest matches runtime-lock.json."
    )
    return (
        "# Assembly and calibration — provisional\n\n"
        f"Profile: `{profile.profile_id}`  \n"
        f"Spec: `{runtime_bundle.spec_sha256}`\n\n"
        "## Stop condition\n\n"
        "This package is not ready to energize or reproduce. Hardware dimensions, "
        "motor and servo selection, wiring, current, thermal behavior, and mechanical "
        "clearance require recorded measurements. A digital validation result is not a "
        "manufacturing or safety claim.\n\n"
        "## Ordered procedure\n\n"
        "1. Verify every purchased part against the provisional BOM and record exact part numbers.\n"
        "2. Measure boards, holes, connectors, motors, servos, wheels, battery, and fasteners.\n"
        "3. Recompile the exact Spec and compare every artifact digest before printing.\n"
        "4. Dry-fit unpowered hardware; verify connector access and full wheel/neck swept volumes.\n"
        "5. Complete `calibration-template.json`; keep motor power disconnected while checking logic I/O.\n"
        "6. Perform polarity and continuity tests, then use a current-limited supply for first power.\n"
        f"7. {platform_step}\n"
        "8. Calibrate wheel diameter, axle track, servo centers, and conservative motion limits.\n"
        "9. Record thermal, brownout, stop-distance, 100-cycle motion, and emergency-stop evidence.\n"
        "10. Seal signed measurements against the exact Spec and Build Pack digests.\n"
    ).encode()


def generate_maker_pack_artifacts(
    spec: CharacterRobotSpec,
    profile: MakerProfileLike,
    validation_report: ValidationReport,
    *,
    physical_records: tuple[PhysicalEvidenceRecord, ...] = (),
    evidence_verifier: EvidenceSignatureVerifier | None = None,
    exact_build_manifest_sha256: str | None = None,
    runtime_catalog: RuntimeCatalog | None = None,
    qualified_instructions: QualifiedBuildInstructions | None = None,
) -> MakerPackResult:
    runtime_bundle = compile_runtime_bundle(spec, profile, catalog=runtime_catalog)
    if validation_report.spec_hash != runtime_bundle.spec_sha256:
        raise MakerPackError(
            "VALIDATION_SPEC_MISMATCH",
            "The validation report does not belong to the Build Pack Spec.",
        )
    profile_payload = _profile_payload(profile)
    profile_sha256 = hashlib.sha256(_canonical_json_bytes(profile_payload)).hexdigest()
    qualification = profile.qualification
    if qualification not in {
        "digital_only",
        "profile_qualified",
        "exact_build_verified",
    }:
        raise MakerPackError(
            "HARDWARE_PROFILE_INVALID",
            "The hardware profile has an unsupported qualification state.",
        )
    evidence = evaluate_physical_evidence(
        digital_checks_passed=validation_report.passed,
        profile_qualification=qualification,
        profile_id=profile.profile_id,
        catalog_version=spec.versions.catalog,
        profile_sha256=profile_sha256,
        spec_sha256=runtime_bundle.spec_sha256,
        exact_build_manifest_sha256=exact_build_manifest_sha256,
        records=physical_records,
        verifier=evidence_verifier,
    )
    manufacturing_qualified = validation_report.evidence_level in {
        "within_qualified_profile",
        "exact_build_verified",
    }
    effective_evidence_level = (
        evidence.evidence_level
        if manufacturing_qualified
        else ("digital_checks_passed" if validation_report.passed else "concept_only")
    )
    if qualified_instructions is None:
        bom_content = _canonical_json_bytes(_bom_payload(spec, profile))
        wiring_content = _canonical_json_bytes(_wiring_payload(spec, profile))
        calibration_content = next(
            file.content
            for file in runtime_bundle.files
            if file.path == "calibration-template.json"
        )
        assembly_content = _assembly_markdown(spec, profile, runtime_bundle)
        instruction_file_names = (
            "provisional-bom.json",
            "provisional-wiring.json",
            "calibration-template.json",
        )
        instruction_blockers = (
            "provisional_bom_incomplete",
            "provisional_wiring_incomplete",
            "calibration_unmeasured",
        )
    else:
        if (
            qualified_instructions.hardware_profile_id != profile.profile_id
            or qualified_instructions.catalog_version != spec.versions.catalog
            or qualified_instructions.runtime_version != spec.versions.firmware_runtime
        ):
            raise MakerPackError(
                "QUALIFIED_INSTRUCTIONS_VERSION_MISMATCH",
                "Qualified build instructions do not match the exact profile and versions.",
            )
        (
            bom_content,
            wiring_content,
            calibration_content,
            assembly_content,
        ) = qualified_instructions.canonical_payloads()
        instruction_file_names = ("bom.json", "wiring.json", "calibration.json")
        instruction_blockers = ()

    blockers = tuple(
        dict.fromkeys(
            (
                *runtime_bundle.blockers,
                *evidence.blockers,
                *instruction_blockers,
                *(
                    ()
                    if manufacturing_qualified
                    else ("manufacturing_validation_not_qualified",)
                ),
            )
        )
    )
    replication_ready = (
        effective_evidence_level == "exact_build_verified" and not blockers
    )
    experimental = not replication_ready
    character = next(
        file for file in runtime_bundle.files if file.path == "character.json"
    )
    evidence_payload = {
        "effective_evidence_level": effective_evidence_level,
        "evaluation": evidence.to_dict(),
        "exact_build_manifest_sha256": exact_build_manifest_sha256,
        "hardware_profile_id": profile.profile_id,
        "profile_sha256": profile_sha256,
        "records": [record.to_dict() for record in physical_records],
        "replication_ready": replication_ready,
        "runtime": dict(runtime_bundle_manifest(runtime_bundle)),
        "spec_sha256": runtime_bundle.spec_sha256,
    }
    artifacts = (
        MakerPackArtifact(
            kind="bom_json",
            file_name=instruction_file_names[0],
            media_type="application/json",
            content=bom_content,
            experimental=experimental,
        ),
        MakerPackArtifact(
            kind="wiring_json",
            file_name=instruction_file_names[1],
            media_type="application/json",
            content=wiring_content,
            experimental=experimental,
        ),
        MakerPackArtifact(
            kind="firmware_config_json",
            file_name="character.json",
            media_type="application/json",
            content=character.content,
            experimental=experimental,
        ),
        MakerPackArtifact(
            kind="assembly_markdown",
            file_name="ASSEMBLY-AND-CALIBRATION.md",
            media_type="text/markdown",
            content=assembly_content,
            experimental=experimental,
        ),
        MakerPackArtifact(
            kind="runtime_bundle_zip",
            file_name="runtime-config.zip",
            media_type="application/zip",
            content=runtime_bundle.zip_bytes,
            experimental=experimental,
        ),
        MakerPackArtifact(
            kind="calibration_json",
            file_name=instruction_file_names[2],
            media_type="application/json",
            content=calibration_content,
            experimental=experimental,
        ),
        MakerPackArtifact(
            kind="physical_evidence_json",
            file_name="physical-evidence-gate.json",
            media_type="application/json",
            content=_canonical_json_bytes(evidence_payload),
            experimental=experimental,
        ),
    )
    return MakerPackResult(
        artifacts=artifacts,
        runtime_bundle=runtime_bundle,
        profile_sha256=profile_sha256,
        evidence_level=effective_evidence_level,
        replication_ready=replication_ready,
        blockers=blockers,
    )


__all__ = [
    "MakerArtifactKind",
    "MakerPackArtifact",
    "MakerPackError",
    "MakerPackResult",
    "QualifiedBuildInstructions",
    "generate_maker_pack_artifacts",
]
