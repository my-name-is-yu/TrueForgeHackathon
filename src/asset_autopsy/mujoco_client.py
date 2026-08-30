from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.metadata
from io import BytesIO
import json
import math
import os
import sys
import sysconfig
from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image

UPSTREAM_COMMIT = "ce9bed80ec3698d7b778230abc21f2228a3ce94b"
UPSTREAM_PACKAGE = "mujoco-mcp-server"
REQUIRED_MUJOCO_VERSION = "3.5.0"
MAX_STEPS = 100_000
MAX_XML_BYTES = 2_000_000
MAX_RENDER_DIMENSION = 4096
MAX_RENDER_BYTES = 64 * 1024 * 1024
MAX_TRACE_SCALARS = 2_000_000
_MAX_JSON_INTEGER_DIGITS = 19

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
    _session_token: object | None = field(default=None, repr=False)
    _time: float = field(default=0.0, repr=False)
    _operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @property
    def poisoned(self) -> bool:
        return self.state is SlotState.POISONED


def server_parameters(*, no_render: bool = False) -> StdioServerParameters:
    environment = {
        key: os.environ[key] for key in _INHERITED_ENVIRONMENT if key in os.environ
    }
    environment.update(REQUIRED_ENVIRONMENT)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    if no_render:
        environment["MUJOCO_MCP_NO_RENDER"] = "1"
    return StdioServerParameters(
        command=sys.executable,
        args=["-s", "-P", "-m", "mujoco_mcp", "--transport", "stdio"],
        env=environment,
    )


def verify_pinned_upstream() -> None:
    try:
        site_paths = {
            path
            for name in ("purelib", "platlib")
            if (path := sysconfig.get_path(name)) is not None
        }
        matches = [
            distribution
            for path in site_paths
            for distribution in importlib.metadata.distributions(path=[path])
            if distribution.metadata.get("Name", "").lower().replace("_", "-")
            == UPSTREAM_PACKAGE
        ]
        if len(matches) != 1:
            raise ValueError("pinned distribution metadata was ambiguous")
        distribution = matches[0]
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


async def _finish_resource_task(task: asyncio.Task[None]) -> None:
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    failure = None if task.cancelled() else task.exception()
    if cancelled:
        raise asyncio.CancelledError
    if failure is not None:
        raise failure


async def _discard_resource_task_failure(task: asyncio.Task[None]) -> None:
    try:
        await _finish_resource_task(task)
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
    *,
    max_text_chars: int,
) -> dict[str, Any]:
    if _is_error_result(result):
        raise _wrapped_error()
    text = _text_block(result)
    if len(text) > max_text_chars:
        raise _bad_response("Upstream response was too large.")
    if not _utf8_text(text):
        raise _bad_response("Upstream response text was invalid.")
    try:
        payload = json.loads(text, parse_int=_parse_json_integer)
    except (TypeError, ValueError, RecursionError):
        raise _bad_response("Upstream response was not valid JSON.") from None

    if not _utf8_json_text(payload):
        raise _bad_response("Upstream response text was invalid.")
    if not isinstance(payload, dict):
        raise _bad_response("Upstream response JSON had an invalid shape.")
    if "error" in payload:
        raise _wrapped_error()
    try:
        valid = validate(payload)
    except Exception:
        valid = False
    if not valid:
        raise _bad_response("Upstream response JSON did not match the expected schema.")
    return payload


def _parse_json_integer(token: str) -> int:
    digit_count = len(token) - (1 if token.startswith("-") else 0)
    if digit_count > _MAX_JSON_INTEGER_DIGITS:
        raise ValueError("JSON integer exceeds the supported range")
    return int(token)


def _utf8_text(value: str) -> bool:
    return not any("\ud800" <= character <= "\udfff" for character in value)


def _utf8_json_text(value: Any) -> bool:
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if not _utf8_text(item):
                return False
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, dict):
            pending.extend(item)
            pending.extend(item.values())
    return True


def _validated_xml_bytes(xml_string: object) -> bytes:
    if (
        not isinstance(xml_string, str)
        or not xml_string
        or len(xml_string) > MAX_XML_BYTES
    ):
        raise ValueError("xml_string is invalid")
    try:
        xml_bytes = xml_string.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("xml_string is invalid") from None
    if len(xml_bytes) > MAX_XML_BYTES:
        raise ValueError("xml_string is invalid")
    return xml_bytes


def _strict_float(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def _state_number(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        float(value)
    except OverflowError:
        return False
    return True


def _numeric_tree(value: Any, depth: int = 0) -> bool:
    if depth > 3:
        return False
    if _strict_float(value):
        return True
    return isinstance(value, list) and all(
        _numeric_tree(item, depth + 1) for item in value
    )


def _matches_load(payload: dict[str, Any], *, expected_name: str | None = None) -> bool:
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
    if any(
        type(payload[name]) is not expected for name, expected in scalar_types.items()
    ):
        return False
    if expected_name is not None and payload["name"] != expected_name:
        return False
    if payload["mujoco_version"] != REQUIRED_MUJOCO_VERSION:
        return False
    if any(
        payload[name] < 0
        for name in (
            "nq",
            "nv",
            "nu",
            "nbody",
            "ngeom",
            "njnt",
            "nsite",
            "nsensor",
            "ncam",
        )
    ):
        return False
    if not _strict_float(payload["timestep"]) or payload["timestep"] <= 0:
        return False
    return all(
        type(payload[name]) is list and all(type(item) is str for item in payload[name])
        for name in list_fields
    )


def _matches_status(payload: dict[str, Any], status: str) -> bool:
    return (
        set(payload) == {"status", "time"}
        and payload["status"] == status
        and _strict_float(payload["time"])
    )


def _matches_validation(payload: dict[str, Any]) -> bool:
    if set(payload) != {"valid", "errors", "warnings"}:
        return False
    if type(payload["valid"]) is not bool:
        return False
    for field in ("errors", "warnings"):
        diagnostics = payload[field]
        if type(diagnostics) is not list or len(diagnostics) > 256:
            return False
        if not all(
            isinstance(diagnostic, dict)
            and set(diagnostic) == {"rule", "element", "message"}
            and all(
                isinstance(diagnostic[name], str)
                for name in ("rule", "element", "message")
            )
            for diagnostic in diagnostics
        ):
            return False
    return payload["valid"] == (not payload["errors"])


def _numeric_vector(value: Any, width: int) -> bool:
    return (
        type(value) is list
        and len(value) == width
        and all(_strict_float(item) for item in value)
    )


def _state_vector(value: Any, width: int) -> bool:
    return (
        type(value) is list
        and len(value) == width
        and all(_state_number(item) for item in value)
    )


def _state_equal(left: Any, right: Any) -> bool:
    if _state_number(left) and _state_number(right):
        left_number = float(left)
        right_number = float(right)
        return left_number == right_number or (
            math.isnan(left_number) and math.isnan(right_number)
        )
    return (
        type(left) is list
        and type(right) is list
        and len(left) == len(right)
        and all(_state_equal(a, b) for a, b in zip(left, right, strict=True))
    )


def _validate_track(track: tuple[str, ...], bodies: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    for selection in track:
        if type(selection) is not str or selection in seen:
            raise ValueError("track selections must be unique strings")
        if selection == "contact_count":
            pass
        elif selection.startswith("body_xpos:"):
            body_name = selection.partition(":")[2]
            if not body_name or body_name not in bodies:
                raise ValueError("body_xpos must name a body in the loaded model")
        else:
            raise ValueError("unsupported track selection")
        seen.add(selection)
    return track


def _trace_scalars_per_step(
    *, nq: int, nv: int, nu: int, track: tuple[str, ...]
) -> int:
    return (
        nq
        + nv
        + nu
        + 3
        + (1 if "contact_count" in track else 0)
        + 3 * sum(selection.startswith("body_xpos:") for selection in track)
    )


def _run_record_scalar_count(
    *,
    segments: tuple[tuple[int, int], ...],
    nq: int,
    nv: int,
    track: tuple[str, ...],
) -> int:
    returned_width = _trace_scalars_per_step(nq=nq, nv=nv, nu=0, track=track)
    return sum(
        n_steps * (returned_width + ctrl_width) + ctrl_width
        for n_steps, ctrl_width in segments
    )


def _matches_run(
    payload: dict[str, Any],
    *,
    qpos_width: int,
    qvel_width: int,
    timestep: float,
    expected_start: float,
    track: tuple[str, ...],
) -> bool:
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
        not _state_vector(final_state["qpos"], qpos_width)
        or not _state_vector(final_state["qvel"], qvel_width)
        or type(final_state["energy"]) is not list
        or len(final_state["energy"]) != 2
        or type(final_state["n_contacts"]) is not int
        or final_state["n_contacts"] < 0
        or not all(_state_number(value) for value in final_state["energy"])
    ):
        return False
    timeseries = payload["timeseries"]
    if type(timeseries) is not list or len(timeseries) > MAX_STEPS:
        return False
    body_fields = {
        selection for selection in track if selection.startswith("body_xpos:")
    }
    row_fields = {"t", "E_pot", "E_kin", "qpos", "qvel"} | body_fields
    if "contact_count" in track:
        row_fields.add("ncon")
    if not all(
        isinstance(row, dict)
        and set(row) == row_fields
        and _strict_float(row["t"])
        and _state_number(row["E_pot"])
        and _state_number(row["E_kin"])
        and ("ncon" not in row or (type(row["ncon"]) is int and row["ncon"] >= 0))
        and _state_vector(row["qpos"], qpos_width)
        and _state_vector(row["qvel"], qvel_width)
        and all(_state_vector(row[field], 3) for field in body_fields)
        for row in timeseries
    ):
        return False
    if not timeseries:
        return False
    last_row = timeseries[-1]
    if (
        not _state_equal(final_state["qpos"], last_row["qpos"])
        or not _state_equal(final_state["qvel"], last_row["qvel"])
        or not _state_equal(final_state["energy"], [last_row["E_pot"], last_row["E_kin"]])
        or ("ncon" in last_row and final_state["n_contacts"] != last_row["ncon"])
    ):
        return False
    timestamps = [row["t"] for row in timeseries]
    if any(
        current <= previous for previous, current in zip(timestamps, timestamps[1:])
    ):
        return False
    sim_start, sim_end = payload["sim_time"]
    interval_tolerance = timestep * 1e-6
    return bool(
        timestamps
        and math.isclose(
            timestamps[0], expected_start, rel_tol=0.0, abs_tol=interval_tolerance
        )
        and math.isclose(
            timestamps[0], sim_start, rel_tol=0.0, abs_tol=interval_tolerance
        )
        and math.isclose(
            timestamps[-1], sim_end, rel_tol=0.0, abs_tol=interval_tolerance
        )
        and all(
            math.isclose(
                current - previous,
                timestep,
                rel_tol=0.0,
                abs_tol=interval_tolerance,
            )
            for previous, current in zip(timestamps, timestamps[1:])
        )
    )


def _render_png(result: Any, *, width: int, height: int) -> bytes:
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
        or len(summary.text) > 256
        or not _utf8_text(summary.text)
    ):
        raise _bad_response("Upstream render response was unexpected.")
    if len(image.data) > ((MAX_RENDER_BYTES + 2) // 3) * 4:
        raise _bad_response("Upstream render response was too large.")
    try:
        data = base64.b64decode(image.data, validate=True)
    except (ValueError, base64.binascii.Error):
        raise _bad_response("Upstream render response was invalid.") from None
    if len(data) > MAX_RENDER_BYTES:
        raise _bad_response("Upstream render response was invalid.")
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise _bad_response("Upstream render response was invalid.")
    offset = 8
    while True:
        if offset + 12 > len(data):
            raise _bad_response("Upstream render response was invalid.")
        chunk_size = int.from_bytes(data[offset : offset + 4], "big")
        chunk_type = data[offset + 4 : offset + 8]
        offset += 12 + chunk_size
        if offset > len(data):
            raise _bad_response("Upstream render response was invalid.")
        if chunk_type == b"IEND":
            if chunk_size != 0 or offset != len(data):
                raise _bad_response("Upstream render response was invalid.")
            break
    try:
        with Image.open(BytesIO(data)) as decoded:
            if (
                decoded.format != "PNG"
                or decoded.mode != "RGB"
                or decoded.size != (width, height)
            ):
                raise _bad_response("Upstream render response was invalid.")
            if width * height * 3 > MAX_RENDER_BYTES:
                raise _bad_response("Upstream render response was too large.")
            decoded.load()
            clean = BytesIO()
            decoded.save(clean, format="PNG")
    except UpstreamToolError:
        raise
    except Exception:
        raise _bad_response("Upstream render response was invalid.") from None
    clean_data = clean.getvalue()
    if len(clean_data) > MAX_RENDER_BYTES:
        raise _bad_response("Upstream render response was too large.")
    return clean_data


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
        timeouts = (call_timeout, render_timeout, startup_timeout)
        if not all(
            type(timeout) in (int, float)
            and math.isfinite(float(timeout))
            and timeout > 0
            for timeout in timeouts
        ):
            raise ValueError("timeouts must be finite and positive")
        self.call_timeout = call_timeout
        self.render_timeout = render_timeout
        self.startup_timeout = startup_timeout
        self.no_render = no_render
        self._transport_factory = transport_factory
        self._session_factory = session_factory
        self._resource_task: asyncio.Task[None] | None = None
        self._stop_resources: asyncio.Event | None = None
        self._session: ClientSession | None = None
        self._session_token: object | None = None
        self._slots: list[SimulationSlot] = []
        self._generation = 0
        self._lifecycle_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._context_owners = 0
        self._context_tokens: dict[asyncio.Task[Any], list[object]] = {}

    @property
    def ready(self) -> bool:
        return self._session is not None and self._resource_task is not None

    async def _own_resources(
        self,
        started: asyncio.Future[tuple[ClientSession, Any]],
        stop: asyncio.Event,
    ) -> None:
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
            tools = await asyncio.wait_for(
                session.list_tools(), timeout=self.startup_timeout
            )
            started.set_result((session, tools))
            await stop.wait()
        except asyncio.CancelledError:
            if not started.done():
                started.cancel()
            raise
        except BaseException as exc:
            if not started.done():
                started.set_exception(exc)
        finally:
            try:
                async with asyncio.timeout(self.startup_timeout):
                    await stack.aclose()
            except asyncio.CancelledError:
                raise
            except Exception:
                raise UpstreamToolError(
                    UPSTREAM_UNAVAILABLE,
                    SAFE_MESSAGE,
                    True,
                    SAFE_NEXT_ACTION,
                ) from None

    async def __aenter__(self) -> PinnedMujocoClient:
        async with self._lifecycle_lock:
            if self.ready:
                self._record_context_owner()
                return self
            verify_pinned_upstream()
            started: asyncio.Future[tuple[ClientSession, Any]] = (
                asyncio.get_running_loop().create_future()
            )
            stop = asyncio.Event()
            resource_task = asyncio.create_task(self._own_resources(started, stop))
            try:
                session, tools = await started
                try:
                    actual = {tool.name: tool.inputSchema for tool in tools.tools}
                except Exception:
                    raise UpstreamToolError(
                        UPSTREAM_SCHEMA_DRIFT,
                        SAFE_SCHEMA_MESSAGE,
                        False,
                        "Install the pinned upstream dependency.",
                    ) from None
                if any(
                    actual.get(name) != REQUIRED_TOOL_SCHEMAS[name]
                    for name in REQUIRED_TOOL_NAMES
                ):
                    raise UpstreamToolError(
                        UPSTREAM_SCHEMA_DRIFT,
                        SAFE_SCHEMA_MESSAGE,
                        False,
                        "Install the pinned upstream dependency.",
                    )
            except asyncio.CancelledError:
                resource_task.cancel()
                await _discard_resource_task_failure(resource_task)
                raise
            except UpstreamToolError:
                stop.set()
                await _discard_resource_task_failure(resource_task)
                raise
            except (TimeoutError, asyncio.TimeoutError):
                stop.set()
                await _discard_resource_task_failure(resource_task)
                raise UpstreamToolError(
                    UPSTREAM_TIMEOUT,
                    SAFE_TIMEOUT_MESSAGE,
                    True,
                    SAFE_NEXT_ACTION,
                ) from None
            except Exception:
                stop.set()
                await _discard_resource_task_failure(resource_task)
                raise UpstreamToolError(
                    UPSTREAM_UNAVAILABLE,
                    SAFE_MESSAGE,
                    True,
                    SAFE_NEXT_ACTION,
                ) from None
            self._resource_task = resource_task
            self._stop_resources = stop
            self._session = session
            self._session_token = object()
            self._record_context_owner()
            return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        owner = asyncio.current_task()
        if owner is None:
            return
        cleanup = asyncio.create_task(self._release_context(owner, exc))
        await _finish_resource_task(cleanup)

    async def _release_context(
        self, owner: asyncio.Task[Any], primary_error: Any
    ) -> None:
        async with self._lifecycle_lock:
            tokens = self._context_tokens.get(owner)
            if not tokens:
                return
            token = tokens.pop()
            if not tokens:
                del self._context_tokens[owner]
            if token is not self._session_token:
                return
            self._context_owners -= 1
            if self._context_owners == 0:
                try:
                    await self._shutdown_child_locked(poison=False)
                except UpstreamToolError:
                    if primary_error is None:
                        raise

    def _record_context_owner(self) -> None:
        task = asyncio.current_task()
        if task is None or self._session_token is None:
            raise RuntimeError("client context requires an active asyncio task")
        self._context_tokens.setdefault(task, []).append(self._session_token)
        self._context_owners += 1

    async def _shutdown_child(self, *, poison: bool = True) -> None:
        cleanup = asyncio.create_task(
            self._shutdown_session(self._session_token, poison=poison)
        )
        await _finish_resource_task(cleanup)

    async def _shutdown_child_preserving_primary_error(
        self, session_token: object | None
    ) -> None:
        cleanup = asyncio.create_task(
            self._shutdown_session(session_token, poison=True)
        )
        try:
            await _finish_resource_task(cleanup)
        except UpstreamToolError:
            pass

    async def _shutdown_session(
        self, session_token: object | None, *, poison: bool
    ) -> None:
        async with self._lifecycle_lock:
            if session_token is not self._session_token:
                return
            self._context_owners = 0
            await self._shutdown_child_locked(poison=poison)

    async def _shutdown_child_locked(self, *, poison: bool) -> None:
        resource_task, self._resource_task = self._resource_task, None
        stop, self._stop_resources = self._stop_resources, None
        self._session = None
        self._session_token = None
        slots, self._slots = self._slots, []
        for slot in slots:
            if poison and slot.state is not SlotState.CLOSED:
                slot.state = SlotState.POISONED
            elif not poison and slot.state is not SlotState.CLOSED:
                slot.state = SlotState.CLOSED
        if stop is not None:
            stop.set()
        if resource_task is not None:
            await _finish_resource_task(resource_task)

    def _new_slot(self, xml_bytes: bytes) -> SimulationSlot:
        if self._session_token is None:
            raise UpstreamToolError(
                UPSTREAM_UNAVAILABLE,
                SAFE_MESSAGE,
                True,
                SAFE_NEXT_ACTION,
            )
        self._generation += 1
        digest = hashlib.sha256(xml_bytes).hexdigest()[:16]
        slot = SimulationSlot(
            _name=f"aa_{digest}_{self._generation:04d}",
            summary={},
            state=SlotState.PENDING,
            _session_token=self._session_token,
        )
        self._slots.append(slot)
        return slot

    def _require_ready_slot(self, slot: SimulationSlot) -> None:
        if (
            slot.state is not SlotState.READY
            or self._session_token is None
            or slot._session_token is not self._session_token
        ):
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
        async with self._operation_lock:
            if slot is not None:
                if tool_name == "sim_load":
                    if (
                        slot.state is not SlotState.PENDING
                        or self._session_token is None
                        or slot._session_token is not self._session_token
                        or slot not in self._slots
                    ):
                        raise UpstreamToolError(
                            SLOT_POISONED,
                            SAFE_MESSAGE,
                            True,
                            SAFE_SLOT_ACTION,
                        )
                else:
                    self._require_ready_slot(slot)
            session = self._session
            session_token = self._session_token
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
                await self._shutdown_child_preserving_primary_error(session_token)
                raise
            except (TimeoutError, asyncio.TimeoutError):
                if slot is not None:
                    slot.state = SlotState.POISONED
                await self._shutdown_child_preserving_primary_error(session_token)
                raise UpstreamToolError(
                    UPSTREAM_TIMEOUT,
                    SAFE_TIMEOUT_MESSAGE,
                    True,
                    SAFE_NEXT_ACTION,
                ) from None
            except Exception:
                if slot is not None:
                    slot.state = SlotState.POISONED
                await self._shutdown_child_preserving_primary_error(session_token)
                raise UpstreamToolError(
                    UPSTREAM_UNAVAILABLE,
                    SAFE_MESSAGE,
                    True,
                    SAFE_NEXT_ACTION,
                ) from None

    async def load(self, xml_string: str) -> SimulationSlot:
        xml_bytes = _validated_xml_bytes(xml_string)
        slot = self._new_slot(xml_bytes)
        try:
            result = await self._invoke(
                slot,
                "sim_load",
                {"name": slot._name, "xml_string": xml_string},
            )
            payload = normalize_json_result(
                result,
                lambda value: _matches_load(value, expected_name=slot._name),
                max_text_chars=4096 + 4 * len(xml_string),
            )
            slot.summary = {
                key: value for key, value in payload.items() if key != "name"
            }
        except UpstreamToolError:
            slot.state = SlotState.POISONED
            raise
        slot.state = SlotState.READY
        return slot

    async def validate(self, xml_string: str) -> bool:
        _validated_xml_bytes(xml_string)
        result = await self._invoke(
            None,
            "validate_mjcf",
            {"xml_string": xml_string},
        )
        payload = normalize_json_result(
            result,
            _matches_validation,
            max_text_chars=64 * 1024,
        )
        return payload["valid"]

    async def reset(self, slot: SimulationSlot) -> None:
        async with slot._operation_lock:
            result = await self._invoke(slot, "sim_reset", {"sim_name": slot._name})
            try:
                payload = normalize_json_result(
                    result,
                    lambda payload: (
                        _matches_status(payload, "reset") and payload["time"] == 0.0
                    ),
                    max_text_chars=4096,
                )
            except UpstreamToolError:
                slot.state = SlotState.POISONED
                raise
            slot._time = payload["time"]

    async def set_state(
        self,
        slot: SimulationSlot,
        *,
        qpos: list[float] | None = None,
        qvel: list[float] | None = None,
        ctrl: list[float] | None = None,
    ) -> None:
        async with slot._operation_lock:
            self._require_ready_slot(slot)
            for value, width, name in (
                (qpos, slot.summary["nq"], "qpos"),
                (qvel, slot.summary["nv"], "qvel"),
                (ctrl, slot.summary["nu"], "ctrl"),
            ):
                if value is not None and not _numeric_vector(value, width):
                    raise ValueError(
                        f"{name} must match the loaded model width with finite values"
                    )
            arguments: dict[str, Any] = {"sim_name": slot._name}
            if qpos is not None:
                arguments["qpos"] = list(qpos)
            if qvel is not None:
                arguments["qvel"] = list(qvel)
            if ctrl is not None:
                arguments["ctrl"] = list(ctrl)
            result = await self._invoke(slot, "sim_set_state", arguments)
            try:
                normalize_json_result(
                    result,
                    lambda payload: (
                        _matches_status(payload, "ok") and payload["time"] == slot._time
                    ),
                    max_text_chars=4096,
                )
            except UpstreamToolError:
                slot.state = SlotState.POISONED
                raise

    async def run_segment(
        self,
        slot: SimulationSlot,
        *,
        ctrl: list[float],
        n_steps: int,
        track: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        async with slot._operation_lock:
            if type(n_steps) is not int or not 1 <= n_steps <= MAX_STEPS:
                raise ValueError(f"n_steps must be between 1 and {MAX_STEPS}")
            self._require_ready_slot(slot)
            if not _numeric_vector(ctrl, slot.summary["nu"]):
                raise ValueError(
                    "ctrl must match the loaded model width with finite values"
                )
            if type(track) is not tuple:
                raise ValueError("track must be a tuple")
            track = _validate_track(track, slot.summary["bodies"])
            projected_scalars = _run_record_scalar_count(
                segments=((n_steps, len(ctrl)),),
                nq=slot.summary["nq"],
                nv=slot.summary["nv"],
                track=track,
            )
            if projected_scalars > MAX_TRACE_SCALARS:
                raise ValueError(
                    "requested trace exceeds the bounded numeric record budget"
                )
            result = await self._invoke(
                slot,
                "run_and_analyze",
                {
                    "sim_name": slot._name,
                    "ctrl": list(ctrl),
                    "n_steps": n_steps,
                    "capture_every_n": 0,
                    "track": ["qpos", "qvel", "energy", *track],
                },
            )
            try:
                payload = normalize_json_result(
                    result,
                    lambda value: _matches_run(
                        value,
                        qpos_width=slot.summary["nq"],
                        qvel_width=slot.summary["nv"],
                        timestep=slot.summary["timestep"],
                        expected_start=slot._time + slot.summary["timestep"],
                        track=track,
                    ),
                    max_text_chars=4096
                    + n_steps
                    * (
                        512
                        + 64
                        * _trace_scalars_per_step(
                            nq=slot.summary["nq"],
                            nv=slot.summary["nv"],
                            nu=0,
                            track=track,
                        )
                        + sum(len(selection) + 16 for selection in track)
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
            slot._time = payload["sim_time"][1]
            return payload

    async def render(
        self,
        slot: SimulationSlot,
        *,
        width: int = 160,
        height: int = 120,
    ) -> bytes:
        async with slot._operation_lock:
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
                return _render_png(result, width=width, height=height)
            except UpstreamToolError:
                slot.state = SlotState.POISONED
                raise


__all__ = [
    "MAX_STEPS",
    "MAX_TRACE_SCALARS",
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
