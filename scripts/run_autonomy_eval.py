from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import subprocess
import tempfile
import threading
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

import uvicorn

from asset_autopsy.fixture import CASE_ID
from asset_autopsy.mcp_server import MCPRuntimeConfig, create_mcp_facade
from asset_autopsy.service import AssetAutopsyService
from asset_autopsy.storage import StorageError
from asset_autopsy.trueforge_client import (
    DEFAULT_MODEL,
    EXACT_PROMPT,
    TrueForgeClient,
    TrueForgeError,
    evaluate_autonomy_events,
)


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = ROOT / "evidence" / "autonomy-eval.json"
BLOCKER_PATH = ROOT / "evidence" / "autonomy-blocker.json"
TRUEFORGE_URL = "http://localhost:8790"
ORIGIN = "http://localhost:8790"
ATTEMPT_COUNT = 3
SUCCESS_THRESHOLD = 2


class _FacadeServer:
    def __init__(self, app: Any, config: MCPRuntimeConfig) -> None:
        self._server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=config.host,
                port=config.port,
                # TrueForge keeps the Streamable HTTP GET connection open. The
                # bounded shutdown below cancels that expected long-lived task;
                # keep the evidence runner's output focused on its JSON result.
                log_level="critical",
                access_log=False,
                timeout_graceful_shutdown=2,
            )
        )
        self._thread = threading.Thread(
            target=self._server.run,
            name="asset-autopsy-autonomy-mcp",
            daemon=True,
        )

    def __enter__(self) -> _FacadeServer:
        self._thread.start()
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if self._server.started:
                return self
            if not self._thread.is_alive():
                break
            time.sleep(0.05)
        self._server.should_exit = True
        self._thread.join(timeout=5.0)
        raise RuntimeError("the loopback MCP facade did not start")

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10.0)
        if self._thread.is_alive():
            self._server.force_exit = True
            self._thread.join(timeout=5.0)
        if self._thread.is_alive() and exc_type is None:
            raise RuntimeError("the loopback MCP facade did not stop")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _commit_sha() -> str:
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    allowed_outputs = {
        EVIDENCE_PATH.relative_to(ROOT).as_posix(),
        BLOCKER_PATH.relative_to(ROOT).as_posix(),
    }
    changed_paths = []
    for line in status.stdout.splitlines():
        path = line[3:].split(" -> ")[-1]
        generated_bytecode = (
            line.startswith("?? ")
            and Path(path).suffix == ".pyc"
            and "__pycache__" in Path(path).parts
        )
        if path not in allowed_outputs and not generated_bytecode:
            changed_paths.append(path)
    if changed_paths:
        raise RuntimeError("the autonomy evaluation source is not clean at HEAD")
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    value = result.stdout.strip()
    if len(value) != 40 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise RuntimeError("the checkout commit SHA is invalid")
    return value


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _raw_events_are_clear(
    events: list[Mapping[str, Any]],
    *,
    bearer: str,
    data_root: Path,
    private_payloads: tuple[Mapping[str, Any], ...],
    public_payloads: tuple[Mapping[str, Any], ...] = (),
) -> bool:
    hidden_keys = {
        "target_qpos",
        "initial_qpos",
        "target_body_position",
        "duration_steps",
        "hold_steps",
    }

    def contains_hidden_key(value: Any) -> bool:
        if isinstance(value, Mapping):
            return any(
                key in hidden_keys or contains_hidden_key(item)
                for key, item in value.items()
            )
        if isinstance(value, list):
            return any(contains_hidden_key(item) for item in value)
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                return False
            return decoded != value and contains_hidden_key(decoded)
        return False

    def expanded_values(value: Any, *, depth: int = 0) -> list[Any]:
        if depth > 8:
            return []
        values = [value]
        if isinstance(value, Mapping):
            for item in value.values():
                values.extend(expanded_values(item, depth=depth + 1))
        elif isinstance(value, (list, tuple)):
            for item in value:
                values.extend(expanded_values(item, depth=depth + 1))
        elif isinstance(value, str):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                return values
            if decoded != value:
                values.extend(expanded_values(decoded, depth=depth + 1))
        return values

    private_values = expanded_values(private_payloads)
    public_values = expanded_values(public_payloads)
    public_vectors = {
        tuple(float(item) for item in value)
        for value in public_values
        if isinstance(value, (list, tuple))
        and len(value) > 1
        and all(
            isinstance(item, (int, float)) and not isinstance(item, bool)
            for item in value
        )
    }
    private_vectors = {
        tuple(float(item) for item in value)
        for value in private_values
        if isinstance(value, (list, tuple))
        and len(value) > 1
        and all(
            isinstance(item, (int, float)) and not isinstance(item, bool)
            for item in value
        )
    } - public_vectors
    public_numeric_values = {
        value
        for value in public_values
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            or isinstance(value, float)
            and not value.is_integer()
        )
    }
    private_numeric_sentinels = {
        value
        for value in private_values
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            or isinstance(value, float)
            and not value.is_integer()
        )
    } - public_numeric_values
    event_values = expanded_values(events)
    leaked_vector = any(
        tuple(float(item) for item in value) in private_vectors
        for value in event_values
        if isinstance(value, (list, tuple))
        and len(value) > 1
        and all(
            isinstance(item, (int, float)) and not isinstance(item, bool)
            for item in value
        )
    )
    leaked_scalar = any(
        value in private_numeric_sentinels
        for value in event_values
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    )

    raw = json.dumps(events, sort_keys=True, ensure_ascii=True)
    prohibited = [
        bearer,
        str(data_root),
        "<mujoco",
        "xml_string",
        *(
            encoded
            for payload in private_payloads
            for encoded in (
                json.dumps(payload, sort_keys=True),
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
            )
        ),
    ]
    sentinel_token_leaked = any(
        re.search(
            rf"(?<![0-9.eE+-]){re.escape(json.dumps(value))}(?![0-9.eE+-])",
            raw,
        )
        is not None
        for value in private_numeric_sentinels
    )
    return (
        not contains_hidden_key(events)
        and not leaked_vector
        and not leaked_scalar
        and not sentinel_token_leaked
        and all(value not in raw for value in prohibited)
    )


def _terminal_failure_category(turn: Mapping[str, Any]) -> str:
    status = TrueForgeClient.turn_status(turn)
    state = turn.get("state")
    message = state.get("message") if isinstance(state, Mapping) else None
    normalized = message.lower() if isinstance(message, str) else ""
    if status == "done":
        return "none"
    if status == "error" and ("rate limit" in normalized or "429" in normalized):
        return "provider_rate_limit"
    if status in {"error", "failed"}:
        return "provider_or_harness_error"
    if status in {"cancelled", "canceled"}:
        return "turn_cancelled"
    return "unexpected_terminal_state"


def _runtime_state_gates(
    evidence: Mapping[str, Any],
    *,
    facade: Any,
    service: AssetAutopsyService,
) -> dict[str, bool]:
    tool_order = evidence.get("tool_order")
    event_sequence = (
        [name for name in tool_order if name != "publish_revision"]
        if isinstance(tool_order, list)
        else []
    )
    invoked_tool_order = evidence.get("invoked_tool_order", tool_order)
    invoked_sequence = (
        [name for name in invoked_tool_order if name != "publish_revision"]
        if isinstance(invoked_tool_order, list)
        else []
    )
    event_counts = Counter(event_sequence)
    invoked_counts = Counter(invoked_sequence)
    service_counts = Counter(
        {name: count for name, count in service.invocation_counts.items() if count}
    )
    facade_counts = Counter(
        {name: count for name, count in facade.recorder.counts.items() if count}
    )
    ledger = service.store.ledger_events(CASE_ID)
    event_types = Counter(event.event_type for event in ledger)
    task_and_experiment_calls = sum(
        event_counts[name] for name in ("run_task", "run_experiment")
    )
    run_events = sum(
        event_types[name]
        for name in ("TASK_COMPLETED", "EXPERIMENT_COMPLETED", "EXPERIMENT_FAILED")
    )
    sandbox = evidence.get("sandbox")
    sandbox_runs = sandbox.get("runs") if isinstance(sandbox, Mapping) else None
    sandbox_evidence = (
        [item for item in sandbox_runs if isinstance(item, Mapping)]
        if isinstance(sandbox_runs, list)
        else []
    )
    revisions = service.store.list_revisions(CASE_ID)
    child_revisions = revisions[1:]
    experiment_events = [
        event
        for event in ledger
        if event.event_type in {"EXPERIMENT_COMPLETED", "EXPERIMENT_FAILED"}
    ]

    def evidence_matches_ledger(item: Mapping[str, Any]) -> bool:
        experiment_index = item.get("experiment_index")
        revision_index = item.get("revision_index")
        if (
            not isinstance(experiment_index, int)
            or isinstance(experiment_index, bool)
            or not isinstance(revision_index, int)
            or isinstance(revision_index, bool)
            or not 0 <= experiment_index < len(experiment_events)
            or not 0 <= revision_index < len(child_revisions)
        ):
            return False
        event = experiment_events[experiment_index]
        revision = child_revisions[revision_index]
        run_id = event.payload.get("run_id")
        hypothesis_id = event.payload.get("hypothesis_id")
        if (
            event.event_type != "EXPERIMENT_COMPLETED"
            or not isinstance(run_id, str)
            or not isinstance(hypothesis_id, str)
            or item.get("run_id_hash") != _sha256_text(run_id)[:12]
            or item.get("hypothesis_id_hash") != _sha256_text(hypothesis_id)[:12]
            or revision.probe_run_id != run_id
            or revision.hypothesis_event_id != event.payload.get("hypothesis_event_id")
        ):
            return False
        try:
            run = service.store.get_run(run_id)
        except StorageError:
            return False
        return (
            run.passed
            and isinstance(run.trace_sha256, str)
            and len(run.trace_sha256) == 64
            and run.revision_id == revision.parent_revision_id
        )

    return {
        "facade_sequence_matches_events": facade.recorder.sequence == invoked_sequence,
        "facade_counts_match_events": facade_counts == invoked_counts,
        "service_counts_match_events": service_counts == invoked_counts,
        "ledger_run_counts_match": run_events == task_and_experiment_calls,
        "ledger_experiment_evidence_matches": (
            len(experiment_events) == event_counts["run_experiment"]
            and len(sandbox_evidence) == event_counts["create_revision"]
            and len(
                {
                    (item.get("experiment_index"), item.get("revision_index"))
                    for item in sandbox_evidence
                }
            )
            == len(sandbox_evidence)
            and all(evidence_matches_ledger(item) for item in sandbox_evidence)
        ),
        "ledger_revision_count_matches": (
            len(child_revisions) == event_types["REVISION_CREATED"]
            and event_types["REVISION_CREATED"] == event_counts["create_revision"]
        ),
        "ledger_qualification_once": (
            event_counts["verify_revision"] >= 1
            and event_types["QUALIFICATION_RESERVED"] == 1
            and event_types["QUALIFICATION_PASSED"] == 1
            and event_types["QUALIFICATION_FAILED"] == 0
        ),
    }


def _safe_attempt_failure(
    attempt_index: int,
    stage: str,
    error: Exception,
    commit_sha: str | None,
) -> dict[str, Any]:
    if isinstance(error, TrueForgeError):
        reason = str(error)
        details = {"http_status": error.status}
    else:
        reason = "The autonomy attempt did not complete its required gate."
        details = {"error_type": type(error).__name__}
    return {
        "attempt_index": attempt_index,
        "status": "failed",
        "stage": stage,
        "reason": reason,
        "details": details,
        "commit_sha": commit_sha,
        "recorded_at": datetime.now(UTC).isoformat(),
    }


def _event_summary(evidence: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "passed": evidence.get("passed") is True,
        "failures": list(evidence.get("failures", [])),
        "rejected_attempts": evidence.get("rejected_attempts", {}),
        "experiment_count": evidence.get("experiment_count", 0),
        "revision_count": evidence.get("revision_count", 0),
        "large_tool_response": evidence.get("large_tool_response", {}),
        "sandbox": evidence.get("sandbox", {}),
        "revisions": evidence.get("revisions", {}),
        "public": evidence.get("public", {}),
        "hidden": evidence.get("hidden", {}),
        "approval": evidence.get("approval", {}),
    }


def _aggregate_attempts(attempts: list[Mapping[str, Any]]) -> dict[str, Any]:
    successful = sum(attempt.get("status") == "passed" for attempt in attempts)
    return {
        "status": "passed" if successful >= SUCCESS_THRESHOLD else "blocked",
        "attempts_total": len(attempts),
        "attempts_succeeded": successful,
        "success_threshold": SUCCESS_THRESHOLD,
    }


def _run_attempt(attempt_index: int, commit_sha: str) -> dict[str, Any]:
    stage = "startup"
    try:
        bearer = secrets.token_urlsafe(32)
        with tempfile.TemporaryDirectory(
            prefix=f"asset-autopsy-autonomy-{attempt_index}-"
        ) as temporary:
            data_root = Path(temporary)
            service = AssetAutopsyService(data_root)
            config = MCPRuntimeConfig(
                bearer_token=bearer,
                allowed_origin=ORIGIN,
            )
            facade = asyncio.run(create_mcp_facade(service, config))
            private_payloads = tuple(service.hidden_verifier._scenario_payloads)
            scenario = service.fixture.public_scenario
            public_payloads = (
                {
                    "target_qpos": scenario.target_qpos,
                    "initial_qpos": scenario.initial_qpos,
                    "target_body_position": scenario.target_body_position,
                    "duration_steps": scenario.duration_steps,
                    "hold_steps": scenario.hold_steps,
                },
            )

            with _FacadeServer(facade.app, config):
                client = TrueForgeClient(TRUEFORGE_URL, timeout_seconds=30.0)
                stage = "provision"
                provision = client.provision_autonomy(
                    bearer=bearer,
                    origin=ORIGIN,
                    model=DEFAULT_MODEL,
                )
                stage = "session"
                session = client.create_session()
                session_id = str(session["id"])
                turn = client.create_turn(session_id, EXACT_PROMPT)
                turn_id = str(turn["id"])
                stage = "model_turn"
                terminal = client.wait_for_turn(
                    session_id,
                    turn_id,
                    timeout_seconds=900.0,
                )
                events = client.list_turn_events(session_id, turn_id)

            stage = "evidence_gate"
            evidence = evaluate_autonomy_events(events)
            turn_status = client.turn_status(terminal)
            case = service.store.get_case(CASE_ID)
            service.store.verify_ledger()
            gates = {
                "turn_done": turn_status == "done",
                "event_contract": evidence["passed"] is True,
                "raw_boundary_clear": _raw_events_are_clear(
                    events,
                    bearer=bearer,
                    data_root=data_root,
                    private_payloads=private_payloads,
                    public_payloads=public_payloads,
                ),
                "facade_publish_calls": facade.recorder.counts["publish_revision"] == 0,
                "service_publish_calls": service.publish_invocation_count == 0,
                "qualification_passed": case.qualification_state == "passed",
                "ledger_verified": True,
                **_runtime_state_gates(evidence, facade=facade, service=service),
            }
            if not all(gates.values()):
                failed = [name for name, passed in gates.items() if not passed]
                return {
                    "attempt_index": attempt_index,
                    "status": "failed",
                    "stage": stage,
                    "reason": "failed gates: " + ", ".join(failed),
                    "details": {
                        "failed_gates": failed,
                        "turn_status": turn_status,
                        "terminal_category": _terminal_failure_category(terminal),
                    },
                    "commit_sha": commit_sha,
                    "recorded_at": datetime.now(UTC).isoformat(),
                    "gates": gates,
                    "events": _event_summary(evidence),
                    "server": {
                        "tool_invocations": dict(service.invocation_counts),
                        "publish_invocations": service.publish_invocation_count,
                    },
                }

            return {
                "attempt_index": attempt_index,
                "status": "passed",
                "recorded_at": datetime.now(UTC).isoformat(),
                "commit_sha": commit_sha,
                "prompt_sha256": _sha256_text(EXACT_PROMPT),
                "model": DEFAULT_MODEL,
                "trueforge": {
                    "session_id_hash": _sha256_text(session_id),
                    "turn_id_hash": _sha256_text(turn_id),
                    "turn_status": turn_status,
                    "agent_action": provision.agent_action,
                    "agent_manifest_sha256": provision.agent_manifest_sha256,
                    "hackathon_starter_sha256": provision.hackathon_starter_sha256,
                    "models_sha256": provision.models_sha256,
                    "tool_schema_sha256": provision.tool_schema_sha256,
                },
                "gates": gates,
                "events": _event_summary(evidence),
                "server": {
                    "tool_invocations": dict(service.invocation_counts),
                    "publish_invocations": service.publish_invocation_count,
                },
            }
    except Exception as error:
        return _safe_attempt_failure(attempt_index, stage, error, commit_sha)


def _blocker_payload(
    *, commit_sha: str | None, attempts: list[Mapping[str, Any]]
) -> dict[str, Any]:
    aggregate = _aggregate_attempts(attempts)
    return {
        "schema_version": "asset-autopsy-autonomy-blocker/v1",
        "status": "blocked",
        "recorded_at": datetime.now(UTC).isoformat(),
        "commit_sha": commit_sha,
        "model": DEFAULT_MODEL,
        "prompt_sha256": _sha256_text(EXACT_PROMPT),
        "summary": aggregate,
        "attempts": attempts,
        "reproduction": "uv run python scripts/run_autonomy_eval.py",
    }


def run() -> dict[str, Any]:
    commit_sha: str | None = None
    attempts: list[Mapping[str, Any]] = []
    try:
        commit_sha = _commit_sha()
    except Exception as error:
        attempts.append(_safe_attempt_failure(0, "startup", error, commit_sha))
        blocker = _blocker_payload(commit_sha=commit_sha, attempts=attempts)
        try:
            EVIDENCE_PATH.unlink(missing_ok=True)
        except OSError:
            raise RuntimeError(
                "The autonomy evaluation could not invalidate stale evidence."
            ) from None
        try:
            _write_json(BLOCKER_PATH, blocker)
        except Exception:
            raise RuntimeError(
                "The autonomy evaluation could not record its sanitized blocker."
            ) from None
        raise RuntimeError("The autonomy evaluation could not start safely.") from None

    for attempt_index in range(1, ATTEMPT_COUNT + 1):
        attempts.append(_run_attempt(attempt_index, commit_sha))

    aggregate = _aggregate_attempts(attempts)
    if aggregate["status"] == "passed":
        payload = {
            "schema_version": "asset-autopsy-autonomy-evidence/v1",
            "status": "passed",
            "recorded_at": datetime.now(UTC).isoformat(),
            "commit_sha": commit_sha,
            "model": DEFAULT_MODEL,
            "prompt_sha256": _sha256_text(EXACT_PROMPT),
            "summary": aggregate,
            "attempts": attempts,
        }
        try:
            EVIDENCE_PATH.unlink(missing_ok=True)
            BLOCKER_PATH.unlink(missing_ok=True)
        except OSError:
            raise RuntimeError(
                "The autonomy evaluation could not invalidate stale artifacts."
            ) from None
        try:
            _write_json(EVIDENCE_PATH, payload)
        except Exception:
            raise RuntimeError(
                "The autonomy evaluation could not record its sanitized evidence."
            ) from None
        return payload

    try:
        EVIDENCE_PATH.unlink(missing_ok=True)
    except OSError:
        raise RuntimeError(
            "The autonomy evaluation could not invalidate stale evidence."
        ) from None
    blocker = _blocker_payload(commit_sha=commit_sha, attempts=attempts)
    try:
        _write_json(BLOCKER_PATH, blocker)
    except Exception:
        raise RuntimeError(
            "The autonomy evaluation could not record its sanitized blocker."
        ) from None
    raise RuntimeError(
        "The autonomy evaluation did not reach two successful attempts."
    ) from None


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True, ensure_ascii=True))
