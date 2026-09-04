"""Standalone local application for Character Robot Studio.

The combined Asset Autopsy workbench still owns the compatibility application
that serves both products.  This module intentionally composes only the
Character Robot Studio routes so a Studio-only process cannot create an Asset
Autopsy session or expose its endpoints.
"""

from __future__ import annotations

import os
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .workbench import StudioSessionManager, create_studio_routes


DEFAULT_FRONTEND_DIR = Path(__file__).resolve().parents[2] / "web" / "dist"


def _default_studio_manager() -> StudioSessionManager:
    configured_root = os.environ.get("CHARACTER_ROBOT_STUDIO_ROOT")
    return StudioSessionManager(
        root=Path(configured_root) if configured_root else None,
    )


def _health_endpoint(_request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def create_studio_app(
    *,
    manager: StudioSessionManager | None = None,
    frontend_dir: Path | None = None,
) -> Starlette:
    """Create the Studio-only local application.

    The factory accepts a session manager and frontend directory for focused
    integration tests and local embedding.  Its default construction path is
    self-contained in ``character_robot``; it deliberately does not import or
    instantiate any Asset Autopsy service, session, or MCP server.
    """

    manager = manager or _default_studio_manager()
    frontend_dir = Path(frontend_dir or DEFAULT_FRONTEND_DIR).resolve()
    routes = [
        *create_studio_routes(manager),
        Route("/health", _health_endpoint, methods=["GET"]),
    ]
    if frontend_dir.is_dir():
        routes.extend(
            [
                Mount(
                    "/assets",
                    StaticFiles(directory=frontend_dir / "assets"),
                    name="studio-assets",
                ),
                Route(
                    "/studio",
                    lambda _request: FileResponse(frontend_dir / "index.html"),
                ),
                Route(
                    "/studio/",
                    lambda _request: FileResponse(frontend_dir / "index.html"),
                ),
            ]
        )
    app = Starlette(routes=routes)
    app.state.studio_manager = manager
    app.state.frontend_dir = frontend_dir
    return app


__all__ = ["DEFAULT_FRONTEND_DIR", "create_studio_app"]
