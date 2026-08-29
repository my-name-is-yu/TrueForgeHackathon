from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "symphony_review_decision.py"
SPEC = importlib.util.spec_from_file_location("symphony_review_decision", SCRIPT)
assert SPEC and SPEC.loader
decision_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(decision_module)


HEAD = "a" * 40


def decision(**overrides):
    value = {
        "head_sha": HEAD,
        "review_head_number": 1,
        "rework_round": 0,
        "sources": {"codex": "complete", "qodo": "complete"},
        "findings": [],
        "uncertain": False,
        "gate": "merge_ready",
        "summary": "Both sources completed with no accepted findings.",
    }
    value.update(overrides)
    return value


def finding(classification: str, fingerprint: str = "finding-1"):
    return {
        "fingerprint": fingerprint,
        "title": "Finding",
        "sources": ["codex"],
        "severity": "P2",
        "acceptance_required": classification == "accept",
        "followup_alignment": "aligned",
        "yagni_risk": "none",
        "classification": classification,
        "rationale": "The packet contains matching evidence.",
        "requested_change": "Change the behavior.",
        "backlog_title": "",
    }


def test_completed_sources_without_action_can_be_merge_ready() -> None:
    assert decision_module.validate(decision(), HEAD, 1, 0) == []


def test_timeout_must_block() -> None:
    errors = decision_module.validate(
        decision(sources={"codex": "complete", "qodo": "timeout"}), HEAD, 1, 0
    )

    assert "timeout, escalation, uncertainty, or the rework limit must block" in errors


def test_accepted_finding_requires_rework() -> None:
    errors = decision_module.validate(decision(findings=[finding("accept")]), HEAD, 1, 0)

    assert "accepted findings require rework" in errors


def test_conflict_must_block() -> None:
    errors = decision_module.validate(decision(findings=[finding("conflict")]), HEAD, 1, 0)

    assert "timeout, escalation, uncertainty, or the rework limit must block" in errors


def test_rework_limit_forbids_third_round() -> None:
    errors = decision_module.validate(
        decision(findings=[finding("accept")], gate="rework", rework_round=2), HEAD, 1, 2
    )

    assert "timeout, escalation, uncertainty, or the rework limit must block" in errors


def test_duplicate_finding_fingerprints_are_rejected() -> None:
    errors = decision_module.validate(
        decision(
            findings=[finding("reject"), finding("reject")],
            gate="merge_ready",
        ),
        HEAD,
        1,
        0,
    )

    assert "duplicate finding fingerprint: finding-1" in errors


def test_uncertainty_allows_block_without_fabricating_a_finding() -> None:
    assert decision_module.validate(
        decision(uncertain=True, gate="blocked"), HEAD, 1, 0
    ) == []


def test_packet_counters_must_match_sol_output() -> None:
    errors = decision_module.validate(decision(), HEAD, 2, 1)

    assert "review_head_number must equal the expected value between 1 and 3" in errors
    assert "rework_round must equal the expected value between 0 and 2" in errors


def test_high_yagni_finding_cannot_enter_rework() -> None:
    item = finding("accept")
    item["yagni_risk"] = "high"
    errors = decision_module.validate(
        decision(findings=[item], gate="rework"), HEAD, 1, 0
    )

    assert "accepted finding 0 cannot have high YAGNI risk" in errors


def test_backlog_finding_is_not_rework_and_requires_title() -> None:
    item = finding("backlog")
    item["acceptance_required"] = False
    item["yagni_risk"] = "high"
    errors = decision_module.validate(
        decision(findings=[item], gate="merge_ready"), HEAD, 1, 0
    )

    assert "backlog finding 0 needs a narrow backlog_title" in errors


def test_valid_backlog_finding_can_continue_to_merge_gate() -> None:
    item = finding("backlog")
    item["acceptance_required"] = False
    item["yagni_risk"] = "high"
    item["backlog_title"] = "Defer shared retry abstraction to YU-200"

    assert decision_module.validate(
        decision(findings=[item], gate="merge_ready"), HEAD, 1, 0
    ) == []


def test_design_conflict_must_escalate() -> None:
    item = finding("reject")
    item["followup_alignment"] = "conflicts"
    errors = decision_module.validate(
        decision(findings=[item], gate="merge_ready"), HEAD, 1, 0
    )

    assert "finding 0 conflicts with design context and must escalate" in errors


def test_current_acceptance_requirement_cannot_be_rejected() -> None:
    item = finding("reject")
    item["acceptance_required"] = True
    errors = decision_module.validate(
        decision(findings=[item], gate="merge_ready"), HEAD, 1, 0
    )

    assert "required finding 0 cannot be rejected or deferred" in errors


def test_unknown_followup_context_cannot_create_backlog() -> None:
    item = finding("backlog")
    item["backlog_title"] = "Potential shared abstraction"
    item["followup_alignment"] = "unknown"
    errors = decision_module.validate(
        decision(findings=[item], gate="merge_ready"), HEAD, 1, 0
    )

    assert "backlog finding 0 needs known design/dependency evidence" in errors
