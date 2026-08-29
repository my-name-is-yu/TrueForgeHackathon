from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]


def render_fixture(
    tmp_path: Path, template: str, project_name: str = "project"
) -> subprocess.CompletedProcess[str]:
    project = tmp_path / project_name
    scripts = project / "scripts"
    symphony = project / "symphony"
    bin_dir = tmp_path / "bin"
    scripts.mkdir(parents=True)
    symphony.mkdir()
    bin_dir.mkdir()
    shutil.copy2(ROOT / "scripts" / "symphony", scripts / "symphony")
    (symphony / "WORKFLOW.md.template").write_text(template)
    codex = bin_dir / "codex"
    codex.write_text("#!/bin/sh\nexit 0\n")
    codex.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "LINEAR_API_KEY": "test-key",
        "LINEAR_PROJECT_SLUG": "test-project",
    }
    return subprocess.run(
        ["zsh", str(scripts / "symphony"), "render"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_render_validates_tracker_entries_in_their_yaml_lists(tmp_path: Path) -> None:
    template = (ROOT / "symphony" / "WORKFLOW.md.template").read_text()
    template = template.replace("    - Auto Review\n", "", 1)

    result = render_fixture(tmp_path, template)

    assert result.returncode == 1
    assert "missing tracker.active_states entry: Auto Review" in result.stderr
    assert not (tmp_path / "project" / ".symphony" / "runtime" / "WORKFLOW.md").exists()


def test_render_publishes_a_matching_workflow_and_hash(tmp_path: Path) -> None:
    template = (ROOT / "symphony" / "WORKFLOW.md.template").read_text()

    result = render_fixture(tmp_path, template)

    assert result.returncode == 0
    runtime = tmp_path / "project" / ".symphony" / "runtime"
    workflow = runtime / "WORKFLOW.md"
    declared = (runtime / "WORKFLOW.sha256").read_text().strip()
    assert hashlib.sha256(workflow.read_bytes()).hexdigest() == declared
    rendered = workflow.read_text()
    assert str(tmp_path / "project" / "scripts" / "symphony_sol_review") in rendered
    assert "__SYMPHONY_CONTROLLER_ROOT__" not in rendered


def test_render_preserves_backslashes_in_the_controller_root(tmp_path: Path) -> None:
    template = (ROOT / "symphony" / "WORKFLOW.md.template").read_text()

    result = render_fixture(tmp_path, template, project_name="project\\root")

    assert result.returncode == 0
    workflow = tmp_path / "project\\root" / ".symphony" / "runtime" / "WORKFLOW.md"
    rendered = workflow.read_text()
    command = f'"{tmp_path / "project\\root" / "scripts" / "symphony_sol_review"}"'
    assert command in rendered


def test_render_quotes_a_controller_root_with_spaces(tmp_path: Path) -> None:
    template = (ROOT / "symphony" / "WORKFLOW.md.template").read_text()

    result = render_fixture(tmp_path, template, project_name="project root")

    assert result.returncode == 0
    workflow = tmp_path / "project root" / ".symphony" / "runtime" / "WORKFLOW.md"
    command = f'"{tmp_path / "project root" / "scripts" / "symphony_sol_review"}"'
    assert command in workflow.read_text()


def test_agent_linear_operations_stay_behind_the_authenticated_tool_boundary() -> None:
    template = (ROOT / "symphony" / "WORKFLOW.md.template").read_text()

    assert "linear_graphql" in template
    assert "scripts/symphony_linear.py" in template
    assert "Never read\n   tracker credentials or call `scripts/symphony_linear.py`" in template
    assert "python3 scripts/symphony_linear.py" not in template


def test_existing_pr_and_sol_commands_are_unambiguous() -> None:
    template = (ROOT / "symphony" / "WORKFLOW.md.template").read_text()

    assert "never a raw SHA into a remote-tracking ref" in template
    assert "refs/heads/<recorded-head-branch>:refs/remotes/origin/<recorded-head-branch>" in template
    assert '"__SYMPHONY_CONTROLLER_ROOT__/scripts/symphony_sol_review"' in template
    assert "Start exactly one wrapper process for a packet" in template
    assert "poll that same session until it exits" in template
    assert "terminate the original session and its child processes" in template
    assert "wait until all of them have exited" in template
