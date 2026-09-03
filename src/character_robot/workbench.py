from __future__ import annotations

import asyncio
import os
import re
import secrets
import shutil
import tempfile
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .cad_jobs import CadJobLimits, IsolatedCadCompiler, IsolatedCadJobRunner
from .profiles import ProfileRegistry
from .project_store import (
    MAX_PORTABLE_PROJECT_BYTES,
    PortableProjectSizeError,
    ProjectSnapshot,
    ProjectStore,
    ProjectStoreError,
    ProjectValidationError,
    import_portable_project,
)
from .schemas import (
    TOOL_INPUT_MODELS,
    TOOL_NAMES,
    DraftTarget,
    RevisionTarget,
    StudioSelectionInput,
)
from .service import CharacterRobotService, DomainError


STUDIO_SESSION_COOKIE = "character_robot_session"
MAX_STUDIO_SESSIONS = 8
MAX_CONCURRENT_UPLOAD_BUFFERS = 2
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
_ARTIFACT_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_ACTIVE_GENERATION_FILE = "active-generation"


class StudioWorkbenchError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


async def _read_portable_project_body(request: Request) -> bytes:
    content_lengths = request.headers.getlist("content-length")
    if len(content_lengths) > 1:
        raise StudioWorkbenchError(
            "INVALID_CONTENT_LENGTH",
            "Project import requires one valid Content-Length value.",
            400,
        )
    if content_lengths:
        content_length = content_lengths[0]
        if not content_length.isascii() or not content_length.isdigit():
            raise StudioWorkbenchError(
                "INVALID_CONTENT_LENGTH",
                "Project import requires one valid Content-Length value.",
                400,
            )
        normalized_size = content_length.lstrip("0") or "0"
        maximum_size = str(MAX_PORTABLE_PROJECT_BYTES)
        if len(normalized_size) > len(maximum_size) or (
            len(normalized_size) == len(maximum_size) and normalized_size > maximum_size
        ):
            raise PortableProjectSizeError

    content = bytearray()
    async for chunk in request.stream():
        if len(chunk) > MAX_PORTABLE_PROJECT_BYTES - len(content):
            raise PortableProjectSizeError
        content.extend(chunk)
    return bytes(content)


@dataclass(slots=True)
class StudioSession:
    service: CharacterRobotService
    data_root: Path
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    active_requests: int = 0
    selection_target: DraftTarget | RevisionTarget | None = None
    selected_node_id: str | None = None


class StudioSessionManager:
    def __init__(
        self,
        *,
        root: Path | None = None,
        service_factory: Callable[[Path], CharacterRobotService] | None = None,
        max_sessions: int = MAX_STUDIO_SESSIONS,
    ) -> None:
        if not 1 <= max_sessions <= 32:
            raise ValueError("max_sessions must be between 1 and 32")
        self._tempdir = None if root is not None else tempfile.TemporaryDirectory()
        self.persistent = root is not None
        self.root = Path(root or self._tempdir.name).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if service_factory is None:
            profiles = ProfileRegistry()
            compiler = IsolatedCadCompiler(
                IsolatedCadJobRunner(limits=CadJobLimits(wall_timeout_seconds=60.0))
            )

            def create_service(path: Path) -> CharacterRobotService:
                return CharacterRobotService(
                    data_root=path,
                    profile_registry=profiles,
                    cad_compiler=compiler,
                    project_store=(
                        ProjectStore(path / "character-project.sqlite3")
                        if self.persistent
                        else None
                    ),
                )

            service_factory = create_service
        self.service_factory = service_factory
        self.max_sessions = max_sessions
        self.sessions: OrderedDict[str, StudioSession] = OrderedDict()
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def lease(
        self, session_id: str | None
    ) -> AsyncIterator[tuple[str, StudioSession, bool]]:
        async with self._lock:
            created = False
            session = self.sessions.get(session_id or "")
            if session is None:
                if len(self.sessions) >= self.max_sessions:
                    self._evict_one()
                existing_root = self._existing_generation(session_id)
                if existing_root is not None:
                    assert session_id is not None
                    session = self._new_session(session_id, data_root=existing_root)
                else:
                    session_id = secrets.token_urlsafe(24)
                    session = self._new_session(session_id)
                    created = True
                self.sessions[session_id] = session
            else:
                assert session_id is not None
                self.sessions.move_to_end(session_id)
            session.active_requests += 1
        try:
            async with session.lock:
                yield session_id, session, created
        finally:
            async with self._lock:
                session.active_requests -= 1

    def reset(self, session_id: str, session: StudioSession) -> None:
        if self.sessions.get(session_id) is not session or not session.lock.locked():
            raise RuntimeError("reset requires the leased current session")
        previous_root = self._validated_generation_path(session.data_root)
        replacement = self._new_session(session_id, publish=False)
        try:
            self._publish_generation(replacement.data_root)
        except Exception:
            self._delete_generation(replacement.data_root)
            raise
        session.service = replacement.service
        session.data_root = replacement.data_root
        session.selection_target = None
        session.selected_node_id = None
        try:
            self._delete_generation(previous_root)
        except OSError:
            pass

    def import_project(
        self,
        session_id: str,
        session: StudioSession,
        content: bytes,
        *,
        expected_generation: int,
    ) -> ProjectSnapshot:
        """Replace one leased session with a validated portable revision chain."""

        if self.sessions.get(session_id) is not session or not session.lock.locked():
            raise RuntimeError("import requires the leased current session")
        if session.service.project_generation != expected_generation:
            raise StudioWorkbenchError(
                "STALE_PROJECT",
                "The Studio changed after the import dialog was opened.",
                409,
            )
        snapshot = import_portable_project(content)
        previous_root = self._validated_generation_path(session.data_root)
        replacement = self._new_session(session_id, publish=False)
        try:
            restored = replacement.service.restore_portable_project(snapshot)
            self._publish_generation(replacement.data_root)
        except Exception:
            self._delete_generation(replacement.data_root)
            raise
        session.service = replacement.service
        session.data_root = replacement.data_root
        session.selection_target = None
        session.selected_node_id = None
        try:
            self._delete_generation(previous_root)
        except OSError:
            pass
        return restored

    def _new_session(
        self,
        session_id: str,
        *,
        data_root: Path | None = None,
        publish: bool = True,
    ) -> StudioSession:
        if data_root is None:
            generation = secrets.token_hex(8)
            data_root = self.root / session_id / generation
            data_root.mkdir(parents=True, exist_ok=False)
        session = StudioSession(
            service=self.service_factory(data_root),
            data_root=data_root,
        )
        if publish:
            self._publish_generation(data_root)
        return session

    def _publish_generation(self, data_root: Path) -> None:
        if not self.persistent:
            return
        generation_root = self._validated_generation_path(data_root)
        session_root = generation_root.parent
        pointer = session_root / _ACTIVE_GENERATION_FILE
        pending = session_root / f".{_ACTIVE_GENERATION_FILE}.{secrets.token_hex(8)}"
        try:
            pending.write_text(f"{generation_root.name}\n", encoding="ascii")
            os.replace(pending, pointer)
        finally:
            pending.unlink(missing_ok=True)

    def _existing_generation(self, session_id: str | None) -> Path | None:
        if (
            not self.persistent
            or session_id is None
            or _SESSION_ID.fullmatch(session_id) is None
        ):
            return None
        session_root = (self.root / session_id).resolve()
        try:
            relative = session_root.relative_to(self.root)
        except ValueError:
            return None
        if len(relative.parts) != 1 or not session_root.is_dir():
            return None
        pointer = session_root / _ACTIVE_GENERATION_FILE
        if pointer.exists():
            try:
                generation = pointer.read_text(encoding="ascii").strip()
                candidate = (session_root / generation).resolve()
                candidate_relative = candidate.relative_to(session_root)
            except (OSError, UnicodeError, ValueError):
                return None
            if (
                len(candidate_relative.parts) != 1
                or candidate_relative.name != generation
                or not (candidate / "character-project.sqlite3").is_file()
            ):
                return None
            return candidate
        candidates = [
            path
            for path in session_root.iterdir()
            if path.is_dir() and (path / "character-project.sqlite3").is_file()
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime_ns)

    def _evict_one(self) -> None:
        session_id = next(
            (
                key
                for key, session in self.sessions.items()
                if session.active_requests == 0 and not session.lock.locked()
            ),
            None,
        )
        if session_id is None:
            raise StudioWorkbenchError(
                "SESSION_CAPACITY_REACHED",
                "All active Character Robot Studio sessions are busy.",
                503,
            )
        session = self.sessions.pop(session_id)
        if not self.persistent:
            self._delete_generation(session.data_root)

    def _delete_generation(self, data_root: Path) -> None:
        resolved = self._validated_generation_path(data_root)
        if resolved.exists():
            shutil.rmtree(resolved)
        try:
            resolved.parent.rmdir()
        except OSError:
            pass

    def _validated_generation_path(self, data_root: Path) -> Path:
        resolved = data_root.resolve()
        relative = resolved.relative_to(self.root)
        if len(relative.parts) != 2:
            raise RuntimeError("studio session generation path is invalid")
        return resolved


_TOOL_MODELS = dict(zip(TOOL_NAMES, TOOL_INPUT_MODELS, strict=True))
_TOOL_DESCRIPTIONS = {
    "get_studio_context": (
        "Read the shared Character Robot Studio head, draft, two digital hardware "
        "profiles, evidence policy, and bounded design capabilities."
    ),
    "set_design_draft": (
        "Create or replace the shared typed character-robot draft from a complete "
        "CharacterRobotSpec. Replacing an existing draft requires its exact "
        "expected_draft_hash. Raw CAD, mesh, MJCF, and executable code are not accepted."
    ),
    "revise_design_draft": (
        "Apply bounded semantic edits to the exact shared draft hash without changing "
        "the immutable revision head."
    ),
    "inspect_design": (
        "Inspect one exact draft or immutable revision, including semantic morphology "
        "nodes and compiled geometry identity."
    ),
    "preview_scenario": (
        "Preview an idle, greeting, listening, thinking, delight, or sleep scenario "
        "for an exact draft or revision."
    ),
    "validate_design": (
        "Run bounded CAD and MuJoCo checks plus any connected measured manufacturing "
        "probes, returning explicit evidence level, limits, warnings, and repairs."
    ),
    "create_revision_from_draft": (
        "Commit the exact shared draft as the next immutable design revision after "
        "checking its expected head and draft hash."
    ),
    "prepare_build_pack": (
        "Prepare an evidence-gated CAD, simulation, fixed-runtime, and maker pack for an "
        "exact immutable revision. This never downloads, purchases, or flashes anything."
    ),
}
_READ_ONLY_TOOLS = {
    "get_studio_context",
    "inspect_design",
    "preview_scenario",
    "validate_design",
}


def studio_tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": _TOOL_DESCRIPTIONS[name],
            "inputSchema": _TOOL_MODELS[name].model_json_schema(
                mode="validation", by_alias=True
            ),
            "annotations": {"readOnlyHint": name in _READ_ONLY_TOOLS},
        }
        for name in TOOL_NAMES
    ]


def _json(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def _with_cookie(
    response: Response, *, created: bool, session_id: str, persistent: bool
) -> Response:
    if created:
        response.set_cookie(
            STUDIO_SESSION_COOKIE,
            session_id,
            httponly=True,
            samesite="lax",
            secure=False,
            max_age=30 * 24 * 60 * 60 if persistent else None,
        )
    return response


async def _call_tool(
    service: CharacterRobotService, name: str, arguments: dict[str, Any]
) -> Any:
    model = _TOOL_MODELS.get(name)
    if model is None:
        raise StudioWorkbenchError(
            "UNKNOWN_TOOL", "The requested Studio tool does not exist.", 404
        )
    request = model.model_validate(arguments)
    return await getattr(service, name)(request)


async def _clear_stale_selection(session: StudioSession) -> None:
    if session.selection_target is None:
        session.selected_node_id = None
        return
    value = StudioSelectionInput(
        target=session.selection_target,
        node_id=session.selected_node_id,
    )
    try:
        await session.service.validate_selection(value)
    except DomainError:
        session.selection_target = None
        session.selected_node_id = None


def create_studio_routes(manager: StudioSessionManager) -> list[Route]:
    upload_buffer_admission = asyncio.Semaphore(MAX_CONCURRENT_UPLOAD_BUFFERS)

    async def tool_definitions_endpoint(_request: Request) -> Response:
        return JSONResponse(studio_tool_definitions())

    async def tool_endpoint(request: Request) -> Response:
        created = False
        session_id = ""
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise StudioWorkbenchError(
                    "INVALID_ARGUMENTS", "Tool arguments must be an object.", 422
                )
            async with manager.lease(request.cookies.get(STUDIO_SESSION_COOKIE)) as (
                session_id,
                session,
                created,
            ):
                result = await _call_tool(
                    session.service, request.path_params["name"], body
                )
                await _clear_stale_selection(session)
                if request.path_params["name"] == "get_studio_context":
                    result = result.model_copy(
                        update={"selected_node_id": session.selected_node_id}
                    )
            response = JSONResponse({"ok": True, "result": _json(result)})
        except (ValidationError, ValueError) as error:
            response = JSONResponse(
                {
                    "ok": False,
                    "error": {
                        "code": "INVALID_ARGUMENTS",
                        "message": str(error),
                    },
                },
                status_code=422,
            )
        except DomainError as error:
            response = JSONResponse(
                {
                    "ok": False,
                    "error": {
                        "code": error.code,
                        "message": error.safe_message,
                        "retryable": error.retryable,
                        "request_id": error.request_id,
                        "next_action": error.next_action,
                    },
                },
                status_code=error.http_status,
            )
        except StudioWorkbenchError as error:
            response = JSONResponse(
                {
                    "ok": False,
                    "error": {"code": error.code, "message": str(error)},
                },
                status_code=error.status_code,
            )
        return _with_cookie(
            response,
            created=created,
            session_id=session_id,
            persistent=manager.persistent,
        )

    async def context_endpoint(request: Request) -> Response:
        created = False
        session_id = ""
        try:
            async with manager.lease(request.cookies.get(STUDIO_SESSION_COOKIE)) as (
                session_id,
                session,
                created,
            ):
                result = await _call_tool(session.service, "get_studio_context", {})
                await _clear_stale_selection(session)
                result = result.model_copy(
                    update={"selected_node_id": session.selected_node_id}
                )
            response = JSONResponse(_json(result))
        except DomainError as error:
            response = JSONResponse(
                {"error": {"code": error.code, "message": error.safe_message}},
                status_code=error.http_status,
            )
        except StudioWorkbenchError as error:
            response = JSONResponse(
                {"error": {"code": error.code, "message": str(error)}},
                status_code=error.status_code,
            )
        return _with_cookie(
            response,
            created=created,
            session_id=session_id,
            persistent=manager.persistent,
        )

    async def selection_endpoint(request: Request) -> Response:
        created = False
        session_id = ""
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise StudioWorkbenchError(
                    "INVALID_ARGUMENTS", "Selection input must be an object.", 422
                )
            value = StudioSelectionInput.model_validate(body)
            async with manager.lease(request.cookies.get(STUDIO_SESSION_COOKIE)) as (
                session_id,
                session,
                created,
            ):
                await session.service.validate_selection(value)
                session.selection_target = value.target
                session.selected_node_id = value.node_id
            response = JSONResponse(
                {
                    "target": value.target.model_dump(mode="json"),
                    "selected_node_id": value.node_id,
                }
            )
        except (ValidationError, ValueError) as error:
            response = JSONResponse(
                {
                    "error": {
                        "code": "INVALID_ARGUMENTS",
                        "message": str(error),
                    }
                },
                status_code=422,
            )
        except DomainError as error:
            response = JSONResponse(
                {
                    "error": {
                        "code": error.code,
                        "message": error.safe_message,
                        "retryable": error.retryable,
                        "request_id": error.request_id,
                        "next_action": error.next_action,
                    }
                },
                status_code=error.http_status,
            )
        except StudioWorkbenchError as error:
            response = JSONResponse(
                {"error": {"code": error.code, "message": str(error)}},
                status_code=error.status_code,
            )
        return _with_cookie(
            response,
            created=created,
            session_id=session_id,
            persistent=manager.persistent,
        )

    async def artifact_endpoint(request: Request) -> Response:
        digest = request.path_params["sha256"]
        if _SHA256.fullmatch(digest) is None:
            return JSONResponse(
                {
                    "error": {
                        "code": "INVALID_ARTIFACT_DIGEST",
                        "message": "Artifact digest must be a lowercase SHA-256.",
                    }
                },
                status_code=422,
            )
        created = False
        session_id = ""
        try:
            async with manager.lease(request.cookies.get(STUDIO_SESSION_COOKIE)) as (
                session_id,
                session,
                created,
            ):
                content, media_type, file_name = session.service.read_artifact(digest)
            if _ARTIFACT_FILE_NAME.fullmatch(file_name) is None:
                raise StudioWorkbenchError(
                    "INVALID_ARTIFACT_METADATA",
                    "The artifact has an invalid download name.",
                    500,
                )
            response = Response(
                content,
                media_type=media_type,
                headers={
                    "Content-Disposition": f'inline; filename="{file_name}"',
                    "X-Content-Type-Options": "nosniff",
                    "Cache-Control": "private, max-age=31536000, immutable",
                },
            )
        except DomainError as error:
            response = JSONResponse(
                {"error": {"code": error.code, "message": error.safe_message}},
                status_code=error.http_status,
            )
        except StudioWorkbenchError as error:
            response = JSONResponse(
                {"error": {"code": error.code, "message": str(error)}},
                status_code=error.status_code,
            )
        return _with_cookie(
            response,
            created=created,
            session_id=session_id,
            persistent=manager.persistent,
        )

    async def reset_endpoint(request: Request) -> Response:
        created = False
        session_id = ""
        try:
            async with manager.lease(request.cookies.get(STUDIO_SESSION_COOKIE)) as (
                session_id,
                session,
                created,
            ):
                manager.reset(session_id, session)
            response = JSONResponse({"reset": True})
        except StudioWorkbenchError as error:
            response = JSONResponse(
                {"error": {"code": error.code, "message": str(error)}},
                status_code=error.status_code,
            )
        return _with_cookie(
            response,
            created=created,
            session_id=session_id,
            persistent=manager.persistent,
        )

    async def import_endpoint(request: Request) -> Response:
        created = False
        session_id = ""
        try:
            expected_headers = request.headers.getlist("x-character-project-generation")
            if (
                len(expected_headers) != 1
                or not expected_headers[0].isascii()
                or not expected_headers[0].isdigit()
                or len(expected_headers[0]) > 20
            ):
                raise StudioWorkbenchError(
                    "EXPECTED_GENERATION_REQUIRED",
                    "Project import requires the current Studio generation.",
                    409,
                )
            expected_generation = int(expected_headers[0])
            async with upload_buffer_admission:
                content = await _read_portable_project_body(request)
                try:
                    async with manager.lease(
                        request.cookies.get(STUDIO_SESSION_COOKIE)
                    ) as (session_id, session, created):
                        restored = manager.import_project(
                            session_id,
                            session,
                            content,
                            expected_generation=expected_generation,
                        )
                finally:
                    content = b""
            response = JSONResponse(
                {
                    "imported": True,
                    "head_revision_id": restored.head_revision_id,
                    "project_generation": restored.generation,
                    "revision_count": len(restored.revisions),
                }
            )
        except PortableProjectSizeError as error:
            response = JSONResponse(
                {
                    "error": {
                        "code": "PROJECT_IMPORT_TOO_LARGE",
                        "message": str(error),
                    }
                },
                status_code=413,
            )
        except ProjectValidationError as error:
            response = JSONResponse(
                {
                    "error": {
                        "code": "INVALID_PROJECT_IMPORT",
                        "message": str(error),
                    }
                },
                status_code=422,
            )
        except ClientDisconnect:
            response = JSONResponse(
                {
                    "error": {
                        "code": "PROJECT_IMPORT_INTERRUPTED",
                        "message": "The portable project upload was interrupted.",
                    }
                },
                status_code=400,
            )
        except ProjectStoreError:
            response = JSONResponse(
                {
                    "error": {
                        "code": "PROJECT_IMPORT_FAILED",
                        "message": "The portable project could not be stored.",
                    }
                },
                status_code=503,
            )
        except StudioWorkbenchError as error:
            response = JSONResponse(
                {"error": {"code": error.code, "message": str(error)}},
                status_code=error.status_code,
            )
        return _with_cookie(
            response,
            created=created,
            session_id=session_id,
            persistent=manager.persistent,
        )

    return [
        Route(
            "/api/studio/v1/tool-definitions",
            tool_definitions_endpoint,
            methods=["GET"],
        ),
        Route("/api/studio/v1/context", context_endpoint, methods=["GET"]),
        Route("/api/studio/v1/selection", selection_endpoint, methods=["POST"]),
        Route("/api/studio/v1/tools/{name:str}", tool_endpoint, methods=["POST"]),
        Route(
            "/api/studio/v1/artifacts/{sha256:str}",
            artifact_endpoint,
            methods=["GET"],
        ),
        Route("/api/studio/v1/reset", reset_endpoint, methods=["POST"]),
        Route("/api/studio/v1/project-import", import_endpoint, methods=["POST"]),
    ]


__all__ = [
    "MAX_STUDIO_SESSIONS",
    "STUDIO_SESSION_COOKIE",
    "StudioSessionManager",
    "create_studio_routes",
    "studio_tool_definitions",
]
