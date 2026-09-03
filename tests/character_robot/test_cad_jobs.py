from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace

import pytest

from character_robot.cad import CadCompileError
from character_robot.cad_jobs import (
    CadJobFailure,
    CadJobLimits,
    CadJobOutcome,
    IsolatedCadCompiler,
    IsolatedCadJobRunner,
    decode_cad_job_request,
    decode_cad_job_response,
    encode_cad_job_request,
    encode_cad_job_success,
)
from character_robot.profiles import ProfileRegistry
from character_robot.schemas import CharacterRobotSpec

from test_cad import _spec_payload


def _spec(*, maximum: float = 250.0) -> CharacterRobotSpec:
    return CharacterRobotSpec.model_validate(
        _spec_payload(maximum_dimensions_mm=(maximum, maximum, maximum))
    )


def test_isolated_worker_returns_digest_verified_compile_result() -> None:
    outcome = IsolatedCadJobRunner(
        limits=CadJobLimits(wall_timeout_seconds=45.0, cpu_seconds=45)
    ).run(_spec())

    assert outcome.status == "succeeded"
    assert outcome.failure is None
    assert outcome.result is not None
    assert outcome.transport_sha256 is not None
    assert outcome.result.profile_id == "m5-cores3-goplus2/v1"
    assert {artifact.kind for artifact in outcome.result.artifacts} == {
        "glb",
        "step",
        "stl",
        "3mf",
    }
    assert all(
        hashlib.sha256(artifact.content).hexdigest() == artifact.sha256
        for artifact in outcome.result.artifacts
    )

    first_transport = encode_cad_job_success(outcome.result)
    second_transport = encode_cad_job_success(outcome.result)
    decoded, digest = decode_cad_job_response(first_transport)
    assert first_transport == second_transport
    assert digest == outcome.transport_sha256
    assert decoded == outcome.result


def test_isolated_worker_preserves_safe_compiler_failure() -> None:
    outcome = IsolatedCadJobRunner(
        limits=CadJobLimits(wall_timeout_seconds=45.0, cpu_seconds=45)
    ).run(_spec(maximum=10.0))

    assert outcome.status == "failed"
    assert outcome.result is None
    assert outcome.failure is not None
    assert outcome.failure.code == "CAD_DIMENSION_LIMIT_EXCEEDED"
    assert outcome.failure.retryable is False
    assert outcome.failure.details["axis"] in {"x", "y", "z"}
    assert outcome.transport_sha256 is not None


def test_parent_kills_worker_after_wall_timeout() -> None:
    outcome = IsolatedCadJobRunner(
        limits=CadJobLimits(wall_timeout_seconds=0.001, cpu_seconds=30)
    ).run(_spec())

    assert outcome.status == "failed"
    assert outcome.failure is not None
    assert outcome.failure.code == "CAD_JOB_TIMEOUT"
    assert outcome.failure.retryable is True
    assert outcome.transport_sha256 is None


def test_request_and_response_transport_reject_tampering() -> None:
    spec = _spec()
    profile = ProfileRegistry().get_profile(spec.hardware_profile_id)
    request = bytearray(encode_cad_job_request(spec, profile))
    location = request.index(b"Timid Duck Guide")
    request[location] = ord("X")

    with pytest.raises(ValueError, match="digest"):
        decode_cad_job_request(bytes(request))


def test_isolated_worker_compiles_the_exact_transported_profile() -> None:
    spec = _spec()
    base = ProfileRegistry().get_profile(spec.hardware_profile_id)
    controller = base.components[0]
    widened = replace(
        controller,
        envelope=replace(controller.envelope, size_mm=(70.0, 15.5, 54.0)),
    )
    profile = replace(base, components=(widened, *base.components[1:]))
    encoded = encode_cad_job_request(spec, profile)
    decoded_spec, decoded_profile = decode_cad_job_request(encoded)
    assert decoded_spec == spec
    assert decoded_profile == profile

    outcome = IsolatedCadJobRunner(
        limits=CadJobLimits(wall_timeout_seconds=45.0, cpu_seconds=45)
    ).run(spec, profile)

    assert outcome.result is not None
    controller_part = next(
        part for part in outcome.result.parts if part.name == "hardware_m5stack-cores3"
    )
    assert controller_part.bounds.size_mm[0] == pytest.approx(70.0)


def test_response_transport_rejects_zip_slip_artifact_name() -> None:
    outcome = IsolatedCadJobRunner(
        limits=CadJobLimits(wall_timeout_seconds=45.0, cpu_seconds=45)
    ).run(_spec())
    assert outcome.result is not None
    malicious = replace(
        outcome.result,
        artifacts=(
            replace(outcome.result.artifacts[0], file_name="../../payload.glb"),
            *outcome.result.artifacts[1:],
        ),
    )

    with pytest.raises(ValueError, match="unsafe"):
        decode_cad_job_response(encode_cad_job_success(malicious))


class _FailedRunner:
    def run(self, _spec: CharacterRobotSpec, _profile: object) -> CadJobOutcome:
        return CadJobOutcome(
            status="failed",
            duration_ms=1.0,
            failure=CadJobFailure(
                code="CAD_JOB_RESOURCE_LIMIT",
                safe_message="The child reached a configured resource limit.",
                retryable=True,
            ),
        )


def test_async_compiler_adapter_maps_structured_failure_to_cad_error() -> None:
    compiler = IsolatedCadCompiler(_FailedRunner())  # type: ignore[arg-type]
    spec = _spec()
    profile = ProfileRegistry().get_profile(spec.hardware_profile_id)

    with pytest.raises(CadCompileError) as captured:
        asyncio.run(compiler.compile(spec, profile))
    assert captured.value.code == "CAD_JOB_RESOURCE_LIMIT"
    assert captured.value.retryable is True
