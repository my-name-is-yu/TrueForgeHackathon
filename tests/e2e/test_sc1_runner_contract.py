from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from asset_autopsy.storage import CaseRecord  # noqa: E402
from run_sc1_e2e import (  # noqa: E402
    _EvidenceGateFailure,
    _case_is_qualified,
    _commit_sha,
    _raw_events_are_clear,
    _runtime_state_gates,
    _safe_blocker,
    _terminal_failure_category,
)
import run_sc1_e2e as e2e_runner  # noqa: E402


def test_raw_event_gate_rejects_private_boundary_material() -> None:
    private = (
        {
            "target_qpos": [-0.553127, 0.347211, -0.203811],
            "initial_qpos": [-0.553127, 0.347211, -0.163811],
            "target_body_position": [0.87654321, -0.23456789, 0.12345678],
            "duration_steps": 2_000,
            "hold_steps": 1_000,
        },
    )
    safe = [{"event": {"type": "tool.response", "content": "aggregate 3/3"}}]

    assert _raw_events_are_clear(
        safe,
        bearer="secret-bearer-value",
        data_root=Path("/private/tmp/sc1-private"),
        private_payloads=private,
    )
    for content in (
        "secret-bearer-value",
        "/private/tmp/sc1-private/evidence.sqlite",
        '<mujoco model="secret"/>',
        json.dumps(private[0]),
        json.dumps({"unrelated_name": private[0]["initial_qpos"]}),
        json.dumps({"unrelated_name": -0.553127}),
        json.dumps({"initial_qpos": [0.1, 0.2, 0.3]}),
    ):
        assert not _raw_events_are_clear(
            [{"event": {"type": "tool.response", "content": content}}],
            bearer="secret-bearer-value",
            data_root=Path("/private/tmp/sc1-private"),
            private_payloads=private,
        )


def test_raw_event_gate_detects_private_integer_scalars_with_token_boundaries() -> None:
    private = ({"duration_steps": 2_000, "private_flag": True},)

    for content in (
        2_000,
        json.dumps({"unrelated_name": 2_000}),
        "measured duration=2000 steps",
    ):
        assert not _raw_events_are_clear(
            [{"event": {"type": "tool.response", "content": content}}],
            bearer="secret-bearer-value",
            data_root=Path("/private/tmp/sc1-private"),
            private_payloads=private,
        )

    for content in (
        20_000,
        json.dumps({"unrelated_name": 20_000}),
        "measured duration=20000 steps",
        True,
    ):
        assert _raw_events_are_clear(
            [{"event": {"type": "tool.response", "content": content}}],
            bearer="secret-bearer-value",
            data_root=Path("/private/tmp/sc1-private"),
            private_payloads=private,
        )


def test_case_qualification_gate_uses_only_the_qualification_lifecycle() -> None:
    case = CaseRecord(
        case_id="case_compound-arm-01",
        root_revision_id="r000",
        head_revision_id="r002",
        qualification_revision_id="r002",
        qualification_attempt_id="qualify-1",
        qualification_result="PASSED",
        source_asset_sha256="a" * 64,
        controller_sha256="b" * 64,
        public_contract_sha256="c" * 64,
        runner_sha256="d" * 64,
        holdout_commitment_sha256="e" * 64,
        created_at="2026-08-30T00:00:00+00:00",
    )

    assert _case_is_qualified(case)
    assert not _case_is_qualified(replace(case, qualification_result="FAILED"))


def test_commit_sha_rejects_dirty_or_untracked_execution_source(monkeypatch) -> None:
    monkeypatch.setattr(
        e2e_runner.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="?? scripts/run_sc1_e2e.py\n"),
    )

    with pytest.raises(RuntimeError, match="not clean at HEAD"):
        _commit_sha()


def test_commit_sha_allows_only_generated_evidence_outputs(monkeypatch) -> None:
    results = iter(
        (
            SimpleNamespace(
                stdout=(
                    " M evidence/sc1-evidence.json\n"
                    "?? evidence/sc1-blocker.json\n"
                    "?? src/asset_autopsy/__pycache__/service.cpython-312.pyc\n"
                )
            ),
            SimpleNamespace(stdout="a" * 40 + "\n"),
        )
    )
    monkeypatch.setattr(
        e2e_runner.subprocess,
        "run",
        lambda *args, **kwargs: next(results),
    )

    assert _commit_sha() == "a" * 40


def test_startup_commit_failure_removes_stale_evidence_and_records_null_sha(
    monkeypatch, tmp_path
) -> None:
    evidence_path = tmp_path / "sc1-evidence.json"
    blocker_path = tmp_path / "sc1-blocker.json"
    evidence_path.write_text('{"status":"passed"}\n', encoding="utf-8")

    def fail_commit_sha() -> str:
        raise RuntimeError("secret checkout failure at /private/repository")

    monkeypatch.setattr(e2e_runner, "EVIDENCE_PATH", evidence_path)
    monkeypatch.setattr(e2e_runner, "BLOCKER_PATH", blocker_path)
    monkeypatch.setattr(e2e_runner, "_commit_sha", fail_commit_sha)

    with pytest.raises(RuntimeError) as caught:
        e2e_runner.run()

    blocker = json.loads(blocker_path.read_text(encoding="utf-8"))
    rendered = json.dumps(blocker, sort_keys=True)
    assert (
        str(caught.value) == "The SC1 evidence run did not complete its required gate."
    )
    assert not evidence_path.exists()
    assert blocker["stage"] == "startup"
    assert blocker["commit_sha"] is None
    assert "secret checkout failure" not in rendered
    assert "/private/repository" not in rendered


def test_startup_bearer_failure_records_the_available_commit(
    monkeypatch, tmp_path
) -> None:
    evidence_path = tmp_path / "sc1-evidence.json"
    blocker_path = tmp_path / "sc1-blocker.json"
    evidence_path.write_text('{"status":"passed"}\n', encoding="utf-8")

    def fail_bearer(_length: int) -> str:
        raise RuntimeError("secret entropy failure at /private/random")

    monkeypatch.setattr(e2e_runner, "EVIDENCE_PATH", evidence_path)
    monkeypatch.setattr(e2e_runner, "BLOCKER_PATH", blocker_path)
    monkeypatch.setattr(e2e_runner, "_commit_sha", lambda: "a" * 40)
    monkeypatch.setattr(e2e_runner.secrets, "token_urlsafe", fail_bearer)

    with pytest.raises(RuntimeError) as caught:
        e2e_runner.run()

    blocker = json.loads(blocker_path.read_text(encoding="utf-8"))
    rendered = json.dumps(blocker, sort_keys=True)
    assert (
        str(caught.value) == "The SC1 evidence run did not complete its required gate."
    )
    assert not evidence_path.exists()
    assert blocker["stage"] == "startup"
    assert blocker["commit_sha"] == "a" * 40
    assert "secret entropy failure" not in rendered
    assert "/private/random" not in rendered


def test_stale_evidence_is_removed_before_a_blocker_write_failure(
    monkeypatch, tmp_path
) -> None:
    evidence_path = tmp_path / "sc1-evidence.json"
    blocker_path = tmp_path / "sc1-blocker.json"
    evidence_path.write_text('{"status":"passed"}\n', encoding="utf-8")

    def fail_commit_sha() -> str:
        raise RuntimeError("secret checkout failure at /private/repository")

    def fail_blocker_write(path, payload) -> None:
        assert path == blocker_path
        assert payload["commit_sha"] is None
        assert not evidence_path.exists()
        raise OSError("secret blocker path at /private/blocker")

    monkeypatch.setattr(e2e_runner, "EVIDENCE_PATH", evidence_path)
    monkeypatch.setattr(e2e_runner, "BLOCKER_PATH", blocker_path)
    monkeypatch.setattr(e2e_runner, "_commit_sha", fail_commit_sha)
    monkeypatch.setattr(e2e_runner, "_write_json", fail_blocker_write)

    with pytest.raises(RuntimeError) as caught:
        e2e_runner.run()

    assert (
        str(caught.value) == "The SC1 evidence run did not complete its required gate."
    )
    assert not evidence_path.exists()


def test_runtime_state_gates_reconcile_events_with_facade_service_and_ledger() -> None:
    tool_order = [
        "open_case",
        "run_task",
        "inspect_asset",
        "run_experiment",
        "create_revision",
        "run_task",
        "run_experiment",
        "create_revision",
        "run_task",
        "verify_revision",
        "publish_revision",
    ]
    invoked = tool_order[:-1]
    run_ids = ["run_axis_evidence", "run_damping_evidence"]
    hypothesis_ids = ["hyp_axis_evidence", "hyp_damping_evidence"]
    hypothesis_event_ids = ["evt_hyp_axis", "evt_hyp_damping"]
    trace_hashes: dict[str, str | None] = {
        run_ids[0]: "a" * 64,
        run_ids[1]: "b" * 64,
    }
    events = [
        SimpleNamespace(event_type="TASK_COMPLETED", payload={}),
        SimpleNamespace(
            event_type="EXPERIMENT_COMPLETED",
            payload={
                "run_id": run_ids[0],
                "hypothesis_id": hypothesis_ids[0],
                "hypothesis_event_id": hypothesis_event_ids[0],
            },
        ),
        SimpleNamespace(event_type="REVISION_CREATED", payload={}),
        SimpleNamespace(event_type="TASK_COMPLETED", payload={}),
        SimpleNamespace(
            event_type="EXPERIMENT_COMPLETED",
            payload={
                "run_id": run_ids[1],
                "hypothesis_id": hypothesis_ids[1],
                "hypothesis_event_id": hypothesis_event_ids[1],
            },
        ),
        SimpleNamespace(event_type="REVISION_CREATED", payload={}),
        SimpleNamespace(event_type="TASK_COMPLETED", payload={}),
        SimpleNamespace(event_type="QUALIFICATION_RESERVED", payload={}),
        SimpleNamespace(event_type="QUALIFICATION_PASSED", payload={}),
    ]

    class Store:
        def ledger_events(self, _case_id):
            return tuple(events)

        def list_revisions(self, _case_id):
            return (
                SimpleNamespace(
                    parent_revision_id=None,
                    probe_run_id=None,
                    hypothesis_event_id=None,
                ),
                SimpleNamespace(
                    parent_revision_id="r000",
                    probe_run_id=run_ids[0],
                    hypothesis_event_id=hypothesis_event_ids[0],
                ),
                SimpleNamespace(
                    parent_revision_id="r001",
                    probe_run_id=run_ids[1],
                    hypothesis_event_id=hypothesis_event_ids[1],
                ),
            )

        def get_run(self, run_id):
            index = run_ids.index(run_id)
            return SimpleNamespace(
                passed=True,
                trace_sha256=trace_hashes[run_id],
                revision_id=("r000" if index == 0 else "r001"),
            )

    evidence = {
        "tool_order": tool_order,
        "sandbox": {
            "runs": [
                {
                    "revision_index": index,
                    "experiment_index": index,
                    "run_id_hash": e2e_runner._sha256_text(run_id)[:12],
                    "hypothesis_id_hash": e2e_runner._sha256_text(
                        hypothesis_ids[index]
                    )[:12],
                }
                for index, run_id in enumerate(run_ids)
            ]
        },
    }
    recorder = SimpleNamespace(sequence=invoked, counts=Counter(invoked))
    facade = SimpleNamespace(recorder=recorder)
    service = SimpleNamespace(invocation_counts=Counter(invoked), store=Store())

    gates = _runtime_state_gates(evidence, facade=facade, service=service)

    assert all(gates.values())
    evidence["sandbox"]["runs"][1]["run_id_hash"] = "mismatch"
    assert not _runtime_state_gates(evidence, facade=facade, service=service)[
        "ledger_experiment_evidence_matches"
    ]
    evidence["sandbox"]["runs"][1]["run_id_hash"] = e2e_runner._sha256_text(run_ids[1])[
        :12
    ]
    trace_hashes[run_ids[1]] = None
    assert not _runtime_state_gates(evidence, facade=facade, service=service)[
        "ledger_experiment_evidence_matches"
    ]


def test_blocker_redacts_untrusted_exception_text() -> None:
    blocker = _safe_blocker(
        "model_turn",
        RuntimeError("secret raw upstream response /private/tmp/private.xml"),
        "a" * 40,
    )

    rendered = json.dumps(blocker, sort_keys=True)
    assert blocker["status"] == "blocked"
    assert blocker["stage"] == "model_turn"
    assert "secret raw upstream" not in rendered
    assert "/private/tmp/private.xml" not in rendered


def test_evidence_gate_blocker_records_only_sanitized_terminal_category() -> None:
    terminal = {
        "state": {
            "status": "error",
            "message": "Request failed (429): Rate limit; /private/tmp/private.xml",
        }
    }
    category = _terminal_failure_category(terminal)
    blocker = _safe_blocker(
        "evidence_gate",
        _EvidenceGateFailure(
            ["turn_done", "event_contract"],
            turn_status="error",
            terminal_category=category,
        ),
        "a" * 40,
    )

    assert blocker["details"] == {
        "error_type": "_EvidenceGateFailure",
        "failed_gates": ["turn_done", "event_contract"],
        "turn_status": "error",
        "terminal_category": "provider_rate_limit",
    }
    assert "/private/tmp/private.xml" not in json.dumps(blocker, sort_keys=True)
