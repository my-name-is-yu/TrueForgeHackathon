from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from asset_autopsy.fixture import CASE_ID
from asset_autopsy.schemas import InspectAssetInput, OpenCaseInput, RunExperimentInput
from asset_autopsy.service import AssetAutopsyService, DomainError


class NoCallRunner:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, _configuration):
        self.calls += 1
        raise AssertionError("runner should not be called")


def test_service_preprovisions_the_demo_case_idempotently_and_exposes_no_diagnosis(
    tmp_path,
) -> None:
    first = AssetAutopsyService(tmp_path)
    second = AssetAutopsyService(tmp_path)

    opened = asyncio.run(second.open_case(OpenCaseInput(case_id=CASE_ID)))
    inspected = asyncio.run(
        second.inspect_asset(
            InspectAssetInput(case_id=CASE_ID, revision_id="r000", view="both")
        )
    )

    assert opened.remaining_budgets.model_dump() == {
        "runs_remaining": 10,
        "experiments_remaining": 5,
        "revisions_remaining": 2,
        "qualification_remaining": 1,
    }
    assert inspected.joints[1].name == "joint_b"
    assert inspected.joints[1].axis == (0.0, 0.0, 1.0)
    serialized = inspected.model_dump_json()
    assert "fault" not in serialized.lower()
    assert "golden" not in serialized.lower()
    first.store.close()
    second.store.close()


def test_service_requires_strict_models_and_a_same_revision_baseline(tmp_path) -> None:
    runner = NoCallRunner()
    service = AssetAutopsyService(tmp_path, runner=runner)

    with pytest.raises(DomainError) as invalid:
        asyncio.run(service.open_case({"case_id": CASE_ID}))
    assert invalid.value.code == "INVALID_TOOL_INPUT"

    payload = RunExperimentInput.model_validate(
        {
            "case_id": CASE_ID,
            "revision_id": "r000",
            "hypothesis": {
                "claim": "The observed response depends on one joint axis.",
                "suspected_elements": [
                    {"kind": "joint", "name": "joint_b", "attributes": ["axis"]}
                ],
                "competing_explanation": {
                    "claim": "The response instead depends on joint damping.",
                    "suspected_elements": [
                        {"kind": "joint", "name": "joint_c", "attributes": ["damping"]}
                    ],
                    "discriminating_reason": "The position and velocity signals separate them.",
                },
                "prediction": "The body-position direction will follow the selected axis.",
                "falsifier": "A different direction with the same velocity would reject it.",
            },
            "initial_joint_positions": [
                {"joint_name": "joint_a", "position_rad": 0.0},
                {"joint_name": "joint_b", "position_rad": 0.0},
                {"joint_name": "joint_c", "position_rad": 0.0},
            ],
            "segments": [
                {
                    "n_steps": 256,
                    "controls": [
                        {"actuator_name": "motor_a", "value": 0.0},
                        {"actuator_name": "motor_b", "value": 0.2},
                        {"actuator_name": "motor_c", "value": 0.0},
                    ],
                }
            ],
            "observables": [{"kind": "qpos"}],
        }
    )
    with pytest.raises(DomainError) as baseline:
        asyncio.run(service.run_experiment(payload))
    assert baseline.value.code == "BASELINE_REQUIRED"
    assert runner.calls == 0
    assert service.publish_invocation_count == 0


def test_removed_surfaces_have_no_production_references() -> None:
    package = Path(__file__).resolve().parents[2] / "src" / "asset_autopsy"
    assert not (package / "publisher.py").exists()
    production = "\n".join(
        path.read_text() for path in sorted(package.glob("*.py"))
    )
    for removed in (
        "PublishRevisionOutput",
        "PublicationBundle",
        "PublicationError",
        "PromotionReceipt",
        "PublicationRecord",
        "record_promotion_receipt",
        "reconcile_promotion",
        "get_promotion_receipt",
        "promoted_revision_id",
        "promotion_state",
        "publication_receipt_count",
        "published_bundle_count",
        "public_artifact_count",
        "publication_root",
        '"PROMOTED"',
        "QUALIFICATION_RECOVERING",
        "QUALIFICATION_RECOVERED",
        '"recovering"',
        "mark_qualification_recovering",
        "recover_qualification",
        "StorageIntegrityError =",
        "restore_case_state =",
        "append_ledger_event =",
        "commit_revision_and_event =",
        "complete_qualification =",
        "SQLiteEvidenceStore =",
        "Storage = EvidenceStore",
    ):
        assert removed not in production


def test_service_rejects_a_tampered_revision_object_with_a_sanitized_integrity_error(
    tmp_path,
) -> None:
    service = AssetAutopsyService(tmp_path)
    case = service.store.get_case(CASE_ID)
    service.store.objects.path_for(case.source_asset_sha256).write_bytes(b"tampered")

    with pytest.raises(DomainError) as caught:
        asyncio.run(
            service.inspect_asset(
                InspectAssetInput(
                    case_id=CASE_ID,
                    revision_id="r000",
                    view="authored",
                )
            )
        )

    assert caught.value.code == "EVIDENCE_INTEGRITY_FAILED"
    assert "path" not in caught.value.safe_message.lower()
    assert caught.value.retryable is False


def test_revision_resolution_preserves_corrupt_row_as_integrity_error(tmp_path) -> None:
    service = AssetAutopsyService(tmp_path)
    with sqlite3.connect(tmp_path / "evidence.sqlite") as connection:
        connection.execute(
            "UPDATE revisions SET asset_sha256 = 'corrupt' WHERE revision_id = 'r000'"
        )
        connection.commit()

    with pytest.raises(DomainError) as caught:
        service._revision(CASE_ID, "r000", "req_corrupt_revision")

    assert caught.value.code == "EVIDENCE_INTEGRITY_FAILED"
    assert caught.value.retryable is False


def test_revision_resolution_keeps_missing_row_distinct_from_corruption(tmp_path) -> None:
    service = AssetAutopsyService(tmp_path)

    with pytest.raises(DomainError) as caught:
        service._revision(CASE_ID, "r999", "req_missing_revision")

    assert caught.value.code == "REVISION_NOT_FOUND"
