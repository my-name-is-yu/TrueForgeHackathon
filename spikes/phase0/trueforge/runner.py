from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import socket
import stat
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
SUCCESSFUL_TURN_STATUSES = {"done"}


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


def _events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    value = _data(payload)
    if not isinstance(value, list):
        return []
    return [entry.get("event", entry) if isinstance(entry, dict) else {} for entry in value]


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
        self.temp_directory: Path | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        executable = ROOT / "node_modules/.bin/trueforge"
        if not executable.is_file():
            raise RuntimeError("TrueForge 0.1.4 runtime is not installed")
        home = self.runtime_root / "home"
        temp = self.runtime_root / "tmp"
        home.mkdir()
        temp.mkdir()
        self.temp_directory = temp
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
        try:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
        finally:
            if self.temp_directory is not None:
                shutil.rmtree(self.temp_directory, ignore_errors=True)
                self.temp_directory = None


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


def _remote_tools_attempt(
    trueforge: TrueForgeProcess,
    facade: DummyFacade,
    bearer: str,
    origin: str,
) -> dict[str, Any]:
    request_start = len(facade.requests)
    saved_status = _save_mcp_connection(trueforge, facade, bearer, origin)
    trueforge_status, payload = _request(trueforge.base_url, "GET", "/api/v1/mcp-servers/phase0-facade/tools")
    requests = [request for request in facade.requests[request_start:] if request.get("path") == "/mcp"]
    return {
        "saved_status": saved_status,
        "trueforge_status": trueforge_status,
        "payload": payload,
        "request_count": len(requests),
        "request": requests[0] if len(requests) == 1 else None,
    }


def _rejection_matches(attempt: dict[str, Any], expected_status: int, auth_ok: bool, origin_ok: bool) -> bool:
    request = attempt.get("request")
    return (
        attempt.get("request_count") == 1
        and attempt.get("trueforge_status") != 200
        and isinstance(request, dict)
        and request.get("path") == "/mcp"
        and request.get("auth_ok") is auth_ok
        and request.get("origin_ok") is origin_ok
        and request.get("response_status") == expected_status
    )


def evaluate_phase0_gates(evidence: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], bool]:
    http = evidence["http"]
    wrong_bearer = _rejection_matches(http["wrong_bearer"], 401, auth_ok=False, origin_ok=True)
    wrong_origin = _rejection_matches(http["wrong_origin"], 403, auth_ok=True, origin_ok=False)
    http_tools = http["streamable_http_tools"] is True
    http_saved = http["saved_connection"] is True
    http_result = http_saved and http_tools and wrong_bearer and wrong_origin

    ltr = evidence["ltr"]
    sandbox = evidence["sandbox"]
    ltr_result = (
        evidence["turn_status"] in SUCCESSFUL_TURN_STATUSES
        and ltr.get("offloaded_reference_seen") is True
        and sandbox.get("matching_response_count") == 1
        and sandbox.get("successful") is True
        and sandbox.get("rows") is True
        and sandbox.get("analyzed") is True
    )
    sandbox_result = (
        evidence["turn_status"] in SUCCESSFUL_TURN_STATUSES
        and sandbox.get("matching_response_count") == 1
        and sandbox.get("successful") is True
        and sandbox.get("checkout_isolated") is True
        and sandbox.get("private_runtime_isolated") is True
        and sandbox.get("canary_metadata_valid") is True
        and sandbox.get("private_data_clear") is True
        and sandbox.get("network_attempted") is True
        and sandbox.get("network") == "blocked"
    )

    approval = evidence["approval"]
    approval_result = (
        evidence["turn_status"] in SUCCESSFUL_TURN_STATUSES
        and evidence["exact_spec"] is True
        and evidence["only_publish_destructive"] is True
        and approval.get("approval_event_seen") is True
        and approval.get("publish_approval_call_match") is True
        and evidence["publish_calls"] == 0
    )

    image = evidence["image"]
    image_result = (
        image.get("image_blocks") == 1
        and image.get("mime_type") == "image/png"
        and image.get("width") == 160
        and image.get("height") == 120
        and image.get("host_path_exposed") is False
        and image.get("model_context_image_data") is False
    )

    wrong_bearer_request = http["wrong_bearer"].get("request")
    wrong_origin_request = http["wrong_origin"].get("request")

    gates = {
        "http_auth_origin": {
            "result": "PASS" if http_result else "BLOCKED_HARD_GATE",
            "saved_connection": http_saved,
            "streamable_http_tools": http_tools,
            "wrong_bearer_rejected": wrong_bearer,
            "wrong_bearer_expected_status": 401,
            "wrong_bearer_observed_status": (
                wrong_bearer_request.get("response_status") if isinstance(wrong_bearer_request, dict) else None
            ),
            "wrong_origin_rejected": wrong_origin,
            "wrong_origin_expected_status": 403,
            "wrong_origin_observed_status": (
                wrong_origin_request.get("response_status") if isinstance(wrong_origin_request, dict) else None
            ),
        },
        "large_tool_response": {
            "result": "PASS" if ltr_result else "BLOCKED_HARD_GATE",
            "rows": 256,
            "offloaded_reference_seen": ltr.get("offloaded_reference_seen") is True,
            "sandbox_analysis_seen": sandbox.get("analyzed") is True,
        },
        "sandbox": {
            "result": "PASS" if sandbox_result else "BLOCKED_HARD_GATE",
            "checkout_isolated": sandbox.get("checkout_isolated") is True,
            "private_runtime_isolated": sandbox.get("private_runtime_isolated") is True,
            "boundary_canary_metadata_valid": sandbox.get("canary_metadata_valid") is True,
            "protected_data_clear": sandbox.get("private_data_clear") is True,
            "outbound_network_attempted": sandbox.get("network_attempted") is True,
            "outbound_network_blocked": sandbox.get("network") == "blocked",
        },
        "agent_spec_approval": {
            "result": "PASS" if approval_result else "BLOCKED_HARD_GATE",
            "serial": evidence["exact_spec"] is True,
            "planned_tool_count": len(PLANNED_TOOLS),
            "dummy_tool_count": len(DUMMY_TOOLS),
            "publish_approval_pause": approval.get("publish_approval_call_match") is True,
            "publish_calls": evidence["publish_calls"],
            "turn_completed_successfully": evidence["turn_status"] in SUCCESSFUL_TURN_STATUSES,
        },
        "image_transport": {
            "result": "PASS" if image_result else "BLOCKED_HARD_GATE",
            "cgl_dimensions": [image.get("width"), image.get("height")],
            "image_blocks": image.get("image_blocks"),
            "mime_type": image.get("mime_type"),
            "host_path_exposed": image.get("host_path_exposed"),
            "model_context_image_data": image.get("model_context_image_data"),
        },
    }
    return gates, all(gate["result"] == "PASS" for gate in gates.values())


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


def _tool_call_ids(payload: dict[str, Any], tool_name: str) -> list[str]:
    ids: list[str] = []
    for event in _events(payload):
        if event.get("type") != "model.message":
            continue
        for tool_call in event.get("tool_calls", []):
            if not isinstance(tool_call, dict) or not isinstance(tool_call.get("function"), dict):
                continue
            if tool_call["function"].get("name") == tool_name and isinstance(tool_call.get("id"), str):
                ids.append(tool_call["id"])
    return ids


def _approval_evidence(payload: dict[str, Any], publish_call_ids: list[str]) -> dict[str, Any]:
    approval_call_ids: list[str] = []
    for event in _events(payload):
        if event.get("type") != "tool.approval_required":
            continue
        for tool_call in event.get("tool_calls", []):
            if isinstance(tool_call, dict) and isinstance(tool_call.get("id"), str):
                approval_call_ids.append(tool_call["id"])
    matched = len(publish_call_ids) == 1 and approval_call_ids.count(publish_call_ids[0]) == 1
    return {
        "approval_event_seen": bool(approval_call_ids),
        "publish_call_count": len(publish_call_ids),
        "publish_approval_call_match": matched,
    }


def _sandbox_analysis_evidence(payload: dict[str, Any], sandbox_call_id: str | None) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    if sandbox_call_id is None:
        return {"matching_response_count": 0}
    for event in _events(payload):
        if event.get("type") != "tool.response" or event.get("tool_call_id") != sandbox_call_id:
            continue
        content = event.get("content")
        if not isinstance(content, str):
            continue
        try:
            outer = json.loads(content)
            response = outer.get("response", {})
            result = response.get("result", "")
            analysis = json.loads(result.strip())
        except (AttributeError, TypeError, json.JSONDecodeError):
            continue
        if not isinstance(response, dict) or not isinstance(analysis, dict):
            continue
        matches.append(
            {
                "success": outer.get("success") is True,
                "exit_code": response.get("exitCode"),
                "rows": analysis.get("rows"),
                "analyzed": analysis.get("analyzed"),
                "checkout_isolated": analysis.get("checkout_isolated"),
                "private_runtime_isolated": analysis.get("private_runtime_isolated"),
                "network_attempted": analysis.get("network_attempted"),
                "network": analysis.get("network"),
            }
        )
    if len(matches) != 1:
        return {"matching_response_count": len(matches)}
    observation = matches[0]
    return {
        "matching_response_count": 1,
        "successful": observation["success"] and observation["exit_code"] == 0,
        "rows": observation["rows"] == 256,
        "analyzed": observation["analyzed"] is True,
        "checkout_isolated": observation["checkout_isolated"] is True,
        "private_runtime_isolated": observation["private_runtime_isolated"] is True,
        "network_attempted": observation["network_attempted"] is True,
        "network": observation["network"],
    }


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


def _make_sentinel(directory: Path, prefix: str) -> tuple[Path, bytes]:
    value = os.urandom(32)
    with tempfile.NamedTemporaryFile(mode="wb", dir=directory, prefix=prefix, delete=False) as handle:
        handle.write(value)
        return Path(handle.name), value


def _make_boundary_canary(directory: Path, target: Path, name: str) -> Path:
    path = directory / name
    path.hardlink_to(target)
    return path


def _boundary_canary_metadata_matches(canaries: list[Path], targets: list[Path]) -> bool:
    if len(canaries) != len(targets):
        return False
    for canary, target in zip(canaries, targets, strict=True):
        try:
            canary_metadata = canary.lstat()
            target_metadata = target.stat()
        except OSError:
            return False
        if (
            not stat.S_ISREG(canary_metadata.st_mode)
            or stat.S_ISLNK(canary_metadata.st_mode)
            or canary_metadata.st_dev != target_metadata.st_dev
            or canary_metadata.st_ino != target_metadata.st_ino
            or canary_metadata.st_mode != target_metadata.st_mode
            or canary_metadata.st_uid != target_metadata.st_uid
            or canary_metadata.st_gid != target_metadata.st_gid
            or canary_metadata.st_nlink < 2
        ):
            return False
    return True


def _contains_boundary_data(observed: Any, paths: list[Path], values: list[bytes]) -> bool:
    serialized = json.dumps(observed, sort_keys=True, default=str)
    needles = [str(path) for path in paths]
    for value in values:
        representation = repr(value)
        needles.extend(
            (
                value.hex(),
                base64.b64encode(value).decode("ascii"),
                representation,
                json.dumps(representation),
            )
        )
    return any(needle in serialized for needle in needles)


def run_live_probe() -> dict[str, Any]:
    package = json.loads((ROOT / "package.json").read_text())
    package_lock = json.loads((ROOT / "package-lock.json").read_text())
    if package.get("dependencies", {}).get("@truefoundry/trueforge") != TRUEFORGE_VERSION:
        raise RuntimeError("TrueForge package pin is not 0.1.4")
    if package_lock.get("packages", {}).get("", {}).get("dependencies", {}).get("@truefoundry/trueforge") != TRUEFORGE_VERSION:
        raise RuntimeError("TrueForge lockfile pin is not 0.1.4")

    runtime_directory = Path(tempfile.mkdtemp(prefix="tf0-", dir="/tmp"))
    checkout_sentinel: Path | None = None
    checkout_value: bytes | None = None
    private_sentinel: Path | None = None
    private_value: bytes | None = None
    boundary_directory: Path | None = None
    boundary_canaries: list[Path] = []
    trueforge: TrueForgeProcess | None = None
    facade: DummyFacade | None = None
    model: ModelServer | None = None
    try:
        checkout_sentinel, checkout_value = _make_sentinel(ROOT, ".trueforge-phase0-checkout-")
        private_runtime = runtime_directory / "private-runtime"
        private_runtime.mkdir()
        private_sentinel, private_value = _make_sentinel(private_runtime, "sentinel-")
        boundary_directory = Path(tempfile.mkdtemp(prefix="tf0-boundary-", dir="/tmp"))
        boundary_canaries.append(_make_boundary_canary(boundary_directory, checkout_sentinel, "a"))
        boundary_canaries.append(_make_boundary_canary(boundary_directory, private_sentinel, "b"))
        image = _render_cgl_png()
        trueforge = TrueForgeProcess(_free_port(), runtime_directory)
        allowed_origin = f"http://localhost:{trueforge.port}"
        facade = DummyFacade("phase0-bearer", allowed_origin, image=image)
        model = ModelServer(ROOT, boundary_directory.name)
        facade.start()
        model.start()
        trueforge.start()

        wrong_bearer = _remote_tools_attempt(trueforge, facade, "wrong-bearer", allowed_origin)
        wrong_origin = _remote_tools_attempt(trueforge, facade, "phase0-bearer", "http://localhost:1")
        valid_connection = _remote_tools_attempt(trueforge, facade, "phase0-bearer", allowed_origin)
        tools = _data(valid_connection["payload"])
        tool_names = [tool.get("name") for tool in tools] if isinstance(tools, list) else []
        expected_schemas = {tool["name"]: tool for tool in planned_tool_schemas()}
        actual_schemas = {tool.get("name"): tool for tool in tools if isinstance(tool, dict)} if isinstance(tools, list) else {}
        normalized_schemas = {
            name: {key: value for key, value in tool.items() if key != "preload"}
            for name, tool in actual_schemas.items()
            if isinstance(tool, dict)
        }
        exact_schemas = len(tools) == len(PLANNED_TOOLS) and normalized_schemas == expected_schemas
        only_publish_destructive = {
            name for name, tool in actual_schemas.items() if tool.get("annotations", {}).get("destructiveHint") is True
        } == {"publish_revision"}
        http_evidence = {
            "saved_connection": valid_connection["saved_status"] == 200,
            "streamable_http_tools": valid_connection["trueforge_status"] == 200
            and len(tool_names) == len(PLANNED_TOOLS)
            and set(tool_names) == set(PLANNED_TOOLS)
            and exact_schemas,
            "wrong_bearer": wrong_bearer,
            "wrong_origin": wrong_origin,
        }

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
        stored_servers = stored_spec.get("mcp_servers") if isinstance(stored_spec, dict) else None
        stored_server = stored_servers[0] if isinstance(stored_servers, list) and len(stored_servers) == 1 else {}
        exact_spec = (
            stored_spec.get("model", {}).get("name") == "phase0/phase0-model"
            and stored_spec.get("model", {}).get("params", {}).get("parallel_tool_calls") is False
            and stored_server.get("name") == "phase0-facade"
            and stored_server.get("enable_tools") == list(PLANNED_TOOLS)
            and stored_server.get("disable_tools") == []
            and stored_server.get("preload_tools") == []
            and stored_server.get("require_approval_for_tools") == ["publish_revision"]
            and stored_server.get("preload") is True
            and stored_spec.get("config", {}).get("iteration_limit") == 30
            and stored_spec.get("config", {}).get("sandbox", {}).get("enabled") is True
            and stored_spec.get("config", {}).get("context_management", {}).get("large_tool_response", {}).get("enabled") is True
        )

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
        turn: dict[str, Any] = {}
        if turn_status in {200, 201}:
            turn = _wait_for_turn(trueforge, session["id"], turn_payload)
        turn_state = _turn_status(turn)
        _, events_payload = _request(trueforge.base_url, "GET", f"/api/v1/sessions/{session['id']}/events?limit=100")
        sandbox_call_ids = _tool_call_ids(events_payload, "exec")
        sandbox_evidence = _sandbox_analysis_evidence(
            events_payload, sandbox_call_ids[0] if len(sandbox_call_ids) == 1 else None
        )
        canary_metadata_valid = (
            checkout_sentinel is not None
            and private_sentinel is not None
            and _boundary_canary_metadata_matches(boundary_canaries, [checkout_sentinel, private_sentinel])
        )
        private_data_clear = (
            boundary_directory is not None
            and checkout_sentinel is not None
            and private_sentinel is not None
            and checkout_value is not None
            and private_value is not None
            and not _contains_boundary_data(
                (facade.results, model.request_bodies, events_payload),
                [ROOT, private_runtime, checkout_sentinel, private_sentinel, boundary_directory, *boundary_canaries],
                [checkout_value, private_value],
            )
        )
        sandbox_evidence["canary_metadata_valid"] = canary_metadata_valid
        sandbox_evidence["private_data_clear"] = private_data_clear
        publish_call_ids = _tool_call_ids(events_payload, "publish_revision")
        approval = _approval_evidence(events_payload, publish_call_ids)
        counts = facade.tool_call_counts()
        gates, all_pass = evaluate_phase0_gates(
            {
                "turn_status": turn_state,
                "http": http_evidence,
                "ltr": {"offloaded_reference_seen": model.saw_ltr_reference},
                "sandbox": sandbox_evidence,
                "approval": approval,
                "exact_spec": exact_spec,
                "only_publish_destructive": only_publish_destructive,
                "publish_calls": counts["publish_revision"],
                "image": {
                    "image_blocks": image_result.get("image_blocks"),
                    "mime_type": image_result.get("mime_type"),
                    "width": image_result.get("width"),
                    "height": image_result.get("height"),
                    "host_path_exposed": image_result.get("host_path_exposed"),
                    "model_context_image_data": model.saw_image_data,
                },
            }
        )
        if not all_pass:
            raise RuntimeError("one or more TrueForge Phase 0 gates failed")
        return {"overall": "PASS", "gates": gates, "runtime": TRUEFORGE_VERSION}
    finally:
        if trueforge is not None:
            trueforge.stop()
        if model is not None:
            model.close()
        if facade is not None:
            facade.close()
        for canary in boundary_canaries:
            canary.unlink(missing_ok=True)
        if boundary_directory is not None:
            shutil.rmtree(boundary_directory, ignore_errors=True)
        if checkout_sentinel is not None:
            checkout_sentinel.unlink(missing_ok=True)
        shutil.rmtree(runtime_directory, ignore_errors=True)
