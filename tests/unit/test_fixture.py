from __future__ import annotations

import hashlib
import asyncio
import xml.etree.ElementTree as ET
from dataclasses import replace

import pytest

from asset_autopsy.fixture import (
    CASE_ID,
    clean_end_effector_position,
    load_compound_arm_fixture,
)
from asset_autopsy.schemas import RunTaskInput
from asset_autopsy.service import AssetAutopsyService


def test_compound_arm_fixture_has_the_frozen_topology_and_two_authored_defects() -> (
    None
):
    fixture = load_compound_arm_fixture()
    root = ET.fromstring(fixture.asset_xml)
    joints = {joint.attrib["name"]: joint for joint in root.findall(".//joint")}
    actuators = {
        item.attrib["name"]: item for item in root.findall("./actuator/position")
    }

    assert fixture.case_id == CASE_ID == "case_compound-arm-01"
    assert fixture.joint_names == ("joint_a", "joint_b", "joint_c")
    assert fixture.actuator_names == ("motor_a", "motor_b", "motor_c")
    assert root.find("option").attrib == {
        "timestep": "0.002",
        "integrator": "RK4",
        "gravity": "0 0 0",
    }
    assert joints["joint_a"].attrib["axis"] == "0 0 1"
    assert joints["joint_b"].attrib["axis"] == "0 0 1"
    assert joints["joint_c"].attrib["axis"] == "0 1 0"
    assert joints["joint_c"].attrib["damping"] == "0.01"
    assert all(item.attrib["kp"] == "20" for item in actuators.values())
    assert all(item.attrib["ctrlrange"] == "-1.2 1.2" for item in actuators.values())
    assert fixture.source_asset_sha256 == hashlib.sha256(fixture.asset_xml).hexdigest()


def test_clean_target_kinematics_are_deterministic() -> None:
    fixture = load_compound_arm_fixture()

    assert clean_end_effector_position((0.0, 0.0, 0.0)) == (1.0, 0.0, -0.0)
    assert clean_end_effector_position(fixture.public_scenario.target_qpos) == (
        fixture.public_scenario.target_body_position
    )


def test_public_contract_hash_changes_when_target_qpos_changes() -> None:
    fixture = load_compound_arm_fixture()
    changed_scenario = replace(
        fixture.public_scenario,
        target_qpos=(0.36, -0.45, 0.25),
    )
    changed_fixture = replace(fixture, public_scenario=changed_scenario)

    assert changed_fixture.public_contract_sha256 != fixture.public_contract_sha256


def test_public_contract_hash_is_stable_for_identical_contracts() -> None:
    first = load_compound_arm_fixture()
    second = load_compound_arm_fixture()

    assert first.public_contract_sha256 == second.public_contract_sha256


def test_loading_rejects_a_disk_fixture_that_differs_from_the_embedded_asset(
    tmp_path,
) -> None:
    changed = tmp_path / "asset.mjcf"
    changed.write_text('<mujoco model="different"/>')

    try:
        load_compound_arm_fixture(changed)
    except ValueError as error:
        assert "differs" in str(error)
    else:
        raise AssertionError("changed fixture was accepted")


@pytest.mark.phase0_upstream
def test_real_pinned_mcp_calibrates_the_root_fixture_as_a_public_failure(
    tmp_path,
) -> None:
    service = AssetAutopsyService(tmp_path)

    result = asyncio.run(
        service.run_task(
            RunTaskInput(
                case_id=CASE_ID,
                revision_id="r000",
                scenario_id="public_center",
                capture="metrics",
            )
        )
    )
    values = {item.metric: item.value for item in result.observations}

    assert result.result == "fail"
    assert values["hold_error_p95_m"] == pytest.approx(0.373396, abs=1e-4)
    assert values["joint_speed_rms_rad_s"] == pytest.approx(0.245915, abs=1e-4)
    assert values["settling_time_s"] is None
    assert values["joint_limit_violation_count"] == 0.0
    assert values["non_finite_count"] == 0.0
