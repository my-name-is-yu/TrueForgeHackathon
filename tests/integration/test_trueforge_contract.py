from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from asset_autopsy.mcp_server import TOOL_NAMES, trueforge_tool_input_schema
from asset_autopsy.schemas import TOOL_INPUT_MODELS
from asset_autopsy.trueforge_client import (
    AGENT_NAME,
    DEFAULT_MODEL,
    EXACT_PROMPT,
    MCP_SERVER_NAME,
    TrueForgeClient,
    TrueForgeError,
    build_agent_spec,
)


def _tools() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "inputSchema": trueforge_tool_input_schema(model),
            "annotations": {
                "readOnlyHint": name in {"open_case", "inspect_asset"},
                "destructiveHint": name == "publish_revision",
                "idempotentHint": name
                in {
                    "open_case",
                    "inspect_asset",
                    "create_revision",
                    "verify_revision",
                    "publish_revision",
                },
                "openWorldHint": False,
            },
        }
        for name, model in zip(TOOL_NAMES, TOOL_INPUT_MODELS, strict=True)
    ]


class ProvisionTransport:
    def __init__(self, *, existing_autonomy: Mapping[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, str, Mapping[str, Any] | None]] = []
        self.starter = {
            "id": "agent-starter",
            "name": "hackathon-starter",
            "manifest": {"model": {"name": "openai/gpt-5-4-mini"}},
        }
        self.autonomy = (
            dict(existing_autonomy) if existing_autonomy is not None else None
        )

    def __call__(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None,
    ) -> tuple[int, Mapping[str, Any]]:
        self.calls.append((method, path, payload))
        if (method, path) == ("GET", "/api/v1/capabilities"):
            return 200, {
                "data": {
                    "sandbox": {"enabled": True},
                    "skill": {"enabled": True},
                    "settings": {"enabled": True},
                }
            }
        if (method, path) == ("GET", "/api/v1/models"):
            return 200, {
                "data": [
                    {
                        "name": DEFAULT_MODEL,
                        "model_id": "gpt-5.6-sol",
                        "provider": {"name": "openai"},
                        "properties": {},
                    }
                ]
            }
        if (method, path) == ("GET", "/api/v1/agents"):
            agents = [self.starter]
            if self.autonomy is not None:
                agents.append(self.autonomy)
            return 200, {"data": agents}
        if (method, path) == ("PUT", "/api/v1/settings/mcp-servers"):
            assert payload is not None
            manifest = payload["manifest"]
            assert manifest["name"] == AGENT_NAME
            assert manifest["url"] == "http://127.0.0.1:8712/mcp"
            return 200, {"data": {"name": AGENT_NAME}}
        if (method, path) == (
            "GET",
            f"/api/v1/mcp-servers/{MCP_SERVER_NAME}/tools",
        ):
            return 200, {"data": _tools()}
        if (method, path) == ("POST", "/api/v1/agents"):
            assert payload is not None
            self.autonomy = {
                "id": "agent-autonomy",
                "name": payload["name"],
                "manifest": payload["manifest"],
            }
            return 201, {"data": self.autonomy}
        if method == "PUT" and path == "/api/v1/agents/agent-autonomy":
            assert payload is not None and self.autonomy is not None
            self.autonomy = {**self.autonomy, "manifest": payload["manifest"]}
            return 200, {"data": self.autonomy}
        raise AssertionError(f"unexpected request: {method} {path}")


def test_agent_spec_is_the_exact_serial_approval_contract() -> None:
    spec = build_agent_spec()
    assert spec["model"] == {
        "name": DEFAULT_MODEL,
        "params": {
            "parallel_tool_calls": False,
            "reasoning_effort": "high",
        },
    }
    assert spec["mcp_servers"] == [
        {
            "name": MCP_SERVER_NAME,
            "enable_tools": list(TOOL_NAMES),
            "disable_tools": [],
            "preload_tools": [],
            "require_approval_for_tools": ["publish_revision"],
            "preload": True,
        }
    ]
    config = spec["config"]
    assert config["iteration_limit"] == 30
    assert config["sandbox"] == {"enabled": True, "file_downloads": True}
    assert config["context_management"]["large_tool_response"]["enabled"] is True
    assert "skills" not in spec
    instructions = spec["instructions"]
    assert len(instructions) < 800
    for required_boundary in (
        "Use only the public capabilities and public evidence",
        "Preserve the controller, tests, declared contract, pinned runner",
        "Do not fabricate evidence",
        "bypass human approval",
        "Choose how to investigate, change, and verify",
    ):
        assert required_boundary in instructions
    for forbidden_procedure in (
        *TOOL_NAMES,
        "Sandbox",
        "baseline",
        "BehaviorDiff",
        "public 1/1",
        "hidden 3/3",
        "Result saved to:",
    ):
        assert forbidden_procedure not in instructions
    assert EXACT_PROMPT == (
        "Repair compound-arm-01 so it satisfies its declared requirements, then submit "
        "the qualified revision for human approval."
    )


def test_provision_creates_only_dedicated_connector_and_agent() -> None:
    transport = ProvisionTransport()
    client = TrueForgeClient(transport=transport)
    result = client.provision_autonomy(bearer="runtime-bearer-token-value")
    assert result.agent_id == "agent-autonomy"
    assert result.agent_action == "created"
    writes = [(method, path) for method, path, _ in transport.calls if method != "GET"]
    assert writes == [
        ("PUT", "/api/v1/settings/mcp-servers"),
        ("POST", "/api/v1/agents"),
    ]
    assert all("model-providers" not in path for _, path, _ in transport.calls)
    assert transport.starter == {
        "id": "agent-starter",
        "name": "hackathon-starter",
        "manifest": {"model": {"name": "openai/gpt-5-4-mini"}},
    }


def test_exact_existing_autonomy_agent_is_a_noop() -> None:
    transport = ProvisionTransport(
        existing_autonomy={
            "id": "agent-autonomy",
            "name": AGENT_NAME,
            "manifest": build_agent_spec(),
        }
    )
    result = TrueForgeClient(transport=transport).provision_autonomy(
        bearer="runtime-bearer-token-value"
    )
    assert result.agent_action == "unchanged"
    agent_writes = [
        (method, path)
        for method, path, _ in transport.calls
        if path.startswith("/api/v1/agents") and method != "GET"
    ]
    assert agent_writes == []


def test_changed_autonomy_agent_updates_only_its_immutable_id() -> None:
    transport = ProvisionTransport(
        existing_autonomy={
            "id": "agent-autonomy",
            "name": AGENT_NAME,
            "manifest": {"model": {"name": DEFAULT_MODEL}},
        }
    )
    result = TrueForgeClient(transport=transport).provision_autonomy(
        bearer="runtime-bearer-token-value"
    )
    assert result.agent_action == "updated"
    assert ("PUT", "/api/v1/agents/agent-autonomy") in [
        (method, path) for method, path, _ in transport.calls
    ]


def test_provision_rejects_empty_tool_schemas() -> None:
    transport = ProvisionTransport()

    def empty_schemas(
        method: str, path: str, payload: Mapping[str, Any] | None
    ) -> tuple[int, Mapping[str, Any]]:
        if (method, path) == (
            "GET",
            f"/api/v1/mcp-servers/{MCP_SERVER_NAME}/tools",
        ):
            tools = _tools()
            for tool in tools:
                tool["inputSchema"] = {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                }
            return 200, {"data": tools}
        return transport(method, path, payload)

    with pytest.raises(TrueForgeError, match="differs from the Asset Autopsy contract"):
        TrueForgeClient(transport=empty_schemas).provision_autonomy(
            bearer="runtime-bearer-token-value"
        )


def test_provision_requires_the_agent_in_the_post_write_list() -> None:
    transport = ProvisionTransport()
    created = False

    def missing_after_write(
        method: str, path: str, payload: Mapping[str, Any] | None
    ) -> tuple[int, Mapping[str, Any]]:
        nonlocal created
        if (method, path) == ("GET", "/api/v1/agents") and created:
            return 200, {"data": [transport.starter]}
        result = transport(method, path, payload)
        if (method, path) == ("POST", "/api/v1/agents"):
            created = True
        return result

    with pytest.raises(TrueForgeError, match="not persisted exactly"):
        TrueForgeClient(transport=missing_after_write).provision_autonomy(
            bearer="runtime-bearer-token-value"
        )


def test_provision_fails_before_writes_without_saved_model_or_starter() -> None:
    transport = ProvisionTransport()

    def missing_model(
        method: str, path: str, payload: Mapping[str, Any] | None
    ) -> tuple[int, Mapping[str, Any]]:
        if (method, path) == ("GET", "/api/v1/models"):
            return 200, {"data": []}
        return transport(method, path, payload)

    with pytest.raises(TrueForgeError, match="saved OpenAI model"):
        TrueForgeClient(transport=missing_model).provision_autonomy(
            bearer="runtime-bearer-token-value"
        )
    assert all(method == "GET" for method, _, _ in transport.calls)
