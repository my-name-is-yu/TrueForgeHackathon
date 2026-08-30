from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.metadata
import json
import math
import os
import sys
import zlib
from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

UPSTREAM_COMMIT = "ce9bed80ec3698d7b778230abc21f2228a3ce94b"
UPSTREAM_PACKAGE = "mujoco-mcp-server"
MAX_STEPS = 100_000
MAX_XML_BYTES = 2_000_000
MAX_RENDER_DIMENSION = 4096
MAX_RENDER_BYTES = 64 * 1024 * 1024

UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
UPSTREAM_BAD_RESPONSE = "UPSTREAM_BAD_RESPONSE"
UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
UPSTREAM_SCHEMA_DRIFT = "UPSTREAM_SCHEMA_DRIFT"
UPSTREAM_STEP_MISMATCH = "UPSTREAM_STEP_MISMATCH"
SLOT_POISONED = "SLOT_POISONED"

SAFE_MESSAGE = "The upstream simulation operation failed."
SAFE_NEXT_ACTION = "Reload the immutable model and retry once."
SAFE_SLOT_ACTION = "Do not reuse the affected simulation slot."
SAFE_TIMEOUT_MESSAGE = "The upstream simulation operation timed out."
SAFE_SCHEMA_MESSAGE = "The pinned upstream contract was not satisfied."

REQUIRED_TOOL_NAMES = (
    "validate_mjcf",
    "sim_load",
    "sim_reset",
    "sim_set_state",
    "run_and_analyze",
    "model_summary",
    "render_snapshot",
)


def _nullable(type_name: str, title: str) -> dict[str, Any]:
    return {
        "anyOf": [{"type": type_name}, {"type": "null"}],
        "default": None,
        "title": title,
    }


def _nullable_array(item_type: str, title: str) -> dict[str, Any]:
    return {
        "anyOf": [
            {"items": {"type": item_type}, "type": "array"},
            {"type": "null"},
        ],
        "default": None,
        "title": title,
    }


def _object_schema(
    title: str,
    properties: dict[str, Any],
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "properties": properties,
        "title": title,
        "type": "object",
    }
    if required:
        schema["required"] = list(required)
    return schema


REQUIRED_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "validate_mjcf": _object_schema(
        "validate_mjcfArguments",
        {
            "xml_path": _nullable("string", "Xml Path"),
            "xml_string": _nullable("string", "Xml String"),
        },
    ),
    "sim_load": _object_schema(
        "sim_loadArguments",
        {
            "name": {"default": "default", "title": "Name", "type": "string"},
            "xml_path": _nullable("string", "Xml Path"),
            "xml_string": _nullable("string", "Xml String"),
        },
    ),
    "sim_reset": _object_schema(
        "sim_resetArguments",
        {"sim_name": _nullable("string", "Sim Name")},
    ),
    "sim_set_state": _object_schema(
        "sim_set_stateArguments",
        {
            "ctrl": _nullable_array("number", "Ctrl"),
            "keyframe": _nullable("integer", "Keyframe"),
            "qpos": _nullable_array("number", "Qpos"),
            "qvel": _nullable_array("number", "Qvel"),
            "sim_name": _nullable("string", "Sim Name"),
        },
    ),
    "run_and_analyze": _object_schema(
        "run_and_analyzeArguments",
        {
            "camera": _nullable("string", "Camera"),
            "capture_every_n": {
                "default": 200,
                "title": "Capture Every N",
                "type": "integer",
            },
            "ctrl": _nullable_array("number", "Ctrl"),
            "n_steps": {"default": 1000, "title": "N Steps", "type": "integer"},
            "sim_name": _nullable("string", "Sim Name"),
            "track": {
                "anyOf": [
                    {"items": {"type": "string"}, "type": "array"},
                    {"type": "null"},
                ],
                "default": None,
                "title": "Track",
            },
        },
    ),
    "model_summary": _object_schema(
        "model_summaryArguments",
        {
            "ctx": {"title": "ctx", "type": "string"},
            "sim_name": _nullable("string", "Sim Name"),
        },
        required=("ctx",),
    ),
    "render_snapshot": _object_schema(
        "render_snapshotArguments",
        {
            "camera": _nullable("string", "Camera"),
            "height": _nullable("integer", "Height"),
            "show_contacts": {
                "default": False,
                "title": "Show Contacts",
                "type": "boolean",
            },
            "sim_name": _nullable("string", "Sim Name"),
            "width": _nullable("integer", "Width"),
        },
    ),
}

REQUIRED_ENVIRONMENT = {
    "MUJOCO_GL": "cgl",
    "MUJOCO_MCP_MAX_WORKERS": "1",
    "MUJOCO_MCP_RENDER_WIDTH": "640",
    "MUJOCO_MCP_RENDER_HEIGHT": "480",
}
_INHERITED_ENVIRONMENT = ("PATH", "HOME", "LANG")


class SlotState(str, Enum):
    PENDING = "pending"
    READY = "ready"
    POISONED = "poisoned"
    CLOSED = "closed"


@dataclass
class UpstreamToolError(Exception):
    code: str
    message: str
    retryable: bool
    next_action: str

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)

    def __str__(self) -> str:
        return self.message

    def envelope(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "next_action": self.next_action,
        }


@dataclass
class SimulationSlot:
    _name: str
    summary: dict[str, Any]
    state: SlotState = SlotState.READY
    _xml_string: str = field(default="", repr=False)

    @property
    def poisoned(self) -> bool:
        return self.state is SlotState.POISONED


def server_parameters(*, no_render: bool = False) -> StdioServerParameters:
    environment = {
        key: os.environ[key]
        for key in _INHERITED_ENVIRONMENT
        if key in os.environ
    }
    environment.update(REQUIRED_ENVIRONMENT)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    if no_render:
        environment["MUJOCO_MCP_NO_RENDER"] = "1"
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "mujoco_mcp", "--transport", "stdio"],
        env=environment,
    )


def verify_pinned_upstream() -> None:
    try:
        distribution = importlib.metadata.distribution(UPSTREAM_PACKAGE)
        direct_url = distribution.read_text("direct_url.json")
        metadata = json.loads(direct_url or "")
    except (
        importlib.metadata.PackageNotFoundError,
        OSError,
        TypeError,
        ValueError,
        AttributeError,
        json.JSONDecodeError,
    ):
        raise UpstreamToolError(
            UPSTREAM_SCHEMA_DRIFT,
            SAFE_SCHEMA_MESSAGE,
            False,
            "Install the pinned upstream dependency.",
        ) from None

    vcs_info = metadata.get("vcs_info") if isinstance(metadata, dict) else None
    commit_id = vcs_info.get("commit_id") if isinstance(vcs_info, dict) else None
    if commit_id != UPSTREAM_COMMIT:
        raise UpstreamToolError(
            UPSTREAM_SCHEMA_DRIFT,
            SAFE_SCHEMA_MESSAGE,
            False,
            "Install the pinned upstream dependency.",
        )


def _bad_response(message: str = "Upstream response was invalid.") -> UpstreamToolError:
    return UpstreamToolError(
        UPSTREAM_BAD_RESPONSE,
        message,
        False,
        SAFE_SLOT_ACTION,
    )


def _text_block(result: Any) -> str:
    blocks = getattr(result, "content", None)
    if not isinstance(blocks, list):
        raise _bad_response("Upstream response content was invalid.")
    if (
        len(blocks) != 1
        or getattr(blocks[0], "type", None) != "text"
        or not isinstance(getattr(blocks[0], "text", None), str)
    ):
        raise _bad_response("Upstream response content was unexpected.")
    return blocks[0].text


async def _close_stack(stack: AsyncExitStack) -> None:
    try:
        await stack.aclose()
    except Exception:
        pass


def _is_error_result(result: Any) -> bool:
    return bool(getattr(result, "isError", False) or getattr(result, "is_error", False))


def _wrapped_error() -> UpstreamToolError:
    return UpstreamToolError(
        UPSTREAM_UNAVAILABLE,
        SAFE_MESSAGE,
        True,
        SAFE_NEXT_ACTION,
    )


def normalize_json_result(
    result: Any,
    validate: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    text = _text_block(result)
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        raise _bad_response("Upstream response was not valid JSON.") from None

    if not isinstance(payload, dict):
        raise _bad_response("Upstream response JSON had an invalid shape.")
    if _is_error_result(result) or "error" in payload:
        raise _wrapped_error()
    try:
        valid = validate(payload)
    except Exception:
        valid = False
    if not valid:
        raise _bad_response("Upstream response JSON did not match the expected schema.")
    return payload


def _strict_float(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(float(value))


def _numeric_tree(value: Any, depth: int = 0) -> bool:
    if depth > 3:
        return False
    if _strict_float(value):
        return True
    return isinstance(value, list) and all(_numeric_tree(item, depth + 1) for item in value)


def _matches_load(payload: dict[str, Any]) -> bool:
    scalar_types = {
        "name": str,
        "mujoco_version": str,
        "nq": int,
        "nv": int,
        "nu": int,
        "nbody": int,
        "ngeom": int,
        "njnt": int,
        "nsite": int,
        "nsensor": int,
        "ncam": int,
        "timestep": float,
        "has_renderer": bool,
    }
    list_fields = {"bodies", "joints", "actuators", "sensors", "cameras"}
    if set(payload) != set(scalar_types) | list_fields:
        return False
    if any(type(payload[name]) is not expected for name, expected in scalar_types.items()):
        return False
    return all(
        type(payload[name]) is list
        and all(type(item) is str for item in payload[name])
        for name in list_fields
    )


def _matches_status(payload: dict[str, Any], status: str) -> bool:
    return set(payload) == {"status", "time"} and payload["status"] == status and _strict_float(payload["time"])


def _numeric_vector(value: Any, width: int) -> bool:
    return type(value) is list and len(value) == width and all(_strict_float(item) for item in value)


def _matches_run(payload: dict[str, Any], *, qpos_width: int, qvel_width: int) -> bool:
    if set(payload) != {"n_steps", "sim_time", "final_state", "timeseries"}:
        return False
    if type(payload["n_steps"]) is not int or payload["n_steps"] < 0:
        return False
    if (
        type(payload["sim_time"]) is not list
        or len(payload["sim_time"]) != 2
        or not all(_strict_float(value) for value in payload["sim_time"])
    ):
        return False
    final_state = payload["final_state"]
    if not isinstance(final_state, dict):
        return False
    if set(final_state) != {"qpos", "qvel", "n_contacts", "energy"}:
        return False
    if (
        not _numeric_vector(final_state["qpos"], qpos_width)
        or not _numeric_vector(final_state["qvel"], qvel_width)
        or type(final_state["energy"]) is not list
        or len(final_state["energy"]) != 2
        or type(final_state["n_contacts"]) is not int
        or not _numeric_tree(final_state["energy"])
    ):
        return False
    timeseries = payload["timeseries"]
    if type(timeseries) is not list or len(timeseries) > MAX_STEPS:
        return False
    return all(
        isinstance(row, dict)
        and {"t", "E_pot", "E_kin", "qpos", "qvel"}.issubset(row)
        and set(row).issubset({"t", "E_pot", "E_kin", "ncon", "qpos", "qvel"})
        and _strict_float(row["t"])
        and _strict_float(row["E_pot"])
        and _strict_float(row["E_kin"])
        and ("ncon" not in row or type(row["ncon"]) is int)
        and _numeric_vector(row["qpos"], qpos_width)
        and _numeric_vector(row["qvel"], qvel_width)
        for row in timeseries
    )


def _valid_png(data: bytes) -> bool:
    signature = b"\x89PNG\r\n\x1a\n"
    if len(data) > MAX_RENDER_BYTES or not data.startswith(signature):
        return False
    offset = len(signature)
    saw_ihdr = False
    saw_idat = False
    saw_iend = False
    while offset < len(data):
        if len(data) - offset < 12:
            return False
        length = int.from_bytes(data[offset : offset + 4], "big")
        chunk_type = data[offset + 4 : offset + 8]
        chunk_start = offset + 8
        chunk_end = chunk_start + length
        chunk_record_end = chunk_end + 4
        if (
            not all(65 <= byte <= 90 or 97 <= byte <= 122 for byte in chunk_type)
            or chunk_record_end > len(data)
        ):
            return False
        chunk_data = data[chunk_start:chunk_end]
        expected_crc = int.from_bytes(data[chunk_end:chunk_record_end], "big")
        actual_crc = zlib.crc32(chunk_type)
        actual_crc = zlib.crc32(chunk_data, actual_crc) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            return False
        if not saw_ihdr:
            if chunk_type != b"IHDR" or length != 13:
                return False
            width = int.from_bytes(chunk_data[0:4], "big")
            height = int.from_bytes(chunk_data[4:8], "big")
            if not 1 <= width <= MAX_RENDER_DIMENSION or not 1 <= height <= MAX_RENDER_DIMENSION:
                return False
            saw_ihdr = True
        elif chunk_type == b"IHDR":
            return False
        if chunk_type == b"IDAT":
            saw_idat = True
        if chunk_type == b"IEND":
            if length != 0 or not saw_idat:
                return False
            saw_iend = True
            offset = chunk_record_end
            break
        offset = chunk_record_end
    return saw_ihdr and saw_idat and saw_iend and offset == len(data)


def _render_png(result: Any) -> bytes:
    if _is_error_result(result):
        raise _wrapped_error()
    blocks = getattr(result, "content", None)
    if not isinstance(blocks, list) or len(blocks) != 2:
        raise _bad_response("Upstream render response was unexpected.")
    image, summary = blocks
    if (
        getattr(image, "type", None) != "image"
        or getattr(image, "mimeType", None) != "image/png"
        or not isinstance(getattr(image, "data", None), str)
        or getattr(summary, "type", None) != "text"
        or not isinstance(getattr(summary, "text", None), str)
    ):
        raise _bad_response("Upstream render response was unexpected.")
    if len(image.data) > ((MAX_RENDER_BYTES + 2) // 3) * 4:
        raise _bad_response("Upstream render response was too large.")
    try:
        data = base64.b64decode(image.data, validate=True)
    except (ValueError, base64.binascii.Error):
        raise _bad_response("Upstream render response was invalid.") from None
    if not _valid_png(data):
        raise _bad_response("Upstream render response was invalid.")
    return data


class PinnedMujocoClient:
    def __init__(
        self,
        *,
        call_timeout: float = 10.0,
        render_timeout: float = 10.0,
        startup_timeout: float = 30.0,
        no_render: bool = False,
        transport_factory: Callable[[StdioServerParameters], Any] = stdio_client,
        session_factory: Callable[[Any, Any], ClientSession] = ClientSession,
    ) -> None:
        if min(call_timeout, render_timeout, startup_timeout) <= 0:
            raise ValueError("timeouts must be positive")
        self.call_timeout = call_timeout
        self.render_timeout = render_timeout
        self.startup_timeout = startup_timeout
        self.no_render = no_render
        self._transport_factory = transport_factory
        self._session_factory = session_factory
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._slots: list[SimulationSlot] = []
        self._generation = 0
        self._shutdown_lock = asyncio.Lock()

    @property
    def ready(self) -> bool:
        return self._session is not None and self._stack is not None

    async def __aenter__(self) -> PinnedMujocoClient:
        if self.ready:
            return self
        verify_pinned_upstream()
        stack = AsyncExitStack()
        try:
            read, write = await asyncio.wait_for(
                stack.enter_async_context(
                    self._transport_factory(server_parameters(no_render=self.no_render))
                ),
                timeout=self.startup_timeout,
            )
            session = await asyncio.wait_for(
                stack.enter_async_context(self._session_factory(read, write)),
                timeout=self.startup_timeout,
            )
            await asyncio.wait_for(session.initialize(), timeout=self.startup_timeout)
            tools = await asyncio.wait_for(session.list_tools(), timeout=self.startup_timeout)
            try:
                actual = {tool.name: tool.inputSchema for tool in tools.tools}
            except Exception:
                raise UpstreamToolError(
                    UPSTREAM_SCHEMA_DRIFT,
                    SAFE_SCHEMA_MESSAGE,
                    False,
                    "Install the pinned upstream dependency.",
                ) from None
            if any(actual.get(name) != REQUIRED_TOOL_SCHEMAS[name] for name in REQUIRED_TOOL_NAMES):
                raise UpstreamToolError(
                    UPSTREAM_SCHEMA_DRIFT,
                    SAFE_SCHEMA_MESSAGE,
                    False,
                    "Install the pinned upstream dependency.",
                )
        except UpstreamToolError:
            await _close_stack(stack)
            raise
        except (TimeoutError, asyncio.TimeoutError):
            await _close_stack(stack)
            raise UpstreamToolError(
                UPSTREAM_TIMEOUT,
                SAFE_TIMEOUT_MESSAGE,
                True,
                SAFE_NEXT_ACTION,
            ) from None
        except Exception:
            await _close_stack(stack)
            raise UpstreamToolError(
                UPSTREAM_UNAVAILABLE,
                SAFE_MESSAGE,
                True,
                SAFE_NEXT_ACTION,
            ) from None
        self._stack = stack
        self._session = session
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self._shutdown_child(poison=False)

    async def _shutdown_child(self, *, poison: bool = True) -> None:
        async with self._shutdown_lock:
            stack, self._stack = self._stack, None
            self._session = None
            for slot in self._slots:
                if poison and slot.state is not SlotState.CLOSED:
                    slot.state = SlotState.POISONED
                elif not poison and slot.state is SlotState.READY:
                    slot.state = SlotState.CLOSED
            if stack is not None:
                await _close_stack(stack)

    def _new_slot(self, xml_string: str) -> SimulationSlot:
        self._generation += 1
        digest = hashlib.sha256(xml_string.encode("utf-8")).hexdigest()[:16]
        slot = SimulationSlot(
            _name=f"aa_{digest}_{self._generation:04d}",
            summary={},
            state=SlotState.PENDING,
            _xml_string=xml_string,
        )
        self._slots.append(slot)
        return slot

    def _require_ready_slot(self, slot: SimulationSlot) -> None:
        if slot.state is not SlotState.READY:
            raise UpstreamToolError(
                SLOT_POISONED,
                SAFE_MESSAGE,
                True,
                SAFE_SLOT_ACTION,
            )
        if not self.ready:
            raise UpstreamToolError(
                UPSTREAM_UNAVAILABLE,
                SAFE_MESSAGE,
                True,
                SAFE_NEXT_ACTION,
            )

    async def _invoke(
        self,
        slot: SimulationSlot | None,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> Any:
        if tool_name not in REQUIRED_TOOL_NAMES:
            raise ValueError("upstream operation is not permitted")
        if slot is not None and tool_name != "sim_load":
            self._require_ready_slot(slot)
        session = self._session
        if session is None:
            if slot is not None:
                slot.state = SlotState.POISONED
            raise UpstreamToolError(
                UPSTREAM_UNAVAILABLE,
                SAFE_MESSAGE,
                True,
                SAFE_NEXT_ACTION,
            )
        try:
            return await asyncio.wait_for(
                session.call_tool(tool_name, arguments=arguments),
                timeout=timeout or self.call_timeout,
            )
        except asyncio.CancelledError:
            if slot is not None:
                slot.state = SlotState.POISONED
            await self._shutdown_child()
            raise
        except (TimeoutError, asyncio.TimeoutError):
            if slot is not None:
                slot.state = SlotState.POISONED
            await self._shutdown_child()
            raise UpstreamToolError(
                UPSTREAM_TIMEOUT,
                SAFE_TIMEOUT_MESSAGE,
                True,
                SAFE_NEXT_ACTION,
            ) from None
        except Exception:
            if slot is not None:
                slot.state = SlotState.POISONED
            await self._shutdown_child()
            raise UpstreamToolError(
                UPSTREAM_UNAVAILABLE,
                SAFE_MESSAGE,
                True,
                SAFE_NEXT_ACTION,
            ) from None

    async def load(self, xml_string: str) -> SimulationSlot:
        if not isinstance(xml_string, str) or not xml_string or len(xml_string.encode("utf-8")) > MAX_XML_BYTES:
            raise ValueError("xml_string is invalid")
        slot = self._new_slot(xml_string)
        try:
            result = await self._invoke(
                slot,
                "sim_load",
                {"name": slot._name, "xml_string": xml_string},
            )
            payload = normalize_json_result(result, _matches_load)
            slot.summary = {key: value for key, value in payload.items() if key != "name"}
        except UpstreamToolError:
            slot.state = SlotState.POISONED
            raise
        slot.state = SlotState.READY
        return slot

    async def reset(self, slot: SimulationSlot) -> None:
        result = await self._invoke(slot, "sim_reset", {"sim_name": slot._name})
        try:
            normalize_json_result(result, lambda payload: _matches_status(payload, "reset"))
        except UpstreamToolError:
            slot.state = SlotState.POISONED
            raise

    async def set_state(
        self,
        slot: SimulationSlot,
        *,
        qpos: list[float] | None = None,
        qvel: list[float] | None = None,
        ctrl: list[float] | None = None,
    ) -> None:
        arguments: dict[str, Any] = {"sim_name": slot._name}
        if qpos is not None:
            arguments["qpos"] = list(qpos)
        if qvel is not None:
            arguments["qvel"] = list(qvel)
        if ctrl is not None:
            arguments["ctrl"] = list(ctrl)
        result = await self._invoke(slot, "sim_set_state", arguments)
        try:
            normalize_json_result(result, lambda payload: _matches_status(payload, "ok"))
        except UpstreamToolError:
            slot.state = SlotState.POISONED
            raise

    async def run_segment(
        self,
        slot: SimulationSlot,
        *,
        ctrl: list[float],
        n_steps: int,
    ) -> dict[str, Any]:
        if type(n_steps) is not int or not 1 <= n_steps <= MAX_STEPS:
            raise ValueError(f"n_steps must be between 1 and {MAX_STEPS}")
        result = await self._invoke(
            slot,
            "run_and_analyze",
            {
                "sim_name": slot._name,
                "ctrl": list(ctrl),
                "n_steps": n_steps,
                "capture_every_n": 0,
                "track": ["qpos", "qvel", "energy"],
            },
        )
        try:
            payload = normalize_json_result(
                result,
                lambda value: _matches_run(
                    value,
                    qpos_width=slot.summary["nq"],
                    qvel_width=slot.summary["nv"],
                ),
            )
        except UpstreamToolError:
            slot.state = SlotState.POISONED
            raise
        if payload["n_steps"] != n_steps or len(payload["timeseries"]) != n_steps:
            slot.state = SlotState.POISONED
            raise UpstreamToolError(
                UPSTREAM_STEP_MISMATCH,
                "Upstream returned an unexpected step count.",
                False,
                SAFE_SLOT_ACTION,
            )
        return payload

    async def render(
        self,
        slot: SimulationSlot,
        *,
        width: int = 160,
        height: int = 120,
    ) -> bytes:
        if (
            type(width) is not int
            or type(height) is not int
            or not 1 <= width <= MAX_RENDER_DIMENSION
            or not 1 <= height <= MAX_RENDER_DIMENSION
        ):
            raise ValueError("render dimensions are invalid")
        result = await self._invoke(
            slot,
            "render_snapshot",
            {
                "sim_name": slot._name,
                "width": width,
                "height": height,
            },
            timeout=self.render_timeout,
        )
        try:
            return _render_png(result)
        except UpstreamToolError:
            slot.state = SlotState.POISONED
            raise


__all__ = [
    "MAX_STEPS",
    "PinnedMujocoClient",
    "REQUIRED_ENVIRONMENT",
    "REQUIRED_TOOL_NAMES",
    "REQUIRED_TOOL_SCHEMAS",
    "SAFE_MESSAGE",
    "SAFE_NEXT_ACTION",
    "SAFE_SLOT_ACTION",
    "SimulationSlot",
    "SlotState",
    "UPSTREAM_BAD_RESPONSE",
    "UPSTREAM_COMMIT",
    "UPSTREAM_SCHEMA_DRIFT",
    "UPSTREAM_STEP_MISMATCH",
    "UPSTREAM_TIMEOUT",
    "UPSTREAM_UNAVAILABLE",
    "UpstreamToolError",
    "normalize_json_result",
    "server_parameters",
    "verify_pinned_upstream",
]
