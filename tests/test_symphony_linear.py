from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "symphony_linear.py"
SPEC = importlib.util.spec_from_file_location("symphony_linear", SCRIPT)
assert SPEC and SPEC.loader
symphony_linear = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(symphony_linear)


def issue(comments=None):
    return {
        "id": "issue-id",
        "identifier": "YU-123",
        "url": "https://linear.example/YU-123",
        "description": "Source issue",
        "team": {"id": "team-id"},
        "project": {"id": "project-id"},
        "comments": {"nodes": comments or []},
    }


def test_workpad_template_has_all_required_sections() -> None:
    body = symphony_linear.workpad_template("YU-123")

    symphony_linear.validate_workpad(body, "YU-123")
    assert body.count(symphony_linear.WORKPAD_MARKER) == 1
    assert "Rework round: 0\n" in body
    assert "Reviewed heads: 0\n" in body
    assert "Rework round: 0 /" not in body
    assert "Reviewed heads: 0 /" not in body


def test_find_workpad_refuses_ambiguous_comments() -> None:
    marked = {"id": "one", "body": symphony_linear.WORKPAD_MARKER}

    with pytest.raises(symphony_linear.LinearError, match="multiple"):
        symphony_linear.find_workpad(issue([marked, {**marked, "id": "two"}]))


def test_issue_context_collects_workpad_from_later_comment_page(monkeypatch) -> None:
    pages = iter(
        [
            {
                "issue": {
                    **issue(),
                    "comments": {
                        "nodes": [{"id": "ordinary", "body": "ordinary"}],
                        "pageInfo": {"hasNextPage": True, "endCursor": "page-2"},
                    },
                }
            },
            {
                "issue": {
                    **issue(),
                    "comments": {
                        "nodes": [{"id": "workpad", "body": symphony_linear.WORKPAD_MARKER}],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    },
                }
            },
        ]
    )
    calls = []

    def fake_graphql(_query, variables):
        calls.append(variables)
        return next(pages)

    monkeypatch.setattr(symphony_linear, "graphql", fake_graphql)

    context = symphony_linear.issue_context("YU-123")

    assert symphony_linear.find_workpad(context)["id"] == "workpad"
    assert calls == [
        {"id": "YU-123", "after": None},
        {"id": "YU-123", "after": "page-2"},
    ]


def test_read_safe_body_rejects_likely_secret(tmp_path: Path) -> None:
    body = tmp_path / "body.md"
    body.write_text("token: lin_api_abcdefghijklmnopqrstuvwxyz")

    with pytest.raises(symphony_linear.LinearError, match="secret"):
        symphony_linear.read_safe_body(body)


def test_read_safe_body_rejects_fine_grained_github_pat(tmp_path: Path) -> None:
    body = tmp_path / "body.md"
    body.write_text("token: github_pat_abcdefghijklmnopqrstuvwxyz123456")

    with pytest.raises(symphony_linear.LinearError, match="secret"):
        symphony_linear.read_safe_body(body)


def test_graphql_rejects_a_non_object_payload(monkeypatch) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setenv("LINEAR_API_KEY", "test-key")
    monkeypatch.setattr(symphony_linear.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(json, "load", lambda _response: [])

    with pytest.raises(symphony_linear.LinearError, match="invalid response"):
        symphony_linear.graphql("query", {})


def test_upsert_workpad_updates_existing_comment(tmp_path: Path, monkeypatch) -> None:
    body = tmp_path / "workpad.md"
    body.write_text(symphony_linear.workpad_template("YU-123"))
    existing = {"id": "comment-id", "body": symphony_linear.WORKPAD_MARKER}
    calls = []
    monkeypatch.setattr(symphony_linear, "issue_context", lambda _identifier: issue([existing]))

    def fake_graphql(query, variables):
        calls.append((query, variables))
        return {"commentUpdate": {"success": True, "comment": {"id": "comment-id"}}}

    monkeypatch.setattr(symphony_linear, "graphql", fake_graphql)

    assert symphony_linear.upsert_workpad("YU-123", body) == 0
    assert len(calls) == 1
    assert calls[0][1]["id"] == "comment-id"


def test_backlog_fingerprint_is_stable_for_title_whitespace_and_case() -> None:
    first = symphony_linear.backlog_fingerprint("YU-123", "Improve   retry policy")
    second = symphony_linear.backlog_fingerprint("YU-123", " improve RETRY policy ")

    assert first == second
    assert len(first) == 24


def test_backlog_returns_existing_fingerprint_without_create(tmp_path: Path, monkeypatch) -> None:
    body = tmp_path / "candidate.md"
    body.write_text("Evidence and a narrow acceptance criterion.")
    fingerprint = symphony_linear.backlog_fingerprint("YU-123", "Follow up")
    queries = []
    monkeypatch.setattr(symphony_linear, "issue_context", lambda _identifier: issue())

    def fake_project_issues(project_id):
        queries.append(project_id)
        return [
            {
                "id": "duplicate",
                "identifier": "YU-999",
                "title": "Follow up",
                "description": f"<!-- symphony-backlog:{fingerprint} -->",
                "url": "https://linear.example/YU-999",
            }
        ]

    monkeypatch.setattr(symphony_linear, "project_issues", fake_project_issues)

    assert symphony_linear.create_backlog_candidate("YU-123", "Follow up", body) == 0
    assert len(queries) == 1


def test_project_issue_scan_paginates_before_deduplication(monkeypatch) -> None:
    pages = iter(
        [
            {
                "issues": {
                    "nodes": [{"identifier": "YU-1"}],
                    "pageInfo": {"hasNextPage": True, "endCursor": "page-2"},
                }
            },
            {
                "issues": {
                    "nodes": [{"identifier": "YU-2"}],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            },
        ]
    )
    calls = []

    def fake_graphql(_query, variables):
        calls.append(variables)
        return next(pages)

    monkeypatch.setattr(symphony_linear, "graphql", fake_graphql)

    assert [item["identifier"] for item in symphony_linear.project_issues("project-id")] == [
        "YU-1",
        "YU-2",
    ]
    assert calls == [
        {"projectId": "project-id", "after": None},
        {"projectId": "project-id", "after": "page-2"},
    ]


def test_set_state_refuses_missing_exact_state(monkeypatch) -> None:
    monkeypatch.setattr(symphony_linear, "issue_context", lambda _identifier: issue())
    monkeypatch.setattr(
        symphony_linear,
        "graphql",
        lambda *_args: {"workflowStates": {"nodes": [{"id": "one", "name": "Todo"}]}},
    )

    with pytest.raises(symphony_linear.LinearError, match="exactly one"):
        symphony_linear.set_state("YU-123", "Merge Ready")


def test_backlog_refuses_recursive_candidate(tmp_path: Path, monkeypatch) -> None:
    body = tmp_path / "candidate.md"
    body.write_text("Evidence.")
    source = issue()
    source["description"] = "<!-- symphony-backlog:abc -->"
    monkeypatch.setattr(symphony_linear, "issue_context", lambda _identifier: source)

    with pytest.raises(symphony_linear.LinearError, match="cannot create another"):
        symphony_linear.create_backlog_candidate("YU-123", "Follow up", body)


def test_backlog_enforces_three_candidate_limit(tmp_path: Path, monkeypatch) -> None:
    body = tmp_path / "candidate.md"
    body.write_text("Evidence.")
    marker = "<!-- symphony-backlog-source:YU-123 -->"
    monkeypatch.setattr(symphony_linear, "issue_context", lambda _identifier: issue())
    monkeypatch.setattr(
        symphony_linear,
        "project_issues",
        lambda _project_id: [
            {"description": marker, "identifier": f"YU-{index}", "url": "url"}
            for index in range(3)
        ],
    )

    with pytest.raises(symphony_linear.LinearError, match="maximum of three"):
        symphony_linear.create_backlog_candidate("YU-123", "Fourth follow up", body)
