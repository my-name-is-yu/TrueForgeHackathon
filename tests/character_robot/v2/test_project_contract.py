from __future__ import annotations

import hashlib
import inspect
import json
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from character_robot.project_store import (
    ProjectAlreadyExistsError,
    ProjectNotFoundError,
    ProjectStore as V1ProjectStore,
)
from character_robot.v2 import (
    DomainRecord,
    ImmutableRequirementsError,
    PROJECT_SCHEMA_VERSION,
    Requirements,
    READINESS_DOMAINS,
    RobotSystemSpec,
    StaleTargetTokenError,
    UnsupportedSchemaVersionError,
    V2ProjectService,
    V2ProjectSnapshot,
    V2ProjectStore,
    V2ContractError,
    V2ValidationError,
    SAFE_TEXT_MAX_LENGTH,
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


def test_readiness_preserves_max_length_requirement_text(tmp_path: Path) -> None:
    question = "q" * SAFE_TEXT_MAX_LENGTH
    assumption = "a" * SAFE_TEXT_MAX_LENGTH
    requirements = _requirements(
        unresolved_questions=[question],
        assumptions=[assumption],
    )
    spec = RobotSystemSpec(project_id="studio", requirements=requirements)
    matrix = derive_readiness(spec)
    requirements_readiness = matrix.for_domain("requirements")
    assert requirements_readiness.blockers == (question,)
    assert requirements_readiness.unknowns == (assumption,)

    service = V2ProjectService(tmp_path / "projects.sqlite3")
    created = service.create_project("studio", requirements)
    result = service.write_project("studio", created.active_target_token)
    prefix = "requirements: "
    suffix = " [1/1]"
    head_length = SAFE_TEXT_MAX_LENGTH - len(prefix) - len(suffix)
    expected = f"{prefix}{question[:head_length]}{suffix}"
    assert result.blockers[0] == expected


def test_identical_max_length_blockers_keep_domain_identity_and_persist(
    tmp_path: Path,
) -> None:
    blocker = "x" * SAFE_TEXT_MAX_LENGTH
    service = V2ProjectService(tmp_path / "projects.sqlite3")
    created = service.create_project("studio", _requirements())

    result = service.write_project(
        "studio",
        created.active_target_token,
        update={
            "domains": [
                {"domain_id": "visual_design", "blockers": [blocker]},
                {"domain_id": "component_selection", "blockers": [blocker]},
            ]
        },
    )

    assert result.state.generation == 1
    assert len(result.blockers) == 9
    assert result.blockers[0].startswith("visual_design: ")
    assert result.blockers[1].startswith("component_selection: ")
    assert result.blockers[0] != result.blockers[1]
    assert all(len(blocker) <= SAFE_TEXT_MAX_LENGTH for blocker in result.blockers)
    assert service.get_project_state("studio") == result.state


def test_same_domain_long_blockers_keep_ordinal_and_persist(tmp_path: Path) -> None:
    first = "x" * (SAFE_TEXT_MAX_LENGTH - 1) + "a"
    second = "x" * (SAFE_TEXT_MAX_LENGTH - 1) + "b"
    service = V2ProjectService(tmp_path / "projects.sqlite3")
    created = service.create_project("studio", _requirements())

    result = service.write_project(
        "studio",
        created.active_target_token,
        update={
            "domains": [
                {
                    "domain_id": "visual_design",
                    "blockers": [first, second],
                }
            ]
        },
    )

    visual_blockers = result.blockers[:2]
    assert visual_blockers[0].startswith("visual_design: ")
    assert visual_blockers[1].startswith("visual_design: ")
    assert visual_blockers[0].endswith("[1/2]")
    assert visual_blockers[1].endswith("[2/2]")
    assert visual_blockers[0] != visual_blockers[1]
    assert all(len(blocker) <= SAFE_TEXT_MAX_LENGTH for blocker in visual_blockers)
    assert service.get_project_state("studio") == result.state


@pytest.mark.parametrize(
    "changes",
    [
        {"original_request": "bad\ud800text"},
        {"environment": {"description": "bad\ud800text", "indoor_only": True}},
    ],
)
def test_surrogate_codepoints_are_rejected_at_text_boundaries(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _requirements(**changes)


def test_normal_unicode_text_is_accepted(tmp_path: Path) -> None:
    requirements = _requirements(
        original_request="屋内で挨拶するペンギン型ロボット",
        environment={"description": "屋内の机上", "indoor_only": True},
    )
    service = V2ProjectService(tmp_path / "projects.sqlite3")
    created = service.create_project("studio", requirements)
    assert service.get_project_state("studio") == created


def test_surrogate_create_write_and_import_are_structured_and_read_only(
    tmp_path: Path,
) -> None:
    database = tmp_path / "projects.sqlite3"
    service = V2ProjectService(database)
    invalid_requirements = _requirements().model_dump(mode="python")
    invalid_requirements["original_request"] = "bad\ud800"
    with pytest.raises(V2ValidationError):
        service.create_project("studio", invalid_requirements)
    assert not database.exists()

    created = service.create_project("studio", _requirements())
    before = database.read_bytes()
    with pytest.raises(V2ValidationError):
        service.write_project(
            "studio",
            created.active_target_token,
            update={
                "requirements": {
                    **created.spec.requirements.model_dump(mode="python"),
                    "original_request": "bad\ud800",
                }
            },
        )
    assert database.read_bytes() == before
    assert service.get_project_state("studio") == created

    payload = created.model_dump(mode="python")
    payload["spec"]["requirements"]["original_request"] = "bad\ud800"
    import_store = V2ProjectStore(tmp_path / "import.sqlite3")
    with pytest.raises(V2ValidationError):
        import_store.import_payload(payload)
    assert not (tmp_path / "import.sqlite3").exists()


def test_database_open_failure_is_a_retryable_structured_error(tmp_path: Path) -> None:
    database_directory = tmp_path / "database-directory"
    database_directory.mkdir()
    store = V2ProjectStore(database_directory)

    with pytest.raises(V2ContractError) as captured:
        store.create_project("studio", _requirements())

    error = captured.value.as_dict()
    assert error["code"] == "V2_STORAGE_ERROR"
    assert error["path"] == "database_path"
    assert error["retryable"] is True
    json.dumps(error, allow_nan=False)


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


def test_v1_and_v2_creation_share_a_database_namespace(tmp_path: Path) -> None:
    v1_database = tmp_path / "v1-first.sqlite3"
    v1 = V1ProjectStore(v1_database)
    v2 = V2ProjectStore(v1_database)
    v1_snapshot = v1.create_project("shared")
    before = v1_database.read_bytes()
    with pytest.raises(UnsupportedSchemaVersionError):
        v2.create_project("shared", _requirements())
    assert v1_database.read_bytes() == before
    assert v1.load_project("shared") == v1_snapshot

    v2_database = tmp_path / "v2-first.sqlite3"
    v2 = V2ProjectStore(v2_database)
    v2_snapshot = v2.create_project("shared", _requirements())
    v1 = V1ProjectStore(v2_database)
    with pytest.raises(ProjectAlreadyExistsError):
        v1.create_project("shared")
    assert v2.load_project("shared") == v2_snapshot
    with pytest.raises(ProjectNotFoundError):
        v1.load_project("shared")


def test_concurrent_v1_and_v2_creation_has_one_admitted_owner(
    tmp_path: Path,
) -> None:
    database = tmp_path / "concurrent.sqlite3"
    v1 = V1ProjectStore(database)
    v2 = V2ProjectStore(database)
    barrier = threading.Barrier(2)
    outcomes: list[object] = [None, None]

    def run(index: int, operation) -> None:
        barrier.wait()
        try:
            outcomes[index] = operation()
        except Exception as error:  # capture each admission result for assertions
            outcomes[index] = error

    threads = (
        threading.Thread(
            target=run,
            args=(0, lambda: v1.create_project("shared")),
        ),
        threading.Thread(
            target=run,
            args=(1, lambda: v2.create_project("shared", _requirements())),
        ),
    )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    successes = [outcome for outcome in outcomes if not isinstance(outcome, Exception)]
    failures = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(
        failures[0], (ProjectAlreadyExistsError, UnsupportedSchemaVersionError)
    )


def test_service_owns_result_metadata_and_returns_next_token(
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
    )
    assert result.state.generation == 1
    assert result.changed_entities == ("active_draft", "active_draft_digest")
    assert result.invalidated_domains == ()
    assert result.invalidated_artifacts == ()
    assert result.invalidated_evidence == ()
    incomplete = tuple(
        domain
        for domain in result.state.readiness.domains
        if domain.state in {"missing", "dirty", "blocked"}
    )
    assert result.blockers == tuple(
        f"{domain.domain_id}: {domain.state}" for domain in incomplete
    )
    assert result.next_actions == tuple(
        f"Complete and check {domain.domain_id}." for domain in incomplete
    )
    assert result.next_target_token == result.state.active_target_token

    result_fields = {
        "changed_entities",
        "invalidated_domains",
        "invalidated_artifacts",
        "invalidated_evidence",
        "blockers",
        "next_actions",
    }
    assert result_fields.isdisjoint(inspect.signature(service.write_project).parameters)
    with pytest.raises(TypeError):
        service.write_project(
            "studio",
            result.next_target_token,
            changed_entities=("forged",),
        )


def test_changed_entities_include_digest_only_changes(tmp_path: Path) -> None:
    service = V2ProjectService(tmp_path / "projects.sqlite3")
    created = service.create_project("studio", _requirements())
    with_draft = service.write_project(
        "studio",
        created.active_target_token,
        update={
            "active_draft_id": "draft",
            "active_draft_digest": "d" * 64,
        },
    )
    digest_only = service.write_project(
        "studio",
        with_draft.next_target_token,
        update={"active_draft_digest": "e" * 64},
    )
    assert digest_only.changed_entities == ("active_draft_digest",)

    with_head = service.write_project(
        "studio",
        digest_only.next_target_token,
        update={
            "committed_head_id": "head",
            "committed_head_digest": "a" * 64,
        },
    )
    head_digest_only = service.write_project(
        "studio",
        with_head.next_target_token,
        update={"committed_head_digest": "b" * 64},
    )
    assert head_digest_only.changed_entities == ("committed_head_digest",)


def test_write_result_validation_precedes_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "projects.sqlite3"
    service = V2ProjectService(database)
    created = service.create_project("studio", _requirements())
    before = database.read_bytes()
    too_long = "x" * (SAFE_TEXT_MAX_LENGTH + 1)
    monkeypatch.setattr(
        V2ProjectService,
        "_readiness_actions",
        staticmethod(lambda readiness: ((), (too_long,))),
    )

    with pytest.raises(V2ValidationError) as captured:
        service.write_project(
            "studio",
            created.active_target_token,
            update={
                "active_draft_id": "draft",
                "active_draft_digest": "d" * 64,
            },
        )
    assert captured.value.as_dict()["path"] == "write_result"
    assert database.read_bytes() == before
    unchanged = service.get_project_state("studio")
    assert unchanged.generation == created.generation
    assert unchanged.active_target_token == created.active_target_token


@pytest.mark.parametrize(
    "environment",
    [
        {"description": "indoor desktop"},
        {"indoor_only": True},
    ],
)
def test_environment_constraints_require_explicit_environment_facts(
    environment: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _requirements(environment=environment)


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


@pytest.mark.parametrize(
    "value",
    [
        object(),
        float("nan"),
        float("inf"),
    ],
)
def test_mapping_import_serialization_failures_are_structured(
    tmp_path: Path, value: object
) -> None:
    store = V2ProjectStore(tmp_path / "projects.sqlite3")
    with pytest.raises(V2ValidationError) as captured:
        store.import_payload(
            {
                "schema_version": PROJECT_SCHEMA_VERSION,
                "unserializable": value,
            }
        )
    error = captured.value.as_dict()
    assert error["code"] == "INVALID_V2_PROJECT"
    assert error["path"] == "$"
    json.dumps(error, allow_nan=False)
    assert not (tmp_path / "projects.sqlite3").exists()


def test_recursive_mapping_import_serialization_failure_is_structured(
    tmp_path: Path,
) -> None:
    store = V2ProjectStore(tmp_path / "projects.sqlite3")
    payload: dict[str, object] = {"schema_version": PROJECT_SCHEMA_VERSION}
    payload["recursive"] = payload
    with pytest.raises(V2ValidationError) as captured:
        store.import_payload(payload)
    assert captured.value.as_dict()["code"] == "INVALID_V2_PROJECT"
    assert not (tmp_path / "projects.sqlite3").exists()


def test_error_dict_is_bounded_and_json_safe_for_arbitrary_values() -> None:
    error = V2ContractError(
        code=b"c" * 10_000,
        path=object(),
        expected={
            "bytes": b"b" * 10_000,
            "non_finite": float("nan"),
            "items": list(range(10_000)),
        },
        actual=object(),
        retryable=object(),
        current_target=b"t" * 10_000,
        next_actions=object(),
    )
    payload = error.as_dict()
    encoded = json.dumps(payload, allow_nan=False).encode("utf-8")
    assert len(encoded) <= 16 * 1024
    assert set(payload) == {
        "code",
        "path",
        "expected",
        "actual",
        "retryable",
        "current_target",
        "next_actions",
    }


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
