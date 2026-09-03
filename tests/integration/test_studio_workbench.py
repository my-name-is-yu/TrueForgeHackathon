from __future__ import annotations

import asyncio
import copy
import hashlib
import time

import pytest
from starlette.requests import ClientDisconnect, Request
from starlette.testclient import TestClient

from asset_autopsy.workbench import SessionManager, create_workbench_app
import character_robot.workbench as workbench_module
from character_robot.project_store import ProjectStore
from character_robot.schemas import (
    TOOL_NAMES,
    CreateRevisionFromDraftInput,
    GetStudioContextInput,
    ReviseDesignDraftInput,
    SetDesignDraftInput,
    SetIdentityEdit,
)
from character_robot.service import CharacterRobotService
from character_robot.workbench import STUDIO_SESSION_COOKIE, StudioSessionManager


def _spec_payload() -> dict[str, object]:
    expressions = [
        "neutral",
        "happy",
        "listening",
        "thinking",
        "delighted",
        "sleepy",
    ]

    def scenario(
        scenario_id: str,
        expression: str,
        *,
        left: float = 0.0,
        right: float = 0.0,
        pan: float = 0.0,
        tilt: float = 0.0,
    ) -> dict[str, object]:
        return {
            "scenario_id": scenario_id,
            "duration_ms": 1200,
            "keyframes": [
                {"at_ms": 0, "face_expression": "neutral"},
                {
                    "at_ms": 600,
                    "face_expression": expression,
                    "wheel_left": left,
                    "wheel_right": right,
                    "head_pan_deg": pan,
                    "head_tilt_deg": tilt,
                },
            ],
        }

    return {
        "identity": {
            "name": "Pip",
            "role": "indoor guide",
            "motif": "duck",
            "design_brief": "A shy but curious small duck-shaped companion robot.",
        },
        "hardware_profile_id": "m5-cores3-goplus2/v1",
        "appearance": {
            "primary_color": "#F2C94C",
            "secondary_color": "#FFF7D6",
            "accent_color": "#F2994A",
            "eye_color": "#111111",
            "finish": "matte",
            "style_tags": ["soft", "friendly"],
        },
        "morphology": {
            "nodes": [
                {
                    "kind": "rounded_solid",
                    "node_id": "body",
                    "role": "chassis_shell",
                    "label": "rounded body",
                    "size_mm": {"x": 100.0, "y": 80.0, "z": 58.0},
                    "corner_radius_mm": 18.0,
                },
                {
                    "kind": "loft",
                    "node_id": "head",
                    "role": "head_shell",
                    "label": "duck head",
                    "attachment": {
                        "parent_node_id": "body",
                        "parent_anchor": "neck_mount",
                    },
                    "sections": [
                        {
                            "z_mm": -20.0,
                            "radius_x_mm": 38.0,
                            "radius_y_mm": 33.0,
                        },
                        {
                            "z_mm": 20.0,
                            "radius_x_mm": 34.0,
                            "radius_y_mm": 31.0,
                        },
                    ],
                },
                {
                    "kind": "rounded_solid",
                    "node_id": "beak",
                    "role": "beak",
                    "label": "short round beak",
                    "attachment": {
                        "parent_node_id": "head",
                        "parent_anchor": "face",
                        "translation_mm": {"x": 0.0, "y": -4.0, "z": -2.0},
                    },
                    "size_mm": {"x": 34.0, "y": 20.0, "z": 14.0},
                    "corner_radius_mm": 6.0,
                },
                {
                    "kind": "rounded_solid",
                    "node_id": "wing_left",
                    "role": "wing",
                    "label": "left lowered wing",
                    "attachment": {
                        "parent_node_id": "body",
                        "parent_anchor": "left_side",
                        "translation_mm": {"x": -2.0, "y": 0.0, "z": -5.0},
                        "rotation_deg": {"x": 0.0, "y": 18.0, "z": 0.0},
                    },
                    "size_mm": {"x": 12.0, "y": 46.0, "z": 28.0},
                    "corner_radius_mm": 5.0,
                },
                {
                    "kind": "mirror",
                    "node_id": "wing_right",
                    "role": "wing",
                    "label": "right lowered wing",
                    "source_node_id": "wing_left",
                    "plane": "x",
                },
            ]
        },
        "personality": {
            "curiosity": 0.8,
            "boldness": 0.2,
            "energy": 0.4,
            "sociability": 0.7,
            "voice_style": "shy",
            "motion_style": "careful",
        },
        "face": {
            "default_expression": "neutral",
            "supported_expressions": expressions,
        },
        "behavior": {
            "scenarios": [
                scenario("idle", "neutral", pan=6.0),
                scenario("greet", "happy", left=0.12, right=0.12, pan=18.0),
                scenario("listen", "listening", tilt=12.0),
                scenario("think", "thinking", pan=-16.0, tilt=8.0),
                scenario("delight", "delighted", left=-0.1, right=0.1),
                scenario("sleep", "sleepy", tilt=-12.0),
            ]
        },
        "manufacturing": {
            "material": "pla",
            "nozzle_diameter_mm": 0.4,
            "layer_height_mm": 0.2,
            "minimum_wall_mm": 1.6,
            "fit_clearance_mm": 0.3,
            "printer_volume_mm": {"x": 250.0, "y": 250.0, "z": 250.0},
        },
        "constraints": {
            "maximum_dimensions_mm": {"x": 240.0, "y": 220.0, "z": 240.0},
            "maximum_mass_g": 1200.0,
            "maximum_speed_m_s": 0.25,
            "indoor_only": True,
            "low_voltage_only": True,
        },
        "versions": {
            "schema_version": "character-robot/v1",
            "compiler": "character-cad-v1",
            "catalog": "hardware-catalog-v1",
            "firmware_runtime": "character-runtime-v1",
        },
    }


def _app(tmp_path):
    frontend = tmp_path / "frontend"
    (frontend / "assets").mkdir(parents=True, exist_ok=True)
    (frontend / "index.html").write_text(
        "<!doctype html><title>Combined workbench</title>", encoding="utf-8"
    )
    return create_workbench_app(
        manager=SessionManager(root=tmp_path / "autopsy-sessions"),
        studio_manager=StudioSessionManager(root=tmp_path / "studio-sessions"),
        frontend_dir=frontend,
    )


def _storage_mode_app(tmp_path, *, durable: bool):
    frontend = tmp_path / "frontend"
    (frontend / "assets").mkdir(parents=True, exist_ok=True)
    (frontend / "index.html").write_text("<!doctype html>", encoding="utf-8")

    def create_service(path):
        return CharacterRobotService(
            data_root=path,
            cad_compiler=None,
            project_store=(
                ProjectStore(path / "character-project.sqlite3") if durable else None
            ),
        )

    return create_workbench_app(
        manager=SessionManager(root=tmp_path / "autopsy-sessions"),
        studio_manager=StudioSessionManager(
            root=tmp_path / "studio-sessions" if durable else None,
            service_factory=create_service,
        ),
        frontend_dir=frontend,
    )


def _tool(client: TestClient, name: str, payload: dict[str, object]) -> dict:
    response = client.post(f"/api/studio/v1/tools/{name}", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    return body["result"]


def test_studio_http_flow_uses_real_cad_and_keeps_artifacts_in_session(
    tmp_path,
) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        definitions = client.get("/api/studio/v1/tool-definitions")
        assert definitions.status_code == 200
        assert [tool["name"] for tool in definitions.json()] == list(TOOL_NAMES)
        assert len(definitions.json()) == 8
        assert all(isinstance(tool["inputSchema"], dict) for tool in definitions.json())
        assert all(
            tool["annotations"] == {"readOnlyHint": False}
            for tool in definitions.json()
        )

        blank = client.get("/api/studio/v1/context")
        assert blank.status_code == 200
        assert client.cookies.get(STUDIO_SESSION_COOKIE)
        context = blank.json()
        assert context["head_revision_id"] is None
        assert context["current_spec"] is None
        assert context["draft"] is None
        assert len(context["hardware_profiles"]) == 2

        draft = _tool(
            client,
            "set_design_draft",
            {"expected_revision": None, "spec": _spec_payload()},
        )
        preview = draft["preview_artifact"]
        assert preview["kind"] == "glb"
        assert preview["byte_size"] > 0

        artifact = client.get(f"/api/studio/v1/artifacts/{preview['sha256']}")
        assert artifact.status_code == 200
        assert hashlib.sha256(artifact.content).hexdigest() == preview["sha256"]
        assert artifact.headers["content-type"] == "model/gltf-binary"
        assert artifact.headers["x-content-type-options"] == "nosniff"
        assert 'filename="preview.glb"' in artifact.headers["content-disposition"]

        committed = _tool(
            client,
            "create_revision_from_draft",
            {
                "expected_revision": None,
                "draft_hash": draft["draft_hash"],
                "note": "First complete duck showcase revision.",
            },
        )
        assert committed["head_revision_id"] == "r000"
        assert committed["revision"]["parent_revision_id"] is None

        validated = _tool(
            client,
            "validate_design",
            {"target": {"kind": "revision", "revision_id": "r000"}},
        )
        assert validated["report"]["passed"] is True
        assert validated["report"]["evidence_level"] == "digital_checks_passed"

        prepared = _tool(
            client,
            "prepare_build_pack",
            {
                "revision_id": "r000",
                "expected_spec_hash": committed["revision"]["spec_hash"],
            },
        )
        assert prepared["status"] == "experimental_ready"
        assert prepared["human_action_required"] is True
        assert prepared["manifest"]["download_requires_human_action"] is True
        assert {item["kind"] for item in prepared["manifest"]["artifacts"]} == {
            "glb",
            "step",
            "stl",
            "3mf",
            "spec_json",
            "bom_json",
            "wiring_json",
            "firmware_config_json",
            "assembly_markdown",
            "validation_json",
            "runtime_bundle_zip",
            "calibration_json",
            "physical_evidence_json",
            "mjcf",
            "simulation_json",
            "project_snapshot_json",
            "build_pack_zip",
        }

        with TestClient(app) as other_client:
            isolated = other_client.get(f"/api/studio/v1/artifacts/{preview['sha256']}")
            assert isolated.status_code == 404
            assert isolated.json()["error"]["code"] == "ARTIFACT_NOT_FOUND"


def test_durable_studio_reopens_and_regenerates_identical_build_pack(
    tmp_path,
) -> None:
    first_app = _app(tmp_path)
    with TestClient(first_app) as first:
        blank = first.get("/api/studio/v1/context")
        session_id = blank.cookies[STUDIO_SESSION_COOKIE]
        assert blank.json()["storage_mode"] == "durable"
        draft = _tool(
            first,
            "set_design_draft",
            {"expected_revision": None, "spec": _spec_payload()},
        )
        committed = _tool(
            first,
            "create_revision_from_draft",
            {
                "expected_revision": None,
                "draft_hash": draft["draft_hash"],
                "note": "Durable beta revision.",
            },
        )
        prepared = _tool(
            first,
            "prepare_build_pack",
            {
                "revision_id": "r000",
                "expected_spec_hash": committed["revision"]["spec_hash"],
            },
        )
        first_manifest = prepared["manifest"]
        first_digests = {
            artifact["kind"]: artifact["sha256"]
            for artifact in first_manifest["artifacts"]
        }

    reopened_app = _app(tmp_path)
    with TestClient(reopened_app) as reopened:
        reopened.cookies.set(STUDIO_SESSION_COOKIE, session_id)
        context = reopened.get("/api/studio/v1/context")
        assert context.status_code == 200
        restored = context.json()
        assert restored["head_revision_id"] == "r000"
        assert restored["draft"]["spec"]["identity"]["name"] == "Pip"
        assert restored["artifact_manifest_count"] == 1
        assert restored["project_generation"] > 0
        preview = restored["current_preview_artifact"]
        assert preview["sha256"] == first_digests["glb"]
        assert (
            reopened.get(f"/api/studio/v1/artifacts/{preview['sha256']}").status_code
            == 200
        )

        regenerated = _tool(
            reopened,
            "prepare_build_pack",
            {
                "revision_id": "r000",
                "expected_spec_hash": committed["revision"]["spec_hash"],
            },
        )
        assert (
            regenerated["manifest"]["manifest_hash"] == first_manifest["manifest_hash"]
        )
        assert {
            artifact["kind"]: artifact["sha256"]
            for artifact in regenerated["manifest"]["artifacts"]
        } == first_digests
        assert (
            reopened.get("/api/studio/v1/context").json()["artifact_manifest_count"]
            == 1
        )


def test_studio_adapter_rejects_invalid_and_unknown_operations(tmp_path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        invalid = client.post(
            "/api/studio/v1/tools/set_design_draft",
            json={"expected_revision": None},
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "INVALID_ARGUMENTS"

        unknown = client.post("/api/studio/v1/tools/run_arbitrary_code", json={})
        assert unknown.status_code == 404
        assert unknown.json()["error"]["code"] == "UNKNOWN_TOOL"

        malformed_digest = client.get("/api/studio/v1/artifacts/not-a-digest")
        assert malformed_digest.status_code == 422
        assert malformed_digest.json()["error"]["code"] == ("INVALID_ARTIFACT_DIGEST")


def test_two_clients_cannot_overwrite_a_newer_shared_draft(tmp_path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as first, TestClient(app) as second:
        blank = first.get("/api/studio/v1/context")
        session_id = blank.cookies[STUDIO_SESSION_COOKIE]
        second.cookies.set(STUDIO_SESSION_COOKIE, session_id)

        created = _tool(
            first,
            "set_design_draft",
            {"expected_revision": None, "spec": _spec_payload()},
        )
        replacement = _spec_payload()
        replacement["identity"]["name"] = "Pip from stale client"

        stale = second.post(
            "/api/studio/v1/tools/set_design_draft",
            json={"expected_revision": None, "spec": replacement},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "STALE_DRAFT"
        assert (
            first.get("/api/studio/v1/context").json()["draft"]["spec"]["identity"][
                "name"
            ]
            == "Pip"
        )

        replaced = _tool(
            second,
            "set_design_draft",
            {
                "expected_revision": None,
                "expected_draft_hash": created["draft_hash"],
                "spec": replacement,
            },
        )
        assert replaced["spec"]["identity"]["name"] == "Pip from stale client"


def test_human_selection_is_session_scoped_and_clears_when_stale_or_reset(
    tmp_path,
) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        draft = _tool(
            client,
            "set_design_draft",
            {"expected_revision": None, "spec": _spec_payload()},
        )
        target = {"kind": "draft", "draft_hash": draft["draft_hash"]}

        selected = client.post(
            "/api/studio/v1/selection",
            json={"target": target, "node_id": "head"},
        )
        assert selected.status_code == 200
        assert selected.json()["selected_node_id"] == "head"
        assert client.get("/api/studio/v1/context").json()["selected_node_id"] == (
            "head"
        )
        tool_context = _tool(client, "get_studio_context", {})
        assert tool_context["selected_node_id"] == "head"

        missing = client.post(
            "/api/studio/v1/selection",
            json={"target": target, "node_id": "missing"},
        )
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "NODE_NOT_FOUND"

        revised_identity = dict(_spec_payload()["identity"])
        revised_identity["name"] = "Pip revised"
        revised = _tool(
            client,
            "revise_design_draft",
            {
                "draft_hash": draft["draft_hash"],
                "edits": [
                    {"kind": "set_identity", "identity": revised_identity},
                ],
            },
        )
        assert revised["draft_hash"] != draft["draft_hash"]
        assert client.get("/api/studio/v1/context").json()["selected_node_id"] is None

        stale = client.post(
            "/api/studio/v1/selection",
            json={"target": target, "node_id": "head"},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "SELECTION_TARGET_STALE"

        current_target = {
            "kind": "draft",
            "draft_hash": revised["draft_hash"],
        }
        cleared = client.post(
            "/api/studio/v1/selection",
            json={"target": current_target, "node_id": None},
        )
        assert cleared.status_code == 200
        assert cleared.json()["selected_node_id"] is None

        assert client.post("/api/studio/v1/reset").status_code == 200
        reset = client.get("/api/studio/v1/context").json()
        assert reset["draft"] is None
        assert reset["selected_node_id"] is None


def test_showcase_journey_keeps_one_draft_through_five_edits_and_profile_switch(
    tmp_path,
) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        started = time.perf_counter()
        draft = _tool(
            client,
            "set_design_draft",
            {"expected_revision": None, "spec": _spec_payload()},
        )
        assert time.perf_counter() - started < 60
        original_body = draft["spec"]["morphology"]["nodes"][0]

        identity = copy.deepcopy(draft["spec"]["identity"])
        identity["name"] = "Pip the careful guide"
        draft = _tool(
            client,
            "revise_design_draft",
            {
                "draft_hash": draft["draft_hash"],
                "edits": [{"kind": "set_identity", "identity": identity}],
            },
        )

        personality = copy.deepcopy(draft["spec"]["personality"])
        personality["boldness"] = 0.1
        personality["motion_style"] = "careful"
        draft = _tool(
            client,
            "revise_design_draft",
            {
                "draft_hash": draft["draft_hash"],
                "edits": [{"kind": "set_personality", "personality": personality}],
            },
        )

        behavior = copy.deepcopy(draft["spec"]["behavior"])
        for scenario in behavior["scenarios"]:
            for keyframe in scenario["keyframes"]:
                keyframe["wheel_left"] *= 0.5
                keyframe["wheel_right"] *= 0.5
                keyframe["head_pan_deg"] *= 0.7
        draft = _tool(
            client,
            "revise_design_draft",
            {
                "draft_hash": draft["draft_hash"],
                "edits": [{"kind": "set_behavior", "behavior": behavior}],
            },
        )

        inspected = _tool(
            client,
            "inspect_design",
            {"target": {"kind": "draft", "draft_hash": draft["draft_hash"]}},
        )
        node_hashes = {
            node["node_id"]: node["node_hash"] for node in inspected["nodes"]
        }
        head = copy.deepcopy(draft["spec"]["morphology"]["nodes"][1])
        for section in head["sections"]:
            section["radius_x_mm"] -= 4.0
            section["radius_y_mm"] -= 3.0
        draft = _tool(
            client,
            "revise_design_draft",
            {
                "draft_hash": draft["draft_hash"],
                "edits": [
                    {
                        "kind": "replace_morphology_node",
                        "node_id": "head",
                        "expected_node_hash": node_hashes["head"],
                        "node": head,
                    }
                ],
            },
        )

        inspected = _tool(
            client,
            "inspect_design",
            {"target": {"kind": "draft", "draft_hash": draft["draft_hash"]}},
        )
        node_hashes = {
            node["node_id"]: node["node_hash"] for node in inspected["nodes"]
        }
        beak = copy.deepcopy(draft["spec"]["morphology"]["nodes"][2])
        beak["size_mm"] = {"x": 28.0, "y": 15.0, "z": 13.0}
        beak["corner_radius_mm"] = 6.0
        draft = _tool(
            client,
            "revise_design_draft",
            {
                "draft_hash": draft["draft_hash"],
                "edits": [
                    {
                        "kind": "replace_morphology_node",
                        "node_id": "beak",
                        "expected_node_hash": node_hashes["beak"],
                        "node": beak,
                    }
                ],
            },
        )

        assert draft["spec"]["identity"]["name"] == "Pip the careful guide"
        assert draft["spec"]["morphology"]["nodes"][0] == original_body
        assert draft["spec"]["morphology"]["nodes"][1] == head
        assert draft["spec"]["morphology"]["nodes"][2] == beak

        before_switch = _tool(
            client,
            "inspect_design",
            {"target": {"kind": "draft", "draft_hash": draft["draft_hash"]}},
        )
        draft = _tool(
            client,
            "revise_design_draft",
            {
                "draft_hash": draft["draft_hash"],
                "edits": [
                    {
                        "kind": "set_hardware_profile",
                        "hardware_profile_id": "pi-zero2wh-crickit-ws2/v1",
                    }
                ],
            },
        )
        after_switch = _tool(
            client,
            "inspect_design",
            {"target": {"kind": "draft", "draft_hash": draft["draft_hash"]}},
        )
        assert before_switch["geometry_sha256"] != after_switch["geometry_sha256"]
        assert before_switch["dimensions_mm"] != after_switch["dimensions_mm"]

        huge_head = copy.deepcopy(
            next(
                node
                for node in draft["spec"]["morphology"]["nodes"]
                if node["node_id"] == "head"
            )
        )
        huge_head["sections"] = [
            {"z_mm": -80.0, "radius_x_mm": 80.0, "radius_y_mm": 80.0},
            {"z_mm": 40.0, "radius_x_mm": 76.0, "radius_y_mm": 76.0},
        ]
        after_switch_hashes = {
            node["node_id"]: node["node_hash"] for node in after_switch["nodes"]
        }
        before_interference = draft["draft_hash"]
        interference = client.post(
            "/api/studio/v1/tools/revise_design_draft",
            json={
                "draft_hash": before_interference,
                "edits": [
                    {
                        "kind": "replace_morphology_node",
                        "node_id": "head",
                        "expected_node_hash": after_switch_hashes["head"],
                        "node": huge_head,
                    }
                ],
            },
        )
        assert interference.status_code == 422
        assert interference.json()["error"]["code"] == ("CAD_HEAD_WHEEL_INTERFERENCE")
        assert "overlaps" in interference.json()["error"]["message"]
        assert client.get("/api/studio/v1/context").json()["draft"]["draft_hash"] == (
            before_interference
        )

        for scenario_id in ("idle", "greet", "listen", "think", "delight"):
            preview = _tool(
                client,
                "preview_scenario",
                {
                    "target": {
                        "kind": "draft",
                        "draft_hash": draft["draft_hash"],
                    },
                    "scenario_id": scenario_id,
                },
            )
            assert preview["scenario_id"] == scenario_id
            assert len(preview["keyframes"]) >= 2

        before_rejection = draft["draft_hash"]
        too_small = copy.deepcopy(draft["spec"]["constraints"])
        too_small["maximum_dimensions_mm"] = {"x": 50.0, "y": 50.0, "z": 50.0}
        rejected = client.post(
            "/api/studio/v1/tools/revise_design_draft",
            json={
                "draft_hash": before_rejection,
                "edits": [{"kind": "set_constraints", "constraints": too_small}],
            },
        )
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] in {
            "CAD_DIMENSION_LIMIT_EXCEEDED",
            "MAXIMUM_DIMENSIONS_EXCEEDED",
        }
        assert client.get("/api/studio/v1/context").json()["draft"]["draft_hash"] == (
            before_rejection
        )

        committed = _tool(
            client,
            "create_revision_from_draft",
            {
                "expected_revision": None,
                "draft_hash": before_rejection,
                "note": "Showcase journey after five semantic refinements.",
            },
        )
        prepared = _tool(
            client,
            "prepare_build_pack",
            {
                "revision_id": "r000",
                "expected_spec_hash": committed["revision"]["spec_hash"],
            },
        )
        assert prepared["status"] == "experimental_ready"
        assert prepared["manifest"]["cad_engine_version"] == "0.11.1"
        assert len(prepared["manifest"]["manifest_hash"]) == 64
        context = client.get("/api/studio/v1/context").json()
        assert any(run["kind"] == "validation" for run in context["recent_runs"])
        assert all(run["duration_ms"] >= 0 for run in context["recent_runs"])


def test_studio_and_autopsy_frontend_routes_coexist(tmp_path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        studio = client.get("/studio")
        autopsy = client.get("/autopsy")

    assert studio.status_code == 200
    assert autopsy.status_code == 200
    assert studio.content == autopsy.content
    assert b"Combined workbench" in studio.content


def test_human_project_import_regenerates_the_exact_build_pack(tmp_path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as source:
        source.get("/api/studio/v1/context")
        draft = _tool(
            source,
            "set_design_draft",
            {"expected_revision": None, "spec": _spec_payload()},
        )
        committed = _tool(
            source,
            "create_revision_from_draft",
            {
                "expected_revision": None,
                "draft_hash": draft["draft_hash"],
                "note": "Portable exact revision.",
            },
        )
        original = _tool(
            source,
            "prepare_build_pack",
            {
                "revision_id": "r000",
                "expected_spec_hash": committed["revision"]["spec_hash"],
            },
        )
        project_artifact = next(
            artifact
            for artifact in original["manifest"]["artifacts"]
            if artifact["kind"] == "project_snapshot_json"
        )
        exported = source.get(f"/api/studio/v1/artifacts/{project_artifact['sha256']}")
        assert exported.status_code == 200

    with TestClient(app) as receiver:
        imported = receiver.post(
            "/api/studio/v1/project-import",
            content=exported.content,
            headers={
                "Content-Type": "application/json",
                "X-Character-Project-Generation": "0",
            },
        )
        assert imported.status_code == 200, imported.text
        assert imported.json()["revision_count"] == 1
        context = receiver.get("/api/studio/v1/context").json()
        assert context["head_revision_id"] == "r000"
        assert context["draft"] is None
        assert context["current_spec"]["identity"]["name"] == "Pip"
        regenerated = _tool(
            receiver,
            "prepare_build_pack",
            {
                "revision_id": "r000",
                "expected_spec_hash": committed["revision"]["spec_hash"],
            },
        )

    assert (
        regenerated["manifest"]["manifest_hash"]
        == original["manifest"]["manifest_hash"]
    )
    assert {
        artifact["kind"]: artifact["sha256"]
        for artifact in regenerated["manifest"]["artifacts"]
    } == {
        artifact["kind"]: artifact["sha256"]
        for artifact in original["manifest"]["artifacts"]
    }


def test_project_import_rejects_an_oversized_json_integer_and_keeps_session_usable(
    tmp_path,
) -> None:
    app = _app(tmp_path)
    oversized_integer = b"9" * 5000

    with TestClient(app) as client:
        response = client.post(
            "/api/studio/v1/project-import",
            content=b'{"schema_version":' + oversized_integer + b"}",
            headers={
                "Content-Type": "application/json",
                "X-Character-Project-Generation": "0",
            },
        )

        assert response.status_code == 422
        assert response.json() == {
            "error": {
                "code": "INVALID_PROJECT_IMPORT",
                "message": "portable project is not valid JSON",
            }
        }
        draft = _tool(
            client,
            "set_design_draft",
            {"expected_revision": None, "spec": _spec_payload()},
        )

    assert draft["draft_hash"]


@pytest.mark.parametrize(
    "content_length",
    [
        str(workbench_module.MAX_PORTABLE_PROJECT_BYTES + 1),
        "9" * 5000,
    ],
)
def test_project_import_rejects_oversized_content_length_without_reading_body(
    tmp_path, monkeypatch, content_length
) -> None:
    app = _app(tmp_path)

    async def fail_if_read(_request):
        pytest.fail("oversized declared body was read")
        yield b""

    monkeypatch.setattr(Request, "stream", fail_if_read)
    with TestClient(app) as client:
        response = client.post(
            "/api/studio/v1/project-import",
            content=b"{}",
            headers={
                "Content-Length": content_length,
                "Content-Type": "application/json",
                "X-Character-Project-Generation": "0",
            },
        )

    assert response.status_code == 413
    assert response.json() == {
        "error": {
            "code": "PROJECT_IMPORT_TOO_LARGE",
            "message": "portable project size is invalid",
        }
    }


def test_project_import_stops_stream_at_the_portable_project_limit(
    tmp_path, monkeypatch
) -> None:
    app = _app(tmp_path)
    read_chunks: list[bytes] = []
    monkeypatch.setattr(workbench_module, "MAX_PORTABLE_PROJECT_BYTES", 5)

    async def oversized_stream(_request):
        for chunk in (b"12345", b"6"):
            read_chunks.append(chunk)
            yield chunk
        pytest.fail("stream was read after exceeding the portable project limit")

    monkeypatch.setattr(Request, "stream", oversized_stream)
    with TestClient(app) as client:
        response = client.post(
            "/api/studio/v1/project-import",
            content=(chunk for chunk in (b"123", b"456", b"789")),
            headers={
                "Content-Type": "application/json",
                "X-Character-Project-Generation": "0",
            },
        )

    assert read_chunks == [b"12345", b"6"]
    assert response.status_code == 413
    assert response.json() == {
        "error": {
            "code": "PROJECT_IMPORT_TOO_LARGE",
            "message": "portable project size is invalid",
        }
    }


def test_portable_project_stream_allows_the_exact_byte_limit(monkeypatch) -> None:
    monkeypatch.setattr(workbench_module, "MAX_PORTABLE_PROJECT_BYTES", 5)
    messages = iter(
        (
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"45", "more_body": False},
        )
    )

    async def receive():
        return next(messages)

    request = Request(
        {"type": "http", "headers": [(b"content-length", b"0005")]}, receive
    )
    content = asyncio.run(workbench_module._read_portable_project_body(request))

    assert content == b"12345"


def test_project_import_holds_upload_admission_while_waiting_for_session(
    tmp_path, monkeypatch
) -> None:
    manager = StudioSessionManager(root=tmp_path)
    monkeypatch.setattr(workbench_module, "MAX_CONCURRENT_UPLOAD_BUFFERS", 1)
    read_uploads: list[str] = []
    first_read = asyncio.Event()

    async def read_body(request: Request) -> bytes:
        upload_id = request.headers["x-upload-id"]
        read_uploads.append(upload_id)
        if upload_id == "first":
            first_read.set()
        return b"{}"

    monkeypatch.setattr(workbench_module, "_read_portable_project_body", read_body)

    async def exercise() -> tuple[tuple[str, ...], list[int]]:
        async with manager.lease(None) as (session_id, session, _created):
            pass
        await session.lock.acquire()
        import_endpoint = next(
            route.endpoint
            for route in workbench_module.create_studio_routes(manager)
            if route.path == "/api/studio/v1/project-import"
        )

        def request(upload_id: str) -> Request:
            async def receive() -> dict[str, object]:
                return {"type": "http.request", "body": b"", "more_body": False}

            return Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/api/studio/v1/project-import",
                    "headers": [
                        (b"x-character-project-generation", b"0"),
                        (b"x-upload-id", upload_id.encode()),
                        (
                            b"cookie",
                            f"{STUDIO_SESSION_COOKIE}={session_id}".encode(),
                        ),
                    ],
                },
                receive,
            )

        first = asyncio.create_task(import_endpoint(request("first")))
        try:
            await asyncio.wait_for(first_read.wait(), timeout=1)
            second = asyncio.create_task(import_endpoint(request("second")))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            observed_while_locked = tuple(read_uploads)
        finally:
            session.lock.release()
        responses = await asyncio.gather(first, second)
        return observed_while_locked, [response.status_code for response in responses]

    observed_while_locked, statuses = asyncio.run(exercise())

    assert observed_while_locked == ("first",)
    assert read_uploads == ["first", "second"]
    assert statuses == [422, 422]


@pytest.mark.parametrize(
    "content_length_headers",
    [
        [("Content-Length", "+1")],
        [("Content-Length", "1"), ("Content-Length", "1")],
    ],
)
def test_project_import_rejects_invalid_content_length_without_reading_body(
    tmp_path, monkeypatch, content_length_headers
) -> None:
    app = _app(tmp_path)

    async def fail_if_read(_request):
        pytest.fail("invalid Content-Length body was read")
        yield b""

    monkeypatch.setattr(Request, "stream", fail_if_read)
    with TestClient(app) as client:
        response = client.post(
            "/api/studio/v1/project-import",
            content=b"{}",
            headers=[
                *content_length_headers,
                ("Content-Type", "application/json"),
                ("X-Character-Project-Generation", "0"),
            ],
        )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "INVALID_CONTENT_LENGTH",
            "message": "Project import requires one valid Content-Length value.",
        }
    }


def test_project_import_rejects_unbounded_generation_without_reading_body(
    tmp_path, monkeypatch
) -> None:
    app = _app(tmp_path)

    async def fail_if_read(_request):
        pytest.fail("invalid expected generation body was read")
        yield b""

    monkeypatch.setattr(Request, "stream", fail_if_read)
    with TestClient(app) as client:
        response = client.post(
            "/api/studio/v1/project-import",
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                "X-Character-Project-Generation": "9" * 5000,
            },
        )

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "EXPECTED_GENERATION_REQUIRED",
            "message": "Project import requires the current Studio generation.",
        }
    }


def test_ephemeral_project_import_rejects_a_generation_read_before_mutation() -> None:
    manager = StudioSessionManager(
        service_factory=lambda path: CharacterRobotService(
            data_root=path,
            cad_compiler=None,
        )
    )

    async def exercise() -> tuple[workbench_module.StudioWorkbenchError, str]:
        async with manager.lease(None) as (session_id, session, _created):
            draft = await session.service.set_design_draft(
                SetDesignDraftInput(expected_revision=None, spec=_spec_payload())
            )
            first_revision = await session.service.create_revision_from_draft(
                CreateRevisionFromDraftInput(
                    expected_revision=None,
                    draft_hash=draft.draft_hash,
                    note="Revision visible when the import dialog opened.",
                )
            )
            exported = session.service._portable_project_bytes("r000")
            preflight_generation = session.service.project_generation

            await session.service.revise_design_draft(
                ReviseDesignDraftInput(
                    draft_hash=first_revision.draft_hash,
                    edits=[
                        SetIdentityEdit(
                            kind="set_identity",
                            identity=draft.spec.identity.model_copy(
                                update={"name": "Pip after import preflight"}
                            ),
                        )
                    ],
                )
            )
            assert session.service.project_generation > preflight_generation

            with pytest.raises(workbench_module.StudioWorkbenchError) as stale:
                manager.import_project(
                    session_id,
                    session,
                    exported,
                    expected_generation=preflight_generation,
                )
            context = await session.service.get_studio_context(GetStudioContextInput())
            assert context.draft is not None
            return stale.value, context.draft.spec.identity.name

    stale, draft_name = asyncio.run(exercise())

    assert stale.code == "STALE_PROJECT"
    assert stale.status_code == 409
    assert draft_name == "Pip after import preflight"


@pytest.mark.parametrize("durable", [False, True], ids=["ephemeral", "durable"])
def test_project_import_advances_generation_and_rejects_reuse(
    tmp_path, durable
) -> None:
    source = CharacterRobotService(
        data_root=tmp_path / "source",
        cad_compiler=None,
    )

    async def export_project() -> bytes:
        draft = await source.set_design_draft(
            SetDesignDraftInput(expected_revision=None, spec=_spec_payload())
        )
        await source.create_revision_from_draft(
            CreateRevisionFromDraftInput(
                expected_revision=None,
                draft_hash=draft.draft_hash,
                note="Portable project for ephemeral import.",
            )
        )
        return source._portable_project_bytes("r000")

    app = _storage_mode_app(tmp_path, durable=durable)
    exported = asyncio.run(export_project())

    with TestClient(app) as client:
        initial = client.get("/api/studio/v1/context")
        assert initial.status_code == 200
        initial_generation = initial.json()["project_generation"]

        imported = client.post(
            "/api/studio/v1/project-import",
            content=exported,
            headers={
                "Content-Type": "application/json",
                "X-Character-Project-Generation": str(initial_generation),
            },
        )
        first_generation = imported.json()["project_generation"]
        imported_again = client.post(
            "/api/studio/v1/project-import",
            content=exported,
            headers={
                "Content-Type": "application/json",
                "X-Character-Project-Generation": str(first_generation),
            },
        )
        current = client.get("/api/studio/v1/context")
        stale = client.post(
            "/api/studio/v1/project-import",
            content=exported,
            headers={
                "Content-Type": "application/json",
                "X-Character-Project-Generation": str(first_generation),
            },
        )

    imported_again_generation = imported_again.json()["project_generation"]
    assert initial_generation == 0
    assert imported.status_code == 200
    assert first_generation > initial_generation
    assert imported_again.status_code == 200
    assert imported_again_generation > first_generation
    assert imported_again_generation >= 2
    assert current.status_code == 200
    assert current.json()["project_generation"] >= imported_again_generation
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "STALE_PROJECT"


@pytest.mark.parametrize("durable", [False, True], ids=["ephemeral", "durable"])
def test_project_reset_advances_generation_and_rejects_the_old_import_header(
    tmp_path, durable
) -> None:
    app = _storage_mode_app(tmp_path, durable=durable)

    with TestClient(app) as client:
        initial = client.get("/api/studio/v1/context")
        assert initial.status_code == 200
        _tool(
            client,
            "set_design_draft",
            {"expected_revision": None, "spec": _spec_payload()},
        )
        before_reset = client.get("/api/studio/v1/context")
        assert before_reset.status_code == 200
        before_generation = before_reset.json()["project_generation"]

        reset = client.post("/api/studio/v1/reset")
        after_reset = client.get("/api/studio/v1/context")
        stale = client.post(
            "/api/studio/v1/project-import",
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                "X-Character-Project-Generation": str(before_generation),
            },
        )

    assert reset.status_code == 200
    assert after_reset.status_code == 200
    assert after_reset.json()["project_generation"] > before_generation
    assert after_reset.json()["draft"] is None
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "STALE_PROJECT"


def test_project_import_maps_client_disconnect_to_typed_response(
    tmp_path, monkeypatch
) -> None:
    app = _app(tmp_path)

    async def disconnected_stream(_request):
        raise ClientDisconnect
        yield b""

    monkeypatch.setattr(Request, "stream", disconnected_stream)
    with TestClient(app) as client:
        response = client.post(
            "/api/studio/v1/project-import",
            content=(chunk for chunk in (b"partial",)),
            headers={
                "Content-Type": "application/json",
                "X-Character-Project-Generation": "0",
            },
        )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "PROJECT_IMPORT_INTERRUPTED",
            "message": "The portable project upload was interrupted.",
        }
    }


def test_interrupted_project_import_keeps_the_previous_generation_restart_visible(
    tmp_path, monkeypatch
) -> None:
    session_root = tmp_path / "studio-sessions"
    manager = StudioSessionManager(root=session_root)

    class SimulatedProcessExit(BaseException):
        pass

    async def interrupt_import() -> tuple[str, object]:
        async with manager.lease(None) as (session_id, session, _created):
            draft = await session.service.set_design_draft(
                SetDesignDraftInput(
                    expected_revision=None,
                    spec=_spec_payload(),
                )
            )
            await session.service.create_revision_from_draft(
                CreateRevisionFromDraftInput(
                    expected_revision=None,
                    draft_hash=draft.draft_hash,
                    note="Durable revision before interrupted import.",
                )
            )
            previous_root = session.data_root
            exported = session.service._portable_project_bytes("r000")
            expected_generation = session.service.project_generation

            def stop_before_restore(_service, _snapshot, *, next_generation):
                assert next_generation == expected_generation + 1
                raise SimulatedProcessExit

            with monkeypatch.context() as patch:
                patch.setattr(
                    CharacterRobotService,
                    "restore_portable_project",
                    stop_before_restore,
                )
                with pytest.raises(SimulatedProcessExit):
                    manager.import_project(
                        session_id,
                        session,
                        exported,
                        expected_generation=expected_generation,
                    )
            return session_id, previous_root

    session_id, previous_root = asyncio.run(interrupt_import())
    assert (
        len([path for path in (session_root / session_id).iterdir() if path.is_dir()])
        == 2
    )

    restarted = StudioSessionManager(root=session_root)

    async def read_restarted_project():
        async with restarted.lease(session_id) as (
            restored_session_id,
            session,
            created,
        ):
            context = await session.service.get_studio_context(GetStudioContextInput())
            return restored_session_id, session.data_root, created, context

    restored_session_id, restored_root, created, context = asyncio.run(
        read_restarted_project()
    )
    assert restored_session_id == session_id
    assert restored_root == previous_root
    assert created is False
    assert context.head_revision_id == "r000"
    assert context.current_spec is not None
    assert context.current_spec.identity.name == "Pip"
