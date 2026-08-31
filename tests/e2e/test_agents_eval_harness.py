from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from asset_autopsy.agents_runner import RunTranscript


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import run_autonomy_eval as eval_runner  # noqa: E402


def transcript_with(value: object) -> RunTranscript:
    return RunTranscript(tools=(), code_interpreter=(), final_output=value)


def test_boundary_rejects_private_vectors_scalars_and_encoded_values() -> None:
    private = (
        {
            "initial_qpos": [-0.553127, 0.347211, -0.163811],
            "duration_steps": 2_000,
        },
    )
    for content in (
        private[0]["initial_qpos"],
        2_000,
        "measured duration=2000 steps",
        json.dumps({"unrelated_name": private[0]["initial_qpos"]}),
        json.dumps({"initial_qpos": [0.1, 0.2, 0.3]}),
    ):
        assert not eval_runner._raw_boundary_clear(
            transcript_with(content),
            bearer="secret-bearer",
            data_root=Path("/private/tmp/agents-eval"),
            private_payloads=private,
        )


def test_boundary_allows_public_overlaps_and_numeric_token_extensions() -> None:
    private = ({"hold_steps": 1_000, "private_measurement": 0.123456789},)
    public = ({"hold_steps": 1_000},)

    assert eval_runner._raw_boundary_clear(
        transcript_with("public hold=1000; unrelated=20000"),
        bearer="secret-bearer",
        data_root=Path("/private/tmp/agents-eval"),
        private_payloads=private,
        public_payloads=public,
    )
    assert eval_runner._raw_boundary_clear(
        transcript_with({"hold_steps": 1_000}),
        bearer="secret-bearer",
        data_root=Path("/private/tmp/agents-eval"),
        private_payloads=private,
        public_payloads=public,
    )
    assert not eval_runner._raw_boundary_clear(
        transcript_with("value=0.123456789"),
        bearer="secret-bearer",
        data_root=Path("/private/tmp/agents-eval"),
        private_payloads=private,
        public_payloads=public,
    )


def test_startup_blocker_write_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    evidence_path = tmp_path / "autonomy-eval.json"
    blocker_path = tmp_path / "autonomy-blocker.json"
    evidence_path.write_text('{"status":"passed"}\n', encoding="utf-8")

    def fail_commit_sha() -> str:
        raise RuntimeError("private checkout detail")

    def fail_write(path: Path, payload: object) -> None:
        assert path == blocker_path
        assert payload
        raise OSError("private path")

    monkeypatch.setattr(eval_runner, "EVIDENCE_PATH", evidence_path)
    monkeypatch.setattr(eval_runner, "BLOCKER_PATH", blocker_path)
    monkeypatch.setattr(eval_runner, "_commit_sha", fail_commit_sha)
    monkeypatch.setattr(eval_runner, "_write_json", fail_write)

    with pytest.raises(
        RuntimeError,
        match="The autonomy evaluation could not record its sanitized blocker.",
    ):
        eval_runner.run()

    assert not evidence_path.exists()
