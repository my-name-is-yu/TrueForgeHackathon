from __future__ import annotations

import hashlib
import inspect
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from asset_autopsy.storage import (
    CaseAlreadyExistsError,
    EvidenceStore,
    IntegrityError,
    LedgerEventRecord,
    ObjectStore,
    ObjectIntegrityError,
    QualificationConflictError,
    RevisionConflictError,
    RevisionRecord,
    RunRecord,
    StorageError,
    ValidationError,
    canonical_json_bytes,
)


COMMITMENTS = {
    "source_asset_sha256": "a" * 64,
    "controller_sha256": "b" * 64,
    "public_contract_sha256": "c" * 64,
    "runner_sha256": "d" * 64,
    "holdout_commitment_sha256": "e" * 64,
}


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def directory_chain_to_root(path: Path) -> list[Path]:
    chain = [path]
    while path.parent != path:
        path = path.parent
        chain.append(path)
    return chain


def run_event_payload(run: RunRecord) -> dict[str, object]:
    return {
        "run_id": run.run_id,
        "case_id": run.case_id,
        "revision_id": run.revision_id,
        "run_kind": run.run_kind,
        "probe_kind": run.probe_kind,
        "condition_hash": run.condition_hash,
        "execution_fingerprint": run.execution_fingerprint,
        "trace_sha256": run.trace_sha256,
        "metrics_sha256": run.metrics_sha256,
        "passed": run.passed,
    }


def revision_event_payload(
    revision: RevisionRecord, probe: RunRecord
) -> dict[str, object]:
    return {
        "parent_revision_id": revision.parent_revision_id,
        "ordinal": revision.ordinal,
        "asset_sha256": revision.asset_sha256,
        "patch_manifest_sha256": revision.patch_manifest_sha256,
        "hypothesis_event_id": revision.hypothesis_event_id,
        "probe_run_id": revision.probe_run_id,
        "probe_run": run_event_payload(probe),
    }


def make_store(tmp_path: Path) -> EvidenceStore:
    store = EvidenceStore(tmp_path / "ledger.sqlite", tmp_path / "objects")
    store.create_preprovisioned_case(
        case_id="case-1",
        root_revision_id="r000",
        **COMMITMENTS,
    )
    return store


def test_case_creation_rejects_an_invalid_existing_ledger(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    with sqlite3.connect(tmp_path / "ledger.sqlite") as connection:
        connection.execute(
            "UPDATE ledger_events SET event_hash = ? WHERE event_type = 'CASE_CREATED'",
            ("0" * 64,),
        )
        connection.commit()

    with pytest.raises(IntegrityError, match="ledger event hash mismatch"):
        store.create_preprovisioned_case(
            case_id="case-2",
            root_revision_id="r100",
            **COMMITMENTS,
        )
    with sqlite3.connect(tmp_path / "ledger.sqlite") as connection:
        assert connection.execute(
            "SELECT 1 FROM cases WHERE case_id = 'case-2'"
        ).fetchone() is None


def replace_event_payload(
    database_path: Path, event_type: str, payload: dict[str, object]
) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        target = connection.execute(
            "SELECT seq, prev_hash FROM ledger_events WHERE event_type = ?",
            (event_type,),
        ).fetchone()
        assert target is not None
        connection.execute(
            "UPDATE ledger_events SET payload_json = ? WHERE seq = ?",
            (canonical_json_bytes(payload).decode(), target["seq"]),
        )
        previous_hash = target["prev_hash"]
        for (seq,) in connection.execute(
            "SELECT seq FROM ledger_events WHERE seq >= ? ORDER BY seq", (target["seq"],)
        ):
            connection.execute(
                "UPDATE ledger_events SET prev_hash = ? WHERE seq = ?",
                (previous_hash, seq),
            )
            row = connection.execute(
                "SELECT * FROM ledger_events WHERE seq = ?", (seq,)
            ).fetchone()
            assert row is not None
            previous_hash = hashlib.sha256(
                bytes.fromhex(previous_hash)
                + canonical_json_bytes(EvidenceStore._event_without_hash(row))
            ).hexdigest()
            connection.execute(
                "UPDATE ledger_events SET event_hash = ? WHERE seq = ?",
                (previous_hash, seq),
            )
        connection.commit()


def add_probe_evidence(store: EvidenceStore) -> None:
    store.append_event(
        LedgerEventRecord(
            event_id="evt-hypothesis-1",
            case_id="case-1",
            revision_id="r000",
            event_type="HYPOTHESIS_RECORDED",
            payload={"claim": "synthetic claim", "prediction": {"metric": "x"}},
        )
    )
    store.record_run(
        run=RunRecord(
            run_id="run-probe-1",
            case_id="case-1",
            revision_id="r000",
            run_kind="probe",
            probe_kind="joint_pulse",
            condition_hash=sha256_text("condition-1"),
            execution_fingerprint=sha256_text("execution-1"),
            trace_sha256="f" * 64,
            metrics_sha256="1" * 64,
            passed=True,
        )
    )


def add_child(store: EvidenceStore, revision_id: str = "r001") -> None:
    revision = RevisionRecord(
        case_id="case-1",
        revision_id=revision_id,
        parent_revision_id="r000",
        ordinal=1,
        asset_sha256="2" * 64,
        patch_manifest_sha256="3" * 64,
        hypothesis_event_id="evt-hypothesis-1",
        probe_run_id="run-probe-1",
    )
    store.commit_revision_with_event(
        revision=revision,
        event=LedgerEventRecord(
            event_id=f"evt-revision-{revision_id}",
            case_id="case-1",
            revision_id=revision_id,
            event_type="REVISION_CREATED",
            payload=revision_event_payload(revision, store.get_run("run-probe-1")),
        ),
        expected_head_revision_id="r000",
    )


def qualify(store: EvidenceStore) -> None:
    store.reserve_qualification(
        case_id="case-1",
        revision_id="r001",
        attempt_id="attempt-1",
        suite_commitment_sha256="4" * 64,
        scenario_hashes=("5" * 64, "6" * 64, "7" * 64),
        expected_head_revision_id="r001",
        **COMMITMENTS,
    )


def test_schema_has_exactly_the_four_design_tables(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "ledger.sqlite", tmp_path / "objects")

    with sqlite3.connect(tmp_path / "ledger.sqlite") as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        case_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(cases)")
        }

    assert tables == {"cases", "revisions", "runs", "ledger_events"}
    assert "promoted_revision_id" not in case_columns
    assert store.objects.hash_root == tmp_path / "objects" / "sha256"


def test_preprovisioning_is_keyword_only_exact_and_create_once(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "ledger.sqlite", tmp_path / "objects")
    signature = inspect.signature(store.create_preprovisioned_case)
    assert all(parameter.kind is parameter.KEYWORD_ONLY for parameter in signature.parameters.values())

    with pytest.raises(TypeError):
        store.create_preprovisioned_case("case-1", "r000", **COMMITMENTS)
    with pytest.raises(TypeError):
        store.create_preprovisioned_case(case_id="case-1", root_revision_id="r000", **COMMITMENTS, extra="x")
    with pytest.raises(ValidationError):
        store.create_preprovisioned_case(
            case_id="case-1",
            root_revision_id="r000",
            **{**COMMITMENTS, "runner_sha256": "A" * 64},
        )

    case = store.create_preprovisioned_case(
        case_id="case-1",
        root_revision_id="r000",
        **COMMITMENTS,
    )
    assert case.root_revision_id == case.head_revision_id == "r000"
    assert case.qualification_result is None
    assert {key: getattr(case, key) for key in COMMITMENTS} == COMMITMENTS
    root = store.get_revision("case-1", "r000")
    assert root.parent_revision_id is None
    assert root.hypothesis_event_id is None
    assert root.probe_run_id is None
    created = store.ledger_events("case-1")[-1]
    assert created.event_type == "CASE_CREATED"
    assert {field: created.payload[field] for field in COMMITMENTS} == COMMITMENTS

    with pytest.raises(CaseAlreadyExistsError):
        store.create_preprovisioned_case(
            case_id="case-1",
            root_revision_id="r000-other",
            **COMMITMENTS,
        )
    assert not hasattr(store, "update_case_commitments")


def test_object_store_hashes_external_payload_atomically(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "ledger.sqlite", tmp_path / "objects")
    payload = b"synthetic large XML, trace, and image bytes"
    digest = hashlib.sha256(payload).hexdigest()

    reference = store.objects.put_bytes(payload, expected_sha256=digest)

    path = tmp_path / "objects" / "sha256" / digest[:2] / digest
    assert reference.sha256 == digest
    assert reference.bytes == len(payload)
    assert path.read_bytes() == payload
    assert not list((tmp_path / "objects" / "sha256").glob(".tmp-*"))
    assert store.objects.read_bytes(digest) == payload
    with sqlite3.connect(tmp_path / "ledger.sqlite") as connection:
        assert connection.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 0
        assert payload.decode() not in "".join(
            str(row) for row in connection.execute("SELECT name, sql FROM sqlite_master")
        )

    with pytest.raises(ObjectIntegrityError):
        store.objects.put_bytes(payload, expected_sha256="0" * 64)
    assert not list((tmp_path / "objects" / "sha256").glob(".tmp-*"))

    path.write_bytes(b"mutated")
    with pytest.raises(ObjectIntegrityError):
        store.objects.read_bytes(digest)


def test_object_store_wraps_root_creation_failure(tmp_path: Path) -> None:
    root = tmp_path / "objects-file"
    root.write_bytes(b"not a directory")
    store = ObjectStore(root)

    with pytest.raises(ObjectIntegrityError, match="publication failed"):
        store.put_bytes(b"payload")


def test_object_store_verifies_and_returns_bytes_from_one_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ObjectStore(tmp_path / "objects")
    payload = b"single descriptor object read"
    digest = store.put_bytes(payload).sha256

    def reject_second_path_read(_path: Path) -> bytes:
        raise AssertionError("read_bytes must not reopen the canonical path")

    monkeypatch.setattr(Path, "read_bytes", reject_second_path_read)

    assert store.read_bytes(digest) == payload


def test_object_store_syncs_new_shard_and_hash_root_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    object_store = ObjectStore(tmp_path / "objects")
    synchronized: list[Path] = []
    monkeypatch.setattr(
        object_store,
        "_fsync_directory",
        lambda path: synchronized.append(path),
    )

    payload = b"first object in a new shard"
    digest = hashlib.sha256(payload).hexdigest()
    object_store.put_bytes(payload, expected_sha256=digest)

    hash_root = tmp_path / "objects" / "sha256"
    shard = hash_root / digest[:2]
    assert synchronized == directory_chain_to_root(shard)


def test_new_object_syncs_ancestors_even_when_they_already_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    object_store = ObjectStore(tmp_path / "objects")
    object_store.hash_root.mkdir(parents=True)
    synchronized: list[Path] = []
    monkeypatch.setattr(
        object_store,
        "_fsync_directory",
        lambda path: synchronized.append(path),
    )

    payload = b"publisher observed a concurrently created object root"
    digest = hashlib.sha256(payload).hexdigest()
    object_store.put_bytes(payload, expected_sha256=digest)

    hash_root = tmp_path / "objects" / "sha256"
    assert synchronized == directory_chain_to_root(hash_root / digest[:2])


def test_existing_object_removes_temporary_before_directory_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    object_store = ObjectStore(tmp_path / "objects")
    payload = b"deduplicated object publication"
    digest = hashlib.sha256(payload).hexdigest()
    object_store.put_bytes(payload, expected_sha256=digest)

    original_fsync = object_store._fsync_directory
    temp_files_seen_during_sync: list[Path] = []

    def record_temp_files(path: Path) -> None:
        temp_files_seen_during_sync.extend(object_store.hash_root.glob(".tmp-*"))
        original_fsync(path)

    monkeypatch.setattr(object_store, "_fsync_directory", record_temp_files)

    object_store.put_bytes(payload, expected_sha256=digest)

    assert temp_files_seen_during_sync == []
    assert not list(object_store.hash_root.glob(".tmp-*"))


def test_object_store_creates_and_syncs_missing_parent_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    object_store = ObjectStore(tmp_path / "new" / "a" / "objects")
    payload = b"object root with multiple missing ancestors"
    digest = hashlib.sha256(payload).hexdigest()
    synchronized: list[Path] = []
    original_fsync = object_store._fsync_directory

    def record_fsync(path: Path) -> None:
        synchronized.append(path)
        original_fsync(path)

    monkeypatch.setattr(object_store, "_fsync_directory", record_fsync)

    reference = object_store.put_bytes(payload, expected_sha256=digest)

    assert reference.sha256 == digest
    assert reference.bytes == len(payload)
    assert object_store.read_bytes(digest) == payload
    assert tmp_path / "new" in synchronized
    assert tmp_path / "new" / "a" in synchronized
    assert tmp_path / "new" / "a" / "objects" in synchronized


def test_concurrent_object_publication_syncs_existing_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    object_root = tmp_path / "new" / "a" / "objects"
    first_store = ObjectStore(object_root)
    second_store = ObjectStore(object_root)
    payload = b"same digest published concurrently"
    digest = hashlib.sha256(payload).hexdigest()
    first_started = threading.Event()
    release_first = threading.Event()
    first_errors: list[BaseException] = []

    def hold_first_sync(_path: Path) -> None:
        first_started.set()
        release_first.wait(timeout=5)

    monkeypatch.setattr(first_store, "_fsync_directory", hold_first_sync)
    second_synchronized: list[Path] = []
    monkeypatch.setattr(
        second_store,
        "_fsync_directory",
        lambda path: second_synchronized.append(path),
    )

    def publish_first() -> None:
        try:
            first_store.put_bytes(payload, expected_sha256=digest)
        except BaseException as exc:
            first_errors.append(exc)

    first_thread = threading.Thread(target=publish_first)
    first_thread.start()
    assert first_started.wait(timeout=5)

    second_store.put_bytes(payload, expected_sha256=digest)
    release_first.set()
    first_thread.join(timeout=5)

    assert not first_thread.is_alive()
    assert first_errors == []
    hash_root = object_root / "sha256"
    shard = hash_root / digest[:2]
    assert second_synchronized == directory_chain_to_root(shard)


def test_event_artifact_references_must_be_objects(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    before = store.ledger_events("case-1")

    invalid_references = (
        42,
        {},
        {"sha256": "changed", "kind": "trace", "size": 1, "media_type": "text/plain"},
        {"sha256": "f" * 64, "kind": "trace", "size": -1, "media_type": "text/plain"},
        {"sha256": "f" * 64, "kind": "trace", "size": True, "media_type": "text/plain"},
    )
    for index, reference in enumerate(invalid_references):
        with pytest.raises(ValidationError):
            store.append_event(
                LedgerEventRecord(
                    event_id=f"evt-invalid-artifact-ref-{index}",
                    case_id="case-1",
                    revision_id="r000",
                    event_type="EVIDENCE_RECORDED",
                    payload={},
                    artifact_refs=(reference,),
                )
            )

    assert store.ledger_events("case-1") == before
    published = store.objects.put_bytes(b"x")
    valid_reference = {
        "sha256": published.sha256,
        "kind": "trace",
        "size": published.bytes,
        "media_type": "text/plain",
    }
    store.append_event(
        LedgerEventRecord(
            event_id="evt-valid-artifact-ref",
            case_id="case-1",
            revision_id="r000",
            event_type="EVIDENCE_RECORDED",
            payload={},
            artifact_refs=(valid_reference,),
        )
    )
    assert store.ledger_events("case-1")[-1].artifact_refs == (valid_reference,)


def test_event_artifact_reference_snapshot_survives_mutation_during_and_after_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    verified = store.objects.put_bytes(b"a")
    replacement = store.objects.put_bytes(b"b")
    reference = {
        "sha256": verified.sha256,
        "kind": "trace",
        "size": verified.bytes,
        "media_type": "application/octet-stream",
    }
    expected = dict(reference)
    original_read = store.objects.read_bytes

    def mutate_during_verification(digest: str) -> bytes:
        data = original_read(digest)
        reference.update(
            sha256=replacement.sha256,
            kind="changed-during-call",
            size=replacement.bytes,
            media_type="text/plain",
        )
        return data

    monkeypatch.setattr(store.objects, "read_bytes", mutate_during_verification)

    stored = store.append_event(
        LedgerEventRecord(
            event_id="evt-frozen-artifact-ref",
            case_id="case-1",
            revision_id="r000",
            event_type="EVIDENCE_RECORDED",
            payload={},
            artifact_refs=[reference],
        )
    )
    reference.update(
        sha256="invalid-after-call",
        kind="changed-after-call",
        size=999,
        media_type="text/plain",
    )

    assert stored.artifact_refs == (expected,)
    store.close()

    reopened = EvidenceStore(tmp_path / "ledger.sqlite", tmp_path / "objects")
    replayed = next(
        event
        for event in reopened.verify_ledger()
        if event.event_id == "evt-frozen-artifact-ref"
    )
    assert replayed.artifact_refs == (expected,)


@pytest.mark.parametrize("failure", ["missing", "wrong_hash", "wrong_size"])
def test_event_artifact_reference_must_match_stored_object(
    tmp_path: Path, failure: str
) -> None:
    store = make_store(tmp_path)
    payload = b"expected"
    digest = hashlib.sha256(payload).hexdigest()
    size = len(payload)
    if failure == "wrong_hash":
        path = store.objects.path_for(digest)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"corrupt!")
    elif failure == "wrong_size":
        store.objects.put_bytes(payload)
        size += 1
    before = store.ledger_events("case-1")

    with pytest.raises(ObjectIntegrityError):
        store.append_event(
            LedgerEventRecord(
                event_id=f"evt-{failure}-artifact",
                case_id="case-1",
                revision_id="r000",
                event_type="EVIDENCE_RECORDED",
                payload={},
                artifact_refs=(
                    {
                        "sha256": digest,
                        "kind": "trace",
                        "size": size,
                        "media_type": "application/octet-stream",
                    },
                ),
            )
        )

    assert store.ledger_events("case-1") == before


def test_record_run_accepts_a_valid_artifact_reference(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    published = store.objects.put_bytes(b"trace")
    reference = {
        "sha256": published.sha256,
        "kind": "trace",
        "size": published.bytes,
        "media_type": "application/octet-stream",
    }

    run = store.record_run(
        run=RunRecord(
            run_id="run-with-artifact",
            case_id="case-1",
            revision_id="r000",
            run_kind="probe",
            probe_kind="joint_pulse",
            condition_hash=sha256_text("condition-artifact"),
            execution_fingerprint=sha256_text("execution-artifact"),
            trace_sha256=published.sha256,
            passed=True,
        ),
        event=LedgerEventRecord(
            event_id="evt-run-with-artifact",
            case_id="case-1",
            revision_id="r000",
            event_type="RUN_RECORDED",
            payload={"run_id": "run-with-artifact"},
            artifact_refs=(reference,),
        ),
    )

    assert run.run_id == "run-with-artifact"
    assert store.ledger_events("case-1")[-1].artifact_refs == (reference,)


@pytest.mark.parametrize("field", ["condition_hash", "execution_fingerprint"])
def test_record_run_rejects_malformed_identity_digest(
    tmp_path: Path, field: str
) -> None:
    store = make_store(tmp_path)
    values = {
        "run_id": "run-invalid-digest",
        "case_id": "case-1",
        "revision_id": "r000",
        "run_kind": "probe",
        "probe_kind": "joint_pulse",
        "condition_hash": sha256_text("condition"),
        "execution_fingerprint": sha256_text("execution"),
        "passed": False,
    }
    values[field] = "not-a-sha256"

    with pytest.raises(ValidationError, match=field):
        store.record_run(run=RunRecord(**values))
    with pytest.raises(StorageError, match="run was not found"):
        store.get_run("run-invalid-digest")


def test_get_run_reports_a_corrupt_identity_digest_as_integrity_error(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    store.record_run(
        run=RunRecord(
            run_id="run-corrupt-digest",
            case_id="case-1",
            revision_id="r000",
            run_kind="probe",
            probe_kind="joint_pulse",
            condition_hash=sha256_text("condition"),
            execution_fingerprint=sha256_text("execution"),
            passed=False,
        )
    )
    with sqlite3.connect(tmp_path / "ledger.sqlite") as connection:
        connection.execute(
            "UPDATE runs SET condition_hash = 'corrupt' WHERE run_id = ?",
            ("run-corrupt-digest",),
        )
        connection.commit()

    with pytest.raises(IntegrityError, match="stored run is invalid"):
        store.get_run("run-corrupt-digest")


def test_failed_partial_run_and_ledger_evidence_survive_reopen(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    partial = store.objects.put_bytes(b'{"completed_steps":128}')
    reference = {
        "sha256": partial.sha256,
        "kind": "partial_experiment",
        "size": partial.bytes,
        "media_type": "application/json",
    }
    expected = RunRecord(
        run_id="run-partial-1",
        case_id="case-1",
        revision_id="r000",
        run_kind="probe",
        probe_kind="agent_defined",
        condition_hash=sha256_text("condition-partial"),
        execution_fingerprint=sha256_text("execution-partial"),
        trace_sha256=None,
        metrics_sha256=None,
        passed=False,
    )
    stored = store.record_run(
        run=expected,
        event=LedgerEventRecord(
            event_id="evt-partial-1",
            request_id="req-partial-1",
            case_id="case-1",
            revision_id="r000",
            event_type="EXPERIMENT_FAILED",
            payload={
                "run_id": expected.run_id,
                "completed_steps": 128,
                "completed_segment_boundaries": [
                    {"segment_index": 0, "start_step": 0, "end_step": 128}
                ],
            },
            artifact_refs=(reference,),
        ),
    )
    store.close()

    reopened = EvidenceStore(tmp_path / "ledger.sqlite", tmp_path / "objects")

    reopened.restore_state("case-1")
    assert reopened.get_run(expected.run_id) == stored
    events = reopened.verify_ledger()
    assert events[-1].event_type == "EXPERIMENT_FAILED"
    assert events[-1].artifact_refs == (reference,)
    assert reopened.objects.read_bytes(partial.sha256) == b'{"completed_steps":128}'


def test_record_run_missing_artifact_rolls_back_run_and_ledger(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    missing_digest = hashlib.sha256(b"missing").hexdigest()
    before = store.ledger_events("case-1")

    with pytest.raises(ObjectIntegrityError):
        store.record_run(
            run=RunRecord(
                run_id="run-missing-artifact",
                case_id="case-1",
                revision_id="r000",
                run_kind="probe",
                probe_kind="joint_pulse",
                condition_hash=sha256_text("condition-missing"),
                execution_fingerprint=sha256_text("execution-missing"),
                trace_sha256=missing_digest,
                passed=False,
            ),
            event=LedgerEventRecord(
                event_id="evt-run-missing-artifact",
                case_id="case-1",
                revision_id="r000",
                event_type="RUN_RECORDED",
                payload={"run_id": "run-missing-artifact"},
                artifact_refs=(
                    {
                        "sha256": missing_digest,
                        "kind": "trace",
                        "size": len(b"missing"),
                        "media_type": "application/octet-stream",
                    },
                ),
            ),
        )

    with pytest.raises(StorageError, match="run was not found"):
        store.get_run("run-missing-artifact")
    assert store.ledger_events("case-1") == before


@pytest.mark.parametrize("artifact_exists", [False, True])
def test_revision_commit_validates_artifact_before_mutation(
    tmp_path: Path, artifact_exists: bool
) -> None:
    store = make_store(tmp_path)
    add_probe_evidence(store)
    payload = b"patch"
    digest = hashlib.sha256(payload).hexdigest()
    if artifact_exists:
        store.objects.put_bytes(payload)
    revision = RevisionRecord(
        case_id="case-1",
        revision_id="r001",
        parent_revision_id="r000",
        ordinal=1,
        asset_sha256="2" * 64,
        patch_manifest_sha256="3" * 64,
        hypothesis_event_id="evt-hypothesis-1",
        probe_run_id="run-probe-1",
    )
    reference = {
        "sha256": digest,
        "kind": "patch-manifest",
        "size": len(payload),
        "media_type": "application/json",
    }
    event = LedgerEventRecord(
        event_id="evt-revision-r001",
        case_id="case-1",
        revision_id="r001",
        event_type="REVISION_CREATED",
        payload=revision_event_payload(revision, store.get_run("run-probe-1")),
        artifact_refs=(reference,),
    )
    before = store.ledger_events("case-1")

    if artifact_exists:
        assert store.commit_revision_with_event(
            revision=revision,
            event=event,
            expected_head_revision_id="r000",
        ).revision_id == "r001"
        assert store.ledger_events("case-1")[-1].artifact_refs == (reference,)
    else:
        with pytest.raises(ObjectIntegrityError):
            store.commit_revision_with_event(
                revision=revision,
                event=event,
                expected_head_revision_id="r000",
            )
        with pytest.raises(StorageError, match="revision was not found"):
            store.get_revision("case-1", "r001")
        assert store.get_case("case-1").head_revision_id == "r000"
        assert store.ledger_events("case-1") == before


@pytest.mark.parametrize(
    "event_type",
    [
        "CASE_CREATED",
        "REVISION_CREATED",
        "QUALIFICATION_RESERVED",
        "QUALIFICATION_PASSED",
        "QUALIFICATION_FAILED",
    ],
)
def test_generic_append_rejects_transaction_owned_events(
    tmp_path: Path, event_type: str
) -> None:
    store = make_store(tmp_path)
    before = store.ledger_events("case-1")

    with pytest.raises(ValidationError):
        store.append_event(
            LedgerEventRecord(
                event_id=f"evt-bypass-{event_type.lower()}",
                case_id="case-1",
                revision_id="r000",
                event_type=event_type,
                payload={},
            )
        )

    assert store.ledger_events("case-1") == before


def test_revision_and_ledger_event_are_one_atomic_transaction(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_probe_evidence(store)
    add_child(store)

    child = store.get_revision("case-1", "r001")
    assert child.hypothesis_event_id == "evt-hypothesis-1"
    assert child.probe_run_id == "run-probe-1"
    assert store.get_case("case-1").head_revision_id == "r001"
    revision_event = store.ledger_events("case-1")[-1]
    assert revision_event.event_id == "evt-revision-r001"
    assert revision_event.payload == revision_event_payload(
        child, store.get_run("run-probe-1")
    )
    assert "created_at" not in revision_event.payload["probe_run"]

    store.append_event(
        LedgerEventRecord(
            event_id="evt-hypothesis-2",
            case_id="case-1",
            revision_id="r001",
            event_type="HYPOTHESIS_RECORDED",
            payload={"claim": "second synthetic claim"},
        )
    )
    store.record_run(
        run=RunRecord(
            run_id="run-probe-2",
            case_id="case-1",
            revision_id="r001",
            run_kind="probe",
            probe_kind="pose_hold",
            condition_hash=sha256_text("condition-2"),
            execution_fingerprint=sha256_text("execution-2"),
            passed=True,
        )
    )
    before = len(store.ledger_events())
    with pytest.raises(IntegrityError):
        store.commit_revision_with_event(
            revision=RevisionRecord(
                case_id="case-1",
                revision_id="r002",
                parent_revision_id="r001",
                ordinal=2,
                asset_sha256="8" * 64,
                patch_manifest_sha256="7" * 64,
                hypothesis_event_id="evt-hypothesis-2",
                probe_run_id="run-probe-2",
            ),
            event=LedgerEventRecord(
                event_id="evt-hypothesis-1",
                case_id="case-1",
                revision_id="r002",
                event_type="REVISION_CREATED",
                payload={
                    "parent_revision_id": "r001",
                    "ordinal": 2,
                    "asset_sha256": "8" * 64,
                    "patch_manifest_sha256": "7" * 64,
                    "hypothesis_event_id": "evt-hypothesis-2",
                    "probe_run_id": "run-probe-2",
                    "probe_run": run_event_payload(store.get_run("run-probe-2")),
                },
            ),
            expected_head_revision_id="r001",
        )
    assert len(store.ledger_events()) == before
    with pytest.raises(Exception):
        store.get_revision("case-1", "r002")
    assert store.get_case("case-1").head_revision_id == "r001"

    with pytest.raises(RevisionConflictError):
        store.commit_revision_with_event(
            revision=RevisionRecord(
                case_id="case-1",
                revision_id="r003",
                parent_revision_id="r001",
                ordinal=2,
                asset_sha256="9" * 64,
                patch_manifest_sha256="a" * 64,
                hypothesis_event_id="evt-hypothesis-1",
                probe_run_id="run-probe-1",
            ),
            event=LedgerEventRecord(
                event_id="evt-revision-r003",
                case_id="case-1",
                revision_id="r003",
                event_type="REVISION_CREATED",
                payload={
                    "parent_revision_id": "r001",
                    "ordinal": 2,
                    "asset_sha256": "9" * 64,
                    "patch_manifest_sha256": "a" * 64,
                    "hypothesis_event_id": "evt-hypothesis-1",
                    "probe_run_id": "run-probe-1",
                    "probe_run": run_event_payload(store.get_run("run-probe-1")),
                },
            ),
            expected_head_revision_id="r000",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_type", "DIFFERENT_EVENT"),
        ("payload", {"asset_sha256": "changed"}),
        ("artifact_refs", ({"sha256": "changed"},)),
        ("request_id", "request-changed"),
        ("created_at", "changed-time"),
    ],
)
def test_revision_retry_rejects_changed_ledger_event(
    tmp_path: Path, field: str, value: object
) -> None:
    store = make_store(tmp_path)
    add_probe_evidence(store)
    add_child(store)
    child = store.get_revision("case-1", "r001")
    persisted = next(
        event for event in store.ledger_events("case-1") if event.event_id == "evt-revision-r001"
    )

    supplied = {
        "event_type": persisted.event_type,
        "payload": persisted.payload,
        "artifact_refs": persisted.artifact_refs,
        "request_id": persisted.request_id,
        "created_at": persisted.created_at,
    }
    supplied[field] = value
    with pytest.raises((IntegrityError, ValidationError)):
        store.commit_revision_with_event(
            revision=child,
            event=LedgerEventRecord(
                event_id=persisted.event_id,
                case_id=persisted.case_id,
                revision_id=persisted.revision_id,
                event_type=supplied["event_type"],
                payload=supplied["payload"],
                artifact_refs=supplied["artifact_refs"],
                request_id=supplied["request_id"],
                created_at=supplied["created_at"],
            ),
            expected_head_revision_id="r001",
        )
    assert store.get_revision("case-1", "r001") == child
    assert store.ledger_events("case-1")[-1].event_id == persisted.event_id


def test_revision_retry_accepts_omitted_generated_event_metadata(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_probe_evidence(store)
    add_child(store)
    child = store.get_revision("case-1", "r001")

    assert store.commit_revision_with_event(
        revision=child,
        event=LedgerEventRecord(
            event_id="evt-revision-r001",
            case_id="case-1",
            revision_id="r001",
            event_type="REVISION_CREATED",
            payload=revision_event_payload(child, store.get_run("run-probe-1")),
        ),
        expected_head_revision_id="r001",
    ) == child


def test_revision_retry_rejects_changed_explicit_timestamp(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_probe_evidence(store)
    add_child(store)
    child = store.get_revision("case-1", "r001")

    with pytest.raises(RevisionConflictError):
        store.commit_revision_with_event(
            revision=RevisionRecord(
                case_id=child.case_id,
                revision_id=child.revision_id,
                parent_revision_id=child.parent_revision_id,
                ordinal=child.ordinal,
                asset_sha256=child.asset_sha256,
                patch_manifest_sha256=child.patch_manifest_sha256,
                hypothesis_event_id=child.hypothesis_event_id,
                probe_run_id=child.probe_run_id,
                created_at="changed-time",
            ),
            event=LedgerEventRecord(
                event_id="evt-revision-r001",
                case_id="case-1",
                revision_id="r001",
                event_type="REVISION_CREATED",
                payload=revision_event_payload(child, store.get_run("run-probe-1")),
            ),
            expected_head_revision_id="r001",
        )


def test_child_revision_requires_probe_run(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.append_event(
        LedgerEventRecord(
            event_id="evt-task-hypothesis",
            case_id="case-1",
            revision_id="r000",
            event_type="HYPOTHESIS_RECORDED",
            payload={"claim": "synthetic task"},
        )
    )
    store.record_run(
        run=RunRecord(
            run_id="run-task-1",
            case_id="case-1",
            revision_id="r000",
            run_kind="task",
            probe_kind=None,
            condition_hash=sha256_text("condition-task"),
            execution_fingerprint=sha256_text("execution-task"),
            passed=True,
        )
    )

    with pytest.raises(RevisionConflictError):
        store.commit_revision_with_event(
            revision=RevisionRecord(
                case_id="case-1",
                revision_id="r001",
                parent_revision_id="r000",
                ordinal=1,
                asset_sha256="2" * 64,
                patch_manifest_sha256="3" * 64,
                hypothesis_event_id="evt-task-hypothesis",
                probe_run_id="run-task-1",
            ),
            event=LedgerEventRecord(
                event_id="evt-revision-r001",
                case_id="case-1",
                revision_id="r001",
                event_type="REVISION_CREATED",
                payload={
                    "parent_revision_id": "r000",
                    "ordinal": 1,
                    "asset_sha256": "2" * 64,
                    "patch_manifest_sha256": "3" * 64,
                    "hypothesis_event_id": "evt-task-hypothesis",
                    "probe_run_id": "run-task-1",
                    "probe_run": run_event_payload(store.get_run("run-task-1")),
                },
            ),
            expected_head_revision_id="r000",
        )


def test_child_revision_requires_patch_manifest(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_probe_evidence(store)

    with pytest.raises(ValidationError, match="requires a patch manifest"):
        store.commit_revision_with_event(
            revision=RevisionRecord(
                case_id="case-1",
                revision_id="r001",
                parent_revision_id="r000",
                ordinal=1,
                asset_sha256="2" * 64,
                hypothesis_event_id="evt-hypothesis-1",
                probe_run_id="run-probe-1",
            ),
            event=LedgerEventRecord(
                event_id="evt-revision-r001",
                case_id="case-1",
                revision_id="r001",
                event_type="REVISION_CREATED",
                payload={},
            ),
            expected_head_revision_id="r000",
        )


def test_revision_event_binding_preserves_json_types(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_probe_evidence(store)
    revision = RevisionRecord(
        case_id="case-1",
        revision_id="r001",
        parent_revision_id="r000",
        ordinal=1,
        asset_sha256="2" * 64,
        patch_manifest_sha256="3" * 64,
        hypothesis_event_id="evt-hypothesis-1",
        probe_run_id="run-probe-1",
    )
    payload = revision_event_payload(revision, store.get_run("run-probe-1"))
    payload["ordinal"] = True

    with pytest.raises(ValidationError, match="does not bind the revision"):
        store.commit_revision_with_event(
            revision=revision,
            event=LedgerEventRecord(
                event_id="evt-revision-r001",
                case_id="case-1",
                revision_id="r001",
                event_type="REVISION_CREATED",
                payload=payload,
            ),
            expected_head_revision_id="r000",
        )


def test_event_chain_detects_mutation_and_restores_state(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_probe_evidence(store)
    assert len(store.verify_ledger()) == 2

    with sqlite3.connect(tmp_path / "ledger.sqlite") as connection:
        connection.execute(
            "UPDATE ledger_events SET payload_json = ? WHERE event_id = ?",
            (json.dumps({"mutated": True}), "evt-hypothesis-1"),
        )
        connection.commit()
    with pytest.raises(IntegrityError):
        store.verify_ledger()
    with pytest.raises(IntegrityError):
        store.restore_state("case-1")


def test_event_chain_detects_tail_deletion(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_probe_evidence(store)

    with sqlite3.connect(tmp_path / "ledger.sqlite") as connection:
        connection.execute(
            "DELETE FROM ledger_events WHERE seq = (SELECT MAX(seq) FROM ledger_events)"
        )
        connection.commit()

    with pytest.raises(IntegrityError, match="tail is missing"):
        store.verify_ledger()
    with pytest.raises(IntegrityError, match="tail is missing"):
        store.restore_state("case-1")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_asset_sha256", "f" * 64),
        ("head_revision_id", "r000"),
        ("qualification_attempt_id", "attempt-corrupted"),
        ("qualification_result", "FAILED"),
    ],
)
def test_restore_rejects_materialized_state_that_differs_from_ledger(
    tmp_path: Path, field: str, value: str | None
) -> None:
    store = make_store(tmp_path)
    add_probe_evidence(store)
    add_child(store)
    qualify(store)
    identity = {
        "case_id": "case-1",
        "attempt_id": "attempt-1",
        "revision_id": "r001",
        "suite_commitment_sha256": "4" * 64,
        "scenario_hashes": ("5" * 64, "6" * 64, "7" * 64),
    }
    store.record_qualification_terminal(**identity, state="PASSED")

    with sqlite3.connect(tmp_path / "ledger.sqlite") as connection:
        connection.execute(f"UPDATE cases SET {field} = ? WHERE case_id = ?", (value, "case-1"))
        connection.commit()

    with pytest.raises(
        IntegrityError, match="materialized case state|commitments|reservation differs"
    ):
        store.restore_state("case-1")


def test_restore_uses_one_snapshot_during_concurrent_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    add_probe_evidence(store)
    concurrent_store = EvidenceStore(tmp_path / "ledger.sqlite", tmp_path / "objects")
    original_verify = store._verified_ledger_from_connection

    def verify_then_commit(connection: sqlite3.Connection):
        events = original_verify(connection)
        add_child(concurrent_store)
        return events

    monkeypatch.setattr(store, "_verified_ledger_from_connection", verify_then_commit)

    restored = store.restore_state("case-1")

    assert restored.head_revision_id == "r000"
    assert store.get_case("case-1").head_revision_id == "r001"


@pytest.mark.parametrize(
    "field",
    [
        "controller_sha256",
        "public_contract_sha256",
        "runner_sha256",
        "holdout_commitment_sha256",
    ],
)
def test_restore_rejects_unqualified_case_commitment_corruption(
    tmp_path: Path, field: str
) -> None:
    store = make_store(tmp_path)

    with sqlite3.connect(tmp_path / "ledger.sqlite") as connection:
        connection.execute(
            f"UPDATE cases SET {field} = ? WHERE case_id = ?",
            ("f" * 64, "case-1"),
        )
        connection.commit()

    with pytest.raises(IntegrityError, match="case commitments"):
        store.restore_state("case-1")


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (
            f"UPDATE revisions SET patch_manifest_sha256 = '{'f' * 64}' "
            "WHERE revision_id = 'r000'",
            "root revision state",
        ),
        (
            "DELETE FROM revisions WHERE revision_id = 'r001'",
            "revision event state|missing storage identity",
        ),
        ("UPDATE revisions SET ordinal = 9 WHERE revision_id = 'r001'", "revision event state"),
        (
            f"UPDATE revisions SET asset_sha256 = '{'f' * 64}' WHERE revision_id = 'r001'",
            "does not match its ledger event",
        ),
        ("DELETE FROM runs WHERE run_id = 'run-probe-1'", "causal citations"),
        (
            "UPDATE runs SET condition_hash = 'changed' WHERE run_id = 'run-probe-1'",
            "stored run is invalid",
        ),
        (
            """INSERT INTO revisions (
                case_id, revision_id, parent_revision_id, ordinal,
                asset_sha256, patch_manifest_sha256,
                hypothesis_event_id, probe_run_id, created_at
            ) VALUES (
                'case-1', 'r999', 'r001', 2,
                'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff',
                NULL, 'evt-hypothesis-1', 'run-probe-1', '2026-01-01T00:00:00Z'
            )""",
            "revision rows do not match",
        ),
    ],
)
def test_restore_rejects_missing_or_invalid_child_revision_state(
    tmp_path: Path, mutation: str, error: str
) -> None:
    store = make_store(tmp_path)
    add_probe_evidence(store)
    add_child(store)

    with sqlite3.connect(tmp_path / "ledger.sqlite") as connection:
        connection.execute(mutation)
        connection.commit()

    with pytest.raises(IntegrityError, match=error):
        store.restore_state("case-1")


def test_qualification_reservation_requires_exact_case_commitments(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_probe_evidence(store)
    add_child(store)
    mismatched = {**COMMITMENTS, "controller_sha256": "f" * 64}
    parameters = inspect.signature(store.reserve_qualification).parameters
    assert all(
        parameters[field].kind is inspect.Parameter.KEYWORD_ONLY
        and parameters[field].default is inspect.Parameter.empty
        for field in COMMITMENTS
    )

    with pytest.raises(QualificationConflictError, match="commitments differ"):
        store.reserve_qualification(
            case_id="case-1",
            revision_id="r001",
            attempt_id="attempt-1",
            suite_commitment_sha256="4" * 64,
            scenario_hashes=("5" * 64,),
            expected_head_revision_id="r001",
            **mismatched,
        )

    assert store.get_case("case-1").qualification_state == "unused"


def test_get_qualification_reports_corrupt_case_commitment_as_integrity_error(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    add_probe_evidence(store)
    add_child(store)
    qualify(store)

    with sqlite3.connect(tmp_path / "ledger.sqlite") as connection:
        connection.execute(
            "UPDATE cases SET controller_sha256 = 'invalid' WHERE case_id = 'case-1'"
        )
        connection.commit()

    with pytest.raises(IntegrityError, match="stored case commitments"):
        store.get_qualification("case-1")


def test_qualification_reads_verify_the_reservation_event(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_probe_evidence(store)
    add_child(store)
    qualify(store)

    with sqlite3.connect(tmp_path / "ledger.sqlite") as connection:
        connection.execute(
            """
            UPDATE ledger_events SET payload_json = ?
            WHERE event_type = 'QUALIFICATION_RESERVED'
            """,
            (
                json.dumps(
                    {
                        "attempt_id": "forged-attempt",
                        "revision_id": "r001",
                        "suite_commitment_sha256": "4" * 64,
                        "scenario_hashes": ["5" * 64, "6" * 64, "7" * 64],
                        **COMMITMENTS,
                    }
                ),
            ),
        )
        connection.commit()

    with pytest.raises(IntegrityError, match="ledger event hash mismatch"):
        store.get_qualification("case-1")


def test_malformed_stored_qualification_identity_is_an_integrity_error(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    add_probe_evidence(store)
    add_child(store)
    qualify(store)
    replace_event_payload(
        tmp_path / "ledger.sqlite",
        "QUALIFICATION_RESERVED",
        {
            "attempt_id": "",
            "revision_id": "r001",
            "suite_commitment_sha256": "4" * 64,
            "scenario_hashes": ["5" * 64, "6" * 64, "7" * 64],
            **COMMITMENTS,
        },
    )

    with pytest.raises(IntegrityError, match="qualification identity is invalid"):
        store.get_qualification("case-1")


def test_qualification_event_revision_must_match_its_payload(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_probe_evidence(store)
    add_child(store)
    qualify(store)
    with sqlite3.connect(tmp_path / "ledger.sqlite") as connection:
        connection.execute(
            """
            UPDATE ledger_events SET revision_id = 'r000'
            WHERE event_type = 'QUALIFICATION_RESERVED'
            """
        )
        connection.commit()
    replace_event_payload(
        tmp_path / "ledger.sqlite",
        "QUALIFICATION_RESERVED",
        {
            "attempt_id": "attempt-1",
            "revision_id": "r001",
            "suite_commitment_sha256": "4" * 64,
            "scenario_hashes": ["5" * 64, "6" * 64, "7" * 64],
            **COMMITMENTS,
        },
    )

    with pytest.raises(IntegrityError, match="qualification event revision is invalid"):
        store.get_qualification("case-1")
    with pytest.raises(IntegrityError, match="qualification event revision is invalid"):
        store.restore_state("case-1")


def test_qualification_reservation_must_match_materialized_case(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_probe_evidence(store)
    add_child(store)
    qualify(store)
    replace_event_payload(
        tmp_path / "ledger.sqlite",
        "QUALIFICATION_RESERVED",
        {
            "attempt_id": "forged-attempt",
            "revision_id": "r001",
            "suite_commitment_sha256": "4" * 64,
            "scenario_hashes": ["5" * 64, "6" * 64, "7" * 64],
            **COMMITMENTS,
        },
    )

    with pytest.raises(IntegrityError, match="reservation differs from the case"):
        store.get_qualification("case-1")


def test_nonterminal_qualification_event_cannot_expose_a_result(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_probe_evidence(store)
    add_child(store)
    qualify(store)
    replace_event_payload(
        tmp_path / "ledger.sqlite",
        "QUALIFICATION_RESERVED",
        {
            "attempt_id": "attempt-1",
            "revision_id": "r001",
            "suite_commitment_sha256": "4" * 64,
            "scenario_hashes": ["5" * 64, "6" * 64, "7" * 64],
            "result": {"private_score": 99},
            **COMMITMENTS,
        },
    )

    with pytest.raises(IntegrityError, match="nonterminal qualification result"):
        store.get_qualification("case-1")
    with pytest.raises(IntegrityError, match="nonterminal qualification result"):
        store.restore_state("case-1")


def test_terminal_case_still_rejects_result_on_reservation(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_probe_evidence(store)
    add_child(store)
    qualify(store)
    identity = {
        "case_id": "case-1",
        "attempt_id": "attempt-1",
        "revision_id": "r001",
        "suite_commitment_sha256": "4" * 64,
        "scenario_hashes": ("5" * 64, "6" * 64, "7" * 64),
    }
    store.record_qualification_terminal(**identity, state="PASSED")
    replace_event_payload(
        tmp_path / "ledger.sqlite",
        "QUALIFICATION_RESERVED",
        {
            "attempt_id": "attempt-1",
            "revision_id": "r001",
            "suite_commitment_sha256": "4" * 64,
            "scenario_hashes": ["5" * 64, "6" * 64, "7" * 64],
            "result": {"private_score": 99},
            **COMMITMENTS,
        },
    )

    with pytest.raises(IntegrityError, match="nonterminal qualification result"):
        store.get_qualification("case-1")


def test_restore_rejects_terminal_qualification_without_reservation(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_probe_evidence(store)
    add_child(store)
    qualify(store)
    with sqlite3.connect(tmp_path / "ledger.sqlite") as connection:
        connection.execute(
            """
            UPDATE ledger_events SET event_type = 'QUALIFICATION_PASSED'
            WHERE event_type = 'QUALIFICATION_RESERVED'
            """
        )
        connection.commit()
    replace_event_payload(
        tmp_path / "ledger.sqlite",
        "QUALIFICATION_PASSED",
        {
            "attempt_id": "attempt-1",
            "revision_id": "r001",
            "suite_commitment_sha256": "4" * 64,
            "scenario_hashes": ["5" * 64, "6" * 64, "7" * 64],
            **COMMITMENTS,
        },
    )

    with pytest.raises(IntegrityError, match="lifecycle transition is invalid"):
        store.restore_state("case-1")
    with pytest.raises(IntegrityError, match="lifecycle transition is invalid"):
        store.get_qualification("case-1")


def test_qualification_reserve_terminal_preserves_exact_identity(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_probe_evidence(store)
    add_child(store)
    qualify(store)
    identity = {
        "case_id": "case-1",
        "attempt_id": "attempt-1",
        "revision_id": "r001",
        "suite_commitment_sha256": "4" * 64,
        "scenario_hashes": ("5" * 64, "6" * 64, "7" * 64),
    }

    assert store.get_case("case-1").qualification_state == "running"
    assert store.get_qualification("case-1").state == "RUNNING"
    with pytest.raises(QualificationConflictError):
        store.record_qualification_terminal(
            **{**identity, "attempt_id": "other"}, state="PASSED"
        )
    assert store.get_case("case-1").qualification_state == "running"

    terminal = store.record_qualification_terminal(
        **identity,
        state="PASSED",
        result={
            "qualified_core_sha256": "9" * 64,
            "passed": 3,
            "members": ("a", "b"),
        },
    )
    assert terminal.state == "PASSED"
    assert terminal.result == {
        "qualified_core_sha256": "9" * 64,
        "passed": 3,
        "members": ["a", "b"],
    }
    assert store.get_case("case-1").qualification_state == "passed"
    assert store.get_qualification("case-1").result == terminal.result
    assert store.record_qualification_terminal(
        **identity,
        state="PASSED",
        result={
            "qualified_core_sha256": "9" * 64,
            "passed": 3,
            "members": ("a", "b"),
        },
    ).result == terminal.result
    qualification_events = tuple(
        event
        for event in store.ledger_events("case-1")
        if event.event_type.startswith("QUALIFICATION_")
    )
    assert [event.event_type for event in qualification_events] == [
        "QUALIFICATION_RESERVED",
        "QUALIFICATION_PASSED",
    ]
    assert all(
        {field: event.payload[field] for field in COMMITMENTS} == COMMITMENTS
        for event in qualification_events
    )
    with pytest.raises(QualificationConflictError):
        store.record_qualification_terminal(**identity, state="FAILED")
    with pytest.raises(QualificationConflictError):
        store.record_qualification_terminal(
            **identity,
            state="PASSED",
            result={"qualified_core_sha256": "0" * 64, "passed": 999},
        )
    with pytest.raises(QualificationConflictError):
        store.reserve_qualification(
            case_id="case-1",
            revision_id="r001",
            attempt_id="attempt-2",
            suite_commitment_sha256="a" * 64,
            scenario_hashes=("b" * 64,),
            expected_head_revision_id="r001",
            **COMMITMENTS,
        )


def test_qualification_terminal_reads_verify_the_ledger_chain(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_probe_evidence(store)
    add_child(store)
    qualify(store)
    identity = {
        "case_id": "case-1",
        "attempt_id": "attempt-1",
        "revision_id": "r001",
        "suite_commitment_sha256": "4" * 64,
        "scenario_hashes": ("5" * 64, "6" * 64, "7" * 64),
    }
    store.record_qualification_terminal(
        **identity, state="PASSED", result={"passed": 3}
    )

    with sqlite3.connect(tmp_path / "ledger.sqlite") as connection:
        connection.execute(
            """
            UPDATE ledger_events SET payload_json = ?
            WHERE event_type = 'QUALIFICATION_PASSED'
            """,
            (json.dumps({"result": {"passed": 999}}),),
        )
        connection.commit()

    with pytest.raises(IntegrityError, match="ledger event hash mismatch"):
        store.get_qualification("case-1")
    with pytest.raises(IntegrityError, match="ledger event hash mismatch"):
        store.record_qualification_terminal(
            **identity, state="PASSED", result={"passed": 3}
        )


def test_malformed_stored_terminal_result_is_an_integrity_error(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_probe_evidence(store)
    add_child(store)
    qualify(store)
    identity = {
        "case_id": "case-1",
        "attempt_id": "attempt-1",
        "revision_id": "r001",
        "suite_commitment_sha256": "4" * 64,
        "scenario_hashes": ("5" * 64, "6" * 64, "7" * 64),
    }
    store.record_qualification_terminal(
        **identity, state="PASSED", result={"passed": 3}
    )
    replace_event_payload(
        tmp_path / "ledger.sqlite",
        "QUALIFICATION_PASSED",
        {
            "attempt_id": "attempt-1",
            "revision_id": "r001",
            "suite_commitment_sha256": "4" * 64,
            "scenario_hashes": ["5" * 64, "6" * 64, "7" * 64],
            "result": "malformed-scalar",
            **COMMITMENTS,
        },
    )

    with pytest.raises(IntegrityError, match="qualification terminal result is invalid"):
        store.get_qualification("case-1")
    with pytest.raises(IntegrityError, match="qualification terminal result is invalid"):
        store.restore_state("case-1")


def test_uncited_run_cannot_reference_a_missing_revision(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.record_run(
        run=RunRecord(
            run_id="run-uncited",
            case_id="case-1",
            revision_id="r000",
            run_kind="probe",
            probe_kind="pose_hold",
            condition_hash=sha256_text("condition-uncited"),
            execution_fingerprint=sha256_text("execution-uncited"),
            passed=False,
        )
    )
    with sqlite3.connect(tmp_path / "ledger.sqlite") as connection:
        connection.execute(
            "UPDATE runs SET revision_id = 'r999' WHERE run_id = 'run-uncited'"
        )
        connection.commit()

    with pytest.raises(IntegrityError, match="stored run references missing revision"):
        store.get_run("run-uncited")


def _add_child_evidence(store: EvidenceStore) -> None:
    store.append_event(
        LedgerEventRecord(
            event_id="evt-hypothesis-2",
            case_id="case-1",
            revision_id="r001",
            event_type="HYPOTHESIS_RECORDED",
            payload={"claim": "synthetic second claim"},
        )
    )
    store.record_run(
        run=RunRecord(
            run_id="run-probe-2",
            case_id="case-1",
            revision_id="r001",
            run_kind="probe",
            probe_kind="pose_hold",
            condition_hash=sha256_text("condition-2"),
            execution_fingerprint=sha256_text("execution-2"),
            passed=False,
        )
    )


@pytest.mark.parametrize("sealed_state", ["RUNNING", "PASSED", "FAILED"])
def test_new_child_revision_is_rejected_after_lifecycle_seal(
    tmp_path: Path, sealed_state: str
) -> None:
    store = make_store(tmp_path)
    add_probe_evidence(store)
    add_child(store)
    _add_child_evidence(store)
    identity = {
        "case_id": "case-1",
        "attempt_id": "attempt-1",
        "revision_id": "r001",
        "suite_commitment_sha256": "4" * 64,
        "scenario_hashes": ("5" * 64, "6" * 64, "7" * 64),
    }
    if sealed_state == "RUNNING":
        qualify(store)
    elif sealed_state in {"PASSED", "FAILED"}:
        qualify(store)
        store.record_qualification_terminal(**identity, state=sealed_state)
    persisted = next(
        event for event in store.ledger_events("case-1") if event.event_id == "evt-revision-r001"
    )
    assert store.commit_revision_with_event(
        revision=store.get_revision("case-1", "r001"),
        event=LedgerEventRecord(
            event_id=persisted.event_id,
            case_id=persisted.case_id,
            revision_id=persisted.revision_id,
            event_type=persisted.event_type,
            payload=persisted.payload,
            artifact_refs=persisted.artifact_refs,
            request_id=persisted.request_id,
            created_at=persisted.created_at,
        ),
        expected_head_revision_id="r001",
    ).revision_id == "r001"

    with pytest.raises(RevisionConflictError):
        store.commit_revision_with_event(
            revision=RevisionRecord(
                case_id="case-1",
                revision_id="r002",
                parent_revision_id="r001",
                ordinal=2,
                asset_sha256="9" * 64,
                patch_manifest_sha256="a" * 64,
                hypothesis_event_id="evt-hypothesis-2",
                probe_run_id="run-probe-2",
            ),
            event=LedgerEventRecord(
                event_id="evt-revision-r002",
                case_id="case-1",
                revision_id="r002",
                event_type="REVISION_CREATED",
                payload={
                    "parent_revision_id": "r001",
                    "ordinal": 2,
                    "asset_sha256": "9" * 64,
                    "patch_manifest_sha256": "a" * 64,
                    "hypothesis_event_id": "evt-hypothesis-2",
                    "probe_run_id": "run-probe-2",
                    "probe_run": run_event_payload(store.get_run("run-probe-2")),
                },
            ),
            expected_head_revision_id="r001",
        )
    assert store.get_case("case-1").head_revision_id == "r001"
