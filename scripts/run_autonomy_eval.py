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
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

import uvicorn
from agents import Runner

from asset_autopsy.agents_runner import (
    DEFAULT_MODEL,
    EXACT_PROMPT,
    RunTranscript,
    build_agent,
    approval_request_from_result,
    collect_run_transcript,
    create_mcp_connection,
    evaluate_autonomy_run,
    run_config,
)
from asset_autopsy.fixture import CASE_ID
from asset_autopsy.mcp_server import MCPRuntimeConfig, create_mcp_facade
from asset_autopsy.service import AssetAutopsyService
from asset_autopsy.storage import StorageError


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = ROOT / "evidence" / "autonomy-eval.json"
BLOCKER_PATH = ROOT / "evidence" / "autonomy-blocker.json"
ORIGIN = "http://localhost:8712"
ATTEMPT_COUNT = 3
SUCCESS_THRESHOLD = 2


class _FacadeServer:
    def __init__(self, app: Any, config: MCPRuntimeConfig) -> None:
        self._server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=config.host,
                port=config.port,
                log_level="critical",
                access_log=False,
                timeout_graceful_shutdown=2,
            )
        )
        self._thread = threading.Thread(
            target=self._server.run,
            name="asset-autopsy-agents-mcp",
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
    value = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
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


def _expanded_values(value: Any, *, depth: int = 0) -> list[Any]:
    if depth > 8:
        return []
    values = [value]
    if isinstance(value, Mapping):
        for item in value.values():
            values.extend(_expanded_values(item, depth=depth + 1))
    elif isinstance(value, (list, tuple)):
        for item in value:
            values.extend(_expanded_values(item, depth=depth + 1))
    elif isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return values
        if decoded != value:
            values.extend(_expanded_values(decoded, depth=depth + 1))
    return values


def _raw_boundary_clear(
    transcript: RunTranscript,
    *,
    bearer: str,
    data_root: Path,
    private_payloads: tuple[Mapping[str, Any], ...],
    public_payloads: tuple[Mapping[str, Any], ...] = (),
) -> bool:
    transcript_value = asdict(transcript) if is_dataclass(transcript) else transcript
    hidden_keys = {
        "target_qpos",
        "initial_qpos",
        "target_body_position",
        "duration_steps",
        "hold_steps",
    }
    public_hidden_pairs = {
        (key, json.dumps(value, sort_keys=True, default=str))
        for payload in public_payloads
        for key, value in payload.items()
        if key in hidden_keys
    }

    def contains_unapproved_hidden_field(value: Any) -> bool:
        if isinstance(value, Mapping):
            return any(
                (
                    key in hidden_keys
                    and (key, json.dumps(item, sort_keys=True, default=str))
                    not in public_hidden_pairs
                )
                or contains_unapproved_hidden_field(item)
                for key, item in value.items()
            )
        if isinstance(value, (list, tuple)):
            return any(contains_unapproved_hidden_field(item) for item in value)
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                return False
            return decoded != value and contains_unapproved_hidden_field(decoded)
        return False

    private_values = _expanded_values(private_payloads)
    public_values = _expanded_values(public_payloads)
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
    transcript_values = _expanded_values(transcript_value)
    leaked_vector = any(
        tuple(float(item) for item in value) in private_vectors
        for value in transcript_values
        if isinstance(value, (list, tuple))
        and len(value) > 1
        and all(
            isinstance(item, (int, float)) and not isinstance(item, bool)
            for item in value
        )
    )
    leaked_scalar = any(
        value in private_numeric_sentinels
        for value in transcript_values
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    )

    raw = json.dumps(transcript_value, default=str, sort_keys=True, ensure_ascii=True)
    prohibited = [bearer, str(data_root), "<mujoco", "xml_string"]
    prohibited.extend(
        encoded
        for payload in private_payloads
        for encoded in (
            json.dumps(payload, sort_keys=True),
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
        )
    )
    sentinel_token_leaked = any(
        re.search(
            rf"(?<![0-9.eE+-]){re.escape(json.dumps(value))}(?![0-9.eE+-])",
            raw,
        )
        is not None
        for value in private_numeric_sentinels
    )
    return (
        not contains_unapproved_hidden_field(transcript_value)
        and not leaked_vector
        and not leaked_scalar
        and not sentinel_token_leaked
        and all(value not in raw for value in prohibited)
    )


def _runtime_state_gates(
    evidence: Mapping[str, Any],
    *,
    facade: Any,
    service: AssetAutopsyService,
) -> dict[str, bool]:
    tool_order = evidence.get("tool_order")
    public_sequence = tool_order if isinstance(tool_order, list) else []
    mcp_sequence = [name for name in public_sequence if name != "publish_revision"]
    event_counts = Counter(public_sequence)
    mcp_counts = Counter(mcp_sequence)
    service_counts = Counter(
        {name: count for name, count in service.invocation_counts.items() if count}
    )
    facade_counts = Counter(
        {name: count for name, count in facade.recorder.counts.items() if count}
    )
    ledger = service.store.ledger_events(CASE_ID)
    event_types = Counter(event.event_type for event in ledger)
    revisions = service.store.list_revisions(CASE_ID)
    child_revisions = revisions[1:]
    experiment_events = [
        event
        for event in ledger
        if event.event_type in {"EXPERIMENT_COMPLETED", "EXPERIMENT_FAILED"}
    ]
    analysis = evidence.get("analysis")
    analysis_runs = analysis.get("runs") if isinstance(analysis, Mapping) else None
    analysis_evidence = (
        [item for item in analysis_runs if isinstance(item, Mapping)]
        if isinstance(analysis_runs, list)
        else []
    )

    def evidence_ledger_pair(item: Mapping[str, Any]) -> tuple[int, int] | None:
        eligible_indexes = item.get("eligible_experiment_indexes")
        revision_index = item.get("revision_index")
        if (
            not isinstance(eligible_indexes, list)
            or len(eligible_indexes) != 1
            or not isinstance(revision_index, int)
            or isinstance(revision_index, bool)
            or not 0 <= revision_index < len(child_revisions)
        ):
            return None
        experiment_index = eligible_indexes[0]
        if (
            not isinstance(experiment_index, int)
            or isinstance(experiment_index, bool)
            or not 0 <= experiment_index < len(experiment_events)
        ):
            return None
        event = experiment_events[experiment_index]
        run_id = event.payload.get("run_id")
        hypothesis_id = event.payload.get("hypothesis_id")
        if (
            event.event_type != "EXPERIMENT_COMPLETED"
            or not isinstance(run_id, str)
            or not isinstance(hypothesis_id, str)
            or item.get("run_id_hash") != _sha256_text(run_id)[:12]
            or item.get("hypothesis_id_hash") != _sha256_text(hypothesis_id)[:12]
        ):
            return None
        revision = child_revisions[revision_index]
        if (
            revision.probe_run_id != run_id
            or revision.hypothesis_event_id != event.payload.get("hypothesis_event_id")
        ):
            return None
        try:
            run = service.store.get_run(run_id)
        except StorageError:
            return None
        if not (
            run.passed
            and run.trace_sha256 == item.get("trace_sha256")
            and run.revision_id == revision.parent_revision_id
        ):
            return None
        return experiment_index, revision_index

    resolved_pairs = [
        pair
        for item in analysis_evidence
        if (pair := evidence_ledger_pair(item)) is not None
    ]
    run_events = sum(
        event_types[name]
        for name in ("TASK_COMPLETED", "EXPERIMENT_COMPLETED", "EXPERIMENT_FAILED")
    )
    return {
        "facade_sequence_matches_run": facade.recorder.sequence == mcp_sequence,
        "facade_counts_match_run": facade_counts == mcp_counts,
        "service_counts_match_run": service_counts == mcp_counts,
        "ledger_run_counts_match": run_events
        == event_counts["run_task"] + event_counts["run_experiment"],
        "ledger_analysis_evidence_matches": (
            len(experiment_events) == event_counts["run_experiment"]
            and len(analysis_evidence) == event_counts["create_revision"]
            and len(resolved_pairs) == len(child_revisions)
            and len({pair[0] for pair in resolved_pairs}) == len(resolved_pairs)
            and len({pair[1] for pair in resolved_pairs}) == len(resolved_pairs)
        ),
        "ledger_revision_count_matches": (
            len(child_revisions) == event_types["REVISION_CREATED"]
            and event_types["REVISION_CREATED"] == event_counts["create_revision"]
        ),
        "ledger_qualification_once": (
            event_counts["verify_revision"] == 1
            and event_types["QUALIFICATION_RESERVED"] == 1
            and event_types["QUALIFICATION_PASSED"] == 1
            and event_types["QUALIFICATION_FAILED"] == 0
        ),
    }


async def _execute_agent(
    config: MCPRuntimeConfig,
    *,
    group_id: str,
):
    connection = create_mcp_connection(config)
    async with connection:
        agent = build_agent(connection)
        return await Runner.run(
            agent,
            EXACT_PROMPT,
            max_turns=30,
            run_config=run_config(group_id=group_id),
        )


def _safe_attempt_failure(
    attempt_index: int,
    stage: str,
    error: Exception,
    commit_sha: str | None,
) -> dict[str, Any]:
    return {
        "attempt_index": attempt_index,
        "status": "failed",
        "stage": stage,
        "reason": "The autonomy attempt did not complete its required gate.",
        "details": {"error_type": type(error).__name__},
        "commit_sha": commit_sha,
        "recorded_at": datetime.now(UTC).isoformat(),
    }


def _event_summary(evidence: Mapping[str, Any]) -> dict[str, Any]:
    analysis = evidence.get("analysis")
    runs = analysis.get("runs") if isinstance(analysis, Mapping) else []
    return {
        "passed": evidence.get("passed") is True,
        "failures": list(evidence.get("failures", [])),
        "tool_counts": dict(evidence.get("tool_counts", {})),
        "analysis_count": len(runs) if isinstance(runs, list) else 0,
        "approval_required": evidence.get("approval") is not None,
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
            prefix=f"asset-autopsy-agents-{attempt_index}-"
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
            stage = "model_turn"
            with _FacadeServer(facade.app, config):
                result = asyncio.run(
                    _execute_agent(
                        config,
                        group_id=f"{commit_sha[:12]}-{attempt_index}",
                    )
                )

            stage = "evidence_gate"
            transcript = collect_run_transcript(result)
            approval_request = approval_request_from_result(result)
            evidence = evaluate_autonomy_run(transcript, approval_request)
            case = service.store.get_case(CASE_ID)
            service.store.verify_ledger()
            gates = {
                "agent_contract": evidence["passed"] is True,
                "raw_boundary_clear": _raw_boundary_clear(
                    transcript,
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
                    "details": {"failed_gates": failed},
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
                "agents_sdk": {
                    "last_response_id_hash": _sha256_text(
                        result.last_response_id or "none"
                    ),
                    "approval_boundary": "local_function_tool_stop",
                    "trace_sensitive_data": False,
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
    return {
        "schema_version": "asset-autopsy-agents-blocker/v1",
        "status": "blocked",
        "recorded_at": datetime.now(UTC).isoformat(),
        "commit_sha": commit_sha,
        "model": DEFAULT_MODEL,
        "prompt_sha256": _sha256_text(EXACT_PROMPT),
        "summary": _aggregate_attempts(attempts),
        "attempts": attempts,
        "reproduction": "uv run python scripts/run_autonomy_eval.py",
    }


def run() -> dict[str, Any]:
    commit_sha: str | None = None
    attempts: list[Mapping[str, Any]] = []
    try:
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is required")
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
            "schema_version": "asset-autopsy-agents-evidence/v1",
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
            try:
                EVIDENCE_PATH.unlink(missing_ok=True)
            except OSError:
                pass
            raise RuntimeError(
                "The autonomy evaluation could not record sanitized evidence."
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
        f"The autonomy evaluation reached only {aggregate['attempts_succeeded']}/"
        f"{ATTEMPT_COUNT} successful attempts."
    )


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
