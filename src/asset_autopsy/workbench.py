from __future__ import annotations

import asyncio
import math
import secrets
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .fixture import CASE_ID
from .qualification import validate_promotion_ticket
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
WEBMCP_TOOL_NAMES = (
    "get_design_context",
    "inspect_design",
    "run_task",
    "run_experiment",
    "query_trace",
    "set_draft_patch",
    "create_revision_from_draft",
    "verify_revision",
    "record_design_feedback",
)
_ATTRIBUTE_PATCH = TypeAdapter(AttributePatch)


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
    patch: dict[str, Any]


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
class DraftPatch:
    base_revision_id: str
    expected_base_sha256: str
    patch: AttributePatch

    def public(self) -> dict[str, Any]:
        return {
            "base_revision_id": self.base_revision_id,
            "expected_base_sha256": self.expected_base_sha256,
            "patch": self.patch.model_dump(mode="json"),
        }


@dataclass(slots=True)
class DesignFeedback:
    revision_id: str
    asset_sha256: str
    feedback: str


@dataclass(slots=True)
class WorkbenchSession:
    service: AssetAutopsyService
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    draft: DraftPatch | None = None
    traces: dict[str, dict[str, Any]] = field(default_factory=dict)
    feedback: list[DesignFeedback] = field(default_factory=list)
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
    ) -> None:
        self._tempdir = None if root is not None else tempfile.TemporaryDirectory()
        self.root = Path(root or self._tempdir.name)
        self.root.mkdir(parents=True, exist_ok=True)
        self.service_factory = service_factory
        self.sessions: dict[str, WorkbenchSession] = {}

    def get(self, session_id: str | None) -> tuple[str, WorkbenchSession, bool]:
        if session_id is not None and session_id in self.sessions:
            return session_id, self.sessions[session_id], False
        session_id = secrets.token_urlsafe(24)
        session = self._new_session(session_id)
        self.sessions[session_id] = session
        return session_id, session, True

    def reset(self, session_id: str) -> WorkbenchSession:
        previous = self.sessions[session_id]
        previous.service.store.close()
        session = self._new_session(session_id)
        self.sessions[session_id] = session
        return session

    def _new_session(self, session_id: str) -> WorkbenchSession:
        generation = secrets.token_hex(8)
        return WorkbenchSession(
            service=self.service_factory(self.root / session_id / generation)
        )


def _json(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


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
        "draft": session.draft.public() if session.draft else None,
        "feedback": [asdict(feedback) for feedback in session.feedback],
        "editing_locked": session.editing_locked,
        "qualification_passed": session.promotion_ticket is not None,
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
        patch = _ATTRIBUTE_PATCH.validate_python(request.patch)
        session.draft = DraftPatch(
            base_revision_id=request.base_revision_id,
            expected_base_sha256=request.expected_base_sha256,
            patch=patch,
        )
        return {"draft": session.draft.public(), "persisted": False}

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
        stored_run = session.service.store.get_run(request.basis_experiment_run_id)
        if (
            stored_run.case_id != CASE_ID
            or stored_run.revision_id != draft.base_revision_id
        ):
            raise WorkbenchError(
                "EXPERIMENT_REVISION_MISMATCH",
                "The cited experiment does not belong to the draft base revision.",
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
        feedback = DesignFeedback(
            revision_id=request.revision_id,
            asset_sha256=request.asset_sha256,
            feedback=request.feedback,
        )
        if len(session.feedback) >= 50:
            raise WorkbenchError(
                "FEEDBACK_LIMIT_REACHED",
                "This temporary session already contains 50 feedback entries.",
            )
        session.feedback.append(feedback)
        return {"recorded": True, "feedback": asdict(feedback)}

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
        session_id, session, created = manager.get(request.cookies.get(SESSION_COOKIE))
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise WorkbenchError(
                    "INVALID_ARGUMENTS", "Tool arguments must be an object.", 422
                )
            async with session.lock:
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
        if created:
            response.set_cookie(
                SESSION_COOKIE,
                session_id,
                httponly=True,
                samesite="lax",
                secure=False,
                max_age=None,
            )
        return response

    async def context_endpoint(request: Request) -> Response:
        session_id, session, created = manager.get(request.cookies.get(SESSION_COOKIE))
        async with session.lock:
            context = await _context(session)
        response = JSONResponse(context)
        if created:
            response.set_cookie(
                SESSION_COOKIE, session_id, httponly=True, samesite="lax", secure=False
            )
        return response

    async def trace_endpoint(request: Request) -> Response:
        session_id, session, created = manager.get(request.cookies.get(SESSION_COOKIE))
        async with session.lock:
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
        if created:
            response.set_cookie(
                SESSION_COOKIE, session_id, httponly=True, samesite="lax", secure=False
            )
        return response

    async def accept_endpoint(request: Request) -> Response:
        session_id, session, created = manager.get(request.cookies.get(SESSION_COOKIE))
        try:
            async with session.lock:
                body = AcceptRequest.model_validate(await request.json())
                ticket = session.promotion_ticket
                case, head = await _head(session)
                stored_case = session.service.store.get_case(CASE_ID)
                commitment_hashes = {
                    "source_asset_sha256": stored_case.source_asset_sha256,
                    "controller_sha256": stored_case.controller_sha256,
                    "public_contract_sha256": stored_case.public_contract_sha256,
                    "runner_sha256": stored_case.runner_sha256,
                    "holdout_commitment_sha256": stored_case.holdout_commitment_sha256,
                }
                if (
                    ticket is None
                    or body.ticket_digest != ticket.ticket_digest
                    or case.qualification_state != "passed"
                    or ticket.revision_id != head.revision_id
                    or ticket.asset_sha256 != head.asset_sha256
                    or not validate_promotion_ticket(
                        ticket, commitment_hashes=commitment_hashes
                    )
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
        if created:
            response.set_cookie(
                SESSION_COOKIE, session_id, httponly=True, samesite="lax", secure=False
            )
        return response

    async def reset_endpoint(request: Request) -> Response:
        session_id, session, created = manager.get(request.cookies.get(SESSION_COOKIE))
        async with session.lock:
            manager.reset(session_id)
        response = JSONResponse({"reset": True})
        if created:
            response.set_cookie(
                SESSION_COOKIE, session_id, httponly=True, samesite="lax", secure=False
            )
        return response

    async def health_endpoint(_request: Request) -> Response:
        return JSONResponse({"status": "ok", "webmcp_tools": list(WEBMCP_TOOL_NAMES)})

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
    return Starlette(routes=routes)


__all__ = [
    "SESSION_COOKIE",
    "WEBMCP_TOOL_NAMES",
    "SessionManager",
    "WorkbenchSession",
    "create_workbench_app",
]
