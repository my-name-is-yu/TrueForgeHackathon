#!/usr/bin/env python3
"""Small, auditable Linear mutations used by the repository-owned Symphony workflow."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


LINEAR_ENDPOINT = "https://api.linear.app/graphql"
WORKPAD_MARKER = "<!-- symphony-workpad:v1 -->"
BACKLOG_MARKER_PREFIX = "<!-- symphony-backlog:"
REQUIRED_WORKPAD_HEADINGS = (
    "## Objective and acceptance",
    "## Design, dependencies, and frozen contracts",
    "## Plan and next action",
    "## Pull request and current head",
    "## Verification",
    "## Review ledger",
    "## Decisions and evidence",
    "## Follow-up candidates",
)
SECRET_PATTERNS = (
    re.compile(r"\blin_api_[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bgh[opusr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
)


class LinearError(RuntimeError):
    pass


def graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    api_key = os.environ.get("LINEAR_API_KEY")
    if not api_key:
        raise LinearError("LINEAR_API_KEY is required")
    request = urllib.request.Request(
        LINEAR_ENDPOINT,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except (OSError, urllib.error.URLError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LinearError(f"Linear request failed: {type(error).__name__}") from error
    if not isinstance(payload, dict) or payload.get("errors"):
        messages = [item.get("message", "unknown error") for item in payload.get("errors", [])]
        raise LinearError("Linear GraphQL error: " + "; ".join(messages))
    data = payload.get("data")
    if not isinstance(data, dict):
        raise LinearError("Linear returned an invalid response")
    return data


def issue_context(identifier: str) -> dict[str, Any]:
    comments: list[dict[str, Any]] = []
    after: str | None = None
    seen_cursors: set[str] = set()
    context: dict[str, Any] | None = None
    while True:
        data = graphql(
            """
            query SymphonyIssue($id: String!, $after: String) {
              issue(id: $id) {
                id identifier url description
                team { id }
                project { id }
                comments(first: 100, after: $after) {
                  nodes { id body }
                  pageInfo { hasNextPage endCursor }
                }
              }
            }
            """,
            {"id": identifier, "after": after},
        )
        issue = data.get("issue")
        if not isinstance(issue, dict):
            raise LinearError(f"Linear issue not found: {identifier}")
        if context is None:
            context = issue.copy()
        connection = issue.get("comments", {})
        comments.extend(connection.get("nodes", []))
        after = next_cursor(connection, seen_cursors, "issue comments")
        if after is None:
            break
    assert context is not None
    context["comments"] = {"nodes": comments}
    return context


def next_cursor(
    connection: dict[str, Any], seen_cursors: set[str], connection_name: str
) -> str | None:
    page_info = connection.get("pageInfo")
    if not isinstance(page_info, dict) or page_info.get("hasNextPage") is not True:
        return None
    cursor = page_info.get("endCursor")
    if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
        raise LinearError(f"{connection_name} pagination did not advance")
    seen_cursors.add(cursor)
    return cursor


def project_issues(project_id: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    after: str | None = None
    seen_cursors: set[str] = set()
    while True:
        data = graphql(
            """
            query ProjectBacklogCandidates($projectId: ID!, $after: String) {
              issues(first: 250, after: $after, filter: {project: {id: {eq: $projectId}}}) {
                nodes { id identifier title description url }
                pageInfo { hasNextPage endCursor }
              }
            }
            """,
            {"projectId": project_id, "after": after},
        )
        connection = data.get("issues")
        if not isinstance(connection, dict):
            raise LinearError("Linear returned an invalid project issue connection")
        issues.extend(connection.get("nodes", []))
        after = next_cursor(connection, seen_cursors, "project issues")
        if after is None:
            return issues


def find_workpad(issue: dict[str, Any]) -> dict[str, Any] | None:
    comments = issue.get("comments", {}).get("nodes", [])
    matches = [comment for comment in comments if WORKPAD_MARKER in comment.get("body", "")]
    if len(matches) > 1:
        raise LinearError("multiple Symphony Workpad comments exist; refusing to guess")
    return matches[0] if matches else None


def workpad_template(identifier: str) -> str:
    return f"""{WORKPAD_MARKER}
# Symphony Workpad — {identifier}

## Objective and acceptance
- Objective:
- Acceptance criteria:

## Design, dependencies, and frozen contracts
- Current design boundary:
- Known prerequisite and follow-up issues:
- Frozen contracts and decisions:

## Plan and next action
- Current phase: implementation
- Next action:
- Rework round: 0 / 2
- Reviewed heads: 0 / 3

## Pull request and current head
- PR: not created
- Head branch:
- Head SHA:
- Base branch: main

## Verification
- Required checks:
- Latest results:
- Base synchronization:

## Review ledger
- OpenAI Codex review:
- Qodo review:
- Quiet-period confirmation:
- Late-comment recheck:

## Decisions and evidence
- No review decisions yet. For each finding record acceptance necessity, dependency/contract
  alignment, YAGNI risk, disposition, and rationale.

## Follow-up candidates
- None.
"""


def read_safe_body(path: Path) -> str:
    body = path.read_text(encoding="utf-8")
    for pattern in SECRET_PATTERNS:
        if pattern.search(body):
            raise LinearError("body appears to contain a secret; refusing to publish it")
    return body


def validate_workpad(body: str, identifier: str) -> None:
    if body.count(WORKPAD_MARKER) != 1:
        raise LinearError("workpad must contain exactly one version marker")
    if identifier not in body:
        raise LinearError("workpad must name its Linear issue identifier")
    missing = [heading for heading in REQUIRED_WORKPAD_HEADINGS if heading not in body]
    if missing:
        raise LinearError("workpad is missing headings: " + ", ".join(missing))


def get_workpad(identifier: str) -> int:
    issue = issue_context(identifier)
    comment = find_workpad(issue)
    print(comment["body"] if comment else workpad_template(identifier))
    return 0


def upsert_workpad(identifier: str, body_path: Path) -> int:
    body = read_safe_body(body_path)
    validate_workpad(body, identifier)
    issue = issue_context(identifier)
    comment = find_workpad(issue)
    if comment:
        data = graphql(
            "mutation UpdateWorkpad($id: String!, $body: String!) {"
            " commentUpdate(id: $id, input: {body: $body}) { success comment { id } } }",
            {"id": comment["id"], "body": body},
        )
        result = data.get("commentUpdate", {})
        action = "updated"
    else:
        data = graphql(
            "mutation CreateWorkpad($issueId: String!, $body: String!) {"
            " commentCreate(input: {issueId: $issueId, body: $body})"
            " { success comment { id } } }",
            {"issueId": issue["id"], "body": body},
        )
        result = data.get("commentCreate", {})
        action = "created"
    if result.get("success") is not True:
        raise LinearError(f"workpad was not {action}")
    print(json.dumps({"action": action, "comment_id": result["comment"]["id"]}))
    return 0


def set_state(identifier: str, state_name: str) -> int:
    issue = issue_context(identifier)
    team_id = issue["team"]["id"]
    data = graphql(
        """
        query SymphonyStates($teamId: ID!) {
          workflowStates(filter: {team: {id: {eq: $teamId}}}) { nodes { id name } }
        }
        """,
        {"teamId": team_id},
    )
    states = [state for state in data.get("workflowStates", {}).get("nodes", []) if state["name"] == state_name]
    if len(states) != 1:
        raise LinearError(f"expected exactly one state named {state_name!r}; found {len(states)}")
    result = graphql(
        "mutation MoveIssue($id: String!, $stateId: String!) {"
        " issueUpdate(id: $id, input: {stateId: $stateId}) { success issue { url } } }",
        {"id": issue["id"], "stateId": states[0]["id"]},
    ).get("issueUpdate", {})
    if result.get("success") is not True:
        raise LinearError("issue state was not updated")
    print(json.dumps({"state": state_name, "url": result["issue"]["url"]}))
    return 0


def backlog_fingerprint(source_identifier: str, title: str) -> str:
    normalized = " ".join(title.lower().split())
    return hashlib.sha256(f"{source_identifier}\n{normalized}".encode()).hexdigest()[:24]


def public_issue(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        key: issue.get(key)
        for key in ("id", "identifier", "title", "url")
        if issue.get(key) is not None
    }


def create_backlog_candidate(source_identifier: str, title: str, body_path: Path) -> int:
    title = " ".join(title.split())
    if not title or len(title) > 120:
        raise LinearError("backlog candidate title must contain 1 to 120 characters")
    description = read_safe_body(body_path).strip()
    if not description:
        raise LinearError("backlog candidate description must not be empty")
    source = issue_context(source_identifier)
    if BACKLOG_MARKER_PREFIX in (source.get("description") or ""):
        raise LinearError("an automatically created candidate cannot create another candidate")
    project = source.get("project")
    if not isinstance(project, dict):
        raise LinearError("source issue is not assigned to a project")
    fingerprint = backlog_fingerprint(source_identifier, title)
    marker = f"{BACKLOG_MARKER_PREFIX}{fingerprint} -->"
    existing_issues = project_issues(project["id"])
    duplicates = [
        issue
        for issue in existing_issues
        if marker in (issue.get("description") or "")
    ]
    if duplicates:
        print(
            json.dumps(
                {
                    "action": "duplicate",
                    "fingerprint": fingerprint,
                    "issue": public_issue(duplicates[0]),
                }
            )
        )
        return 0
    source_marker = f"<!-- symphony-backlog-source:{source_identifier} -->"
    source_candidates = [
        issue
        for issue in existing_issues
        if source_marker in (issue.get("description") or "")
    ]
    if len(source_candidates) >= 3:
        raise LinearError("source issue already has the maximum of three Backlog candidates")

    states = graphql(
        """
        query BacklogState($teamId: ID!) {
          workflowStates(filter: {team: {id: {eq: $teamId}}}) { nodes { id name } }
        }
        """,
        {"teamId": source["team"]["id"]},
    ).get("workflowStates", {}).get("nodes", [])
    backlog_states = [state for state in states if state["name"] == "Backlog"]
    if len(backlog_states) != 1:
        raise LinearError("expected exactly one Backlog state")
    full_description = (
        f"{marker}\n{source_marker}\n\n"
        f"Discovered while working on [{source_identifier}]({source['url']}).\n\n"
        f"{description}\n\nThis candidate is not authorized for automatic execution."
    )
    result = graphql(
        """
        mutation CreateBacklogCandidate($input: IssueCreateInput!) {
          issueCreate(input: $input) { success issue { id identifier title url } }
        }
        """,
        {
            "input": {
                "teamId": source["team"]["id"],
                "projectId": project["id"],
                "stateId": backlog_states[0]["id"],
                "title": title,
                "description": full_description,
            }
        },
    ).get("issueCreate", {})
    if result.get("success") is not True:
        raise LinearError("backlog candidate was not created")
    print(json.dumps({"action": "created", "fingerprint": fingerprint, "issue": result["issue"]}))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    workpad = commands.add_parser("workpad")
    workpad_commands = workpad.add_subparsers(dest="workpad_command", required=True)
    get = workpad_commands.add_parser("get")
    get.add_argument("issue")
    upsert = workpad_commands.add_parser("upsert")
    upsert.add_argument("issue")
    upsert.add_argument("--file", required=True, type=Path)
    state = commands.add_parser("state")
    state.add_argument("issue")
    state.add_argument("name")
    backlog = commands.add_parser("backlog")
    backlog.add_argument("source_issue")
    backlog.add_argument("--title", required=True)
    backlog.add_argument("--body-file", required=True, type=Path)
    return root


def main() -> int:
    arguments = parser().parse_args()
    try:
        if arguments.command == "workpad" and arguments.workpad_command == "get":
            return get_workpad(arguments.issue)
        if arguments.command == "workpad" and arguments.workpad_command == "upsert":
            return upsert_workpad(arguments.issue, arguments.file)
        if arguments.command == "state":
            return set_state(arguments.issue, arguments.name)
        if arguments.command == "backlog":
            return create_backlog_candidate(arguments.source_issue, arguments.title, arguments.body_file)
    except (LinearError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
