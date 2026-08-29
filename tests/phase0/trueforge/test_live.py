from __future__ import annotations

import pytest

from spikes.phase0.trueforge.runner import run_live_probe


@pytest.mark.phase0_trueforge
def test_live_trueforge_phase0_gates() -> None:
    result = run_live_probe()
    assert result["overall"] == "PASS"
    assert all(gate["result"] == "PASS" for gate in result["gates"].values())
