from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from typing import Any

from .mujoco_client import (
    MAX_RENDER_DIMENSION,
    MAX_STEPS,
    PinnedMujocoClient,
    UPSTREAM_BAD_RESPONSE,
    UPSTREAM_TIMEOUT,
    UPSTREAM_UNAVAILABLE,
    UpstreamToolError,
)

MAX_TOTAL_STEPS = MAX_STEPS
_RENDER_FALLBACK_CODES = frozenset(
    {UPSTREAM_BAD_RESPONSE, UPSTREAM_TIMEOUT, UPSTREAM_UNAVAILABLE}
)


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(float(value))


def _numbers(value: Any) -> bool:
    if _number(value):
        return True
    return isinstance(value, list) and all(_numbers(item) for item in value)


@dataclass(frozen=True)
class ConstantSegment:
    ctrl: tuple[float, ...]
    n_steps: int
    label: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.ctrl, tuple):
            object.__setattr__(self, "ctrl", tuple(self.ctrl))
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
        if not isinstance(self.segments, tuple):
            object.__setattr__(self, "segments", tuple(self.segments))
        if not isinstance(self.initial_qpos, tuple):
            object.__setattr__(self, "initial_qpos", tuple(self.initial_qpos))
        if not isinstance(self.initial_qvel, tuple):
            object.__setattr__(self, "initial_qvel", tuple(self.initial_qvel))
        if self.initial_ctrl is not None and not isinstance(self.initial_ctrl, tuple):
            object.__setattr__(self, "initial_ctrl", tuple(self.initial_ctrl))
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
    timeseries: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "step_count": self.step_count,
            "ctrl": list(self.ctrl),
            "timeseries": [dict(row) for row in self.timeseries],
        }


@dataclass(frozen=True)
class RunRecord:
    step_count: int
    segments: tuple[SegmentRecord, ...]
    image_png: bytes | None = field(default=None, repr=False)
    render_fallback: bool = False

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
        if self.client.ready:
            return await self._run(configuration)
        async with self.client:
            return await self._run(configuration)

    async def _run(self, configuration: RunConfiguration) -> RunRecord:
        slot = await self.client.load(configuration.xml_string)
        await self.client.reset(slot)
        await self.client.set_state(
            slot,
            qpos=list(configuration.initial_qpos) or None,
            qvel=list(configuration.initial_qvel) or None,
            ctrl=list(configuration.initial_ctrl) if configuration.initial_ctrl is not None else None,
        )

        records: list[SegmentRecord] = []
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
    "MAX_TOTAL_STEPS",
    "RunConfiguration",
    "RunRecord",
    "SegmentRecord",
    "run_deterministic",
    "run_deterministic_sync",
]
