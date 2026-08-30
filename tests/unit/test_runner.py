from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from asset_autopsy.mujoco_client import (
    SAFE_NEXT_ACTION,
    UPSTREAM_TIMEOUT,
    UpstreamToolError,
)
from asset_autopsy.runner import (
    ConstantSegment,
    DeterministicRunner,
    PartialRunError,
    RunConfiguration,
)


class SegmentFailureClient:
    def __init__(self, fail_on_call: int) -> None:
        self.fail_on_call = fail_on_call
        self.calls = 0
        self.time = 0.0

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        return None

    async def load(self, _xml_string: str):
        return SimpleNamespace(
            summary={"nq": 1, "nv": 1, "nu": 1, "timestep": 0.1},
            state="ready",
        )

    async def reset(self, _slot) -> None:
        return None

    async def set_state(self, _slot, **_state) -> None:
        return None

    async def run_segment(self, _slot, *, ctrl, n_steps, track):
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise UpstreamToolError(
                UPSTREAM_TIMEOUT,
                "primary upstream timeout",
                True,
                SAFE_NEXT_ACTION,
            )
        rows = []
        for _ in range(n_steps):
            self.time += 0.1
            rows.append(
                {
                    "t": self.time,
                    "qpos": [0.0],
                    "qvel": [0.0],
                    "E_pot": 0.0,
                    "E_kin": 0.0,
                }
            )
        return {"n_steps": n_steps, "timeseries": rows}


def configuration() -> RunConfiguration:
    return RunConfiguration(
        xml_string="<mujoco/>",
        segments=(
            ConstantSegment(ctrl=(0.0,), n_steps=2, label="first"),
            ConstantSegment(ctrl=(0.5,), n_steps=2, label="second"),
        ),
        initial_qpos=(0.0,),
        initial_qvel=(0.0,),
        initial_ctrl=(0.0,),
    )


def test_failure_before_the_first_completed_segment_has_no_partial_record() -> None:
    client = SegmentFailureClient(fail_on_call=1)

    with pytest.raises(UpstreamToolError) as caught:
        asyncio.run(DeterministicRunner(client).run(configuration()))

    assert type(caught.value) is UpstreamToolError
    assert caught.value.code == UPSTREAM_TIMEOUT
    assert client.calls == 1


def test_failure_after_a_completed_segment_preserves_only_that_segment() -> None:
    client = SegmentFailureClient(fail_on_call=2)

    with pytest.raises(PartialRunError) as caught:
        asyncio.run(DeterministicRunner(client).run(configuration()))

    assert caught.value.code == UPSTREAM_TIMEOUT
    assert str(caught.value) == "primary upstream timeout"
    partial = caught.value.partial_record
    assert partial.step_count == 2
    assert [segment.label for segment in partial.segments] == ["first"]
    assert len(partial.segments[0].timeseries) == 2
    assert client.calls == 2
