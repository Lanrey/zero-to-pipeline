"""MCP stdio transport implementation."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from data_pipeline.mcp.server import PipelineMCPServer


def _read_message() -> dict[str, Any] | None:
    content_length = 0
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        stripped = line.strip()
        if not stripped:
            break
        header = line.decode("utf-8", errors="replace").strip()
        if header.lower().startswith("content-length:"):
            content_length = int(header.split(":", 1)[1].strip())

    if content_length <= 0:
        return None
    body = sys.stdin.buffer.read(content_length)
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def _write_message(payload: dict[str, Any]) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode()
    sys.stdout.buffer.write(header)
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def run_stdio_loop(server: PipelineMCPServer) -> None:
    """Run the MCP stdio message loop."""
    loop = asyncio.new_event_loop()

    while True:
        message = _read_message()
        if message is None:
            return

        method = message.get("method")
        request_id = message.get("id")

        if request_id is None:
            continue

        try:
            if method == "initialize":
                _write_message({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": server.PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {
                            "name": server.SERVER_NAME,
                            "version": server.SERVER_VERSION,
                        },
                    },
                })
            elif method == "tools/list":
                _write_message({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"tools": server.get_tools()},
                })
            elif method == "tools/call":
                params = message.get("params", {})
                name = params.get("name", "")
                arguments = params.get("arguments", {})
                content, is_error = loop.run_until_complete(
                    server.handle_tool_call(name, arguments)
                )
                _write_message({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [{"type": "text", "text": content}],
                        "isError": is_error,
                    },
                })
            else:
                _write_message({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                })
        except Exception as e:
            _write_message({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": str(e)},
            })
