from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

import asset_autopsy.workbench as autopsy_workbench
from character_robot.schemas import TOOL_NAMES
from character_robot.standalone import create_studio_app
from character_robot.workbench import STUDIO_SESSION_COOKIE, StudioSessionManager

from test_studio_workbench import _spec_payload


def _app(tmp_path: Path):
    frontend = tmp_path / "frontend"
    assets = frontend / "assets"
    assets.mkdir(parents=True)
    (frontend / "index.html").write_text(
        "<!doctype html><title>Character Robot Studio</title>",
        encoding="utf-8",
    )
    (assets / "studio.js").write_text("console.log('studio');", encoding="utf-8")
    return create_studio_app(
        manager=StudioSessionManager(root=tmp_path / "studio-sessions"),
        frontend_dir=frontend,
    )


def _tool(client: TestClient, name: str, payload: dict[str, object]) -> dict:
    response = client.post(f"/api/studio/v1/tools/{name}", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    return body["result"]


def test_standalone_serves_studio_only_routes_and_assets(tmp_path: Path) -> None:
    app = _app(tmp_path)
    paths = {route.path for route in app.routes}

    assert "/autopsy" not in paths
    assert "/api/context" not in paths
    assert "/api/tools/{name:str}" not in paths
    assert "/api/traces/{run_id:str}" not in paths
    assert "/api/reset" not in paths
    assert "/" not in paths
    assert not any("mcp" in path.lower() for path in paths)

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/studio").status_code == 200
        assert client.get("/studio/").status_code == 200
        asset = client.get("/assets/studio.js")
        assert asset.status_code == 200
        assert asset.text == "console.log('studio');"

        for path in (
            "/",
            "/autopsy",
            "/autopsy/",
            "/api/context",
            "/api/tools/open_case",
            "/api/traces/trace",
            "/api/reset",
        ):
            assert client.get(path).status_code == 404, path


def test_standalone_without_built_frontend_remains_api_usable(tmp_path: Path) -> None:
    frontend = tmp_path / "incomplete-frontend"
    frontend.mkdir()
    app = create_studio_app(
        manager=StudioSessionManager(root=tmp_path / "studio-sessions"),
        frontend_dir=frontend,
    )

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/studio").status_code == 404
        assert client.get("/assets/studio.js").status_code == 404


def test_standalone_runs_the_complete_eight_tool_v1_http_path(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        definitions = client.get("/api/studio/v1/tool-definitions")
        assert definitions.status_code == 200
        assert [item["name"] for item in definitions.json()] == list(TOOL_NAMES)

        context = client.get("/api/studio/v1/context")
        assert context.status_code == 200
        assert context.json()["head_revision_id"] is None
        assert client.cookies.get(STUDIO_SESSION_COOKIE)
        tool_context = _tool(client, "get_studio_context", {})
        assert tool_context["head_revision_id"] is None

        draft = _tool(
            client,
            "set_design_draft",
            {"expected_revision": None, "spec": _spec_payload()},
        )
        target = {"kind": "draft", "draft_hash": draft["draft_hash"]}
        revised = _tool(
            client,
            "revise_design_draft",
            {
                "draft_hash": draft["draft_hash"],
                "edits": [
                    {
                        "kind": "set_identity",
                        "identity": {
                            **draft["spec"]["identity"],
                            "name": "Pip standalone",
                        },
                    }
                ],
            },
        )
        target["draft_hash"] = revised["draft_hash"]
        inspected = _tool(client, "inspect_design", {"target": target})
        assert inspected["geometry_sha256"]
        scenario = _tool(
            client, "preview_scenario", {"target": target, "scenario_id": "greet"}
        )
        assert scenario["scenario_id"] == "greet"
        validation = _tool(client, "validate_design", {"target": target})
        assert validation["target"] == target
        revision = _tool(
            client,
            "create_revision_from_draft",
            {
                "expected_revision": None,
                "draft_hash": target["draft_hash"],
                "note": "Standalone integration revision.",
            },
        )
        prepared = _tool(
            client,
            "prepare_build_pack",
            {
                "revision_id": revision["revision"]["revision_id"],
                "expected_spec_hash": revision["revision"]["spec_hash"],
            },
        )
        assert prepared["status"] == "experimental_ready"
        assert len(prepared["manifest"]["manifest_hash"]) == 64


def test_standalone_only_persists_character_robot_state(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        client.get("/api/studio/v1/context")
        assert client.cookies.get(STUDIO_SESSION_COOKIE)
        assert client.get("/api/context").status_code == 404

    studio_root = tmp_path / "studio-sessions"
    assert list(studio_root.rglob("character-project.sqlite3"))
    assert not (tmp_path / "autopsy-sessions").exists()
    assert not list(tmp_path.rglob("*asset-autopsy*"))


def test_standalone_does_not_construct_asset_autopsy_sessions(
    tmp_path: Path, monkeypatch
) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("standalone Studio must not construct Asset Autopsy")

    monkeypatch.setattr(autopsy_workbench, "SessionManager", forbidden)
    monkeypatch.setattr(autopsy_workbench, "AssetAutopsyService", forbidden)
    monkeypatch.setenv("CHARACTER_ROBOT_STUDIO_ROOT", str(tmp_path / "studio"))

    app = create_studio_app(frontend_dir=tmp_path / "missing-frontend")
    with TestClient(app) as client:
        context = client.get("/api/studio/v1/context")
        assert context.status_code == 200
        assert context.json()["storage_mode"] == "durable"

    assert (tmp_path / "studio").is_dir()
    assert not (tmp_path / "autopsy-sessions").exists()
