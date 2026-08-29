from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
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


def _sanitize_event(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sanitize_event(child) for key, child in value.items() if key != "sandbox_id"}
    if isinstance(value, list):
        return [_sanitize_event(child) for child in value]
    return value


def _sanitized_events_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized: list[dict[str, Any]] = []
    for event in _events(payload):
        if str(event.get("type", "")).startswith("sandbox."):
            continue
        sanitized.append(
            {
                "event": _sanitize_event(event),
            }
        )
    return {"data": sanitized}


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
    def __init__(self, port: int, runtime_root: Path, home_alias: Path) -> None:
        self.port = port
        self.runtime_root = runtime_root
        self.home_alias = home_alias
        self.process: subprocess.Popen[bytes] | None = None
        self.temp_directory: Path | None = None
        self.home_alias_created = False

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
        self.home_alias.symlink_to(home, target_is_directory=True)
        self.home_alias_created = True
        temp.mkdir()
        self.temp_directory = temp
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.home_alias),
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
            if self.home_alias_created:
                self.home_alias.unlink(missing_ok=True)
                self.home_alias_created = False


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
        and sandbox.get("sentinels_intact") is True
        and sandbox.get("helper_staged") is True
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
            "boundary_sentinels_intact": sandbox.get("sentinels_intact") is True,
            "boundary_helper_staged": sandbox.get("helper_staged") is True,
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
                "helper_status": analysis.get("helper_status"),
                "checkout_metadata": analysis.get("checkout_metadata"),
                "checkout_content": analysis.get("checkout_content"),
                "private_runtime_metadata": analysis.get("private_runtime_metadata"),
                "private_runtime_content": analysis.get("private_runtime_content"),
                "network_attempted": analysis.get("network_attempted"),
                "network": analysis.get("network"),
            }
        )
    if len(matches) != 1:
        return {"matching_response_count": len(matches)}
    observation = matches[0]
    return {
        "matching_response_count": 1,
        "successful": (
            observation["success"]
            and observation["exit_code"] == 0
            and observation["helper_status"] == "ok"
        ),
        "rows": observation["rows"] == 256,
        "analyzed": observation["analyzed"] is True,
        "checkout_isolated": (
            observation["checkout_metadata"] == "blocked" and observation["checkout_content"] == "blocked"
        ),
        "private_runtime_isolated": (
            observation["private_runtime_metadata"] == "blocked"
            and observation["private_runtime_content"] == "blocked"
        ),
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


def _trueforge_data_directory(home: Path) -> Path:
    relative = Path("Library", "Application Support") if sys.platform == "darwin" else Path(".local", "share")
    return home / relative / "trueforge"


def _sentinels_intact(paths: list[Path], values: list[bytes]) -> bool:
    if len(paths) != len(values):
        return False
    try:
        return all(path.is_file() and path.read_bytes() == value for path, value in zip(paths, values, strict=True))
    except OSError:
        return False


def _contains_prohibited_boundary_data(observed: Any, paths: list[Path], values: list[bytes]) -> bool:
    text_needles = [str(path) for path in paths]
    for value in values:
        text_needles.extend((value.hex(), base64.b64encode(value).decode("ascii")))

    def contains(item: Any) -> bool:
        if isinstance(item, dict):
            return any(contains(key) or contains(value) for key, value in item.items())
        if isinstance(item, (list, tuple)):
            return any(contains(value) for value in item)
        if isinstance(item, bytes):
            return any(value in item for value in values)
        if isinstance(item, str):
            return any(needle in item for needle in text_needles)
        return False

    return contains(observed)


def _boundary_helper_source(ltr_path: str, checkout_sentinel: Path, private_sentinel: Path) -> str:
    return f'''from __future__ import annotations

import json
import socket
from pathlib import Path

LTR_PATH = Path({ltr_path!r})
CHECKOUT_SENTINEL = Path({str(checkout_sentinel)!r})
PRIVATE_SENTINEL = Path({str(private_sentinel)!r})


def access_status(path: Path, operation: str) -> str:
    try:
        if operation == "metadata":
            path.stat()
        else:
            path.read_bytes()
    except PermissionError:
        return "blocked"
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "error"
    return "readable"


def main() -> int:
    try:
        data = json.loads(LTR_PATH.read_text())
        rows = len(data["rows"])
    except (KeyError, TypeError, OSError, UnicodeError, json.JSONDecodeError):
        print(json.dumps({{"helper_status": "error"}}, separators=(",", ":")))
        return 1

    network = "reachable"
    try:
        socket.create_connection(("example.com", 80), 1).close()
    except OSError:
        network = "blocked"

    print(
        json.dumps(
            {{
                "helper_status": "ok",
                "rows": rows,
                "analyzed": True,
                "checkout_metadata": access_status(CHECKOUT_SENTINEL, "metadata"),
                "checkout_content": access_status(CHECKOUT_SENTINEL, "content"),
                "private_runtime_metadata": access_status(PRIVATE_SENTINEL, "metadata"),
                "private_runtime_content": access_status(PRIVATE_SENTINEL, "content"),
                "network_attempted": True,
                "network": network,
            }},
            separators=(",", ":"),
        )
    )
    return 0


raise SystemExit(main())
'''


def _stage_boundary_helper(
    sandbox_parent: Path,
    ltr_path: str,
    checkout_sentinel: Path,
    private_sentinel: Path,
) -> Path:
    roots = [path for path in sandbox_parent.iterdir() if path.is_dir()]
    if len(roots) != 1:
        raise RuntimeError("exact TrueForge sandbox root was unavailable")
    helper = roots[0] / ".phase0-boundary-probe.py"
    helper.write_text(_boundary_helper_source(ltr_path, checkout_sentinel, private_sentinel))
    helper.chmod(0o600)
    return helper


def _cleanup_live_probe(
    boundary_helper: Path | None,
    trueforge: TrueForgeProcess | None,
    model: ModelServer | None,
    facade: DummyFacade | None,
    home_alias: Path | None,
    checkout_sentinel: Path | None,
    runtime_directory: Path,
) -> None:
    cleanup_error: Exception | None = None
    actions = (
        lambda: boundary_helper.unlink(missing_ok=True) if boundary_helper is not None else None,
        lambda: trueforge.stop() if trueforge is not None else None,
        lambda: model.close() if model is not None else None,
        lambda: facade.close() if facade is not None else None,
        lambda: home_alias.unlink(missing_ok=True) if home_alias is not None else None,
        lambda: checkout_sentinel.unlink(missing_ok=True) if checkout_sentinel is not None else None,
        lambda: shutil.rmtree(runtime_directory, ignore_errors=True),
    )
    for action in actions:
        try:
            action()
        except Exception as error:
            if cleanup_error is None:
                cleanup_error = error
    if cleanup_error is not None:
        raise cleanup_error


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
    boundary_helper: Path | None = None
    home_alias: Path | None = None
    trueforge: TrueForgeProcess | None = None
    facade: DummyFacade | None = None
    model: ModelServer | None = None
    try:
        checkout_sentinel, checkout_value = _make_sentinel(ROOT, ".trueforge-phase0-checkout-")
        private_runtime = runtime_directory / "private-runtime"
        private_runtime.mkdir()
        private_sentinel, private_value = _make_sentinel(private_runtime, "sentinel-")
        image = _render_cgl_png()
        home_alias = Path(tempfile.mkdtemp(prefix="tf0-home-", dir=tempfile.gettempdir()))
        home_alias.rmdir()
        trueforge = TrueForgeProcess(_free_port(), runtime_directory, home_alias)
        allowed_origin = f"http://localhost:{trueforge.port}"
        facade = DummyFacade("phase0-bearer", allowed_origin, image=image)
        model = ModelServer(image=image)
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
        if not _sentinels_intact(
            [checkout_sentinel, private_sentinel],
            [checkout_value, private_value],
        ):
            raise RuntimeError("boundary sentinels were unavailable before the sandbox attempt")
        sandbox_parent = _trueforge_data_directory(runtime_directory / "home") / "sandboxes" / session["id"]

        def stage_helper(ltr_path: str) -> None:
            nonlocal boundary_helper
            boundary_helper = _stage_boundary_helper(
                sandbox_parent,
                ltr_path,
                checkout_sentinel,
                private_sentinel,
            )

        model.configure_boundary_helper(stage_helper)
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
        _, raw_events_payload = _request(
            trueforge.base_url, "GET", f"/api/v1/sessions/{session['id']}/events?limit=100"
        )
        sentinels_intact = _sentinels_intact(
            [checkout_sentinel, private_sentinel],
            [checkout_value, private_value],
        )
        private_data_clear = not _contains_prohibited_boundary_data(
            (facade.results, model.request_bodies, raw_events_payload),
            [checkout_sentinel, private_sentinel],
            [checkout_value, private_value],
        )
        events_payload = _sanitized_events_payload(raw_events_payload)
        sandbox_call_ids = _tool_call_ids(events_payload, "exec")
        sandbox_evidence = _sandbox_analysis_evidence(
            events_payload, sandbox_call_ids[0] if len(sandbox_call_ids) == 1 else None
        )
        sandbox_evidence["sentinels_intact"] = sentinels_intact
        sandbox_evidence["helper_staged"] = model.boundary_helper_staged
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
        _cleanup_live_probe(
            boundary_helper,
            trueforge,
            model,
            facade,
            home_alias,
            checkout_sentinel,
            runtime_directory,
        )
