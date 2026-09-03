from __future__ import annotations

import hashlib
import importlib
import json
import math
import time
from dataclasses import asdict, dataclass
from importlib import metadata
from typing import Literal


MUJOCO_VERSION = "3.5.0"
SIMULATION_COMPILER_VERSION = "character-sim-v1"


class SimulationError(RuntimeError):
    def __init__(self, code: str, safe_message: str) -> None:
        self.code = code
        self.safe_message = safe_message
        self.retryable = code == "SIMULATION_DEPENDENCY_UNAVAILABLE"
        super().__init__(safe_message)


@dataclass(frozen=True, slots=True)
class SimulationCheck:
    code: str
    passed: bool
    measured_value: float
    limit_value: float
    unit: str
    message: str


@dataclass(frozen=True, slots=True)
class MotionSimulationResult:
    engine_version: str
    compiler_version: str
    assumption_level: Literal["planning_only", "measured_profile"]
    model_sha256: str
    model_xml: bytes
    checks: tuple[SimulationCheck, ...]
    duration_ms: float

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def canonical_report(self) -> bytes:
        payload = {
            "schema_version": "character-simulation/v1",
            "engine": "mujoco",
            "engine_version": self.engine_version,
            "compiler_version": self.compiler_version,
            "assumption_level": self.assumption_level,
            "model_sha256": self.model_sha256,
            "passed": self.passed,
            "checks": [asdict(check) for check in self.checks],
        }
        return json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()


def _load_mujoco():
    try:
        mujoco = importlib.import_module("mujoco")
        installed = metadata.version("mujoco")
    except (ImportError, metadata.PackageNotFoundError) as error:
        raise SimulationError(
            "SIMULATION_DEPENDENCY_UNAVAILABLE",
            f"mujoco=={MUJOCO_VERSION} is required for motion checks.",
        ) from error
    if installed != MUJOCO_VERSION:
        raise SimulationError(
            "SIMULATION_DEPENDENCY_VERSION_MISMATCH",
            f"Motion checks require mujoco=={MUJOCO_VERSION}.",
        )
    return mujoco


def _finite_positive_triplet(
    dimensions_mm: tuple[float, float, float],
) -> tuple[float, float, float]:
    if len(dimensions_mm) != 3:
        raise SimulationError(
            "SIMULATION_INPUT_INVALID", "Simulation dimensions must have three axes."
        )
    values = tuple(float(value) for value in dimensions_mm)
    if not all(math.isfinite(value) and value > 0 for value in values):
        raise SimulationError(
            "SIMULATION_INPUT_INVALID",
            "Simulation dimensions must be positive and finite.",
        )
    return values  # type: ignore[return-value]


def compile_mjcf(
    dimensions_mm: tuple[float, float, float],
    *,
    assembly_mass_g: float | None,
) -> bytes:
    """Compile a bounded internal MJCF model; raw user MJCF is never accepted."""

    width_mm, depth_mm, height_mm = _finite_positive_triplet(dimensions_mm)
    if assembly_mass_g is not None and (
        not math.isfinite(assembly_mass_g) or assembly_mass_g <= 0
    ):
        raise SimulationError(
            "SIMULATION_INPUT_INVALID", "Assembly mass must be positive and finite."
        )

    width = min(max(width_mm / 1000.0, 0.06), 0.30)
    depth = min(max(depth_mm / 1000.0, 0.06), 0.30)
    height = min(max(height_mm / 1000.0, 0.08), 0.40)
    wheel_radius = min(max(height * 0.18, 0.018), 0.04)
    wheel_width = min(max(width * 0.08, 0.008), 0.018)
    track = width + wheel_width
    base_height = min(max(height * 0.55, 0.05), 0.10)
    head_height = max(height - base_height, 0.025)
    body_z = wheel_radius + 0.002
    base_local_z = base_height / 2.0 - wheel_radius * 0.45
    head_local_z = base_local_z + base_height / 2.0 + head_height / 2.0
    caster_radius = min(wheel_radius * 0.38, 0.012)
    caster_x = -depth * 0.35
    caster_local_z = -wheel_radius + caster_radius
    mass = (assembly_mass_g / 1000.0) if assembly_mass_g is not None else 0.75
    chassis_mass = mass * 0.72
    head_mass = mass * 0.18
    wheel_mass = mass * 0.04
    caster_mass = mass * 0.02
    step_x = depth * 0.85

    def number(value: float) -> str:
        return format(value, ".9f")

    xml = f"""<mujoco model="character_robot">
  <compiler angle="radian"/>
  <option timestep="0.002" gravity="0 0 -9.81" integrator="RK4"/>
  <default>
    <geom condim="3" friction="1.0 0.005 0.0001" solref="0.01 1"/>
    <joint damping="0.015" armature="0.001"/>
  </default>
  <worldbody>
    <geom name="floor" type="plane" size="2 2 0.02" rgba="0.2 0.2 0.2 1"/>
    <geom name="step" type="box" pos="{number(step_x)} 0 0.004" size="0.012 {number(track)} 0.004" rgba="0.5 0.3 0.2 1"/>
    <body name="robot" pos="0 0 {number(body_z)}">
      <freejoint name="robot_free"/>
      <geom name="chassis" type="box" pos="0 0 {number(base_local_z)}" size="{number(depth * 0.42)} {number(width * 0.42)} {number(base_height / 2.0)}" mass="{number(chassis_mass)}" rgba="0.9 0.65 0.2 1"/>
      <geom name="head" type="ellipsoid" pos="0 0 {number(head_local_z)}" size="{number(depth * 0.33)} {number(width * 0.34)} {number(head_height / 2.0)}" mass="{number(head_mass)}" rgba="0.95 0.75 0.25 1"/>
      <body name="left_wheel" pos="0 {number(track / 2.0)} 0">
        <joint name="left_wheel_hinge" type="hinge" axis="0 1 0"/>
        <geom name="left_wheel_geom" type="cylinder" euler="1.570796327 0 0" size="{number(wheel_radius)} {number(wheel_width / 2.0)}" mass="{number(wheel_mass)}" friction="1.4 0.01 0.001" rgba="0.05 0.05 0.05 1"/>
      </body>
      <body name="right_wheel" pos="0 {number(-track / 2.0)} 0">
        <joint name="right_wheel_hinge" type="hinge" axis="0 1 0"/>
        <geom name="right_wheel_geom" type="cylinder" euler="1.570796327 0 0" size="{number(wheel_radius)} {number(wheel_width / 2.0)}" mass="{number(wheel_mass)}" friction="1.4 0.01 0.001" rgba="0.05 0.05 0.05 1"/>
      </body>
      <body name="rear_caster" pos="{number(caster_x)} 0 {number(caster_local_z)}">
        <joint name="caster_ball" type="ball" damping="0.002"/>
        <geom name="caster_geom" type="sphere" size="{number(caster_radius)}" mass="{number(caster_mass)}" friction="0.08 0.001 0.0001" rgba="0.15 0.15 0.15 1"/>
      </body>
    </body>
  </worldbody>
  <actuator>
    <motor name="left_motor" joint="left_wheel_hinge" ctrlrange="-0.12 0.12" ctrllimited="true" gear="1"/>
    <motor name="right_motor" joint="right_wheel_hinge" ctrlrange="-0.12 0.12" ctrllimited="true" gear="1"/>
  </actuator>
</mujoco>
"""
    return xml.encode()


def run_motion_checks(
    dimensions_mm: tuple[float, float, float],
    *,
    assembly_mass_g: float | None,
) -> MotionSimulationResult:
    started = time.perf_counter()
    mujoco = _load_mujoco()
    model_xml = compile_mjcf(dimensions_mm, assembly_mass_g=assembly_mass_g)
    try:
        model = mujoco.MjModel.from_xml_string(model_xml.decode())
    except Exception as error:
        raise SimulationError(
            "SIMULATION_MODEL_INVALID",
            "The deterministic character simulation model could not be loaded.",
        ) from error

    robot_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "robot")
    step_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "step")
    left_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "left_wheel_geom")
    right_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "right_wheel_geom")

    def fresh_data():
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        return data

    def upright(data) -> float:
        return float(data.xmat[robot_id].reshape(3, 3)[2, 2])

    turn = fresh_data()
    turn.ctrl[:] = (-0.07, 0.07)
    minimum_turn_upright = 1.0
    for _ in range(500):
        mujoco.mj_step(model, turn)
        minimum_turn_upright = min(minimum_turn_upright, upright(turn))
    rotation = turn.xmat[robot_id].reshape(3, 3)
    yaw_deg = abs(math.degrees(math.atan2(rotation[1, 0], rotation[0, 0])))

    stop = fresh_data()
    stop.ctrl[:] = (0.07, 0.07)
    for _ in range(350):
        mujoco.mj_step(model, stop)
    stop.ctrl[:] = (0.0, 0.0)
    for _ in range(650):
        mujoco.mj_step(model, stop)
    final_speed = math.sqrt(sum(float(value) ** 2 for value in stop.qvel[:3]))

    step = fresh_data()
    step.ctrl[:] = (0.08, 0.08)
    contacted_step = False
    minimum_step_upright = 1.0
    for _ in range(1800):
        mujoco.mj_step(model, step)
        minimum_step_upright = min(minimum_step_upright, upright(step))
        for contact_index in range(step.ncon):
            contact = step.contact[contact_index]
            pair = {int(contact.geom1), int(contact.geom2)}
            if step_id in pair and (left_id in pair or right_id in pair):
                contacted_step = True

    width_m, depth_m, height_m = (
        value / 1000.0 for value in _finite_positive_triplet(dimensions_mm)
    )
    assumed_cog_height = height_m * 0.55
    support_half_width = max(width_m / 2.0, 0.03)
    static_tip_angle = math.degrees(
        math.atan2(support_half_width, max(assumed_cog_height, 0.001))
    )

    checks = (
        SimulationCheck(
            code="turn_response",
            passed=yaw_deg >= 5.0 and minimum_turn_upright >= 0.7,
            measured_value=round(yaw_deg, 6),
            limit_value=5.0,
            unit="deg",
            message="Opposed wheel commands should produce a bounded turn while upright.",
        ),
        SimulationCheck(
            code="stop_response",
            passed=final_speed <= 0.05,
            measured_value=round(final_speed, 6),
            limit_value=0.05,
            unit="m_s",
            message="The planning model should settle after both wheel commands return to zero.",
        ),
        SimulationCheck(
            code="step_contact",
            passed=contacted_step and minimum_step_upright >= 0.7,
            measured_value=round(minimum_step_upright, 6),
            limit_value=0.7,
            unit="up_axis_dot",
            message="A wheel should contact the 8 mm step without the planning model overturning.",
        ),
        SimulationCheck(
            code="static_tip_margin",
            passed=static_tip_angle >= 15.0,
            measured_value=round(static_tip_angle, 6),
            limit_value=15.0,
            unit="deg",
            message="The estimated static side-tip angle should retain a planning margin.",
        ),
    )
    return MotionSimulationResult(
        engine_version=MUJOCO_VERSION,
        compiler_version=SIMULATION_COMPILER_VERSION,
        # A measured total mass improves one input, but wheel geometry, inertia,
        # mass distribution, actuator response, backlash, latency, and friction
        # are still planning assumptions. Do not promote the whole dynamics
        # model until a versioned measured-dynamics profile exists.
        assumption_level="planning_only",
        model_sha256=hashlib.sha256(model_xml).hexdigest(),
        model_xml=model_xml,
        checks=checks,
        duration_ms=round((time.perf_counter() - started) * 1000.0, 3),
    )


__all__ = [
    "MUJOCO_VERSION",
    "SIMULATION_COMPILER_VERSION",
    "MotionSimulationResult",
    "SimulationCheck",
    "SimulationError",
    "compile_mjcf",
    "run_motion_checks",
]
