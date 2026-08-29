from __future__ import annotations

import importlib.util
import http.client
import io
import json
import os
import threading
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
    monkeypatch.setattr(symphony_status, "agent_processes", lambda _pid: [])
    status = symphony_status.collect(tmp_path)

    assert status["health"] == "idle"
    assert status["agents"] == []
    assert status["workspaces"] == []
    assert status["linear"] == {"status": "disabled"}
    json.dumps(status)


def test_classify_health_reports_runtime_state_without_inferring_progress() -> None:
    service = {"state": "running"}
    agents = [{"pid": 42}]

    assert symphony_status.classify_health(service, agents) == "running"


def test_agent_processes_only_returns_symphony_descendants(monkeypatch) -> None:
    process_table = """
      110 100 00:10 beam-child
      120 110 00:09 node codex app-server
      121 120 00:09 binary codex app-server
      200   1 00:05 unrelated codex app-server
    """
    monkeypatch.setattr(
        symphony_status,
        "run",
        lambda *_args, **_kwargs: type(
            "Result", (), {"returncode": 0, "stdout": process_table}
        )(),
    )

    assert symphony_status.agent_processes(100) == [
        {"pid": 120, "ppid": 110, "elapsed": "00:09"}
    ]


def test_collect_uses_newest_active_or_rotated_structured_log(tmp_path: Path, monkeypatch) -> None:
    log_dir = tmp_path / ".symphony" / "logs" / "log"
    log_dir.mkdir(parents=True)
    active = log_dir / "symphony.log"
    rotated = log_dir / "symphony.log.1"
    active.write_text("old")
    rotated.write_text("current")
    os.utime(active, (100, 100))
    os.utime(rotated, (200, 200))
    monkeypatch.setattr(
        symphony_status,
        "service_status",
        lambda: {"state": "running", "pid": 42, "runs": 1, "last_exit_code": None},
    )
    monkeypatch.setattr(symphony_status, "agent_processes", lambda _pid: [])
    status = symphony_status.collect(tmp_path)

    assert status["logs"]["structured_file"] == "symphony.log.1"


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

    lookup, issue = symphony_status.linear_issue("YU-21", "secret-token")

    assert lookup == "ok"
    assert issue == response["data"]["issue"]
    assert captured == {"authorization": "secret-token", "timeout": 10}


def test_linear_issue_reports_api_errors(monkeypatch) -> None:
    def fail_request(_request, timeout):
        assert timeout == 10
        raise symphony_status.urllib.error.URLError("offline")

    monkeypatch.setattr(symphony_status.urllib.request, "urlopen", fail_request)

    assert symphony_status.linear_issue("YU-21", "secret-token") == ("error", None)


def test_linear_issue_reports_truncated_responses(monkeypatch) -> None:
    def fail_request(_request, timeout):
        assert timeout == 10
        raise http.client.IncompleteRead(b'{"data":')

    monkeypatch.setattr(symphony_status.urllib.request, "urlopen", fail_request)

    assert symphony_status.linear_issue("YU-21", "secret-token") == ("error", None)


def test_linear_issue_reports_unexpected_response_shapes(monkeypatch) -> None:
    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    responses = iter(
        [
            [],
            {"data": None},
            {"data": {"issue": {}}},
            {"data": {"issue": {"state": None}}},
            {"data": {"issue": {"state": "Todo"}}},
        ]
    )

    def open_request(_request, timeout):
        assert timeout == 10
        return Response(json.dumps(next(responses)).encode())

    monkeypatch.setattr(symphony_status.urllib.request, "urlopen", open_request)

    assert symphony_status.linear_issue("YU-21", "secret-token") == ("error", None)
    assert symphony_status.linear_issue("YU-21", "secret-token") == ("error", None)
    assert symphony_status.linear_issue("YU-21", "secret-token") == ("error", None)
    assert symphony_status.linear_issue("YU-21", "secret-token") == ("error", None)
    assert symphony_status.linear_issue("YU-21", "secret-token") == ("error", None)


def test_collect_reports_failed_linear_lookup_as_unavailable(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".symphony" / "workspaces" / "YU-21").mkdir(parents=True)
    monkeypatch.setenv("LINEAR_API_KEY", "secret-token")
    monkeypatch.setattr(
        symphony_status,
        "service_status",
        lambda: {"state": "running", "pid": 42, "runs": 1, "last_exit_code": None},
    )
    monkeypatch.setattr(symphony_status, "agent_processes", lambda _pid: [])
    monkeypatch.setattr(symphony_status, "linear_issue", lambda *_args: ("error", None))

    status = symphony_status.collect(tmp_path)

    assert status["linear"] == {"status": "unavailable"}
    assert status["workspaces"][0]["linear_lookup"] == "error"
    assert "linear=error" in symphony_status.render_text(status)


def test_collect_inspects_workspaces_concurrently(tmp_path: Path, monkeypatch) -> None:
    workspace_root = tmp_path / ".symphony" / "workspaces"
    for identifier in ("YU-20", "YU-21"):
        (workspace_root / identifier).mkdir(parents=True)
    barrier = threading.Barrier(2)

    monkeypatch.setattr(
        symphony_status,
        "service_status",
        lambda: {"state": "running", "pid": 42, "runs": 1, "last_exit_code": None},
    )
    monkeypatch.setattr(symphony_status, "agent_processes", lambda _pid: [])

    def inspect_workspace(path, _api_key):
        barrier.wait(timeout=1)
        return {
            "issue": path.name,
            "branch": None,
            "dirty": False,
            "head": None,
            "last_file_update_at": None,
            "linear_lookup": "disabled",
            "linear": None,
        }

    monkeypatch.setattr(symphony_status, "workspace_status", inspect_workspace)

    status = symphony_status.collect(tmp_path)

    assert [workspace["issue"] for workspace in status["workspaces"]] == ["YU-20", "YU-21"]
