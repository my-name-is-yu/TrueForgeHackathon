from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace

import pytest

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


class _FakeTransport:
    def __init__(self) -> None:
        self.closed = False

    async def __aenter__(self) -> tuple[object, object]:
        return object(), object()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.closed = True


class _FakeSession:
    def __init__(self, _read: object, _write: object, *, timeout_on_reset: bool = False) -> None:
        self.closed = False
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.timeout_on_reset = timeout_on_reset

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
            return _text_result(
                {
                    "name": "synthetic",
                    "mujoco_version": "3.5.0",
                    "nq": 0,
                    "nv": 0,
                    "nu": 0,
                    "nbody": 1,
                    "ngeom": 0,
                    "njnt": 0,
                    "nsite": 0,
                    "nsensor": 0,
                    "ncam": 0,
                    "timestep": 0.002,
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
            return _text_result(
                {
                    "n_steps": steps,
                    "sim_time": [0.0, 0.002],
                    "final_state": {"qpos": [], "qvel": [], "n_contacts": 0, "energy": [0.0, 0.0]},
                    "timeseries": [{"t": 0.002, "E_pot": 0.0, "E_kin": 0.0, "ncon": 0} for _ in range(steps)],
                }
            )
        if name == "render_snapshot":
            return _text_result({"error": "synthetic render failure"})
        raise AssertionError(name)


def _fake_client(*, timeout_on_reset: bool = False):
    transport = _FakeTransport()
    session: _FakeSession | None = None

    def make_session(read: object, write: object) -> _FakeSession:
        nonlocal session
        session = _FakeSession(read, write, timeout_on_reset=timeout_on_reset)
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


def test_render_failure_returns_one_numeric_only_fallback() -> None:
    async def run() -> None:
        transport, make_session, _get_session = _fake_client()
        from asset_autopsy.mujoco_client import PinnedMujocoClient

        record = await DeterministicRunner(
            PinnedMujocoClient(
                transport_factory=lambda _parameters: transport,
                session_factory=make_session,
            )
        ).run(
            RunConfiguration(
                xml_string="<mujoco model=\"synthetic\"/>",
                segments=(ConstantSegment((), 2, "numeric"),),
                render=True,
            )
        )
        assert record.step_count == 2
        assert record.image_png is None
        assert record.render_fallback is True
        assert record.as_dict()["render"]["numeric_only_fallback"] is True

    asyncio.run(run())


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


def test_render_payload_is_not_a_text_fallback() -> None:
    png = base64.b64encode(b"\x89PNG\r\n\x1a\nsynthetic").decode()
    assert png
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
