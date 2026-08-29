from __future__ import annotations

import subprocess
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "symphony_sol_review"
HEAD = "a" * 40


def run_wrapper(packet: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), str(packet), str(output)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def valid_header() -> str:
    return f"Head SHA: {HEAD}\nReview head number: 1\nRework round: 0\n"


def test_wrapper_refuses_likely_secret_before_invoking_sol(tmp_path: Path) -> None:
    packet = tmp_path / "packet.md"
    packet.write_text(valid_header() + "lin_api_abcdefghijklmnopqrstuvwxyz\n")

    result = run_wrapper(packet, tmp_path / "decision.json")

    assert result.returncode == 1
    assert "appears to contain a secret" in result.stderr


def test_wrapper_refuses_oversize_packet_instead_of_truncating(tmp_path: Path) -> None:
    packet = tmp_path / "packet.md"
    packet.write_text(valid_header() + ("review evidence\n" * 4001))

    result = run_wrapper(packet, tmp_path / "decision.json")

    assert result.returncode == 1
    assert "refusing to truncate evidence" in result.stderr


def test_wrapper_refuses_ambiguous_head_metadata(tmp_path: Path) -> None:
    packet = tmp_path / "packet.md"
    packet.write_text(valid_header() + f"Head SHA: {HEAD}\n")

    result = run_wrapper(packet, tmp_path / "decision.json")

    assert result.returncode == 1
    assert "multiple head SHA" in result.stderr
