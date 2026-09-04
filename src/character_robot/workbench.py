from __future__ import annotations

import asyncio
import os
import re
import secrets
import shutil
import tempfile
import threading
from collections import OrderedDict, deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from .artifacts import ArtifactDownload
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
MAX_CONCURRENT_ARTIFACT_DOWNLOADS = 2
ARTIFACT_DOWNLOAD_CHUNK_BYTES = 64 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
_ARTIFACT_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_ACTIVE_GENERATION_FILE = "active-generation"


class StudioWorkbenchError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class _ArtifactStreamingResponse(StreamingResponse):
    def __init__(
        self,
        download: ArtifactDownload,
        cleanup: Callable[[], None],
        *,
        media_type: str,
        headers: dict[str, str],
    ) -> None:
        self._download = download
        self._cleanup_callback = cleanup
        self._cleaned_up = False
        super().__init__(
            self._body_iterator(),
            media_type=media_type,
            headers=headers,
        )

    async def _body_iterator(self) -> AsyncIterator[bytes]:
        try:
            while chunk := self._download.source.read(ARTIFACT_DOWNLOAD_CHUNK_BYTES):
                yield chunk
        finally:
            self._cleanup()

    def _cleanup(self) -> None:
        if self._cleaned_up:
            return
        self._cleaned_up = True
        self._cleanup_callback()

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._cleanup()


@dataclass(slots=True)
class _ArtifactAdmissionWaiter:
    future: asyncio.Future[None]
    granted: bool = False


class _ArtifactDownloadAdmission:
    """Process-wide bounded admission that is safe across event loops."""

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("artifact download capacity must be positive")
        self.capacity = capacity
        self._active = 0
        self._lock = threading.Lock()
        self._waiters: deque[_ArtifactAdmissionWaiter] = deque()

    async def acquire(self) -> None:
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._active < self.capacity:
                self._active += 1
                return
            waiter = _ArtifactAdmissionWaiter(loop.create_future())
            self._waiters.append(waiter)
        try:
            await asyncio.shield(waiter.future)
        except BaseException:
            acquired = False
            with self._lock:
                if waiter.granted:
                    acquired = True
                else:
                    try:
                        self._waiters.remove(waiter)
                    except ValueError:
                        acquired = waiter.granted
            if acquired:
                self.release()
            raise

    def release(self) -> None:
        with self._lock:
            waiter = None
            while self._waiters:
                candidate = self._waiters.popleft()
                if candidate.future.done():
                    continue
                candidate.granted = True
                waiter = candidate
                break
            if waiter is None:
                if self._active < 1:
                    raise RuntimeError("artifact download admission was over-released")
                self._active -= 1
                return

        def wake_waiter() -> None:
            if waiter.future.done():
                self.release()
            else:
                waiter.future.set_result(None)

        try:
            waiter.future.get_loop().call_soon_threadsafe(wake_waiter)
        except RuntimeError:
            self.release()


_PROCESS_ARTIFACT_DOWNLOAD_ADMISSION = _ArtifactDownloadAdmission(
    MAX_CONCURRENT_ARTIFACT_DOWNLOADS
)


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
        artifact_download_admission: _ArtifactDownloadAdmission | None = None,
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
        self.artifact_download_admission = (
            artifact_download_admission or _PROCESS_ARTIFACT_DOWNLOAD_ADMISSION
        )
        self._download_generation_pins: dict[Path, int] = {}
        self._pending_generation_deletes: set[Path] = set()

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
        next_generation = session.service.project_generation + 1
        previous_root = self._validated_generation_path(session.data_root)
        replacement = self._new_session(session_id, publish=False)
        try:
            replacement.service.advance_blank_project_generation(
                next_generation=next_generation
            )
            self._publish_generation(replacement.data_root)
        except Exception:
            self._delete_generation(replacement.data_root)
            raise
        previous_service = session.service
        session.service = replacement.service
        session.data_root = replacement.data_root
        session.selection_target = None
        session.selected_node_id = None
        previous_service.release_displayed_build_pack()
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
            restored = replacement.service.restore_portable_project(
                snapshot,
                next_generation=expected_generation + 1,
            )
            self._publish_generation(replacement.data_root)
        except Exception:
            self._delete_generation(replacement.data_root)
            raise
        previous_service = session.service
        session.service = replacement.service
        session.data_root = replacement.data_root
        session.selection_target = None
        session.selected_node_id = None
        previous_service.release_displayed_build_pack()
        try:
            self._delete_generation(previous_root)
        except OSError:
            pass
        return restored

    def pin_download_generation(self, data_root: Path) -> Path:
        resolved = self._validated_generation_path(data_root)
        self._download_generation_pins[resolved] = (
            self._download_generation_pins.get(resolved, 0) + 1
        )
        return resolved

    def release_download_generation(self, data_root: Path) -> None:
        resolved = self._validated_generation_path(data_root)
        pins = self._download_generation_pins.get(resolved, 0)
        if pins <= 1:
            self._download_generation_pins.pop(resolved, None)
            if resolved in self._pending_generation_deletes:
                self._pending_generation_deletes.remove(resolved)
                try:
                    self._delete_generation(resolved)
                except OSError:
                    pass
            return
        self._download_generation_pins[resolved] = pins - 1

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
                if (
                    session.active_requests == 0
                    and not session.lock.locked()
                    and not self._download_generation_pins.get(
                        session.data_root.resolve()
                    )
                )
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
        if self._download_generation_pins.get(resolved):
            self._pending_generation_deletes.add(resolved)
            return
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
        "nodes, compiled geometry identity, and the exact compiler GLB. In the Studio "
        "browser this also captures four canonical views and generic render diagnostics "
        "for visual comparison with the requested motif and design brief."
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


def studio_tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": _TOOL_DESCRIPTIONS[name],
            "inputSchema": _TOOL_MODELS[name].model_json_schema(
                mode="validation", by_alias=True
            ),
            "annotations": {"readOnlyHint": False},
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
        admitted = False
        download: ArtifactDownload | None = None
        pinned_root: Path | None = None
        try:
            await manager.artifact_download_admission.acquire()
            admitted = True
            async with manager.lease(request.cookies.get(STUDIO_SESSION_COOKIE)) as (
                session_id,
                session,
                created,
            ):
                download = session.service.prepare_artifact_download(digest)
                pinned_root = manager.pin_download_generation(session.data_root)
            descriptor = download.descriptor
            if _ARTIFACT_FILE_NAME.fullmatch(descriptor.file_name) is None:
                raise StudioWorkbenchError(
                    "INVALID_ARTIFACT_METADATA",
                    "The artifact has an invalid download name.",
                    500,
                )

            stream_download = download
            stream_root = pinned_root

            def cleanup_download() -> None:
                try:
                    try:
                        stream_download.close()
                    except OSError:
                        pass
                finally:
                    try:
                        assert stream_root is not None
                        manager.release_download_generation(stream_root)
                    except OSError:
                        pass
                    finally:
                        manager.artifact_download_admission.release()

            response = _ArtifactStreamingResponse(
                stream_download,
                cleanup_download,
                media_type=descriptor.media_type,
                headers={
                    "Content-Disposition": (
                        f'inline; filename="{descriptor.file_name}"'
                    ),
                    "X-Content-Type-Options": "nosniff",
                    "Cache-Control": "private, max-age=31536000, immutable",
                    "Content-Length": str(descriptor.byte_size),
                },
            )
            download = None
            pinned_root = None
            admitted = False
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
        finally:
            if download is not None:
                try:
                    try:
                        download.close()
                    except OSError:
                        pass
                finally:
                    if pinned_root is not None:
                        try:
                            manager.release_download_generation(pinned_root)
                        except OSError:
                            pass
            elif pinned_root is not None:
                try:
                    manager.release_download_generation(pinned_root)
                except OSError:
                    pass
            if admitted:
                manager.artifact_download_admission.release()
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
    "ARTIFACT_DOWNLOAD_CHUNK_BYTES",
    "MAX_CONCURRENT_ARTIFACT_DOWNLOADS",
    "MAX_STUDIO_SESSIONS",
    "STUDIO_SESSION_COOKIE",
    "StudioSessionManager",
    "create_studio_routes",
    "studio_tool_definitions",
]
