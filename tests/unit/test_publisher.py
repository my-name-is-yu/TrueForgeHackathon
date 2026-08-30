from __future__ import annotations

from asset_autopsy.publisher import PublicationError, Publisher


PAYLOAD = {
    "case_id": "case_compound-arm-01",
    "revision_id": "r002",
    "repaired_mjcf": b'<mujoco model="qualified"/>',
    "patch_manifest": {"canonical_diff": [1, 2]},
    "qualification": {"passed": True},
    "evidence_ledger": [{"event_id": "evt_one"}],
}


def test_publisher_materializes_and_verifies_one_idempotent_bundle(tmp_path) -> None:
    publisher = Publisher(tmp_path)

    first = publisher.publish(**PAYLOAD)
    second = publisher.publish(**PAYLOAD)

    assert first.created is True
    assert second.created is False
    assert first.artifacts == second.artifacts
    assert (
        publisher.load_existing(PAYLOAD["case_id"], PAYLOAD["revision_id"]).artifacts
        == first.artifacts
    )
    assert {artifact.kind for artifact in first.artifacts} == {
        "repaired_mjcf",
        "patch_manifest",
        "qualification",
        "evidence_ledger",
    }
    assert publisher.publication_count == 1
    assert publisher.public_artifact_count == 4


def test_publisher_rejects_an_existing_bundle_with_changed_content(tmp_path) -> None:
    publisher = Publisher(tmp_path)
    publisher.publish(**PAYLOAD)
    target = (
        tmp_path / PAYLOAD["case_id"] / PAYLOAD["revision_id"] / "qualification.json"
    )
    target.write_text("tampered")

    try:
        publisher.publish(**PAYLOAD)
    except PublicationError:
        pass
    else:
        raise AssertionError("tampered publication was accepted")
