from __future__ import annotations

import hashlib
import os
import re
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
    assert "__SYMPHONY_CONTROLLER_WORD__" not in rendered
    assert "__SYMPHONY_CONTROLLER_ROOT__" not in rendered


def test_render_preserves_backslashes_in_the_controller_root(tmp_path: Path) -> None:
    template = (ROOT / "symphony" / "WORKFLOW.md.template").read_text()

    result = render_fixture(tmp_path, template, project_name="project\\root")

    assert result.returncode == 0
    workflow = tmp_path / "project\\root" / ".symphony" / "runtime" / "WORKFLOW.md"
    rendered = workflow.read_text()
    assert str(tmp_path / "project\\root" / "scripts" / "symphony_sol_review") in rendered


def test_rendered_controller_word_executes_from_a_shell_metacharacter_path(
    tmp_path: Path,
) -> None:
    template = (ROOT / "symphony" / "WORKFLOW.md.template").read_text()
    project_name = "project $HOME `printf injected` \\\"quote\\\" 'apostrophe' \\\\root"

    result = render_fixture(tmp_path, template, project_name=project_name)

    assert result.returncode == 0
    project = tmp_path / project_name
    wrapper = project / "scripts" / "symphony_sol_review"
    wrapper.write_text('#!/bin/sh\nprintf ran > "$1"\n')
    wrapper.chmod(0o755)
    rendered = (project / ".symphony" / "runtime" / "WORKFLOW.md").read_text()
    match = re.search(r"^\s+(.+) PACKET\.md DECISION\.json$", rendered, re.MULTILINE)
    assert match is not None
    marker = tmp_path / "controller-ran"

    invoked = subprocess.run(
        ["zsh", "-c", f'{match.group(1)} "$1" "$2"', "_", str(marker), "unused"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert invoked.returncode == 0, invoked.stderr
    assert marker.read_text() == "ran"


def test_agent_linear_operations_stay_behind_the_authenticated_tool_boundary() -> None:
    template = (ROOT / "symphony" / "WORKFLOW.md.template").read_text()

    assert "linear_graphql" in template
    assert "scripts/symphony_linear.py" in template
    assert "tracker auth behind the dynamic-tool boundary" in template
    assert "Never read tracker credentials or call `scripts/symphony_linear.py`" in template
    assert "python3 scripts/symphony_linear.py" not in template


def test_existing_pr_and_sol_commands_are_unambiguous() -> None:
    template = (ROOT / "symphony" / "WORKFLOW.md.template").read_text()

    assert "never a raw SHA into a remote-tracking ref" in template
    assert '^yu/[A-Za-z0-9._/-]+$' in template
    assert 'git check-ref-format --branch "$recorded_head_branch"' in template
    assert 'refspec="+refs/heads/${recorded_head_branch}:' in template
    assert 'git fetch origin "$refspec"' in template
    assert "passing the complete refspec as one quoted argument" in template
    assert "__SYMPHONY_CONTROLLER_WORD__ PACKET.md DECISION.json" in template
    assert "Start exactly one wrapper process for a packet" in template
    assert "poll that same session until it exits" in template
    assert "terminate the original session and its child processes" in template
    assert "wait until all of them have exited" in template


def test_review_protocol_requests_each_missing_source_once_per_head() -> None:
    template = (ROOT / "symphony" / "WORKFLOW.md.template").read_text()

    assert "publish exactly one top-level comment" in template
    assert "`@codex review`" in template
    assert "`/agentic_review`" in template
    assert "OpenAI Codex or" in template
    assert "first record a `pending` request intent" in template
    assert "symphony-review-request:v1 source=<codex|qodo> head=<40-char-sha>" in template
    assert "whose second line is an HTML comment containing" in template
    assert "Update the intent to `published`" in template
    assert "source plus full head SHA as the idempotency key" in template
    assert "never publish a second request for" in template
    assert "the same source and head" in template
    assert "reconcile only a comment containing the exact marker" in template
    assert "never match\n     the command text alone or an older-head request" in template
    assert "A timeout does not authorize another request" in template


def test_review_protocol_uses_pr_coverage_then_one_current_reviewer() -> None:
    template = (ROOT / "symphony" / "WORKFLOW.md.template").read_text()
    normalized = " ".join(template.split())

    assert "Derive reviewer\n     coverage from GitHub every time; do not store it in the Workpad" in template
    assert "request only the missing source or both missing sources in\n     parallel" in template
    assert "Once both have coverage, require only Qodo" in template
    assert "one-time exact-head fallback" in template
    assert "a timeout from a missing source cannot be replaced" in template
    assert "Current reviewer: codex|qodo|both" in template
    assert "PR history contains at least one completed Codex review and one completed Qodo review" in normalized
    assert "at least one completed current-head review" in normalized


def test_review_packet_uses_authoritative_contract_context_and_separates_resolved_findings() -> None:
    template = (ROOT / "symphony" / "WORKFLOW.md.template").read_text()

    assert "re-read the issue's authoritative source-of-truth sections" in template
    assert "exact relevant acceptance criterion" in template
    assert "do not rely on a Workpad summary" in template
    assert "Separate\n     outstanding current-head findings from resolved or outdated context" in template
    assert "exact implementation and test evidence that resolved it" in template
    assert "never present a still-required acceptance\n     invariant as though it had been rejected or deferred" in template


def test_sol_decision_is_exact_head_and_uses_only_three_dispositions() -> None:
    template = (ROOT / "symphony" / "WORKFLOW.md.template").read_text()

    assert "re-read the PR through the\n     GitHub connector" in template
    assert "If its head differs from the packet head, discard the decision" in template
    assert "`fix_now`, `backlog`, or `reject`" in template
    assert "cannot request human\n   adjudication" in template
    assert "without re-adjudicating its meaning" in template


def test_no_comments_is_a_hash_bound_finalization_gate() -> None:
    template = (ROOT / "symphony" / "WORKFLOW.md.template").read_text()

    implementation_step = template.split("8. After pushing", 1)[0]
    finalization = template.split("11. Finalization and merge-ready gate:", 1)[1]
    normalized = " ".join(finalization.split())
    assert "$no-comments" not in implementation_step
    assert "once per finalization attempt, not once per PR" in finalization
    assert "exact absolute workspace from `pwd -P`" in finalization
    assert "diff --name-status --no-ext-diff origin/main...HEAD" in finalization
    assert "diff --binary --full-index --no-ext-diff --no-textconv" in finalization
    assert "shell pipe-failure propagation enabled" in finalization
    assert "complete 64-character SHA-256" in normalized
    assert "retry No Comments once" in finalization
    assert "return to step 8" in finalization


def test_review_counters_do_not_stop_rework() -> None:
    template = (ROOT / "symphony" / "WORKFLOW.md.template").read_text()

    assert "informational counters, not quotas or stopping conditions" in template
    assert "reviewed head 10" not in template
    assert "rework round 9" not in template
    assert "safety-limit exhaustion" not in template


def test_merge_ready_allows_absent_optional_checks_and_preserves_dirty_rework() -> None:
    template = (ROOT / "symphony" / "WORKFLOW.md.template").read_text()

    assert "empty GitHub checks/status list is not a failure unless repository configuration" in template
    assert "Never move to terminal `Blocked` while\n   intentional work exists only as uncommitted" in template
    assert "keep the issue in `Rework`" in template


def test_workpad_keeps_contract_content_in_authoritative_linear_sources() -> None:
    template = (ROOT / "symphony" / "WORKFLOW.md.template").read_text()

    assert "Keep the Workpad to durable orchestration facts" in template
    assert "Do not copy acceptance criteria, follow-up\n   descriptions, or frozen contracts" in template
