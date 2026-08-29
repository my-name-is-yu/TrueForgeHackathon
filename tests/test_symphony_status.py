from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "symphony_status.py"
SPEC = importlib.util.spec_from_file_location("symphony_status", SCRIPT)
assert SPEC and SPEC.loader
symphony_status = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(symphony_status)


def test_parse_launchctl_uses_top_level_service_fields() -> None:
    parsed = symphony_status.parse_launchctl(
        """
        state = running
        runs = 4
        pid = 57521
        last exit code = 1
            state = active
        """
    )

    assert parsed == {
        "label": "com.trueforge.symphony",
        "state": "running",
        "pid": 57521,
        "runs": 4,
        "last_exit_code": 1,
    }


def test_collect_without_runtime_is_valid_idle_snapshot(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        symphony_status,
        "service_status",
        lambda: {
            "label": "com.trueforge.symphony",
            "state": "running",
            "pid": 42,
            "runs": 1,
            "last_exit_code": None,
        },
    )
    monkeypatch.setattr(symphony_status, "agent_processes", lambda: [])
    monkeypatch.setattr(symphony_status, "repository_slug", lambda _: "owner/repo")
    monkeypatch.setattr(symphony_status, "github_available", lambda _: False)

    status = symphony_status.collect(tmp_path, include_github=False)

    assert status["health"] == "idle"
    assert status["agents"] == []
    assert status["workspaces"] == []
    assert status["github"] == {
        "enabled": False,
        "available": False,
        "repository": "owner/repo",
    }
    assert status["linear"] == {"available": False}
    json.dumps(status)


def test_classify_health_requires_activity_not_only_a_pid() -> None:
    service = {"state": "running"}
    agents = [{"pid": 42}]

    assert symphony_status.classify_health(service, agents, 10) == "working"
    assert symphony_status.classify_health(service, agents, 301) == "stalled_candidate"


def test_pull_request_summarizes_checks(monkeypatch) -> None:
    payload = [
        {
            "number": 12,
            "url": "https://github.com/owner/repo/pull/12",
            "state": "OPEN",
            "isDraft": False,
            "reviewDecision": "REVIEW_REQUIRED",
            "mergeStateStatus": "UNSTABLE",
            "statusCheckRollup": [
                {"conclusion": "SUCCESS"},
                {"conclusion": "FAILURE"},
                {"conclusion": ""},
            ],
        }
    ]
    monkeypatch.setattr(
        symphony_status,
        "run",
        lambda *_args, **_kwargs: type(
            "Result", (), {"returncode": 0, "stdout": json.dumps(payload)}
        )(),
    )

    pr = symphony_status.pull_request("owner/repo", "yu/change")

    assert pr is not None
    assert pr["number"] == 12
    assert pr["checks"] == {"total": 3, "failing": 1, "pending": 1}


def test_linear_issue_returns_state_without_exposing_token(monkeypatch) -> None:
    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    response = {
        "data": {
            "issue": {
                "identifier": "YU-21",
                "url": "https://linear.app/example/YU-21",
                "state": {"name": "In Progress", "type": "started"},
            }
        }
    }
    captured = {}

    def open_request(request, timeout):
        captured["authorization"] = request.headers["Authorization"]
        captured["timeout"] = timeout
        return Response(json.dumps(response).encode())

    monkeypatch.setattr(symphony_status.urllib.request, "urlopen", open_request)

    issue = symphony_status.linear_issue("YU-21", "secret-token")

    assert issue == response["data"]["issue"]
    assert captured == {"authorization": "secret-token", "timeout": 10}
