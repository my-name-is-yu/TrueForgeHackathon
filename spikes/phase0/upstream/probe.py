from __future__ import annotations

import asyncio
import json

from .contract import REQUIRED_TOOL_NAMES
from .stdio import with_stdio_session


async def inspect_server() -> dict[str, object]:
    async def collect(session):
        result = await session.list_tools()
        by_name = {tool.name: tool for tool in result.tools}
        return {
            "tool_count": len(result.tools),
            "missing_tools": [
                name for name in REQUIRED_TOOL_NAMES if name not in by_name
            ],
            "required_tools": [
                {"name": name, "input_schema": by_name[name].inputSchema}
                for name in REQUIRED_TOOL_NAMES
                if name in by_name
            ],
        }

    return await with_stdio_session(collect)


def main() -> None:
    print(json.dumps(asyncio.run(inspect_server()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
