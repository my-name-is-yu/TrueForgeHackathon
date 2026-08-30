from __future__ import annotations

import asyncio
import base64
from io import BytesIO
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
from asset_autopsy.runner import ConstantSegment, DeterministicRunner, RunConfiguration


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
        render_is_error: bool = False,
        invalid_render_png: bool = False,
        timestamp_mode: str | None = None,
        wrong_load_name: bool = False,
        invalid_load_metadata: str | None = None,
        nq: int = 0,
        nv: int = 0,
        nu: int = 0,
    ) -> None:
        self.closed = False
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.timeout_on_reset = timeout_on_reset
        self.incomplete_run = incomplete_run
        self.wrong_width_run = wrong_width_run
        self.block_on_run = block_on_run
        self.render_is_error = render_is_error
        self.invalid_render_png = invalid_render_png
        self.timestamp_mode = timestamp_mode
        self.wrong_load_name = wrong_load_name
        self.invalid_load_metadata = invalid_load_metadata
        self.nq = nq
        self.nv = nv
        self.nu = nu
        self.run_started = asyncio.Event()

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.closed = True

    async def initialize(self) -> None:
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
            timestep = 0.002
            if self.invalid_load_metadata == "negative_dimension":
                nu = -1
            elif self.invalid_load_metadata == "invalid_timestep":
                timestep = 0.0
            return _text_result(
                {
                    "name": "unexpected" if self.wrong_load_name else arguments["name"],
                    "mujoco_version": "3.5.0",
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
            return _text_result({"status": "reset", "time": 0.0})
        if name == "sim_set_state":
            return _text_result({"status": "ok", "time": 0.0})
        if name == "run_and_analyze":
            steps = arguments["n_steps"]
            if self.block_on_run:
                self.run_started.set()
                await asyncio.Event().wait()
            qpos = [0.0] if self.wrong_width_run else [0.0] * self.nq
            qvel = [0.0] if self.wrong_width_run else [0.0] * self.nv
            timestamps = [0.002 * (index + 1) for index in range(steps)]
            if self.timestamp_mode == "duplicate" and len(timestamps) >= 2:
                timestamps[1] = timestamps[0]
            elif self.timestamp_mode == "reversed":
                timestamps.reverse()
            elif self.timestamp_mode == "wrong_interval" and len(timestamps) >= 3:
                timestamps[2] = timestamps[1] + 0.003
            rows = []
            for timestamp in timestamps:
                row = {"t": timestamp, "E_pot": 0.0, "E_kin": 0.0, "ncon": 0}
                if not self.incomplete_run:
                    row.update({"qpos": qpos, "qvel": qvel})
                rows.append(row)
            sim_time = [timestamps[0], timestamps[-1]]
            if self.timestamp_mode == "inconsistent":
                sim_time[1] += 0.002
            return _text_result(
                {
                    "n_steps": steps,
                    "sim_time": sim_time,
                    "final_state": {"qpos": qpos, "qvel": qvel, "n_contacts": 0, "energy": [0.0, 0.0]},
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
    render_is_error: bool = False,
    invalid_render_png: bool = False,
    timestamp_mode: str | None = None,
    wrong_load_name: bool = False,
    invalid_load_metadata: str | None = None,
    nq: int = 0,
    nv: int = 0,
    nu: int = 0,
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
            render_is_error=render_is_error,
            invalid_render_png=invalid_render_png,
            timestamp_mode=timestamp_mode,
            wrong_load_name=wrong_load_name,
            invalid_load_metadata=invalid_load_metadata,
            nq=nq,
            nv=nv,
            nu=nu,
        )
        return session

    return transport, make_session, lambda: session


def test_child_environment_is_allowlisted_and_pinned() -> None:
    parameters = server_parameters()
    assert parameters.args == ["-m", "mujoco_mcp", "--transport", "stdio"]
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


def test_run_response_requires_requested_signals_and_model_widths() -> None:
    async def check() -> None:
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        for incomplete_run, wrong_width_run in ((True, False), (False, True)):
            transport, make_session, get_session = _fake_client(
                incomplete_run=incomplete_run,
                wrong_width_run=wrong_width_run,
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


@pytest.mark.parametrize("invalid_load_metadata", ("negative_dimension", "invalid_timestep"))
def test_load_rejects_impossible_dimensions_and_timestep(invalid_load_metadata: str) -> None:
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
            assert client._slots[0].state is SlotState.POISONED

    asyncio.run(check())


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
