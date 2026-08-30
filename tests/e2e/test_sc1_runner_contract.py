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
    _case_is_qualified_and_unpublished,
    _commit_sha,
    _raw_events_are_clear,
    _runtime_state_gates,
    _safe_blocker,
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


def test_qualified_case_remains_unpublished_while_promotion_is_open() -> None:
    case = CaseRecord(
        case_id="case_compound-arm-01",
        root_revision_id="r000",
        head_revision_id="r002",
        qualification_revision_id="r002",
        qualification_attempt_id="qualify-1",
        qualification_result="PASSED",
        promoted_revision_id=None,
        source_asset_sha256="a" * 64,
        controller_sha256="b" * 64,
        public_contract_sha256="c" * 64,
        runner_sha256="d" * 64,
        holdout_commitment_sha256="e" * 64,
        created_at="2026-08-30T00:00:00+00:00",
    )

    assert case.promotion_state == "open"
    assert _case_is_qualified_and_unpublished(case)
    assert not _case_is_qualified_and_unpublished(
        replace(case, promoted_revision_id="r002")
    )


def test_commit_sha_rejects_dirty_or_untracked_execution_source(monkeypatch) -> None:
    monkeypatch.setattr(
        e2e_runner.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout="?? scripts/run_sc1_e2e.py\n"
        ),
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
    events = [
        SimpleNamespace(event_type="TASK_COMPLETED", payload={}),
        SimpleNamespace(
            event_type="EXPERIMENT_COMPLETED", payload={"run_id": run_ids[0]}
        ),
        SimpleNamespace(event_type="REVISION_CREATED", payload={}),
        SimpleNamespace(event_type="TASK_COMPLETED", payload={}),
        SimpleNamespace(
            event_type="EXPERIMENT_COMPLETED", payload={"run_id": run_ids[1]}
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
            return (object(), object(), object())

    evidence = {
        "tool_order": tool_order,
        "sandbox": {
            "runs": [
                {
                    "run_id_hash": e2e_runner._sha256_text(run_id)[:12],
                }
                for run_id in run_ids
            ]
        },
    }
    recorder = SimpleNamespace(sequence=invoked, counts=Counter(invoked))
    facade = SimpleNamespace(recorder=recorder)
    service = SimpleNamespace(invocation_counts=Counter(invoked), store=Store())

    gates = _runtime_state_gates(evidence, facade=facade, service=service)

    assert all(gates.values())
    evidence["sandbox"]["runs"][1]["run_id_hash"] = "mismatch"
    assert not _runtime_state_gates(
        evidence, facade=facade, service=service
    )["ledger_experiment_evidence_matches"]


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
