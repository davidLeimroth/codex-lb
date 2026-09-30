"""Per-request stdio MCP server advertising client-owned tools to the Claude CLI.

Run by path with the stdlib only: ``python mcp.py <tools.json>``. ``tools/list`` returns
the client's original names and JSON schemas. ``tools/call`` is never answered: the
bridge kills the CLI process group as soon as the tool_use is captured, so client
tools are never executed here.
"""

from __future__ import annotations

import json
import sys
from typing import TextIO

JsonDict = dict[str, object]


def handle(message: JsonDict, tools: list[JsonDict]) -> JsonDict | None:
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None or method == "tools/call":
        return None  # notifications, and tool calls that intentionally block until killed
    if method == "initialize":
        params = message.get("params")
        version = params.get("protocolVersion") if isinstance(params, dict) else None
        result: JsonDict = {
            "protocolVersion": version or "2025-06-18",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "codex-lb-client-tools", "version": "1"},
        }
    elif method == "tools/list":
        result = {"tools": tools}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "method not found"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve(tools: list[JsonDict], stdin: TextIO, stdout: TextIO) -> None:
    for line in stdin:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        reply = handle(message, tools)
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as handle_file:
        serve(json.load(handle_file), sys.stdin, sys.stdout)
