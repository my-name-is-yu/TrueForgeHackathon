from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Literal, Self, cast

from pydantic import Field, StrictInt, ValidationError, model_validator

from .schemas import (
    ArtifactManifest,
    CharacterRobotSpec,
    RevisionId,
    RevisionSummary,
    SafeIdentifier,
    Sha256,
    StrictModel,
    StudioRunSummary,
)


PROJECT_STORE_VERSION = "character-project-store/v1"
PROJECT_REVISION_LIMIT = 512
_SQLITE_SCHEMA_VERSION = 1
PROJECT_MANIFEST_LIMIT = 512
_MAX_PROJECT_BYTES = 64 * 1024 * 1024
MAX_PORTABLE_PROJECT_BYTES = _MAX_PROJECT_BYTES


class ProjectStoreError(RuntimeError):
    """Base class for durable Character Robot project failures."""


class ProjectNotFoundError(ProjectStoreError):
    pass


class ProjectAlreadyExistsError(ProjectStoreError):
    pass


class ProjectConflictError(ProjectStoreError):
    def __init__(self, project_id: str, expected: int, actual: int) -> None:
        self.project_id = project_id
        self.expected_generation = expected
        self.actual_generation = actual
        super().__init__(
            f"project {project_id!r} is at generation {actual}, not {expected}"
        )


class ProjectIntegrityError(ProjectStoreError):
    pass


class ProjectSchemaError(ProjectStoreError):
    pass


class ProjectValidationError(ProjectStoreError):
    pass


class PortableProjectSizeError(ProjectValidationError):
    code = "PORTABLE_PROJECT_TOO_LARGE"
    safe_message = "The portable project exceeds its bounded size."
    suggestion = "Prepare an earlier revision or start a new Studio project."
    next_action = (
        "Create a smaller portable project before preparing another Build Pack."
    )

    def __init__(self) -> None:
        super().__init__("portable project size is invalid")


def validate_portable_project_bytes(content: bytes) -> bytes:
    if not isinstance(content, bytes) or len(content) < 1:
        raise ProjectValidationError("portable project size is invalid")
    if len(content) > MAX_PORTABLE_PROJECT_BYTES:
        raise PortableProjectSizeError
    return content


def import_portable_project(
    content: bytes, *, destination_project_id: str = "studio"
) -> ProjectSnapshot:
    """Validate a human-supplied portable revision chain for a new project."""

    content = validate_portable_project_bytes(content)
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, ValueError) as error:
        raise ProjectValidationError("portable project is not valid JSON") from error
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "project_id",
        "head_revision_id",
        "draft",
        "revisions",
        "import_requires_human_action",
    }:
        raise ProjectValidationError("portable project fields are invalid")
    if (
        payload["schema_version"] != "character-project-export/v1"
        or payload["import_requires_human_action"] is not True
        or payload["draft"] is not None
        or not isinstance(payload["project_id"], str)
        or not isinstance(payload["revisions"], list)
    ):
        raise ProjectValidationError("portable project contract is invalid")
    try:
        return ProjectSnapshot(
            project_id=destination_project_id,
            generation=0,
            head_revision_id=payload["head_revision_id"],
            draft=None,
            revisions=[
                PersistedRevision.model_validate(revision)
                for revision in payload["revisions"]
            ],
            recent_runs=[],
            artifact_manifests=[],
        )
    except (TypeError, ValidationError, ValueError) as error:
        raise ProjectValidationError(
            "portable project revision chain is invalid"
        ) from error


class PersistedDraft(StrictModel):
    base_revision_id: RevisionId | None
    draft_hash: Sha256
    spec_hash: Sha256
    spec: CharacterRobotSpec

    @model_validator(mode="after")
    def validate_hashes(self) -> Self:
        actual_spec_hash = spec_sha256(self.spec)
        if self.spec_hash != actual_spec_hash:
            raise ValueError("draft spec_hash does not match its canonical Spec")
        if self.draft_hash != draft_sha256(self.base_revision_id, self.spec_hash):
            raise ValueError("draft_hash does not bind its base revision and Spec")
        return self


class PersistedRevision(StrictModel):
    summary: RevisionSummary
    spec: CharacterRobotSpec

    @model_validator(mode="after")
    def validate_spec_hash(self) -> Self:
        if self.summary.spec_hash != spec_sha256(self.spec):
            raise ValueError("revision spec_hash does not match its canonical Spec")
        return self


class ProjectSnapshot(StrictModel):
    store_version: Literal[PROJECT_STORE_VERSION] = PROJECT_STORE_VERSION
    project_id: SafeIdentifier
    generation: StrictInt = Field(ge=0)
    head_revision_id: RevisionId | None = None
    draft: PersistedDraft | None = None
    revisions: list[PersistedRevision] = Field(
        default_factory=list, max_length=PROJECT_REVISION_LIMIT
    )
    recent_runs: list[StudioRunSummary] = Field(default_factory=list, max_length=64)
    artifact_manifests: list[ArtifactManifest] = Field(
        default_factory=list, max_length=PROJECT_MANIFEST_LIMIT
    )

    @model_validator(mode="after")
    def validate_project_graph(self) -> Self:
        expected_parent: str | None = None
        revisions_by_id: dict[str, PersistedRevision] = {}
        for ordinal, revision in enumerate(self.revisions):
            summary = revision.summary
            expected_id = f"r{ordinal:03d}"
            if summary.ordinal != ordinal or summary.revision_id != expected_id:
                raise ValueError("revision IDs and ordinals must be contiguous")
            if summary.parent_revision_id != expected_parent:
                raise ValueError("revision parent chain is invalid")
            revisions_by_id[summary.revision_id] = revision
            expected_parent = summary.revision_id

        expected_head = (
            self.revisions[-1].summary.revision_id if self.revisions else None
        )
        if self.head_revision_id != expected_head:
            raise ValueError("head_revision_id must identify the last revision")
        if self.draft is not None and self.draft.base_revision_id != expected_head:
            raise ValueError("draft must be based on the current head revision")

        manifest_hashes: set[str] = set()
        for manifest in self.artifact_manifests:
            revision = revisions_by_id.get(manifest.revision_id)
            if revision is None:
                raise ValueError("artifact manifest references an unknown revision")
            if manifest.manifest_hash in manifest_hashes:
                raise ValueError("artifact manifest hashes must be unique")
            manifest_hashes.add(manifest.manifest_hash)
            if manifest.manifest_hash != manifest_sha256(manifest):
                raise ValueError("artifact manifest hash is invalid")
            spec = revision.spec
            if manifest.spec_hash != revision.summary.spec_hash:
                raise ValueError("artifact manifest does not match its revision Spec")
            if manifest.profile_id != spec.hardware_profile_id:
                raise ValueError(
                    "artifact manifest profile does not match its revision"
                )
            if manifest.catalog_version != spec.versions.catalog:
                raise ValueError("artifact manifest catalog version is invalid")
            if manifest.compiler_version != spec.versions.compiler:
                raise ValueError("artifact manifest compiler version is invalid")
            if manifest.firmware_runtime_version != spec.versions.firmware_runtime:
                raise ValueError("artifact manifest runtime version is invalid")

        run_ids = [run.run_id for run in self.recent_runs]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("recent run IDs must be unique")
        return self


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _model_sha256(value: StrictModel) -> str:
    return hashlib.sha256(
        _canonical_json_bytes(value.model_dump(mode="json"))
    ).hexdigest()


def spec_sha256(spec: CharacterRobotSpec) -> str:
    return _model_sha256(spec)


def draft_sha256(base_revision_id: str | None, spec_hash: str) -> str:
    return hashlib.sha256(
        _canonical_json_bytes(
            {"base_revision_id": base_revision_id, "spec_hash": spec_hash}
        )
    ).hexdigest()


def manifest_sha256(manifest: ArtifactManifest) -> str:
    payload = manifest.model_dump(mode="json", exclude={"manifest_hash"})
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def empty_project_snapshot(project_id: str) -> ProjectSnapshot:
    try:
        return ProjectSnapshot(project_id=project_id, generation=0)
    except ValidationError as error:
        raise ProjectValidationError("project_id is invalid") from error


class ProjectStore:
    """Atomic, optimistic-concurrency store for durable Studio projects.

    One canonical, checksummed project snapshot is replaced per transaction. SQLite's
    rollback journal/WAL recovers an interrupted replacement, while validation on every
    read prevents a corrupt or version-incompatible project from entering the service.
    Generated artifact bytes remain in the content-addressed artifact store; this store
    persists their immutable manifest and digests.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def create_project(self, project_id: str) -> ProjectSnapshot:
        snapshot = empty_project_snapshot(project_id)
        payload, digest = self._encode(snapshot)
        with self._lock, self._connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO projects(
                        project_id, generation, snapshot_json, snapshot_sha256
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (project_id, 0, payload, digest),
                )
                connection.commit()
            except sqlite3.IntegrityError as error:
                connection.rollback()
                raise ProjectAlreadyExistsError(
                    f"project {project_id!r} already exists"
                ) from error
            except sqlite3.Error as error:
                connection.rollback()
                raise ProjectStoreError("project could not be created") from error
        return snapshot

    def load_project(self, project_id: str) -> ProjectSnapshot:
        with self._lock, self._connection() as connection:
            try:
                row = connection.execute(
                    """
                    SELECT generation, snapshot_json, snapshot_sha256
                    FROM projects WHERE project_id = ?
                    """,
                    (project_id,),
                ).fetchone()
            except sqlite3.Error as error:
                raise ProjectStoreError("project could not be read") from error
        if row is None:
            raise ProjectNotFoundError(f"project {project_id!r} does not exist")
        return self._decode(project_id, int(row[0]), bytes(row[1]), str(row[2]))

    def save_project(
        self, snapshot: ProjectSnapshot, *, expected_generation: int
    ) -> ProjectSnapshot:
        if isinstance(expected_generation, bool) or expected_generation < 0:
            raise ProjectValidationError("expected_generation must be non-negative")
        validated = self._validate_snapshot(snapshot)
        if validated.generation != expected_generation:
            raise ProjectValidationError(
                "snapshot generation must equal expected_generation"
            )
        next_snapshot = validated.model_copy(
            update={"generation": expected_generation + 1}
        )
        next_snapshot = self._validate_snapshot(next_snapshot)
        payload, digest = self._encode(next_snapshot)

        with self._lock, self._connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT generation FROM projects WHERE project_id = ?",
                    (validated.project_id,),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    raise ProjectNotFoundError(
                        f"project {validated.project_id!r} does not exist"
                    )
                actual_generation = int(row[0])
                if actual_generation != expected_generation:
                    connection.rollback()
                    raise ProjectConflictError(
                        validated.project_id,
                        expected_generation,
                        actual_generation,
                    )
                cursor = connection.execute(
                    """
                    UPDATE projects
                    SET generation = ?, snapshot_json = ?, snapshot_sha256 = ?
                    WHERE project_id = ? AND generation = ?
                    """,
                    (
                        next_snapshot.generation,
                        payload,
                        digest,
                        validated.project_id,
                        expected_generation,
                    ),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    current = connection.execute(
                        "SELECT generation FROM projects WHERE project_id = ?",
                        (validated.project_id,),
                    ).fetchone()
                    raise ProjectConflictError(
                        validated.project_id,
                        expected_generation,
                        int(current[0]) if current is not None else -1,
                    )
                connection.commit()
            except ProjectStoreError:
                raise
            except sqlite3.Error as error:
                connection.rollback()
                raise ProjectStoreError(
                    "project could not be saved atomically"
                ) from error
        return next_snapshot

    def restore_blank_project(
        self, snapshot: ProjectSnapshot, *, generation: int
    ) -> ProjectSnapshot:
        if (
            not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 1
        ):
            raise ProjectValidationError("generation must be a positive integer")
        validated = self._validate_snapshot(snapshot)
        if validated.generation != 0:
            raise ProjectValidationError(
                "restored project snapshot must start at generation zero"
            )
        restored = self._validate_snapshot(
            validated.model_copy(update={"generation": generation})
        )
        payload, digest = self._encode(restored)

        with self._lock, self._connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    UPDATE projects
                    SET generation = ?, snapshot_json = ?, snapshot_sha256 = ?
                    WHERE project_id = ? AND generation = 0
                    """,
                    (
                        restored.generation,
                        payload,
                        digest,
                        restored.project_id,
                    ),
                )
                if cursor.rowcount != 1:
                    current = connection.execute(
                        "SELECT generation FROM projects WHERE project_id = ?",
                        (restored.project_id,),
                    ).fetchone()
                    connection.rollback()
                    if current is None:
                        raise ProjectNotFoundError(
                            f"project {restored.project_id!r} does not exist"
                        )
                    raise ProjectConflictError(
                        restored.project_id,
                        0,
                        int(current[0]),
                    )
                connection.commit()
            except ProjectStoreError:
                raise
            except sqlite3.Error as error:
                connection.rollback()
                raise ProjectStoreError(
                    "project could not be restored atomically"
                ) from error
        return restored

    def list_project_ids(self) -> tuple[str, ...]:
        with self._lock, self._connection() as connection:
            try:
                rows = connection.execute(
                    "SELECT project_id FROM projects ORDER BY project_id"
                ).fetchall()
            except sqlite3.Error as error:
                raise ProjectStoreError("projects could not be listed") from error
        return tuple(str(row[0]) for row in rows)

    def verify(self) -> None:
        """Verify SQLite and every persisted project without changing state."""

        with self._lock, self._connection() as connection:
            try:
                status = connection.execute("PRAGMA integrity_check").fetchone()
                rows = connection.execute(
                    """
                    SELECT project_id, generation, snapshot_json, snapshot_sha256
                    FROM projects ORDER BY project_id
                    """
                ).fetchall()
            except sqlite3.Error as error:
                raise ProjectIntegrityError(
                    "project database could not be verified"
                ) from error
        if status is None or status[0] != "ok":
            raise ProjectIntegrityError("SQLite integrity_check failed")
        for project_id, generation, payload, digest in rows:
            self._decode(str(project_id), int(generation), bytes(payload), str(digest))

    def _initialize(self) -> None:
        with self._lock, self._connection() as connection:
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("BEGIN IMMEDIATE")
                user_version = int(
                    connection.execute("PRAGMA user_version").fetchone()[0]
                )
                if user_version not in {0, _SQLITE_SCHEMA_VERSION}:
                    raise ProjectSchemaError(
                        f"unsupported SQLite user_version {user_version!r}"
                    )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS metadata(
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    ) WITHOUT ROWID
                    """
                )
                row = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'schema_version'"
                ).fetchone()
                if row is None:
                    connection.execute(
                        "INSERT INTO metadata(key, value) VALUES ('schema_version', ?)",
                        (str(_SQLITE_SCHEMA_VERSION),),
                    )
                elif str(row[0]) != str(_SQLITE_SCHEMA_VERSION):
                    raise ProjectSchemaError(
                        f"unsupported project database schema version {row[0]!r}"
                    )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS projects(
                        project_id TEXT PRIMARY KEY,
                        generation INTEGER NOT NULL CHECK(generation >= 0),
                        snapshot_json BLOB NOT NULL,
                        snapshot_sha256 TEXT NOT NULL
                            CHECK(length(snapshot_sha256) = 64)
                    ) WITHOUT ROWID
                    """
                )
                connection.execute(f"PRAGMA user_version = {_SQLITE_SCHEMA_VERSION}")
                connection.commit()
            except ProjectSchemaError:
                connection.rollback()
                raise
            except sqlite3.Error as error:
                connection.rollback()
                raise ProjectStoreError(
                    "project database could not be initialized"
                ) from error

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=5.0,
            isolation_level=None,
        )
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _validate_snapshot(self, snapshot: ProjectSnapshot) -> ProjectSnapshot:
        try:
            payload = snapshot.model_dump(mode="json")
            return ProjectSnapshot.model_validate(payload)
        except (AttributeError, TypeError, ValueError, ValidationError) as error:
            raise ProjectValidationError("project snapshot is invalid") from error

    def _encode(self, snapshot: ProjectSnapshot) -> tuple[bytes, str]:
        validated = self._validate_snapshot(snapshot)
        payload = _canonical_json_bytes(validated.model_dump(mode="json"))
        if len(payload) > _MAX_PROJECT_BYTES:
            raise ProjectValidationError("project snapshot exceeds the storage limit")
        return payload, hashlib.sha256(payload).hexdigest()

    def _decode(
        self, project_id: str, generation: int, payload: bytes, digest: str
    ) -> ProjectSnapshot:
        if len(payload) > _MAX_PROJECT_BYTES:
            raise ProjectIntegrityError("project snapshot exceeds the storage limit")
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ProjectIntegrityError("project snapshot checksum does not match")
        try:
            raw = json.loads(payload)
            snapshot = ProjectSnapshot.model_validate(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
            raise ProjectIntegrityError(
                "project snapshot is not valid canonical data"
            ) from error
        if _canonical_json_bytes(snapshot.model_dump(mode="json")) != payload:
            raise ProjectIntegrityError("project snapshot is not canonical JSON")
        if snapshot.project_id != project_id or snapshot.generation != generation:
            raise ProjectIntegrityError(
                "project row identity does not match its snapshot"
            )
        return cast(ProjectSnapshot, snapshot)


__all__ = [
    "MAX_PORTABLE_PROJECT_BYTES",
    "PROJECT_MANIFEST_LIMIT",
    "PROJECT_REVISION_LIMIT",
    "PROJECT_STORE_VERSION",
    "PersistedDraft",
    "PersistedRevision",
    "ProjectAlreadyExistsError",
    "ProjectConflictError",
    "ProjectIntegrityError",
    "ProjectNotFoundError",
    "ProjectSchemaError",
    "ProjectSnapshot",
    "ProjectStore",
    "ProjectStoreError",
    "ProjectValidationError",
    "PortableProjectSizeError",
    "draft_sha256",
    "empty_project_snapshot",
    "import_portable_project",
    "manifest_sha256",
    "spec_sha256",
    "validate_portable_project_bytes",
]
