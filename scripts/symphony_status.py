#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SERVICE_LABEL = "com.trueforge.symphony"


def run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as error:
        return subprocess.CompletedProcess(command, 127, "", str(error))


def parse_launchctl(output: str) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "label": SERVICE_LABEL,
        "state": "stopped",
        "pid": None,
        "runs": None,
        "last_exit_code": None,
    }
    patterns = {
        "state": r"^\s*state = (\S+)",
        "pid": r"^\s*pid = (\d+)",
        "runs": r"^\s*runs = (\d+)",
        "last_exit_code": r"^\s*last exit code = (-?\d+)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, output, re.MULTILINE)
        if match:
            value: Any = match.group(1)
            if key != "state":
                value = int(value)
            fields[key] = value
    return fields


def service_status() -> dict[str, Any]:
    result = run(["launchctl", "print", f"gui/{os.getuid()}/{SERVICE_LABEL}"])
    if result.returncode != 0:
        return parse_launchctl("")
    return parse_launchctl(result.stdout)


def agent_processes(service_pid: int | None) -> list[dict[str, Any]]:
    if service_pid is None:
        return []
    result = run(["ps", "-axo", "pid=,ppid=,etime=,command="])
    if result.returncode != 0:
        return []
    processes: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        match = re.match(r"\s*(\d+)\s+(\d+)\s+(\S+)\s+(.*)$", line)
        if not match:
            continue
        processes.append(
            {
                "pid": int(match.group(1)),
                "ppid": int(match.group(2)),
                "elapsed": match.group(3),
                "command": match.group(4),
            }
        )
    descendants = {service_pid}
    while True:
        discovered = {process["pid"] for process in processes if process["ppid"] in descendants}
        expanded = descendants | discovered
        if expanded == descendants:
            break
        descendants = expanded
    candidates = [
        process
        for process in processes
        if process["pid"] in descendants and "codex app-server" in process["command"]
    ]
    pids = {process["pid"] for process in candidates}
    return [
        {key: process[key] for key in ("pid", "ppid", "elapsed")}
        for process in candidates
        if process["ppid"] not in pids
    ]


def iso_mtime(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return None


def mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def latest_workspace_update(workspace: Path) -> str | None:
    latest = 0.0
    for root, directories, filenames in os.walk(workspace):
        directories[:] = [name for name in directories if name != ".git"]
        for filename in filenames:
            try:
                latest = max(latest, (Path(root) / filename).stat().st_mtime)
            except OSError:
                continue
    if not latest:
        return None
    return datetime.fromtimestamp(latest, timezone.utc).isoformat()


def classify_health(service: dict[str, Any], agents: list[dict[str, Any]]) -> str:
    if service["state"] != "running":
        return "stopped"
    if not agents:
        return "idle"
    return "running"


def linear_issue(identifier: str, api_key: str | None) -> tuple[str, dict[str, Any] | None]:
    if not api_key:
        return "disabled", None
    payload = json.dumps(
        {
            "query": "query SymphonyStatusIssue($id: String!) { issue(id: $id) { identifier url state { name type } } }",
            "variables": {"id": identifier},
        }
    ).encode()
    request = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=payload,
        headers={"Authorization": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return "error", None
    if not isinstance(body, dict) or body.get("errors"):
        return "error", None
    data = body.get("data")
    if not isinstance(data, dict):
        return "error", None
    issue = data.get("issue")
    if issue is None:
        return "not_found", None
    if not isinstance(issue, dict):
        return "error", None
    return "ok", issue


def workspace_status(workspace: Path, linear_api_key: str | None) -> dict[str, Any]:
    branch_result = run(["git", "branch", "--show-current"], cwd=workspace)
    branch = branch_result.stdout.strip() or None if branch_result.returncode == 0 else None
    head_result = run(["git", "rev-parse", "HEAD"], cwd=workspace)
    head = head_result.stdout.strip() if head_result.returncode == 0 else None
    dirty_result = run(["git", "status", "--porcelain"], cwd=workspace)
    dirty = bool(dirty_result.stdout.strip()) if dirty_result.returncode == 0 else None
    linear_lookup, linear = linear_issue(workspace.name, linear_api_key)
    return {
        "issue": workspace.name,
        "branch": branch,
        "dirty": dirty,
        "head": head,
        "last_file_update_at": latest_workspace_update(workspace),
        "linear_lookup": linear_lookup,
        "linear": linear,
    }


def collect(project_root: Path) -> dict[str, Any]:
    symphony_home = project_root / ".symphony"
    workspace_root = Path(
        os.environ.get("SYMPHONY_WORKSPACE_ROOT", str(symphony_home / "workspaces"))
    )
    service = service_status()
    agents = agent_processes(service["pid"])
    linear_api_key = os.environ.get("LINEAR_API_KEY")
    workspaces = []
    if workspace_root.is_dir():
        workspace_paths = [path for path in sorted(workspace_root.iterdir()) if path.is_dir()]
        if workspace_paths:
            with ThreadPoolExecutor(max_workers=min(4, len(workspace_paths))) as executor:
                workspaces = list(
                    executor.map(
                        lambda path: workspace_status(path, linear_api_key), workspace_paths
                    )
                )
    stdout_log = symphony_home / "launchd.stdout.log"
    stderr_log = symphony_home / "launchd.stderr.log"
    structured_log_dir = symphony_home / "logs" / "log"
    structured_logs = [structured_log_dir / "symphony.log"]
    if structured_log_dir.is_dir():
        structured_logs.extend(
            path
            for path in structured_log_dir.glob("symphony.log.*")
            if path.suffix[1:].isdigit()
        )
    structured_mtimes = [(path, mtime(path)) for path in structured_logs]
    latest_structured = max(
        ((path, value) for path, value in structured_mtimes if value is not None),
        key=lambda item: item[1],
        default=(None, None),
    )
    activity_times = [latest_structured[1]]
    for workspace in workspaces:
        updated_at = workspace["last_file_update_at"]
        if updated_at:
            activity_times.append(datetime.fromisoformat(updated_at).timestamp())
    latest_activity = max((value for value in activity_times if value is not None), default=None)
    activity_age_seconds = (
        max(0, int(datetime.now(timezone.utc).timestamp() - latest_activity))
        if latest_activity is not None
        else None
    )
    health = classify_health(service, agents)
    linear_lookups = [workspace["linear_lookup"] for workspace in workspaces]
    if not linear_api_key:
        linear_status = "disabled"
    elif not linear_lookups:
        linear_status = "unchecked"
    elif "error" in linear_lookups:
        linear_status = "unavailable"
    else:
        linear_status = "available"
    return {
        "health": health,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "service": service,
        "agents": agents,
        "workspaces": workspaces,
        "logs": {
            "stdout_updated_at": iso_mtime(stdout_log),
            "stderr_updated_at": iso_mtime(stderr_log),
            "structured_file": latest_structured[0].name if latest_structured[0] else None,
            "structured_updated_at": (
                datetime.fromtimestamp(latest_structured[1], timezone.utc).isoformat()
                if latest_structured[1] is not None
                else None
            ),
        },
        "activity": {
            "latest_at": (
                datetime.fromtimestamp(latest_activity, timezone.utc).isoformat()
                if latest_activity is not None
                else None
            ),
            "age_seconds": activity_age_seconds,
        },
        "linear": {"status": linear_status},
    }


def render_text(status: dict[str, Any]) -> str:
    service = status["service"]
    lines = [
        f"Symphony: {status['health']} (state={service['state']}, pid={service['pid']}, runs={service['runs']}, last_exit={service['last_exit_code']})",
        f"Agents: {len(status['agents'])} (activity_age={status['activity']['age_seconds']}s)",
    ]
    for workspace in status["workspaces"]:
        linear = workspace["linear"]
        linear_text = linear["state"]["name"] if linear else workspace["linear_lookup"]
        lines.append(
            f"- {workspace['issue']}: branch={workspace['branch'] or '-'} "
            f"dirty={workspace['dirty']} "
            f"linear={linear_text}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Symphony runtime status")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--project-root", type=Path)
    arguments = parser.parse_args()
    project_root = arguments.project_root or Path(__file__).resolve().parent.parent
    status = collect(project_root)
    if arguments.as_json:
        print(json.dumps(status, ensure_ascii=False, sort_keys=True))
    else:
        print(render_text(status))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
