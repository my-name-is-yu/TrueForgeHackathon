from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from io import BytesIO

import pytest

from character_robot.profiles import ProfileRegistry
from character_robot.runtime import (
    RuntimeBundleError,
    compile_runtime_bundle,
)
from character_robot.schemas import CharacterRobotSpec

from test_domain_schemas import _spec_payload


def _spec(profile_id: str = "m5-cores3-goplus2/v1") -> CharacterRobotSpec:
    payload = _spec_payload()
    payload["hardware_profile_id"] = profile_id
    return CharacterRobotSpec.model_validate(payload)


def test_runtime_bundle_is_byte_deterministic_and_contains_only_configuration() -> None:
    profile = ProfileRegistry().get_profile("m5-cores3-goplus2/v1")

    first = compile_runtime_bundle(_spec(), profile)
    second = compile_runtime_bundle(_spec(), profile)

    assert first.zip_bytes == second.zip_bytes
    assert first.sha256 == second.sha256
    assert first.install_ready is False
    assert first.blockers == ("runtime_release_not_published",)
    with zipfile.ZipFile(BytesIO(first.zip_bytes)) as archive:
        assert archive.namelist() == [
            "README.txt",
            "calibration-template.json",
            "character.json",
            "runtime-lock.json",
        ]
        assert all(
            item.date_time == (1980, 1, 1, 0, 0, 0) for item in archive.infolist()
        )
        assert not any(
            name.endswith((".bin", ".cpp", ".ino", ".py", ".sh"))
            for name in archive.namelist()
        )
        lock = json.loads(archive.read("runtime-lock.json"))
        assert lock["executable_included"] is False
        assert lock["release"] == {
            "file_name": None,
            "media_type": None,
            "sha256": None,
            "status": "not_published",
        }
        config = json.loads(archive.read("character.json"))
        assert config["spec_sha256"] == first.spec_sha256
        assert config["hardware_profile_id"] == profile.profile_id


def test_runtime_targets_are_explicitly_bound_to_each_profile_family() -> None:
    registry = ProfileRegistry()
    m5 = compile_runtime_bundle(_spec(), registry.get_profile("m5-cores3-goplus2/v1"))
    pi = compile_runtime_bundle(
        _spec("pi-zero2wh-crickit-ws2/v1"),
        registry.get_profile("pi-zero2wh-crickit-ws2/v1"),
    )

    assert m5.target.target_id == "cores3-goplus2"
    assert m5.target.platform == "esp32-s3"
    assert m5.target.deployment_mode == "prebuilt_firmware"
    assert pi.target.target_id == "pi-zero2wh-crickit-ws2"
    assert pi.target.platform == "linux-aarch64"
    assert pi.target.deployment_mode == "system_service"
    assert m5.sha256 != pi.sha256


def test_runtime_bundle_rejects_a_profile_different_from_the_spec() -> None:
    pi = ProfileRegistry().get_profile("pi-zero2wh-crickit-ws2/v1")

    with pytest.raises(RuntimeBundleError) as caught:
        compile_runtime_bundle(_spec(), pi)

    assert caught.value.code == "SPEC_PROFILE_MISMATCH"


@dataclass(frozen=True)
class _IncompleteProfile:
    profile_id: str = "m5-cores3-goplus2/v1"
    capabilities: tuple[str, ...] = ("differential_drive",)


def test_runtime_bundle_rejects_missing_profile_capabilities() -> None:
    with pytest.raises(RuntimeBundleError) as caught:
        compile_runtime_bundle(_spec(), _IncompleteProfile())

    assert caught.value.code == "RUNTIME_PROFILE_CAPABILITY_MISSING"
