from __future__ import annotations

import asyncio
import base64
from io import BytesIO
from itertools import repeat
import json
from types import SimpleNamespace
from typing import Any, cast

import pytest
from PIL import Image

from asset_autopsy.mujoco_client import (
    REQUIRED_TOOL_SCHEMAS,
    REQUIRED_ENVIRONMENT,
    REQUIRED_TOOL_NAMES,
    SAFE_MESSAGE,
    SAFE_NEXT_ACTION,
    SAFE_SLOT_ACTION,
    SlotState,
    UPSTREAM_BAD_RESPONSE,
    UPSTREAM_COMMIT,
    UPSTREAM_STEP_MISMATCH,
    UPSTREAM_UNAVAILABLE,
    UpstreamToolError,
    normalize_json_result,
    server_parameters,
    verify_pinned_upstream,
)
from asset_autopsy.runner import (
    MAX_SEGMENTS,
    ConstantSegment,
    DeterministicRunner,
    RunConfiguration,
)


def _text_result(payload: object, *, is_error: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        isError=is_error,
        content=[SimpleNamespace(type="text", text=json.dumps(payload))],
    )


def _png_bytes(*, mode: str = "RGB", width: int = 160, height: int = 120) -> bytes:
    output = BytesIO()
    Image.new(mode, (width, height)).save(output, format="PNG")
    return output.getvalue()


class _FakeTransport:
    def __init__(self) -> None:
        self.closed = False

    async def __aenter__(self) -> tuple[object, object]:
        return object(), object()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.closed = True


class _BlockingCloseTransport(_FakeTransport):
    def __init__(self) -> None:
        super().__init__()
        self.close_started = asyncio.Event()
        self.allow_close = asyncio.Event()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.close_started.set()
        await self.allow_close.wait()
        self.closed = True


class _FailingCloseTransport(_FakeTransport):
    async def __aexit__(self, exc_type, exc, tb) -> None:
        raise RuntimeError("private close failure")


class _BlockingFailingCloseTransport(_BlockingCloseTransport):
    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.close_started.set()
        await self.allow_close.wait()
        raise RuntimeError("private close failure")


class _FakeSession:
    def __init__(
        self,
        _read: object,
        _write: object,
        *,
        timeout_on_reset: bool = False,
        incomplete_run: bool = False,
        wrong_width_run: bool = False,
        block_on_run: bool = False,
        synchronize_two_runs: bool = False,
        block_on_initialize: bool = False,
        render_is_error: bool = False,
        invalid_render_png: bool = False,
        timestamp_mode: str | None = None,
        boundary_mode: str | None = None,
        wrong_load_name: bool = False,
        invalid_load_metadata: str | None = None,
        negative_contacts: bool = False,
        nested_final_energy: bool = False,
        reset_time: float = 0.0,
        set_state_time: float | None = None,
        final_state_mismatch: str | None = None,
        nq: int = 0,
        nv: int = 0,
        nu: int = 0,
        timestep: float = 0.002,
    ) -> None:
        self.closed = False
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.timeout_on_reset = timeout_on_reset
        self.incomplete_run = incomplete_run
        self.wrong_width_run = wrong_width_run
        self.block_on_run = block_on_run
        self.synchronize_two_runs = synchronize_two_runs
        self.block_on_initialize = block_on_initialize
        self.render_is_error = render_is_error
        self.invalid_render_png = invalid_render_png
        self.timestamp_mode = timestamp_mode
        self.boundary_mode = boundary_mode
        self.wrong_load_name = wrong_load_name
        self.invalid_load_metadata = invalid_load_metadata
        self.negative_contacts = negative_contacts
        self.nested_final_energy = nested_final_energy
        self.reset_time = reset_time
        self.set_state_time = set_state_time
        self.final_state_mismatch = final_state_mismatch
        self.nq = nq
        self.nv = nv
        self.nu = nu
        self.timestep = timestep
        self.current_times: dict[str, float] = {}
        self.run_started = asyncio.Event()
        self.two_runs_started = asyncio.Event()
        self.run_call_count = 0
        self.initialize_started = asyncio.Event()

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.closed = True

    async def initialize(self) -> None:
        if self.block_on_initialize:
            self.initialize_started.set()
            await asyncio.Event().wait()
        return None

    async def list_tools(self) -> SimpleNamespace:
        return SimpleNamespace(
            tools=[
                SimpleNamespace(name=name, inputSchema=schema)
                for name, schema in REQUIRED_TOOL_SCHEMAS.items()
            ]
        )

    async def call_tool(self, name: str, *, arguments: dict[str, object]) -> SimpleNamespace:
        self.calls.append((name, arguments))
        if name == "sim_load":
            nq = self.nq
            nv = self.nv
            nu = self.nu
            timestep = self.timestep
            mujoco_version = "3.5.0"
            if self.invalid_load_metadata == "negative_dimension":
                nu = -1
            elif self.invalid_load_metadata == "invalid_timestep":
                timestep = 0.0
            elif self.invalid_load_metadata == "runtime_version":
                mujoco_version = "3.4.0"
            return _text_result(
                {
                    "name": "unexpected" if self.wrong_load_name else arguments["name"],
                    "mujoco_version": mujoco_version,
                    "nq": nq,
                    "nv": nv,
                    "nu": nu,
                    "nbody": 1,
                    "ngeom": 0,
                    "njnt": 0,
                    "nsite": 0,
                    "nsensor": 0,
                    "ncam": 0,
                    "timestep": timestep,
                    "has_renderer": False,
                    "bodies": ["world"],
                    "joints": [],
                    "actuators": [],
                    "sensors": [],
                    "cameras": [],
                }
            )
        if name == "sim_reset":
            if self.timeout_on_reset:
                await asyncio.sleep(1)
            return _text_result({"status": "reset", "time": self.reset_time})
        if name == "sim_set_state":
            sim_name = str(arguments["sim_name"])
            time = self.current_times.get(sim_name, 0.0)
            if self.set_state_time is not None:
                time = self.set_state_time
            return _text_result({"status": "ok", "time": time})
        if name == "run_and_analyze":
            steps = arguments["n_steps"]
            if self.block_on_run:
                self.run_started.set()
                await asyncio.Event().wait()
            if self.synchronize_two_runs:
                self.run_call_count += 1
                if self.run_call_count == 2:
                    self.two_runs_started.set()
                await self.two_runs_started.wait()
            sim_name = str(arguments["sim_name"])
            current_time = self.current_times.get(sim_name, 0.0)
            qpos = [0.0] if self.wrong_width_run else [0.0] * self.nq
            qvel = [0.0] if self.wrong_width_run else [0.0] * self.nv
            timestamps = [current_time + self.timestep * (index + 1) for index in range(steps)]
            if self.timestamp_mode == "duplicate" and len(timestamps) >= 2:
                timestamps[1] = timestamps[0]
            elif self.timestamp_mode == "reversed":
                timestamps.reverse()
            elif self.timestamp_mode == "wrong_interval" and len(timestamps) >= 3:
                timestamps[2] = timestamps[1] + 2 * self.timestep
            if self.boundary_mode and current_time and timestamps:
                if self.boundary_mode == "duplicate":
                    timestamps[0] = current_time
                elif self.boundary_mode == "backward":
                    timestamps[0] = current_time - self.timestep
                elif self.boundary_mode == "late":
                    timestamps[0] += self.timestep * 9e-6
            rows = []
            for timestamp in timestamps:
                row = {
                    "t": timestamp,
                    "E_pot": 0.0,
                    "E_kin": 0.0,
                    "ncon": -1 if self.negative_contacts else 0,
                }
                if not self.incomplete_run:
                    row.update({"qpos": qpos, "qvel": qvel})
                rows.append(row)
            sim_time = [timestamps[0], timestamps[-1]]
            self.current_times[sim_name] = timestamps[-1]
            if self.timestamp_mode == "inconsistent":
                sim_time[1] += 0.002
            final_qpos = [1.0] * self.nq if self.final_state_mismatch == "qpos" else qpos
            final_qvel = [1.0] * self.nv if self.final_state_mismatch == "qvel" else qvel
            final_energy = (
                [1.0, 0.0]
                if self.final_state_mismatch == "energy"
                else ([[0.0], [0.0]] if self.nested_final_energy else [0.0, 0.0])
            )
            final_contacts = (
                1
                if self.final_state_mismatch == "contacts"
                else (-1 if self.negative_contacts else 0)
            )
            return _text_result(
                {
                    "n_steps": steps,
                    "sim_time": sim_time,
                    "final_state": {
                        "qpos": final_qpos,
                        "qvel": final_qvel,
                        "n_contacts": final_contacts,
                        "energy": final_energy,
                    },
                    "timeseries": rows,
                }
            )
        if name == "render_snapshot":
            if self.invalid_render_png:
                return SimpleNamespace(
                    isError=False,
                    content=[
                        SimpleNamespace(
                            type="image",
                            mimeType="image/png",
                            data="not-base64",
                        ),
                        SimpleNamespace(type="text", text="synthetic"),
                    ],
                )
            return _text_result(
                {"error": "synthetic render failure"},
                is_error=self.render_is_error,
            )
        raise AssertionError(name)


def _fake_client(
    *,
    timeout_on_reset: bool = False,
    incomplete_run: bool = False,
    wrong_width_run: bool = False,
    block_on_run: bool = False,
    synchronize_two_runs: bool = False,
    block_on_initialize: bool = False,
    render_is_error: bool = False,
    invalid_render_png: bool = False,
    timestamp_mode: str | None = None,
    boundary_mode: str | None = None,
    wrong_load_name: bool = False,
    invalid_load_metadata: str | None = None,
    negative_contacts: bool = False,
    nested_final_energy: bool = False,
    reset_time: float = 0.0,
    set_state_time: float | None = None,
    final_state_mismatch: str | None = None,
    nq: int = 0,
    nv: int = 0,
    nu: int = 0,
    timestep: float = 0.002,
):
    transport = _FakeTransport()
    session: _FakeSession | None = None

    def make_session(read: object, write: object) -> _FakeSession:
        nonlocal session
        session = _FakeSession(
            read,
            write,
            timeout_on_reset=timeout_on_reset,
            incomplete_run=incomplete_run,
            wrong_width_run=wrong_width_run,
            block_on_run=block_on_run,
            synchronize_two_runs=synchronize_two_runs,
            block_on_initialize=block_on_initialize,
            render_is_error=render_is_error,
            invalid_render_png=invalid_render_png,
            timestamp_mode=timestamp_mode,
            boundary_mode=boundary_mode,
            wrong_load_name=wrong_load_name,
            invalid_load_metadata=invalid_load_metadata,
            negative_contacts=negative_contacts,
            nested_final_energy=nested_final_energy,
            reset_time=reset_time,
            set_state_time=set_state_time,
            final_state_mismatch=final_state_mismatch,
            nq=nq,
            nv=nv,
            nu=nu,
            timestep=timestep,
        )
        return session

    return transport, make_session, lambda: session


def test_child_environment_is_allowlisted_and_pinned() -> None:
    parameters = server_parameters()
    assert parameters.args == ["-P", "-m", "mujoco_mcp", "--transport", "stdio"]
    assert parameters.env is not None
    assert parameters.env["MUJOCO_GL"] == "cgl"
    assert parameters.env["MUJOCO_MCP_MAX_WORKERS"] == "1"
    assert parameters.env["MUJOCO_MCP_RENDER_WIDTH"] == "640"
    assert parameters.env["MUJOCO_MCP_RENDER_HEIGHT"] == "480"
    assert parameters.env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert set(parameters.env) <= {
        "PATH",
        "HOME",
        "LANG",
        "MUJOCO_GL",
        "MUJOCO_MCP_MAX_WORKERS",
        "MUJOCO_MCP_RENDER_WIDTH",
        "MUJOCO_MCP_RENDER_HEIGHT",
        "PYTHONDONTWRITEBYTECODE",
    }
    verify_pinned_upstream()
    assert UPSTREAM_COMMIT == "ce9bed80ec3698d7b778230abc21f2228a3ce94b"


def test_normalizer_rejects_wrapped_error_and_unexpected_content() -> None:
    with pytest.raises(UpstreamToolError) as wrapped:
        normalize_json_result(_text_result({"error": "private traceback"}), lambda _: True)
    assert wrapped.value.envelope() == {
        "code": "UPSTREAM_UNAVAILABLE",
        "message": SAFE_MESSAGE,
        "retryable": True,
        "next_action": SAFE_NEXT_ACTION,
    }

    result = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text='{"ok": true}'),
            SimpleNamespace(type="text", text="private traceback"),
        ],
        isError=False,
    )
    with pytest.raises(UpstreamToolError) as unexpected:
        normalize_json_result(result, lambda _: True)
    assert unexpected.value.envelope() == {
        "code": UPSTREAM_BAD_RESPONSE,
        "message": "Upstream response content was unexpected.",
        "retryable": False,
        "next_action": SAFE_SLOT_ACTION,
    }

    with pytest.raises(UpstreamToolError) as flagged:
        normalize_json_result(
            SimpleNamespace(isError=True, content=[]),
            lambda _: True,
        )
    assert flagged.value.envelope() == {
        "code": UPSTREAM_UNAVAILABLE,
        "message": SAFE_MESSAGE,
        "retryable": True,
        "next_action": SAFE_NEXT_ACTION,
    }


def test_runner_configuration_requires_constant_bounded_segments() -> None:
    configuration = RunConfiguration(
        xml_string="<mujoco/>",
        segments=(ConstantSegment((0.0,), 2, "hold"),),
    )
    assert configuration.segments[0].n_steps == 2
    with pytest.raises(ValueError):
        ConstantSegment((0.0,), 0)
    with pytest.raises(ValueError):
        RunConfiguration(
            xml_string="<mujoco/>",
            segments=tuple(ConstantSegment((), 1) for _ in range(100_001)),
        )
    assert len(
        RunConfiguration(
            xml_string="<mujoco/>",
            segments=tuple(ConstantSegment((), 1) for _ in range(MAX_SEGMENTS)),
        ).segments
    ) == MAX_SEGMENTS
    with pytest.raises(ValueError, match="segments exceeds its bounded size"):
        RunConfiguration(
            xml_string="<mujoco/>",
            segments=tuple(ConstantSegment((), 1) for _ in range(MAX_SEGMENTS + 1)),
        )
    with pytest.raises(ValueError, match="segments exceeds its bounded size"):
        RunConfiguration(
            xml_string="<mujoco/>",
            segments=repeat(ConstantSegment((), 1)),
        )

    class OversizedTuple(tuple):
        def __len__(self) -> int:
            return 1

        def __iter__(self):
            return iter((ConstantSegment((), 1),) * (MAX_SEGMENTS + 1))

    with pytest.raises(ValueError, match="segments exceeds its bounded size"):
        RunConfiguration(
            xml_string="<mujoco/>",
            segments=OversizedTuple((ConstantSegment((), 1),)),
        )


def test_client_uses_only_xml_string_and_poisoned_slots_cannot_be_reused() -> None:
    async def check() -> None:
        transport, make_session, get_session = _fake_client()
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        async with PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=make_session,
        ) as client:
            xml = "<mujoco model=\"synthetic\"/>"
            slot = await client.load(xml)
            await client.reset(slot)
            await client.set_state(slot)
            await client.run_segment(slot, ctrl=[], n_steps=2)
            calls = get_session().calls
            assert calls[0][0] == "sim_load"
            assert calls[0][1]["xml_string"] == xml
            assert "xml_path" not in calls[0][1]
            assert calls[3][1]["capture_every_n"] == 0

        assert slot.state is SlotState.CLOSED
        assert transport.closed is True
        assert get_session().closed is True

    asyncio.run(check())


def test_load_rejects_response_for_a_different_simulation_name() -> None:
    async def check() -> None:
        transport, make_session, _get_session = _fake_client(wrong_load_name=True)
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        async with PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=make_session,
        ) as client:
            with pytest.raises(UpstreamToolError) as caught:
                await client.load("<mujoco model=\"synthetic\"/>")
            assert caught.value.code == UPSTREAM_BAD_RESPONSE
            assert client._slots[0].state is SlotState.POISONED

    asyncio.run(check())


def test_timeout_terminates_child_and_poisoned_slot() -> None:
    async def check() -> None:
        transport, make_session, get_session = _fake_client(timeout_on_reset=True)
        from asset_autopsy.mujoco_client import PinnedMujocoClient, UPSTREAM_TIMEOUT

        client = PinnedMujocoClient(
            call_timeout=0.01,
            startup_timeout=1.0,
            transport_factory=lambda _parameters: transport,
            session_factory=make_session,
        )
        async with client:
            slot = await client.load("<mujoco model=\"synthetic\"/>")
            with pytest.raises(UpstreamToolError) as caught:
                await client.reset(slot)
            assert caught.value.code == UPSTREAM_TIMEOUT
            assert slot.state is SlotState.POISONED
            assert client.ready is False
            with pytest.raises(UpstreamToolError) as reused:
                await client.reset(slot)
            assert reused.value.code == "SLOT_POISONED"
        assert transport.closed is True
        assert get_session().closed is True

    asyncio.run(check())


def test_reset_rejects_nonzero_upstream_clock() -> None:
    async def check() -> None:
        transport, make_session, _get_session = _fake_client(reset_time=1.0)
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        async with PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=make_session,
        ) as client:
            slot = await client.load("<mujoco model=\"synthetic\"/>")
            with pytest.raises(UpstreamToolError) as caught:
                await client.reset(slot)
            assert caught.value.code == UPSTREAM_BAD_RESPONSE
            assert slot.state is SlotState.POISONED

    asyncio.run(check())


def test_set_state_requires_the_slot_current_clock() -> None:
    async def check() -> None:
        transport, make_session, _get_session = _fake_client(set_state_time=1.0)
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        async with PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=make_session,
        ) as client:
            slot = await client.load("<mujoco model=\"synthetic\"/>")
            await client.reset(slot)
            with pytest.raises(UpstreamToolError) as caught:
                await client.set_state(slot)
            assert caught.value.code == UPSTREAM_BAD_RESPONSE
            assert slot.state is SlotState.POISONED

    asyncio.run(check())


def test_set_state_accepts_the_clock_after_a_segment() -> None:
    async def check() -> None:
        transport, make_session, _get_session = _fake_client()
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        async with PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=make_session,
        ) as client:
            slot = await client.load("<mujoco model=\"synthetic\"/>")
            await client.reset(slot)
            await client.run_segment(slot, ctrl=[], n_steps=1)
            await client.set_state(slot)
            assert slot.state is SlotState.READY

    asyncio.run(check())


def test_run_response_requires_requested_signals_and_model_widths() -> None:
    async def check() -> None:
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        for incomplete_run, wrong_width_run, nested_final_energy in (
            (True, False, False),
            (False, True, False),
            (False, False, True),
        ):
            transport, make_session, get_session = _fake_client(
                incomplete_run=incomplete_run,
                wrong_width_run=wrong_width_run,
                nested_final_energy=nested_final_energy,
                nq=0,
                nv=0,
            )
            async with PinnedMujocoClient(
                transport_factory=lambda _parameters: transport,
                session_factory=make_session,
            ) as client:
                slot = await client.load("<mujoco model=\"synthetic\"/>")
                with pytest.raises(UpstreamToolError) as caught:
                    await client.run_segment(slot, ctrl=[], n_steps=1)
                assert caught.value.code == UPSTREAM_BAD_RESPONSE
                assert slot.state is SlotState.POISONED
            assert get_session().closed is True

    asyncio.run(check())


@pytest.mark.parametrize("mismatch", ("qpos", "qvel", "energy", "contacts"))
def test_run_response_requires_final_state_to_match_last_sample(mismatch: str) -> None:
    async def check() -> None:
        transport, make_session, _get_session = _fake_client(
            final_state_mismatch=mismatch,
            nq=1,
            nv=1,
        )
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        async with PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=make_session,
        ) as client:
            slot = await client.load("<mujoco model=\"synthetic\"/>")
            with pytest.raises(UpstreamToolError) as caught:
                await client.run_segment(slot, ctrl=[], n_steps=1)
            assert caught.value.code == UPSTREAM_BAD_RESPONSE
            assert slot.state is SlotState.POISONED

    asyncio.run(check())


@pytest.mark.parametrize(
    "timestamp_mode", ("duplicate", "reversed", "inconsistent", "wrong_interval")
)
def test_run_response_requires_monotonic_consistent_timestamps(timestamp_mode: str) -> None:
    async def check() -> None:
        transport, make_session, _get_session = _fake_client(timestamp_mode=timestamp_mode)
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        async with PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=make_session,
        ) as client:
            slot = await client.load("<mujoco model=\"synthetic\"/>")
            with pytest.raises(UpstreamToolError) as caught:
                await client.run_segment(slot, ctrl=[], n_steps=3)
            assert caught.value.code == UPSTREAM_BAD_RESPONSE
            assert slot.state is SlotState.POISONED

    asyncio.run(check())


def test_run_response_interval_tolerance_scales_to_tiny_timesteps() -> None:
    async def check() -> None:
        transport, make_session, _get_session = _fake_client(
            timestamp_mode="wrong_interval", timestep=1e-12
        )
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        async with PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=make_session,
        ) as client:
            slot = await client.load("<mujoco model=\"synthetic\"/>")
            with pytest.raises(UpstreamToolError) as caught:
                await client.run_segment(slot, ctrl=[], n_steps=3)
            assert caught.value.code == UPSTREAM_BAD_RESPONSE
            assert slot.state is SlotState.POISONED

    asyncio.run(check())


def test_direct_run_rejects_a_trace_disconnected_from_the_slot_clock() -> None:
    async def check() -> None:
        transport, make_session, _get_session = _fake_client(boundary_mode="duplicate")
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        async with PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=make_session,
        ) as client:
            slot = await client.load("<mujoco model=\"synthetic\"/>")
            await client.run_segment(slot, ctrl=[], n_steps=1)
            with pytest.raises(UpstreamToolError) as caught:
                await client.run_segment(slot, ctrl=[], n_steps=1)
            assert caught.value.code == UPSTREAM_BAD_RESPONSE
            assert slot.state is SlotState.POISONED

    asyncio.run(check())


@pytest.mark.parametrize("boundary_mode", ("duplicate", "backward"))
def test_runner_rejects_noncontiguous_segment_boundaries(boundary_mode: str) -> None:
    async def check() -> None:
        transport, make_session, _get_session = _fake_client(boundary_mode=boundary_mode)
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        client = PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=make_session,
        )
        async with client:
            with pytest.raises(UpstreamToolError) as caught:
                await DeterministicRunner(client).run(
                    RunConfiguration(
                        xml_string="<mujoco model=\"synthetic\"/>",
                        segments=(
                            ConstantSegment((), 1, "first"),
                            ConstantSegment((), 1, "second"),
                        ),
                    )
                )
            assert caught.value.code == UPSTREAM_BAD_RESPONSE
            assert client._slots[-1].state is SlotState.POISONED

    asyncio.run(check())


def test_cancelled_context_preserves_cancellation_when_cleanup_fails() -> None:
    async def check() -> None:
        transport = _FailingCloseTransport()
        session: _FakeSession | None = None
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        def make_session(read: object, write: object) -> _FakeSession:
            nonlocal session
            session = _FakeSession(read, write)
            return session

        client = PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=make_session,
        )
        entered = asyncio.Event()

        async def owner() -> None:
            async with client:
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(owner())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert client.ready is False
        assert session is not None and session.closed is True

    asyncio.run(check())


@pytest.mark.parametrize(
    "invalid_load_metadata",
    ("negative_dimension", "invalid_timestep", "runtime_version"),
)
def test_load_rejects_impossible_metadata(invalid_load_metadata: str) -> None:
    async def check() -> None:
        transport, make_session, _get_session = _fake_client(
            invalid_load_metadata=invalid_load_metadata
        )
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        async with PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=make_session,
        ) as client:
            with pytest.raises(UpstreamToolError) as caught:
                await client.load("<mujoco model=\"synthetic\"/>")
            assert caught.value.code == UPSTREAM_BAD_RESPONSE
            assert client._slots[-1].state is SlotState.POISONED

    asyncio.run(check())


@pytest.mark.parametrize("timeout_name", ("call_timeout", "render_timeout", "startup_timeout"))
@pytest.mark.parametrize("timeout_value", (float("nan"), float("inf"), 0.0, -1.0))
def test_client_rejects_nonfinite_or_nonpositive_timeouts(
    timeout_name: str, timeout_value: float
) -> None:
    from asset_autopsy.mujoco_client import PinnedMujocoClient

    with pytest.raises(ValueError, match="finite and positive"):
        PinnedMujocoClient(**{timeout_name: timeout_value})


def test_render_failure_returns_one_numeric_only_fallback() -> None:
    async def run() -> None:
        transport, make_session, _get_session = _fake_client(
            invalid_render_png=True,
            nu=1,
        )
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        record = await DeterministicRunner(
            PinnedMujocoClient(
                transport_factory=lambda _parameters: transport,
                session_factory=make_session,
            )
        ).run(
            RunConfiguration(
                xml_string="<mujoco model=\"synthetic\"/>",
                segments=(ConstantSegment((0.25,), 2, "numeric"),),
                render=True,
            )
        )
        assert record.step_count == 2
        assert record.image_png is None
        assert record.render_fallback is True
        assert record.as_dict()["render"]["numeric_only_fallback"] is True
        assert record.segments[0].ctrl == (0.25,)
        assert record.as_dict()["segments"][0]["ctrl"] == [0.25]
        assert record.as_dict()["segments"][0]["timeseries"][0]["ctrl"] == [0.25]

    asyncio.run(run())


def test_render_error_flag_uses_bounded_typed_error_path() -> None:
    async def check() -> None:
        transport, make_session, _get_session = _fake_client(
            render_is_error=True,
            nu=1,
        )
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        async with PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=make_session,
        ) as client:
            slot = await client.load("<mujoco model=\"synthetic\"/>")
            with pytest.raises(UpstreamToolError) as caught:
                await client.render(slot)
            assert caught.value.code == UPSTREAM_UNAVAILABLE
            assert caught.value.envelope() == {
                "code": UPSTREAM_UNAVAILABLE,
                "message": SAFE_MESSAGE,
                "retryable": True,
                "next_action": SAFE_NEXT_ACTION,
            }
            assert slot.state is SlotState.POISONED

    asyncio.run(check())


def test_run_records_are_immutable_but_as_dict_is_independent() -> None:
    async def run() -> None:
        transport, make_session, _get_session = _fake_client(
            nq=1,
            nv=1,
            nu=1,
        )
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        record = await DeterministicRunner(
            PinnedMujocoClient(
                transport_factory=lambda _parameters: transport,
                session_factory=make_session,
            )
        ).run(
            RunConfiguration(
                xml_string="<mujoco model=\"synthetic\"/>",
                segments=(ConstantSegment((0.25,), 1, "numeric"),),
            )
        )
        row = record.segments[0].timeseries[0]
        with pytest.raises(TypeError):
            cast(Any, row)["t"] = 1.0
        with pytest.raises(TypeError):
            cast(Any, row["qpos"])[0] = 1.0

        serialized = record.as_dict()
        serialized["segments"][0]["timeseries"][0]["qpos"][0] = 1.0
        assert record.segments[0].timeseries[0]["qpos"] == (0.0,)

    asyncio.run(run())


def test_invalid_render_configuration_is_not_a_fallback() -> None:
    with pytest.raises(ValueError):
        RunConfiguration(
            xml_string="<mujoco/>",
            segments=(ConstantSegment((), 1),),
            render=True,
            render_width=0,
        )


def test_cancelled_upstream_call_poisoned_slot_and_closes_child() -> None:
    async def check() -> None:
        transport, make_session, get_session = _fake_client(block_on_run=True)
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        client = PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=make_session,
        )
        async with client:
            slot = await client.load("<mujoco model=\"synthetic\"/>")
            task = asyncio.create_task(client.run_segment(slot, ctrl=[], n_steps=1))
            await get_session().run_started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert slot.state is SlotState.POISONED
            assert client.ready is False
            assert transport.closed is True
            assert get_session().closed is True

    asyncio.run(check())


def test_cancelled_upstream_call_preserves_cancellation_when_cleanup_fails() -> None:
    async def check() -> None:
        transport = _FailingCloseTransport()
        session: _FakeSession | None = None
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        def make_session(read: object, write: object) -> _FakeSession:
            nonlocal session
            session = _FakeSession(read, write, block_on_run=True)
            return session

        client = PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=make_session,
        )
        async with client:
            slot = await client.load("<mujoco model=\"synthetic\"/>")
            task = asyncio.create_task(client.run_segment(slot, ctrl=[], n_steps=1))
            assert session is not None
            await session.run_started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert slot.state is SlotState.POISONED
            assert client.ready is False
            assert session.closed is True

    asyncio.run(check())


def test_timeout_preserves_timeout_code_when_cleanup_fails() -> None:
    async def check() -> None:
        transport = _FailingCloseTransport()
        session: _FakeSession | None = None
        from asset_autopsy.mujoco_client import PinnedMujocoClient, UPSTREAM_TIMEOUT

        def make_session(read: object, write: object) -> _FakeSession:
            nonlocal session
            session = _FakeSession(read, write, timeout_on_reset=True)
            return session

        client = PinnedMujocoClient(
            call_timeout=0.01,
            transport_factory=lambda _parameters: transport,
            session_factory=make_session,
        )
        async with client:
            slot = await client.load("<mujoco model=\"synthetic\"/>")
            with pytest.raises(UpstreamToolError) as caught:
                await client.reset(slot)
            assert caught.value.code == UPSTREAM_TIMEOUT
            assert client.ready is False
            assert session is not None and session.closed is True

    asyncio.run(check())


@pytest.mark.phase0_upstream
def test_real_client_runner_returns_requested_steps() -> None:
    from asset_autopsy.mujoco_client import PinnedMujocoClient

    xml = """<mujoco model="synthetic-run">
      <option timestep="0.002"/>
      <worldbody><geom name="box" type="box" size="0.1 0.1 0.1"/></worldbody>
    </mujoco>"""

    async def run() -> None:
        record = await DeterministicRunner(PinnedMujocoClient(no_render=True)).run(
            RunConfiguration(
                xml_string=xml,
                segments=(ConstantSegment((), 2, "first"), ConstantSegment((), 3, "second")),
            )
        )
        assert record.step_count == 5
        assert [segment.step_count for segment in record.segments] == [2, 3]
        assert record.numeric_only is True

    asyncio.run(run())


def test_step_mismatch_error_is_typed() -> None:
    error = UpstreamToolError(
        UPSTREAM_STEP_MISMATCH,
        "Upstream returned an unexpected step count.",
        False,
        SAFE_SLOT_ACTION,
    )
    assert error.envelope()["code"] == UPSTREAM_STEP_MISMATCH


def test_render_payload_accepts_only_observed_profile_and_bounds_data(monkeypatch: pytest.MonkeyPatch) -> None:
    from asset_autopsy.mujoco_client import _render_png

    png = _png_bytes()
    result = SimpleNamespace(
        isError=False,
        content=[
            SimpleNamespace(
                type="image", mimeType="image/png", data=base64.b64encode(png).decode()
            ),
            SimpleNamespace(type="text", text="synthetic"),
        ],
    )
    assert _render_png(result, width=160, height=120) == png

    for invalid_data in (
        "not-base64",
        base64.b64encode(b"not an image").decode(),
        base64.b64encode(_png_bytes(width=1, height=1)).decode(),
        base64.b64encode(_png_bytes(mode="RGBA")).decode(),
        base64.b64encode(png[:8] + b"malformed").decode(),
    ):
        result.content[0].data = invalid_data
        with pytest.raises(UpstreamToolError) as caught:
            _render_png(result, width=160, height=120)
        assert caught.value.code == UPSTREAM_BAD_RESPONSE

    monkeypatch.setattr("asset_autopsy.mujoco_client.MAX_RENDER_BYTES", len(png) - 1)
    result.content[0].data = base64.b64encode(png).decode()
    with pytest.raises(UpstreamToolError) as caught:
        _render_png(result, width=160, height=120)
    assert caught.value.code == UPSTREAM_BAD_RESPONSE
    assert set(REQUIRED_TOOL_NAMES) == {
        "validate_mjcf",
        "sim_load",
        "sim_reset",
        "sim_set_state",
        "run_and_analyze",
        "model_summary",
        "render_snapshot",
    }
    assert REQUIRED_ENVIRONMENT["MUJOCO_GL"] == "cgl"
    assert SlotState.POISONED.value == "poisoned"


def test_concurrent_context_entry_reuses_one_started_session() -> None:
    async def check() -> None:
        transport, make_session, get_session = _fake_client()
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        session_count = 0

        def counting_session(read: object, write: object) -> _FakeSession:
            nonlocal session_count
            session_count += 1
            return make_session(read, write)

        client = PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=counting_session,
        )
        release = asyncio.Event()
        first_entered = asyncio.Event()
        second_entered = asyncio.Event()

        async def owner(entered: asyncio.Event) -> None:
            await client.__aenter__()
            entered.set()
            await release.wait()
            await client.__aexit__(None, None, None)

        first = asyncio.create_task(owner(first_entered))
        await first_entered.wait()
        second = asyncio.create_task(owner(second_entered))
        await second_entered.wait()
        assert session_count == 1
        assert client._context_owners == 2
        assert client.ready is True

        release.set()
        await asyncio.gather(first, second)
        assert client.ready is False
        assert transport.closed is True
        assert get_session().closed is True

    asyncio.run(check())


def test_concurrent_first_runner_startup_shares_lifecycle_without_early_close() -> None:
    async def check() -> None:
        transport, make_session, _get_session = _fake_client(synchronize_two_runs=True)
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        session_count = 0

        def counting_session(read: object, write: object) -> _FakeSession:
            nonlocal session_count
            session_count += 1
            return make_session(read, write)

        client = PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=counting_session,
        )
        runner = DeterministicRunner(client)
        configuration = RunConfiguration(
            xml_string="<mujoco model=\"synthetic\"/>",
            segments=(ConstantSegment((), 1),),
        )
        first, second = await asyncio.gather(
            runner.run(configuration),
            runner.run(configuration),
        )
        assert first.step_count == second.step_count == 1
        assert session_count == 1
        assert client.ready is False
        assert transport.closed is True

    asyncio.run(check())


def test_cancelled_partial_startup_closes_entered_resources() -> None:
    async def check() -> None:
        transport = _BlockingCloseTransport()
        session: _FakeSession | None = None
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        def make_session(read: object, write: object) -> _FakeSession:
            nonlocal session
            session = _FakeSession(read, write, block_on_initialize=True)
            return session

        client = PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=make_session,
        )
        task = asyncio.create_task(client.__aenter__())
        while session is None:
            await asyncio.sleep(0)
        await session.initialize_started.wait()
        task.cancel()
        await transport.close_started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert task.done() is False
        transport.allow_close.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert client.ready is False
        assert transport.closed is True
        assert session.closed is True

    asyncio.run(check())


def test_startup_cleanup_preserves_caller_cancellation() -> None:
    async def check() -> None:
        transport = _BlockingCloseTransport()
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        class InvalidSchemaSession(_FakeSession):
            async def list_tools(self) -> SimpleNamespace:
                return SimpleNamespace(tools=[])

        client = PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=InvalidSchemaSession,
        )
        task = asyncio.create_task(client.__aenter__())
        await transport.close_started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert task.done() is False
        transport.allow_close.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert transport.closed is True
        assert client.ready is False

    asyncio.run(check())


def test_cancelled_shutdown_finishes_closing_child_before_propagating() -> None:
    async def check() -> None:
        transport = _BlockingCloseTransport()
        session: _FakeSession | None = None
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        def make_session(read: object, write: object) -> _FakeSession:
            nonlocal session
            session = _FakeSession(read, write)
            return session

        client = PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=make_session,
        )
        entered = asyncio.Event()
        begin_exit = asyncio.Event()

        async def owner() -> None:
            async with client:
                entered.set()
                await begin_exit.wait()

        shutdown = asyncio.create_task(owner())
        await entered.wait()
        begin_exit.set()
        await transport.close_started.wait()
        shutdown.cancel()
        await asyncio.sleep(0)
        assert shutdown.done() is False
        shutdown.cancel()
        await asyncio.sleep(0)
        assert shutdown.done() is False
        transport.allow_close.set()
        with pytest.raises(asyncio.CancelledError):
            await shutdown
        assert transport.closed is True
        assert session is not None and session.closed is True
        assert client.ready is False

    asyncio.run(check())


def test_cancelled_shutdown_wins_over_a_late_cleanup_failure() -> None:
    async def check() -> None:
        transport = _BlockingFailingCloseTransport()
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        client = PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=_FakeSession,
        )
        begin_exit = asyncio.Event()

        async def owner() -> None:
            async with client:
                await begin_exit.wait()

        task = asyncio.create_task(owner())
        while not client.ready:
            await asyncio.sleep(0)
        begin_exit.set()
        await transport.close_started.wait()
        task.cancel()
        transport.allow_close.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert client.ready is False

    asyncio.run(check())


def test_old_session_failure_does_not_close_the_restarted_child() -> None:
    async def check() -> None:
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        failure_started = asyncio.Event()
        release_failure = asyncio.Event()
        sessions: list[_FakeSession] = []

        class DelayedFailureSession(_FakeSession):
            async def call_tool(
                self, name: str, *, arguments: dict[str, object]
            ) -> SimpleNamespace:
                if name == "run_and_analyze":
                    failure_started.set()
                    await release_failure.wait()
                    raise RuntimeError("old session failed")
                return await super().call_tool(name, arguments=arguments)

        def make_session(read: object, write: object) -> _FakeSession:
            session_type = DelayedFailureSession if not sessions else _FakeSession
            session = session_type(read, write)
            sessions.append(session)
            return session

        client = PinnedMujocoClient(
            transport_factory=lambda _parameters: _FakeTransport(),
            session_factory=make_session,
        )
        await client.__aenter__()
        slot = await client.load("<mujoco model=\"synthetic\"/>")
        old_call = asyncio.create_task(client.run_segment(slot, ctrl=[], n_steps=1))
        await failure_started.wait()
        await client._shutdown_child()
        await client.__aenter__()
        release_failure.set()
        with pytest.raises(UpstreamToolError) as caught:
            await old_call
        assert caught.value.code == UPSTREAM_UNAVAILABLE
        assert client.ready is True
        assert sessions[1].closed is False
        await client.__aexit__(None, None, None)
        await client.__aexit__(None, None, None)

    asyncio.run(check())


def test_stale_context_exit_does_not_close_restarted_session() -> None:
    async def check() -> None:
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        transports: list[_FakeTransport] = []
        sessions: list[_FakeSession] = []

        def make_transport(_parameters: object) -> _FakeTransport:
            transport = _FakeTransport()
            transports.append(transport)
            return transport

        def make_session(read: object, write: object) -> _FakeSession:
            session = _FakeSession(read, write)
            sessions.append(session)
            return session

        client = PinnedMujocoClient(
            transport_factory=make_transport,
            session_factory=make_session,
        )
        first_entered = asyncio.Event()
        second_entered = asyncio.Event()
        failure_closed = asyncio.Event()
        release_stale_exits = asyncio.Event()

        async def failing_owner() -> None:
            async with client:
                first_entered.set()
                await second_entered.wait()
                await client._shutdown_child()
                failure_closed.set()
                await release_stale_exits.wait()

        async def stale_owner() -> None:
            async with client:
                second_entered.set()
                await release_stale_exits.wait()

        first = asyncio.create_task(failing_owner())
        await first_entered.wait()
        second = asyncio.create_task(stale_owner())
        await failure_closed.wait()

        async with client:
            assert len(transports) == len(sessions) == 2
            assert client.ready is True
            release_stale_exits.set()
            await asyncio.gather(first, second)
            assert client.ready is True
            assert transports[1].closed is False
            assert sessions[1].closed is False

        assert transports[1].closed is True
        assert sessions[1].closed is True

    asyncio.run(check())


def test_foreign_and_restarted_slots_are_rejected() -> None:
    async def check() -> None:
        first_transport, first_factory, _first_session = _fake_client()
        second_transport, second_factory, _second_session = _fake_client()
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        first = PinnedMujocoClient(
            transport_factory=lambda _parameters: first_transport,
            session_factory=first_factory,
        )
        second = PinnedMujocoClient(
            transport_factory=lambda _parameters: second_transport,
            session_factory=second_factory,
        )
        async with first, second:
            slot = await first.load("<mujoco model=\"synthetic\"/>")
            with pytest.raises(UpstreamToolError) as foreign:
                await second.reset(slot)
            assert foreign.value.code == "SLOT_POISONED"
            await first.reset(slot)

        async with first:
            with pytest.raises(UpstreamToolError) as restarted:
                await first.reset(slot)
            assert restarted.value.code == "SLOT_POISONED"

    asyncio.run(check())


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("initial_qpos", (0.0,)),
        ("initial_qvel", (0.0,)),
        ("initial_ctrl", (0.0,)),
    ),
)
def test_runner_rejects_initial_state_widths_before_set_state(
    field_name: str, value: tuple[float, ...]
) -> None:
    async def check() -> None:
        transport, make_session, get_session = _fake_client()
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        client = PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=make_session,
        )
        configuration = RunConfiguration(
            xml_string="<mujoco model=\"synthetic\"/>",
            segments=(ConstantSegment((), 1),),
            **{field_name: value},
        )
        with pytest.raises(ValueError, match="width does not match"):
            await DeterministicRunner(client).run(configuration)
        assert [name for name, _arguments in get_session().calls] == ["sim_load"]

    asyncio.run(check())


@pytest.mark.parametrize(
    ("nq", "nv", "nu", "n_steps"),
    ((100, 100, 0, 10_000), (0, 0, 100, 20_000)),
)
def test_trace_budget_rejects_high_dimensional_run_before_upstream_call(
    nq: int, nv: int, nu: int, n_steps: int
) -> None:
    async def check() -> None:
        transport, make_session, get_session = _fake_client(nq=nq, nv=nv, nu=nu)
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        async with PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=make_session,
        ) as client:
            slot = await client.load("<mujoco model=\"synthetic\"/>")
            with pytest.raises(ValueError, match="bounded numeric record budget"):
                await client.run_segment(slot, ctrl=[0.0] * nu, n_steps=n_steps)
            assert [name for name, _arguments in get_session().calls] == ["sim_load"]

    asyncio.run(check())


@pytest.mark.parametrize(
    ("ctrl", "nu"),
    (([0.0], 0), ([float("nan")], 1), ([10**1000], 1)),
)
def test_run_segment_rejects_invalid_control_before_upstream_call(
    ctrl: list[float], nu: int
) -> None:
    async def check() -> None:
        transport, make_session, get_session = _fake_client(nu=nu)
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        async with PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=make_session,
        ) as client:
            slot = await client.load("<mujoco model=\"synthetic\"/>")
            with pytest.raises(ValueError, match="ctrl must match"):
                await client.run_segment(slot, ctrl=ctrl, n_steps=1)
            assert [name for name, _arguments in get_session().calls] == ["sim_load"]

    asyncio.run(check())


def test_normal_shutdown_surfaces_sanitized_cleanup_failure() -> None:
    async def check() -> None:
        transport = _FailingCloseTransport()
        session: _FakeSession | None = None
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        def make_session(read: object, write: object) -> _FakeSession:
            nonlocal session
            session = _FakeSession(read, write)
            return session

        with pytest.raises(UpstreamToolError) as caught:
            async with PinnedMujocoClient(
                transport_factory=lambda _parameters: transport,
                session_factory=make_session,
            ):
                pass
        assert caught.value.envelope() == {
            "code": UPSTREAM_UNAVAILABLE,
            "message": SAFE_MESSAGE,
            "retryable": True,
            "next_action": SAFE_NEXT_ACTION,
        }
        assert session is not None and session.closed is True

    asyncio.run(check())


@pytest.mark.parametrize(
    ("field_name", "value", "dimensions"),
    (
        ("qpos", [0.0], {}),
        ("qvel", [float("inf")], {"nv": 1}),
        ("ctrl", [0.0], {}),
    ),
)
def test_set_state_rejects_invalid_vectors_before_upstream_call(
    field_name: str, value: list[float], dimensions: dict[str, int]
) -> None:
    async def check() -> None:
        transport, make_session, get_session = _fake_client(**dimensions)
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        async with PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=make_session,
        ) as client:
            slot = await client.load("<mujoco model=\"synthetic\"/>")
            with pytest.raises(ValueError, match="loaded model width"):
                await client.set_state(slot, **{field_name: value})
            assert [name for name, _arguments in get_session().calls] == ["sim_load"]

    asyncio.run(check())


def test_runner_enforces_trace_budget_across_all_segments() -> None:
    async def check() -> None:
        transport, make_session, get_session = _fake_client(nq=100)
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        client = PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=make_session,
        )
        configuration = RunConfiguration(
            xml_string="<mujoco model=\"synthetic\"/>",
            segments=(ConstantSegment((), 10_000), ConstantSegment((), 10_000)),
        )
        with pytest.raises(ValueError, match="bounded numeric record budget"):
            await DeterministicRunner(client).run(configuration)
        assert [name for name, _arguments in get_session().calls] == ["sim_load"]

    asyncio.run(check())


def test_run_response_rejects_negative_contact_counts() -> None:
    async def check() -> None:
        transport, make_session, _get_session = _fake_client(negative_contacts=True)
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        async with PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=make_session,
        ) as client:
            slot = await client.load("<mujoco model=\"synthetic\"/>")
            with pytest.raises(UpstreamToolError) as caught:
                await client.run_segment(slot, ctrl=[], n_steps=1)
            assert caught.value.code == UPSTREAM_BAD_RESPONSE
            assert slot.state is SlotState.POISONED

    asyncio.run(check())


def test_segment_boundary_tolerance_does_not_grow_with_absolute_time() -> None:
    async def check() -> None:
        transport, make_session, _get_session = _fake_client(
            boundary_mode="late", timestep=100.0
        )
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        with pytest.raises(UpstreamToolError) as caught:
            await DeterministicRunner(
                PinnedMujocoClient(
                    transport_factory=lambda _parameters: transport,
                    session_factory=make_session,
                )
            ).run(
                RunConfiguration(
                    xml_string="<mujoco model=\"synthetic\"/>",
                    segments=(ConstantSegment((), 11), ConstantSegment((), 1)),
                )
            )
        assert caught.value.code == UPSTREAM_BAD_RESPONSE

    asyncio.run(check())


def test_shutdown_releases_slot_bookkeeping_without_retaining_xml() -> None:
    async def check() -> None:
        transport, make_session, _get_session = _fake_client()
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        client = PinnedMujocoClient(
            transport_factory=lambda _parameters: transport,
            session_factory=make_session,
        )
        async with client:
            slot = await client.load("<mujoco model=\"synthetic\"/>")
            assert not hasattr(slot, "_xml_string")
            assert client._slots == [slot]
        assert client._slots == []

    asyncio.run(check())
