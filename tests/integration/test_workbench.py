from __future__ import annotations

import xml.etree.ElementTree as ET

from starlette.testclient import TestClient

from asset_autopsy.fixture import CASE_ID, clean_end_effector_position
from asset_autopsy.runner import RunRecord, SegmentRecord
from asset_autopsy.service import AssetAutopsyService
from asset_autopsy.workbench import SessionManager, create_workbench_app


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


def test_sessions_are_isolated_and_reset_discards_temporary_state(tmp_path) -> None:
    manager = _manager(tmp_path)
    app = create_workbench_app(manager=manager, frontend_dir=tmp_path / "missing")
    first = TestClient(app)
    second = TestClient(app)
    first_context = _context(first)
    second_context = _context(second)

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
    assert _context(second)["draft"] is None
    assert _context(second)["feedback"] == []
    assert first_context["head_asset_sha256"] == second_context["head_asset_sha256"]

    reset = first.post("/api/reset")
    assert reset.status_code == 200
    reset_context = _context(first)
    assert reset_context["draft"] is None
    assert reset_context["feedback"] == []
    assert reset_context["head_revision_id"] == "r000"


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
