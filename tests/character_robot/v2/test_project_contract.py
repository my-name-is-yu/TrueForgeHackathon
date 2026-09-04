from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from character_robot.project_store import ProjectStore as V1ProjectStore
from character_robot.v2 import (
    DomainRecord,
    ImmutableRequirementsError,
    Requirements,
    READINESS_DOMAINS,
    RobotSystemSpec,
    StaleTargetTokenError,
    UnsupportedSchemaVersionError,
    V2ProjectService,
    V2ProjectSnapshot,
    V2ProjectStore,
    V2ValidationError,
    calculate_active_target_token,
    derive_readiness,
)


def _requirements(**changes: object) -> Requirements:
    payload: dict[str, object] = {
        "original_request": "A small indoor robot that greets a person.",
        "environment": {"description": "indoor desktop", "indoor_only": True},
        "dimensions": {"max_height_mm": 180.0},
        "speed": {"max_m_s": 0.1},
        "voltage": {"nominal_v": 7.4, "min_v": 6.0, "max_v": 8.4},
        "required_behavior": ["greet"],
        "safety_constraints": ["stop on communication loss"],
        "user_must_haves": ["penguin-like appearance"],
        "assumptions": ["the desk is level"],
        "unresolved_questions": [],
    }
    payload.update(changes)
    return Requirements.model_validate(payload)


def _snapshot(
    project_id: str = "studio", generation: int = 0, **spec_changes: object
) -> V2ProjectSnapshot:
    requirements = _requirements()
    spec = RobotSystemSpec(
        project_id=project_id,
        requirements=requirements,
        **spec_changes,
    )
    return V2ProjectSnapshot(
        project_id=project_id,
        generation=generation,
        spec=spec,
    )


def test_requirements_are_strict_and_deeply_immutable() -> None:
    requirements = _requirements()
    assert isinstance(requirements.required_behavior, tuple)
    with pytest.raises((ValidationError, TypeError)):
        requirements.required_behavior += ("stop",)
    with pytest.raises((ValidationError, TypeError)):
        requirements.environment.indoor_only = False
    with pytest.raises(ValidationError):
        Requirements.model_validate(
            {
                **requirements.model_dump(),
                "unexpected": "rejected",
            }
        )


def test_readiness_is_complete_server_derived_and_physical_verification_pending() -> (
    None
):
    initial = _snapshot()
    matrix = derive_readiness(initial.spec)
    assert len(matrix.domains) == 10
    assert [domain.domain_id for domain in matrix.domains] == [
        "requirements",
        "visual_design",
        "component_selection",
        "mechanical_assembly",
        "spatial_layout",
        "electrical_design",
        "runtime_binding",
        "manufacturing_plan",
        "verification_plan",
        "artifact_manifest",
    ]
    assert matrix.for_domain("requirements").state == "checked"
    assert matrix.for_domain("visual_design").state == "missing"
    assert matrix.design_complete is False
    assert matrix.datasheet_checked is False
    assert matrix.physical_verification_pending is True

    digest = hashlib.sha256(b"domain").hexdigest()
    dirty_spec = initial.spec.model_copy(
        update={
            "domains": tuple(
                DomainRecord(domain_id=domain, content_digest=digest)
                for domain in READINESS_DOMAINS
                if domain != "requirements"
            )
        }
    )
    dirty = derive_readiness(dirty_spec)
    assert dirty.for_domain("visual_design").state == "dirty"
    assert dirty.design_complete is False
    assert dirty.datasheet_checked is False
    assert dirty.physical_verification_pending is True


def test_target_token_changes_for_every_bound_field() -> None:
    fields: dict[str, object] = {
        "schema_version": "character-project/v2",
        "project_id": "studio",
        "project_generation": 0,
        "requirements_hash": "a" * 64,
        "committed_head_id": None,
        "committed_head_digest": None,
        "active_draft_id": None,
        "active_draft_digest": None,
    }
    original = calculate_active_target_token(**fields)
    mutations = {
        "schema_version": "character-project/v3",
        "project_id": "other",
        "project_generation": 1,
        "requirements_hash": "b" * 64,
        "committed_head_id": "head",
        "committed_head_digest": "b" * 64,
        "active_draft_id": "draft",
        "active_draft_digest": "c" * 64,
    }
    for field, value in mutations.items():
        candidate = dict(fields)
        candidate[field] = value
        assert calculate_active_target_token(**candidate) != original, field


def test_store_round_trip_reads_are_pure_and_write_advances_once(
    tmp_path: Path,
) -> None:
    database = tmp_path / "projects.sqlite3"
    store = V2ProjectStore(database)
    created = store.create_project("studio", _requirements())
    before = database.read_bytes()
    first = store.load_project("studio")
    second = store.load_project("studio")
    assert first == second == created
    assert first.generation == 0
    assert first.active_target_token == created.active_target_token
    assert database.read_bytes() == before

    updated = first.model_copy(
        update={
            "spec": first.spec.model_copy(
                update={
                    "active_draft_id": "draft",
                    "active_draft_digest": "d" * 64,
                }
            )
        }
    )
    saved = store.save_project(updated, first.active_target_token)
    assert saved.generation == 1
    assert store.load_project("studio").generation == 1


def test_stale_write_keeps_state_and_sqlite_bytes_unchanged(tmp_path: Path) -> None:
    database = tmp_path / "projects.sqlite3"
    first = V2ProjectStore(database)
    second = V2ProjectStore(database)
    created = first.create_project("studio", _requirements())
    stale = second.load_project("studio")
    candidate = created.model_copy(
        update={
            "spec": created.spec.model_copy(
                update={
                    "active_draft_id": "draft",
                    "active_draft_digest": "d" * 64,
                }
            )
        }
    )
    first.save_project(candidate, created.active_target_token)
    before = database.read_bytes()
    with pytest.raises(StaleTargetTokenError) as captured:
        second.save_project(stale, stale.active_target_token)
    assert captured.value.as_dict()["code"] == "STALE_TARGET_TOKEN"
    assert database.read_bytes() == before
    assert second.load_project("studio").generation == 1


def test_requirements_cannot_be_replaced_by_a_valid_token(tmp_path: Path) -> None:
    store = V2ProjectStore(tmp_path / "projects.sqlite3")
    original = store.create_project("studio", _requirements())
    replacement = original.model_copy(
        update={
            "spec": original.spec.model_copy(
                update={"requirements": _requirements(original_request="changed")}
            )
        }
    )
    with pytest.raises(ImmutableRequirementsError):
        store.save_project(replacement, original.active_target_token)
    assert store.load_project("studio") == original


def test_v1_row_is_rejected_before_v2_parsing_without_mutation(tmp_path: Path) -> None:
    database = tmp_path / "projects.sqlite3"
    V1ProjectStore(database).create_project("studio")
    before = database.read_bytes()
    with pytest.raises(UnsupportedSchemaVersionError) as captured:
        V2ProjectStore(database).load_project("studio")
    error = captured.value.as_dict()
    assert set(error) == {
        "code",
        "path",
        "expected",
        "actual",
        "retryable",
        "current_target",
        "next_actions",
    }
    assert error["code"] == "UNSUPPORTED_SCHEMA_VERSION"
    assert database.read_bytes() == before


def test_service_returns_changed_entities_invalidations_and_next_token(
    tmp_path: Path,
) -> None:
    service = V2ProjectService(tmp_path / "projects.sqlite3")
    created = service.create_project("studio", _requirements())
    result = service.write_project(
        "studio",
        created.active_target_token,
        update={
            "active_draft_id": "draft",
            "active_draft_digest": "d" * 64,
        },
        changed_entities=["active_draft"],
        invalidated_domains=["visual_design"],
        invalidated_artifacts=["preview"],
        invalidated_evidence=["evidence"],
        next_actions=["Review the draft."],
    )
    assert result.state.generation == 1
    assert result.changed_entities == ("active_draft",)
    assert result.invalidated_domains == ("visual_design",)
    assert result.invalidated_artifacts == ("preview",)
    assert result.invalidated_evidence == ("evidence",)
    assert result.next_target_token == result.state.active_target_token


def test_public_write_cannot_self_promote_a_domain_to_checked(tmp_path: Path) -> None:
    service = V2ProjectService(tmp_path / "projects.sqlite3")
    created = service.create_project("studio", _requirements())
    with pytest.raises(V2ValidationError) as captured:
        service.write_project(
            "studio",
            created.active_target_token,
            update={
                "domains": [
                    {
                        "domain_id": "visual_design",
                        "content_digest": "d" * 64,
                        "checked": True,
                    }
                ]
            },
        )
    assert getattr(captured.value, "code", None) == "INVALID_V2_PROJECT"
    assert service.get_project_state("studio") == created
    assert service.readiness("studio").design_complete is False


def test_malformed_schema_and_non_finite_values_are_structured(tmp_path: Path) -> None:
    store = V2ProjectStore(tmp_path / "projects.sqlite3")
    with pytest.raises(UnsupportedSchemaVersionError) as unsupported:
        store.import_payload({"schema_version": "character-robot/v1"})
    assert unsupported.value.as_dict()["code"] == "UNSUPPORTED_SCHEMA_VERSION"
    with pytest.raises(ValidationError):
        _requirements(dimensions={"max_height_mm": float("nan")})
    with pytest.raises(ValidationError):
        DomainRecord(
            domain_id="visual_design",
            content_digest="d" * 64,
            checked=True,
        )


def test_v1_import_payload_is_rejected_without_creating_store(tmp_path: Path) -> None:
    database = tmp_path / "projects.sqlite3"
    store = V2ProjectStore(database)
    with pytest.raises(UnsupportedSchemaVersionError):
        store.import_payload(
            {
                "schema_version": "character-project-export/v1",
                "project_id": "studio",
                "draft": None,
                "revisions": [],
                "head_revision_id": None,
                "import_requires_human_action": True,
            }
        )
    assert not database.exists()
