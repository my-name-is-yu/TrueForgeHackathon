from __future__ import annotations

import asyncio

import pytest

from asset_autopsy.fixture import clean_end_effector_position, load_compound_arm_fixture
from asset_autopsy.qualification import (
    HiddenVerifier,
    build_promotion_ticket,
    validate_promotion_ticket,
)
from asset_autopsy.runner import RunRecord, SegmentRecord
from asset_autopsy.schemas import AggregateResult, CanonicalDiffEntry, PromotionTicket


class PassingRunner:
    async def run(self, configuration):
        target = tuple(configuration.initial_ctrl)
        body = clean_end_effector_position(target)
        rows = tuple(
            {
                "t": 0.002 * (index + 1),
                "E_pot": 0.0,
                "E_kin": 0.0,
                "qpos": target,
                "qvel": (0.0, 0.0, 0.0),
                "ncon": 0,
                "body_xpos:end_effector": body,
                "ctrl": target,
            }
            for index in range(2_000)
        )
        return RunRecord(
            step_count=2_000,
            segments=(SegmentRecord("qualification", 2_000, target, rows),),
        )


def test_hidden_verifier_returns_only_aggregate_state() -> None:
    fixture = load_compound_arm_fixture()
    verifier = HiddenVerifier(runner=PassingRunner(), fixture=fixture)

    result = asyncio.run(verifier.verify(fixture.asset_xml))

    assert result.passed == result.total == 3
    assert result.violated_clause_ids == ()
    assert len(verifier.scenario_hashes) == 3
    assert all(len(item) == 64 for item in verifier.scenario_hashes)
    assert not hasattr(result, "scenarios")
    assert not hasattr(result, "traces")


@pytest.mark.parametrize("diff_count", [1, 2])
def test_promotion_ticket_digest_binds_commitments_for_qualifiable_heads(
    diff_count: int,
) -> None:
    diffs = [
        CanonicalDiffEntry(
            target="joint_b", attribute="axis", before="0 0 1", after="0 1 0"
        ),
        CanonicalDiffEntry(
            target="joint_c", attribute="damping", before="0.01", after="0.4"
        ),
    ]
    public = AggregateResult(passed=1, total=1, violated_clause_ids=[])
    holdout = AggregateResult(passed=3, total=3, violated_clause_ids=[])
    commitments = {
        "source_asset_sha256": "a" * 64,
        "controller_sha256": "b" * 64,
        "public_contract_sha256": "c" * 64,
        "runner_sha256": "d" * 64,
        "holdout_commitment_sha256": "e" * 64,
    }
    ticket = build_promotion_ticket(
        ticket_id="evt_ticket_a",
        case_id="case_compound-arm-01",
        revision_id="r002",
        asset_sha256="f" * 64,
        canonical_diff=diffs[:diff_count],
        public_result=public,
        holdout_result=holdout,
        commitment_hashes=commitments,
    )

    assert validate_promotion_ticket(ticket, commitment_hashes=commitments) is True
    changed = dict(commitments)
    changed["runner_sha256"] = "0" * 64
    assert validate_promotion_ticket(ticket, commitment_hashes=changed) is False
    tampered = PromotionTicket.model_validate(
        {**ticket.model_dump(mode="json"), "ticket_digest": "0" * 64}
    )
    assert validate_promotion_ticket(tampered, commitment_hashes=commitments) is False
    changed_ticket_id = PromotionTicket.model_validate(
        {**ticket.model_dump(mode="json"), "ticket_id": "evt_ticket_b"}
    )
    assert (
        validate_promotion_ticket(changed_ticket_id, commitment_hashes=commitments)
        is False
    )
    changed_asset = PromotionTicket.model_validate(
        {**ticket.model_dump(mode="json"), "asset_sha256": "0" * 64}
    )
    assert (
        validate_promotion_ticket(changed_asset, commitment_hashes=commitments) is False
    )
