from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .storage import canonical_json_bytes


CASE_ID: Final = "case_compound-arm-01"
ROOT_REVISION_ID: Final = "r000"
FIXTURE_VERSION: Final = "compound-arm-01/v1"
TIMESTEP_S: Final = 0.002
JOINT_NAMES: Final = ("joint_a", "joint_b", "joint_c")
ACTUATOR_NAMES: Final = ("motor_a", "motor_b", "motor_c")
BODY_NAMES: Final = ("world", "link_a", "link_b", "link_c", "end_effector")
LINK_LENGTHS_M: Final = (0.40, 0.35, 0.25)
JOINT_RANGE_RAD: Final = (-1.2, 1.2)
CONTROL_RANGE: Final = (-1.2, 1.2)
PUBLIC_TARGET_QPOS: Final = (0.35, -0.45, 0.25)
PUBLIC_INITIAL_QPOS: Final = (0.35, -0.45, 0.21)

_EMBEDDED_MJCF = b"""<mujoco model="compound-arm-01">
  <compiler angle="radian"/>
  <option timestep="0.002" integrator="RK4" gravity="0 0 0"/>
  <worldbody>
    <body name="link_a">
      <joint name="joint_a" type="hinge" axis="0 0 1" range="-1.2 1.2" limited="true" damping="0.4" armature="0.02" frictionloss="0"/>
      <geom name="link_a_geom" type="capsule" fromto="0 0 0 0.4 0 0" size="0.03" density="500"/>
      <body name="link_b" pos="0.4 0 0">
        <joint name="joint_b" type="hinge" axis="0 0 1" range="-1.2 1.2" limited="true" damping="0.4" armature="0.02" frictionloss="0"/>
        <geom name="link_b_geom" type="capsule" fromto="0 0 0 0.35 0 0" size="0.03" density="500"/>
        <body name="link_c" pos="0.35 0 0">
          <joint name="joint_c" type="hinge" axis="0 1 0" range="-1.2 1.2" limited="true" damping="0.01" armature="0.02" frictionloss="0"/>
          <geom name="link_c_geom" type="capsule" fromto="0 0 0 0.25 0 0" size="0.03" density="500"/>
          <body name="end_effector" pos="0.25 0 0">
            <geom name="end_effector_geom" type="sphere" size="0.02" mass="0.01"/>
          </body>
        </body>
      </body>
    </body>
  </worldbody>
  <actuator>
    <position name="motor_a" joint="joint_a" kp="20" ctrllimited="true" ctrlrange="-1.2 1.2"/>
    <position name="motor_b" joint="joint_b" kp="20" ctrllimited="true" ctrlrange="-1.2 1.2"/>
    <position name="motor_c" joint="joint_c" kp="20" ctrllimited="true" ctrlrange="-1.2 1.2"/>
  </actuator>
</mujoco>
"""


@dataclass(frozen=True, slots=True)
class PublicScenario:
    scenario_id: str
    initial_qpos: tuple[float, ...]
    target_qpos: tuple[float, ...]
    target_body_position: tuple[float, float, float]
    duration_steps: int
    hold_steps: int


@dataclass(frozen=True, slots=True)
class CompoundArmFixture:
    case_id: str
    root_revision_id: str
    version: str
    asset_xml: bytes
    joint_names: tuple[str, ...]
    actuator_names: tuple[str, ...]
    body_names: tuple[str, ...]
    timestep_s: float
    joint_range: tuple[float, float]
    control_range: tuple[float, float]
    public_scenario: PublicScenario

    @property
    def source_asset_sha256(self) -> str:
        return hashlib.sha256(self.asset_xml).hexdigest()

    @property
    def controller_sha256(self) -> str:
        return _sha256_json(
            {
                "actuator_names": self.actuator_names,
                "joint_names": self.joint_names,
                "kind": "position",
                "kp": 20.0,
                "control_range": self.control_range,
            }
        )

    @property
    def public_contract_sha256(self) -> str:
        scenario = self.public_scenario
        return _sha256_json(
            {
                "version": self.version,
                "scenario_id": scenario.scenario_id,
                "initial_qpos": scenario.initial_qpos,
                "target_body_position": scenario.target_body_position,
                "duration_steps": scenario.duration_steps,
                "hold_steps": scenario.hold_steps,
                "limits": {
                    "hold_error_p95_m": 0.03,
                    "joint_speed_rms_rad_s": 0.05,
                    "settling_time_s": 2.0,
                    "joint_limit_violation_count": 0,
                    "non_finite_count": 0,
                },
            }
        )

    @property
    def runner_sha256(self) -> str:
        return _sha256_json(
            {
                "fixture": self.version,
                "timestep_s": self.timestep_s,
                "integrator": "RK4",
                "mujoco": "3.5.0",
                "upstream_commit": "ce9bed80ec3698d7b778230abc21f2228a3ce94b",
            }
        )


def _sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def clean_end_effector_position(
    qpos: tuple[float, float, float],
) -> tuple[float, float, float]:
    joint_a, joint_b, joint_c = qpos
    first, second, third = LINK_LENGTHS_M
    radial = first + second * math.cos(joint_b) + third * math.cos(joint_b + joint_c)
    return (
        radial * math.cos(joint_a),
        radial * math.sin(joint_a),
        -second * math.sin(joint_b) - third * math.sin(joint_b + joint_c),
    )


def default_fixture_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "compound-arm-01"
        / "asset.mjcf"
    )


def load_compound_arm_fixture(path: Path | None = None) -> CompoundArmFixture:
    source = _EMBEDDED_MJCF
    candidate = path or default_fixture_path()
    if candidate.is_file():
        disk_source = candidate.read_bytes()
        if disk_source != _EMBEDDED_MJCF:
            raise ValueError(
                "compound-arm fixture differs from the embedded immutable asset"
            )
        source = disk_source
    public = PublicScenario(
        scenario_id="public_center",
        initial_qpos=PUBLIC_INITIAL_QPOS,
        target_qpos=PUBLIC_TARGET_QPOS,
        target_body_position=clean_end_effector_position(PUBLIC_TARGET_QPOS),
        duration_steps=2_000,
        hold_steps=1_000,
    )
    return CompoundArmFixture(
        case_id=CASE_ID,
        root_revision_id=ROOT_REVISION_ID,
        version=FIXTURE_VERSION,
        asset_xml=source,
        joint_names=JOINT_NAMES,
        actuator_names=ACTUATOR_NAMES,
        body_names=BODY_NAMES,
        timestep_s=TIMESTEP_S,
        joint_range=JOINT_RANGE_RAD,
        control_range=CONTROL_RANGE,
        public_scenario=public,
    )


__all__ = [
    "ACTUATOR_NAMES",
    "BODY_NAMES",
    "CASE_ID",
    "CompoundArmFixture",
    "JOINT_NAMES",
    "PublicScenario",
    "clean_end_effector_position",
    "load_compound_arm_fixture",
]
