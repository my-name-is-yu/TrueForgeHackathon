"""Durable V2 project rows and raw-schema admission."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .contract import (
    PROJECT_SCHEMA_VERSION,
    V2ProjectSnapshot,
    V2_STORE_NAMESPACE,
    Requirements,
    RobotSystemSpec,
)
from .errors import (
    ImmutableRequirementsError,
    StaleTargetTokenError,
    UnsupportedSchemaVersionError,
    V2ContractError,
    V2ProjectAlreadyExistsError,
    V2ProjectNotFoundError,
    V2ValidationError,
)


MAX_PROJECT_BYTES = 64 * 1024 * 1024
_TABLE_NAME = "project_rows"
_LEGACY_TABLE_NAME = "projects"
_V2_INSERT_TRIGGER = "v2_prevent_legacy_project_collision"
_V2_UPDATE_TRIGGER = "v2_prevent_legacy_project_update_collision"
_LEGACY_INSERT_TRIGGER = "v2_prevent_v2_project_collision"
_LEGACY_UPDATE_TRIGGER = "v2_prevent_v2_project_update_collision"


def _storage_error(
    error: BaseException,
    *,
    path: str,
    expected: str,
    message: str,
    current_target: str | None = None,
) -> V2ContractError:
    return V2ContractError(
        code="V2_STORAGE_ERROR",
        path=path,
        expected=expected,
        actual=type(error).__name__,
        retryable=True,
        current_target=current_target,
        next_actions=("Retry the project operation.",),
        message=message,
    )


def _rollback_safely(connection: sqlite3.Connection) -> None:
    try:
        connection.rollback()
    except (OSError, sqlite3.Error):
        pass


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def snapshot_sha256(snapshot: V2ProjectSnapshot) -> str:
    return hashlib.sha256(
        _canonical_json_bytes(snapshot.model_dump(mode="json"))
    ).hexdigest()


def empty_v2_project_snapshot(
    project_id: str, requirements: Requirements
) -> V2ProjectSnapshot:
    try:
        spec = RobotSystemSpec(project_id=project_id, requirements=requirements)
        return V2ProjectSnapshot(project_id=project_id, generation=0, spec=spec)
    except (TypeError, ValueError, ValidationError) as error:
        raise V2ValidationError(
            path="project_id",
            actual=project_id,
            message="the V2 project ID or requirements are invalid",
        ) from error


class V2ProjectStore:
    """Optimistic-concurrency store sharing a SQLite file with the V1 store.

    V2 rows live in a namespaced ``project_rows`` table.  The table is created
    lazily on the first V2 creation, so a read or a rejected V1 admission does
    not alter the database.  If the requested ID exists only in V1's ``projects``
    table, its raw schema marker is inspected and rejected before V2 parsing.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self._lock = threading.RLock()
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise _storage_error(
                error,
                path="database_path",
                expected="a writable parent directory for the SQLite database",
                message="the V2 SQLite database parent directory could not be created",
            ) from error

    def create_project(
        self, project_id: str, requirements: Requirements
    ) -> V2ProjectSnapshot:
        self._reject_legacy_before_write(project_id)
        snapshot = empty_v2_project_snapshot(project_id, requirements)
        payload, digest = self._encode(snapshot)
        with self._lock, self._connection_scope() as connection:
            # Keep a rejected legacy admission read-only.  A second check is
            # required after taking the write lock to close the admission race.
            self._reject_legacy_if_present(connection, project_id)
            try:
                connection.execute("BEGIN IMMEDIATE")
                # Acquire the database write lock before inspecting the legacy
                # table.  Schema setup and admission then share one transaction,
                # so a V1 creator cannot pass the check between those steps.
                self._reject_legacy_if_present(connection, project_id)
                self._ensure_schema(connection)
                existing = connection.execute(
                    f"""
                    SELECT 1 FROM {_TABLE_NAME}
                    WHERE namespace = ? AND project_id = ?
                    """,
                    (V2_STORE_NAMESPACE, project_id),
                ).fetchone()
                if existing is not None:
                    _rollback_safely(connection)
                    raise V2ProjectAlreadyExistsError(project_id)
                connection.execute(
                    f"""
                    INSERT INTO {_TABLE_NAME}(
                        namespace, project_id, generation,
                        snapshot_json, snapshot_sha256
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (V2_STORE_NAMESPACE, project_id, 0, payload, digest),
                )
                connection.commit()
            except V2ContractError:
                _rollback_safely(connection)
                raise
            except sqlite3.IntegrityError as error:
                _rollback_safely(connection)
                raise V2ProjectAlreadyExistsError(project_id) from error
            except (OSError, sqlite3.Error) as error:
                _rollback_safely(connection)
                raise _storage_error(
                    error,
                    path="project_id",
                    expected="a writable SQLite V2 project store",
                    message="the V2 project could not be created",
                ) from error
        return snapshot

    def load_project(self, project_id: str) -> V2ProjectSnapshot:
        if not self._database_exists():
            raise V2ProjectNotFoundError(project_id)
        with self._lock, self._connection_scope(read_only=True) as connection:
            self._reject_legacy_if_present(connection, project_id)
            row = self._read_v2_row(connection, project_id)
            if row is not None:
                return self._decode_row(project_id, row)
        raise V2ProjectNotFoundError(project_id)

    def save_project(
        self,
        snapshot: V2ProjectSnapshot,
        expected_target_token: str,
        *,
        expected_generation: int | None = None,
    ) -> V2ProjectSnapshot:
        """Replace one snapshot, requiring the exact current target token."""

        validated = self._validate_snapshot(snapshot)
        if expected_generation is not None:
            if (
                not isinstance(expected_generation, int)
                or isinstance(expected_generation, bool)
                or expected_generation < 0
            ):
                raise V2ValidationError(
                    path="generation",
                    expected="a non-negative integer",
                    actual=expected_generation,
                )
            if validated.generation != expected_generation:
                raise V2ValidationError(
                    path="generation",
                    expected=expected_generation,
                    actual=validated.generation,
                )
        if not isinstance(expected_target_token, str):
            raise V2ValidationError(
                path="active_target_token",
                expected="a string target token",
                actual=expected_target_token,
            )
        self._reject_legacy_before_write(validated.project_id)
        payload_snapshot = validated.model_copy(
            update={"generation": validated.generation + 1}
        )
        payload, digest = self._encode(payload_snapshot)

        with self._lock, self._connection_scope() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._reject_legacy_if_present(connection, validated.project_id)
                self._ensure_schema(connection)
                row = self._read_v2_row(connection, validated.project_id)
                if row is None:
                    _rollback_safely(connection)
                    raise V2ProjectNotFoundError(validated.project_id)
                current = self._decode_row(validated.project_id, row)
                if current.active_target_token != expected_target_token:
                    _rollback_safely(connection)
                    raise StaleTargetTokenError(
                        actual=expected_target_token,
                        current_target=current.active_target_token,
                    )
                if current.generation != validated.generation:
                    _rollback_safely(connection)
                    raise StaleTargetTokenError(
                        actual=expected_target_token,
                        current_target=current.active_target_token,
                        path="generation",
                    )
                if current.spec.requirements != validated.spec.requirements:
                    _rollback_safely(connection)
                    raise ImmutableRequirementsError(
                        current_target=current.active_target_token
                    )
                cursor = connection.execute(
                    f"""
                    UPDATE {_TABLE_NAME}
                    SET generation = ?, snapshot_json = ?, snapshot_sha256 = ?
                    WHERE namespace = ? AND project_id = ? AND generation = ?
                    """,
                    (
                        payload_snapshot.generation,
                        payload,
                        digest,
                        V2_STORE_NAMESPACE,
                        validated.project_id,
                        validated.generation,
                    ),
                )
                if cursor.rowcount != 1:
                    _rollback_safely(connection)
                    current_row = self._read_v2_row(connection, validated.project_id)
                    if current_row is None:
                        raise V2ProjectNotFoundError(validated.project_id)
                    current = self._decode_row(validated.project_id, current_row)
                    raise StaleTargetTokenError(
                        actual=expected_target_token,
                        current_target=current.active_target_token,
                    )
                connection.commit()
            except V2ContractError:
                _rollback_safely(connection)
                raise
            except (OSError, sqlite3.Error) as error:
                _rollback_safely(connection)
                raise _storage_error(
                    error,
                    path="project_id",
                    expected="an atomic SQLite write",
                    message="the V2 project could not be saved atomically",
                ) from error
        return payload_snapshot

    def list_project_ids(self) -> tuple[str, ...]:
        if not self._database_exists():
            return ()
        with self._lock, self._connection_scope(read_only=True) as connection:
            if not self._table_exists(connection):
                return ()
            try:
                rows = connection.execute(
                    f"""
                    SELECT project_id FROM {_TABLE_NAME}
                    WHERE namespace = ? ORDER BY project_id
                    """,
                    (V2_STORE_NAMESPACE,),
                ).fetchall()
            except (OSError, sqlite3.Error) as error:
                raise _storage_error(
                    error,
                    path="project_id",
                    expected="a readable SQLite V2 project store",
                    message="V2 projects could not be listed",
                ) from error
        return tuple(str(row[0]) for row in rows)

    def verify(self) -> None:
        """Validate all V2 rows without changing SQLite state."""

        if not self._database_exists():
            return
        with self._lock, self._connection_scope(read_only=True) as connection:
            if not self._table_exists(connection):
                return
            try:
                rows = connection.execute(
                    f"""
                    SELECT project_id, generation, snapshot_json, snapshot_sha256
                    FROM {_TABLE_NAME} WHERE namespace = ? ORDER BY project_id
                    """,
                    (V2_STORE_NAMESPACE,),
                ).fetchall()
            except (OSError, sqlite3.Error) as error:
                raise _storage_error(
                    error,
                    path="project_id",
                    expected="a readable SQLite V2 project store",
                    message="V2 projects could not be verified",
                ) from error
        for project_id, generation, payload, digest in rows:
            row = self._coerce_row_values(
                (generation, payload, digest),
                path="project_row",
                expected="generation INTEGER, snapshot_json BLOB, snapshot_sha256 TEXT",
            )
            snapshot = self._decode_row(str(project_id), row)
            if snapshot.generation != row[0]:
                raise V2ValidationError(
                    path="generation",
                    expected=row[0],
                    actual=snapshot.generation,
                )

    def import_payload(
        self, content: bytes | bytearray | memoryview | dict[str, Any]
    ) -> V2ProjectSnapshot:
        """Admit a V2 payload, rejecting V1 before model parsing or mutation."""

        raw = self._raw_json(content)
        self._require_v2_schema(raw)
        return self._decode_payload(raw, payload=self._canonical_payload(raw))

    def raw_project_bytes(self, project_id: str) -> bytes:
        """Return the persisted payload bytes for diagnostics and tests."""

        if not self._database_exists():
            raise V2ProjectNotFoundError(project_id)
        with self._lock, self._connection_scope(read_only=True) as connection:
            self._reject_legacy_if_present(connection, project_id)
            row = self._read_v2_row(connection, project_id)
            if row is None:
                raise V2ProjectNotFoundError(project_id)
            return bytes(row[1])

    def _connection(self, *, read_only: bool = False) -> sqlite3.Connection:
        connection: sqlite3.Connection | None = None
        try:
            if read_only:
                connection = sqlite3.connect(
                    f"{self.database_path.absolute().as_uri()}?mode=ro",
                    uri=True,
                    timeout=5.0,
                    isolation_level=None,
                )
            else:
                connection = sqlite3.connect(
                    self.database_path,
                    timeout=5.0,
                    isolation_level=None,
                )
            connection.execute("PRAGMA busy_timeout=5000")
            if read_only:
                connection.execute("BEGIN")
            return connection
        except (OSError, sqlite3.Error) as error:
            if connection is not None:
                try:
                    connection.close()
                except (OSError, sqlite3.Error):
                    pass
            raise _storage_error(
                error,
                path="database_path",
                expected="an accessible SQLite database path",
                message="the V2 SQLite database could not be opened",
            ) from error

    @contextmanager
    def _connection_scope(
        self, *, read_only: bool = False
    ) -> Iterator[sqlite3.Connection]:
        connection = self._connection(read_only=read_only)
        had_error = False
        try:
            try:
                yield connection
            except (OSError, sqlite3.Error) as error:
                had_error = True
                raise _storage_error(
                    error,
                    path="database_path",
                    expected="a readable SQLite database connection",
                    message="the V2 SQLite database operation failed",
                ) from error
            except BaseException:
                had_error = True
                raise
        finally:
            if read_only:
                _rollback_safely(connection)
            try:
                connection.close()
            except (OSError, sqlite3.Error) as error:
                if not had_error:
                    raise _storage_error(
                        error,
                        path="database_path",
                        expected="a closable SQLite database connection",
                        message="the V2 SQLite database could not be closed",
                    ) from error

    def _reject_legacy_before_write(self, project_id: str) -> None:
        if not self._database_exists():
            return
        with self._lock, self._connection_scope(read_only=True) as connection:
            self._reject_legacy_if_present(connection, project_id)

    def _database_exists(self) -> bool:
        try:
            return self.database_path.exists()
        except OSError as error:
            raise _storage_error(
                error,
                path="database_path",
                expected="an accessible SQLite database path",
                message="the V2 SQLite database path could not be inspected",
            ) from error

    def _table_exists(self, connection: sqlite3.Connection) -> bool:
        try:
            row = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = ?
                """,
                (_TABLE_NAME,),
            ).fetchone()
        except (OSError, sqlite3.Error) as error:
            raise _storage_error(
                error,
                path="database_path",
                expected="readable SQLite schema metadata",
                message="the V2 SQLite schema could not be inspected",
            ) from error
        return row is not None

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        """Create V2 rows and cross-namespace admission guards in a transaction."""

        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_LEGACY_TABLE_NAME}(
                project_id TEXT PRIMARY KEY,
                generation INTEGER NOT NULL CHECK(generation >= 0),
                snapshot_json BLOB NOT NULL,
                snapshot_sha256 TEXT NOT NULL
                    CHECK(length(snapshot_sha256) = 64)
            ) WITHOUT ROWID
            """
        )
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_TABLE_NAME}(
                namespace TEXT NOT NULL,
                project_id TEXT NOT NULL,
                generation INTEGER NOT NULL CHECK(generation >= 0),
                snapshot_json BLOB NOT NULL,
                snapshot_sha256 TEXT NOT NULL
                    CHECK(length(snapshot_sha256) = 64),
                PRIMARY KEY(namespace, project_id)
            ) WITHOUT ROWID
            """
        )
        connection.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS {_V2_INSERT_TRIGGER}
            BEFORE INSERT ON {_LEGACY_TABLE_NAME}
            WHEN EXISTS(
                SELECT 1 FROM {_TABLE_NAME}
                WHERE namespace = '{V2_STORE_NAMESPACE}'
                  AND project_id = NEW.project_id
            )
            BEGIN
                SELECT RAISE(ABORT, 'project ID already exists in V2');
            END
            """
        )
        connection.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS {_V2_UPDATE_TRIGGER}
            BEFORE UPDATE OF project_id ON {_LEGACY_TABLE_NAME}
            WHEN EXISTS(
                SELECT 1 FROM {_TABLE_NAME}
                WHERE namespace = '{V2_STORE_NAMESPACE}'
                  AND project_id = NEW.project_id
            )
            BEGIN
                SELECT RAISE(ABORT, 'project ID already exists in V2');
            END
            """
        )
        connection.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS {_LEGACY_INSERT_TRIGGER}
            BEFORE INSERT ON {_TABLE_NAME}
            WHEN NEW.namespace = '{V2_STORE_NAMESPACE}'
             AND EXISTS(
                SELECT 1 FROM {_LEGACY_TABLE_NAME}
                WHERE project_id = NEW.project_id
            )
            BEGIN
                SELECT RAISE(ABORT, 'project ID already exists in V1');
            END
            """
        )
        connection.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS {_LEGACY_UPDATE_TRIGGER}
            BEFORE UPDATE OF namespace, project_id ON {_TABLE_NAME}
            WHEN NEW.namespace = '{V2_STORE_NAMESPACE}'
             AND EXISTS(
                SELECT 1 FROM {_LEGACY_TABLE_NAME}
                WHERE project_id = NEW.project_id
            )
            BEGIN
                SELECT RAISE(ABORT, 'project ID already exists in V1');
            END
            """
        )

    def _read_v2_row(
        self, connection: sqlite3.Connection, project_id: str
    ) -> tuple[int, bytes, str] | None:
        if not self._table_exists(connection):
            return None
        try:
            row = connection.execute(
                f"""
                SELECT generation, snapshot_json, snapshot_sha256
                FROM {_TABLE_NAME}
                WHERE namespace = ? AND project_id = ?
                """,
                (V2_STORE_NAMESPACE, project_id),
            ).fetchone()
        except (OSError, sqlite3.Error) as error:
            raise _storage_error(
                error,
                path="project_id",
                expected="a readable V2 project row",
                message="the V2 project row could not be read",
            ) from error
        if row is None:
            return None
        return self._coerce_row_values(
            row,
            path="project_row",
            expected="generation INTEGER, snapshot_json BLOB, snapshot_sha256 TEXT",
        )

    def _legacy_row(
        self, connection: sqlite3.Connection, project_id: str
    ) -> tuple[int, bytes, str] | None:
        try:
            row = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'projects'"
            ).fetchone()
            if row is None:
                return None
            legacy = connection.execute(
                """
                SELECT generation, snapshot_json, snapshot_sha256
                FROM projects WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()
        except (OSError, sqlite3.Error) as error:
            raise _storage_error(
                error,
                path="project_id",
                expected="a readable legacy project row",
                message="the legacy project row could not be inspected",
            ) from error
        if legacy is None:
            return None
        return self._coerce_row_values(
            legacy,
            path="legacy_project_row",
            expected="generation INTEGER, snapshot_json BLOB, snapshot_sha256 TEXT",
        )

    @staticmethod
    def _coerce_row_values(
        row: tuple[Any, ...], *, path: str, expected: str
    ) -> tuple[int, bytes, str]:
        try:
            generation, payload, digest = row
            if not isinstance(generation, int) or isinstance(generation, bool):
                raise TypeError("generation is not an integer")
            if not isinstance(payload, (bytes, bytearray, memoryview)):
                raise TypeError("snapshot_json is not a byte sequence")
            if not isinstance(digest, str):
                raise TypeError("snapshot_sha256 is not text")
            return generation, bytes(payload), digest
        except (
            IndexError,
            TypeError,
            ValueError,
            OverflowError,
            UnicodeError,
        ) as error:
            raise V2ValidationError(
                path=path,
                expected=expected,
                actual=type(error).__name__,
                message="the persisted SQLite project row has invalid column types",
            ) from error

    def _reject_legacy_if_present(
        self, connection: sqlite3.Connection, project_id: str
    ) -> None:
        row = self._legacy_row(connection, project_id)
        if row is None:
            return
        payload = row[1]
        try:
            raw = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise V2ValidationError(
                path="schema_version",
                expected="a readable schema marker",
                actual="malformed legacy JSON",
            ) from error
        if not isinstance(raw, dict):
            raise V2ValidationError(
                path="schema_version",
                expected="a JSON object with schema_version",
                actual=type(raw).__name__,
            )
        actual = raw.get("schema_version", raw.get("store_version"))
        raise UnsupportedSchemaVersionError(actual=actual)

    def _validate_snapshot(self, snapshot: V2ProjectSnapshot) -> V2ProjectSnapshot:
        try:
            return V2ProjectSnapshot.model_validate(snapshot.model_dump(mode="json"))
        except (AttributeError, TypeError, ValueError, ValidationError) as error:
            raise V2ValidationError(actual=str(error)) from error

    def _encode(self, snapshot: V2ProjectSnapshot) -> tuple[bytes, str]:
        validated = self._validate_snapshot(snapshot)
        payload = _canonical_json_bytes(validated.model_dump(mode="json"))
        if len(payload) > MAX_PROJECT_BYTES:
            raise V2ValidationError(
                path="$",
                expected=f"at most {MAX_PROJECT_BYTES} bytes",
                actual=len(payload),
            )
        return payload, hashlib.sha256(payload).hexdigest()

    def _decode_row(
        self, project_id: str, row: tuple[int, bytes, str]
    ) -> V2ProjectSnapshot:
        generation, payload, digest = row
        if hashlib.sha256(payload).hexdigest() != digest:
            raise V2ValidationError(
                path="snapshot_sha256",
                expected="the SHA-256 of snapshot_json",
                actual=digest,
                message="the V2 project checksum does not match",
            )
        return self._decode_payload(
            self._raw_json(payload),
            payload=payload,
            project_id=project_id,
            generation=generation,
        )

    def _decode_payload(
        self,
        raw: dict[str, Any],
        *,
        payload: bytes,
        project_id: str | None = None,
        generation: int | None = None,
    ) -> V2ProjectSnapshot:
        self._require_v2_schema(raw)
        try:
            snapshot = V2ProjectSnapshot.model_validate(raw)
        except (TypeError, ValueError, ValidationError) as error:
            path = "$"
            if isinstance(error, ValidationError) and error.errors():
                location = error.errors()[0].get("loc", ())
                path = ".".join(str(item) for item in location) or "$"
            raise V2ValidationError(
                path=path,
                expected="a valid V2 project snapshot",
                actual=str(error),
            ) from error
        canonical = self._canonical_payload(snapshot.model_dump(mode="json"))
        if canonical != payload:
            raise V2ValidationError(
                path="$",
                expected="canonical JSON",
                actual="non-canonical JSON",
                message="the V2 project snapshot is not canonical JSON",
            )
        if project_id is not None and snapshot.project_id != project_id:
            raise V2ValidationError(
                path="project_id",
                expected=project_id,
                actual=snapshot.project_id,
            )
        if generation is not None and snapshot.generation != generation:
            raise V2ValidationError(
                path="generation",
                expected=generation,
                actual=snapshot.generation,
            )
        return snapshot

    @staticmethod
    def _canonical_payload(value: object) -> bytes:
        try:
            return _canonical_json_bytes(value)
        except (
            TypeError,
            ValueError,
            UnicodeError,
            OverflowError,
            RecursionError,
        ) as error:
            raise V2ValidationError(
                path="$",
                expected="a finite JSON-serializable V2 mapping",
                actual=type(error).__name__,
                message="the V2 payload could not be serialized as canonical JSON",
            ) from error

    def _raw_json(
        self, content: bytes | bytearray | memoryview | dict[str, Any]
    ) -> dict[str, Any]:
        if isinstance(content, dict):
            raw = content
        elif isinstance(content, (bytes, bytearray, memoryview)):
            if len(content) > MAX_PROJECT_BYTES:
                raise V2ValidationError(
                    path="$",
                    expected=f"at most {MAX_PROJECT_BYTES} bytes",
                    actual=len(content),
                )
            try:
                raw = json.loads(bytes(content))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise V2ValidationError(
                    path="$",
                    expected="valid JSON",
                    actual="malformed JSON",
                ) from error
        else:
            raise V2ValidationError(
                path="$",
                expected="bytes or a JSON object",
                actual=type(content).__name__,
            )
        if not isinstance(raw, dict):
            raise V2ValidationError(
                path="$",
                expected="a JSON object",
                actual=type(raw).__name__,
            )
        return raw

    def _require_v2_schema(self, raw: dict[str, Any]) -> None:
        actual = raw.get("schema_version")
        if actual != PROJECT_SCHEMA_VERSION:
            raise UnsupportedSchemaVersionError(actual=actual)


__all__ = [
    "MAX_PROJECT_BYTES",
    "V2ProjectStore",
    "empty_v2_project_snapshot",
    "snapshot_sha256",
]
