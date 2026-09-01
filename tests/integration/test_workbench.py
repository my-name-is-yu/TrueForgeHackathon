from __future__ import annotations

import asyncio
import json
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
                error = 0.0 if repaired or requested.label != "public_center" else 0.1
                speed = 0.0 if repaired or requested.label != "public_center" else 0.1
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


def _manager(tmp_path) -> SessionManager:
    return SessionManager(
        root=tmp_path,
        service_factory=lambda path: AssetAutopsyService(
            path, runner=WorkbenchRunner()
        ),
    )


def _context(client: TestClient) -> dict:
    response = client.get("/api/context")
    assert response.status_code == 200
    return response.json()


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
    ticket = verified.json()["result"]["promotion_ticket"]
    assert ticket is not None
    return ticket


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
    feedback = first.post(
        "/api/tools/record_design_feedback",
        json={
            "revision_id": first_context["head_revision_id"],
            "asset_sha256": first_context["head_asset_sha256"],
            "feedback": "Prefer a calmer end-effector settle.",
        },
    )
    assert feedback.status_code == 200

    assert _context(first)["draft"] is not None
    assert len(_context(first)["feedback"]) == 1
    assert _context(first)["rejections"] == []
    assert _context(first)["experiment_traces"] == []
    assert _context(first)["latest_task"] is None
    assert _context(second)["draft"] is None
    assert _context(second)["feedback"] == []
    assert first_context["head_asset_sha256"] == second_context["head_asset_sha256"]

    reset = first.post("/api/reset")
    assert reset.status_code == 200
    assert manager.sessions[first_session_id] is first_session
    assert first_session.data_root != previous_root
    assert first_session.data_root.is_dir()
    assert not previous_root.exists()
    reset_context = _context(first)
    assert reset_context["draft"] is None
    assert reset_context["feedback"] == []
    assert reset_context["rejections"] == []
    assert reset_context["experiment_traces"] == []
    assert reset_context["latest_task"] is None
    assert reset_context["head_revision_id"] == "r000"


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

    assert _context(client)["head_revision_id"] == "r001"
    assert _context(client)["draft"] is None


def test_feedback_rejects_stale_revision_and_accept_is_human_only(tmp_path) -> None:
    manager = _manager(tmp_path)
    app = create_workbench_app(manager=manager, frontend_dir=tmp_path / "missing")
    client = TestClient(app)
    context = _context(client)

    stale = client.post(
        "/api/tools/record_design_feedback",
        json={
            "revision_id": "r001",
            "asset_sha256": context["head_asset_sha256"],
            "feedback": "This targets the wrong revision.",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "STALE_FEEDBACK_TARGET"

    unknown_tool = client.post(
        "/api/tools/accept_revision", json={"ticket_digest": "0" * 64}
    )
    assert unknown_tool.status_code == 404
    ticket = _qualify_repaired_revision(client)
    locked = _context(client)
    assert locked["editing_locked"] is True

    wrong = client.post("/api/accept", json={"ticket_digest": "0" * 64})
    assert wrong.status_code == 409
    accepted = client.post(
        "/api/accept", json={"ticket_digest": ticket["ticket_digest"]}
    )
    assert accepted.status_code == 200
    assert accepted.json()["accepted"] is True
    assert _context(client)["accepted"] is True


def test_reject_preserves_feedback_and_starts_a_fresh_attempt(tmp_path) -> None:
    manager = _manager(tmp_path)
    client = TestClient(
        create_workbench_app(manager=manager, frontend_dir=tmp_path / "missing")
    )
    ticket = _qualify_repaired_revision(client)
    session_id = client.cookies.get(SESSION_COOKIE)
    assert session_id is not None
    session = manager.sessions[session_id]
    previous_root = session.data_root
    previous_trace_ids = tuple(session.traces)
    assert previous_trace_ids
    qualified = _context(client)
    assert qualified["latest_task"]["revision_id"] == ticket["revision_id"]
    assert qualified["latest_task"]["result"] == "pass"
    assert qualified["latest_task"]["behavior_diff"]["verdict"] == "public_pass"

    response = client.post(
        "/api/reject",
        json={
            "ticket_digest": ticket["ticket_digest"],
            "feedback": "Keep the stable motion but make the final approach less abrupt.",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "rejected": True,
        "revision_id": ticket["revision_id"],
        "asset_sha256": ticket["asset_sha256"],
    }
    assert manager.sessions[session_id] is session
    assert session.data_root != previous_root
    assert not previous_root.exists()
    fresh = _context(client)
    assert fresh["head_revision_id"] == "r000"
    assert fresh["case"]["qualification_state"] == "unused"
    assert fresh["editing_locked"] is False
    assert fresh["accepted"] is False
    assert fresh["accept_ticket_digest"] is None
    assert fresh["draft"] is None
    assert fresh["feedback"] == []
    assert fresh["experiment_traces"] == []
    assert fresh["latest_task"] is None
    assert fresh["rejections"] == [
        {
            "revision_id": ticket["revision_id"],
            "asset_sha256": ticket["asset_sha256"],
            "feedback": "Keep the stable motion but make the final approach less abrupt.",
        }
    ]
    for run_id in previous_trace_ids:
        assert client.get(f"/api/traces/{run_id}").status_code == 404

    _create_repaired_revision(client, fresh)
    continued = _context(client)
    assert continued["head_revision_id"] == "r001"
    assert continued["rejections"] == fresh["rejections"]
    assert continued["experiment_traces"]

    reset = client.post("/api/reset")
    assert reset.status_code == 200
    assert _context(client)["rejections"] == []


def test_reject_commits_when_old_generation_cleanup_fails(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path)
    client = TestClient(
        create_workbench_app(manager=manager, frontend_dir=tmp_path / "missing")
    )
    ticket = _qualify_repaired_revision(client)
    session_id = client.cookies.get(SESSION_COOKIE)
    assert session_id is not None
    session = manager.sessions[session_id]
    previous_root = session.data_root

    def fail_cleanup(data_root) -> None:
        assert data_root == previous_root
        raise OSError("simulated stale-generation cleanup failure")

    monkeypatch.setattr(manager, "_delete_generation", fail_cleanup)
    response = client.post(
        "/api/reject",
        json={
            "ticket_digest": ticket["ticket_digest"],
            "feedback": "Keep this rejection even if stale-file cleanup fails.",
        },
    )

    assert response.status_code == 200
    assert previous_root.exists()
    assert session.data_root != previous_root
    fresh = _context(client)
    assert fresh["head_revision_id"] == "r000"
    assert fresh["editing_locked"] is False
    assert fresh["accept_ticket_digest"] is None
    assert fresh["rejections"] == [
        {
            "revision_id": ticket["revision_id"],
            "asset_sha256": ticket["asset_sha256"],
            "feedback": "Keep this rejection even if stale-file cleanup fails.",
        }
    ]


def test_accept_rejects_another_sessions_ticket_digest(tmp_path) -> None:
    app = create_workbench_app(
        manager=_manager(tmp_path), frontend_dir=tmp_path / "missing"
    )
    first = TestClient(app)
    second = TestClient(app)

    first_ticket = _qualify_repaired_revision(first)
    second_ticket = _qualify_repaired_revision(second)

    assert first_ticket["ticket_id"] != second_ticket["ticket_id"]
    assert first_ticket["ticket_digest"] != second_ticket["ticket_digest"]
    rejected = first.post(
        "/api/accept",
        content=json.dumps({"ticket_digest": second_ticket["ticket_digest"]}),
        headers={"content-type": "text/plain"},
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "INVALID_PROMOTION_TICKET"
    assert _context(first)["accepted"] is False


def test_reject_is_human_only_and_rejects_invalid_requests(tmp_path) -> None:
    manager = _manager(tmp_path)
    app = create_workbench_app(manager=manager, frontend_dir=tmp_path / "missing")
    first = TestClient(app)
    second = TestClient(app)
    first_ticket = _qualify_repaired_revision(first)
    second_ticket = _qualify_repaired_revision(second)
    first_session_id = first.cookies.get(SESSION_COOKIE)
    assert first_session_id is not None
    first_root = manager.sessions[first_session_id].data_root

    unknown_tool = first.post(
        "/api/tools/reject_revision",
        json={
            "ticket_digest": first_ticket["ticket_digest"],
            "feedback": "This must remain human-only.",
        },
    )
    assert unknown_tool.status_code == 404

    cross_session = first.post(
        "/api/reject",
        json={
            "ticket_digest": second_ticket["ticket_digest"],
            "feedback": "This ticket belongs to another session.",
        },
    )
    assert cross_session.status_code == 409
    assert cross_session.json()["error"]["code"] == "INVALID_PROMOTION_TICKET"
    unchanged = _context(first)
    assert unchanged["editing_locked"] is True
    assert unchanged["rejections"] == []
    assert manager.sessions[first_session_id].data_root == first_root

    malformed = first.post(
        "/api/reject",
        content="{",
        headers={"content-type": "application/json"},
    )
    assert malformed.status_code == 422
    assert malformed.json()["rejected"] is False
    assert malformed.json()["error"]["code"] == "INVALID_ARGUMENTS"

    blank = first.post(
        "/api/reject",
        json={"ticket_digest": first_ticket["ticket_digest"], "feedback": "   "},
    )
    assert blank.status_code == 422
    assert blank.json()["error"]["code"] == "INVALID_ARGUMENTS"

    accepted = first.post(
        "/api/accept", json={"ticket_digest": first_ticket["ticket_digest"]}
    )
    assert accepted.status_code == 200
    after_accept = first.post(
        "/api/reject",
        json={
            "ticket_digest": first_ticket["ticket_digest"],
            "feedback": "Accepted revisions cannot be rejected afterward.",
        },
    )
    assert after_accept.status_code == 409
    assert after_accept.json()["error"]["code"] == "INVALID_PROMOTION_TICKET"
    final = _context(first)
    assert final["accepted"] is True
    assert final["rejections"] == []


def test_accept_rejects_malformed_json_with_structured_error(tmp_path) -> None:
    client = TestClient(
        create_workbench_app(
            manager=_manager(tmp_path), frontend_dir=tmp_path / "missing"
        )
    )

    response = client.post(
        "/api/accept",
        content="{",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["accepted"] is False
    assert response.json()["error"]["code"] == "INVALID_ARGUMENTS"
