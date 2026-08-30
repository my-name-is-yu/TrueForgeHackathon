from __future__ import annotations

import ast
import hashlib
import json
import math
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
Follow this mandatory evidence loop in the same turn: open_case; failing run_task baseline on r000; inspect_asset; run_experiment; Sandbox exec analysis; create_revision; run_task; a second run_experiment; Sandbox exec analysis; a second create_revision; final run_task; verify_revision; one publish_revision request. A run_task is only the fixed public check: it is never an experiment and its run ID or artifact cannot support create_revision.

Before every run_experiment, register a concrete causal claim, suspected elements, a competing explanation, a prediction, and a falsifier. Isolate a suspected joint from neutral positions and choose controls and qpos, qvel, energy, contact_count, or body_position observations that discriminate the explanations. Name joint_a, joint_b, and joint_c exactly once in initial_joint_positions. Name motor_a, motor_b, and motor_c exactly once in every segment, and use at least 256 total steps.

Before choosing each experiment, use only public inspect and task evidence to rank at least two competing causal hypotheses. Choose an intervention and requested non-control observation whose measured outcome can falsify one explanation while supporting the other. Treat authored patterns as hypotheses, not diagnoses, until the trace discriminates them. Use public topology and metadata only to justify which elements are comparable; do not infer a repair from repeated values, a model-wide majority, or element names alone.

Use this exact run_experiment structure. REVISION_ID, SUSPECTED_JOINT, SUSPECTED_ATTRIBUTE, COMPETING_JOINT, and COMPETING_ATTRIBUTE are metavariables, not literal values: replace every one from the current head, inspect_asset, and your ranking before calling the tool. The first REVISION_ID is r000; after create_revision use that latest child revision. Replace neutral numeric examples with an informative isolated excitation; all-zero controls are useful only when a nonzero initial state is itself the intervention. The request root contains exactly case_id, revision_id, hypothesis, initial_joint_positions, segments, observables, and capture_final_snapshot. Hypothesis contains exactly claim, suspected_elements, competing_explanation, prediction, and falsifier. Competing_explanation contains exactly claim, suspected_elements, and discriminating_reason. Close competing_explanation before prediction and falsifier, then close hypothesis before initial_joint_positions. Never move root fields under hypothesis or prediction/falsifier under competing_explanation.
{"case_id":"case_compound-arm-01","revision_id":"REVISION_ID","hypothesis":{"claim":"...","suspected_elements":[{"kind":"joint","name":"SUSPECTED_JOINT","attributes":["SUSPECTED_ATTRIBUTE"]}],"competing_explanation":{"claim":"...","suspected_elements":[{"kind":"joint","name":"COMPETING_JOINT","attributes":["COMPETING_ATTRIBUTE"]}],"discriminating_reason":"..."},"prediction":"...","falsifier":"..."},"initial_joint_positions":[{"joint_name":"joint_a","position_rad":0.0},{"joint_name":"joint_b","position_rad":0.0},{"joint_name":"joint_c","position_rad":0.0}],"segments":[{"label":"isolate","n_steps":512,"controls":[{"actuator_name":"motor_a","value":0.0},{"actuator_name":"motor_b","value":0.0},{"actuator_name":"motor_c","value":0.0}]}],"observables":[{"kind":"qpos"},{"kind":"qvel"},{"kind":"energy"},{"kind":"body_position","body_name":"end_effector"}],"capture_final_snapshot":false}

After every run_experiment response, copy the exact `Result saved to:` path into a TrueForge Sandbox exec command using `python - <<'PY'` and `payload=json.load(open(exact_path))`. Actually analyze that JSON; do not merely mention the path or fabricate a result. The loaded root is the full run_experiment output: assign `rows=payload["trace"]["rows"]`. Each row is shaped as {"time_s": number, "values": {"qpos:joint_name": number, "control:actuator_name": number, ...}}. Build a list from one exact named signal key, optionally applying abs to each selected value. Compute a direct aggregate such as max(signal), mean(signal), or max(signal)-min(signal); do not replace or transform the aggregate afterward. Print one compact JSON object with exactly these keys: {"rows":len(rows),"run_id":payload["run_id"],"metric":f"signal_name={computed_value:.8g}","finding":"your evidence-bounded interpretation","candidate_attribute":"joint_name.attribute"}. The metric string must include the computed numeric aggregate. Additional Sandbox inspection is allowed. If it changes your conclusion, run the exact compact analysis again with the final candidate. Immediately before create_revision, the last compact analysis for that experiment must name the same candidate_attribute as the patch. Never paste the 256 rows back into context. Use the exact hypothesis_id and run_id returned by that experiment in the next create_revision. A revision patch has target {"kind":"joint","name":"..."}, one attribute, expected_old_value, and new_value; expected_effect has scenario_id `public_center` and one or more metric predicates.

For create_revision, copy base_revision_id and expected_base_sha256 from open_case or the preceding revision, and copy basis_hypothesis_id and basis_experiment_run_id from the completed run_experiment. Use patch {"target":{"kind":"joint","name":"..."},"attribute":"...","expected_old_value":...,"new_value":...} and expected_effect {"scenario_id":"public_center","predicates":[{"metric":"hold_error_p95_m","op":"lte","value":0.03}]}. Choose the candidate value from public evidence and the analyzed experiment, not an arbitrary guess; only two revisions are available.

Create one attribute per revision and re-run the public task after each. Continue until two different causes have been repaired in two immutable child revisions and the public BehaviorDiff passes. Only then call verify_revision. If qualification returns 3/3, request publish_revision exactly once with the returned promotion ticket. If a bounded tool call returns INVALID_REQUEST, read every returned field path and error type, compare the rejected call with the exact keysets above, and make one corrected retry; do not guess repeatedly. Never stop, use Sandbox to call MCP tools, or offer to continue later. Do not ask the user questions, do not change the controller or tests, and do not use or request XML, host paths, URLs, seeds, timesteps, hidden targets, or hidden traces."""


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
        "model": {
            "name": model,
            "params": {
                "parallel_tool_calls": False,
                "reasoning_effort": "high",
            },
        },
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


def _response_error_code(record: Mapping[str, Any]) -> str | None:
    event = record.get("event")
    if not isinstance(event, Mapping):
        return None
    for candidate in _json_objects(event.get("content")):
        code = candidate.get("code")
        if isinstance(code, str):
            return code
    return None


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


@dataclass(frozen=True)
class SandboxAnalysisProof:
    candidate_attribute: str
    finding: str
    metric_prefix: str
    signal_key: str


_FINITE_METRIC_NUMBER = re.compile(
    r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
)
_PATCHABLE_HYPOTHESIS_ATTRIBUTES = frozenset(
    {"axis", "damping", "armature", "frictionloss"}
)


def _metric_matches_proof(value: Any, prefix: str) -> bool:
    if not isinstance(value, str) or not value.startswith(prefix):
        return False
    number = value[len(prefix) :]
    if _FINITE_METRIC_NUMBER.fullmatch(number) is None:
        return False
    try:
        return math.isfinite(float(number))
    except ValueError:
        return False


def _experiment_candidate_attributes(arguments: Mapping[str, Any]) -> set[str]:
    hypothesis = arguments.get("hypothesis")
    if not isinstance(hypothesis, Mapping):
        return set()
    competing = hypothesis.get("competing_explanation")
    references: list[Any] = []
    for container in (hypothesis, competing):
        if not isinstance(container, Mapping):
            continue
        suspected = container.get("suspected_elements")
        if isinstance(suspected, list):
            references.extend(suspected)
    candidates: set[str] = set()
    for reference in references:
        if not isinstance(reference, Mapping) or reference.get("kind") != "joint":
            continue
        name = reference.get("name")
        attributes = reference.get("attributes")
        if not isinstance(name, str) or not isinstance(attributes, list):
            continue
        candidates.update(
            f"{name}.{attribute}"
            for attribute in attributes
            if attribute in _PATCHABLE_HYPOTHESIS_ATTRIBUTES
        )
    return candidates


def _experiment_observable_signals(arguments: Mapping[str, Any]) -> set[str]:
    positions = arguments.get("initial_joint_positions")
    observables = arguments.get("observables")
    if not isinstance(positions, list) or not isinstance(observables, list):
        return set()
    joint_names = {
        name
        for position in positions
        if isinstance(position, Mapping)
        and isinstance((name := position.get("joint_name")), str)
    }
    signals: set[str] = set()
    for observable in observables:
        if not isinstance(observable, Mapping):
            continue
        kind = observable.get("kind")
        if kind in {"qpos", "qvel"}:
            signals.update(f"{kind}:{name}" for name in joint_names)
        elif kind == "energy":
            signals.update({"energy:potential", "energy:kinetic"})
        elif kind == "contact_count":
            signals.add("contact_count")
        elif kind == "body_position" and isinstance(
            body_name := observable.get("body_name"), str
        ):
            signals.update(
                f"body_position:{body_name}:{axis}" for axis in ("x", "y", "z")
            )
    return signals


def _sandbox_python_reads_json(
    arguments: Mapping[str, Any], path: str
) -> SandboxAnalysisProof | None:
    language = arguments.get("language")
    code = arguments.get("code")
    command = arguments.get("command")
    source: str | None = None
    if command is not None:
        if not isinstance(command, str) or code is not None or language is not None:
            return None
        heredoc = re.compile(
            r"\s*python(?:3(?:\.\d+)?)?\s+-\s+"
            r"<<(?P<quote>['\"])(?P<tag>[A-Za-z_][A-Za-z0-9_]*)(?P=quote)"
            r"[ \t]*\r?\n(?P<source>.*?)\r?\n(?P=tag)[ \t]*\s*",
            re.DOTALL,
        )
        match = heredoc.fullmatch(command)
        if match is None:
            return None
        source = match.group("source")
    elif (
        isinstance(language, str)
        and language.lower() == "python"
        and isinstance(code, str)
    ):
        source = code
    if source is None:
        return None
    return _python_source_reads_json(source, path)


def _python_source_reads_json(
    code: str, path: str
) -> SandboxAnalysisProof | None:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None

    statistics_functions = {
        "fmean",
        "mean",
        "median",
        "pstdev",
        "stdev",
        "variance",
    }
    imported_modules: set[str] = set()
    imported_statistics_functions: set[str] = set()
    print_statements: list[ast.Expr] = []
    assignment_targets: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            if any(
                alias.asname is not None or alias.name not in {"json", "statistics"}
                for alias in statement.names
            ):
                return None
            imported_modules.update(alias.name for alias in statement.names)
            continue
        if isinstance(statement, ast.ImportFrom):
            if statement.module != "statistics" or any(
                alias.asname is not None or alias.name not in statistics_functions
                for alias in statement.names
            ):
                return None
            imported_statistics_functions.update(
                alias.name for alias in statement.names
            )
            continue
        if isinstance(statement, ast.Assign):
            if len(statement.targets) != 1 or not isinstance(
                statement.targets[0], ast.Name
            ):
                return None
            target_name = statement.targets[0].id
            if target_name in assignment_targets or target_name in {
                "abs",
                "json",
                "len",
                "max",
                "min",
                "open",
                "print",
                "statistics",
                "sum",
                *statistics_functions,
            }:
                return None
            assignment_targets.add(target_name)
            continue
        if (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Name)
            and statement.value.func.id == "print"
        ):
            if len(statement.value.args) != 1 or statement.value.keywords:
                return None
            print_statements.append(statement)
            continue
        return None
    if (
        imported_modules.isdisjoint({"json"})
        or len(print_statements) != 1
        or tree.body[-1] is not print_statements[0]
    ):
        return None

    assignments: dict[str, ast.expr] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            if isinstance(target, ast.Name):
                assignments[target.id] = statement.value
    if (
        set(assignments) & imported_modules
        or set(assignments) & imported_statistics_functions
    ):
        return None

    def resolved_expression(node: ast.expr, depth: int = 0) -> ast.expr:
        if depth > 8:
            return node
        if isinstance(node, ast.Name) and node.id in assignments:
            return resolved_expression(assignments[node.id], depth + 1)
        return node

    def resolved_string(
        node: ast.AST, seen: frozenset[str] = frozenset()
    ) -> str | None:
        if isinstance(node, ast.Name) and node.id in assignments:
            if node.id in seen or len(seen) >= 8:
                return None
            return resolved_string(assignments[node.id], seen | {node.id})
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    def subscript_key(node: ast.AST) -> str | None:
        if not isinstance(node, ast.Subscript):
            return None
        return resolved_string(node.slice)

    def is_json_load(node: ast.expr) -> bool:
        expression = resolved_expression(node)
        if not (
            isinstance(expression, ast.Call)
            and isinstance(expression.func, ast.Attribute)
            and isinstance(expression.func.value, ast.Name)
            and expression.func.value.id == "json"
            and expression.func.attr == "load"
            and len(expression.args) == 1
            and not expression.keywords
        ):
            return False
        opened = expression.args[0]
        return (
            isinstance(opened, ast.Call)
            and isinstance(opened.func, ast.Name)
            and opened.func.id == "open"
            and "open" not in assignments
            and len(opened.args) == 1
            and not opened.keywords
            and resolved_string(opened.args[0]) == path
        )

    payload_names = {
        name for name, expression in assignments.items() if is_json_load(expression)
    }
    if not payload_names:
        return None

    def is_trace_rows(node: ast.expr, payload_name: str) -> bool:
        expression = resolved_expression(node)
        if subscript_key(expression) != "rows":
            return False
        trace = expression.value
        return (
            subscript_key(trace) == "trace"
            and isinstance(trace, ast.Subscript)
            and isinstance(trace.value, ast.Name)
            and trace.value.id == payload_name
        )

    rows_to_payload: dict[str, str] = {}
    for name, expression in assignments.items():
        for payload_name in payload_names:
            if is_trace_rows(expression, payload_name):
                rows_to_payload[name] = payload_name
                break
    if not rows_to_payload:
        return None

    def series_signal_key(node: ast.expr, rows_name: str) -> str | None:
        expression = resolved_expression(node)
        if not isinstance(expression, ast.ListComp) or len(expression.generators) != 1:
            return None
        generator = expression.generators[0]
        if (
            not isinstance(generator.target, ast.Name)
            or generator.target.id in assignments
            or not isinstance(generator.iter, ast.Name)
            or generator.iter.id != rows_name
            or generator.is_async
        ):
            return None
        element = expression.elt
        if (
            isinstance(element, ast.Call)
            and isinstance(element.func, ast.Name)
            and element.func.id == "abs"
            and "abs" not in assignments
            and len(element.args) == 1
            and not element.keywords
        ):
            element = element.args[0]
        if not isinstance(element, ast.Subscript):
            return None
        key = subscript_key(element)
        values = element.value
        if (
            key is None
            or not (
                key == "contact_count"
                or re.fullmatch(
                    r"(?:qpos|qvel|energy|body_position|control):"
                    r"[A-Za-z0-9_.:-]+",
                    key,
                )
            )
            or subscript_key(values) != "values"
            or not isinstance(values, ast.Subscript)
            or not isinstance(values.value, ast.Name)
            or values.value.id != generator.target.id
        ):
            return None
        for condition in generator.ifs:
            if not (
                isinstance(condition, ast.Compare)
                and len(condition.ops) == 1
                and isinstance(condition.ops[0], ast.In)
                and len(condition.comparators) == 1
                and resolved_string(condition.left) == key
                and subscript_key(condition.comparators[0]) == "values"
                and isinstance(condition.comparators[0], ast.Subscript)
                and isinstance(condition.comparators[0].value, ast.Name)
                and condition.comparators[0].value.id == generator.target.id
            ):
                return None
        return key

    series_to_signal: dict[str, str] = {}
    series_to_rows: dict[str, str] = {}
    for name, expression in assignments.items():
        for rows_name in rows_to_payload:
            signal_key = series_signal_key(expression, rows_name)
            if signal_key is not None:
                series_to_signal[name] = signal_key
                series_to_rows[name] = rows_name
                break
    if not series_to_signal:
        return None

    builtin_aggregate_names = {"max", "min", "sum"}

    def direct_series_name(node: ast.expr) -> str | None:
        if isinstance(node, ast.Name) and node.id in series_to_signal:
            return node.id
        return None

    def aggregate_series_name(node: ast.expr) -> str | None:
        if (
            not isinstance(node, ast.Call)
            or len(node.args) != 1
            or node.keywords
        ):
            return None
        if isinstance(node.func, ast.Name):
            name = node.func.id
            valid_function = (
                name in builtin_aggregate_names and name not in assignments
            ) or name in imported_statistics_functions
        elif (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
        ):
            valid_function = (
                node.func.value.id == "statistics"
                and "statistics" in imported_modules
                and node.func.attr in statistics_functions
            )
        else:
            valid_function = False
        if not valid_function:
            return None
        return direct_series_name(node.args[0])

    def metric_series_name(node: ast.expr) -> str | None:
        expression = resolved_expression(node)
        direct = aggregate_series_name(expression)
        if direct is not None:
            return direct
        if isinstance(expression, ast.IfExp):
            guarded = aggregate_series_name(expression.body)
            if (
                guarded is not None
                and isinstance(expression.test, ast.Name)
                and expression.test.id == guarded
                and isinstance(expression.orelse, ast.Constant)
                and expression.orelse.value is None
            ):
                return guarded
            return None
        if not isinstance(expression, ast.BinOp) or not isinstance(
            expression.op, ast.Sub
        ):
            return None
        left = aggregate_series_name(expression.left)
        right = aggregate_series_name(expression.right)
        return left if left is not None and left == right else None

    metric_to_series = {
        name: series_name
        for name, expression in assignments.items()
        if (series_name := metric_series_name(expression)) is not None
    }
    if not metric_to_series:
        return None

    def is_len_of(node: ast.expr, name: str) -> bool:
        expression = resolved_expression(node)
        return (
            isinstance(expression, ast.Call)
            and isinstance(expression.func, ast.Name)
            and expression.func.id == "len"
            and len(expression.args) == 1
            and not expression.keywords
            and isinstance(expression.args[0], ast.Name)
            and expression.args[0].id == name
        )

    def is_payload_run_id(node: ast.expr, payload_name: str) -> bool:
        expression = resolved_expression(node)
        return (
            subscript_key(expression) == "run_id"
            and isinstance(expression, ast.Subscript)
            and isinstance(expression.value, ast.Name)
            and expression.value.id == payload_name
        )

    def joined_metric_prefix(
        expression: ast.expr,
    ) -> tuple[str, str] | None:
        if not isinstance(expression, ast.JoinedStr):
            return None
        metric_values = [
            value
            for value in expression.values
            if (
                isinstance(value, ast.FormattedValue)
                and value.conversion == -1
                and isinstance(value.value, ast.Name)
                and value.value.id in metric_to_series
                and isinstance(value.format_spec, ast.JoinedStr)
                and len(value.format_spec.values) == 1
                and isinstance(value.format_spec.values[0], ast.Constant)
                and value.format_spec.values[0].value == ".8g"
            )
        ]
        if len(metric_values) != 1:
            return None
        metric_value = metric_values[0]
        metric_name = metric_value.value.id
        signal_key = series_to_signal[metric_to_series[metric_name]]
        prefix_parts: list[str] = []
        for value in expression.values:
            if value is metric_value:
                if value is not expression.values[-1]:
                    return None
                break
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                prefix_parts.append(value.value)
                continue
            if (
                isinstance(value, ast.FormattedValue)
                and value.conversion == -1
                and value.format_spec is None
                and resolved_string(value.value) == signal_key
            ):
                prefix_parts.append(signal_key)
                continue
            return None
        prefix = "".join(prefix_parts)
        if prefix != f"{signal_key}=":
            return None
        return prefix, metric_name

    def rendered_null_fallback(node: ast.expr, prefix: str) -> bool:
        expression = resolved_expression(node)
        if isinstance(expression, ast.Constant) and isinstance(
            expression.value, str
        ):
            return expression.value == f"{prefix}null"
        if not isinstance(expression, ast.JoinedStr):
            return False
        parts: list[str] = []
        for value in expression.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
                continue
            if (
                isinstance(value, ast.FormattedValue)
                and value.conversion == -1
                and value.format_spec is None
                and (rendered := resolved_string(value.value)) is not None
            ):
                parts.append(rendered)
                continue
            return False
        return "".join(parts) == f"{prefix}null"

    def metric_prefix(node: ast.expr) -> tuple[str, str] | None:
        expression = resolved_expression(node)
        if not isinstance(expression, ast.IfExp):
            return joined_metric_prefix(expression)
        metric = joined_metric_prefix(expression.body)
        if metric is None:
            return None
        prefix, metric_name = metric
        test = expression.test
        if not (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == metric_name
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.IsNot)
            and len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value is None
            and rendered_null_fallback(expression.orelse, prefix)
        ):
            return None
        return metric

    required_keys = {"rows", "run_id", "metric", "finding", "candidate_attribute"}

    def safe_json_dumps_keywords(call: ast.Call) -> bool:
        seen: set[str] = set()
        for keyword in call.keywords:
            name = keyword.arg
            if name is None or name in seen:
                return False
            seen.add(name)
            if name in {"sort_keys", "ensure_ascii"}:
                if not (
                    isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, bool)
                ):
                    return False
                continue
            if name == "separators":
                value = keyword.value
                if not (
                    isinstance(value, ast.Tuple)
                    and len(value.elts) == 2
                    and all(
                        isinstance(item, ast.Constant)
                        and isinstance(item.value, str)
                        and len(item.value) <= 4
                        for item in value.elts
                    )
                ):
                    return False
                continue
            return False
        return True

    def reachable_assignment_names(node: ast.AST) -> set[str]:
        reachable: set[str] = set()
        pending = [node]
        while pending:
            expression = pending.pop()
            for child in ast.walk(expression):
                if (
                    isinstance(child, ast.Name)
                    and child.id in assignments
                    and child.id not in reachable
                ):
                    reachable.add(child.id)
                    pending.append(assignments[child.id])
        return reachable

    for statement in print_statements:
        if not statement.value.args:
            continue
        printed = resolved_expression(statement.value.args[0])
        if (
            isinstance(printed, ast.Call)
            and isinstance(printed.func, ast.Attribute)
            and isinstance(printed.func.value, ast.Name)
            and printed.func.value.id == "json"
            and printed.func.attr == "dumps"
        ):
            if len(printed.args) != 1 or not safe_json_dumps_keywords(printed):
                continue
            printed = resolved_expression(printed.args[0])
        if not isinstance(printed, ast.Dict):
            continue
        keys = [resolved_string(key) for key in printed.keys]
        if None in keys or set(keys) != required_keys or len(keys) != len(required_keys):
            continue
        fields = dict(zip(keys, printed.values, strict=True))
        candidate = resolved_string(fields["candidate_attribute"])
        finding = resolved_string(fields["finding"])
        metric = metric_prefix(fields["metric"])
        if (
            candidate is None
            or re.fullmatch(
                r"[A-Za-z][A-Za-z0-9_.-]*\."
                r"(?:axis|damping|armature|frictionloss)",
                candidate,
            )
            is None
            or finding is None
            or not finding.strip()
            or metric is None
        ):
            continue
        prefix, metric_name = metric
        series_name = metric_to_series[metric_name]
        for rows_name, payload_name in rows_to_payload.items():
            if not is_len_of(fields["rows"], rows_name) or not is_payload_run_id(
                fields["run_id"], payload_name
            ):
                continue
            if series_to_rows.get(series_name) != rows_name:
                continue
            if reachable_assignment_names(statement.value.args[0]) != set(assignments):
                continue
            return SandboxAnalysisProof(
                candidate_attribute=candidate,
                finding=finding,
                metric_prefix=prefix,
                signal_key=series_to_signal[series_name],
            )
    return None


def evaluate_sc1_events(events: list[Mapping[str, Any]]) -> dict[str, Any]:
    calls = _call_records(events)
    responses = _response_records(events)
    all_public_calls = [record for record in calls if record["name"] in TOOL_NAMES]
    public_calls: list[dict[str, Any]] = []
    rejected_attempts: list[dict[str, Any]] = []
    for record in all_public_calls:
        model = _TOOL_INPUT_BY_NAME[record["name"]]
        try:
            model.model_validate(_call_arguments(record["call"]))
            schema_valid = True
        except ValueError:
            schema_valid = False
        response = responses.get(record["id"])
        if (
            not schema_valid
            and response is not None
            and _response_error_code(response) == "INVALID_REQUEST"
        ):
            rejected_attempts.append(record)
        else:
            public_calls.append(record)
    experiments = [record for record in public_calls if record["name"] == "run_experiment"]
    revisions = [record for record in public_calls if record["name"] == "create_revision"]
    task_calls = [record for record in public_calls if record["name"] == "run_task"]
    verify_calls = [record for record in public_calls if record["name"] == "verify_revision"]
    publish_calls = [record for record in public_calls if record["name"] == "publish_revision"]
    exec_calls = [record for record in calls if record["name"] == "exec"]

    failures: list[str] = []
    for record in rejected_attempts:
        response = responses[record["id"]]
        next_public_index = min(
            (
                candidate["event_index"]
                for candidate in all_public_calls
                if candidate["event_index"] > record["event_index"]
            ),
            default=len(events),
        )
        if not (
            record["event_index"] < response["event_index"] < next_public_index
        ):
            failures.append(
                f"rejected {record['name']} attempt lacks an ordered INVALID_REQUEST response"
            )
    for record in public_calls:
        model = _TOOL_INPUT_BY_NAME[record["name"]]
        try:
            model.model_validate(_call_arguments(record["call"]))
        except ValueError:
            failures.append(f"{record['name']} has arguments outside its exact public schema")
        if record["name"] == "publish_revision":
            continue
        response = responses.get(record["id"])
        next_public_index = min(
            (
                candidate["event_index"]
                for candidate in all_public_calls
                if candidate["event_index"] > record["event_index"]
            ),
            default=len(events),
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
        match = re.search(r"Result saved to:\s*([^\s]+)", content)
        if match is None:
            failures.append("an experiment response was not moved by Large Tool Response")
            continue
        ltr_path = match.group(1).rstrip(".\"')")
        next_revision_index = min(
            (
                record["event_index"]
                for record in revisions
                if record["event_index"] > response["event_index"]
            ),
            default=len(events),
        )
        compact_keys = {
            "rows",
            "run_id",
            "metric",
            "finding",
            "candidate_attribute",
        }
        last_compact_attempt: tuple[
            dict[str, Any],
            SandboxAnalysisProof | None,
            dict[str, Any] | None,
            Mapping[str, Any] | None,
        ] | None = None
        for record in exec_calls:
            if not (
                record["event_index"] > response["event_index"]
                and record["event_index"] < next_revision_index
            ):
                continue
            proof = _sandbox_python_reads_json(
                _call_arguments(record["call"]), ltr_path
            )
            exec_response = responses.get(record["id"])
            analysis = (
                _response_payload(exec_response)
                if exec_response is not None
                else None
            )
            compact_response = (
                isinstance(analysis, Mapping)
                and compact_keys.issubset(analysis)
            )
            if proof is not None or compact_response:
                last_compact_attempt = (
                    record,
                    proof,
                    exec_response,
                    analysis,
                )
        if last_compact_attempt is None:
            failures.append("an offloaded experiment was not read by Sandbox Python")
            continue
        exec_record, analysis_proof, exec_response, analysis = last_compact_attempt
        if analysis_proof is None:
            failures.append(
                "the last compact Sandbox analysis lacks provable trace dataflow"
            )
            continue
        if (
            exec_response is None
            or exec_response["event_index"] <= exec_record["event_index"]
            or exec_response["event_index"] >= next_revision_index
        ):
            failures.append("a Sandbox Python analysis lacks an ordered tool response")
            continue
        if analysis is None:
            failures.append("a Sandbox Python analysis has no parseable response")
            continue
        required = ("run_id", "metric", "finding", "candidate_attribute")
        if (
            set(analysis) != compact_keys
            or analysis.get("rows") != 256
            or any(not isinstance(analysis.get(key), str) for key in required)
        ):
            failures.append("a Sandbox Python analysis lacks the compact 256-row evidence")
            continue
        if (
            analysis.get("candidate_attribute")
            != analysis_proof.candidate_attribute
            or analysis.get("finding") != analysis_proof.finding
            or not _metric_matches_proof(
                analysis.get("metric"), analysis_proof.metric_prefix
            )
        ):
            failures.append(
                "a Sandbox Python response does not match its analyzed stdout shape"
            )
            continue
        experiment_arguments = _call_arguments(experiment["call"])
        if analysis["candidate_attribute"] not in _experiment_candidate_attributes(
            experiment_arguments
        ):
            failures.append(
                "a Sandbox candidate was not preregistered by its experiment"
            )
            continue
        if analysis_proof.signal_key not in _experiment_observable_signals(
            experiment_arguments
        ):
            failures.append(
                "a Sandbox metric is not a requested non-control experiment observable"
            )
            continue
        sandbox_evidence.append(
            {
                "experiment_index": experiment_index,
                "event_index": exec_response["event_index"],
                "run_id": analysis["run_id"],
                "run_id_hash": _short_hash(str(analysis["run_id"])),
                "metric": analysis["metric"],
                "candidate_attribute": analysis["candidate_attribute"],
                "signal_key": analysis_proof.signal_key,
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
        "rejected_attempts": {
            "count": len(rejected_attempts),
            "tools": [record["name"] for record in rejected_attempts],
        },
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
                    "signal_key": evidence["signal_key"],
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
