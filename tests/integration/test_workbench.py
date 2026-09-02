from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET

import pytest
from starlette.testclient import TestClient

from asset_autopsy.fixture import CASE_ID, clean_end_effector_position
from asset_autopsy.runner import RunRecord, SegmentRecord
from asset_autopsy.service import AssetAutopsyService
from asset_autopsy.workbench import (
    SESSION_COOKIE,
    SessionManager,
    WorkbenchError,
    create_workbench_app,
)


class WorkbenchRunner:
    def __init__(self, *, fail_hidden_qualification: bool = False) -> None:
        self.fail_hidden_qualification = fail_hidden_qualification

    async def validate(self, _xml_string: str) -> bool:
        return True

    async def run(self, configuration):
        root = ET.fromstring(configuration.xml_string)
        joints = {joint.attrib["name"]: joint for joint in root.findall(".//joint")}
        repaired = tuple(
            float(item) for item in joints["joint_b"].attrib["axis"].split()
        ) == (0.0, 1.0, 0.0)
        elapsed = 0
        segments = []
        for requested in configuration.segments:
            rows = []
            for _ in range(requested.n_steps):
                elapsed += 1
                target = tuple(float(item) for item in requested.ctrl)
                hidden_failure = (
                    self.fail_hidden_qualification
                    and requested.label == "qualification"
                )
                if hidden_failure:
                    error = 0.1
                else:
                    error = (
                        0.0 if repaired or requested.label != "public_center" else 0.1
                    )
                speed = error
                body = clean_end_effector_position(target)
                row = {
                    "t": 0.002 * elapsed,
                    "E_pot": 0.0,
                    "E_kin": speed * speed * 3,
                    "qpos": target,
                    "qvel": (speed, speed, speed),
                    "ctrl": target,
                }
                for selection in configuration.track:
                    if selection.startswith("body_xpos:"):
                        row[selection] = (body[0] + error, body[1], body[2])
                    elif selection == "contact_count":
                        row["ncon"] = 0
                rows.append(row)
            segments.append(
                SegmentRecord(
                    requested.label, requested.n_steps, requested.ctrl, tuple(rows)
                )
            )
        return RunRecord(step_count=elapsed, segments=tuple(segments))


def _manager(tmp_path, *, fail_hidden_qualification: bool = False) -> SessionManager:
    return SessionManager(
        root=tmp_path,
        service_factory=lambda path: AssetAutopsyService(
            path,
            runner=WorkbenchRunner(fail_hidden_qualification=fail_hidden_qualification),
        ),
    )


def _context(client: TestClient) -> dict:
    response = client.get("/api/context")
    assert response.status_code == 200
    return response.json()


def _assert_public_context_shape(context: dict) -> None:
    assert set(context) == {
        "case",
        "design",
        "head_revision_id",
        "head_asset_sha256",
        "head_parent_revision_id",
        "head_canonical_diff",
        "draft",
        "experiment_traces",
        "latest_task",
        "editing_locked",
    }
    assert set(context["case"]) == {
        "schema_version",
        "request_id",
        "case_id",
        "event_ids",
        "warnings",
        "artifacts",
        "qualification_state",
        "original_revision_id",
        "original_asset_sha256",
        "controller_sha256",
        "public_contract_sha256",
        "runner_sha256",
        "holdout_commitment_sha256",
        "public_scenarios",
        "contract_clauses",
        "compiled_dimensions",
        "joints",
        "bodies",
        "actuators",
        "observable_metric_names",
        "patch_policy",
        "remaining_budgets",
    }


def _draft(context: dict) -> dict:
    return {
        "base_revision_id": context["head_revision_id"],
        "expected_base_sha256": context["head_asset_sha256"],
        "patch": {
            "target": {"kind": "joint", "name": "joint_b"},
            "attribute": "axis",
            "expected_old_value": [0.0, 0.0, 1.0],
            "new_value": [0.0, 1.0, 0.0],
        },
    }


def _experiment(context: dict) -> dict:
    return {
        "case_id": context["case"]["case_id"],
        "revision_id": context["head_revision_id"],
        "hypothesis": {
            "claim": "Joint B axis controls the out-of-plane response.",
            "suspected_elements": [
                {"kind": "joint", "name": "joint_b", "attributes": ["axis"]}
            ],
            "competing_explanation": {
                "claim": "Joint C damping controls the observed response.",
                "suspected_elements": [
                    {
                        "kind": "joint",
                        "name": "joint_c",
                        "attributes": ["damping"],
                    }
                ],
                "discriminating_reason": "Axis direction and velocity decay separate the causes.",
            },
            "prediction": "Joint B motion will reveal an axis mismatch.",
            "falsifier": "The response remains aligned with the authored axis.",
        },
        "initial_joint_positions": [
            {"joint_name": "joint_a", "position_rad": 0.0},
            {"joint_name": "joint_b", "position_rad": 0.0},
            {"joint_name": "joint_c", "position_rad": 0.0},
        ],
        "segments": [
            {
                "label": "discriminate",
                "n_steps": 256,
                "controls": [
                    {"actuator_name": "motor_a", "value": 0.0},
                    {"actuator_name": "motor_b", "value": 0.2},
                    {"actuator_name": "motor_c", "value": 0.0},
                ],
            }
        ],
        "observables": [
            {"kind": "qpos"},
            {"kind": "qvel"},
            {"kind": "body_position", "body_name": "end_effector"},
        ],
        "capture_final_snapshot": False,
    }


def _create_repaired_revision(client: TestClient, context: dict) -> dict:
    baseline = client.post(
        "/api/tools/run_task",
        json={
            "case_id": context["case"]["case_id"],
            "revision_id": context["head_revision_id"],
            "scenario_id": "public_center",
            "capture": "metrics",
        },
    )
    assert baseline.status_code == 200, baseline.text
    assert (
        client.post("/api/tools/set_draft_patch", json=_draft(context)).status_code
        == 200
    )
    experiment = client.post("/api/tools/run_experiment", json=_experiment(context))
    assert experiment.status_code == 200, experiment.text
    evidence = experiment.json()["result"]
    created = client.post(
        "/api/tools/create_revision_from_draft",
        json={
            "basis_hypothesis_id": evidence["hypothesis_id"],
            "basis_experiment_run_id": evidence["run_id"],
            "rationale": "The discriminating trace supports correcting joint B axis.",
            "expected_effect": {
                "scenario_id": "public_center",
                "predicates": [
                    {"metric": "final_target_error_m", "op": "lt", "value": 0.02}
                ],
            },
        },
    )
    assert created.status_code == 200
    return evidence


def _qualify_repaired_revision(client: TestClient) -> dict:
    context = _context(client)
    _create_repaired_revision(client, context)
    repaired = _context(client)
    task = client.post(
        "/api/tools/run_task",
        json={
            "case_id": repaired["case"]["case_id"],
            "revision_id": repaired["head_revision_id"],
            "scenario_id": "public_center",
            "capture": "metrics",
        },
    )
    assert task.status_code == 200
    assert task.json()["result"]["result"] == "pass"
    verified = client.post(
        "/api/tools/verify_revision",
        json={
            "case_id": repaired["case"]["case_id"],
            "revision_id": repaired["head_revision_id"],
            "expected_asset_sha256": repaired["head_asset_sha256"],
        },
    )
    assert verified.status_code == 200
    result = verified.json()["result"]
    assert "promotion_ticket" not in result
    assert result["qualified"] is True
    assert result["editing_locked"] is True
    assert result["qualification_state"] == "passed"
    return result


def test_sessions_are_isolated_and_reset_discards_temporary_state(tmp_path) -> None:
    manager = _manager(tmp_path)
    app = create_workbench_app(manager=manager, frontend_dir=tmp_path / "missing")
    first = TestClient(app)
    second = TestClient(app)
    first_context = _context(first)
    second_context = _context(second)
    first_session_id = first.cookies.get(SESSION_COOKIE)
    assert first_session_id is not None
    first_session = manager.sessions[first_session_id]
    previous_root = first_session.data_root

    response = first.post("/api/tools/set_draft_patch", json=_draft(first_context))
    assert response.status_code == 200

    first_with_draft = _context(first)
    second_unchanged = _context(second)
    _assert_public_context_shape(first_with_draft)
    _assert_public_context_shape(second_unchanged)
    assert first_with_draft["draft"] is not None
    assert first_with_draft["experiment_traces"] == []
    assert first_with_draft["latest_task"] is None
    assert second_unchanged["draft"] is None
    assert first_context["head_asset_sha256"] == second_context["head_asset_sha256"]

    reset = first.post("/api/reset")
    assert reset.status_code == 200
    assert manager.sessions[first_session_id] is first_session
    assert first_session.data_root != previous_root
    assert first_session.data_root.is_dir()
    assert not previous_root.exists()
    reset_context = _context(first)
    _assert_public_context_shape(reset_context)
    assert reset_context["draft"] is None
    assert reset_context["experiment_traces"] == []
    assert reset_context["latest_task"] is None
    assert reset_context["head_revision_id"] == "r000"
    assert reset_context["head_parent_revision_id"] is None
    assert reset_context["head_canonical_diff"] == []
    assert reset_context["editing_locked"] is False


def test_session_limit_evicts_idle_lru_and_deletes_its_generation(tmp_path) -> None:
    manager = SessionManager(
        root=tmp_path,
        service_factory=lambda path: AssetAutopsyService(
            path, runner=WorkbenchRunner()
        ),
        max_sessions=2,
    )
    app = create_workbench_app(manager=manager, frontend_dir=tmp_path / "missing")
    first, second, third = TestClient(app), TestClient(app), TestClient(app)
    _context(first)
    first_id = first.cookies.get(SESSION_COOKIE)
    assert first_id is not None
    first_root = manager.sessions[first_id].data_root
    _context(second)
    _context(third)

    assert len(manager.sessions) == 2
    assert first_id not in manager.sessions
    assert not first_root.exists()


def test_reset_reinitializes_same_object_for_an_already_queued_request(
    tmp_path,
) -> None:
    manager = _manager(tmp_path)

    async def scenario() -> None:
        async with manager.lease(None) as (session_id, session, _created):
            previous_root = session.data_root

            async def queued_request():
                async with manager.lease(session_id) as (
                    _queued_id,
                    queued_session,
                    _queued_created,
                ):
                    return queued_session, queued_session.service

            queued = asyncio.create_task(queued_request())
            await asyncio.sleep(0)
            assert session.active_requests == 2
            manager.reset(session_id, session)
            replacement_service = session.service

        queued_session, queued_service = await queued
        assert queued_session is session
        assert queued_service is replacement_service
        assert not previous_root.exists()

    asyncio.run(scenario())


def test_session_limit_rejects_new_session_when_every_session_is_busy(
    tmp_path,
) -> None:
    manager = SessionManager(
        root=tmp_path,
        service_factory=lambda path: AssetAutopsyService(
            path, runner=WorkbenchRunner()
        ),
        max_sessions=1,
    )

    async def scenario() -> None:
        async with manager.lease(None):
            with pytest.raises(WorkbenchError) as caught:
                async with manager.lease(None):
                    pass
            assert caught.value.code == "SESSION_CAPACITY_REACHED"

    asyncio.run(scenario())


def test_draft_is_not_ledger_state_and_revision_requires_session_evidence(
    tmp_path,
) -> None:
    manager = _manager(tmp_path)
    client = TestClient(
        create_workbench_app(manager=manager, frontend_dir=tmp_path / "missing")
    )
    context = _context(client)
    session = next(iter(manager.sessions.values()))
    events_before = len(session.service.store.ledger_events(CASE_ID))

    assert (
        client.post("/api/tools/set_draft_patch", json=_draft(context)).status_code
        == 200
    )
    assert len(session.service.store.ledger_events(CASE_ID)) == events_before
    assert _context(client)["head_revision_id"] == "r000"

    missing = client.post(
        "/api/tools/create_revision_from_draft",
        json={
            "basis_hypothesis_id": "hyp_missing",
            "basis_experiment_run_id": "run_missing",
            "rationale": "Use evidence from the axis experiment.",
            "expected_effect": {
                "scenario_id": "public_center",
                "predicates": [
                    {"metric": "final_target_error_m", "op": "lt", "value": 0.02}
                ],
            },
        },
    )
    assert missing.status_code == 409
    assert missing.json()["error"]["code"] == "SESSION_EXPERIMENT_REQUIRED"

    result = _create_repaired_revision(client, context)
    assert "trace" not in result
    full_trace = client.get(f"/api/traces/{result['run_id']}")
    assert full_trace.status_code == 200
    assert len(full_trace.json()["rows"]) == 256
    trace = full_trace.json()
    shared = _context(client)
    assert shared["latest_task"]["revision_id"] == "r000"
    assert shared["latest_task"]["result"] == "fail"
    assert shared["experiment_traces"] == [
        {
            "run_id": result["run_id"],
            "revision_id": "r000",
            "asset_sha256": context["head_asset_sha256"],
            "signals": sorted(trace["rows"][0]["values"]),
            "row_count": len(trace["rows"]),
            "start_time_s": trace["rows"][0]["time_s"],
            "end_time_s": trace["rows"][-1]["time_s"],
        }
    ]
    compact = client.post(
        "/api/tools/query_trace",
        json={"run_id": result["run_id"], "operation": "sample", "count": 4},
    )
    assert compact.status_code == 200
    assert len(compact.json()["result"]["rows"]) == 4

    revised = _context(client)
    assert revised["head_revision_id"] == "r001"
    assert revised["head_parent_revision_id"] == "r000"
    assert revised["head_canonical_diff"] == [
        {
            "target": "joint_b",
            "attribute": "axis",
            "before": "0 0 1",
            "after": "0 1.0 0",
        }
    ]
    assert revised["draft"] is None


def test_context_and_design_context_expose_only_current_evidence(tmp_path) -> None:
    client = TestClient(
        create_workbench_app(
            manager=_manager(tmp_path), frontend_dir=tmp_path / "missing"
        )
    )

    context = _context(client)
    _assert_public_context_shape(context)
    assert context["head_revision_id"] == "r000"
    assert context["head_parent_revision_id"] is None
    assert context["head_canonical_diff"] == []

    tool_response = client.post("/api/tools/get_design_context", json={})
    assert tool_response.status_code == 200
    tool_context = tool_response.json()["result"]
    _assert_public_context_shape(tool_context)
    assert "revision_history" not in tool_context["case"]
    assert "event_tail" not in tool_context["case"]
    for removed in (
        "feedback",
        "rejections",
        "accepted",
        "accept_ticket_digest",
    ):
        assert removed not in tool_context


def test_accept_reject_and_feedback_surfaces_do_not_exist(tmp_path) -> None:
    client = TestClient(
        create_workbench_app(
            manager=_manager(tmp_path), frontend_dir=tmp_path / "missing"
        )
    )

    assert client.post("/api/accept", json={}).status_code == 404
    assert client.post("/api/reject", json={}).status_code == 404
    feedback = client.post("/api/tools/record_design_feedback", json={})
    assert feedback.status_code == 404
    assert feedback.json()["error"]["code"] == "UNKNOWN_TOOL"


def test_qualification_locks_edits_without_leaking_ticket_and_keeps_evidence_readable(
    tmp_path,
) -> None:
    manager = _manager(tmp_path)
    client = TestClient(
        create_workbench_app(manager=manager, frontend_dir=tmp_path / "missing")
    )

    verified = _qualify_repaired_revision(client)
    session = next(iter(manager.sessions.values()))
    assert session.promotion_ticket is not None
    assert verified["revision_id"] == session.promotion_ticket.revision_id
    assert "ticket_digest" not in str(verified)

    locked = _context(client)
    _assert_public_context_shape(locked)
    assert locked["editing_locked"] is True
    assert locked["latest_task"]["revision_id"] == verified["revision_id"]
    assert locked["latest_task"]["result"] == "pass"
    assert locked["latest_task"]["behavior_diff"]["verdict"] == "public_pass"
    assert "ticket_digest" not in str(locked)

    inspect = client.post(
        "/api/tools/inspect_design",
        json={"revision_id": locked["head_revision_id"], "view": "both"},
    )
    assert inspect.status_code == 200
    trace_id = next(iter(session.traces))
    assert client.get(f"/api/traces/{trace_id}").status_code == 200
    query = client.post(
        "/api/tools/query_trace",
        json={"run_id": trace_id, "operation": "sample", "count": 4},
    )
    assert query.status_code == 200

    edit = client.post("/api/tools/set_draft_patch", json=_draft(locked))
    assert edit.status_code == 409
    assert edit.json()["error"] == {
        "code": "EDITING_LOCKED",
        "message": "Qualification is complete. Reset this session before editing.",
    }


def test_failed_qualification_locks_edits_and_reset_starts_a_fresh_attempt(
    tmp_path,
) -> None:
    manager = _manager(tmp_path, fail_hidden_qualification=True)
    client = TestClient(
        create_workbench_app(manager=manager, frontend_dir=tmp_path / "missing")
    )
    initial = _context(client)
    _create_repaired_revision(client, initial)
    repaired = _context(client)
    public_task = client.post(
        "/api/tools/run_task",
        json={
            "case_id": repaired["case"]["case_id"],
            "revision_id": repaired["head_revision_id"],
            "scenario_id": "public_center",
            "capture": "metrics",
        },
    )
    assert public_task.status_code == 200
    assert public_task.json()["result"]["result"] == "pass"

    draft = {
        "base_revision_id": repaired["head_revision_id"],
        "expected_base_sha256": repaired["head_asset_sha256"],
        "patch": {
            "target": {"kind": "joint", "name": "joint_b"},
            "attribute": "axis",
            "expected_old_value": [0.0, 1.0, 0.0],
            "new_value": [0.0, 0.0, 1.0],
        },
    }
    assert client.post("/api/tools/set_draft_patch", json=draft).status_code == 200
    assert _context(client)["draft"] is not None

    verified = client.post(
        "/api/tools/verify_revision",
        json={
            "case_id": repaired["case"]["case_id"],
            "revision_id": repaired["head_revision_id"],
            "expected_asset_sha256": repaired["head_asset_sha256"],
        },
    )

    assert verified.status_code == 200
    result = verified.json()["result"]
    assert "promotion_ticket" not in result
    assert result["qualified"] is False
    assert result["editing_locked"] is True
    assert result["qualification_state"] == "failed"
    session = next(iter(manager.sessions.values()))
    assert session.promotion_ticket is None
    assert session.qualification_terminal is True

    failed = _context(client)
    assert failed["case"]["qualification_state"] == "failed"
    assert failed["editing_locked"] is True
    assert failed["draft"] is None
    for path, arguments in (
        ("set_draft_patch", draft),
        ("create_revision_from_draft", {}),
    ):
        edit = client.post(f"/api/tools/{path}", json=arguments)
        assert edit.status_code == 409
        assert edit.json()["error"] == {
            "code": "EDITING_LOCKED",
            "message": "Qualification is complete. Reset this session before editing.",
        }

    assert client.post("/api/reset").status_code == 200
    fresh = _context(client)
    assert fresh["case"]["qualification_state"] == "unused"
    assert fresh["head_revision_id"] == "r000"
    assert fresh["editing_locked"] is False
    assert session.qualification_terminal is False
    assert (
        client.post("/api/tools/set_draft_patch", json=_draft(fresh)).status_code == 200
    )


def test_qualified_reset_starts_a_fresh_editable_attempt(tmp_path) -> None:
    manager = _manager(tmp_path)
    client = TestClient(
        create_workbench_app(manager=manager, frontend_dir=tmp_path / "missing")
    )
    _qualify_repaired_revision(client)
    session_id = client.cookies.get(SESSION_COOKIE)
    assert session_id is not None
    session = manager.sessions[session_id]
    previous_root = session.data_root
    previous_trace_ids = tuple(session.traces)
    assert previous_trace_ids

    response = client.post("/api/reset")

    assert response.status_code == 200
    assert manager.sessions[session_id] is session
    assert session.data_root != previous_root
    assert not previous_root.exists()
    assert session.promotion_ticket is None
    fresh = _context(client)
    _assert_public_context_shape(fresh)
    assert fresh["head_revision_id"] == "r000"
    assert fresh["head_parent_revision_id"] is None
    assert fresh["head_canonical_diff"] == []
    assert fresh["case"]["qualification_state"] == "unused"
    assert fresh["editing_locked"] is False
    assert fresh["draft"] is None
    assert fresh["experiment_traces"] == []
    assert fresh["latest_task"] is None
    for run_id in previous_trace_ids:
        assert client.get(f"/api/traces/{run_id}").status_code == 404

    assert (
        client.post("/api/tools/set_draft_patch", json=_draft(fresh)).status_code == 200
    )


def test_reset_commits_when_old_generation_cleanup_fails(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path)
    client = TestClient(
        create_workbench_app(manager=manager, frontend_dir=tmp_path / "missing")
    )
    _context(client)
    session = next(iter(manager.sessions.values()))
    previous_root = session.data_root

    def fail_cleanup(data_root) -> None:
        assert data_root == previous_root
        raise OSError("simulated stale-generation cleanup failure")

    monkeypatch.setattr(manager, "_delete_generation", fail_cleanup)
    response = client.post("/api/reset")

    assert response.status_code == 200
    assert previous_root.exists()
    assert session.data_root != previous_root
    fresh = _context(client)
    assert fresh["head_revision_id"] == "r000"
    assert fresh["editing_locked"] is False
