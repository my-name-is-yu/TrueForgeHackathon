from __future__ import annotations

import base64
import json
import re
import shlex
import socket
import struct
import threading
import zlib
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


PLANNED_TOOLS = (
    "open_case",
    "inspect_asset",
    "run_task",
    "run_probe",
    "create_revision",
    "verify_revision",
    "publish_revision",
)

DUMMY_TOOLS = ("inspect_asset", "publish_revision")


def planned_tool_schemas() -> list[dict[str, Any]]:
    annotations = {
        "open_case": (True, False, True),
        "inspect_asset": (True, False, True),
        "run_task": (False, False, False),
        "run_probe": (False, False, False),
        "create_revision": (False, False, True),
        "verify_revision": (False, False, True),
        "publish_revision": (False, True, True),
    }
    return [
        {
            "name": name,
            "description": "Synthetic Phase 0 schema selector; only inspect and publish are callable probes.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": {
                "readOnlyHint": read_only,
                "destructiveHint": destructive,
                "idempotentHint": idempotent,
            },
        }
        for name in PLANNED_TOOLS
        for read_only, destructive, idempotent in [annotations[name]]
    ]


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def make_png(width: int = 160, height: int = 120) -> bytes:
    row = b"\x00" + bytes((31, 119, 212, 255)) * width
    pixels = zlib.compress(row * height, level=9)
    return b"\x89PNG\r\n\x1a\n" + b"".join(
        (
            _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)),
            _png_chunk(b"IDAT", pixels),
            _png_chunk(b"IEND", b""),
        )
    )


def png_dimensions(data: bytes) -> tuple[int, int]:
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("not a PNG")
    return struct.unpack(">II", data[16:24])


def inspection_payload(row_count: int = 256) -> dict[str, Any]:
    return {
        "rows": [
            {
                "index": index,
                "time": index / 1000,
                "qpos": [index / 100, -(index / 200)],
                "qvel": [index / 300, -(index / 400)],
                "ee_xyz": [index / 500, index / 600, index / 700],
                "control": [index / 800],
                "payload": "synthetic-phase0-row-" + str(index).zfill(3) + ("-" * 120),
            }
            for index in range(row_count)
        ],
        "source": "synthetic-phase0-facade",
    }


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class DummyFacade:
    def __init__(self, bearer: str, allowed_origin: str, image: bytes | None = None) -> None:
        self.bearer = bearer
        self.allowed_origin = allowed_origin
        self.image = image or make_png()
        self.requests: list[dict[str, Any]] = []
        self.tool_calls: list[str] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_type())
        self._server.owner = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _handler_type(self) -> type[BaseHTTPRequestHandler]:
        owner_type = type(self)

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                owner: DummyFacade = self.server.owner  # type: ignore[attr-defined]
                size = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(size)
                auth_ok = self.headers.get("Authorization") == f"Bearer {owner.bearer}"
                origin_ok = self.headers.get("Origin") == owner.allowed_origin
                request: dict[str, Any] = {
                    "auth_ok": auth_ok,
                    "origin_ok": origin_ok,
                    "path": urlsplit(self.path).path,
                    "body_bytes": len(raw),
                }
                if not auth_ok or not origin_ok:
                    owner.requests.append(request)
                    _json_response(self, 401 if not auth_ok else 403, {"error": "connection rejected"})
                    return
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    owner.requests.append(request)
                    _json_response(self, 400, {"error": "invalid request"})
                    return

                method = message.get("method")
                request["rpc_method"] = method
                if method == "tools/call":
                    tool_name = message.get("params", {}).get("name")
                    request["tool_name"] = tool_name
                    owner.tool_calls.append(tool_name)
                owner.requests.append(request)

                if method == "notifications/initialized":
                    self.send_response(202)
                    self.end_headers()
                    return
                if method == "initialize":
                    result: dict[str, Any] = {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "phase0-dummy-facade", "version": "0.0.0"},
                    }
                elif method == "tools/list":
                    result = {
                        "tools": planned_tool_schemas()
                    }
                elif method == "tools/call":
                    tool_name = message.get("params", {}).get("name")
                    if tool_name == "inspect_asset":
                        result = {
                            "content": [
                                {"type": "text", "text": json.dumps(inspection_payload(), separators=(",", ":"))},
                                {
                                    "type": "image",
                                    "data": base64.b64encode(owner.image).decode("ascii"),
                                    "mimeType": "image/png",
                                },
                            ],
                            "isError": False,
                        }
                    elif tool_name == "publish_revision":
                        result = {
                            "content": [{"type": "text", "text": json.dumps({"published": True})}],
                            "isError": False,
                        }
                    else:
                        result = {"content": [{"type": "text", "text": "unknown synthetic tool"}], "isError": True}
                else:
                    result = None
                _json_response(self, 200, {"jsonrpc": "2.0", "id": message.get("id"), "result": result})

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        Handler.__name__ = f"{owner_type.__name__}Handler"
        return Handler

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/mcp"

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def tool_call_counts(self) -> Counter[str]:
        return Counter(self.tool_calls)


class ModelServer:
    def __init__(self, checkout_root: Path) -> None:
        self.checkout_root = checkout_root
        self.request_count = 0
        self.saw_ltr_reference = False
        self.saw_sandbox_analysis = False
        self.saw_checkout_isolation = False
        self.saw_private_runtime_isolation = False
        self.saw_network_measurement = False
        self.saw_image_data = False
        self.saw_sandbox_exec = False
        self.saw_publish_request = False
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_type())
        self._server.owner = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _handler_type(self) -> type[BaseHTTPRequestHandler]:
        owner_type = type(self)

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                owner: ModelServer = self.server.owner  # type: ignore[attr-defined]
                size = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(size)
                owner.request_count += 1
                owner.saw_image_data |= b"data:image" in raw or b'"type":"image_url"' in raw
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    _json_response(self, 400, {"error": {"message": "invalid model request"}})
                    return
                messages = body.get("messages", [])
                text = json.dumps(messages, separators=(",", ":"))
                ltr_match = re.search(r"Result saved to: (.*?)(?:\\n|$)", text)
                if ltr_match:
                    owner.saw_ltr_reference = True
                if "rows" in text and "256" in text and "analyzed" in text:
                    owner.saw_sandbox_analysis = True
                if '"checkout_present"' in text and 'false' in text:
                    owner.saw_checkout_isolation = True
                if '"private_runtime_present"' in text and 'false' in text:
                    owner.saw_private_runtime_isolation = True
                if '"network_attempted"' in text and 'true' in text:
                    owner.saw_network_measurement = True
                if owner.saw_sandbox_exec and owner.request_count >= 3:
                    owner.saw_sandbox_analysis = True

                if owner.saw_sandbox_analysis:
                    name = "publish_revision"
                    arguments = {"revision": "synthetic-phase0"}
                    call_id = "phase0-publish"
                    owner.saw_publish_request = True
                elif ltr_match:
                    owner.saw_sandbox_exec = True
                    name = "exec"
                    sandbox_path = ltr_match.group(1).strip().rstrip(".")
                    code = (
                        "import json,pathlib,socket\n"
                        f"data=json.load(open({sandbox_path!r}))\n"
                        "checkout_present=any(pathlib.Path(x).exists() for x in ('.git','pyproject.toml','README.md'))\n"
                        "private_runtime_present=pathlib.Path('private-runtime').exists()\n"
                        "network='reachable'\n"
                        "try:\n"
                        "    socket.create_connection(('example.com',80),1).close()\n"
                        "except OSError:\n"
                        "    network='blocked'\n"
                        "print(json.dumps({'rows':len(data['rows']),'analyzed':True,'checkout_present':checkout_present,'private_runtime_present':private_runtime_present,'network_attempted':True,'network':network},separators=(',',':')))"
                    )
                    arguments = {"intent": "Analyze the offloaded synthetic trace and measure sandbox boundaries.", "command": f"python -c {shlex.quote(code)}"}
                    call_id = "phase0-sandbox"
                else:
                    name = "inspect_asset"
                    arguments = {}
                    call_id = "phase0-inspect"
                self._send_tool_call(name, arguments, call_id)

            def _send_tool_call(self, name: str, arguments: dict[str, Any], call_id: str) -> None:
                now = 1_700_000_000
                first = {
                    "id": "chatcmpl-phase0",
                    "object": "chat.completion.chunk",
                    "created": now,
                    "model": "phase0-model",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": call_id,
                                        "type": "function",
                                        "function": {"name": name, "arguments": json.dumps(arguments, separators=(",", ":"))},
                                    }
                                ],
                            },
                            "finish_reason": None,
                        }
                    ],
                }
                last = {
                    "id": "chatcmpl-phase0",
                    "object": "chat.completion.chunk",
                    "created": now,
                    "model": "phase0-model",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
                payload = f"data: {json.dumps(first, separators=(',', ':'))}\n\n" + f"data: {json.dumps(last, separators=(',', ':'))}\n\n" + "data: [DONE]\n\n"
                data = payload.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        Handler.__name__ = f"{owner_type.__name__}Handler"
        return Handler

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/v1"

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


def extract_sandbox_path(text: str) -> str | None:
    match = re.search(r"Result saved to: (.*?)(?:\\n|$)", text)
    return match.group(1).strip().rstrip(".") if match else None
