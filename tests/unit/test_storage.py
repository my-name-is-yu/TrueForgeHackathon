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
    RevisionConflictError,
    RevisionRecord,
    RunRecord,
    ValidationError,
)


COMMITMENTS = {
    "source_asset_sha256": "a" * 64,
    "controller_sha256": "b" * 64,
    "public_contract_sha256": "c" * 64,
    "runner_sha256": "d" * 64,
    "holdout_commitment_sha256": "e" * 64,
}


def make_store(tmp_path: Path) -> EvidenceStore:
    store = EvidenceStore(tmp_path / "ledger.sqlite", tmp_path / "objects")
    store.create_preprovisioned_case(
        case_id="case-1",
        root_revision_id="r000",
        **COMMITMENTS,
    )
    return store


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
            condition_hash="condition-1",
            execution_fingerprint="execution-1",
            trace_sha256="f" * 64,
            metrics_sha256="1" * 64,
            passed=True,
        )
    )


def add_child(store: EvidenceStore, revision_id: str = "r001") -> None:
    store.commit_revision_with_event(
        revision=RevisionRecord(
            case_id="case-1",
            revision_id=revision_id,
            parent_revision_id="r000",
            ordinal=1,
            asset_sha256="2" * 64,
            patch_manifest_sha256="3" * 64,
            hypothesis_event_id="evt-hypothesis-1",
            probe_run_id="run-probe-1",
        ),
        event=LedgerEventRecord(
            event_id=f"evt-revision-{revision_id}",
            case_id="case-1",
            revision_id=revision_id,
            event_type="REVISION_CREATED",
            payload={"asset_sha256": "2" * 64},
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

    assert tables == {"cases", "revisions", "runs", "ledger_events"}
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
    assert case.promoted_revision_id is None
    assert {key: getattr(case, key) for key in COMMITMENTS} == COMMITMENTS
    root = store.get_revision("case-1", "r000")
    assert root.parent_revision_id is None
    assert root.hypothesis_event_id is None
    assert root.probe_run_id is None

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
    assert synchronized == [
        shard,
        hash_root,
        hash_root.parent,
        hash_root.parent.parent,
    ]


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
    assert synchronized == [
        hash_root / digest[:2],
        hash_root,
        hash_root.parent,
        hash_root.parent.parent,
    ]


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


def test_object_store_rejects_missing_parent_instead_of_recursive_creation(
    tmp_path: Path,
) -> None:
    object_store = ObjectStore(tmp_path / "new" / "a" / "objects")

    payload = b"object root with multiple missing ancestors"
    digest = hashlib.sha256(payload).hexdigest()

    with pytest.raises(ObjectIntegrityError, match="parent must already exist"):
        object_store.put_bytes(payload, expected_sha256=digest)

    assert not (tmp_path / "new").exists()


def test_concurrent_object_publication_syncs_existing_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_store = ObjectStore(tmp_path / "objects")
    second_store = ObjectStore(tmp_path / "objects")
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
    hash_root = tmp_path / "objects" / "sha256"
    shard = hash_root / digest[:2]
    assert second_synchronized == [
        shard,
        hash_root,
        hash_root.parent,
        hash_root.parent.parent,
    ]


def test_event_artifact_references_must_be_objects(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    before = store.ledger_events("case-1")

    with pytest.raises(ValidationError):
        store.append_event(
            LedgerEventRecord(
                event_id="evt-invalid-artifact-ref",
                case_id="case-1",
                revision_id="r000",
                event_type="EVIDENCE_RECORDED",
                payload={},
                artifact_refs=(42,),
            )
        )

    assert store.ledger_events("case-1") == before
    valid_reference = {
        "sha256": "f" * 64,
        "kind": "trace",
        "size": 1,
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


@pytest.mark.parametrize(
    "event_type",
    [
        "CASE_CREATED",
        "REVISION_CREATED",
        "QUALIFICATION_RESERVED",
        "QUALIFICATION_RECOVERING",
        "QUALIFICATION_RECOVERED",
        "QUALIFICATION_PASSED",
        "QUALIFICATION_FAILED",
        "PROMOTED",
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
    assert store.ledger_events("case-1")[-1].event_id == "evt-revision-r001"

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
            condition_hash="condition-2",
            execution_fingerprint="execution-2",
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
                hypothesis_event_id="evt-hypothesis-2",
                probe_run_id="run-probe-2",
            ),
            event=LedgerEventRecord(
                event_id="evt-hypothesis-1",
                case_id="case-1",
                revision_id="r002",
                event_type="REVISION_CREATED",
                payload={},
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
                hypothesis_event_id="evt-hypothesis-1",
                probe_run_id="run-probe-1",
            ),
            event=LedgerEventRecord(
                event_id="evt-revision-r003",
                case_id="case-1",
                revision_id="r003",
                event_type="REVISION_CREATED",
                payload={},
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
            payload={"asset_sha256": "2" * 64},
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
                payload={"asset_sha256": "2" * 64},
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
            condition_hash="condition-task",
            execution_fingerprint="execution-task",
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
                payload={"asset_sha256": "2" * 64},
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
        ("head_revision_id", "r000"),
        ("qualification_attempt_id", "attempt-corrupted"),
        ("qualification_result", "FAILED"),
        ("promoted_revision_id", None),
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
    store.record_promotion_receipt(
        case_id="case-1",
        revision_id="r001",
        ticket_id="ticket-1",
        manifest_sha256="8" * 64,
    )

    with sqlite3.connect(tmp_path / "ledger.sqlite") as connection:
        connection.execute(f"UPDATE cases SET {field} = ? WHERE case_id = ?", (value, "case-1"))
        connection.commit()

    with pytest.raises(IntegrityError, match="materialized case state"):
        store.restore_state("case-1")


def test_qualification_reserve_recover_terminal_preserves_exact_identity(tmp_path: Path) -> None:
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
    with pytest.raises(Exception):
        store.mark_qualification_recovering(**{**identity, "attempt_id": "other"})
    assert store.get_case("case-1").qualification_state == "running"

    recovering = store.mark_qualification_recovering(**identity)
    assert recovering.state == "RECOVERING"
    assert store.get_case("case-1").qualification_attempt_id == "attempt-1"
    with pytest.raises(Exception):
        store.recover_qualification(**{**identity, "scenario_hashes": ("8" * 64,)})
    assert store.get_case("case-1").qualification_state == "recovering"

    recovered = store.recover_qualification(**identity)
    assert recovered.state == "RUNNING"
    terminal = store.record_qualification_terminal(
        **identity,
        state="PASSED",
        result={"qualified_core_sha256": "9" * 64, "passed": 3},
    )
    assert terminal.state == "PASSED"
    assert terminal.result == {"qualified_core_sha256": "9" * 64, "passed": 3}
    assert store.get_case("case-1").qualification_state == "passed"
    assert store.get_qualification("case-1").result == terminal.result
    assert store.record_qualification_terminal(**identity, state="PASSED").result == terminal.result
    with pytest.raises(Exception):
        store.record_qualification_terminal(**identity, state="FAILED")
    with pytest.raises(Exception):
        store.record_qualification_terminal(
            **identity,
            state="PASSED",
            result={"qualified_core_sha256": "0" * 64, "passed": 999},
        )
    with pytest.raises(Exception):
        store.reserve_qualification(
            case_id="case-1",
            revision_id="r001",
            attempt_id="attempt-2",
            suite_commitment_sha256="a" * 64,
            scenario_hashes=("b" * 64,),
            expected_head_revision_id="r001",
        )


def test_promotion_receipt_is_atomic_and_reconcilable(tmp_path: Path) -> None:
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

    receipt = store.record_promotion_receipt(
        case_id="case-1",
        revision_id="r001",
        ticket_id="ticket-1",
        manifest_sha256="8" * 64,
        receipt={"export_name": "synthetic"},
    )
    assert store.get_case("case-1").promotion_state == "promoted"
    assert store.reconcile_promotion(case_id="case-1", revision_id="r001") == receipt
    assert store.record_promotion_receipt(
        case_id="case-1",
        revision_id="r001",
        ticket_id="ticket-1",
        manifest_sha256="8" * 64,
        receipt={"export_name": "synthetic"},
    ) == receipt
    with pytest.raises(Exception):
        store.record_promotion_receipt(
            case_id="case-1",
            revision_id="r001",
            ticket_id="ticket-2",
            manifest_sha256="8" * 64,
        )


def test_unqualified_promotion_does_not_mutate_case_or_ledger(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    before = len(store.ledger_events())
    with pytest.raises(Exception):
        store.record_promotion_receipt(
            case_id="case-1",
            revision_id="r000",
            ticket_id="ticket-1",
            manifest_sha256="8" * 64,
        )
    assert len(store.ledger_events()) == before
    assert store.get_case("case-1").promoted_revision_id is None


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
            condition_hash="condition-2",
            execution_fingerprint="execution-2",
            passed=False,
        )
    )


@pytest.mark.parametrize("sealed_state", ["RUNNING", "RECOVERING", "PASSED", "FAILED", "PROMOTED"])
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
    elif sealed_state == "RECOVERING":
        qualify(store)
        store.mark_qualification_recovering(**identity)
    elif sealed_state in {"PASSED", "FAILED"}:
        qualify(store)
        store.record_qualification_terminal(**identity, state=sealed_state)
    else:
        qualify(store)
        store.record_qualification_terminal(**identity, state="PASSED")
        store.record_promotion_receipt(
            case_id="case-1",
            revision_id="r001",
            ticket_id="ticket-1",
            manifest_sha256="8" * 64,
        )

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
                payload={"asset_sha256": "9" * 64},
            ),
            expected_head_revision_id="r001",
        )
    assert store.get_case("case-1").head_revision_id == "r001"
