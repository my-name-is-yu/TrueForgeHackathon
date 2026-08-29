from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "symphony_review_decision.py"
SPEC = importlib.util.spec_from_file_location("symphony_review_decision", SCRIPT)
assert SPEC and SPEC.loader
decision_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(decision_module)


HEAD = "a" * 40


def validate_decision(
    value,
    expected_head=HEAD,
    expected_review_head=1,
    expected_rework_round=0,
    expected_codex_status="complete",
    expected_qodo_status="complete",
):
    return decision_module.validate(
        value,
        expected_head,
        expected_review_head,
        expected_rework_round,
        expected_codex_status,
        expected_qodo_status,
    )


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
    assert validate_decision(decision(), HEAD, 1, 0) == []


def test_timeout_must_block() -> None:
    errors = validate_decision(
        decision(sources={"codex": "complete", "qodo": "timeout"}),
        HEAD,
        1,
        0,
        expected_qodo_status="timeout",
    )

    assert "timeout, escalation, uncertainty, or the rework limit must block" in errors


def test_accepted_finding_requires_rework() -> None:
    errors = validate_decision(decision(findings=[finding("accept")]), HEAD, 1, 0)

    assert "accepted findings require rework" in errors


def test_conflict_must_block() -> None:
    errors = validate_decision(decision(findings=[finding("conflict")]), HEAD, 1, 0)

    assert "timeout, escalation, uncertainty, or the rework limit must block" in errors


def test_rework_limit_forbids_an_eleventh_reviewed_head() -> None:
    errors = validate_decision(
        decision(
            findings=[finding("accept")],
            gate="rework",
            review_head_number=10,
            rework_round=2,
        ),
        HEAD,
        10,
        2,
    )

    assert "timeout, escalation, uncertainty, or the rework limit must block" in errors


def test_tenth_reviewed_head_can_be_merge_ready_when_clean() -> None:
    assert validate_decision(
        decision(review_head_number=10, rework_round=9), HEAD, 10, 9
    ) == []


def test_external_head_change_does_not_consume_a_rework_round() -> None:
    assert validate_decision(
        decision(review_head_number=3, rework_round=1), HEAD, 3, 1
    ) == []


def test_rework_round_cannot_reach_the_reviewed_head_count() -> None:
    errors = validate_decision(
        decision(
            findings=[finding("accept")],
            gate="rework",
            review_head_number=2,
            rework_round=2,
        ),
        HEAD,
        2,
        2,
    )

    assert "rework_round must be lower than review_head_number" in errors


def test_duplicate_finding_fingerprints_are_rejected() -> None:
    errors = validate_decision(
        decision(
            findings=[finding("reject"), finding("reject")],
            gate="merge_ready",
        ),
        HEAD,
        1,
        0,
    )

    assert "duplicate finding fingerprint: finding-1" in errors


def test_duplicate_finding_sources_are_rejected() -> None:
    item = finding("reject")
    item["sources"] = ["codex", "codex"]

    errors = validate_decision(
        decision(findings=[item], gate="merge_ready"), HEAD, 1, 0
    )

    assert "finding 0 has duplicate sources" in errors


def test_unknown_finding_source_is_rejected() -> None:
    item = finding("reject")
    item["sources"] = ["unknown"]

    errors = validate_decision(
        decision(findings=[item], gate="merge_ready"), HEAD, 1, 0
    )

    assert "finding 0 has an invalid source" in errors


def test_unhashable_finding_sources_are_rejected_without_crashing() -> None:
    for invalid_source in (["codex"], {"name": "codex"}):
        item = finding("reject")
        item["sources"] = [invalid_source]

        errors = validate_decision(
            decision(findings=[item], gate="merge_ready"), HEAD, 1, 0
        )

        assert "finding 0 has an invalid source" in errors


def test_uncertainty_allows_block_without_fabricating_a_finding() -> None:
    assert validate_decision(
        decision(uncertain=True, gate="blocked"), HEAD, 1, 0
    ) == []


def test_packet_counters_must_match_sol_output() -> None:
    errors = validate_decision(decision(), HEAD, 11, 10)

    assert "review_head_number must equal the expected value between 1 and 10" in errors
    assert "rework_round must equal the expected value between 0 and 9" in errors


def test_high_yagni_finding_cannot_enter_rework() -> None:
    item = finding("accept")
    item["yagni_risk"] = "high"
    errors = validate_decision(
        decision(findings=[item], gate="rework"), HEAD, 1, 0
    )

    assert "accepted finding 0 cannot have high YAGNI risk" in errors


def test_accepted_finding_requires_a_concrete_change() -> None:
    item = finding("accept")
    item["requested_change"] = "   "
    errors = validate_decision(
        decision(findings=[item], gate="rework"), HEAD, 1, 0
    )

    assert "finding 0 needs a concrete requested_change" in errors


def test_backlog_finding_is_not_rework_and_requires_title() -> None:
    item = finding("backlog")
    item["acceptance_required"] = False
    item["yagni_risk"] = "high"
    errors = validate_decision(
        decision(findings=[item], gate="merge_ready"), HEAD, 1, 0
    )

    assert "backlog finding 0 needs a narrow backlog_title" in errors


def test_valid_backlog_finding_can_continue_to_merge_gate() -> None:
    item = finding("backlog")
    item["acceptance_required"] = False
    item["yagni_risk"] = "high"
    item["backlog_title"] = "Defer shared retry abstraction to YU-200"

    assert validate_decision(
        decision(findings=[item], gate="merge_ready"), HEAD, 1, 0
    ) == []


def test_design_conflict_must_escalate() -> None:
    item = finding("reject")
    item["followup_alignment"] = "conflicts"
    errors = validate_decision(
        decision(findings=[item], gate="merge_ready"), HEAD, 1, 0
    )

    assert "finding 0 conflicts with design context and must escalate" in errors


def test_current_acceptance_requirement_cannot_be_rejected() -> None:
    item = finding("reject")
    item["acceptance_required"] = True
    errors = validate_decision(
        decision(findings=[item], gate="merge_ready"), HEAD, 1, 0
    )

    assert "required finding 0 cannot be rejected or deferred" in errors


def test_unknown_followup_context_cannot_create_backlog() -> None:
    item = finding("backlog")
    item["backlog_title"] = "Potential shared abstraction"
    item["followup_alignment"] = "unknown"
    errors = validate_decision(
        decision(findings=[item], gate="merge_ready"), HEAD, 1, 0
    )

    assert "backlog finding 0 needs known design/dependency evidence" in errors


def test_decision_cannot_override_trusted_review_timeout() -> None:
    errors = validate_decision(
        decision(),
        expected_qodo_status="timeout",
    )

    assert "decision sources must equal the trusted packet statuses" in errors
    assert "timeout, escalation, uncertainty, or the rework limit must block" in errors
