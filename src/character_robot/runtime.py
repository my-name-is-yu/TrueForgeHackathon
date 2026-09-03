from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping, Protocol

from .schemas import CharacterRobotSpec


RuntimePlatform = Literal["esp32-s3", "linux-aarch64"]
DeploymentMode = Literal["prebuilt_firmware", "system_service"]
ReleaseStatus = Literal["not_published", "published"]

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUNTIME_VERSION = "character-runtime-v1"


class RuntimeBundleError(RuntimeError):
    def __init__(self, code: str, safe_message: str) -> None:
        self.code = code
        self.safe_message = safe_message
        self.retryable = False
        super().__init__(safe_message)


class HardwareProfileLike(Protocol):
    profile_id: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RuntimeRelease:
    status: ReleaseStatus
    file_name: str | None = None
    media_type: str | None = None
    sha256: str | None = None

    def __post_init__(self) -> None:
        fields = (self.file_name, self.media_type, self.sha256)
        if self.status == "published" and any(value is None for value in fields):
            raise ValueError(
                "published runtime releases require complete artifact metadata"
            )
        if self.status == "not_published" and any(
            value is not None for value in fields
        ):
            raise ValueError(
                "unpublished runtime releases cannot advertise an artifact"
            )
        if self.sha256 is not None and _SHA256.fullmatch(self.sha256) is None:
            raise ValueError("runtime release digest must be a lowercase SHA-256")

    def to_dict(self) -> dict[str, object]:
        return {
            "file_name": self.file_name,
            "media_type": self.media_type,
            "sha256": self.sha256,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class RuntimeTarget:
    target_id: str
    runtime_version: str
    hardware_profile_id: str
    platform: RuntimePlatform
    deployment_mode: DeploymentMode
    required_capabilities: tuple[str, ...]
    release: RuntimeRelease

    def to_dict(self) -> dict[str, object]:
        return {
            "deployment_mode": self.deployment_mode,
            "hardware_profile_id": self.hardware_profile_id,
            "platform": self.platform,
            "release": self.release.to_dict(),
            "required_capabilities": list(self.required_capabilities),
            "runtime_version": self.runtime_version,
            "target_id": self.target_id,
        }


DEFAULT_RUNTIME_TARGETS: tuple[RuntimeTarget, ...] = (
    RuntimeTarget(
        target_id="cores3-goplus2",
        runtime_version=_RUNTIME_VERSION,
        hardware_profile_id="m5-cores3-goplus2/v1",
        platform="esp32-s3",
        deployment_mode="prebuilt_firmware",
        required_capabilities=(
            "differential_drive",
            "head_pan_tilt",
            "display:320x240_touch",
            "speaker",
        ),
        release=RuntimeRelease(status="not_published"),
    ),
    RuntimeTarget(
        target_id="pi-zero2wh-crickit-ws2",
        runtime_version=_RUNTIME_VERSION,
        hardware_profile_id="pi-zero2wh-crickit-ws2/v1",
        platform="linux-aarch64",
        deployment_mode="system_service",
        required_capabilities=(
            "differential_drive",
            "head_pan_tilt",
            "display:320x240_spi",
            "linux_runtime",
        ),
        release=RuntimeRelease(status="not_published"),
    ),
)


class RuntimeCatalog:
    def __init__(
        self, targets: tuple[RuntimeTarget, ...] = DEFAULT_RUNTIME_TARGETS
    ) -> None:
        by_key = {
            (target.runtime_version, target.hardware_profile_id): target
            for target in targets
        }
        if len(by_key) != len(targets):
            raise ValueError("runtime target compatibility keys must be unique")
        self._targets = MappingProxyType(by_key)

    def resolve(
        self,
        *,
        runtime_version: str,
        hardware_profile: HardwareProfileLike,
    ) -> RuntimeTarget:
        target = self._targets.get((runtime_version, hardware_profile.profile_id))
        if target is None:
            raise RuntimeBundleError(
                "RUNTIME_PROFILE_INCOMPATIBLE",
                "The requested common runtime does not support this hardware profile.",
            )
        missing = sorted(
            set(target.required_capabilities).difference(hardware_profile.capabilities)
        )
        if missing:
            raise RuntimeBundleError(
                "RUNTIME_PROFILE_CAPABILITY_MISSING",
                "The hardware profile does not provide every capability required by its runtime.",
            )
        return target


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True, slots=True)
class RuntimeBundleFile:
    path: str
    media_type: str
    content: bytes

    @property
    def sha256(self) -> str:
        return _sha256(self.content)

    def descriptor(self) -> dict[str, object]:
        return {
            "byte_size": len(self.content),
            "media_type": self.media_type,
            "path": self.path,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class RuntimeBundle:
    target: RuntimeTarget
    spec_sha256: str
    configuration_sha256: str
    files: tuple[RuntimeBundleFile, ...]
    zip_bytes: bytes
    install_ready: bool
    blockers: tuple[str, ...]

    @property
    def sha256(self) -> str:
        return _sha256(self.zip_bytes)


def _character_configuration(
    spec: CharacterRobotSpec, target: RuntimeTarget, spec_sha256: str
) -> dict[str, object]:
    return {
        "appearance": spec.appearance.model_dump(mode="json"),
        "behavior": spec.behavior.model_dump(mode="json"),
        "constraints": {
            "indoor_only": spec.constraints.indoor_only,
            "low_voltage_only": spec.constraints.low_voltage_only,
            "maximum_speed_m_s": spec.constraints.maximum_speed_m_s,
        },
        "face": spec.face.model_dump(mode="json"),
        "hardware_profile_id": spec.hardware_profile_id,
        "identity": spec.identity.model_dump(mode="json"),
        "personality": spec.personality.model_dump(mode="json"),
        "runtime_target_id": target.target_id,
        "runtime_version": target.runtime_version,
        "schema_version": spec.versions.schema_version,
        "spec_sha256": spec_sha256,
    }


def _calibration_template(spec: CharacterRobotSpec) -> dict[str, object]:
    return {
        "hardware_profile_id": spec.hardware_profile_id,
        "measurements": {
            "axle_track_mm": None,
            "left_motor_forward_polarity": None,
            "left_wheel_diameter_mm": None,
            "pan_center_pulse_us": None,
            "pan_safe_max_deg": None,
            "pan_safe_min_deg": None,
            "right_motor_forward_polarity": None,
            "right_wheel_diameter_mm": None,
            "tilt_center_pulse_us": None,
            "tilt_safe_max_deg": None,
            "tilt_safe_min_deg": None,
        },
        "required_before_motion": True,
        "status": "unmeasured",
    }


def _normalized_zip(files: tuple[RuntimeBundleFile, ...]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        for file in sorted(files, key=lambda item: item.path):
            info = zipfile.ZipInfo(file.path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, file.content)
    return stream.getvalue()


def compile_runtime_bundle(
    spec: CharacterRobotSpec,
    hardware_profile: HardwareProfileLike,
    *,
    catalog: RuntimeCatalog | None = None,
) -> RuntimeBundle:
    """Compile declarative inputs for a fixed runtime target.

    The ZIP never contains source code or an executable produced from user input.
    A separately published runtime release must match the digest in ``runtime-lock``
    before installation can be considered ready.
    """

    if spec.hardware_profile_id != hardware_profile.profile_id:
        raise RuntimeBundleError(
            "SPEC_PROFILE_MISMATCH",
            "The CharacterRobotSpec and hardware profile do not identify the same target.",
        )
    target = (catalog or RuntimeCatalog()).resolve(
        runtime_version=spec.versions.firmware_runtime,
        hardware_profile=hardware_profile,
    )
    spec_json = spec.model_dump(mode="json")
    spec_sha256 = _sha256(_canonical_json_bytes(spec_json))
    character = _character_configuration(spec, target, spec_sha256)
    character_bytes = _canonical_json_bytes(character)
    calibration_bytes = _canonical_json_bytes(_calibration_template(spec))
    configuration_sha256 = _sha256(character_bytes)
    lock = {
        "configuration_sha256": configuration_sha256,
        "executable_included": False,
        "hardware_profile_id": target.hardware_profile_id,
        "release": target.release.to_dict(),
        "runtime_contract_sha256": _sha256(_canonical_json_bytes(target.to_dict())),
        "runtime_target_id": target.target_id,
        "runtime_version": target.runtime_version,
    }
    readme = (
        "Character Robot fixed-runtime configuration bundle\n"
        f"Target: {target.target_id}\n"
        f"Runtime: {target.runtime_version}\n"
        "This bundle is declarative configuration only. It contains no generated "
        "firmware source or executable. Install only a separately published runtime "
        "whose SHA-256 matches runtime-lock.json. Complete calibration before motion.\n"
    ).encode()
    files = (
        RuntimeBundleFile(path="README.txt", media_type="text/plain", content=readme),
        RuntimeBundleFile(
            path="calibration-template.json",
            media_type="application/json",
            content=calibration_bytes,
        ),
        RuntimeBundleFile(
            path="character.json",
            media_type="application/json",
            content=character_bytes,
        ),
        RuntimeBundleFile(
            path="runtime-lock.json",
            media_type="application/json",
            content=_canonical_json_bytes(lock),
        ),
    )
    install_ready = target.release.status == "published"
    blockers = () if install_ready else ("runtime_release_not_published",)
    return RuntimeBundle(
        target=target,
        spec_sha256=spec_sha256,
        configuration_sha256=configuration_sha256,
        files=files,
        zip_bytes=_normalized_zip(files),
        install_ready=install_ready,
        blockers=blockers,
    )


def runtime_bundle_manifest(bundle: RuntimeBundle) -> Mapping[str, object]:
    return MappingProxyType(
        {
            "blockers": list(bundle.blockers),
            "bundle_sha256": bundle.sha256,
            "configuration_sha256": bundle.configuration_sha256,
            "files": [file.descriptor() for file in bundle.files],
            "install_ready": bundle.install_ready,
            "runtime_target": bundle.target.to_dict(),
            "spec_sha256": bundle.spec_sha256,
        }
    )


__all__ = [
    "DEFAULT_RUNTIME_TARGETS",
    "RuntimeBundle",
    "RuntimeBundleError",
    "RuntimeBundleFile",
    "RuntimeCatalog",
    "RuntimeRelease",
    "RuntimeTarget",
    "compile_runtime_bundle",
    "runtime_bundle_manifest",
]
