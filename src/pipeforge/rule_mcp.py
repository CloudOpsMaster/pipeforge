"""Expose one operator-selected journal through a read-only MCP stdio server."""

import json
import re
import sys
from pathlib import Path
from typing import Any, BinaryIO, TextIO

from pipeforge import __version__
from pipeforge.studio import read_events

PROTOCOL = "2025-11-25"
MAX_FRAME = 64 * 1024
NODE_ID = re.compile(r"n[1-9][0-9]{0,2}\Z")
TOOL = {
    "name": "execution_graph",
    "description": "Return statuses, dependencies and timings from a preselected run.",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
}


def graph(journal: Path) -> dict[str, Any]:
    latest = {event["node_id"]: event for event in read_events(journal)}
    public = []
    for node_id, event in latest.items():
        if not NODE_ID.fullmatch(node_id):
            continue
        public.append(
            {
                "node_id": node_id,
                "status": event["status"],
                "kind": event.get("kind", "command"),
                "duration_ms": event.get("duration_ms"),
                "exit_code": event.get("exit_code"),
                "dependencies": [
                    value
                    for value in event.get("dependencies", [])
                    if value in latest and NODE_ID.fullmatch(value)
                ],
                "children": [
                    value
                    for value in event.get("children", [])
                    if value in latest and NODE_ID.fullmatch(value)
                ],
            }
        )
    return {"nodes": public}


def serve(journal: Path, source: BinaryIO | None = None, sink: TextIO | None = None) -> None:
    snapshot = graph(journal)
    source = source or sys.stdin.buffer
    sink = sink or sys.stdout
    initialized = False

    def send(request_id: Any, **body: Any) -> None:
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, **body}), file=sink, flush=True)

    def error(request_id: Any, code: int, message: str) -> None:
        send(request_id, error={"code": code, "message": message})

    while line := source.readline(MAX_FRAME + 1):
        if len(line) > MAX_FRAME:
            error(None, -32600, "Frame exceeds limit.")
            return
        try:
            request = json.loads(line)
        except (ValueError, RecursionError):
            error(None, -32700, "Invalid JSON.")
            continue
        if (
            not isinstance(request, dict)
            or request.get("jsonrpc") != "2.0"
            or not isinstance(request.get("method"), str)
            or ("id" in request and type(request["id"]) not in (str, int))
        ):
            error(None, -32600, "Invalid request.")
            continue
        if "id" not in request:
            continue
        request_id, method = request["id"], request["method"]
        params = request.get("params", {})
        if not isinstance(params, dict):
            error(request_id, -32602, "Invalid params.")
        elif method == "initialize" and not initialized:
            initialized = True
            send(
                request_id,
                result={
                    "protocolVersion": PROTOCOL,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "pipeforge", "version": __version__},
                },
            )
        elif method == "ping":
            send(request_id, result={})
        elif not initialized:
            error(request_id, -32002, "Initialize the session first.")
        elif method == "tools/list":
            send(request_id, result={"tools": [TOOL]})
        elif method == "tools/call":
            if params.get("name") != TOOL["name"] or params.get("arguments", {}) != {}:
                error(request_id, -32602, "Unknown tool or arguments.")
                continue
            send(
                request_id,
                result={
                    "content": [{"type": "text", "text": json.dumps(snapshot)}],
                    "structuredContent": snapshot,
                },
            )
        else:
            error(request_id, -32601, "Method not found.")
