from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from character_robot.project_store import (
    PersistedDraft,
    PersistedRevision,
    ProjectConflictError,
    ProjectIntegrityError,
    ProjectSchemaError,
    ProjectSnapshot,
    ProjectStore,
    ProjectValidationError,
    draft_sha256,
    manifest_sha256,
    spec_sha256,
)
from character_robot.schemas import (
    ArtifactDescriptor,
    ArtifactManifest,
    CharacterRobotSpec,
    RevisionSummary,
    StudioRunSummary,
)

from test_domain_schemas import _spec_payload


def _spec() -> CharacterRobotSpec:
    return CharacterRobotSpec.model_validate(_spec_payload())


def _run(spec: CharacterRobotSpec) -> StudioRunSummary:
    return StudioRunSummary(
        run_id="run_0123456789abcdef0123456789abcdef",
        kind="compile",
        spec_hash=spec_sha256(spec),
        profile_id=spec.hardware_profile_id,
        catalog_version=spec.versions.catalog,
        compiler_version=spec.versions.compiler,
        cad_engine_version="0.11.1",
        firmware_runtime_version=spec.versions.firmware_runtime,
        duration_ms=123.5,
        cache_hit=False,
        warning_codes=["profile_incomplete"],
        error_codes=[],
    )


def _manifest(spec: CharacterRobotSpec) -> ArtifactManifest:
    artifact_content = b"deterministic GLB"
    descriptor = ArtifactDescriptor(
        kind="glb",
        file_name="robot.glb",
        media_type="model/gltf-binary",
        sha256=hashlib.sha256(artifact_content).hexdigest(),
        byte_size=len(artifact_content),
        experimental=True,
    )
    provisional = ArtifactManifest(
        revision_id="r000",
        spec_hash=spec_sha256(spec),
        build_subject_hash=hashlib.sha256(b"build subject").hexdigest(),
        geometry_sha256=hashlib.sha256(b"geometry").hexdigest(),
        profile_id=spec.hardware_profile_id,
        profile_sha256=hashlib.sha256(b"profile").hexdigest(),
        catalog_version=spec.versions.catalog,
        compiler_version=spec.versions.compiler,
        cad_engine_version="0.11.1",
        firmware_runtime_version=spec.versions.firmware_runtime,
        evidence_level="digital_checks_passed",
        artifacts=[descriptor],
        manifest_hash="0" * 64,
        download_requires_human_action=True,
    )
    return provisional.model_copy(
        update={"manifest_hash": manifest_sha256(provisional)}
    )


def _populated_snapshot(project_id: str = "duck-project") -> ProjectSnapshot:
    spec = _spec()
    spec_hash = spec_sha256(spec)
    summary = RevisionSummary(
        revision_id="r000",
        parent_revision_id=None,
        ordinal=0,
        spec_hash=spec_hash,
        note="Initial measured-profile candidate.",
        created_at="2026-09-03T12:00:00.000000+00:00",
    )
    return ProjectSnapshot(
        project_id=project_id,
        generation=0,
        head_revision_id="r000",
        draft=PersistedDraft(
            base_revision_id="r000",
            draft_hash=draft_sha256("r000", spec_hash),
            spec_hash=spec_hash,
            spec=spec,
        ),
        revisions=[PersistedRevision(summary=summary, spec=spec)],
        recent_runs=[_run(spec)],
        artifact_manifests=[_manifest(spec)],
    )


def test_project_round_trips_every_durable_record_after_reopen(tmp_path: Path) -> None:
    database = tmp_path / "studio" / "projects.sqlite3"
    store = ProjectStore(database)
    assert store.create_project("duck-project").generation == 0

    saved = store.save_project(
        _populated_snapshot(),
        expected_generation=0,
    )

    reopened = ProjectStore(database)
    loaded = reopened.load_project("duck-project")
    reopened.verify()
    assert loaded == saved
    assert loaded.generation == 1
    assert loaded.draft is not None
    assert (
        loaded.revisions[0].spec.identity.name == saved.revisions[0].spec.identity.name
    )
    assert loaded.recent_runs[0].warning_codes == ["profile_incomplete"]
    assert loaded.artifact_manifests[0].artifacts[0].file_name == "robot.glb"
    assert reopened.list_project_ids() == ("duck-project",)


def test_two_store_instances_reject_a_stale_generation(tmp_path: Path) -> None:
    database = tmp_path / "projects.sqlite3"
    first = ProjectStore(database)
    second = ProjectStore(database)
    first.create_project("duck-project")
    stale = second.load_project("duck-project")

    first.save_project(_populated_snapshot(), expected_generation=0)

    with pytest.raises(ProjectConflictError) as captured:
        second.save_project(stale, expected_generation=0)
    assert captured.value.expected_generation == 0
    assert captured.value.actual_generation == 1
    assert second.load_project("duck-project").generation == 1


def test_invalid_snapshot_is_rejected_without_advancing_storage(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "projects.sqlite3")
    store.create_project("duck-project")
    valid = _populated_snapshot()
    assert valid.draft is not None
    invalid_draft = PersistedDraft.model_construct(
        base_revision_id=valid.draft.base_revision_id,
        draft_hash="0" * 64,
        spec_hash=valid.draft.spec_hash,
        spec=valid.draft.spec,
    )
    invalid = valid.model_copy(update={"draft": invalid_draft})

    with pytest.raises(ProjectValidationError):
        store.save_project(invalid, expected_generation=0)

    assert store.load_project("duck-project").generation == 0


def test_uncommitted_sqlite_update_is_rolled_back_on_reopen(tmp_path: Path) -> None:
    database = tmp_path / "projects.sqlite3"
    store = ProjectStore(database)
    store.create_project("duck-project")
    saved = store.save_project(_populated_snapshot(), expected_generation=0)

    connection = sqlite3.connect(database, isolation_level=None)
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        "UPDATE projects SET generation = 99 WHERE project_id = 'duck-project'"
    )
    connection.close()

    recovered = ProjectStore(database).load_project("duck-project")
    assert recovered == saved
    assert recovered.generation == 1


def test_reopen_rejects_unknown_schema_and_corrupt_snapshot(tmp_path: Path) -> None:
    schema_database = tmp_path / "unknown.sqlite3"
    ProjectStore(schema_database).create_project("duck-project")
    with sqlite3.connect(schema_database) as connection:
        connection.execute(
            "UPDATE metadata SET value = '999' WHERE key = 'schema_version'"
        )
    with pytest.raises(ProjectSchemaError):
        ProjectStore(schema_database)

    user_version_database = tmp_path / "unknown-user-version.sqlite3"
    ProjectStore(user_version_database)
    with sqlite3.connect(user_version_database) as connection:
        connection.execute("PRAGMA user_version = 999")
    with pytest.raises(ProjectSchemaError):
        ProjectStore(user_version_database)

    corrupt_database = tmp_path / "corrupt.sqlite3"
    corrupt_store = ProjectStore(corrupt_database)
    corrupt_store.create_project("duck-project")
    with sqlite3.connect(corrupt_database) as connection:
        connection.execute(
            "UPDATE projects SET snapshot_json = ? WHERE project_id = ?",
            (b"{}", "duck-project"),
        )
    with pytest.raises(ProjectIntegrityError):
        ProjectStore(corrupt_database).load_project("duck-project")
