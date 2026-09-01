from __future__ import annotations

import asyncio
import math
import secrets
import shutil
import tempfile
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .fixture import CASE_ID
from .schemas import (
    AttributePatch,
    AssetHash,
    CreateRevisionInput,
    ExpectedEffect,
    HypothesisId,
    InspectAssetInput,
    OpenCaseInput,
    PromotionTicket,
    RevisionId,
    RunExperimentInput,
    RunId,
    RunTaskInput,
    SafeText,
    VerifyRevisionInput,
)
from .service import AssetAutopsyService, DomainError


SESSION_COOKIE = "asset_autopsy_session"
MAX_SESSIONS = 8


class WorkbenchError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class InspectDesignRequest(StrictRequest):
    revision_id: RevisionId | None = None
    view: Literal["authored", "compiled", "both"] = "both"


class DraftPatchRequest(StrictRequest):
    base_revision_id: RevisionId
    expected_base_sha256: AssetHash
    patch: AttributePatch


class CreateFromDraftRequest(StrictRequest):
    basis_hypothesis_id: HypothesisId
    basis_experiment_run_id: RunId
    rationale: SafeText
    expected_effect: ExpectedEffect


class QueryTraceRequest(StrictRequest):
    run_id: RunId
    operation: Literal["sample", "min_max", "delta", "sum", "settling"]
    signal: str | None = Field(default=None, min_length=1, max_length=160)
    start: int = Field(default=0, ge=0)
    end: int | None = Field(default=None, ge=1)
    count: int = Field(default=12, ge=1, le=64)
    target: float | None = None
    tolerance: float | None = Field(default=None, gt=0.0)


class FeedbackRequest(StrictRequest):
    revision_id: RevisionId
    asset_sha256: AssetHash
    feedback: SafeText


class AcceptRequest(StrictRequest):
    ticket_digest: AssetHash


@dataclass(slots=True)
class WorkbenchSession:
    service: AssetAutopsyService
    data_root: Path
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    active_requests: int = 0
    draft: DraftPatchRequest | None = None
    traces: dict[str, dict[str, Any]] = field(default_factory=dict)
    feedback: list[FeedbackRequest] = field(default_factory=list)
    promotion_ticket: PromotionTicket | None = None
    accepted: bool = False

    @property
    def editing_locked(self) -> bool:
        return self.promotion_ticket is not None


class SessionManager:
    def __init__(
        self,
        *,
        root: Path | None = None,
        service_factory: Callable[[Path], AssetAutopsyService] = AssetAutopsyService,
        max_sessions: int = MAX_SESSIONS,
    ) -> None:
        if not 1 <= max_sessions <= 32:
            raise ValueError("max_sessions must be between 1 and 32")
        self._tempdir = None if root is not None else tempfile.TemporaryDirectory()
        self.root = Path(root or self._tempdir.name).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.service_factory = service_factory
        self.max_sessions = max_sessions
        self.sessions: OrderedDict[str, WorkbenchSession] = OrderedDict()
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def lease(
        self, session_id: str | None
    ) -> AsyncIterator[tuple[str, WorkbenchSession, bool]]:
        async with self._lock:
            created = session_id is None or session_id not in self.sessions
            if created:
                if len(self.sessions) >= self.max_sessions:
                    self._evict_one()
                session_id = secrets.token_urlsafe(24)
                session = self._new_session(session_id)
                self.sessions[session_id] = session
            else:
                session = self.sessions[session_id]
                self.sessions.move_to_end(session_id)
            session.active_requests += 1
        try:
            async with session.lock:
                yield session_id, session, created
        finally:
            async with self._lock:
                session.active_requests -= 1

    def reset(self, session_id: str, session: WorkbenchSession) -> WorkbenchSession:
        if self.sessions.get(session_id) is not session or not session.lock.locked():
            raise RuntimeError("reset requires the leased current session")
        replacement = self._new_session(session_id)
        previous_service = session.service
        previous_root = session.data_root
        session.service = replacement.service
        session.data_root = replacement.data_root
        session.draft = None
        session.traces.clear()
        session.feedback.clear()
        session.promotion_ticket = None
        session.accepted = False
        previous_service.store.close()
        self._delete_generation(previous_root)
        return session

    def _new_session(self, session_id: str) -> WorkbenchSession:
        generation = secrets.token_hex(8)
        data_root = self.root / session_id / generation
        return WorkbenchSession(
            service=self.service_factory(data_root), data_root=data_root
        )

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
            raise WorkbenchError(
                "SESSION_CAPACITY_REACHED",
                "All temporary workbench sessions are currently busy.",
                503,
            )
        session = self.sessions.pop(session_id)
        session.service.store.close()
        self._delete_generation(session.data_root)

    def _delete_generation(self, data_root: Path) -> None:
        resolved = data_root.resolve()
        relative = resolved.relative_to(self.root)
        if len(relative.parts) != 2:
            raise RuntimeError("session generation path is invalid")
        if resolved.exists():
            shutil.rmtree(resolved)
        try:
            resolved.parent.rmdir()
        except OSError:
            pass


def _json(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def _set_session_cookie(
    response: Response, *, created: bool, session_id: str
) -> Response:
    if created:
        response.set_cookie(
            SESSION_COOKIE, session_id, httponly=True, samesite="lax", secure=False
        )
    return response


async def _head(session: WorkbenchSession):
    case = await session.service.open_case(OpenCaseInput(case_id=CASE_ID))
    return case, case.revision_history[-1]


async def _context(session: WorkbenchSession) -> dict[str, Any]:
    case, head = await _head(session)
    inspection = await session.service.inspect_asset(
        InspectAssetInput(case_id=CASE_ID, revision_id=head.revision_id, view="both")
    )
    return {
        "case": _json(case),
        "design": _json(inspection),
        "head_revision_id": head.revision_id,
        "head_asset_sha256": head.asset_sha256,
        "draft": session.draft.model_dump(mode="json") if session.draft else None,
        "feedback": [feedback.model_dump(mode="json") for feedback in session.feedback],
        "editing_locked": session.editing_locked,
        "accepted": session.accepted,
        "accept_ticket_digest": (
            session.promotion_ticket.ticket_digest
            if session.promotion_ticket is not None
            else None
        ),
    }


async def _call_tool(
    session: WorkbenchSession, name: str, arguments: dict[str, Any]
) -> Any:
    if name == "get_design_context":
        if arguments:
            raise WorkbenchError(
                "INVALID_ARGUMENTS", "This tool takes no arguments.", 422
            )
        return await _context(session)

    if name == "inspect_design":
        request = InspectDesignRequest.model_validate(arguments)
        _case, head = await _head(session)
        revision_id = request.revision_id or head.revision_id
        return await session.service.inspect_asset(
            InspectAssetInput(
                case_id=CASE_ID, revision_id=revision_id, view=request.view
            )
        )

    if name == "run_task":
        result = await session.service.run_task(RunTaskInput.model_validate(arguments))
        return result

    if name == "run_experiment":
        result = await session.service.run_experiment(
            RunExperimentInput.model_validate(arguments)
        )
        if result.trace is not None:
            session.traces[result.run_id] = result.trace.model_dump(mode="json")
        public_result = result.model_dump(mode="json")
        public_result.pop("trace")
        return public_result

    if name == "query_trace":
        return _query_trace(session, QueryTraceRequest.model_validate(arguments))

    if name == "set_draft_patch":
        if session.editing_locked:
            raise WorkbenchError(
                "EDITING_LOCKED",
                "Qualification passed. Accept or reset this session before editing.",
            )
        request = DraftPatchRequest.model_validate(arguments)
        _case, head = await _head(session)
        if (
            request.base_revision_id != head.revision_id
            or request.expected_base_sha256 != head.asset_sha256
        ):
            raise WorkbenchError(
                "STALE_DRAFT_BASE",
                "The draft must cite the current revision and asset hash.",
            )
        session.draft = request
        return {"draft": request.model_dump(mode="json"), "persisted": False}

    if name == "create_revision_from_draft":
        if session.editing_locked:
            raise WorkbenchError("EDITING_LOCKED", "Qualification has locked editing.")
        if session.draft is None:
            raise WorkbenchError("DRAFT_REQUIRED", "Create a draft patch first.")
        request = CreateFromDraftRequest.model_validate(arguments)
        _case, head = await _head(session)
        draft = session.draft
        if (
            draft.base_revision_id != head.revision_id
            or draft.expected_base_sha256 != head.asset_sha256
        ):
            raise WorkbenchError(
                "STALE_DRAFT_BASE", "The draft no longer targets the current head."
            )
        trace = session.traces.get(request.basis_experiment_run_id)
        if trace is None:
            raise WorkbenchError(
                "SESSION_EXPERIMENT_REQUIRED",
                "The cited experiment must have completed in this browser session.",
            )
        result = await session.service.create_revision(
            CreateRevisionInput(
                case_id=CASE_ID,
                base_revision_id=draft.base_revision_id,
                expected_base_sha256=draft.expected_base_sha256,
                basis_hypothesis_id=request.basis_hypothesis_id,
                basis_experiment_run_id=request.basis_experiment_run_id,
                patch=draft.patch,
                rationale=request.rationale,
                expected_effect=request.expected_effect,
            )
        )
        session.draft = None
        return result

    if name == "verify_revision":
        result = await session.service.verify_revision(
            VerifyRevisionInput.model_validate(arguments)
        )
        if result.promotion_ticket is not None:
            session.promotion_ticket = result.promotion_ticket
            session.draft = None
        return result

    if name == "record_design_feedback":
        request = FeedbackRequest.model_validate(arguments)
        _case, head = await _head(session)
        if (
            request.revision_id != head.revision_id
            or request.asset_sha256 != head.asset_sha256
        ):
            raise WorkbenchError(
                "STALE_FEEDBACK_TARGET",
                "Feedback must cite the exact current revision and asset hash.",
            )
        if len(session.feedback) >= 50:
            raise WorkbenchError(
                "FEEDBACK_LIMIT_REACHED",
                "This temporary session already contains 50 feedback entries.",
            )
        session.feedback.append(request)
        return {"recorded": True, "feedback": request.model_dump(mode="json")}

    raise WorkbenchError(
        "UNKNOWN_TOOL", "The requested workbench tool does not exist.", 404
    )


def _query_trace(
    session: WorkbenchSession, request: QueryTraceRequest
) -> dict[str, Any]:
    trace = session.traces.get(request.run_id)
    if trace is None:
        raise WorkbenchError(
            "TRACE_NOT_IN_SESSION",
            "The requested trace is not available in this session.",
            404,
        )
    rows = trace["rows"]
    end = request.end or len(rows)
    if request.start >= end or end > len(rows):
        raise WorkbenchError("INVALID_TRACE_RANGE", "The trace range is invalid.", 422)
    selected = rows[request.start : end]
    available = sorted(selected[0]["values"])
    if request.operation == "sample":
        if request.signal is not None and request.signal not in available:
            raise WorkbenchError(
                "UNKNOWN_SIGNAL", "The requested trace signal is unknown.", 422
            )
        stride = max(1, math.ceil(len(selected) / request.count))
        sampled = selected[::stride][: request.count]
        return {
            "run_id": request.run_id,
            "operation": request.operation,
            "available_signals": available,
            "rows": [
                {
                    "time_s": row["time_s"],
                    "values": (
                        {request.signal: row["values"][request.signal]}
                        if request.signal
                        else row["values"]
                    ),
                }
                for row in sampled
            ],
        }
    if request.signal is None or request.signal not in available:
        raise WorkbenchError(
            "SIGNAL_REQUIRED", "Choose one of the available trace signals.", 422
        )
    values = [float(row["values"][request.signal]) for row in selected]
    base = {
        "run_id": request.run_id,
        "operation": request.operation,
        "signal": request.signal,
        "start": request.start,
        "end": end,
    }
    if request.operation == "min_max":
        return {**base, "minimum": min(values), "maximum": max(values)}
    if request.operation == "delta":
        return {
            **base,
            "first": values[0],
            "last": values[-1],
            "delta": values[-1] - values[0],
        }
    if request.operation == "sum":
        return {**base, "sum": sum(values)}
    if request.target is None or request.tolerance is None:
        raise WorkbenchError(
            "SETTLING_PARAMETERS_REQUIRED",
            "Settling requires a target and positive tolerance.",
            422,
        )
    settled_index = next(
        (
            index
            for index in range(len(values))
            if all(
                abs(value - request.target) <= request.tolerance
                for value in values[index:]
            )
        ),
        None,
    )
    return {
        **base,
        "target": request.target,
        "tolerance": request.tolerance,
        "settled": settled_index is not None,
        "settling_time_s": (
            selected[settled_index]["time_s"] if settled_index is not None else None
        ),
    }


def create_workbench_app(
    *,
    manager: SessionManager | None = None,
    frontend_dir: Path | None = None,
) -> Starlette:
    manager = manager or SessionManager()
    if frontend_dir is None:
        frontend_dir = Path(__file__).resolve().parents[2] / "web" / "dist"

    async def tool_endpoint(request: Request) -> Response:
        created = False
        session_id = ""
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise WorkbenchError(
                    "INVALID_ARGUMENTS", "Tool arguments must be an object.", 422
                )
            async with manager.lease(request.cookies.get(SESSION_COOKIE)) as (
                session_id,
                session,
                created,
            ):
                result = await _call_tool(session, request.path_params["name"], body)
            response = JSONResponse({"ok": True, "result": _json(result)})
        except (ValidationError, ValueError) as error:
            response = JSONResponse(
                {
                    "ok": False,
                    "error": {"code": "INVALID_ARGUMENTS", "message": str(error)},
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
                        "next_action": error.next_action,
                        "request_id": error.request_id,
                    },
                },
                status_code=409,
            )
        except WorkbenchError as error:
            response = JSONResponse(
                {"ok": False, "error": {"code": error.code, "message": str(error)}},
                status_code=error.status_code,
            )
        return _set_session_cookie(response, created=created, session_id=session_id)

    async def context_endpoint(request: Request) -> Response:
        async with manager.lease(request.cookies.get(SESSION_COOKIE)) as (
            session_id,
            session,
            created,
        ):
            context = await _context(session)
        response = JSONResponse(context)
        return _set_session_cookie(response, created=created, session_id=session_id)

    async def trace_endpoint(request: Request) -> Response:
        async with manager.lease(request.cookies.get(SESSION_COOKIE)) as (
            session_id,
            session,
            created,
        ):
            trace = session.traces.get(request.path_params["run_id"])
        response = (
            JSONResponse(trace)
            if trace is not None
            else JSONResponse(
                {
                    "error": {
                        "code": "TRACE_NOT_IN_SESSION",
                        "message": "Trace not found.",
                    }
                },
                status_code=404,
            )
        )
        return _set_session_cookie(response, created=created, session_id=session_id)

    async def accept_endpoint(request: Request) -> Response:
        created = False
        session_id = ""
        try:
            body = AcceptRequest.model_validate(await request.json())
            async with manager.lease(request.cookies.get(SESSION_COOKIE)) as (
                session_id,
                session,
                created,
            ):
                ticket = session.promotion_ticket
                if (
                    ticket is None
                    or body.ticket_digest != ticket.ticket_digest
                    or not await session.service.validate_promotion_acceptance(ticket)
                ):
                    raise WorkbenchError(
                        "INVALID_PROMOTION_TICKET",
                        "Accept requires this session's successful promotion ticket.",
                    )
                session.accepted = True
                response = JSONResponse(
                    {
                        "accepted": True,
                        "revision_id": ticket.revision_id,
                        "asset_sha256": ticket.asset_sha256,
                    }
                )
        except (ValidationError, WorkbenchError) as error:
            code = getattr(error, "code", "INVALID_ARGUMENTS")
            status = getattr(error, "status_code", 422)
            response = JSONResponse(
                {"accepted": False, "error": {"code": code, "message": str(error)}},
                status_code=status,
            )
        return _set_session_cookie(response, created=created, session_id=session_id)

    async def reset_endpoint(request: Request) -> Response:
        async with manager.lease(request.cookies.get(SESSION_COOKIE)) as (
            session_id,
            session,
            created,
        ):
            manager.reset(session_id, session)
        response = JSONResponse({"reset": True})
        return _set_session_cookie(response, created=created, session_id=session_id)

    async def health_endpoint(_request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    async def workbench_error_handler(
        _request: Request, error: WorkbenchError
    ) -> Response:
        return JSONResponse(
            {"error": {"code": error.code, "message": str(error)}},
            status_code=error.status_code,
        )

    routes = [
        Route("/api/context", context_endpoint, methods=["GET"]),
        Route("/api/tools/{name:str}", tool_endpoint, methods=["POST"]),
        Route("/api/traces/{run_id:str}", trace_endpoint, methods=["GET"]),
        Route("/api/accept", accept_endpoint, methods=["POST"]),
        Route("/api/reset", reset_endpoint, methods=["POST"]),
        Route("/health", health_endpoint, methods=["GET"]),
    ]
    if frontend_dir.is_dir():
        routes.extend(
            [
                Mount(
                    "/assets",
                    StaticFiles(directory=frontend_dir / "assets"),
                    name="assets",
                ),
                Route("/", lambda _request: FileResponse(frontend_dir / "index.html")),
            ]
        )
    return Starlette(
        routes=routes, exception_handlers={WorkbenchError: workbench_error_handler}
    )


__all__ = [
    "SESSION_COOKIE",
    "MAX_SESSIONS",
    "SessionManager",
    "WorkbenchSession",
    "create_workbench_app",
]
