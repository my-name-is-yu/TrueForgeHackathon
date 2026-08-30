from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from asset_autopsy.mcp_server import (
    MCPRuntimeConfig,
    TOOL_NAMES,
    create_mcp_facade,
)


class FakeService:
    def __init__(self) -> None:
        self.provisioned = 0

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
    facade = create_mcp_facade(
        service,
        MCPRuntimeConfig(
            bearer_token="test-bearer-token-value",
            allowed_origin="http://localhost:8790",
        ),
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

    asyncio.run(call())
    assert facade.recorder.counts["open_case"] == 0


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
    facade = create_mcp_facade(
        RawToolErrorService(),
        MCPRuntimeConfig(
            bearer_token="test-bearer-token-value",
            allowed_origin="http://localhost:8790",
        ),
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
