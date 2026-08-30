from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from spikes.phase0.upstream.adapter import (
    BAD_RESPONSE,
    SAFE_MESSAGE,
    SAFE_NEXT_ACTION,
    UpstreamToolError,
    normalize_json_result,
)
from spikes.phase0.upstream.contract import (
    INVALID_XML,
    PRIMITIVE_XML,
    REQUIRED_TOOL_NAMES,
    REQUIRED_TOOL_SCHEMAS,
    matches_sim_load_result,
)
from spikes.phase0.upstream.stdio import with_stdio_session


ROOT = Path(__file__).resolve().parents[3]
UPSTREAM_COMMIT = "ce9bed80ec3698d7b778230abc21f2228a3ce94b"
RENDER_SUMMARY = re.compile(
    r"t=-?\d+\.\d{4}s \| \d+ contacts \| "
    r"E=\[-?\d+\.\d{3}, -?\d+\.\d{3}\]"
)


def _render_png_data(result) -> str | None:
    blocks = getattr(result, "content", None)
    if not isinstance(blocks, list) or len(blocks) != 2:
        return None
    image, summary = blocks
    if (
        getattr(image, "type", None) != "image"
        or getattr(image, "mimeType", None) != "image/png"
        or not isinstance(getattr(image, "data", None), str)
        or getattr(summary, "type", None) != "text"
        or not isinstance(getattr(summary, "text", None), str)
        or RENDER_SUMMARY.fullmatch(summary.text) is None
    ):
        return None
    return image.data


def test_dependency_lock_is_exact_and_frozen() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    dependencies = project["project"]["dependencies"]
    assert "mcp==1.26.0" in dependencies
    assert "mujoco==3.5.0" in dependencies
    assert (
        "mujoco-mcp-server @ git+https://github.com/Rongxuan-Zhou/"
        f"mujoco-mcp-server.git@{UPSTREAM_COMMIT}"
    ) in dependencies

    lock_text = (ROOT / "uv.lock").read_text()
    assert 'name = "mcp"\nversion = "1.26.0"' in lock_text
    assert 'name = "mujoco"\nversion = "3.5.0"' in lock_text
    assert f"#{UPSTREAM_COMMIT}" in lock_text


@pytest.mark.phase0_upstream
def test_stdio_initializes_and_matches_required_schemas() -> None:
    async def check() -> None:
        async def collect(session) -> None:
            result = await session.list_tools()
            actual = {tool.name: tool.inputSchema for tool in result.tools}
            assert set(REQUIRED_TOOL_NAMES).issubset(actual)
            for name in REQUIRED_TOOL_NAMES:
                assert actual[name] == REQUIRED_TOOL_SCHEMAS[name]

        await with_stdio_session(collect)

    asyncio.run(check())


@pytest.mark.cgl
@pytest.mark.phase0_upstream
def test_cgl_renders_160_by_120_primitive_scene() -> None:
    async def render() -> bool:
        async def call(session) -> bool:
            try:
                loaded = await session.call_tool(
                    "sim_load",
                    arguments={"name": "phase0", "xml_string": PRIMITIVE_XML},
                )
                summary = normalize_json_result(loaded, matches_sim_load_result)
                if summary.get("has_renderer") is not True:
                    return False

                result = await session.call_tool(
                    "render_snapshot",
                    arguments={"sim_name": "phase0", "width": 160, "height": 120},
                )
                if result.isError:
                    return False
                png_data = _render_png_data(result)
                if png_data is None:
                    return False
                image = Image.open(io.BytesIO(base64.b64decode(png_data)))
                return image.size == (160, 120)
            except Exception:
                return False

        try:
            return await with_stdio_session(call)
        except Exception:
            return False

    assert asyncio.run(render()) is True


@pytest.mark.phase0_upstream
def test_success_wrapped_upstream_error_is_bounded_and_sanitized() -> None:
    async def call_invalid_model():
        async def call(session):
            return await session.call_tool(
                "sim_load",
                arguments={"name": "invalid", "xml_string": INVALID_XML},
            )

        return await with_stdio_session(call)

    result = asyncio.run(call_invalid_model())
    assert result.isError is False
    with pytest.raises(UpstreamToolError) as caught:
        normalize_json_result(result, matches_sim_load_result)

    error = caught.value
    envelope = error.envelope()
    assert envelope == {
        "code": "UPSTREAM_UNAVAILABLE",
        "message": SAFE_MESSAGE,
        "retryable": True,
        "next_action": SAFE_NEXT_ACTION,
    }
    serialized = json.dumps(envelope)
    assert len(serialized) <= 256
    assert "traceback" not in serialized.lower()
    assert "<mujoco>" not in serialized
    assert "invalid_input" not in serialized
    assert len(error.message) <= 96
    assert len(error.next_action) <= 96


def test_adapter_rejects_additional_response_content() -> None:
    result = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text='{"ok": true}'),
            SimpleNamespace(type="text", text='{"error": "private traceback"}'),
        ],
        isError=False,
    )

    with pytest.raises(UpstreamToolError) as caught:
        normalize_json_result(result, matches_sim_load_result)

    assert caught.value.envelope() == {
        "code": BAD_RESPONSE,
        "message": "Upstream response content was unexpected.",
        "retryable": False,
        "next_action": "Do not reuse the affected simulation slot.",
    }


def test_adapter_rejects_schema_drift() -> None:
    result = SimpleNamespace(
        content=[SimpleNamespace(type="text", text='{"has_renderer": true}')],
        isError=False,
    )

    with pytest.raises(UpstreamToolError) as caught:
        normalize_json_result(result, matches_sim_load_result)

    assert caught.value.code == BAD_RESPONSE


def test_render_gate_rejects_additional_response_content() -> None:
    unexpected_summary = SimpleNamespace(
        content=[
            SimpleNamespace(type="image", mimeType="image/png", data="data"),
            SimpleNamespace(type="text", text="private traceback"),
        ]
    )
    additional_block = SimpleNamespace(
        content=[
            SimpleNamespace(type="image", mimeType="image/png", data="data"),
            SimpleNamespace(
                type="text",
                text="t=0.0000s | 0 contacts | E=[0.000, 0.000]",
            ),
            SimpleNamespace(type="text", text="private traceback"),
        ]
    )

    assert _render_png_data(unexpected_summary) is None
    assert _render_png_data(additional_block) is None
