from __future__ import annotations

import hashlib
import json

import pytest

from character_robot.simulation import SimulationError, compile_mjcf, run_motion_checks


def test_internal_mjcf_is_deterministic_and_does_not_accept_raw_xml() -> None:
    first = compile_mjcf((122.0, 108.0, 98.0), assembly_mass_g=None)
    second = compile_mjcf((122.0, 108.0, 98.0), assembly_mass_g=None)

    assert first == second
    assert b'<mujoco model="character_robot">' in first
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()


def test_motion_checks_cover_turn_stop_step_and_tip_without_claiming_measurement() -> (
    None
):
    result = run_motion_checks((122.0, 108.0, 98.0), assembly_mass_g=None)
    report = json.loads(result.canonical_report())

    assert result.assumption_level == "planning_only"
    assert [check.code for check in result.checks] == [
        "turn_response",
        "stop_response",
        "step_contact",
        "static_tip_margin",
    ]
    assert result.passed is True
    assert report["assumption_level"] == "planning_only"
    assert report["model_sha256"] == hashlib.sha256(result.model_xml).hexdigest()
    assert "duration_ms" not in report


def test_measured_mass_does_not_promote_unmeasured_dynamics() -> None:
    result = run_motion_checks((122.0, 108.0, 98.0), assembly_mass_g=820.0)
    assert result.assumption_level == "planning_only"


@pytest.mark.parametrize(
    "dimensions,mass",
    [
        ((0.0, 10.0, 10.0), None),
        ((10.0, float("nan"), 10.0), None),
        ((10.0, 10.0, 10.0), -1.0),
    ],
)
def test_invalid_physical_inputs_are_rejected_at_the_boundary(dimensions, mass) -> None:
    with pytest.raises(SimulationError, match="positive"):
        compile_mjcf(dimensions, assembly_mass_g=mass)
