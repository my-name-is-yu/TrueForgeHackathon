from __future__ import annotations

import argparse

import uvicorn

from .workbench import create_workbench_app


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the local Asset Autopsy workbench"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8713, type=int)
    arguments = parser.parse_args()
    if arguments.host != "127.0.0.1":
        parser.error("the local workbench must bind to 127.0.0.1")
    uvicorn.run(create_workbench_app(), host=arguments.host, port=arguments.port)


if __name__ == "__main__":
    main()
