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


def valid_header(
    review_head: int = 1,
    rework_round: int = 0,
    codex_status: str = "complete",
    qodo_status: str = "complete",
) -> str:
    return (
        "SYMPHONY_REVIEW_PACKET_V1\n"
        f"Head SHA: {HEAD}\n"
        f"Review head number: {review_head}\n"
        f"Rework round: {rework_round}\n"
        f"Codex status: {codex_status}\n"
        f"Qodo status: {qodo_status}\n"
        "--- END TRUSTED HEADER ---\n"
    )


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


def test_wrapper_counts_an_unterminated_final_line(tmp_path: Path) -> None:
    packet = tmp_path / "packet.md"
    packet.write_text(valid_header() + ("review evidence\n" * 3993) + "final finding")

    result = run_wrapper(packet, tmp_path / "decision.json")

    assert result.returncode == 1
    assert "refusing to truncate evidence" in result.stderr


def test_wrapper_parses_tenth_head_and_timeout_statuses_on_macos(tmp_path: Path) -> None:
    packet = tmp_path / "packet.md"
    packet.write_text(valid_header(10, 9, "timeout", "timeout"))

    result = run_wrapper(packet, tmp_path / "missing" / "decision.json")

    assert result.returncode == 1
    assert "Output directory does not exist" in result.stderr


def test_wrapper_does_not_accept_head_metadata_outside_trusted_line(tmp_path: Path) -> None:
    packet = tmp_path / "packet.md"
    packet.write_text(
        "SYMPHONY_REVIEW_PACKET_V1\n"
        "not a trusted head line\n"
        "Review head number: 1\n"
        "Rework round: 0\n"
        "Codex status: complete\n"
        "Qodo status: complete\n"
        "--- END TRUSTED HEADER ---\n"
        f"Head SHA: {HEAD}\n"
    )

    result = run_wrapper(packet, tmp_path / "decision.json")

    assert result.returncode == 1
    assert "line 2 must contain the head SHA" in result.stderr
