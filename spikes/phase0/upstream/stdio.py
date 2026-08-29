from __future__ import annotations

import os
import sys
from collections.abc import Awaitable, Callable
from typing import TypeVar

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

T = TypeVar("T")

REQUIRED_ENVIRONMENT = {
    "MUJOCO_GL": "cgl",
    "MUJOCO_MCP_MAX_WORKERS": "1",
    "MUJOCO_MCP_RENDER_WIDTH": "640",
    "MUJOCO_MCP_RENDER_HEIGHT": "480",
}


def server_parameters() -> StdioServerParameters:
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PYTHONDONTWRITEBYTECODE": "1",
        **REQUIRED_ENVIRONMENT,
    }
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "mujoco_mcp", "--transport", "stdio"],
        env=environment,
    )


async def with_stdio_session(
    callback: Callable[[ClientSession], Awaitable[T]],
) -> T:
    async with stdio_client(server_parameters()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await callback(session)
