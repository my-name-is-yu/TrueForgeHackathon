from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping, cast

from pydantic import ValidationError

from .cad import (
    Bounds3D,
    CadCompileError,
    CadCompileResult,
    CadIssue,
    CadPartMetadata,
    CompiledArtifact,
)
from .profiles import HardwareProfile, ProfileRegistry, hardware_profile_from_dict
from .schemas import CharacterRobotSpec


CAD_JOB_TRANSPORT_VERSION = "character-cad-job/v2"
_DEFAULT_WORKER_MODULE = "character_robot.cad_worker"
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")


@dataclass(frozen=True, slots=True)
class CadJobLimits:
    wall_timeout_seconds: float = 60.0
    cpu_seconds: int = 60
    max_input_bytes: int = 4 * 1024 * 1024
    max_result_bytes: int = 384 * 1024 * 1024
    max_file_bytes: int = 384 * 1024 * 1024
    max_open_files: int = 128
    memory_limit_bytes: int | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.wall_timeout_seconds, bool)
            or not math.isfinite(self.wall_timeout_seconds)
            or not 0.001 <= self.wall_timeout_seconds <= 600.0
        ):
            raise ValueError("wall_timeout_seconds must be between 0.001 and 600")
        bounded = {
            "cpu_seconds": (self.cpu_seconds, 1, 600),
            "max_input_bytes": (self.max_input_bytes, 1024, 16 * 1024 * 1024),
            "max_result_bytes": (
                self.max_result_bytes,
                1024,
                512 * 1024 * 1024,
            ),
            "max_file_bytes": (
                self.max_file_bytes,
                1024,
                512 * 1024 * 1024,
            ),
            "max_open_files": (self.max_open_files, 32, 1024),
        }
        for name, (value, minimum, maximum) in bounded.items():
            if isinstance(value, bool) or not minimum <= value <= maximum:
                raise ValueError(f"{name} is outside its supported range")
        if self.memory_limit_bytes is not None and (
            isinstance(self.memory_limit_bytes, bool)
            or not 256 * 1024 * 1024
            <= self.memory_limit_bytes
            <= 16 * 1024 * 1024 * 1024
        ):
            raise ValueError("memory_limit_bytes is outside its supported range")


@dataclass(frozen=True, slots=True)
class CadJobFailure:
    code: str
    safe_message: str
    retryable: bool
    details: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if _ERROR_CODE.fullmatch(self.code) is None:
            raise ValueError("CAD job failure code is invalid")
        if not 1 <= len(self.safe_message) <= 240 or any(
            character in self.safe_message for character in ("\x00", "\n", "\r")
        ):
            raise ValueError("CAD job failure message is invalid")
        if not isinstance(self.retryable, bool):
            raise ValueError("CAD job retryable flag is invalid")


@dataclass(frozen=True, slots=True)
class CadJobOutcome:
    status: Literal["succeeded", "failed"]
    duration_ms: float
    result: CadCompileResult | None = None
    failure: CadJobFailure | None = None
    transport_sha256: str | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.duration_ms) or self.duration_ms < 0:
            raise ValueError("CAD job duration is invalid")
        if (
            self.transport_sha256 is not None
            and _SHA256.fullmatch(self.transport_sha256) is None
        ):
            raise ValueError("CAD job transport digest is invalid")
        if self.status == "succeeded":
            if self.result is None or self.failure is not None:
                raise ValueError("successful CAD job requires exactly one result")
        elif self.result is not None or self.failure is None:
            raise ValueError("failed CAD job requires exactly one failure")


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _transport_envelope(payload: Mapping[str, object]) -> bytes:
    digest = hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()
    return _canonical_json_bytes({"payload": dict(payload), "transport_sha256": digest})


def _decode_envelope(content: bytes) -> tuple[dict[str, Any], str]:
    try:
        envelope = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("transport is not JSON") from error
    if not isinstance(envelope, dict) or set(envelope) != {
        "payload",
        "transport_sha256",
    }:
        raise ValueError("transport envelope fields are invalid")
    payload = envelope["payload"]
    digest = envelope["transport_sha256"]
    if not isinstance(payload, dict) or not isinstance(digest, str):
        raise ValueError("transport envelope values are invalid")
    if _SHA256.fullmatch(digest) is None:
        raise ValueError("transport digest is invalid")
    if hashlib.sha256(_canonical_json_bytes(payload)).hexdigest() != digest:
        raise ValueError("transport digest does not match")
    if _canonical_json_bytes(envelope) != content:
        raise ValueError("transport is not canonical JSON")
    return cast(dict[str, Any], payload), digest


def encode_cad_job_request(spec: CharacterRobotSpec, profile: HardwareProfile) -> bytes:
    profile_payload = profile.to_dict()
    payload = {
        "transport_version": CAD_JOB_TRANSPORT_VERSION,
        "spec": spec.model_dump(mode="json"),
        "profile": profile_payload,
        "profile_sha256": hashlib.sha256(
            _canonical_json_bytes(profile_payload)
        ).hexdigest(),
    }
    return _transport_envelope(payload)


def decode_cad_job_request(
    content: bytes,
) -> tuple[CharacterRobotSpec, HardwareProfile]:
    payload, _digest = _decode_envelope(content)
    if set(payload) != {
        "transport_version",
        "spec",
        "profile",
        "profile_sha256",
    }:
        raise ValueError("CAD job request fields are invalid")
    if payload["transport_version"] != CAD_JOB_TRANSPORT_VERSION:
        raise ValueError("CAD job request version is unsupported")
    profile_digest = payload["profile_sha256"]
    if (
        not isinstance(profile_digest, str)
        or _SHA256.fullmatch(profile_digest) is None
        or hashlib.sha256(_canonical_json_bytes(payload["profile"])).hexdigest()
        != profile_digest
    ):
        raise ValueError("CAD job profile digest does not match")
    try:
        spec = CharacterRobotSpec.model_validate(payload["spec"])
        profile = hardware_profile_from_dict(payload["profile"])
    except (ValidationError, ValueError) as error:
        raise ValueError("CAD job Spec or profile is invalid") from error
    if profile.profile_id != spec.hardware_profile_id:
        raise ValueError("CAD job profile does not match its Spec")
    return spec, profile


def _bounds_payload(bounds: Bounds3D) -> dict[str, object]:
    return {
        "minimum_mm": list(bounds.minimum_mm),
        "maximum_mm": list(bounds.maximum_mm),
    }


def _result_payload(result: CadCompileResult) -> dict[str, object]:
    return {
        "compiler_version": result.compiler_version,
        "build123d_version": result.build123d_version,
        "profile_id": result.profile_id,
        "geometry_sha256": result.geometry_sha256,
        "assembly_bounds": _bounds_payload(result.assembly_bounds),
        "parts": [
            {
                "name": part.name,
                "role": part.role,
                "bounds": _bounds_payload(part.bounds),
                "volume_mm3": part.volume_mm3,
                "printable": part.printable,
            }
            for part in result.parts
        ],
        "artifacts": [
            {
                "kind": artifact.kind,
                "file_name": artifact.file_name,
                "media_type": artifact.media_type,
                "content_base64": base64.b64encode(artifact.content).decode("ascii"),
                "sha256": artifact.sha256,
                "experimental": artifact.experimental,
            }
            for artifact in result.artifacts
        ],
        "issues": [
            {
                "code": issue.code,
                "severity": issue.severity,
                "path": issue.path,
                "message": issue.message,
                "suggestion": issue.suggestion,
            }
            for issue in result.issues
        ],
    }


def _json_safe_details(details: Mapping[str, object]) -> dict[str, object]:
    try:
        encoded = _canonical_json_bytes(dict(details))
        value = json.loads(encoded)
    except (TypeError, ValueError):
        return {}
    if len(encoded) > 16 * 1024:
        return {}
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def encode_cad_job_success(result: CadCompileResult) -> bytes:
    return _transport_envelope(
        {
            "transport_version": CAD_JOB_TRANSPORT_VERSION,
            "status": "succeeded",
            "result": _result_payload(result),
        }
    )


def encode_cad_job_failure(failure: CadJobFailure) -> bytes:
    return _transport_envelope(
        {
            "transport_version": CAD_JOB_TRANSPORT_VERSION,
            "status": "failed",
            "failure": {
                "code": failure.code,
                "safe_message": failure.safe_message,
                "retryable": failure.retryable,
                "details": _json_safe_details(failure.details),
            },
        }
    )


def _exact_mapping(value: object, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{label} fields are invalid")
    return cast(dict[str, Any], value)


def _text(value: object, label: str, *, maximum: int = 240) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or any(character in value for character in ("\x00", "\n", "\r"))
    ):
        raise ValueError(f"{label} is invalid")
    return value


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} is invalid")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} is invalid")
    return result


def _bounds(value: object) -> Bounds3D:
    payload = _exact_mapping(value, {"minimum_mm", "maximum_mm"}, "bounds")
    values: list[tuple[float, float, float]] = []
    for field_name in ("minimum_mm", "maximum_mm"):
        raw = payload[field_name]
        if not isinstance(raw, list) or len(raw) != 3:
            raise ValueError("bounds vector is invalid")
        vector = tuple(_number(item, "bounds coordinate") for item in raw)
        values.append(cast(tuple[float, float, float], vector))
    if any(values[1][axis] <= values[0][axis] for axis in range(3)):
        raise ValueError("bounds are empty or inverted")
    return Bounds3D(minimum_mm=values[0], maximum_mm=values[1])


def _decode_result(value: object) -> CadCompileResult:
    payload = _exact_mapping(
        value,
        {
            "compiler_version",
            "build123d_version",
            "profile_id",
            "geometry_sha256",
            "assembly_bounds",
            "parts",
            "artifacts",
            "issues",
        },
        "compile result",
    )
    geometry_sha256 = _text(payload["geometry_sha256"], "geometry digest", maximum=64)
    if _SHA256.fullmatch(geometry_sha256) is None:
        raise ValueError("geometry digest is invalid")

    raw_parts = payload["parts"]
    if not isinstance(raw_parts, list) or not 1 <= len(raw_parts) <= 256:
        raise ValueError("compile parts are invalid")
    parts: list[CadPartMetadata] = []
    for raw_part in raw_parts:
        part = _exact_mapping(
            raw_part,
            {"name", "role", "bounds", "volume_mm3", "printable"},
            "part",
        )
        if not isinstance(part["printable"], bool):
            raise ValueError("part printable flag is invalid")
        volume = _number(part["volume_mm3"], "part volume")
        if volume < 0:
            raise ValueError("part volume is invalid")
        parts.append(
            CadPartMetadata(
                name=_text(part["name"], "part name", maximum=120),
                role=_text(part["role"], "part role", maximum=120),
                bounds=_bounds(part["bounds"]),
                volume_mm3=volume,
                printable=part["printable"],
            )
        )
    if len({part.name for part in parts}) != len(parts):
        raise ValueError("compile part names are not unique")

    raw_artifacts = payload["artifacts"]
    if not isinstance(raw_artifacts, list) or not 1 <= len(raw_artifacts) <= 32:
        raise ValueError("compile artifacts are invalid")
    artifacts: list[CompiledArtifact] = []
    for raw_artifact in raw_artifacts:
        artifact = _exact_mapping(
            raw_artifact,
            {
                "kind",
                "file_name",
                "media_type",
                "content_base64",
                "sha256",
                "experimental",
            },
            "artifact",
        )
        if not isinstance(artifact["experimental"], bool):
            raise ValueError("artifact experimental flag is invalid")
        try:
            content = base64.b64decode(artifact["content_base64"], validate=True)
        except (TypeError, ValueError, binascii.Error) as error:
            raise ValueError("artifact content is invalid") from error
        if not content or len(content) > 64 * 1024 * 1024:
            raise ValueError("artifact content size is invalid")
        sha256 = _text(artifact["sha256"], "artifact digest", maximum=64)
        if (
            _SHA256.fullmatch(sha256) is None
            or hashlib.sha256(content).hexdigest() != sha256
        ):
            raise ValueError("artifact digest does not match")
        kind = _text(artifact["kind"], "artifact kind", maximum=32)
        if kind not in {"glb", "step", "stl", "3mf"}:
            raise ValueError("artifact kind is invalid")
        file_name = _text(artifact["file_name"], "artifact file", maximum=120)
        if _ARTIFACT_FILE_NAME.fullmatch(file_name) is None:
            raise ValueError("artifact file name is unsafe")
        artifacts.append(
            CompiledArtifact(
                kind=kind,
                file_name=file_name,
                media_type=_text(artifact["media_type"], "artifact media", maximum=120),
                content=content,
                sha256=sha256,
                experimental=artifact["experimental"],
            )
        )
    if len({artifact.kind for artifact in artifacts}) != len(artifacts):
        raise ValueError("compile artifact kinds are not unique")

    raw_issues = payload["issues"]
    if not isinstance(raw_issues, list) or len(raw_issues) > 128:
        raise ValueError("compile issues are invalid")
    issues: list[CadIssue] = []
    for raw_issue in raw_issues:
        issue = _exact_mapping(
            raw_issue,
            {"code", "severity", "path", "message", "suggestion"},
            "issue",
        )
        suggestion = issue["suggestion"]
        if suggestion is not None:
            suggestion = _text(suggestion, "issue suggestion", maximum=2000)
        severity = _text(issue["severity"], "issue severity", maximum=16)
        if severity not in {"info", "warning", "error"}:
            raise ValueError("issue severity is invalid")
        issues.append(
            CadIssue(
                code=_text(issue["code"], "issue code", maximum=64),
                severity=severity,
                path=_text(issue["path"], "issue path", maximum=240),
                message=_text(issue["message"], "issue message", maximum=2000),
                suggestion=suggestion,
            )
        )

    return CadCompileResult(
        compiler_version=_text(
            payload["compiler_version"], "compiler version", maximum=120
        ),
        build123d_version=_text(
            payload["build123d_version"], "CAD engine version", maximum=120
        ),
        profile_id=_text(payload["profile_id"], "profile ID", maximum=96),
        geometry_sha256=geometry_sha256,
        assembly_bounds=_bounds(payload["assembly_bounds"]),
        parts=tuple(parts),
        artifacts=tuple(artifacts),
        issues=tuple(issues),
    )


def decode_cad_job_response(
    content: bytes,
) -> tuple[CadCompileResult | CadJobFailure, str]:
    payload, digest = _decode_envelope(content)
    if payload.get("transport_version") != CAD_JOB_TRANSPORT_VERSION:
        raise ValueError("CAD job response version is unsupported")
    status = payload.get("status")
    if status == "succeeded":
        if set(payload) != {"transport_version", "status", "result"}:
            raise ValueError("successful CAD job response fields are invalid")
        return _decode_result(payload["result"]), digest
    if status == "failed":
        if set(payload) != {"transport_version", "status", "failure"}:
            raise ValueError("failed CAD job response fields are invalid")
        failure = _exact_mapping(
            payload["failure"],
            {"code", "safe_message", "retryable", "details"},
            "failure",
        )
        code = _text(failure["code"], "failure code", maximum=64)
        if _ERROR_CODE.fullmatch(code) is None:
            raise ValueError("failure code is invalid")
        if not isinstance(failure["retryable"], bool):
            raise ValueError("failure retryable flag is invalid")
        details = failure["details"]
        if not isinstance(details, dict):
            raise ValueError("failure details are invalid")
        return (
            CadJobFailure(
                code=code,
                safe_message=_text(
                    failure["safe_message"], "failure message", maximum=240
                ),
                retryable=failure["retryable"],
                details=cast(dict[str, object], details),
            ),
            digest,
        )
    raise ValueError("CAD job response status is invalid")


class IsolatedCadJobRunner:
    """Run the pinned CAD compiler in a bounded child Python process."""

    def __init__(
        self,
        *,
        limits: CadJobLimits | None = None,
    ) -> None:
        self.limits = limits or CadJobLimits()

    def run(
        self, spec: CharacterRobotSpec, profile: HardwareProfile | None = None
    ) -> CadJobOutcome:
        started = time.perf_counter()
        try:
            validated = CharacterRobotSpec.model_validate(spec.model_dump(mode="json"))
        except (AttributeError, TypeError, ValidationError):
            return self._failed(
                started,
                "CAD_JOB_INPUT_INVALID",
                "The isolated CAD job requires a valid CharacterRobotSpec.",
                False,
            )
        try:
            selected_profile = profile or ProfileRegistry().get_profile(
                validated.hardware_profile_id
            )
            selected_profile = hardware_profile_from_dict(selected_profile.to_dict())
        except (AttributeError, TypeError, ValueError):
            return self._failed(
                started,
                "CAD_JOB_PROFILE_INVALID",
                "The isolated CAD job requires a canonical hardware profile.",
                False,
            )
        if selected_profile.profile_id != validated.hardware_profile_id:
            return self._failed(
                started,
                "CAD_PROFILE_MISMATCH",
                "The selected hardware profile does not match the character design.",
                False,
            )
        request = encode_cad_job_request(validated, selected_profile)
        if len(request) > self.limits.max_input_bytes:
            return self._failed(
                started,
                "CAD_JOB_INPUT_TOO_LARGE",
                "The canonical CAD job input exceeds the configured limit.",
                False,
                {"maximum_bytes": self.limits.max_input_bytes},
            )

        with tempfile.TemporaryDirectory(prefix="character-cad-job-") as directory:
            root = Path(directory)
            request_path = root / "request.json"
            response_path = root / "response.json"
            stdout_path = root / "stdout.log"
            stderr_path = root / "stderr.log"
            request_path.write_bytes(request)
            command = self._command(request_path, response_path)
            environment = dict(os.environ)
            environment.update(
                {
                    "PYTHONHASHSEED": "0",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "OMP_NUM_THREADS": "1",
                }
            )
            try:
                with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                    process = subprocess.Popen(
                        command,
                        stdin=subprocess.DEVNULL,
                        stdout=stdout,
                        stderr=stderr,
                        env=environment,
                        close_fds=True,
                        start_new_session=True,
                    )
                    try:
                        return_code = process.wait(
                            timeout=self.limits.wall_timeout_seconds
                        )
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                        return self._failed(
                            started,
                            "CAD_JOB_TIMEOUT",
                            "The isolated CAD job exceeded its wall-clock limit.",
                            True,
                            {"timeout_seconds": self.limits.wall_timeout_seconds},
                        )
            except (OSError, subprocess.SubprocessError):
                return self._failed(
                    started,
                    "CAD_JOB_START_FAILED",
                    "The isolated CAD worker could not be started.",
                    True,
                )

            if return_code != 0:
                code = (
                    "CAD_JOB_RESOURCE_LIMIT"
                    if return_code < 0
                    else "CAD_JOB_WORKER_FAILED"
                )
                return self._failed(
                    started,
                    code,
                    "The isolated CAD worker stopped before returning a result.",
                    True,
                    {"worker_exit_code": return_code},
                )
            try:
                size = response_path.stat().st_size
            except OSError:
                return self._failed(
                    started,
                    "CAD_JOB_RESULT_MISSING",
                    "The isolated CAD worker returned no result.",
                    True,
                )
            if size > self.limits.max_result_bytes:
                return self._failed(
                    started,
                    "CAD_JOB_RESULT_TOO_LARGE",
                    "The isolated CAD result exceeds the configured limit.",
                    False,
                    {"maximum_bytes": self.limits.max_result_bytes},
                )
            try:
                decoded, transport_sha256 = decode_cad_job_response(
                    response_path.read_bytes()
                )
            except (OSError, ValueError):
                return self._failed(
                    started,
                    "CAD_JOB_TRANSPORT_INVALID",
                    "The isolated CAD result failed transport integrity checks.",
                    True,
                )
            duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
            if isinstance(decoded, CadJobFailure):
                return CadJobOutcome(
                    status="failed",
                    duration_ms=duration_ms,
                    failure=decoded,
                    transport_sha256=transport_sha256,
                )
            return CadJobOutcome(
                status="succeeded",
                duration_ms=duration_ms,
                result=decoded,
                transport_sha256=transport_sha256,
            )

    def _command(self, request_path: Path, response_path: Path) -> list[str]:
        command = [
            str(Path(sys.executable)),
            "-m",
            _DEFAULT_WORKER_MODULE,
            "--request",
            str(request_path),
            "--response",
            str(response_path),
            "--cpu-seconds",
            str(self.limits.cpu_seconds),
            "--max-file-bytes",
            str(self.limits.max_file_bytes),
            "--max-open-files",
            str(self.limits.max_open_files),
        ]
        if self.limits.memory_limit_bytes is not None:
            command.extend(["--memory-bytes", str(self.limits.memory_limit_bytes)])
        return command

    def _failed(
        self,
        started: float,
        code: str,
        safe_message: str,
        retryable: bool,
        details: Mapping[str, object] | None = None,
    ) -> CadJobOutcome:
        return CadJobOutcome(
            status="failed",
            duration_ms=round((time.perf_counter() - started) * 1000.0, 3),
            failure=CadJobFailure(
                code=code,
                safe_message=safe_message,
                retryable=retryable,
                details=dict(details or {}),
            ),
        )


class IsolatedCadCompiler:
    """Async service adapter for IsolatedCadJobRunner."""

    def __init__(self, runner: IsolatedCadJobRunner | None = None) -> None:
        self.runner = runner or IsolatedCadJobRunner()

    async def compile(
        self, spec: CharacterRobotSpec, profile: object | None = None
    ) -> CadCompileResult:
        profile_id = getattr(profile, "profile_id", spec.hardware_profile_id)
        if profile_id != spec.hardware_profile_id:
            raise CadCompileError(
                "CAD_PROFILE_MISMATCH",
                "The selected hardware profile does not match the character design.",
            )
        if not isinstance(profile, HardwareProfile):
            raise CadCompileError(
                "CAD_JOB_PROFILE_INVALID",
                "The isolated CAD compiler requires a canonical hardware profile.",
            )
        outcome = await asyncio.to_thread(self.runner.run, spec, profile)
        if outcome.failure is not None:
            raise CadCompileError(
                outcome.failure.code,
                outcome.failure.safe_message,
                retryable=outcome.failure.retryable,
                details=outcome.failure.details,
            )
        assert outcome.result is not None
        return outcome.result


__all__ = [
    "CAD_JOB_TRANSPORT_VERSION",
    "CadJobFailure",
    "CadJobLimits",
    "CadJobOutcome",
    "IsolatedCadCompiler",
    "IsolatedCadJobRunner",
    "decode_cad_job_request",
    "decode_cad_job_response",
    "encode_cad_job_failure",
    "encode_cad_job_request",
    "encode_cad_job_success",
]
