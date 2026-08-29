from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]


def render_fixture(tmp_path: Path, template: str) -> subprocess.CompletedProcess[str]:
    project = tmp_path / "project"
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
