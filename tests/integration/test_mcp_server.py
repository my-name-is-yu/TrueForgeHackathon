from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from asset_autopsy.mcp_server import (
    MCPRuntimeConfig,
    MCPStartupError,
    TOOL_NAMES,
    create_mcp_facade,
    serve,
)
from asset_autopsy.mujoco_client import UpstreamToolError


class FakeRunner:
    def __init__(self) -> None:
        self.result = True
        self.error: Exception | None = None
        self.validated: list[str] = []

    async def validate(self, xml_string: str) -> bool:
        self.validated.append(xml_string)
        if self.error is not None:
            raise self.error
        return self.result


class FakeService:
    def __init__(self) -> None:
        self.provisioned = 0
        self.fixture = SimpleNamespace(asset_xml=b'<mujoco model="startup-smoke"/>')
        self.runner = FakeRunner()

    def provision_demo_case(self) -> None:
        self.provisioned += 1

    async def _fail(self, _request: Any) -> Any:
        raise RuntimeError("/private/secret <mujoco>hidden</mujoco>")

    open_case = _fail
    inspect_asset = _fail
    run_task = _fail
    run_experiment = _fail
    create_revision = _fail
    verify_revision = _fail
    publish_revision = _fail


class RawToolErrorService(FakeService):
    async def _fail(self, _request: Any) -> Any:
        raise ToolError("/private/secret bearer=raw-secret-value")

    open_case = _fail


def make_facade() -> tuple[FakeService, Any]:
    service = FakeService()
    facade = asyncio.run(
        create_mcp_facade(
            service,
            MCPRuntimeConfig(
                bearer_token="test-bearer-token-value",
                allowed_origin="http://localhost:8790",
            ),
        )
    )
    return service, facade


def test_runtime_config_is_loopback_and_secret_safe() -> None:
    config = MCPRuntimeConfig(
        bearer_token="test-bearer-token-value",
        allowed_origin="http://localhost:8790",
    )
    assert "test-bearer" not in repr(config)
    with pytest.raises(ValueError):
        MCPRuntimeConfig(
            bearer_token="test-bearer-token-value",
            allowed_origin="https://example.com",
        )
    with pytest.raises(ValueError):
        MCPRuntimeConfig(
            bearer_token="short",
            allowed_origin="http://localhost:8790",
        )


@pytest.mark.parametrize(
    ("bearer", "origin"),
    (
        ("abcdefghijklmnop€", "http://localhost:8790"),
        ("abcdefghijklmnop\x00", "http://localhost:8790"),
        ("abcdefghijklmnop\n", "http://localhost:8790"),
        ("test-bearer-token-value", "http://localhost:8790€"),
        ("test-bearer-token-value", "http://localhost:8790\x00"),
        ("test-bearer-token-value", "http://localhost:8790\rInjected: value"),
    ),
)
def test_runtime_config_rejects_header_unsafe_values_without_echoing(
    bearer: str, origin: str
) -> None:
    with pytest.raises(ValueError) as captured:
        MCPRuntimeConfig(bearer_token=bearer, allowed_origin=origin)
    message = str(captured.value)
    assert bearer not in message
    assert origin not in message
    assert "€" not in message
    assert "Injected" not in message
    assert "\x00" not in message
    assert "\n" not in message
    assert "\r" not in message


@pytest.mark.parametrize(
    ("startup_error", "expected_code"),
    (
        (
            UpstreamToolError(
                "UPSTREAM_SCHEMA_DRIFT",
                "runtime metadata at /private/runtime leaked raw-secret-value",
                False,
                "private upstream action",
            ),
            "UPSTREAM_SCHEMA_DRIFT",
        ),
        (
            UpstreamToolError(
                "UPSTREAM_SCHEMA_DRIFT",
                "schema response at /private/schema leaked raw-secret-value",
                False,
                "private upstream action",
            ),
            "UPSTREAM_SCHEMA_DRIFT",
        ),
        (
            RuntimeError("smoke process failed at /private/smoke with raw-secret-value"),
            "MCP_STARTUP_PREFLIGHT_FAILED",
        ),
    ),
    ids=("runtime-identity", "tool-schema", "unexpected-smoke-error"),
)
def test_startup_preflight_failure_is_sanitized_and_never_binds(
    startup_error: Exception,
    expected_code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FakeService()
    service.runner.error = startup_error
    binds: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "uvicorn.run",
        lambda app, **kwargs: binds.append({"app": app, **kwargs}),
    )

    with pytest.raises(MCPStartupError) as captured:
        serve(
            service,
            MCPRuntimeConfig(
                bearer_token="test-bearer-token-value",
                allowed_origin="http://localhost:8790",
            ),
        )

    assert captured.value.code == expected_code
    assert binds == []
    assert service.provisioned == 0
    assert len(service.runner.validated) == 1
    message = str(captured.value)
    assert len(message) < 120
    assert "/private/" not in message
    assert "raw-secret-value" not in message
    assert "upstream action" not in message


def test_fixture_smoke_rejection_never_builds_or_binds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FakeService()
    service.runner.result = False
    binds: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "uvicorn.run",
        lambda app, **kwargs: binds.append({"app": app, **kwargs}),
    )

    with pytest.raises(MCPStartupError) as captured:
        serve(
            service,
            MCPRuntimeConfig(
                bearer_token="test-bearer-token-value",
                allowed_origin="http://localhost:8790",
            ),
        )

    assert captured.value.code == "MCP_FIXTURE_SMOKE_FAILED"
    assert binds == []
    assert service.provisioned == 0


def test_valid_preflight_starts_only_on_the_configured_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FakeService()
    binds: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "uvicorn.run",
        lambda app, **kwargs: binds.append({"app": app, **kwargs}),
    )
    config = MCPRuntimeConfig(
        bearer_token="test-bearer-token-value",
        allowed_origin="http://localhost:8790",
    )

    serve(service, config)

    assert service.runner.validated == ['<mujoco model="startup-smoke"/>']
    assert service.provisioned == 1
    assert len(binds) == 1
    assert binds[0]["host"] == "127.0.0.1"
    assert binds[0]["port"] == 8712


def test_exact_strict_tool_schemas_and_annotations() -> None:
    service, facade = make_facade()
    tools = asyncio.run(facade.mcp.list_tools())
    assert service.provisioned == 1
    assert [tool.name for tool in tools] == list(TOOL_NAMES)
    assert all(tool.inputSchema.get("additionalProperties") is False for tool in tools)
    assert {
        tool.name
        for tool in tools
        if tool.annotations is not None and tool.annotations.destructiveHint is True
    } == {"publish_revision"}
    assert {
        tool.name
        for tool in tools
        if tool.annotations is not None and tool.annotations.readOnlyHint is True
    } == {"open_case", "inspect_asset"}

    experiment_schema = next(
        tool.inputSchema for tool in tools if tool.name == "run_experiment"
    )
    for field, maximum in (
        ("initial_joint_positions", 64),
        ("segments", 16),
        ("observables", 8),
    ):
        assert experiment_schema["properties"][field]["minItems"] == 1
        assert experiment_schema["properties"][field]["maxItems"] == maximum

    inputs = json.dumps([tool.inputSchema for tool in tools], sort_keys=True)
    for prohibited in (
        "xml_string",
        "file_path",
        "hidden_target",
        "timestep",
        "seed",
        "controller",
        "tests",
        "site_position",
    ):
        assert prohibited not in inputs


def test_unknown_top_level_fields_are_rejected_without_echoing_values() -> None:
    _, facade = make_facade()

    async def call() -> None:
        with pytest.raises(ToolError) as captured:
            await facade.mcp.call_tool(
                "open_case",
                {"case_id": "case_compound_arm_01", "xml_string": "<secret-value>"},
            )
        message = str(captured.value)
        assert "INVALID_REQUEST" in message
        assert "secret-value" not in message
        assert "xml_string" not in message
        assert '"path":"$.<unknown>"' in message
        assert '"type":"extra_forbidden"' in message

    asyncio.run(call())
    assert facade.recorder.counts["open_case"] == 0


def test_validation_feedback_names_only_public_paths_and_safe_error_types() -> None:
    _, facade = make_facade()

    async def call() -> None:
        with pytest.raises(ToolError) as captured:
            await facade.mcp.call_tool(
                "run_experiment",
                {
                    "case_id": "case_compound_arm_01",
                    "revision_id": "r000",
                    "hypothesis": {
                        "claim": "bounded claim",
                        "suspected_elements": [
                            {
                                "kind": "joint",
                                "name": "joint_a",
                                "attributes": ["axis"],
                            }
                        ],
                        "competing_explanation": {
                            "claim": "bounded alternative",
                            "suspected_elements": [
                                {
                                    "kind": "joint",
                                    "name": "joint_a",
                                    "attributes": ["axis"],
                                }
                            ],
                            "discriminating_reason": "bounded reason",
                            "prediction": "must remain outside competing_explanation",
                        },
                        "prediction": "bounded prediction",
                        "falsifier": "bounded falsifier",
                    },
                    "initial_joint_positions": [
                        {"joint_name": "joint_a", "position_rad": 0.0}
                    ],
                    "segments": [
                        {
                            "n_steps": 256,
                            "controls": [
                                {"actuator_name": "motor_a", "value": 0.0}
                            ],
                        }
                    ],
                    "observables": [
                        {
                            "kind": "body_position",
                            "body_name": "https://example.invalid/private.xml",
                            "secret/path.xml": "<hidden>bearer-secret</hidden>",
                        }
                    ],
                },
            )
        message = str(captured.value)
        assert '"path":"$.observables[0].body_name"' in message
        assert '"type":"string_pattern_mismatch"' in message
        assert '"path":"$.observables[0].<unknown>"' in message
        assert (
            '"path":"$.hypothesis.competing_explanation.prediction"'
            in message
        )
        assert '"type":"extra_forbidden"' in message
        for prohibited in (
            "example.invalid",
            "private.xml",
            "secret/path.xml",
            "hidden",
            "bearer-secret",
            "must remain outside",
            "String should match",
        ):
            assert prohibited not in message

    asyncio.run(call())
    assert facade.recorder.counts["run_experiment"] == 0


def test_validation_feedback_is_bounded_and_reports_missing_public_fields() -> None:
    _, facade = make_facade()

    async def call() -> None:
        with pytest.raises(ToolError) as captured:
            await facade.mcp.call_tool(
                "create_revision",
                {"case_id": "case_compound_arm_01"},
            )
        message = str(captured.value)
        envelope = json.loads(message)
        assert envelope["validation_errors"] == [
            {"path": "$.base_revision_id", "type": "missing"},
            {"path": "$.expected_base_sha256", "type": "missing"},
            {"path": "$.basis_hypothesis_id", "type": "missing"},
            {"path": "$.basis_experiment_run_id", "type": "missing"},
            {"path": "$.patch", "type": "missing"},
            {"path": "$.rationale", "type": "missing"},
            {"path": "$.expected_effect", "type": "missing"},
        ]
        assert envelope["validation_errors_truncated"] is False
        assert len(message) < 1_500

    asyncio.run(call())
    assert facade.recorder.counts["create_revision"] == 0


def test_unexpected_service_errors_are_redacted_and_recorded() -> None:
    _, facade = make_facade()

    async def call() -> None:
        with pytest.raises(ToolError) as captured:
            await facade.mcp.call_tool("open_case", {"case_id": "case_compound_arm_01"})
        message = str(captured.value)
        assert "TOOL_EXECUTION_FAILED" in message
        assert "/private/secret" not in message
        assert "mujoco" not in message

    asyncio.run(call())
    assert facade.recorder.counts["open_case"] == 1


def test_service_tool_errors_are_redacted_unless_created_by_the_facade() -> None:
    facade = asyncio.run(
        create_mcp_facade(
            RawToolErrorService(),
            MCPRuntimeConfig(
                bearer_token="test-bearer-token-value",
                allowed_origin="http://localhost:8790",
            ),
        )
    )

    async def call() -> None:
        with pytest.raises(ToolError) as captured:
            await facade.mcp.call_tool("open_case", {"case_id": "case_compound_arm_01"})
        message = str(captured.value)
        assert "TOOL_EXECUTION_FAILED" in message
        assert "/private/secret" not in message
        assert "raw-secret-value" not in message

    asyncio.run(call())


async def _asgi_status(app: Any, headers: list[tuple[bytes, bytes]]) -> int:
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/mcp",
            "raw_path": b"/mcp",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8712),
        },
        receive,
        send,
    )
    starts = [message for message in messages if message["type"] == "http.response.start"]
    assert len(starts) == 1
    return int(starts[0]["status"])


def test_bearer_and_origin_rejections_happen_before_mcp() -> None:
    _, facade = make_facade()
    wrong_bearer = asyncio.run(
        _asgi_status(
            facade.app,
            [
                (b"authorization", b"Bearer wrong-bearer-value"),
                (b"origin", b"http://localhost:8790"),
            ],
        )
    )
    wrong_origin = asyncio.run(
        _asgi_status(
            facade.app,
            [
                (b"authorization", b"Bearer test-bearer-token-value"),
                (b"origin", b"http://localhost:1"),
            ],
        )
    )
    assert wrong_bearer == 401
    assert wrong_origin == 403
    assert facade.recorder.sequence == []
