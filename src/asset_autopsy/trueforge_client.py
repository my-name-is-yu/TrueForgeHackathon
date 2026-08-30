from __future__ import annotations

import ast
import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .mcp_server import TOOL_NAMES
from .schemas import TOOL_INPUT_MODELS


AGENT_NAME = "asset-autopsy-sc1"
MCP_SERVER_NAME = "asset-autopsy-sc1"
DEFAULT_MODEL = "openai/gpt-5-4-mini"
EXACT_PROMPT = (
    "Autopsy compound-arm-01. Do not change its controller or tests. "
    "Qualify and publish the repaired asset."
)

AGENT_INSTRUCTIONS = """You are operating the pre-provisioned Asset Autopsy asset compound-arm-01. Its public case_id is case_compound-arm-01.
First open the case and run the fixed public task to establish the failing baseline. Inspect only the public authored and compiled values. Before every experiment, register a concrete causal claim, suspected elements, a competing explanation, a prediction, and a falsifier. Choose bounded initial joint positions, constant actuator-control segments, and observables that discriminate those explanations.

After every run_experiment response, use the TrueForge Sandbox exec tool with Python and json.load(open(exact_path)) to read the exact Large Tool Response JSON file. Print one compact JSON object with exactly these keys: {"rows":256,"run_id":"...","metric":"...","finding":"...","candidate_attribute":"joint_name.attribute"}. Use that analysis in the next decision. Do not infer a cause from a tool label, and do not skip the sandbox analysis.

Create only one-attribute revisions, each citing the completed experiment that supports it. Re-run the public task after each revision. Continue until two different causes have been repaired in two immutable child revisions and the public BehaviorDiff is improved. Only after the public task passes may you call verify_revision. If qualification returns 3/3, request publish_revision exactly once with the returned promotion ticket. Do not ask the user questions, do not change the controller or tests, and do not use or request XML, paths, URLs, seeds, timesteps, hidden targets, or hidden traces."""


class TrueForgeError(RuntimeError):
    def __init__(self, message: str, *, status: int = 0, path: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.path = path


Transport = Callable[[str, str, Mapping[str, Any] | None], tuple[int, Mapping[str, Any]]]

_TOOL_INPUT_BY_NAME = dict(zip(TOOL_NAMES, TOOL_INPUT_MODELS, strict=True))
_TOOL_ANNOTATIONS = {
    name: {
        "readOnlyHint": name in {"open_case", "inspect_asset"},
        "destructiveHint": name == "publish_revision",
        "idempotentHint": name
        in {
            "open_case",
            "inspect_asset",
            "create_revision",
            "verify_revision",
            "publish_revision",
        },
        "openWorldHint": False,
    }
    for name in TOOL_NAMES
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _data(payload: Mapping[str, Any]) -> Any:
    return payload.get("data", payload)


def build_agent_spec(model: str = DEFAULT_MODEL) -> dict[str, Any]:
    return {
        "model": {"name": model, "params": {"parallel_tool_calls": False}},
        "instructions": AGENT_INSTRUCTIONS,
        "mcp_servers": [
            {
                "name": MCP_SERVER_NAME,
                "enable_tools": list(TOOL_NAMES),
                "disable_tools": [],
                "preload_tools": [],
                "require_approval_for_tools": ["publish_revision"],
                "preload": True,
            }
        ],
        "config": {
            "iteration_limit": 30,
            "sandbox": {"enabled": True, "file_downloads": True},
            "dynamic_sub_agents": {"enabled": False},
            "context_management": {
                "compaction": {"enabled": True},
                "large_tool_response": {"enabled": True},
            },
            "generative_ui": {"enabled": False},
            "ask_user_questions": {"enabled": False},
        },
    }


@dataclass(frozen=True)
class ProvisionResult:
    agent_id: str
    agent_action: str
    agent_manifest_sha256: str
    hackathon_starter_sha256: str
    models_sha256: str
    tool_schema_sha256: str


class TrueForgeClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8790",
        *,
        transport: Transport | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        if self.base_url not in {"http://127.0.0.1:8790", "http://localhost:8790"}:
            raise ValueError("SC1 TrueForge must use the normal loopback standalone runtime")
        self.timeout_seconds = timeout_seconds
        self._transport = transport or self._urllib_transport

    def _urllib_transport(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None,
    ) -> tuple[int, Mapping[str, Any]]:
        body = None if payload is None else _canonical_bytes(payload)
        request = Request(f"{self.base_url}{path}", data=body, method=method)
        if body is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
                status = response.status
        except HTTPError as error:
            raw = error.read()
            status = error.code
        except (URLError, TimeoutError, OSError) as error:
            raise TrueForgeError("TrueForge is unavailable on the loopback runtime.", path=path) from error
        try:
            decoded = json.loads(raw) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TrueForgeError(
                "TrueForge returned a non-JSON response.", status=status, path=path
            ) from error
        if not isinstance(decoded, Mapping):
            decoded = {"data": decoded}
        return status, decoded

    def _request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        expected: Iterable[int] = (200,),
    ) -> Mapping[str, Any]:
        status, response = self._transport(method, path, payload)
        if status not in set(expected):
            raise TrueForgeError(
                f"TrueForge request failed with HTTP {status}.", status=status, path=path
            )
        return response

    @staticmethod
    def _agents(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        data = _data(payload)
        if not isinstance(data, list) or any(not isinstance(item, Mapping) for item in data):
            raise TrueForgeError("TrueForge returned an invalid agent list.")
        return list(data)

    @staticmethod
    def _named_agent(agents: list[Mapping[str, Any]], name: str) -> Mapping[str, Any] | None:
        matches = [agent for agent in agents if agent.get("name") == name]
        if len(matches) > 1:
            raise TrueForgeError("TrueForge contains duplicate saved agent names.")
        return matches[0] if matches else None

    def list_agents(self) -> list[Mapping[str, Any]]:
        return self._agents(self._request("GET", "/api/v1/agents"))

    def list_models(self) -> list[Mapping[str, Any]]:
        data = _data(self._request("GET", "/api/v1/models"))
        if not isinstance(data, list) or any(not isinstance(item, Mapping) for item in data):
            raise TrueForgeError("TrueForge returned an invalid model list.")
        return list(data)

    def capabilities(self) -> Mapping[str, Any]:
        data = _data(self._request("GET", "/api/v1/capabilities"))
        if not isinstance(data, Mapping):
            raise TrueForgeError("TrueForge returned invalid capabilities.")
        return data

    def _put_mcp_server(self, *, mcp_url: str, bearer: str, origin: str) -> None:
        if mcp_url != "http://127.0.0.1:8712/mcp":
            raise ValueError("SC1 MCP connector URL is fixed")
        if len(bearer) < 16:
            raise ValueError("SC1 MCP bearer is invalid")
        manifest = {
            "type": "remote",
            "name": MCP_SERVER_NAME,
            "url": mcp_url,
            "description": "Asset Autopsy SC1 bounded 3D repair tools",
            "auth": {
                "type": "header",
                "headers": {"Authorization": f"Bearer {bearer}", "Origin": origin},
            },
        }
        self._request(
            "PUT",
            "/api/v1/settings/mcp-servers",
            {"manifest": manifest},
        )

    def _tool_schema_gate(self) -> str:
        payload = self._request("GET", f"/api/v1/mcp-servers/{MCP_SERVER_NAME}/tools")
        tools = _data(payload)
        if not isinstance(tools, list) or any(not isinstance(tool, Mapping) for tool in tools):
            raise TrueForgeError("TrueForge returned an invalid MCP tool list.")
        names = [tool.get("name") for tool in tools]
        if names != list(TOOL_NAMES):
            raise TrueForgeError("The saved MCP connector does not expose the exact SC1 tools.")
        for tool in tools:
            name = tool["name"]
            schema = tool.get("inputSchema")
            if not isinstance(schema, Mapping):
                raise TrueForgeError("An MCP tool input schema is invalid.")
            actual_schema = dict(schema)
            actual_schema.pop("title", None)
            expected_schema = _TOOL_INPUT_BY_NAME[name].model_json_schema(by_alias=True)
            expected_schema.pop("title", None)
            if actual_schema != expected_schema:
                raise TrueForgeError("An MCP tool input schema differs from the SC1 contract.")
            annotations = tool.get("annotations")
            if not isinstance(annotations, Mapping) or {
                key: annotations.get(key) for key in _TOOL_ANNOTATIONS[name]
            } != _TOOL_ANNOTATIONS[name]:
                raise TrueForgeError("An MCP tool annotation differs from the SC1 contract.")
        return canonical_sha256(tools)

    def provision_sc1(
        self,
        *,
        bearer: str,
        origin: str = "http://localhost:8790",
        model: str = DEFAULT_MODEL,
    ) -> ProvisionResult:
        capabilities = self.capabilities()
        sandbox = capabilities.get("sandbox")
        settings = capabilities.get("settings")
        if not isinstance(sandbox, Mapping) or sandbox.get("enabled") is not True:
            raise TrueForgeError("TrueForge sandbox capability is unavailable.")
        if not isinstance(settings, Mapping) or settings.get("enabled") is not True:
            raise TrueForgeError("TrueForge settings capability is unavailable.")

        models_before = self.list_models()
        if model not in {entry.get("name") for entry in models_before}:
            raise TrueForgeError("The saved OpenAI model is unavailable.")
        models_sha = canonical_sha256(models_before)

        agents_before = self.list_agents()
        starter_before = self._named_agent(agents_before, "hackathon-starter")
        if starter_before is None:
            raise TrueForgeError("The existing hackathon-starter agent is unavailable.")
        starter_sha = canonical_sha256(
            {"id": starter_before.get("id"), "manifest": starter_before.get("manifest")}
        )

        self._put_mcp_server(
            mcp_url="http://127.0.0.1:8712/mcp",
            bearer=bearer,
            origin=origin,
        )
        tool_schema_sha = self._tool_schema_gate()

        desired = build_agent_spec(model)
        current = self._named_agent(agents_before, AGENT_NAME)
        if current is None:
            response = self._request(
                "POST",
                "/api/v1/agents",
                {"name": AGENT_NAME, "manifest": desired},
                expected=(201,),
            )
            action = "created"
        elif current.get("manifest") == desired:
            response = {"data": current}
            action = "unchanged"
        else:
            agent_id = current.get("id")
            if not isinstance(agent_id, str) or not agent_id:
                raise TrueForgeError("The SC1 saved agent has an invalid immutable id.")
            response = self._request(
                "PUT",
                f"/api/v1/agents/{quote(agent_id, safe='')}",
                {"manifest": desired},
            )
            action = "updated"
        agent = _data(response)
        if not isinstance(agent, Mapping) or agent.get("name") != AGENT_NAME:
            raise TrueForgeError("TrueForge did not return the dedicated SC1 agent.")
        agent_id = agent.get("id")
        if not isinstance(agent_id, str) or not agent_id:
            raise TrueForgeError("TrueForge returned an invalid SC1 agent id.")
        if agent.get("manifest") != desired:
            raise TrueForgeError("The resolved SC1 AgentSpec differs from the required spec.")

        agents_after = self.list_agents()
        persisted = self._named_agent(agents_after, AGENT_NAME)
        if (
            persisted is None
            or persisted.get("id") != agent_id
            or persisted.get("manifest") != desired
        ):
            raise TrueForgeError("The dedicated SC1 agent was not persisted exactly.")
        starter_after = self._named_agent(agents_after, "hackathon-starter")
        if starter_after is None or canonical_sha256(
            {"id": starter_after.get("id"), "manifest": starter_after.get("manifest")}
        ) != starter_sha:
            raise TrueForgeError("Provisioning changed hackathon-starter.")
        if canonical_sha256(self.list_models()) != models_sha:
            raise TrueForgeError("Provisioning changed the saved model projection.")

        return ProvisionResult(
            agent_id=agent_id,
            agent_action=action,
            agent_manifest_sha256=canonical_sha256(desired),
            hackathon_starter_sha256=starter_sha,
            models_sha256=models_sha,
            tool_schema_sha256=tool_schema_sha,
        )

    def create_session(self) -> Mapping[str, Any]:
        payload = self._request(
            "POST",
            "/api/v1/sessions",
            {"agent": {"name": AGENT_NAME}},
            expected=(200, 201),
        )
        session = _data(payload)
        if not isinstance(session, Mapping) or not isinstance(session.get("id"), str):
            raise TrueForgeError("TrueForge returned an invalid SC1 session.")
        return session

    def create_turn(self, session_id: str, prompt: str = EXACT_PROMPT) -> Mapping[str, Any]:
        if prompt != EXACT_PROMPT:
            raise ValueError("SC1 evidence runs accept only the fixed one-prompt request")
        payload = self._request(
            "POST",
            f"/api/v1/sessions/{quote(session_id, safe='')}/turns",
            {
                "previous_turn_id": "none",
                "stream": False,
                "input": [{"type": "user.message", "content": prompt}],
            },
            expected=(200, 201, 202),
        )
        turn = _data(payload)
        if not isinstance(turn, Mapping) or not isinstance(turn.get("id"), str):
            raise TrueForgeError("TrueForge returned an invalid SC1 turn.")
        return turn

    def get_turn(self, session_id: str, turn_id: str) -> Mapping[str, Any]:
        payload = self._request(
            "GET",
            f"/api/v1/sessions/{quote(session_id, safe='')}/turns/{quote(turn_id, safe='')}",
        )
        turn = _data(payload)
        if not isinstance(turn, Mapping):
            raise TrueForgeError("TrueForge returned an invalid turn state.")
        return turn

    @staticmethod
    def turn_status(turn: Mapping[str, Any]) -> str:
        state = turn.get("state")
        if isinstance(state, Mapping):
            return str(state.get("status", "")).lower()
        return str(turn.get("status", "")).lower()

    def wait_for_turn(
        self,
        session_id: str,
        turn_id: str,
        *,
        timeout_seconds: float = 600.0,
    ) -> Mapping[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        turn: Mapping[str, Any] = {}
        while time.monotonic() < deadline:
            turn = self.get_turn(session_id, turn_id)
            if self.turn_status(turn) in {"done", "error", "failed", "cancelled", "canceled"}:
                return turn
            time.sleep(0.5)
        raise TrueForgeError("The SC1 turn exceeded the bounded evidence timeout.")

    def list_turn_events(self, session_id: str, turn_id: str) -> list[Mapping[str, Any]]:
        events: list[Mapping[str, Any]] = []
        page_token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            query: dict[str, str | int] = {"limit": 100, "order": "asc"}
            if page_token is not None:
                query["page_token"] = page_token
            path = (
                f"/api/v1/sessions/{quote(session_id, safe='')}/turns/"
                f"{quote(turn_id, safe='')}/events?{urlencode(query)}"
            )
            payload = self._request("GET", path)
            data = payload.get("data")
            pagination = payload.get("pagination", {})
            if not isinstance(data, list) or any(not isinstance(event, Mapping) for event in data):
                raise TrueForgeError("TrueForge returned invalid turn events.")
            events.extend(data)
            if not isinstance(pagination, Mapping):
                raise TrueForgeError("TrueForge returned invalid event pagination.")
            next_token = pagination.get("next_page_token")
            if next_token is None:
                return events
            if not isinstance(next_token, str) or not next_token or next_token in seen_tokens:
                raise TrueForgeError("TrueForge returned an invalid event cursor.")
            seen_tokens.add(next_token)
            page_token = next_token


def unwrap_event(item: Mapping[str, Any]) -> Mapping[str, Any]:
    event = item.get("event")
    return event if isinstance(event, Mapping) else item


def model_tool_calls(events: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    calls: list[Mapping[str, Any]] = []
    for item in events:
        event = unwrap_event(item)
        if event.get("type") != "model.message":
            continue
        tool_calls = event.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        calls.extend(call for call in tool_calls if isinstance(call, Mapping))
    return calls


def tool_call_name(call: Mapping[str, Any]) -> str:
    function = call.get("function")
    return str(function.get("name", "")) if isinstance(function, Mapping) else ""


def approval_call_ids(events: Iterable[Mapping[str, Any]]) -> list[str]:
    ids: list[str] = []
    for item in events:
        event = unwrap_event(item)
        if event.get("type") != "tool.approval_required":
            continue
        calls = event.get("tool_calls")
        if not isinstance(calls, list):
            continue
        ids.extend(
            str(call["id"])
            for call in calls
            if isinstance(call, Mapping) and isinstance(call.get("id"), str)
        )
    return ids


def tool_responses(events: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    responses: dict[str, Mapping[str, Any]] = {}
    for item in events:
        event = unwrap_event(item)
        if event.get("type") != "tool.response":
            continue
        call_id = event.get("tool_call_id")
        if isinstance(call_id, str):
            responses[call_id] = event
    return responses


def _call_records(events: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for event_index, item in enumerate(events):
        event = unwrap_event(item)
        if event.get("type") != "model.message":
            continue
        calls = event.get("tool_calls")
        if not isinstance(calls, list):
            continue
        for call in calls:
            if not isinstance(call, Mapping) or not isinstance(call.get("id"), str):
                continue
            records.append(
                {
                    "event_index": event_index,
                    "id": call["id"],
                    "name": tool_call_name(call),
                    "call": call,
                }
            )
    return records


def _response_records(events: list[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for event_index, item in enumerate(events):
        event = unwrap_event(item)
        if event.get("type") != "tool.response" or not isinstance(event.get("tool_call_id"), str):
            continue
        records[event["tool_call_id"]] = {"event_index": event_index, "event": event}
    return records


def _json_objects(value: Any, *, depth: int = 0) -> list[Mapping[str, Any]]:
    if depth > 8:
        return []
    if isinstance(value, Mapping):
        nested: list[Mapping[str, Any]] = [value]
        for child in value.values():
            nested.extend(_json_objects(child, depth=depth + 1))
        return nested
    if isinstance(value, list):
        nested = []
        for child in value:
            nested.extend(_json_objects(child, depth=depth + 1))
        return nested
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if parsed == value:
            return []
        return _json_objects(parsed, depth=depth + 1)
    return []


def _response_payload(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    event = record.get("event")
    if not isinstance(event, Mapping):
        return None
    candidates = _json_objects(event.get("content"))
    for candidate in candidates:
        if "schema_version" in candidate or "rows" in candidate or "public_result" in candidate:
            return candidate
    return candidates[-1] if candidates else None


def _call_arguments(call: Mapping[str, Any]) -> Mapping[str, Any]:
    function = call.get("function")
    if not isinstance(function, Mapping):
        return {}
    arguments = function.get("arguments")
    if isinstance(arguments, Mapping):
        return arguments
    if not isinstance(arguments, str):
        return {}
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _sandbox_python_reads_json(arguments: Mapping[str, Any], path: str) -> bool:
    language = arguments.get("language")
    code = arguments.get("code")
    if not isinstance(language, str) or language.lower() != "python" or not isinstance(code, str):
        return False
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False

    string_bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.Assign, ast.AnnAssign))
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    string_bindings[target.id] = node.value.value

    def resolved_string(node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return string_bindings.get(node.id)
        return None

    reads_path = False
    parses_json = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "open"
            and node.args
            and resolved_string(node.args[0]) == path
        ):
            reads_path = True
        if isinstance(node.func, ast.Attribute):
            if (
                node.func.attr in {"open", "read_text", "read_bytes"}
                and isinstance(node.func.value, ast.Call)
                and isinstance(node.func.value.func, ast.Name)
                and node.func.value.func.id == "Path"
                and node.func.value.args
                and resolved_string(node.func.value.args[0]) == path
            ):
                reads_path = True
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "json"
                and node.func.attr in {"load", "loads"}
            ):
                parses_json = True
    return reads_path and parses_json


def evaluate_sc1_events(events: list[Mapping[str, Any]]) -> dict[str, Any]:
    calls = _call_records(events)
    responses = _response_records(events)
    public_calls = [record for record in calls if record["name"] in TOOL_NAMES]
    experiments = [record for record in calls if record["name"] == "run_experiment"]
    revisions = [record for record in calls if record["name"] == "create_revision"]
    task_calls = [record for record in calls if record["name"] == "run_task"]
    verify_calls = [record for record in calls if record["name"] == "verify_revision"]
    publish_calls = [record for record in calls if record["name"] == "publish_revision"]
    exec_calls = [record for record in calls if record["name"] == "exec"]

    failures: list[str] = []
    for index, record in enumerate(public_calls):
        model = _TOOL_INPUT_BY_NAME[record["name"]]
        try:
            model.model_validate(_call_arguments(record["call"]))
        except ValueError:
            failures.append(f"{record['name']} has arguments outside its exact public schema")
        if record["name"] == "publish_revision":
            continue
        response = responses.get(record["id"])
        next_public_index = (
            public_calls[index + 1]["event_index"]
            if index + 1 < len(public_calls)
            else len(events)
        )
        if (
            response is None
            or response["event_index"] <= record["event_index"]
            or response["event_index"] >= next_public_index
        ):
            failures.append(f"{record['name']} lacks an ordered tool response")
    if len(experiments) < 2:
        failures.append("agent did not run at least two experiments")
    if len(revisions) != 2:
        failures.append("agent did not create exactly two revisions")

    sandbox_evidence: list[dict[str, Any]] = []
    for experiment_index, experiment in enumerate(experiments):
        response = responses.get(experiment["id"])
        if response is None:
            failures.append("an experiment has no tool response")
            continue
        content = str(response["event"].get("content", ""))
        match = re.search(r"Content too large\. Result saved to:\s*([^\s]+)", content)
        if match is None:
            failures.append("an experiment response was not moved by Large Tool Response")
            continue
        ltr_path = match.group(1).rstrip(".\"')")
        next_public_index = min(
            (
                record["event_index"]
                for record in public_calls
                if record["event_index"] > response["event_index"]
            ),
            default=len(events),
        )
        matching_exec = next(
            (
                record
                for record in exec_calls
                if record["event_index"] > response["event_index"]
                and record["event_index"] < next_public_index
                and _sandbox_python_reads_json(_call_arguments(record["call"]), ltr_path)
            ),
            None,
        )
        if matching_exec is None:
            failures.append("an offloaded experiment was not read by Sandbox Python")
            continue
        exec_response = responses.get(matching_exec["id"])
        analysis = _response_payload(exec_response) if exec_response is not None else None
        if analysis is None:
            failures.append("a Sandbox Python analysis has no parseable response")
            continue
        required = ("run_id", "metric", "finding", "candidate_attribute")
        if analysis.get("rows") != 256 or any(not isinstance(analysis.get(key), str) for key in required):
            failures.append("a Sandbox Python analysis lacks the compact 256-row evidence")
            continue
        sandbox_evidence.append(
            {
                "experiment_index": experiment_index,
                "event_index": exec_response["event_index"],
                "run_id": analysis["run_id"],
                "run_id_hash": _short_hash(str(analysis["run_id"])),
                "metric": analysis["metric"],
                "candidate_attribute": analysis["candidate_attribute"],
            }
        )

    sandbox_by_run = {
        evidence["run_id"]: evidence
        for evidence in sandbox_evidence
        if isinstance(evidence.get("run_id"), str)
    }
    if len(sandbox_by_run) != len(sandbox_evidence):
        failures.append("Sandbox analyses did not identify distinct experiment runs")

    revision_attributes: list[str] = []
    for revision in revisions:
        arguments = _call_arguments(revision["call"])
        basis_run_id = arguments.get("basis_experiment_run_id")
        basis = sandbox_by_run.get(basis_run_id)
        if basis is None or basis["event_index"] >= revision["event_index"]:
            failures.append("a revision is not causally bound to its Sandbox-analyzed experiment")
            continue
        patch = arguments.get("patch")
        target = patch.get("target") if isinstance(patch, Mapping) else None
        target_name = target.get("name") if isinstance(target, Mapping) else None
        attribute = patch.get("attribute") if isinstance(patch, Mapping) else None
        if not isinstance(target_name, str) or not isinstance(attribute, str):
            failures.append("a revision call does not contain one public attribute patch")
            continue
        patch_attribute = f"{target_name}.{attribute}"
        if basis.get("candidate_attribute") != patch_attribute:
            failures.append("a revision patch does not match its Sandbox candidate attribute")
            continue
        response = responses.get(revision["id"])
        payload = _response_payload(response) if response is not None else None
        canonical_diff = payload.get("canonical_diff") if isinstance(payload, Mapping) else None
        if not isinstance(canonical_diff, list) or len(canonical_diff) != 1:
            failures.append("a revision response does not prove exactly one changed attribute")
            continue
        diff = canonical_diff[0]
        if (
            not isinstance(diff, Mapping)
            or diff.get("target") != target_name
            or diff.get("attribute") != attribute
        ):
            failures.append("a revision response does not match the requested attribute")
            continue
        revision_attributes.append(patch_attribute)
    if len(set(revision_attributes)) != 2:
        failures.append("the two revisions did not repair two different attributes")

    task_payloads: list[Mapping[str, Any]] = []
    for record in task_calls:
        response = responses.get(record["id"])
        payload = _response_payload(response) if response is not None else None
        if payload is not None:
            task_payloads.append(payload)
    baseline_failed = bool(task_payloads) and task_payloads[0].get("result") == "fail"
    if not baseline_failed:
        failures.append("the first public task is not a failing baseline")
    final_task = task_payloads[-1] if task_payloads else {}
    behavior_diff = final_task.get("behavior_diff")
    final_public_pass = final_task.get("result") == "pass"
    behavior_improved = (
        isinstance(behavior_diff, Mapping)
        and behavior_diff.get("verdict") == "public_pass"
        and behavior_diff.get("changed") is True
    )
    if not final_public_pass or not behavior_improved:
        failures.append("the final public task does not contain an improved passing BehaviorDiff")

    verify_response = None
    verify_payload = None
    if len(verify_calls) == 1 and verify_calls[0]["id"] in responses:
        verify_response = responses[verify_calls[0]["id"]]
        verify_payload = _response_payload(verify_response)
    public_result = verify_payload.get("public_result") if isinstance(verify_payload, Mapping) else None
    holdout_result = verify_payload.get("holdout_result") if isinstance(verify_payload, Mapping) else None
    hidden_pass = (
        isinstance(public_result, Mapping)
        and public_result.get("passed") == 1
        and public_result.get("total") == 1
        and isinstance(holdout_result, Mapping)
        and holdout_result.get("passed") == 3
        and holdout_result.get("total") == 3
    )
    if not hidden_pass:
        failures.append("qualification did not return public 1/1 and hidden 3/3")

    approval_ids = approval_call_ids(events)
    publish_id = publish_calls[0]["id"] if len(publish_calls) == 1 else None
    approval_event_indices = [
        event_index
        for event_index, item in enumerate(events)
        if unwrap_event(item).get("type") == "tool.approval_required"
    ]
    publish_is_final = (
        len(publish_calls) == 1
        and bool(public_calls)
        and public_calls[-1]["id"] == publish_calls[0]["id"]
    )
    approval_match = (
        publish_id is not None
        and approval_ids == [publish_id]
        and len(approval_event_indices) == 1
        and approval_event_indices[0] > publish_calls[0]["event_index"]
    )
    publish_response_absent = publish_id is not None and publish_id not in responses
    qualification_precedes_publish = (
        verify_response is not None
        and len(publish_calls) == 1
        and verify_response["event_index"] < publish_calls[0]["event_index"]
    )
    verified_ticket = (
        verify_payload.get("promotion_ticket")
        if isinstance(verify_payload, Mapping)
        else None
    )
    published_ticket = (
        _call_arguments(publish_calls[0]["call"]).get("promotion_ticket")
        if len(publish_calls) == 1
        else None
    )
    verified_ticket_used = (
        isinstance(verified_ticket, Mapping)
        and published_ticket == verified_ticket
    )
    if not qualification_precedes_publish or not verified_ticket_used:
        failures.append(
            "publish is not bound to the preceding successful qualification ticket"
        )
    if not publish_is_final or not approval_match or not publish_response_absent:
        failures.append("publish did not stop at exactly one matching approval requirement")

    return {
        "passed": not failures,
        "failures": failures,
        "tool_order": [record["name"] for record in public_calls],
        "experiment_count": len(experiments),
        "revision_count": len(revisions),
        "large_tool_response": {
            "offloaded_experiments": len(sandbox_evidence),
            "all_read_by_sandbox_python": len(sandbox_evidence) == len(experiments),
        },
        "sandbox": {
            "exec_calls": len(exec_calls),
            "all_rows_256": len(sandbox_evidence) == len(experiments),
            "runs": [
                {
                    "run_id_hash": evidence["run_id_hash"],
                    "metric": evidence["metric"],
                    "candidate_attribute": evidence["candidate_attribute"],
                }
                for evidence in sandbox_evidence
            ],
        },
        "public": {
            "baseline_failed": baseline_failed,
            "final_passed": final_public_pass,
            "behavior_diff_improved": behavior_improved,
        },
        "hidden": {"passed": 3 if hidden_pass else 0, "total": 3},
        "approval": {
            "publish_requests": len(publish_calls),
            "publish_is_final_public_call": publish_is_final,
            "qualification_precedes_publish": qualification_precedes_publish,
            "verified_ticket_used": verified_ticket_used,
            "matching_approval_required": approval_match,
            "publish_response_absent": publish_response_absent,
        },
    }


__all__ = [
    "AGENT_INSTRUCTIONS",
    "AGENT_NAME",
    "DEFAULT_MODEL",
    "EXACT_PROMPT",
    "MCP_SERVER_NAME",
    "ProvisionResult",
    "TrueForgeClient",
    "TrueForgeError",
    "approval_call_ids",
    "build_agent_spec",
    "canonical_sha256",
    "evaluate_sc1_events",
    "model_tool_calls",
    "tool_call_name",
    "tool_responses",
    "unwrap_event",
]
