from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "symphony_sol_review"
HEAD = "a" * 40


def run_wrapper(
    packet: Path, output: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), str(packet), str(output)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def valid_header(
    review_head: int = 1,
    rework_round: int = 0,
    current_reviewer: str = "qodo",
) -> str:
    return (
        "SYMPHONY_REVIEW_PACKET_V2\n"
        f"Head SHA: {HEAD}\n"
        f"Review head number: {review_head}\n"
        f"Rework round: {rework_round}\n"
        f"Current reviewer: {current_reviewer}\n"
        "--- END TRUSTED HEADER ---\n"
    )


def test_wrapper_refuses_likely_secret_before_invoking_sol(tmp_path: Path) -> None:
    packet = tmp_path / "packet.md"
    packet.write_text(valid_header() + "lin_api_abcdefghijklmnopqrstuvwxyz\n")

    result = run_wrapper(packet, tmp_path / "decision.json")

    assert result.returncode == 1
    assert "appears to contain a secret" in result.stderr


def test_wrapper_refuses_fine_grained_github_pat(tmp_path: Path) -> None:
    packet = tmp_path / "packet.md"
    packet.write_text(valid_header() + "github_pat_abcdefghijklmnopqrstuvwxyz123456\n")

    result = run_wrapper(packet, tmp_path / "decision.json")

    assert result.returncode == 1
    assert "appears to contain a secret" in result.stderr


def test_wrapper_refuses_oversize_packet_instead_of_truncating(tmp_path: Path) -> None:
    packet = tmp_path / "packet.md"
    packet.write_text(valid_header() + ("review evidence\n" * 4001))

    result = run_wrapper(packet, tmp_path / "decision.json")

    assert result.returncode == 1
    assert "refusing to truncate evidence" in result.stderr


def test_wrapper_counts_an_unterminated_final_line(tmp_path: Path) -> None:
    packet = tmp_path / "packet.md"
    packet.write_text(valid_header() + ("review evidence\n" * 3994) + "final finding")

    result = run_wrapper(packet, tmp_path / "decision.json")

    assert result.returncode == 1
    assert "refusing to truncate evidence" in result.stderr


def test_wrapper_refuses_unknown_current_reviewer_before_invoking_sol(
    tmp_path: Path,
) -> None:
    packet = tmp_path / "packet.md"
    packet.write_text(valid_header(10, 9, "unknown"))

    result = run_wrapper(packet, tmp_path / "decision.json")

    assert result.returncode == 1
    assert "Packet line 5 must contain the current reviewer" in result.stderr


def test_wrapper_does_not_accept_head_metadata_outside_trusted_line(tmp_path: Path) -> None:
    packet = tmp_path / "packet.md"
    packet.write_text(
        "SYMPHONY_REVIEW_PACKET_V2\n"
        "not a trusted head line\n"
        "Review head number: 1\n"
        "Rework round: 0\n"
        "Current reviewer: qodo\n"
        "--- END TRUSTED HEADER ---\n"
        f"Head SHA: {HEAD}\n"
    )

    result = run_wrapper(packet, tmp_path / "decision.json")

    assert result.returncode == 1
    assert "line 2 must contain the head SHA" in result.stderr


def test_wrapper_runs_sol_without_file_reading_tools(tmp_path: Path) -> None:
    packet = tmp_path / "packet.md"
    packet.write_text(valid_header(review_head=11, rework_round=10))
    decision = tmp_path / "source-decision.json"
    decision.write_text(
        json.dumps(
            {
                "findings": [],
                "summary": "No fix-now findings.",
            }
        )
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_codex = bin_dir / "codex"
    fake_codex.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$@\" > \"$FAKE_CODEX_ARGS\"\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = --output-last-message ]; then cp \"$FAKE_DECISION\" \"$2\"; exit 0; fi\n"
        "  shift\n"
        "done\n"
        "exit 1\n"
    )
    fake_codex.chmod(0o755)
    args_file = tmp_path / "codex-args"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_CODEX_ARGS": str(args_file),
        "FAKE_DECISION": str(decision),
    }

    result = run_wrapper(packet, tmp_path / "decision.json", env)

    assert result.returncode == 0
    args = args_file.read_text().splitlines()
    for feature in (
        "shell_tool",
        "unified_exec",
        "multi_agent",
        "view_image",
        "apps",
        "browser_use",
        "computer_use",
        "image_generation",
        "plugins",
        "memories",
    ):
        assert ["--disable", feature] == args[args.index(feature) - 1 : args.index(feature) + 1]
    workspace = Path(args[args.index("--cd") + 1])
    assert not workspace.exists()


def test_decision_schema_contains_only_final_dispositions() -> None:
    schema = json.loads(
        (Path(__file__).parents[1] / "symphony" / "review-decision.schema.json").read_text()
    )

    assert schema["required"] == ["findings", "summary"]
    assert set(schema["properties"]) == {"findings", "summary"}
    finding = schema["properties"]["findings"]["items"]
    assert finding["required"] == [
        "id",
        "disposition",
        "rationale",
        "instruction",
        "backlog_title",
    ]
    assert finding["properties"]["disposition"]["enum"] == [
        "fix_now",
        "backlog",
        "reject",
    ]
