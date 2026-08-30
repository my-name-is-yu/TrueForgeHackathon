from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from itertools import islice
from types import MappingProxyType
from typing import Any

from .mujoco_client import (
    MAX_RENDER_DIMENSION,
    MAX_STEPS,
    MAX_TRACE_SCALARS,
    PinnedMujocoClient,
    UPSTREAM_BAD_RESPONSE,
    UPSTREAM_TIMEOUT,
    UPSTREAM_UNAVAILABLE,
    UpstreamToolError,
)

MAX_TOTAL_STEPS = MAX_STEPS
MAX_SEGMENTS = 16
_RENDER_FALLBACK_CODES = frozenset(
    {UPSTREAM_BAD_RESPONSE, UPSTREAM_TIMEOUT, UPSTREAM_UNAVAILABLE}
)


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(float(value))


def _bounded_tuple(value: Any, *, limit: int, name: str) -> tuple[Any, ...]:
    if type(value) is tuple:
        result = value
    else:
        result = tuple(islice(iter(value), limit + 1))
    if len(result) > limit:
        raise ValueError(f"{name} exceeds its bounded size")
    return result


def _numbers(value: Any) -> bool:
    if _number(value):
        return True
    return isinstance(value, list) and all(_numbers(item) for item in value)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class ConstantSegment:
    ctrl: tuple[float, ...]
    n_steps: int
    label: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "ctrl",
            _bounded_tuple(self.ctrl, limit=MAX_TRACE_SCALARS, name="ctrl"),
        )
        if not isinstance(self.label, str) or len(self.label) > 64:
            raise ValueError("segment label is invalid")
        if type(self.n_steps) is not int or not 1 <= self.n_steps <= MAX_STEPS:
            raise ValueError(f"n_steps must be between 1 and {MAX_STEPS}")
        if not all(_number(value) for value in self.ctrl):
            raise ValueError("ctrl must contain finite numbers")


ControllerSegment = ConstantSegment


@dataclass(frozen=True)
class RunConfiguration:
    xml_string: str
    segments: tuple[ConstantSegment, ...]
    initial_qpos: tuple[float, ...] = ()
    initial_qvel: tuple[float, ...] = ()
    initial_ctrl: tuple[float, ...] | None = None
    render: bool = False
    render_width: int = 160
    render_height: int = 120

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "segments",
            _bounded_tuple(self.segments, limit=MAX_SEGMENTS, name="segments"),
        )
        object.__setattr__(
            self,
            "initial_qpos",
            _bounded_tuple(
                self.initial_qpos,
                limit=MAX_TRACE_SCALARS,
                name="initial_qpos",
            ),
        )
        object.__setattr__(
            self,
            "initial_qvel",
            _bounded_tuple(
                self.initial_qvel,
                limit=MAX_TRACE_SCALARS,
                name="initial_qvel",
            ),
        )
        if self.initial_ctrl is not None:
            object.__setattr__(
                self,
                "initial_ctrl",
                _bounded_tuple(
                    self.initial_ctrl,
                    limit=MAX_TRACE_SCALARS,
                    name="initial_ctrl",
                ),
            )
        if not isinstance(self.xml_string, str) or not self.xml_string:
            raise ValueError("xml_string is required")
        if not self.segments:
            raise ValueError("at least one constant segment is required")
        if sum(segment.n_steps for segment in self.segments) > MAX_TOTAL_STEPS:
            raise ValueError(f"total steps must not exceed {MAX_TOTAL_STEPS}")
        if not all(_number(value) for value in self.initial_qpos + self.initial_qvel):
            raise ValueError("initial state must contain finite numbers")
        if self.initial_ctrl is not None and not all(_number(value) for value in self.initial_ctrl):
            raise ValueError("initial control must contain finite numbers")
        if (
            type(self.render_width) is not int
            or type(self.render_height) is not int
            or not 1 <= self.render_width <= MAX_RENDER_DIMENSION
            or not 1 <= self.render_height <= MAX_RENDER_DIMENSION
        ):
            raise ValueError("render dimensions are invalid")


@dataclass(frozen=True)
class SegmentRecord:
    label: str
    step_count: int
    ctrl: tuple[float, ...]
    timeseries: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "ctrl", tuple(self.ctrl))
        object.__setattr__(
            self,
            "timeseries",
            tuple(_freeze(row) for row in self.timeseries),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "step_count": self.step_count,
            "ctrl": list(self.ctrl),
            "timeseries": [_thaw(row) for row in self.timeseries],
        }


@dataclass(frozen=True)
class RunRecord:
    step_count: int
    segments: tuple[SegmentRecord, ...]
    image_png: bytes | None = field(default=None, repr=False)
    render_fallback: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "segments", tuple(self.segments))

    @property
    def numeric_only(self) -> bool:
        return self.image_png is None

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_count": self.step_count,
            "segments": [segment.as_dict() for segment in self.segments],
            "render": {
                "image_available": self.image_png is not None,
                "numeric_only_fallback": self.render_fallback,
            },
        }


class DeterministicRunner:
    def __init__(self, client: PinnedMujocoClient | None = None) -> None:
        self.client = client or PinnedMujocoClient()

    async def run(self, configuration: RunConfiguration) -> RunRecord:
        async with self.client:
            return await self._run(configuration)

    async def _run(self, configuration: RunConfiguration) -> RunRecord:
        slot = await self.client.load(configuration.xml_string)
        projected_scalars = sum(segment.n_steps for segment in configuration.segments) * (
            slot.summary["nq"] + slot.summary["nv"] + slot.summary["nu"] + 4
        )
        if projected_scalars > MAX_TRACE_SCALARS:
            raise ValueError("requested run exceeds the bounded numeric record budget")
        if configuration.initial_qpos and len(configuration.initial_qpos) != slot.summary["nq"]:
            raise ValueError("initial qpos width does not match the loaded model")
        if configuration.initial_qvel and len(configuration.initial_qvel) != slot.summary["nv"]:
            raise ValueError("initial qvel width does not match the loaded model")
        if (
            configuration.initial_ctrl is not None
            and len(configuration.initial_ctrl) != slot.summary["nu"]
        ):
            raise ValueError("initial ctrl width does not match the loaded model")
        await self.client.reset(slot)
        await self.client.set_state(
            slot,
            qpos=list(configuration.initial_qpos) or None,
            qvel=list(configuration.initial_qvel) or None,
            ctrl=list(configuration.initial_ctrl) if configuration.initial_ctrl is not None else None,
        )

        records: list[SegmentRecord] = []
        previous_timestamp: float | None = None
        timestep = slot.summary["timestep"]
        interval_tolerance = timestep * 1e-6
        for segment in configuration.segments:
            if len(segment.ctrl) != slot.summary["nu"]:
                raise ValueError("controller width does not match the loaded model")
            payload = await self.client.run_segment(
                slot,
                ctrl=list(segment.ctrl),
                n_steps=segment.n_steps,
            )
            rows = tuple(
                {**row, "ctrl": list(segment.ctrl)} for row in payload["timeseries"]
            )
            if len(rows) != segment.n_steps or payload["n_steps"] != segment.n_steps:
                raise ValueError("runner received an unexpected step count")
            if not all(
                isinstance(row, dict) and all(_numbers(value) for value in row.values())
                for row in rows
            ):
                raise ValueError("runner received a non-numeric run record")
            expected_start = timestep if previous_timestamp is None else previous_timestamp + timestep
            if not math.isclose(
                rows[0]["t"],
                expected_start,
                rel_tol=0.0,
                abs_tol=interval_tolerance,
            ):
                raise ValueError("runner received discontinuous segment timestamps")
            previous_timestamp = rows[-1]["t"]
            records.append(
                SegmentRecord(
                    label=segment.label,
                    step_count=segment.n_steps,
                    ctrl=segment.ctrl,
                    timeseries=rows,
                )
            )

        image: bytes | None = None
        render_fallback = False
        if configuration.render:
            try:
                image = await self.client.render(
                    slot,
                    width=configuration.render_width,
                    height=configuration.render_height,
                )
            except UpstreamToolError as error:
                if error.code not in _RENDER_FALLBACK_CODES:
                    raise
                render_fallback = True

        return RunRecord(
            step_count=sum(record.step_count for record in records),
            segments=tuple(records),
            image_png=image,
            render_fallback=render_fallback,
        )


async def run_deterministic(
    configuration: RunConfiguration,
    *,
    client: PinnedMujocoClient | None = None,
) -> RunRecord:
    return await DeterministicRunner(client).run(configuration)


def run_deterministic_sync(
    configuration: RunConfiguration,
    *,
    client: PinnedMujocoClient | None = None,
) -> RunRecord:
    return asyncio.run(run_deterministic(configuration, client=client))


__all__ = [
    "ConstantSegment",
    "ControllerSegment",
    "DeterministicRunner",
    "MAX_SEGMENTS",
    "MAX_TOTAL_STEPS",
    "RunConfiguration",
    "RunRecord",
    "SegmentRecord",
    "run_deterministic",
    "run_deterministic_sync",
]
