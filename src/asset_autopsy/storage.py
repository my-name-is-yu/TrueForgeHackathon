from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Mapping, Sequence


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ZERO_HASH = "0" * 64
COMMITMENT_FIELDS = (
    "source_asset_sha256",
    "controller_sha256",
    "public_contract_sha256",
    "runner_sha256",
    "holdout_commitment_sha256",
)
REVISION_EVENT_FIELDS = (
    "parent_revision_id",
    "ordinal",
    "asset_sha256",
    "patch_manifest_sha256",
    "hypothesis_event_id",
    "probe_run_id",
)
RUN_EVENT_FIELDS = (
    "run_id",
    "case_id",
    "revision_id",
    "run_kind",
    "probe_kind",
    "condition_hash",
    "execution_fingerprint",
    "trace_sha256",
    "metrics_sha256",
    "passed",
)
TABLE_NAMES = ("cases", "revisions", "runs", "ledger_events")
TRANSACTIONAL_EVENT_TYPES = {
    "CASE_CREATED",
    "REVISION_CREATED",
    "QUALIFICATION_RESERVED",
    "QUALIFICATION_RECOVERING",
    "QUALIFICATION_RECOVERED",
    "QUALIFICATION_PASSED",
    "QUALIFICATION_FAILED",
    "PROMOTED",
}


class StorageError(Exception):
    """Base class for storage contract failures."""


class ValidationError(StorageError):
    """Raised when a typed storage input is invalid."""


class IntegrityError(StorageError):
    """Raised when persisted evidence does not match its integrity proof."""


StorageIntegrityError = IntegrityError


class CaseNotFoundError(StorageError):
    """Raised when a case identity is not present."""


class CaseAlreadyExistsError(StorageError):
    """Raised when pre-provisioning would replace an existing case."""


class RevisionConflictError(StorageError):
    """Raised when a child revision cannot advance the current head."""


class QualificationConflictError(StorageError):
    """Raised when a qualification attempt identity or state conflicts."""


class PromotionConflictError(StorageError):
    """Raised when a promotion receipt conflicts with stored state."""


class ObjectIntegrityError(IntegrityError):
    """Raised when an object cannot be verified by its content hash."""


@dataclass(frozen=True, slots=True)
class CaseRecord:
    case_id: str
    root_revision_id: str
    head_revision_id: str
    qualification_revision_id: str | None
    qualification_attempt_id: str | None
    qualification_result: str | None
    promoted_revision_id: str | None
    source_asset_sha256: str
    controller_sha256: str
    public_contract_sha256: str
    runner_sha256: str
    holdout_commitment_sha256: str
    created_at: str

    @property
    def qualification_state(self) -> str:
        return {
            None: "unused",
            "RUNNING": "running",
            "RECOVERING": "recovering",
            "PASSED": "passed",
            "FAILED": "failed",
        }[self.qualification_result]

    @property
    def promotion_state(self) -> str:
        return "promoted" if self.promoted_revision_id is not None else "open"


@dataclass(frozen=True, slots=True)
class RevisionRecord:
    case_id: str
    revision_id: str
    parent_revision_id: str | None
    ordinal: int
    asset_sha256: str
    patch_manifest_sha256: str | None = None
    hypothesis_event_id: str | None = None
    probe_run_id: str | None = None
    created_at: str | None = None


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: str
    case_id: str
    revision_id: str
    run_kind: str
    probe_kind: str | None
    condition_hash: str
    execution_fingerprint: str
    trace_sha256: str | None = None
    metrics_sha256: str | None = None
    passed: bool = False
    created_at: str | None = None


@dataclass(frozen=True, slots=True)
class LedgerEventRecord:
    event_id: str
    case_id: str
    revision_id: str | None
    event_type: str
    payload: Mapping[str, Any]
    artifact_refs: Sequence[Mapping[str, Any]] = ()
    request_id: str | None = None
    created_at: str | None = None


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    seq: int
    event_id: str
    request_id: str
    case_id: str
    revision_id: str | None
    event_type: str
    payload: Mapping[str, Any]
    artifact_refs: tuple[Mapping[str, Any], ...]
    prev_hash: str
    event_hash: str
    created_at: str


@dataclass(frozen=True, slots=True)
class QualificationAttempt:
    case_id: str
    attempt_id: str
    revision_id: str
    suite_commitment_sha256: str
    scenario_hashes: tuple[str, ...]
    state: str
    result: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PromotionReceipt:
    case_id: str
    revision_id: str
    ticket_id: str
    manifest_sha256: str
    receipt: Mapping[str, Any]


PublicationRecord = PromotionReceipt


@dataclass(frozen=True, slots=True)
class ObjectReference:
    sha256: str
    bytes: int


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValidationError("value is not canonical JSON") from exc


def _json_text(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _timestamp(value: str | datetime | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")
    if not isinstance(value, str) or not value:
        raise ValidationError("created_at must be a non-empty timestamp")
    return value


def _id(value: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or any(char in value for char in ("/", "\\", "\x00", "\n", "\r"))
    ):
        raise ValidationError(f"{field} is invalid")
    return value


def _sha256(value: str, field: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ValidationError(f"{field} must be lowercase hexadecimal SHA-256")
    return value


def _optional_sha256(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    return _sha256(value, field)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class ObjectStore:
    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        self.hash_root = self.root / "sha256"

    def _path(self, digest: str) -> Path:
        _sha256(digest, "sha256")
        return self.hash_root / digest[:2] / digest

    @staticmethod
    def _digest_file(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        try:
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
        except OSError as exc:
            raise ObjectIntegrityError("object cannot be read") from exc
        return digest.hexdigest(), size

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        try:
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise ObjectIntegrityError("object directory cannot be synchronized") from exc

    def _ensure_directory(self, path: Path) -> None:
        missing: list[Path] = []
        current = path
        while not current.exists():
            missing.append(current)
            current = current.parent
        if not current.is_dir():
            raise ObjectIntegrityError("object directory ancestor is not a directory")
        for directory in reversed(missing):
            directory.mkdir(exist_ok=True)
            self._fsync_directory(directory)
            self._fsync_directory(directory.parent)

    def put_bytes(self, data: bytes, *, expected_sha256: str | None = None) -> ObjectReference:
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        return self.put_stream(io.BytesIO(data), expected_sha256=expected_sha256)

    def put_stream(
        self,
        source: BinaryIO,
        *,
        expected_sha256: str | None = None,
    ) -> ObjectReference:
        if expected_sha256 is not None:
            _sha256(expected_sha256, "expected_sha256")
        temporary_path: Path | None = None
        digest = hashlib.sha256()
        size = 0
        try:
            self._ensure_directory(self.root.parent)
            self.root.mkdir(exist_ok=True)
            self.hash_root.mkdir(exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=self.hash_root, prefix=".tmp-", delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
                while chunk := source.read(1024 * 1024):
                    if not isinstance(chunk, bytes):
                        raise TypeError("source must provide bytes")
                    temporary.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                temporary.flush()
                os.fsync(temporary.fileno())
            actual = digest.hexdigest()
            if expected_sha256 is not None and actual != expected_sha256:
                raise ObjectIntegrityError("object hash does not match expected digest")
            destination = self._path(actual)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if not destination.is_file():
                    raise ObjectIntegrityError("canonical object path is not a file")
                stored, stored_size = self._digest_file(destination)
                if stored != actual:
                    raise ObjectIntegrityError("canonical object failed hash verification")
                temporary_path.unlink()
                temporary_path = None
                self._fsync_directory(destination.parent)
                self._fsync_directory(self.hash_root)
                self._fsync_directory(self.root)
                self._fsync_directory(self.root.parent)
                return ObjectReference(stored, stored_size)
            os.replace(temporary_path, destination)
            temporary_path = None
            self._fsync_directory(destination.parent)
            self._fsync_directory(self.hash_root)
            self._fsync_directory(self.root)
            self._fsync_directory(self.root.parent)
            return ObjectReference(actual, size)
        except OSError as exc:
            raise ObjectIntegrityError("object publication failed") from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass

    def path_for(self, sha256: str) -> Path:
        return self._path(sha256)

    def read_bytes(self, sha256: str) -> bytes:
        path = self._path(sha256)
        if not path.is_file():
            raise ObjectIntegrityError("canonical object is missing")
        try:
            with path.open("rb") as source:
                data = source.read()
        except OSError as exc:
            raise ObjectIntegrityError("canonical object cannot be read") from exc
        if hashlib.sha256(data).hexdigest() != sha256:
            raise ObjectIntegrityError("canonical object failed hash verification")
        return data


class EvidenceStore:
    def __init__(
        self,
        database_path: str | os.PathLike[str],
        object_root: str | os.PathLike[str] | None = None,
    ) -> None:
        self.database_path = str(database_path)
        self._memory = self.database_path == ":memory:"
        self._lock = threading.RLock()
        self._memory_connection: sqlite3.Connection | None = None
        if object_root is None:
            object_root = Path(database_path).parent / "objects"
        self.objects = ObjectStore(object_root)
        if self._memory:
            self._memory_connection = self._open_connection()
            self._create_schema(self._memory_connection)
        else:
            connection = self._open_connection()
            try:
                self._create_schema(connection)
            finally:
                connection.close()

    def _open_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        if not self._memory:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
        return connection

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS cases (
                case_id TEXT PRIMARY KEY,
                root_revision_id TEXT NOT NULL,
                head_revision_id TEXT NOT NULL,
                qualification_revision_id TEXT,
                qualification_attempt_id TEXT,
                qualification_result TEXT,
                promoted_revision_id TEXT,
                source_asset_sha256 TEXT NOT NULL,
                public_contract_sha256 TEXT NOT NULL,
                controller_sha256 TEXT NOT NULL,
                runner_sha256 TEXT NOT NULL,
                holdout_commitment_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS revisions (
                case_id TEXT NOT NULL,
                revision_id TEXT NOT NULL,
                parent_revision_id TEXT,
                ordinal INTEGER NOT NULL,
                asset_sha256 TEXT NOT NULL,
                patch_manifest_sha256 TEXT,
                hypothesis_event_id TEXT,
                probe_run_id TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (case_id, revision_id),
                UNIQUE (case_id, ordinal),
                FOREIGN KEY (case_id) REFERENCES cases(case_id)
            );

            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL,
                revision_id TEXT NOT NULL,
                run_kind TEXT NOT NULL,
                probe_kind TEXT,
                condition_hash TEXT NOT NULL,
                execution_fingerprint TEXT NOT NULL,
                trace_sha256 TEXT,
                metrics_sha256 TEXT,
                passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
                created_at TEXT NOT NULL,
                FOREIGN KEY (case_id, revision_id)
                    REFERENCES revisions(case_id, revision_id)
            );

            CREATE TABLE IF NOT EXISTS ledger_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                request_id TEXT NOT NULL,
                case_id TEXT NOT NULL,
                revision_id TEXT,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                artifact_refs_json TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (case_id) REFERENCES cases(case_id),
                FOREIGN KEY (case_id, revision_id)
                    REFERENCES revisions(case_id, revision_id)
            );
            """
        )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._memory_connection or self._open_connection()
            close = connection is not self._memory_connection
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                if close:
                    connection.close()

    @contextmanager
    def _read_connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._memory_connection or self._open_connection()
            close = connection is not self._memory_connection
            try:
                yield connection
            finally:
                if close:
                    connection.close()

    @contextmanager
    def _read_transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._memory_connection or self._open_connection()
            close = connection is not self._memory_connection
            try:
                connection.execute("BEGIN")
                yield connection
            finally:
                if connection.in_transaction:
                    connection.rollback()
                if close:
                    connection.close()

    @staticmethod
    def _case_from_row(row: sqlite3.Row) -> CaseRecord:
        values = dict(row)
        if values["qualification_result"] not in {None, "RUNNING", "RECOVERING", "PASSED", "FAILED"}:
            raise IntegrityError("qualification state is invalid")
        return CaseRecord(**values)

    @staticmethod
    def _revision_from_row(row: sqlite3.Row) -> RevisionRecord:
        return RevisionRecord(**dict(row))

    @staticmethod
    def _revision_event_payload(
        revision: RevisionRecord | sqlite3.Row,
    ) -> dict[str, Any]:
        if isinstance(revision, RevisionRecord):
            return {
                field: getattr(revision, field) for field in REVISION_EVENT_FIELDS
            }
        return {field: revision[field] for field in REVISION_EVENT_FIELDS}

    @staticmethod
    def _run_event_payload(run: RunRecord | sqlite3.Row) -> dict[str, Any]:
        if isinstance(run, RunRecord):
            return {field: getattr(run, field) for field in RUN_EVENT_FIELDS}
        payload = {field: run[field] for field in RUN_EVENT_FIELDS}
        payload["passed"] = bool(payload["passed"])
        return payload

    @staticmethod
    def _payload_contains(
        payload: Mapping[str, Any], required: Mapping[str, Any]
    ) -> bool:
        try:
            return all(
                field in payload
                and canonical_json_bytes(payload[field]) == canonical_json_bytes(value)
                for field, value in required.items()
            )
        except ValidationError:
            return False

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> RunRecord:
        values = dict(row)
        values["passed"] = bool(values["passed"])
        return RunRecord(**values)

    @staticmethod
    def _event_without_hash(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "event_id": row["event_id"],
            "request_id": row["request_id"],
            "case_id": row["case_id"],
            "revision_id": row["revision_id"],
            "event_type": row["event_type"],
            "payload_json": row["payload_json"],
            "artifact_refs_json": row["artifact_refs_json"],
            "created_at": row["created_at"],
        }

    @classmethod
    def _event_from_row(cls, row: sqlite3.Row) -> LedgerEvent:
        try:
            payload = json.loads(row["payload_json"])
            artifact_refs = json.loads(row["artifact_refs_json"])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise IntegrityError("ledger event JSON is invalid") from exc
        if not isinstance(payload, dict) or not isinstance(artifact_refs, list):
            raise IntegrityError("ledger event JSON shape is invalid")
        return LedgerEvent(
            seq=row["seq"],
            event_id=row["event_id"],
            request_id=row["request_id"],
            case_id=row["case_id"],
            revision_id=row["revision_id"],
            event_type=row["event_type"],
            payload=payload,
            artifact_refs=tuple(artifact_refs),
            prev_hash=row["prev_hash"],
            event_hash=row["event_hash"],
            created_at=row["created_at"],
        )

    @classmethod
    def _event_matches_record(
        cls,
        row: sqlite3.Row,
        event: LedgerEventRecord,
    ) -> bool:
        request_id, payload_json, artifact_refs_json = cls._validate_event_record(event)
        if event.request_id is None:
            request_id = row["request_id"]
        created_at = row["created_at"] if event.created_at is None else _timestamp(event.created_at)
        without_hash = {
            "event_id": event.event_id,
            "request_id": request_id,
            "case_id": event.case_id,
            "revision_id": event.revision_id,
            "event_type": event.event_type,
            "payload_json": payload_json,
            "artifact_refs_json": artifact_refs_json,
            "created_at": created_at,
        }
        try:
            _sha256(row["prev_hash"], "prev_hash")
            expected_hash = hashlib.sha256(
                bytes.fromhex(row["prev_hash"]) + canonical_json_bytes(without_hash)
            ).hexdigest()
        except (TypeError, ValueError, ValidationError) as exc:
            raise IntegrityError("stored ledger event hash inputs are invalid") from exc
        return (
            all(
                row[field] == value
                for field, value in without_hash.items()
            )
            and row["event_hash"] == expected_hash
        )

    @staticmethod
    def _validate_artifact_reference(reference: Mapping[str, Any]) -> None:
        try:
            sha256 = reference["sha256"]
            kind = reference["kind"]
            size = reference["size"]
            media_type = reference["media_type"]
        except KeyError as exc:
            raise ValidationError("artifact reference is incomplete") from exc
        _sha256(sha256, "artifact reference sha256")
        _id(kind, "artifact reference kind")
        if (
            not isinstance(media_type, str)
            or not media_type
            or media_type.strip() != media_type
            or any(char in media_type for char in ("\x00", "\n", "\r"))
        ):
            raise ValidationError("artifact reference media_type is invalid")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValidationError("artifact reference size must be nonnegative")

    @staticmethod
    def _validate_event_record(event: LedgerEventRecord) -> tuple[str, str, str]:
        _id(event.event_id, "event_id")
        _id(event.case_id, "case_id")
        if event.revision_id is not None:
            _id(event.revision_id, "revision_id")
        _id(event.event_type, "event_type")
        if not isinstance(event.payload, Mapping):
            raise ValidationError("payload must be an object")
        if not isinstance(event.artifact_refs, Sequence) or isinstance(
            event.artifact_refs, (str, bytes)
        ):
            raise ValidationError("artifact_refs must be a sequence")
        if any(not isinstance(reference, Mapping) for reference in event.artifact_refs):
            raise ValidationError("artifact_refs must contain objects")
        for reference in event.artifact_refs:
            EvidenceStore._validate_artifact_reference(reference)
        payload_json = _json_text(dict(event.payload))
        artifact_refs_json = _json_text(list(event.artifact_refs))
        request_id = event.request_id or _new_id("req")
        _id(request_id, "request_id")
        return request_id, payload_json, artifact_refs_json

    @classmethod
    def _append_event(
        cls,
        connection: sqlite3.Connection,
        event: LedgerEventRecord,
    ) -> LedgerEvent:
        request_id, payload_json, artifact_refs_json = cls._validate_event_record(event)
        created_at = _timestamp(event.created_at)
        previous = connection.execute(
            "SELECT event_hash FROM ledger_events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        prev_hash = previous["event_hash"] if previous else ZERO_HASH
        _sha256(prev_hash, "prev_hash")
        without_hash = {
            "event_id": event.event_id,
            "request_id": request_id,
            "case_id": event.case_id,
            "revision_id": event.revision_id,
            "event_type": event.event_type,
            "payload_json": payload_json,
            "artifact_refs_json": artifact_refs_json,
            "created_at": created_at,
        }
        event_hash = hashlib.sha256(
            bytes.fromhex(prev_hash) + canonical_json_bytes(without_hash)
        ).hexdigest()
        try:
            cursor = connection.execute(
                """
                INSERT INTO ledger_events (
                    event_id, request_id, case_id, revision_id, event_type,
                    payload_json, artifact_refs_json, prev_hash, event_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    request_id,
                    event.case_id,
                    event.revision_id,
                    event.event_type,
                    payload_json,
                    artifact_refs_json,
                    prev_hash,
                    event_hash,
                    created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise IntegrityError("ledger event identity already exists") from exc
        row = connection.execute(
            "SELECT * FROM ledger_events WHERE seq = ?", (cursor.lastrowid,)
        ).fetchone()
        assert row is not None
        return cls._event_from_row(row)

    def create_preprovisioned_case(
        self,
        *,
        case_id: str,
        root_revision_id: str,
        source_asset_sha256: str,
        controller_sha256: str,
        public_contract_sha256: str,
        runner_sha256: str,
        holdout_commitment_sha256: str,
    ) -> CaseRecord:
        _id(case_id, "case_id")
        _id(root_revision_id, "root_revision_id")
        commitments = self._commitment_payload(
            {
                "source_asset_sha256": source_asset_sha256,
                "controller_sha256": controller_sha256,
                "public_contract_sha256": public_contract_sha256,
                "runner_sha256": runner_sha256,
                "holdout_commitment_sha256": holdout_commitment_sha256,
            }
        )
        timestamp = _timestamp(None)
        with self._transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone():
                raise CaseAlreadyExistsError("case identity already exists")
            try:
                connection.execute(
                    """
                    INSERT INTO cases (
                        case_id, root_revision_id, head_revision_id,
                        qualification_revision_id, qualification_attempt_id,
                        qualification_result, promoted_revision_id,
                        source_asset_sha256, public_contract_sha256,
                        controller_sha256, runner_sha256,
                        holdout_commitment_sha256, created_at
                    ) VALUES (?, ?, ?, NULL, NULL, NULL, NULL, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        case_id,
                        root_revision_id,
                        root_revision_id,
                        source_asset_sha256,
                        public_contract_sha256,
                        controller_sha256,
                        runner_sha256,
                        holdout_commitment_sha256,
                        timestamp,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO revisions (
                        case_id, revision_id, parent_revision_id, ordinal,
                        asset_sha256, patch_manifest_sha256,
                        hypothesis_event_id, probe_run_id, created_at
                    ) VALUES (?, ?, NULL, 0, ?, NULL, NULL, NULL, ?)
                    """,
                    (case_id, root_revision_id, source_asset_sha256, timestamp),
                )
                self._append_event(
                    connection,
                    LedgerEventRecord(
                        event_id=_new_id("evt"),
                        case_id=case_id,
                        revision_id=root_revision_id,
                        event_type="CASE_CREATED",
                        payload={
                            "root_revision_id": root_revision_id,
                            **commitments,
                        },
                        request_id=_new_id("req"),
                        created_at=timestamp,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise CaseAlreadyExistsError("case identity already exists") from exc
            row = connection.execute(
                "SELECT * FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
            assert row is not None
            return self._case_from_row(row)

    def get_case(self, case_id: str) -> CaseRecord:
        _id(case_id, "case_id")
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
        if row is None:
            raise CaseNotFoundError("case was not found")
        return self._case_from_row(row)

    def restore_state(self, case_id: str) -> CaseRecord:
        _id(case_id, "case_id")
        with self._read_transaction() as connection:
            events = self._verified_ledger_from_connection(connection)
            row = connection.execute(
                "SELECT * FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
            if row is None:
                raise CaseNotFoundError("case was not found")
            case = self._case_from_row(row)
            case_commitments = self._stored_commitment_payload(row)
            root_revision = connection.execute(
                """
                SELECT * FROM revisions
                WHERE case_id = ? AND revision_id = ?
                """,
                (case_id, case.root_revision_id),
            ).fetchone()
            revision_rows = connection.execute(
                "SELECT * FROM revisions WHERE case_id = ?",
                (case_id,),
            ).fetchall()
            run_rows = connection.execute(
                "SELECT * FROM runs WHERE case_id = ?",
                (case_id,),
            ).fetchall()
        case_events = tuple(event for event in events if event.case_id == case_id)
        events_by_id = {event.event_id: event for event in case_events}
        revisions_by_id = {row["revision_id"]: row for row in revision_rows}
        runs_by_id = {row["run_id"]: row for row in run_rows}
        created = tuple(event for event in case_events if event.event_type == "CASE_CREATED")
        if len(created) != 1 or created[0].payload.get("root_revision_id") != case.root_revision_id:
            raise IntegrityError("case creation state does not match the ledger")
        try:
            created_commitments = self._commitment_payload(created[0].payload)
        except ValidationError as exc:
            raise IntegrityError("case creation commitments are invalid") from exc
        if created_commitments != case_commitments:
            raise IntegrityError("case commitments do not match the ledger")
        if (
            root_revision is None
            or root_revision["parent_revision_id"] is not None
            or root_revision["ordinal"] != 0
            or root_revision["patch_manifest_sha256"] is not None
            or root_revision["hypothesis_event_id"] is not None
            or root_revision["probe_run_id"] is not None
        ):
            raise IntegrityError("root revision state is invalid")

        head_revision_id = case.root_revision_id
        head_ordinal = 0
        replayed_revision_ids = {case.root_revision_id}
        qualification_revision_id: str | None = None
        qualification_attempt_id: str | None = None
        qualification_result: str | None = None
        promoted_revision_id: str | None = None
        qualification_identity: tuple[str, str] | None = None
        for event in case_events:
            if event.event_type == "REVISION_CREATED":
                if event.revision_id is None:
                    raise IntegrityError("revision event identity is invalid")
                revision = revisions_by_id.get(event.revision_id)
                if (
                    revision is None
                    or revision["parent_revision_id"] != head_revision_id
                    or revision["ordinal"] != head_ordinal + 1
                ):
                    raise IntegrityError("revision event state is not linear")
                required_payload = self._revision_event_payload(revision)
                if not self._payload_contains(event.payload, required_payload):
                    raise IntegrityError("revision row does not match its ledger event")
                hypothesis = events_by_id.get(revision["hypothesis_event_id"])
                probe = runs_by_id.get(revision["probe_run_id"])
                if (
                    hypothesis is None
                    or hypothesis.event_type != "HYPOTHESIS_RECORDED"
                    or hypothesis.revision_id != head_revision_id
                    or probe is None
                    or probe["revision_id"] != head_revision_id
                    or probe["run_kind"] != "probe"
                ):
                    raise IntegrityError("revision causal citations are invalid")
                if not self._payload_contains(
                    event.payload,
                    {"probe_run": self._run_event_payload(probe)},
                ):
                    raise IntegrityError("probe run does not match its ledger event")
                head_revision_id = event.revision_id
                head_ordinal = revision["ordinal"]
                replayed_revision_ids.add(event.revision_id)
            elif event.event_type == "QUALIFICATION_RESERVED":
                attempt = self._attempt_from_payload(
                    case_id, "RUNNING", event.payload, commitments=case_commitments
                )
                qualification_revision_id = attempt.revision_id
                qualification_attempt_id = attempt.attempt_id
                qualification_result = "RUNNING"
                qualification_identity = (attempt.revision_id, attempt.attempt_id)
            elif event.event_type in {
                "QUALIFICATION_RECOVERING",
                "QUALIFICATION_RECOVERED",
                "QUALIFICATION_PASSED",
                "QUALIFICATION_FAILED",
            }:
                state = {
                    "QUALIFICATION_RECOVERING": "RECOVERING",
                    "QUALIFICATION_RECOVERED": "RUNNING",
                    "QUALIFICATION_PASSED": "PASSED",
                    "QUALIFICATION_FAILED": "FAILED",
                }[event.event_type]
                attempt = self._attempt_from_payload(
                    case_id, state, event.payload, commitments=case_commitments
                )
                if qualification_identity != (attempt.revision_id, attempt.attempt_id):
                    raise IntegrityError("qualification event identity is invalid")
                qualification_result = state
            elif event.event_type == "PROMOTED":
                if event.revision_id is None or event.payload.get("revision_id") != event.revision_id:
                    raise IntegrityError("promotion event identity is invalid")
                promoted_revision_id = event.revision_id

        if replayed_revision_ids != set(revisions_by_id):
            raise IntegrityError("revision rows do not match the ledger replay")

        if (
            case.head_revision_id != head_revision_id
            or root_revision["asset_sha256"] != case.source_asset_sha256
            or case.qualification_revision_id != qualification_revision_id
            or case.qualification_attempt_id != qualification_attempt_id
            or case.qualification_result != qualification_result
            or case.promoted_revision_id != promoted_revision_id
        ):
            raise IntegrityError("materialized case state does not match the ledger")
        return case

    restore_case_state = restore_state

    def get_revision(self, case_id: str, revision_id: str) -> RevisionRecord:
        _id(case_id, "case_id")
        _id(revision_id, "revision_id")
        with self._read_connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM revisions
                WHERE case_id = ? AND revision_id = ?
                """,
                (case_id, revision_id),
            ).fetchone()
        if row is None:
            raise StorageError("revision was not found")
        return self._revision_from_row(row)

    def list_revisions(self, case_id: str) -> tuple[RevisionRecord, ...]:
        _id(case_id, "case_id")
        with self._read_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM revisions WHERE case_id = ? ORDER BY ordinal", (case_id,)
            ).fetchall()
        return tuple(self._revision_from_row(row) for row in rows)

    def get_run(self, run_id: str) -> RunRecord:
        _id(run_id, "run_id")
        with self._read_connection() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise StorageError("run was not found")
        return self._run_from_row(row)

    def append_event(self, event: LedgerEventRecord) -> LedgerEvent:
        self._validate_event_record(event)
        self._validate_generic_event_type(event.event_type)
        with self._transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM cases WHERE case_id = ?", (event.case_id,)
            ).fetchone() is None:
                raise CaseNotFoundError("case was not found")
            if event.revision_id is not None and connection.execute(
                """
                SELECT 1 FROM revisions WHERE case_id = ? AND revision_id = ?
                """,
                (event.case_id, event.revision_id),
            ).fetchone() is None:
                raise StorageError("revision was not found")
            return self._append_event(connection, event)

    append_ledger_event = append_event

    @staticmethod
    def _validate_generic_event_type(event_type: str) -> None:
        if event_type in TRANSACTIONAL_EVENT_TYPES:
            raise ValidationError("event type requires its dedicated transaction API")

    @staticmethod
    def _validate_revision(revision: RevisionRecord) -> str:
        _id(revision.case_id, "case_id")
        _id(revision.revision_id, "revision_id")
        _sha256(revision.asset_sha256, "asset_sha256")
        if not isinstance(revision.ordinal, int) or isinstance(revision.ordinal, bool):
            raise ValidationError("ordinal must be an integer")
        _optional_sha256(revision.patch_manifest_sha256, "patch_manifest_sha256")
        if revision.parent_revision_id is None:
            if revision.ordinal != 0:
                raise ValidationError("root revision ordinal must be zero")
            if revision.patch_manifest_sha256 is not None:
                raise ValidationError("root revision cannot have a patch manifest")
            if revision.hypothesis_event_id is not None or revision.probe_run_id is not None:
                raise ValidationError("root revision citations must be null")
        else:
            _id(revision.parent_revision_id, "parent_revision_id")
            if revision.ordinal <= 0:
                raise ValidationError("child revision ordinal must be positive")
            if revision.patch_manifest_sha256 is None:
                raise ValidationError("child revision requires a patch manifest")
            if revision.hypothesis_event_id is None or revision.probe_run_id is None:
                raise ValidationError("child revision requires hypothesis and probe citations")
            _id(revision.hypothesis_event_id, "hypothesis_event_id")
            _id(revision.probe_run_id, "probe_run_id")
        return _timestamp(revision.created_at)

    def _revision_matches(self, row: sqlite3.Row, revision: RevisionRecord) -> bool:
        values = dict(row)
        return (
            all(
                values[field] == getattr(revision, field)
                for field in (
                    "case_id",
                    "revision_id",
                    "parent_revision_id",
                    "ordinal",
                    "asset_sha256",
                    "patch_manifest_sha256",
                    "hypothesis_event_id",
                )
            )
            and values["probe_run_id"] == revision.probe_run_id
            and (
                revision.created_at is None
                or values["created_at"] == _timestamp(revision.created_at)
            )
        )

    def commit_revision_with_event(
        self,
        *,
        revision: RevisionRecord,
        event: LedgerEventRecord,
        expected_head_revision_id: str,
    ) -> RevisionRecord:
        created_at = self._validate_revision(revision)
        self._validate_event_record(event)
        if revision.parent_revision_id is None:
            raise ValidationError("commit_revision_with_event requires a child revision")
        if event.case_id != revision.case_id or event.revision_id != revision.revision_id:
            raise ValidationError("revision and event identities do not match")
        if event.event_type != "REVISION_CREATED":
            raise ValidationError("revision transaction requires REVISION_CREATED")
        required_payload = self._revision_event_payload(revision)
        if not self._payload_contains(event.payload, required_payload):
            raise ValidationError("revision event does not bind the revision")
        if not isinstance(event.payload.get("probe_run"), Mapping):
            raise ValidationError("revision event does not bind the probe run")
        _id(expected_head_revision_id, "expected_head_revision_id")
        with self._transaction() as connection:
            case = connection.execute(
                "SELECT * FROM cases WHERE case_id = ?", (revision.case_id,)
            ).fetchone()
            if case is None:
                raise CaseNotFoundError("case was not found")
            existing = connection.execute(
                """
                SELECT * FROM revisions
                WHERE case_id = ? AND revision_id = ?
                """,
                (revision.case_id, revision.revision_id),
            ).fetchone()
            if existing is not None:
                event_row = connection.execute(
                    "SELECT * FROM ledger_events WHERE event_id = ?", (event.event_id,)
                ).fetchone()
                if (
                    event_row is not None
                    and event_row["case_id"] == revision.case_id
                    and event_row["revision_id"] == revision.revision_id
                    and self._revision_matches(existing, revision)
                ):
                    if self._event_matches_record(event_row, event):
                        return self._revision_from_row(existing)
                    raise IntegrityError("revision retry event does not match")
                raise RevisionConflictError("revision identity already exists")
            if case["qualification_result"] is not None or case["promoted_revision_id"] is not None:
                raise RevisionConflictError("case head is sealed")
            if case["head_revision_id"] != expected_head_revision_id:
                raise RevisionConflictError("expected head is stale")
            if revision.parent_revision_id != expected_head_revision_id:
                raise RevisionConflictError("revision parent is not the current head")
            parent = connection.execute(
                """
                SELECT * FROM revisions WHERE case_id = ? AND revision_id = ?
                """,
                (revision.case_id, revision.parent_revision_id),
            ).fetchone()
            if parent is None:
                raise StorageError("revision parent was not found")
            if revision.ordinal != parent["ordinal"] + 1:
                raise RevisionConflictError("revision ordinal is not linear")
            hypothesis = connection.execute(
                "SELECT * FROM ledger_events WHERE event_id = ?",
                (revision.hypothesis_event_id,),
            ).fetchone()
            if (
                hypothesis is None
                or hypothesis["case_id"] != revision.case_id
                or hypothesis["revision_id"] != revision.parent_revision_id
                or hypothesis["event_type"] != "HYPOTHESIS_RECORDED"
            ):
                raise RevisionConflictError("hypothesis citation is invalid")
            probe = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (revision.probe_run_id,)
            ).fetchone()
            if (
                probe is None
                or probe["case_id"] != revision.case_id
                or probe["revision_id"] != revision.parent_revision_id
                or probe["run_kind"] != "probe"
                or probe["passed"] is None
            ):
                raise RevisionConflictError("probe citation is invalid")
            if not self._payload_contains(
                event.payload,
                {"probe_run": self._run_event_payload(probe)},
            ):
                raise ValidationError("revision event does not bind the probe run")
            try:
                connection.execute(
                    """
                    INSERT INTO revisions (
                        case_id, revision_id, parent_revision_id, ordinal,
                        asset_sha256, patch_manifest_sha256,
                        hypothesis_event_id, probe_run_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        revision.case_id,
                        revision.revision_id,
                        revision.parent_revision_id,
                        revision.ordinal,
                        revision.asset_sha256,
                        revision.patch_manifest_sha256,
                        revision.hypothesis_event_id,
                        revision.probe_run_id,
                        created_at,
                    ),
                )
                self._append_event(connection, event)
                connection.execute(
                    "UPDATE cases SET head_revision_id = ? WHERE case_id = ?",
                    (revision.revision_id, revision.case_id),
                )
            except sqlite3.IntegrityError as exc:
                raise RevisionConflictError("revision transaction conflicted") from exc
            row = connection.execute(
                """
                SELECT * FROM revisions WHERE case_id = ? AND revision_id = ?
                """,
                (revision.case_id, revision.revision_id),
            ).fetchone()
            assert row is not None
            return self._revision_from_row(row)

    commit_revision_and_event = commit_revision_with_event

    @staticmethod
    def _validate_run(run: RunRecord) -> str:
        for field, value in (
            ("run_id", run.run_id),
            ("case_id", run.case_id),
            ("revision_id", run.revision_id),
            ("run_kind", run.run_kind),
            ("condition_hash", run.condition_hash),
            ("execution_fingerprint", run.execution_fingerprint),
        ):
            _id(value, field)
        if run.probe_kind is not None:
            _id(run.probe_kind, "probe_kind")
        _optional_sha256(run.trace_sha256, "trace_sha256")
        _optional_sha256(run.metrics_sha256, "metrics_sha256")
        if not isinstance(run.passed, bool):
            raise ValidationError("passed must be a boolean")
        return _timestamp(run.created_at)

    def record_run(
        self,
        *,
        run: RunRecord,
        event: LedgerEventRecord | None = None,
    ) -> RunRecord:
        created_at = self._validate_run(run)
        if event is not None:
            self._validate_event_record(event)
            if event.case_id != run.case_id or event.revision_id != run.revision_id:
                raise ValidationError("run and event identities do not match")
            self._validate_generic_event_type(event.event_type)
        with self._transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM runs WHERE run_id = ?", (run.run_id,)
            ).fetchone() is not None:
                raise StorageError("run identity already exists")
            if connection.execute(
                """
                SELECT 1 FROM revisions WHERE case_id = ? AND revision_id = ?
                """,
                (run.case_id, run.revision_id),
            ).fetchone() is None:
                raise StorageError("revision was not found")
            try:
                connection.execute(
                    """
                    INSERT INTO runs (
                        run_id, case_id, revision_id, run_kind, probe_kind,
                        condition_hash, execution_fingerprint, trace_sha256,
                        metrics_sha256, passed, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.run_id,
                        run.case_id,
                        run.revision_id,
                        run.run_kind,
                        run.probe_kind,
                        run.condition_hash,
                        run.execution_fingerprint,
                        run.trace_sha256,
                        run.metrics_sha256,
                        int(run.passed),
                        created_at,
                    ),
                )
                if event is not None:
                    self._append_event(connection, event)
            except sqlite3.IntegrityError as exc:
                raise StorageError("run transaction conflicted") from exc
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run.run_id,)).fetchone()
            assert row is not None
            return self._run_from_row(row)

    @staticmethod
    def _validate_qualification_identity(
        *,
        case_id: str,
        attempt_id: str,
        revision_id: str,
        suite_commitment_sha256: str,
        scenario_hashes: Sequence[str],
    ) -> tuple[str, ...]:
        _id(case_id, "case_id")
        _id(attempt_id, "attempt_id")
        _id(revision_id, "revision_id")
        _sha256(suite_commitment_sha256, "suite_commitment_sha256")
        if isinstance(scenario_hashes, (str, bytes)) or not isinstance(
            scenario_hashes, Sequence
        ):
            raise ValidationError("scenario_hashes must be a sequence")
        normalized = tuple(_sha256(value, "scenario_hash") for value in scenario_hashes)
        if not normalized:
            raise ValidationError("scenario_hashes cannot be empty")
        return normalized

    @staticmethod
    def _commitment_payload(
        values: Mapping[str, Any] | sqlite3.Row,
    ) -> dict[str, str]:
        try:
            return {
                field: _sha256(values[field], field) for field in COMMITMENT_FIELDS
            }
        except (KeyError, IndexError) as exc:
            raise ValidationError("qualification commitments are incomplete") from exc

    @classmethod
    def _stored_commitment_payload(
        cls, values: Mapping[str, Any] | sqlite3.Row
    ) -> dict[str, str]:
        try:
            return cls._commitment_payload(values)
        except ValidationError as exc:
            raise IntegrityError("stored case commitments are invalid") from exc

    @staticmethod
    def _identity_payload(
        *,
        attempt_id: str,
        revision_id: str,
        suite_commitment_sha256: str,
        scenario_hashes: Sequence[str],
        commitments: Mapping[str, str],
    ) -> dict[str, Any]:
        return {
            "attempt_id": attempt_id,
            "revision_id": revision_id,
            "suite_commitment_sha256": suite_commitment_sha256,
            "scenario_hashes": list(scenario_hashes),
            **commitments,
        }

    @staticmethod
    def _attempt_from_payload(
        case_id: str,
        state: str,
        payload: Mapping[str, Any],
        result: Mapping[str, Any] | None = None,
        *,
        commitments: Mapping[str, str],
    ) -> QualificationAttempt:
        try:
            attempt_id = payload["attempt_id"]
            revision_id = payload["revision_id"]
            suite = payload["suite_commitment_sha256"]
            scenarios = payload["scenario_hashes"]
        except (KeyError, TypeError) as exc:
            raise IntegrityError("qualification identity is incomplete") from exc
        normalized = EvidenceStore._validate_qualification_identity(
            case_id=case_id,
            attempt_id=attempt_id,
            revision_id=revision_id,
            suite_commitment_sha256=suite,
            scenario_hashes=scenarios,
        )
        try:
            payload_commitments = EvidenceStore._commitment_payload(payload)
        except ValidationError as exc:
            raise IntegrityError("qualification commitments are invalid") from exc
        if payload_commitments != dict(commitments):
            raise IntegrityError("qualification commitments differ from the case")
        return QualificationAttempt(
            case_id=case_id,
            attempt_id=attempt_id,
            revision_id=revision_id,
            suite_commitment_sha256=suite,
            scenario_hashes=normalized,
            state=state,
            result=result,
        )

    @classmethod
    def _identity_matches(
        cls,
        attempt: QualificationAttempt,
        *,
        case_id: str,
        attempt_id: str,
        revision_id: str,
        suite_commitment_sha256: str,
        scenario_hashes: Sequence[str],
    ) -> bool:
        normalized = cls._validate_qualification_identity(
            case_id=case_id,
            attempt_id=attempt_id,
            revision_id=revision_id,
            suite_commitment_sha256=suite_commitment_sha256,
            scenario_hashes=scenario_hashes,
        )
        return (
            attempt.case_id == case_id
            and attempt.attempt_id == attempt_id
            and attempt.revision_id == revision_id
            and attempt.suite_commitment_sha256 == suite_commitment_sha256
            and attempt.scenario_hashes == normalized
        )

    @classmethod
    def _latest_attempt_from_connection(
        cls,
        connection: sqlite3.Connection,
        case_id: str,
        state: str,
        commitments: Mapping[str, str],
    ) -> QualificationAttempt:
        row = connection.execute(
            """
            SELECT * FROM ledger_events
            WHERE case_id = ? AND event_type = 'QUALIFICATION_RESERVED'
            ORDER BY seq DESC LIMIT 1
            """,
            (case_id,),
        ).fetchone()
        if row is None:
            raise IntegrityError("qualification reservation is missing")
        event = cls._event_from_row(row)
        return cls._attempt_from_payload(
            case_id, state, event.payload, commitments=commitments
        )

    @classmethod
    def _require_attempt(
        cls,
        connection: sqlite3.Connection,
        *,
        case_id: str,
        attempt_id: str,
        revision_id: str,
        suite_commitment_sha256: str,
        scenario_hashes: Sequence[str],
        state: str,
        commitments: Mapping[str, str],
    ) -> QualificationAttempt:
        attempt = cls._latest_attempt_from_connection(
            connection, case_id, state, commitments
        )
        if not cls._identity_matches(
            attempt,
            case_id=case_id,
            attempt_id=attempt_id,
            revision_id=revision_id,
            suite_commitment_sha256=suite_commitment_sha256,
            scenario_hashes=scenario_hashes,
        ):
            raise QualificationConflictError("qualification attempt identity differs")
        return attempt

    def reserve_qualification(
        self,
        *,
        case_id: str,
        revision_id: str,
        attempt_id: str,
        suite_commitment_sha256: str,
        scenario_hashes: Sequence[str],
        expected_head_revision_id: str,
        source_asset_sha256: str,
        controller_sha256: str,
        public_contract_sha256: str,
        runner_sha256: str,
        holdout_commitment_sha256: str,
    ) -> QualificationAttempt:
        normalized = self._validate_qualification_identity(
            case_id=case_id,
            attempt_id=attempt_id,
            revision_id=revision_id,
            suite_commitment_sha256=suite_commitment_sha256,
            scenario_hashes=scenario_hashes,
        )
        supplied_commitments = self._commitment_payload(
            {
                "source_asset_sha256": source_asset_sha256,
                "controller_sha256": controller_sha256,
                "public_contract_sha256": public_contract_sha256,
                "runner_sha256": runner_sha256,
                "holdout_commitment_sha256": holdout_commitment_sha256,
            }
        )
        _id(expected_head_revision_id, "expected_head_revision_id")
        with self._transaction() as connection:
            case = connection.execute(
                "SELECT * FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
            if case is None:
                raise CaseNotFoundError("case was not found")
            case_commitments = self._stored_commitment_payload(case)
            if supplied_commitments != case_commitments:
                raise QualificationConflictError(
                    "qualification commitments differ from the case"
                )
            if case["qualification_result"] is not None:
                if case["qualification_result"] == "RUNNING":
                    existing = self._latest_attempt_from_connection(
                        connection, case_id, "RUNNING", case_commitments
                    )
                    if self._identity_matches(
                        existing,
                        case_id=case_id,
                        attempt_id=attempt_id,
                        revision_id=revision_id,
                        suite_commitment_sha256=suite_commitment_sha256,
                        scenario_hashes=normalized,
                    ):
                        return existing
                raise QualificationConflictError("qualification attempt is already used")
            if case["head_revision_id"] != expected_head_revision_id or revision_id != expected_head_revision_id:
                raise QualificationConflictError("qualification revision is not the current head")
            payload = self._identity_payload(
                attempt_id=attempt_id,
                revision_id=revision_id,
                suite_commitment_sha256=suite_commitment_sha256,
                scenario_hashes=normalized,
                commitments=case_commitments,
            )
            timestamp = _timestamp(None)
            try:
                connection.execute(
                    """
                    UPDATE cases
                    SET qualification_revision_id = ?,
                        qualification_attempt_id = ?,
                        qualification_result = 'RUNNING'
                    WHERE case_id = ?
                    """,
                    (revision_id, attempt_id, case_id),
                )
                self._append_event(
                    connection,
                    LedgerEventRecord(
                        event_id=_new_id("evt"),
                        case_id=case_id,
                        revision_id=revision_id,
                        event_type="QUALIFICATION_RESERVED",
                        payload=payload,
                        request_id=_new_id("req"),
                        created_at=timestamp,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise QualificationConflictError("qualification reservation conflicted") from exc
            return QualificationAttempt(
                case_id=case_id,
                attempt_id=attempt_id,
                revision_id=revision_id,
                suite_commitment_sha256=suite_commitment_sha256,
                scenario_hashes=normalized,
                state="RUNNING",
            )

    def mark_qualification_recovering(
        self,
        *,
        case_id: str,
        attempt_id: str,
        revision_id: str,
        suite_commitment_sha256: str,
        scenario_hashes: Sequence[str],
    ) -> QualificationAttempt:
        self._validate_qualification_identity(
            case_id=case_id,
            attempt_id=attempt_id,
            revision_id=revision_id,
            suite_commitment_sha256=suite_commitment_sha256,
            scenario_hashes=scenario_hashes,
        )
        with self._transaction() as connection:
            case = connection.execute(
                "SELECT * FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
            if case is None:
                raise CaseNotFoundError("case was not found")
            case_commitments = self._stored_commitment_payload(case)
            if case["qualification_result"] != "RUNNING":
                raise QualificationConflictError("qualification is not running")
            self._require_attempt(
                connection,
                case_id=case_id,
                attempt_id=attempt_id,
                revision_id=revision_id,
                suite_commitment_sha256=suite_commitment_sha256,
                scenario_hashes=scenario_hashes,
                state="RUNNING",
                commitments=case_commitments,
            )
            connection.execute(
                "UPDATE cases SET qualification_result = 'RECOVERING' WHERE case_id = ?",
                (case_id,),
            )
            self._append_event(
                connection,
                LedgerEventRecord(
                    event_id=_new_id("evt"),
                    case_id=case_id,
                    revision_id=revision_id,
                    event_type="QUALIFICATION_RECOVERING",
                    payload=self._identity_payload(
                        attempt_id=attempt_id,
                        revision_id=revision_id,
                        suite_commitment_sha256=suite_commitment_sha256,
                        scenario_hashes=scenario_hashes,
                        commitments=case_commitments,
                    ),
                    request_id=_new_id("req"),
                ),
            )
            return QualificationAttempt(
                case_id=case_id,
                attempt_id=attempt_id,
                revision_id=revision_id,
                suite_commitment_sha256=suite_commitment_sha256,
                scenario_hashes=tuple(scenario_hashes),
                state="RECOVERING",
            )

    def recover_qualification(
        self,
        *,
        case_id: str,
        attempt_id: str,
        revision_id: str,
        suite_commitment_sha256: str,
        scenario_hashes: Sequence[str],
    ) -> QualificationAttempt:
        normalized = self._validate_qualification_identity(
            case_id=case_id,
            attempt_id=attempt_id,
            revision_id=revision_id,
            suite_commitment_sha256=suite_commitment_sha256,
            scenario_hashes=scenario_hashes,
        )
        with self._transaction() as connection:
            case = connection.execute(
                "SELECT * FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
            if case is None:
                raise CaseNotFoundError("case was not found")
            case_commitments = self._stored_commitment_payload(case)
            if case["qualification_result"] != "RECOVERING":
                raise QualificationConflictError("qualification is not recovering")
            self._require_attempt(
                connection,
                case_id=case_id,
                attempt_id=attempt_id,
                revision_id=revision_id,
                suite_commitment_sha256=suite_commitment_sha256,
                scenario_hashes=normalized,
                state="RECOVERING",
                commitments=case_commitments,
            )
            connection.execute(
                "UPDATE cases SET qualification_result = 'RUNNING' WHERE case_id = ?",
                (case_id,),
            )
            self._append_event(
                connection,
                LedgerEventRecord(
                    event_id=_new_id("evt"),
                    case_id=case_id,
                    revision_id=revision_id,
                    event_type="QUALIFICATION_RECOVERED",
                    payload=self._identity_payload(
                        attempt_id=attempt_id,
                        revision_id=revision_id,
                        suite_commitment_sha256=suite_commitment_sha256,
                        scenario_hashes=normalized,
                        commitments=case_commitments,
                    ),
                    request_id=_new_id("req"),
                ),
            )
            return QualificationAttempt(
                case_id=case_id,
                attempt_id=attempt_id,
                revision_id=revision_id,
                suite_commitment_sha256=suite_commitment_sha256,
                scenario_hashes=normalized,
                state="RUNNING",
            )

    def record_qualification_terminal(
        self,
        *,
        case_id: str,
        attempt_id: str,
        revision_id: str,
        suite_commitment_sha256: str,
        scenario_hashes: Sequence[str],
        state: str,
        result: Mapping[str, Any] | None = None,
    ) -> QualificationAttempt:
        normalized = self._validate_qualification_identity(
            case_id=case_id,
            attempt_id=attempt_id,
            revision_id=revision_id,
            suite_commitment_sha256=suite_commitment_sha256,
            scenario_hashes=scenario_hashes,
        )
        if state not in {"PASSED", "FAILED"}:
            raise ValidationError("terminal qualification state is invalid")
        if result is not None and not isinstance(result, Mapping):
            raise ValidationError("qualification result must be an object")
        result_payload = (
            json.loads(_json_text(dict(result))) if result is not None else None
        )
        with self._transaction() as connection:
            case = connection.execute(
                "SELECT * FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
            if case is None:
                raise CaseNotFoundError("case was not found")
            case_commitments = self._stored_commitment_payload(case)
            if case["qualification_result"] in {"PASSED", "FAILED"}:
                self._verified_ledger_from_connection(connection)
                existing = self._latest_attempt_from_connection(
                    connection,
                    case_id,
                    case["qualification_result"],
                    case_commitments,
                )
                terminal_row = connection.execute(
                    """
                    SELECT * FROM ledger_events
                    WHERE case_id = ? AND event_type = ?
                    ORDER BY seq DESC LIMIT 1
                    """,
                    (case_id, f"QUALIFICATION_{case['qualification_result']}"),
                ).fetchone()
                stored_result = None
                if terminal_row is None:
                    raise IntegrityError("qualification terminal event is missing")
                terminal_event = self._event_from_row(terminal_row)
                terminal_attempt = self._attempt_from_payload(
                    case_id,
                    state,
                    terminal_event.payload,
                    commitments=case_commitments,
                )
                if not self._identity_matches(
                    terminal_attempt,
                    case_id=existing.case_id,
                    attempt_id=existing.attempt_id,
                    revision_id=existing.revision_id,
                    suite_commitment_sha256=existing.suite_commitment_sha256,
                    scenario_hashes=existing.scenario_hashes,
                ):
                    raise IntegrityError("qualification terminal identity is invalid")
                stored_result = terminal_event.payload.get("result")
                if stored_result is not None and not isinstance(stored_result, Mapping):
                    raise IntegrityError("qualification terminal result is invalid")
                if (
                    existing.attempt_id == attempt_id
                    and existing.revision_id == revision_id
                    and existing.suite_commitment_sha256 == suite_commitment_sha256
                    and existing.scenario_hashes == normalized
                    and case["qualification_result"] == state
                ):
                    if result_payload is not None and _json_text(result_payload) != _json_text(
                        stored_result
                    ):
                        raise QualificationConflictError(
                            "qualification terminal result is immutable"
                        )
                    return QualificationAttempt(
                        case_id=existing.case_id,
                        attempt_id=existing.attempt_id,
                        revision_id=existing.revision_id,
                        suite_commitment_sha256=existing.suite_commitment_sha256,
                        scenario_hashes=existing.scenario_hashes,
                        state=existing.state,
                        result=stored_result,
                    )
                raise QualificationConflictError("qualification terminal state is immutable")
            if case["qualification_result"] != "RUNNING":
                raise QualificationConflictError("qualification is not running")
            self._require_attempt(
                connection,
                case_id=case_id,
                attempt_id=attempt_id,
                revision_id=revision_id,
                suite_commitment_sha256=suite_commitment_sha256,
                scenario_hashes=normalized,
                state=case["qualification_result"],
                commitments=case_commitments,
            )
            payload = self._identity_payload(
                attempt_id=attempt_id,
                revision_id=revision_id,
                suite_commitment_sha256=suite_commitment_sha256,
                scenario_hashes=normalized,
                commitments=case_commitments,
            )
            payload["result"] = result_payload
            connection.execute(
                "UPDATE cases SET qualification_result = ? WHERE case_id = ?",
                (state, case_id),
            )
            self._append_event(
                connection,
                LedgerEventRecord(
                    event_id=_new_id("evt"),
                    case_id=case_id,
                    revision_id=revision_id,
                    event_type=f"QUALIFICATION_{state}",
                    payload=payload,
                    request_id=_new_id("req"),
                ),
            )
            return QualificationAttempt(
                case_id=case_id,
                attempt_id=attempt_id,
                revision_id=revision_id,
                suite_commitment_sha256=suite_commitment_sha256,
                scenario_hashes=normalized,
                state=state,
                result=result_payload,
            )

    complete_qualification = record_qualification_terminal

    def get_qualification(self, case_id: str) -> QualificationAttempt | None:
        _id(case_id, "case_id")
        with self._read_transaction() as connection:
            case = connection.execute(
                "SELECT * FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
            if case is None:
                raise CaseNotFoundError("case was not found")
            if case["qualification_result"] is None:
                return None
            state = case["qualification_result"]
            case_commitments = self._stored_commitment_payload(case)
            if state in {"PASSED", "FAILED"}:
                self._verified_ledger_from_connection(connection)
            attempt = self._latest_attempt_from_connection(
                connection, case_id, state, case_commitments
            )
            if state in {"PASSED", "FAILED"}:
                row = connection.execute(
                    """
                    SELECT * FROM ledger_events
                    WHERE case_id = ? AND event_type = ?
                    ORDER BY seq DESC LIMIT 1
                    """,
                    (case_id, f"QUALIFICATION_{state}"),
                ).fetchone()
                if row is None:
                    raise IntegrityError("qualification terminal event is missing")
                event = self._event_from_row(row)
                terminal_attempt = self._attempt_from_payload(
                    case_id, state, event.payload, commitments=case_commitments
                )
                if not self._identity_matches(
                    terminal_attempt,
                    case_id=attempt.case_id,
                    attempt_id=attempt.attempt_id,
                    revision_id=attempt.revision_id,
                    suite_commitment_sha256=attempt.suite_commitment_sha256,
                    scenario_hashes=attempt.scenario_hashes,
                ):
                    raise IntegrityError("qualification terminal identity is invalid")
                result = event.payload.get("result")
                return QualificationAttempt(
                    case_id=attempt.case_id,
                    attempt_id=attempt.attempt_id,
                    revision_id=attempt.revision_id,
                    suite_commitment_sha256=attempt.suite_commitment_sha256,
                    scenario_hashes=attempt.scenario_hashes,
                    state=state,
                    result=result if isinstance(result, Mapping) else None,
                )
            return attempt

    def record_promotion_receipt(
        self,
        *,
        case_id: str,
        revision_id: str,
        ticket_id: str,
        manifest_sha256: str,
        receipt: Mapping[str, Any] | None = None,
    ) -> PromotionReceipt:
        _id(case_id, "case_id")
        _id(revision_id, "revision_id")
        _id(ticket_id, "ticket_id")
        _sha256(manifest_sha256, "manifest_sha256")
        if receipt is not None and not isinstance(receipt, Mapping):
            raise ValidationError("receipt must be an object")
        receipt_payload = (
            json.loads(_json_text(dict(receipt))) if receipt is not None else {}
        )
        with self._transaction() as connection:
            case = connection.execute(
                "SELECT * FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
            if case is None:
                raise CaseNotFoundError("case was not found")
            if case["qualification_result"] != "PASSED":
                raise PromotionConflictError("promotion requires a passed qualification")
            if case["qualification_revision_id"] != revision_id:
                raise PromotionConflictError("promotion revision is not qualified")
            existing = self._promotion_from_connection(connection, case_id, revision_id)
            if existing is not None:
                if (
                    existing.ticket_id == ticket_id
                    and existing.manifest_sha256 == manifest_sha256
                    and existing.receipt == receipt_payload
                ):
                    return existing
                raise PromotionConflictError("promotion receipt is immutable")
            payload = {
                "ticket_id": ticket_id,
                "revision_id": revision_id,
                "manifest_sha256": manifest_sha256,
                "receipt": receipt_payload,
            }
            connection.execute(
                "UPDATE cases SET promoted_revision_id = ? WHERE case_id = ?",
                (revision_id, case_id),
            )
            self._append_event(
                connection,
                LedgerEventRecord(
                    event_id=_new_id("evt"),
                    case_id=case_id,
                    revision_id=revision_id,
                    event_type="PROMOTED",
                    payload=payload,
                    request_id=_new_id("req"),
                ),
            )
            return PromotionReceipt(
                case_id=case_id,
                revision_id=revision_id,
                ticket_id=ticket_id,
                manifest_sha256=manifest_sha256,
                receipt=receipt_payload,
            )

    record_promotion = record_promotion_receipt

    @classmethod
    def _promotion_from_connection(
        cls,
        connection: sqlite3.Connection,
        case_id: str,
        revision_id: str | None = None,
    ) -> PromotionReceipt | None:
        if revision_id is None:
            row = connection.execute(
                """
                SELECT * FROM ledger_events
                WHERE case_id = ? AND event_type = 'PROMOTED'
                ORDER BY seq DESC LIMIT 1
                """,
                (case_id,),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT * FROM ledger_events
                WHERE case_id = ? AND revision_id = ? AND event_type = 'PROMOTED'
                ORDER BY seq DESC LIMIT 1
                """,
                (case_id, revision_id),
            ).fetchone()
        if row is None:
            return None
        event = cls._event_from_row(row)
        try:
            ticket_id = event.payload["ticket_id"]
            stored_revision = event.payload["revision_id"]
            manifest_sha256 = event.payload["manifest_sha256"]
            receipt = event.payload["receipt"]
        except (KeyError, TypeError) as exc:
            raise IntegrityError("promotion receipt is incomplete") from exc
        if stored_revision != event.revision_id or not isinstance(receipt, Mapping):
            raise IntegrityError("promotion receipt identity is invalid")
        _id(ticket_id, "ticket_id")
        _sha256(manifest_sha256, "manifest_sha256")
        return PromotionReceipt(
            case_id=case_id,
            revision_id=stored_revision,
            ticket_id=ticket_id,
            manifest_sha256=manifest_sha256,
            receipt=receipt,
        )

    def reconcile_promotion(
        self,
        *,
        case_id: str,
        revision_id: str | None = None,
    ) -> PromotionReceipt | None:
        _id(case_id, "case_id")
        if revision_id is not None:
            _id(revision_id, "revision_id")
        with self._read_connection() as connection:
            case = connection.execute(
                "SELECT * FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
            if case is None:
                raise CaseNotFoundError("case was not found")
            receipt = self._promotion_from_connection(connection, case_id, revision_id)
            if case["promoted_revision_id"] is None:
                if receipt is not None:
                    raise IntegrityError("promotion event exists without promoted state")
                return None
            if receipt is None or receipt.revision_id != case["promoted_revision_id"]:
                raise IntegrityError("promoted state has no matching receipt")
            return receipt

    get_promotion_receipt = reconcile_promotion

    def ledger_events(self, case_id: str | None = None) -> tuple[LedgerEvent, ...]:
        if case_id is not None:
            _id(case_id, "case_id")
        with self._read_connection() as connection:
            if case_id is None:
                rows = connection.execute("SELECT * FROM ledger_events ORDER BY seq").fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM ledger_events WHERE case_id = ? ORDER BY seq", (case_id,)
                ).fetchall()
        return tuple(self._event_from_row(row) for row in rows)

    def _verified_ledger_from_connection(
        self, connection: sqlite3.Connection
    ) -> tuple[LedgerEvent, ...]:
        rows = connection.execute("SELECT * FROM ledger_events ORDER BY seq").fetchall()
        sequence = connection.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'ledger_events'"
        ).fetchone()
        expected_last_seq = sequence["seq"] if sequence is not None else 0
        actual_last_seq = rows[-1]["seq"] if rows else 0
        if len(rows) != expected_last_seq or actual_last_seq != expected_last_seq:
            raise IntegrityError("ledger event tail is missing")
        previous = ZERO_HASH
        events: list[LedgerEvent] = []
        for row in rows:
            if row["prev_hash"] != previous:
                raise IntegrityError("ledger event predecessor hash mismatch")
            _sha256(row["prev_hash"], "prev_hash")
            try:
                expected = hashlib.sha256(
                    bytes.fromhex(row["prev_hash"])
                    + canonical_json_bytes(self._event_without_hash(row))
                ).hexdigest()
            except (ValueError, TypeError) as exc:
                raise IntegrityError("ledger event hash cannot be computed") from exc
            if row["event_hash"] != expected:
                raise IntegrityError("ledger event hash mismatch")
            event = self._event_from_row(row)
            events.append(event)
            previous = row["event_hash"]
        return tuple(events)

    def verify_ledger(self) -> tuple[LedgerEvent, ...]:
        with self._read_transaction() as connection:
            return self._verified_ledger_from_connection(connection)

    def close(self) -> None:
        if self._memory_connection is not None:
            self._memory_connection.close()
            self._memory_connection = None

    def __enter__(self) -> EvidenceStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


SQLiteEvidenceStore = EvidenceStore
Storage = EvidenceStore
