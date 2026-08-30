from __future__ import annotations

import asyncio
import json
import os
import secrets
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Protocol, cast

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import ConfigDict, StrictBool, ValidationError as PydanticValidationError

from .schemas import (
    AssetHash,
    AttributePatch,
    CaseId,
    ConstantControlSegment,
    CreateRevisionInput,
    CreateRevisionOutput,
    ExpectedEffect,
    ExperimentObservable,
    Hypothesis,
    HypothesisId,
    InspectAssetInput,
    InspectAssetOutput,
    JointPosition,
    OpenCaseInput,
    OpenCaseOutput,
    PromotionTicket,
    PublishRevisionInput,
    PublishRevisionOutput,
    RevisionId,
    RunId,
    RunExperimentInput,
    RunExperimentOutput,
    RunTaskInput,
    RunTaskOutput,
    SafeText,
    TOOL_INPUT_MODELS,
    VerifyRevisionInput,
    VerifyRevisionOutput,
)


TOOL_NAMES = (
    "open_case",
    "inspect_asset",
    "run_task",
    "run_experiment",
    "create_revision",
    "verify_revision",
    "publish_revision",
)

_MAX_VALIDATION_ERRORS = 8
_MAX_VALIDATION_PATH_PARTS = 8
_MAX_VALIDATION_PATH_LENGTH = 160
_PUBLIC_INPUT_FIELD_NAMES = frozenset(
    {
        "actuator_name",
        "after",
        "asset_sha256",
        "attribute",
        "attributes",
        "base_revision_id",
        "basis_experiment_run_id",
        "basis_hypothesis_id",
        "before",
        "body_name",
        "canonical_diff",
        "capture",
        "capture_final_snapshot",
        "case_id",
        "claim",
        "competing_explanation",
        "controls",
        "discriminating_reason",
        "expected_asset_sha256",
        "expected_base_sha256",
        "expected_effect",
        "expected_old_value",
        "export_name",
        "falsifier",
        "holdout_result",
        "hypothesis",
        "initial_joint_positions",
        "joint_name",
        "kind",
        "label",
        "metric",
        "n_steps",
        "name",
        "new_value",
        "observables",
        "op",
        "passed",
        "patch",
        "position_rad",
        "predicates",
        "prediction",
        "promotion_ticket",
        "public_result",
        "qualified_core_sha256",
        "rationale",
        "revision_id",
        "scenario_id",
        "segments",
        "suspected_elements",
        "target",
        "ticket_digest",
        "ticket_id",
        "total",
        "value",
        "view",
        "violated_clause_ids",
    }
)
_SAFE_VALIDATION_ERROR_TYPES = frozenset(
    {
        "bool_type",
        "dict_type",
        "extra_forbidden",
        "finite_number",
        "float_parsing",
        "float_type",
        "greater_than",
        "greater_than_equal",
        "int_parsing",
        "int_type",
        "less_than",
        "less_than_equal",
        "list_type",
        "literal_error",
        "missing",
        "string_pattern_mismatch",
        "string_too_long",
        "string_too_short",
        "string_type",
        "too_long",
        "too_short",
        "tuple_type",
        "union_tag_invalid",
        "union_tag_not_found",
    }
)
_SAFE_STARTUP_ERROR_CODES = frozenset(
    {
        "MCP_FIXTURE_SMOKE_FAILED",
        "MCP_STARTUP_PREFLIGHT_FAILED",
        "UPSTREAM_BAD_RESPONSE",
        "UPSTREAM_SCHEMA_DRIFT",
        "UPSTREAM_TIMEOUT",
        "UPSTREAM_UNAVAILABLE",
    }
)


class _StartupRunnerProtocol(Protocol):
    async def validate(self, xml_string: str) -> bool: ...


class _StartupFixtureProtocol(Protocol):
    asset_xml: bytes


class AssetAutopsyServiceProtocol(Protocol):
    runner: _StartupRunnerProtocol
    fixture: _StartupFixtureProtocol

    async def open_case(self, request: OpenCaseInput) -> OpenCaseOutput: ...

    async def inspect_asset(self, request: InspectAssetInput) -> InspectAssetOutput: ...

    async def run_task(self, request: RunTaskInput) -> RunTaskOutput: ...

    async def run_experiment(self, request: RunExperimentInput) -> RunExperimentOutput: ...

    async def create_revision(self, request: CreateRevisionInput) -> CreateRevisionOutput: ...

    async def verify_revision(self, request: VerifyRevisionInput) -> VerifyRevisionOutput: ...

    async def publish_revision(self, request: PublishRevisionInput) -> PublishRevisionOutput: ...


class _SanitizedToolError(ToolError):
    pass


class _SanitizedFastMCP(FastMCP):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        try:
            return await super().call_tool(name, arguments)
        except ToolError as error:
            cause = error.__cause__
            if isinstance(cause, PydanticValidationError):
                raise _safe_error(cause) from None
            if isinstance(error, _SanitizedToolError):
                raise error from None
            if isinstance(cause, _SanitizedToolError):
                raise cause from None
            raise _safe_error(error) from None


def _http_header_bytes(value: str, *, name: str) -> bytes:
    if not isinstance(value, str):
        raise ValueError(f"SC1 MCP {name} must be a safe Latin-1 HTTP header value")
    try:
        encoded = value.encode("latin-1")
    except UnicodeEncodeError:
        raise ValueError(
            f"SC1 MCP {name} must be a safe Latin-1 HTTP header value"
        ) from None
    if any(byte < 0x20 or 0x7F <= byte <= 0x9F for byte in encoded):
        raise ValueError(f"SC1 MCP {name} must be a safe Latin-1 HTTP header value")
    return encoded


@dataclass(frozen=True)
class MCPRuntimeConfig:
    bearer_token: str = field(repr=False)
    allowed_origin: str
    host: str = "127.0.0.1"
    port: int = 8712
    _authorization_header: bytes = field(init=False, repr=False)
    _origin_header: bytes = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.host != "127.0.0.1":
            raise ValueError("SC1 MCP must bind to 127.0.0.1")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("SC1 MCP port is invalid")
        if (
            not isinstance(self.bearer_token, str)
            or len(self.bearer_token) < 16
            or any(character.isspace() for character in self.bearer_token)
        ):
            raise ValueError(
                "SC1 MCP bearer must be a non-whitespace secret of at least 16 characters"
            )
        authorization_header = _http_header_bytes(
            f"Bearer {self.bearer_token}", name="bearer"
        )
        origin_header = _http_header_bytes(self.allowed_origin, name="Origin")
        if not self.allowed_origin.startswith(
            ("http://localhost:", "http://127.0.0.1:")
        ):
            raise ValueError("SC1 MCP Origin must be a loopback HTTP origin")
        if "/" in self.allowed_origin.removeprefix("http://"):
            raise ValueError("SC1 MCP Origin must not include a path")
        object.__setattr__(self, "_authorization_header", authorization_header)
        object.__setattr__(self, "_origin_header", origin_header)

    @classmethod
    def from_environment(cls) -> MCPRuntimeConfig:
        bearer = os.environ.get("ASSET_AUTOPSY_MCP_BEARER", "")
        origin = os.environ.get("ASSET_AUTOPSY_ALLOWED_ORIGIN", "")
        raw_port = os.environ.get("ASSET_AUTOPSY_MCP_PORT", "8712")
        try:
            port = int(raw_port)
        except ValueError as error:
            raise ValueError("ASSET_AUTOPSY_MCP_PORT must be an integer") from error
        return cls(bearer_token=bearer, allowed_origin=origin, port=port)


@dataclass
class InvocationRecorder:
    counts: Counter[str] = field(default_factory=Counter)
    sequence: list[str] = field(default_factory=list)

    def record(self, tool_name: str) -> None:
        if tool_name not in TOOL_NAMES:
            raise ValueError("unknown public tool")
        self.counts[tool_name] += 1
        self.sequence.append(tool_name)


class _AuthOriginMiddleware:
    def __init__(self, app: Any, *, config: MCPRuntimeConfig) -> None:
        self._app = app
        self._bearer = config._authorization_header
        self._origin = config._origin_header

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("path") != "/mcp":
            await self._app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        authorization = headers.get(b"authorization", b"")
        origin = headers.get(b"origin", b"")
        if not secrets.compare_digest(authorization, self._bearer):
            await self._reject(send, 401, b"unauthorized")
            return
        if not secrets.compare_digest(origin, self._origin):
            await self._reject(send, 403, b"forbidden")
            return
        await self._app(scope, receive, send)

    @staticmethod
    async def _reject(send: Any, status: int, message: bytes) -> None:
        body = b'{"error":"' + message + b'"}'
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


@dataclass(frozen=True)
class MCPFacade:
    mcp: FastMCP
    app: Any
    recorder: InvocationRecorder
    config: MCPRuntimeConfig


class MCPStartupError(RuntimeError):
    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        safe_code = (
            code
            if isinstance(code, str) and code in _SAFE_STARTUP_ERROR_CODES
            else "MCP_STARTUP_PREFLIGHT_FAILED"
        )
        self.code = safe_code
        super().__init__(f"{self.code}: SC1 MCP startup preflight failed safely.")


async def preflight_mcp_startup(service: AssetAutopsyServiceProtocol) -> None:
    try:
        fixture_source = service.fixture.asset_xml
        if not isinstance(fixture_source, bytes):
            raise TypeError("fixture source must be bytes")
        fixture_xml = fixture_source.decode("utf-8")
        ready = await service.runner.validate(fixture_xml)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        raise MCPStartupError(getattr(error, "code", "")) from None
    if ready is not True:
        raise MCPStartupError("MCP_FIXTURE_SMOKE_FAILED")


def _request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


def _safe_validation_path(location: tuple[Any, ...], *, unknown_field: bool) -> str:
    parts = ["$"]
    visible = location[:_MAX_VALIDATION_PATH_PARTS]
    for index, part in enumerate(visible):
        is_unknown_field = unknown_field and index == len(location) - 1
        if is_unknown_field and not (
            isinstance(part, str) and part in _PUBLIC_INPUT_FIELD_NAMES
        ):
            parts.append(".<unknown>")
        elif isinstance(part, str) and part in _PUBLIC_INPUT_FIELD_NAMES:
            parts.append(f".{part}")
        elif isinstance(part, int) and not isinstance(part, bool):
            parts.append(f"[{part}]" if 0 <= part <= 999 else "[*]")
    if len(location) > _MAX_VALIDATION_PATH_PARTS:
        parts.append("...")
    return "".join(parts)[:_MAX_VALIDATION_PATH_LENGTH]


def _safe_validation_feedback(error: PydanticValidationError) -> tuple[list[dict[str, str]], bool]:
    raw_errors = error.errors(
        include_url=False,
        include_input=False,
        include_context=False,
    )
    feedback: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw_errors:
        raw_type = item.get("type")
        error_type = (
            raw_type
            if isinstance(raw_type, str) and raw_type in _SAFE_VALIDATION_ERROR_TYPES
            else "invalid_value"
        )
        raw_location = item.get("loc")
        location = raw_location if isinstance(raw_location, tuple) else ()
        path = _safe_validation_path(
            location,
            unknown_field=error_type == "extra_forbidden",
        )
        identity = (path, error_type)
        if identity in seen:
            continue
        seen.add(identity)
        feedback.append({"path": path, "type": error_type})
        if len(feedback) == _MAX_VALIDATION_ERRORS:
            break
    return feedback, len(raw_errors) > len(feedback)


def _safe_error(error: Exception) -> _SanitizedToolError:
    code = getattr(error, "code", None)
    safe_message = getattr(error, "safe_message", None)
    retryable = getattr(error, "retryable", None)
    next_action = getattr(error, "next_action", None)
    request_id = getattr(error, "request_id", None)
    if not isinstance(code, str) or not code.isupper() or len(code) > 64:
        code = "INVALID_REQUEST" if isinstance(error, PydanticValidationError) else "TOOL_EXECUTION_FAILED"
    if not isinstance(safe_message, str) or not 1 <= len(safe_message) <= 240:
        safe_message = (
            "The request did not satisfy the public tool contract."
            if isinstance(error, (PydanticValidationError, ValueError))
            else "The tool could not complete safely."
        )
    if not isinstance(retryable, bool):
        retryable = False
    if not isinstance(next_action, str) or not 1 <= len(next_action) <= 240:
        next_action = "Inspect the public case state and submit a bounded request."
    if not isinstance(request_id, str) or not request_id.startswith("req_"):
        request_id = _request_id()
    envelope = {
        "code": code,
        "message": safe_message,
        "retryable": retryable,
        "request_id": request_id,
        "next_action": next_action,
    }
    if isinstance(error, PydanticValidationError):
        validation_errors, validation_errors_truncated = _safe_validation_feedback(error)
        envelope["validation_errors"] = validation_errors
        envelope["validation_errors_truncated"] = validation_errors_truncated
    return _SanitizedToolError(
        json.dumps(envelope, sort_keys=True, separators=(",", ":"))
    )


def _annotations(*, read_only: bool, destructive: bool, idempotent: bool) -> ToolAnnotations:
    return ToolAnnotations(
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=idempotent,
        openWorldHint=False,
    )


def _enforce_strict_tool_arguments(mcp: FastMCP) -> None:
    manager = cast(Any, mcp)._tool_manager
    for name, public_model in zip(TOOL_NAMES, TOOL_INPUT_MODELS, strict=True):
        tool = manager.get_tool(name)
        if tool is None:
            raise RuntimeError("public MCP tool registration is incomplete")
        model = tool.fn_metadata.arg_model
        model.model_config = ConfigDict(
            **{**dict(model.model_config), "extra": "forbid"}
        )
        model.model_rebuild(force=True)
        tool.parameters = public_model.model_json_schema(by_alias=True)


async def create_mcp_facade(
    service: AssetAutopsyServiceProtocol,
    config: MCPRuntimeConfig,
    *,
    recorder: InvocationRecorder | None = None,
) -> MCPFacade:
    await preflight_mcp_startup(service)
    provision = getattr(service, "provision_demo_case", None)
    if callable(provision):
        provision()
    calls = recorder or InvocationRecorder()
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[f"127.0.0.1:{config.port}", f"localhost:{config.port}"],
        allowed_origins=[config.allowed_origin],
    )
    mcp = _SanitizedFastMCP(
        name="asset-autopsy-sc1",
        instructions="Bounded 3D asset observation, experimentation, revision, and qualification tools.",
        host=config.host,
        port=config.port,
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=transport_security,
    )

    async def invoke(
        name: str,
        request: Any,
        method: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        calls.record(name)
        try:
            return await method(request)
        except _SanitizedToolError:
            raise
        except ToolError as error:
            raise _safe_error(error) from None
        except Exception as error:
            raise _safe_error(error) from None

    @mcp.tool(
        name="open_case",
        description="Read the pre-provisioned case contract, budgets, topology, head, and patch policy.",
        annotations=_annotations(read_only=True, destructive=False, idempotent=True),
        structured_output=True,
    )
    async def open_case(case_id: CaseId) -> OpenCaseOutput:
        request = OpenCaseInput.model_validate({"case_id": case_id})
        return cast(OpenCaseOutput, await invoke("open_case", request, service.open_case))

    @mcp.tool(
        name="inspect_asset",
        description="Inspect authored and compiled values without fault labels, hidden values, or repair advice.",
        annotations=_annotations(read_only=True, destructive=False, idempotent=True),
        structured_output=True,
    )
    async def inspect_asset(
        case_id: CaseId,
        revision_id: RevisionId,
        view: Literal["authored", "compiled", "both"],
    ) -> InspectAssetOutput:
        request = InspectAssetInput.model_validate(
            {"case_id": case_id, "revision_id": revision_id, "view": view}
        )
        return cast(
            InspectAssetOutput,
            await invoke("inspect_asset", request, service.inspect_asset),
        )

    @mcp.tool(
        name="run_task",
        description="Run the fixed public scenario; child revisions include same-condition parent BehaviorDiff.",
        annotations=_annotations(read_only=False, destructive=False, idempotent=False),
        structured_output=True,
    )
    async def run_task(
        case_id: CaseId,
        revision_id: RevisionId,
        scenario_id: Literal["public_center"],
        capture: Literal["metrics", "metrics_and_filmstrip"],
    ) -> RunTaskOutput:
        request = RunTaskInput.model_validate(
            {
                "case_id": case_id,
                "revision_id": revision_id,
                "scenario_id": scenario_id,
                "capture": capture,
            }
        )
        return cast(RunTaskOutput, await invoke("run_task", request, service.run_task))

    @mcp.tool(
        name="run_experiment",
        description="Preregister a competing causal hypothesis, then run one bounded agent-defined experiment.",
        annotations=_annotations(read_only=False, destructive=False, idempotent=False),
        structured_output=True,
    )
    async def run_experiment(
        case_id: CaseId,
        revision_id: RevisionId,
        hypothesis: Hypothesis,
        initial_joint_positions: list[JointPosition],
        segments: list[ConstantControlSegment],
        observables: list[ExperimentObservable],
        capture_final_snapshot: StrictBool = False,
    ) -> RunExperimentOutput:
        request = RunExperimentInput.model_validate(
            {
                "case_id": case_id,
                "revision_id": revision_id,
                "hypothesis": hypothesis,
                "initial_joint_positions": initial_joint_positions,
                "segments": segments,
                "observables": observables,
                "capture_final_snapshot": capture_final_snapshot,
            }
        )
        return cast(
            RunExperimentOutput,
            await invoke("run_experiment", request, service.run_experiment),
        )

    @mcp.tool(
        name="create_revision",
        description="Create one immutable child revision by changing one allowed MJCF joint attribute.",
        annotations=_annotations(read_only=False, destructive=False, idempotent=True),
        structured_output=True,
    )
    async def create_revision(
        case_id: CaseId,
        base_revision_id: RevisionId,
        expected_base_sha256: AssetHash,
        basis_hypothesis_id: HypothesisId,
        basis_experiment_run_id: RunId,
        patch: AttributePatch,
        rationale: SafeText,
        expected_effect: ExpectedEffect,
    ) -> CreateRevisionOutput:
        request = CreateRevisionInput.model_validate(
            {
                "case_id": case_id,
                "base_revision_id": base_revision_id,
                "expected_base_sha256": expected_base_sha256,
                "basis_hypothesis_id": basis_hypothesis_id,
                "basis_experiment_run_id": basis_experiment_run_id,
                "patch": patch,
                "rationale": rationale,
                "expected_effect": expected_effect,
            }
        )
        return cast(
            CreateRevisionOutput,
            await invoke("create_revision", request, service.create_revision),
        )

    @mcp.tool(
        name="verify_revision",
        description="Run the public gate and one private three-scenario qualification; return aggregates only.",
        annotations=_annotations(read_only=False, destructive=False, idempotent=True),
        structured_output=True,
    )
    async def verify_revision(
        case_id: CaseId,
        revision_id: RevisionId,
        expected_asset_sha256: AssetHash,
    ) -> VerifyRevisionOutput:
        request = VerifyRevisionInput.model_validate(
            {
                "case_id": case_id,
                "revision_id": revision_id,
                "expected_asset_sha256": expected_asset_sha256,
            }
        )
        return cast(
            VerifyRevisionOutput,
            await invoke("verify_revision", request, service.verify_revision),
        )

    @mcp.tool(
        name="publish_revision",
        description="Publish the exact qualified revision bound to a stored promotion ticket.",
        annotations=_annotations(read_only=False, destructive=True, idempotent=True),
        structured_output=True,
    )
    async def publish_revision(
        case_id: CaseId,
        promotion_ticket: PromotionTicket,
    ) -> PublishRevisionOutput:
        request = PublishRevisionInput.model_validate(
            {"case_id": case_id, "promotion_ticket": promotion_ticket}
        )
        return cast(
            PublishRevisionOutput,
            await invoke("publish_revision", request, service.publish_revision),
        )

    _enforce_strict_tool_arguments(mcp)
    app = _AuthOriginMiddleware(
        mcp.streamable_http_app(),
        config=config,
    )
    return MCPFacade(mcp=mcp, app=app, recorder=calls, config=config)


def serve(service: AssetAutopsyServiceProtocol, config: MCPRuntimeConfig) -> None:
    import uvicorn

    facade = asyncio.run(create_mcp_facade(service, config))
    uvicorn.run(facade.app, host=config.host, port=config.port, log_level="warning")


__all__ = [
    "AssetAutopsyServiceProtocol",
    "InvocationRecorder",
    "MCPFacade",
    "MCPRuntimeConfig",
    "MCPStartupError",
    "TOOL_NAMES",
    "create_mcp_facade",
    "preflight_mcp_startup",
    "serve",
]
