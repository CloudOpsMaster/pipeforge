"""Render an event journal locally without executing event content."""

import html
import json
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from pipeforge.errors import ConfigError

STATUSES = {
    "success": "#4ade80",
    "failed": "#fb7185",
    "running": "#38bdf8",
    "blocked": "#94a3b8",
    "pending": "#cbd5e1",
    "cancelled": "#fbbf24",
    "skipped": "#94a3b8",
    "warning": "#fbbf24",
    "approval": "#fb923c",
}
MAX_REPORT_BYTES = 32 * 1024 * 1024


def valid_details(event: dict[str, Any]) -> bool:
    timestamp = datetime.fromisoformat(event["timestamp"])
    if timestamp.tzinfo is None:
        return False
    source = event.get("source")
    if source is not None and (
        not isinstance(source, dict)
        or not isinstance(source.get("file"), str)
        or type(source.get("line")) is not int
        or source["line"] < 1
    ):
        return False
    for key in ("dependencies", "children", "artifacts"):
        items = event.get(key, [])
        if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
            return False
    duration = event.get("duration_ms", 0)
    exit_code = event.get("exit_code")
    return (
        isinstance(event.get("message", ""), str)
        and type(duration) is int
        and 0 <= duration <= 2**53 - 1
        and (exit_code is None or type(exit_code) is int)
        and event.get("kind", "command") in ("command", "group")
    )


def read_events(path: Path) -> list[dict[str, Any]]:
    with path.open("rb") as source:
        data = source.read(MAX_REPORT_BYTES + 1)
    if len(data) > MAX_REPORT_BYTES:
        raise ConfigError("Event journal exceeds 32 MiB.")
    result: list[dict[str, Any]] = []
    try:
        for line in data.decode("utf-8").splitlines():
            event = json.loads(line)
            if (
                not isinstance(event, dict)
                or type(event.get("schema_version")) is not int
                or event["schema_version"] != 1
                or type(event.get("sequence")) is not int
                or event["sequence"] != len(result)
                or not all(
                    isinstance(event.get(key), str)
                    for key in ("run_id", "node_id", "rule", "timestamp", "status")
                )
                or event["status"] not in STATUSES
                or (result and result[0]["run_id"] != event["run_id"])
                or not valid_details(event)
                or len(result) >= 2048
            ):
                raise ValueError
            result.append(event)
    except (ValueError, RecursionError):
        raise ConfigError("Invalid or out-of-order version 1 event journal.") from None
    if not result:
        raise ConfigError("Event journal contains no events.")
    return result


def svg(events: list[dict[str, Any]]) -> str:
    latest = {event["node_id"]: event for event in events}
    parents = {
        child: event["node_id"] for event in latest.values() for child in event.get("children", [])
    }
    upstream = {
        node_id: event.get("dependencies") or ([parents[node_id]] if node_id in parents else [])
        for node_id, event in latest.items()
    }
    columns: dict[int, list[str]] = {}
    depths: dict[str, int] = {}
    for node_id in latest:
        depth = 1 + max((depths.get(parent, -1) for parent in upstream[node_id]), default=-1)
        depths[node_id] = depth
        columns.setdefault(depth, []).append(node_id)

    width = max(780, len(columns) * 180)
    height = max(240, max(map(len, columns.values())) * 105 + 80)
    positions = {
        node_id: (
            (column + 0.5) * width / len(columns),
            70 + (row + 0.5) * (height - 80) / len(ids),
        )
        for column, (_, ids) in enumerate(sorted(columns.items()))
        for row, node_id in enumerate(ids)
    }
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="system-ui" font-size="15">',
        '<rect width="100%" height="100%" fill="#0b1220"/>',
        '<text x="22" y="36" fill="white" font-size="24">PipeForge Studio</text>',
    ]
    for node_id, parents_of_node in upstream.items():
        x, y = positions[node_id]
        for parent in parents_of_node:
            if parent in positions:
                px, py = positions[parent]
                parts.append(f'<path d="M{px} {py} L{x} {y}" stroke="#64748b" stroke-width="2"/>')
    for node_id, event in latest.items():
        x, y = positions[node_id]
        label = html.escape(event["rule"])
        status = event["status"]
        dash = ' stroke-dasharray="5 4"' if status == "blocked" else ""
        parts.append(
            f"<g><title>{label} · {status}</title>"
            f'<circle cx="{x}" cy="{y}" r="16" fill="#19405b" '
            f'stroke="{STATUSES[status]}" stroke-width="3"{dash}/>'
            f'<text x="{x}" y="{y + 36}" fill="#e2e8f0" text-anchor="middle">'
            f"{html.escape(event['rule'][:22])}</text></g>"
        )
    return "".join(parts) + "</svg>\n"


def report(events: Path, output: Path, format: str) -> None:
    journal = read_events(events)
    if format == "json":
        content = json.dumps(journal, indent=2) + "\n"
    elif format == "svg":
        content = svg(journal)
    else:
        assets = files("pipeforge").joinpath("studio_assets")
        template = assets.joinpath("report.html").read_text(encoding="utf-8")
        template = template.replace(
            "/*NEURAL*/", assets.joinpath("neural.js").read_text(encoding="utf-8")
        )
        payload = json.dumps(journal).replace("<", r"\u003c")
        content = template.replace(
            "/*TOKENS*/", assets.joinpath("tokens.css").read_text(encoding="utf-8")
        ).replace("/*EVENTS*/", payload)
    with output.open("x", encoding="utf-8") as handle:
        handle.write(content)
