#!/usr/bin/env python3
"""Validate the safety invariants of a Sol review decision."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
CLASSIFICATIONS = {"accept", "backlog", "reject", "conflict", "human"}
GATES = {"rework", "merge_ready", "blocked"}


def validate(
    decision: Any,
    expected_head: str,
    expected_review_head: int,
    expected_rework_round: int,
    expected_codex_status: str,
    expected_qodo_status: str,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(decision, dict):
        return ["decision must be a JSON object"]
    if decision.get("head_sha") != expected_head or not SHA_PATTERN.fullmatch(expected_head):
        errors.append("head_sha must equal the expected 40-character lowercase SHA")
    if decision.get("review_head_number") != expected_review_head or expected_review_head not in range(1, 11):
        errors.append("review_head_number must equal the expected value between 1 and 10")
    if decision.get("rework_round") != expected_rework_round or expected_rework_round not in range(10):
        errors.append("rework_round must equal the expected value between 0 and 9")
    if expected_review_head in range(1, 11) and expected_rework_round >= expected_review_head:
        errors.append("rework_round must be lower than review_head_number")
    sources = decision.get("sources")
    if not isinstance(sources, dict) or set(sources) != {"codex", "qodo"}:
        errors.append("sources must contain exactly codex and qodo")
        sources = {}
    elif any(value not in {"complete", "timeout"} for value in sources.values()):
        errors.append("each review source must be complete or timeout")
    expected_sources = {
        "codex": expected_codex_status,
        "qodo": expected_qodo_status,
    }
    if any(value not in {"complete", "timeout"} for value in expected_sources.values()):
        errors.append("expected review sources must be complete or timeout")
    elif sources != expected_sources:
        errors.append("decision sources must equal the trusted packet statuses")
    findings = decision.get("findings")
    if not isinstance(findings, list):
        errors.append("findings must be a list")
        findings = []
    fingerprints: set[str] = set()
    classifications: list[str] = []
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            errors.append(f"finding {index} must be an object")
            continue
        fingerprint = finding.get("fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            errors.append(f"finding {index} needs a fingerprint")
        elif fingerprint in fingerprints:
            errors.append(f"duplicate finding fingerprint: {fingerprint}")
        else:
            fingerprints.add(fingerprint)
        classification = finding.get("classification")
        if classification not in CLASSIFICATIONS:
            errors.append(f"finding {index} has an invalid classification")
        else:
            classifications.append(classification)
        if not isinstance(finding.get("rationale"), str) or not finding["rationale"].strip():
            errors.append(f"finding {index} needs a rationale")
        requested_change = finding.get("requested_change")
        if not isinstance(requested_change, str) or not requested_change.strip():
            errors.append(f"finding {index} needs a concrete requested_change")
        finding_sources = finding.get("sources")
        if not isinstance(finding_sources, list) or not finding_sources:
            errors.append(f"finding {index} needs at least one source")
        elif any(
            not isinstance(source, str) or source not in {"codex", "qodo"}
            for source in finding_sources
        ):
            errors.append(f"finding {index} has an invalid source")
        elif len(finding_sources) != len(set(finding_sources)):
            errors.append(f"finding {index} has duplicate sources")
        acceptance_required = finding.get("acceptance_required")
        if not isinstance(acceptance_required, bool):
            errors.append(f"finding {index} needs a boolean acceptance_required")
        followup_alignment = finding.get("followup_alignment")
        if followup_alignment not in {"aligned", "conflicts", "unknown", "not_applicable"}:
            errors.append(f"finding {index} has an invalid followup_alignment")
        yagni_risk = finding.get("yagni_risk")
        if yagni_risk not in {"none", "low", "high"}:
            errors.append(f"finding {index} has an invalid yagni_risk")
        backlog_title = finding.get("backlog_title")
        if not isinstance(backlog_title, str):
            errors.append(f"finding {index} needs a backlog_title string")
            backlog_title = ""
        if classification == "accept":
            if acceptance_required is not True:
                errors.append(f"accepted finding {index} must be required by current acceptance")
            if yagni_risk == "high":
                errors.append(f"accepted finding {index} cannot have high YAGNI risk")
            if followup_alignment in {"conflicts", "unknown"}:
                errors.append(f"accepted finding {index} must align with known design context")
        if classification == "backlog":
            if acceptance_required is not False:
                errors.append(f"backlog finding {index} must not be required by current acceptance")
            if not backlog_title.strip() or len(backlog_title.strip()) > 120:
                errors.append(f"backlog finding {index} needs a narrow backlog_title")
            if followup_alignment == "unknown":
                errors.append(f"backlog finding {index} needs known design/dependency evidence")
        elif backlog_title.strip():
            errors.append(f"non-backlog finding {index} must not set backlog_title")
        if acceptance_required is True and classification not in {"accept", "conflict", "human"}:
            errors.append(f"required finding {index} cannot be rejected or deferred")
        if followup_alignment == "conflicts" and classification not in {"conflict", "human"}:
            errors.append(f"finding {index} conflicts with design context and must escalate")
    gate = decision.get("gate")
    if gate not in GATES:
        errors.append("gate must be rework, merge_ready, or blocked")
    uncertain = decision.get("uncertain")
    if not isinstance(uncertain, bool):
        errors.append("uncertain must be a boolean")
        uncertain = True
    has_timeout = "timeout" in expected_sources.values()
    has_escalation = any(value in {"conflict", "human"} for value in classifications)
    has_accept = "accept" in classifications
    limit_reached = has_accept and expected_review_head == 10
    must_block = has_timeout or has_escalation or uncertain or limit_reached
    if must_block and gate != "blocked":
        errors.append("timeout, escalation, uncertainty, or the rework limit must block")
    elif not must_block and has_accept and gate != "rework":
        errors.append("accepted findings require rework")
    elif not must_block and not has_accept:
        if sources == {"codex": "complete", "qodo": "complete"} and gate != "merge_ready":
            errors.append("completed reviews with no accepted findings must be merge_ready")
    if not isinstance(decision.get("summary"), str) or not decision["summary"].strip():
        errors.append("summary is required")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("decision", type=Path)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--expected-review-head", required=True, type=int)
    parser.add_argument("--expected-rework-round", required=True, type=int)
    parser.add_argument("--expected-codex-status", required=True, choices=("complete", "timeout"))
    parser.add_argument("--expected-qodo-status", required=True, choices=("complete", "timeout"))
    arguments = parser.parse_args()
    try:
        decision = json.loads(arguments.decision.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"error: could not read decision JSON: {error}", file=sys.stderr)
        return 1
    errors = validate(
        decision,
        arguments.expected_head,
        arguments.expected_review_head,
        arguments.expected_rework_round,
        arguments.expected_codex_status,
        arguments.expected_qodo_status,
    )
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(decision, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
