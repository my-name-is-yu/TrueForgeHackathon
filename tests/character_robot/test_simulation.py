from __future__ import annotations

import hashlib
import json
import math
import xml.etree.ElementTree as ET

import pytest

from character_robot.simulation import SimulationError, compile_mjcf, run_motion_checks


_WHEEL_GEOMETRY = {
    "wheel_track_mm": 110.0,
    "wheel_width_mm": 10.0,
    "wheel_radius_mm": 24.0,
}


def test_internal_mjcf_is_deterministic_and_does_not_accept_raw_xml() -> None:
    first = compile_mjcf((122.0, 108.0, 98.0), **_WHEEL_GEOMETRY, assembly_mass_g=None)
    second = compile_mjcf((122.0, 108.0, 98.0), **_WHEEL_GEOMETRY, assembly_mass_g=None)

    assert first == second
    assert b'<mujoco model="character_robot">' in first
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()


def test_motion_checks_cover_turn_stop_step_and_tip_without_claiming_measurement() -> (
    None
):
    result = run_motion_checks(
        (122.0, 108.0, 98.0), **_WHEEL_GEOMETRY, assembly_mass_g=None
    )
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
    result = run_motion_checks(
        (122.0, 108.0, 98.0), **_WHEEL_GEOMETRY, assembly_mass_g=820.0
    )
    assert result.assumption_level == "planning_only"


def test_wheel_positions_and_tip_margin_use_compiled_wheel_track() -> None:
    compact = compile_mjcf(
        (122.0, 108.0, 98.0), **_WHEEL_GEOMETRY, assembly_mass_g=None
    )
    ornament_wide = compile_mjcf(
        (260.0, 108.0, 98.0), **_WHEEL_GEOMETRY, assembly_mass_g=None
    )

    def wheel_positions(model_xml: bytes) -> tuple[str, str]:
        root = ET.fromstring(model_xml)
        return tuple(
            root.find(f".//body[@name='{name}']").attrib["pos"]  # type: ignore[union-attr]
            for name in ("left_wheel", "right_wheel")
        )  # type: ignore[return-value]

    assert (
        wheel_positions(compact)
        == wheel_positions(ornament_wide)
        == (
            "0 0.055000000 0",
            "0 -0.055000000 0",
        )
    )

    result = run_motion_checks(
        (260.0, 108.0, 98.0), **_WHEEL_GEOMETRY, assembly_mass_g=None
    )
    tip_margin = next(
        check for check in result.checks if check.code == "static_tip_margin"
    )
    assert tip_margin.measured_value == pytest.approx(
        math.degrees(math.atan2(0.055, 0.098 * 0.55)), abs=1e-6
    )


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
        compile_mjcf(dimensions, **_WHEEL_GEOMETRY, assembly_mass_g=mass)


@pytest.mark.parametrize(
    "field,value",
    [
        ("wheel_track_mm", 0.0),
        ("wheel_width_mm", float("nan")),
        ("wheel_radius_mm", -1.0),
    ],
)
def test_invalid_compiled_wheel_geometry_is_rejected(field, value) -> None:
    geometry = {**_WHEEL_GEOMETRY, field: value}
    with pytest.raises(SimulationError, match="positive"):
        compile_mjcf((122.0, 108.0, 98.0), **geometry, assembly_mass_g=None)
