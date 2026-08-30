from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Sequence

from .fixture import CompoundArmFixture, PublicScenario, clean_end_effector_position
from .metrics import evaluate_task
from .runner import ConstantSegment, DeterministicRunner, RunConfiguration
from .schemas import AggregateResult, CanonicalDiffEntry, PromotionTicket
from .storage import canonical_json_bytes
from .task_evaluation import PASS_LIMITS


_PRIVATE_SCENARIO_QPOS = (
    ((-0.553127, 0.347211, -0.203811), (-0.553127, 0.347211, -0.163811)),
    ((0.697413, -0.247193, 0.353819), (0.697413, -0.247193, 0.313819)),
    ((-0.253417, -0.603191, 0.147829), (-0.253417, -0.603191, 0.107829)),
)


class QualificationExecutionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class HiddenVerificationResult:
    passed: int
    total: int
    violated_clause_ids: tuple[str, ...]

    def aggregate(self) -> AggregateResult:
        return AggregateResult(
            passed=self.passed,
            total=self.total,
            violated_clause_ids=list(self.violated_clause_ids),
        )


class HiddenVerifier:
    def __init__(
        self,
        *,
        runner: DeterministicRunner,
        fixture: CompoundArmFixture,
    ) -> None:
        self._runner = runner
        self._fixture = fixture
        self._scenario_payloads = tuple(
            {
                "target_qpos": target,
                "initial_qpos": initial,
                "target_body_position": clean_end_effector_position(target),
                "duration_steps": 2_000,
                "hold_steps": 1_000,
            }
            for target, initial in _PRIVATE_SCENARIO_QPOS
        )

    @property
    def scenario_hashes(self) -> tuple[str, ...]:
        return tuple(_sha256(payload) for payload in self._scenario_payloads)

    @property
    def suite_commitment_sha256(self) -> str:
        return _sha256(
            {
                "kind": "compound-arm-holdout",
                "scenario_hashes": self.scenario_hashes,
                "limits": PASS_LIMITS,
                "runner": self._fixture.runner_sha256,
            }
        )

    @property
    def holdout_commitment_sha256(self) -> str:
        return self.suite_commitment_sha256

    async def verify(self, asset_xml: bytes) -> HiddenVerificationResult:
        passed = 0
        violated: set[str] = set()
        for payload in self._scenario_payloads:
            scenario = PublicScenario(
                scenario_id="private",
                initial_qpos=payload["initial_qpos"],
                target_qpos=payload["target_qpos"],
                target_body_position=payload["target_body_position"],
                duration_steps=payload["duration_steps"],
                hold_steps=payload["hold_steps"],
            )
            try:
                record = await self._runner.run(
                    RunConfiguration(
                        xml_string=asset_xml.decode("utf-8"),
                        initial_qpos=scenario.initial_qpos,
                        initial_qvel=(0.0,) * len(self._fixture.joint_names),
                        initial_ctrl=scenario.target_qpos,
                        segments=(
                            ConstantSegment(
                                ctrl=scenario.target_qpos,
                                n_steps=scenario.duration_steps,
                                label="qualification",
                            ),
                        ),
                        track=("contact_count", "body_xpos:end_effector"),
                    )
                )
                evaluation = evaluate_task(record, scenario)
            except Exception:
                raise QualificationExecutionError(
                    "a private qualification scenario did not complete"
                ) from None
            if evaluation.passed:
                passed += 1
            else:
                violated.update(evaluation.violated_clause_ids)
        return HiddenVerificationResult(
            passed=passed,
            total=len(self._scenario_payloads),
            violated_clause_ids=tuple(sorted(violated)),
        )


def build_promotion_ticket(
    *,
    ticket_id: str,
    case_id: str,
    revision_id: str,
    asset_sha256: str,
    canonical_diff: Sequence[CanonicalDiffEntry],
    public_result: AggregateResult,
    holdout_result: AggregateResult,
    export_name: str,
    commitment_hashes: dict[str, str],
) -> PromotionTicket:
    core = qualified_core_payload(
        case_id=case_id,
        revision_id=revision_id,
        asset_sha256=asset_sha256,
        canonical_diff=canonical_diff,
        public_result=public_result,
        holdout_result=holdout_result,
        export_name=export_name,
        commitment_hashes=commitment_hashes,
    )
    qualified_core_sha256 = _sha256(core)
    ticket_fields = {
        "ticket_id": ticket_id,
        "case_id": case_id,
        "revision_id": revision_id,
        "asset_sha256": asset_sha256,
        "canonical_diff": [entry.model_dump(mode="json") for entry in canonical_diff],
        "public_result": public_result.model_dump(mode="json"),
        "holdout_result": holdout_result.model_dump(mode="json"),
        "export_name": export_name,
        "qualified_core_sha256": qualified_core_sha256,
    }
    return PromotionTicket(
        **ticket_fields,
        ticket_digest=_sha256(ticket_fields),
    )


def qualified_core_payload(
    *,
    case_id: str,
    revision_id: str,
    asset_sha256: str,
    canonical_diff: Sequence[CanonicalDiffEntry],
    public_result: AggregateResult,
    holdout_result: AggregateResult,
    export_name: str,
    commitment_hashes: dict[str, str],
) -> dict[str, object]:
    return {
        "case_id": case_id,
        "revision_id": revision_id,
        "asset_sha256": asset_sha256,
        "canonical_diff": [entry.model_dump(mode="json") for entry in canonical_diff],
        "public_result": public_result.model_dump(mode="json"),
        "holdout_result": holdout_result.model_dump(mode="json"),
        "export_name": export_name,
        "commitment_hashes": dict(sorted(commitment_hashes.items())),
    }


def validate_promotion_ticket(
    ticket: PromotionTicket,
    *,
    commitment_hashes: dict[str, str],
) -> bool:
    core = qualified_core_payload(
        case_id=ticket.case_id,
        revision_id=ticket.revision_id,
        asset_sha256=ticket.asset_sha256,
        canonical_diff=ticket.canonical_diff,
        public_result=ticket.public_result,
        holdout_result=ticket.holdout_result,
        export_name=ticket.export_name,
        commitment_hashes=commitment_hashes,
    )
    qualified_hash = _sha256(core)
    ticket_fields = {
        "ticket_id": ticket.ticket_id,
        "case_id": ticket.case_id,
        "revision_id": ticket.revision_id,
        "asset_sha256": ticket.asset_sha256,
        "canonical_diff": [
            entry.model_dump(mode="json") for entry in ticket.canonical_diff
        ],
        "public_result": ticket.public_result.model_dump(mode="json"),
        "holdout_result": ticket.holdout_result.model_dump(mode="json"),
        "export_name": ticket.export_name,
        "qualified_core_sha256": qualified_hash,
    }
    return (
        ticket.qualified_core_sha256 == qualified_hash
        and ticket.ticket_digest == _sha256(ticket_fields)
    )


def _sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


__all__ = [
    "HiddenVerificationResult",
    "HiddenVerifier",
    "QualificationExecutionError",
    "build_promotion_ticket",
    "qualified_core_payload",
    "validate_promotion_ticket",
]
