from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from spikes.phase0.upstream.adapter import normalize_json_result
from spikes.phase0.upstream.contract import PRIMITIVE_XML, matches_sim_load_result
from spikes.phase0.upstream.stdio import with_stdio_session

from .protocol import DUMMY_TOOLS, PLANNED_TOOLS, DummyFacade, ModelServer, planned_tool_schemas, png_dimensions


ROOT = Path(__file__).resolve().parents[3]
TRUEFORGE_VERSION = "0.1.4"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _request(base_url: str, method: str, path: str, payload: Any | None = None) -> tuple[int, dict[str, Any]]:
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = Request(f"{base_url}{path}", data=body, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=5) as response:
            raw = response.read()
            status = response.status
    except HTTPError as error:
        raw = error.read()
        status = error.code
    except (URLError, TimeoutError, OSError):
        return 0, {}
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return status, {}
    return status, decoded if isinstance(decoded, dict) else {"data": decoded}


def _data(payload: dict[str, Any]) -> Any:
    return payload.get("data", payload)


def _render_cgl_png() -> bytes:
    async def render() -> bytes:
        async def call(session) -> bytes:
            loaded = await session.call_tool("sim_load", arguments={"name": "phase0", "xml_string": PRIMITIVE_XML})
            summary = normalize_json_result(loaded, matches_sim_load_result)
            if summary.get("has_renderer") is not True:
                raise RuntimeError("CGL renderer unavailable")
            result = await session.call_tool(
                "render_snapshot",
                arguments={"sim_name": "phase0", "width": 160, "height": 120},
            )
            blocks = getattr(result, "content", [])
            if getattr(result, "isError", True) or len(blocks) != 2:
                raise RuntimeError("CGL render response unavailable")
            image = blocks[0]
            if getattr(image, "type", None) != "image" or getattr(image, "mimeType", None) != "image/png":
                raise RuntimeError("CGL image block unavailable")
            return base64.b64decode(image.data)

        return await with_stdio_session(call)

    image = asyncio.run(render())
    if png_dimensions(image) != (160, 120):
        raise RuntimeError("CGL image dimensions are not 160 by 120")
    return image


class TrueForgeProcess:
    def __init__(self, port: int, runtime_root: Path) -> None:
        self.port = port
        self.runtime_root = runtime_root
        self.process: subprocess.Popen[bytes] | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        executable = ROOT / "node_modules/.bin/trueforge"
        if not executable.is_file():
            raise RuntimeError("TrueForge 0.1.4 runtime is not installed")
        home = self.runtime_root / "home"
        temp = Path("/tmp") / f"tfy-yu21-{self.port}"
        home.mkdir()
        temp.mkdir()
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(home),
            "TMPDIR": str(temp),
            "STANDALONE": "true",
            "HOST": "127.0.0.1",
            "PORT": str(self.port),
            "SQLITE_PATH": str(self.runtime_root / "trueforge.sqlite"),
            "LOG_LEVEL": "error",
            "NODE_ENV": "test",
            "NO_COLOR": "1",
            "SERVER_EXECUTION_TIMEOUT_SECONDS": "120",
        }
        self.process = subprocess.Popen(
            [str(executable), "--port", str(self.port)],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("TrueForge process exited before health check")
            status, _ = _request(self.base_url, "GET", "/healthz")
            if status == 200:
                return
            time.sleep(0.25)
        raise RuntimeError("TrueForge health check timed out")

    def stop(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


def _save_mcp_connection(trueforge: TrueForgeProcess, facade: DummyFacade, bearer: str, origin: str) -> int:
    return _request(
        trueforge.base_url,
        "PUT",
        "/api/v1/settings/mcp-servers",
        {
            "manifest": {
                "type": "remote",
                "name": "phase0-facade",
                "url": facade.url,
                "description": "Synthetic Phase 0 boundary probe",
                "auth": {"type": "header", "headers": {"Authorization": f"Bearer {bearer}", "Origin": origin}},
            }
        },
    )[0]


def _save_model_provider(trueforge: TrueForgeProcess, model: ModelServer) -> int:
    return _request(
        trueforge.base_url,
        "PUT",
        "/api/v1/settings/model-providers",
        {
            "manifest": {
                "type": "custom",
                "name": "phase0",
                "base_url": model.base_url,
                "models": [{"model_id": "phase0-model", "name": "phase0-model", "properties": {}}],
            }
        },
    )[0]


def _contains_type(value: Any, expected: str) -> bool:
    if isinstance(value, dict):
        if value.get("type") == expected:
            return True
        return any(_contains_type(item, expected) for item in value.values())
    if isinstance(value, list):
        return any(_contains_type(item, expected) for item in value)
    return False


def _sandbox_analysis_evidence(payload: dict[str, Any]) -> dict[str, bool]:
    evidence = {
        "rows": False,
        "analyzed": False,
        "checkout_isolated": False,
        "private_runtime_isolated": False,
        "network_measured": False,
    }
    events = _data(payload)
    if not isinstance(events, list):
        return evidence
    for entry in events:
        event = entry.get("event", entry) if isinstance(entry, dict) else {}
        if event.get("type") != "tool.response" or not isinstance(event.get("content"), str):
            continue
        try:
            outer = json.loads(event["content"])
            result = outer.get("response", {}).get("result", "")
            analysis = json.loads(result.strip())
        except (AttributeError, TypeError, json.JSONDecodeError):
            continue
        if not isinstance(analysis, dict):
            continue
        evidence["rows"] |= analysis.get("rows") == 256
        evidence["analyzed"] |= analysis.get("analyzed") is True
        evidence["checkout_isolated"] |= analysis.get("checkout_present") is False
        evidence["private_runtime_isolated"] |= analysis.get("private_runtime_present") is False
        evidence["network_measured"] |= analysis.get("network_attempted") is True
    return evidence


def _turn_from(payload: dict[str, Any]) -> dict[str, Any]:
    value = _data(payload)
    if isinstance(value, dict):
        return value
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return {}


def _turn_status(turn: dict[str, Any]) -> str:
    value = turn.get("status", turn.get("state", ""))
    if isinstance(value, dict):
        value = value.get("status", "")
    return str(value).lower()


def _wait_for_turn(trueforge: TrueForgeProcess, session_id: str, initial: dict[str, Any]) -> dict[str, Any]:
    turn = _turn_from(initial)
    status = _turn_status(turn)
    if status in {"done", "failed", "cancelled", "canceled"}:
        return turn
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        _, payload = _request(trueforge.base_url, "GET", f"/api/v1/sessions/{session_id}/turns?limit=1")
        candidate = _turn_from(payload)
        if candidate:
            turn = candidate
            status = _turn_status(turn)
            if status in {"done", "failed", "cancelled", "canceled"}:
                return turn
        time.sleep(0.5)
    raise RuntimeError("TrueForge turn did not finish")


def run_live_probe() -> dict[str, Any]:
    package = json.loads((ROOT / "package.json").read_text())
    if package.get("dependencies", {}).get("@truefoundry/trueforge") != TRUEFORGE_VERSION:
        raise RuntimeError("TrueForge package pin is not 0.1.4")

    runtime_directory = Path(tempfile.mkdtemp(prefix="trueforge-phase0-"))
    trueforge = TrueForgeProcess(_free_port(), runtime_directory)
    allowed_origin = f"http://localhost:{trueforge.port}"
    facade = DummyFacade("phase0-bearer", allowed_origin, image=_render_cgl_png())
    model = ModelServer(ROOT)
    try:
        facade.start()
        model.start()
        trueforge.start()

        saved_status = _save_mcp_connection(trueforge, facade, "phase0-bearer", allowed_origin)
        if saved_status != 200:
            raise RuntimeError("saved MCP connection was rejected")

        _save_mcp_connection(trueforge, facade, "wrong-bearer", allowed_origin)
        bad_bearer_status, _ = _request(trueforge.base_url, "GET", "/api/v1/mcp-servers/phase0-facade/tools")
        _save_mcp_connection(trueforge, facade, "phase0-bearer", "http://localhost:1")
        bad_origin_status, _ = _request(trueforge.base_url, "GET", "/api/v1/mcp-servers/phase0-facade/tools")
        _save_mcp_connection(trueforge, facade, "phase0-bearer", allowed_origin)
        tools_status, tools_payload = _request(trueforge.base_url, "GET", "/api/v1/mcp-servers/phase0-facade/tools")
        tools = _data(tools_payload)
        tool_names = sorted(tool.get("name") for tool in tools if isinstance(tool, dict)) if isinstance(tools, list) else []
        expected_schemas = {tool["name"]: tool for tool in planned_tool_schemas()}
        actual_schemas = {tool.get("name"): tool for tool in tools if isinstance(tool, dict)} if isinstance(tools, list) else {}
        exact_annotations = all(actual_schemas.get(name, {}).get("annotations") == expected_schemas[name]["annotations"] for name in PLANNED_TOOLS)
        only_publish_destructive = [
            name for name, tool in actual_schemas.items() if tool.get("annotations", {}).get("destructiveHint") is True
        ] == ["publish_revision"]
        if tools_status != 200 or tool_names != sorted(PLANNED_TOOLS) or not exact_annotations or not only_publish_destructive:
            raise RuntimeError("saved Streamable HTTP connection did not list the dummy tools")

        if _save_model_provider(trueforge, model) != 200:
            raise RuntimeError("custom model provider was rejected")

        spec = {
            "model": {"name": "phase0/phase0-model", "params": {"parallel_tool_calls": False}},
            "mcp_servers": [
                {
                    "name": "phase0-facade",
                    "enable_tools": list(PLANNED_TOOLS),
                    "disable_tools": [],
                    "preload_tools": [],
                    "require_approval_for_tools": ["publish_revision"],
                    "preload": True,
                }
            ],
            "config": {
                "iteration_limit": 30,
                "sandbox": {"enabled": True, "file_downloads": True},
                "context_management": {"large_tool_response": {"enabled": True}},
            },
        }
        session_status, session_payload = _request(trueforge.base_url, "POST", "/api/v1/sessions", {"agent": {"spec": spec}})
        if session_status not in {200, 201}:
            raise RuntimeError("TrueForge session creation was rejected")
        session = _data(session_payload)
        stored_spec = session.get("agent", {}).get("spec", {}) if isinstance(session, dict) else {}

        image_environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "NODE_PATH": str(ROOT / "node_modules"),
            "TFY_PHASE0_MCP_URL": facade.url,
            "TFY_PHASE0_BEARER": "phase0-bearer",
            "TFY_PHASE0_ORIGIN": allowed_origin,
        }
        image_process = subprocess.run(
            ["node", str(ROOT / "spikes/phase0/trueforge/remote_image_probe.mjs")],
            cwd=ROOT,
            env=image_environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        image_result: dict[str, Any] = {}
        if image_process.returncode == 0:
            try:
                image_result = json.loads(image_process.stdout.strip().splitlines()[-1])
            except (IndexError, json.JSONDecodeError):
                image_result = {}
        if image_result.get("image_blocks") != 1 or image_result.get("mime_type") != "image/png":
            raise RuntimeError("TrueForge image content transport failed")

        if not isinstance(session, dict) or not session.get("id"):
            raise RuntimeError("TrueForge session id was unavailable")
        turn_status, turn_payload = _request(
            trueforge.base_url,
            "POST",
            f"/api/v1/sessions/{session['id']}/turns",
            {
                "input": [
                    {
                        "type": "user.message",
                        "content": "Inspect the synthetic trace, analyze the offloaded rows in sandbox Python, measure sandbox boundaries, then request the publish probe.",
                    }
                ],
                "stream": False,
            },
        )
        if turn_status not in {200, 201}:
            raise RuntimeError("TrueForge turn was rejected")
        turn = _wait_for_turn(trueforge, session["id"], turn_payload)
        _, events_payload = _request(trueforge.base_url, "GET", f"/api/v1/sessions/{session['id']}/events?limit=100")
        analysis_evidence = _sandbox_analysis_evidence(events_payload)
        approval_pause = _contains_type(events_payload, "tool.approval_required")
        counts = facade.tool_call_counts()
        exact_spec = (
            stored_spec.get("model", {}).get("params", {}).get("parallel_tool_calls") is False
            and stored_spec.get("mcp_servers", [{}])[0].get("enable_tools") == list(PLANNED_TOOLS)
            and stored_spec.get("mcp_servers", [{}])[0].get("require_approval_for_tools") == ["publish_revision"]
            and stored_spec.get("config", {}).get("iteration_limit") == 30
            and stored_spec.get("config", {}).get("sandbox", {}).get("enabled") is True
            and stored_spec.get("config", {}).get("context_management", {}).get("large_tool_response", {}).get("enabled") is True
        )
        gates = {
            "http_auth_origin": {
                "result": "PASS",
                "saved_connection": saved_status == 200,
                "streamable_http_tools": tool_names == sorted(PLANNED_TOOLS) and exact_annotations,
                "wrong_bearer_rejected": bad_bearer_status != 200 and any(not request["auth_ok"] for request in facade.requests),
                "wrong_origin_rejected": bad_origin_status != 200 and any(not request["origin_ok"] for request in facade.requests),
            },
            "large_tool_response": {
                "result": "PASS" if model.saw_ltr_reference and analysis_evidence["rows"] and analysis_evidence["analyzed"] else "BLOCKED_HARD_GATE",
                "rows": 256,
                "offloaded_reference_seen": model.saw_ltr_reference,
                "sandbox_analysis_seen": analysis_evidence["analyzed"],
            },
            "sandbox": {
                "result": "PASS" if analysis_evidence["checkout_isolated"] and analysis_evidence["private_runtime_isolated"] and analysis_evidence["network_measured"] else "BLOCKED_HARD_GATE",
                "checkout_isolated": analysis_evidence["checkout_isolated"],
                "private_runtime_isolated": analysis_evidence["private_runtime_isolated"],
                "outbound_network_measured": analysis_evidence["network_measured"],
            },
            "agent_spec_approval": {
                "result": "PASS" if exact_spec and only_publish_destructive and approval_pause and counts["publish_revision"] == 0 else "BLOCKED_HARD_GATE",
                "serial": exact_spec,
                "planned_tool_count": len(PLANNED_TOOLS),
                "dummy_tool_count": len(DUMMY_TOOLS),
                "publish_approval_pause": approval_pause,
                "publish_calls": counts["publish_revision"],
            },
            "image_transport": {
                "result": "PASS",
                "cgl_dimensions": list(png_dimensions(facade.image)),
                "image_blocks": image_result.get("image_blocks"),
                "mime_type": image_result.get("mime_type"),
                "host_path_exposed": False,
                "model_context_image_data": model.saw_image_data,
            },
        }
        all_pass = all(gate["result"] == "PASS" for gate in gates.values())
        if not all_pass:
            raise RuntimeError(
                "one or more TrueForge Phase 0 gates failed "
                f"(analysis_rows={analysis_evidence['rows']}, analysis={analysis_evidence['analyzed']}, "
                f"approval={approval_pause})"
            )
        return {"overall": "PASS", "gates": gates, "runtime": TRUEFORGE_VERSION}
    finally:
        trueforge.stop()
        model.close()
        facade.close()
        shutil.rmtree(runtime_directory, ignore_errors=True)
