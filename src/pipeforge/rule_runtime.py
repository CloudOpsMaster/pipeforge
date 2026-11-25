"""Run expanded rule calls and record ordered, redacted events."""

import hashlib
import json
import os
import shutil
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Lock, Semaphore
from typing import Any, TextIO
from uuid import uuid4

from pipeforge.errors import ConfigError, ExecutionError
from pipeforge.executor import execute
from pipeforge.logging import Masker, safe_text
from pipeforge.rules import ARG_INPUT, Node, RuleSet, Tool


def nodes(node: Node) -> Iterator[Node]:
    yield node
    for group in node.children:
        for child in group:
            yield from nodes(child)


def argument(value: Any) -> str:
    return json.dumps(value) if isinstance(value, bool) else str(value)


class RuleRuntime:
    def __init__(self, spec: RuleSet, output: TextIO, journal: TextIO | None):
        self.output = output
        self.journal = journal
        self.lock = Lock()
        self.slots = Semaphore(16)
        self.cancelled = Event()
        self.sequence = 0
        self.run_id = uuid4().hex
        self.env = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "TERM"}
            or key in spec.secrets
        }
        self.env.setdefault("PATH", os.defpath)
        self.masker = Masker(self.env.get(key, "") for key in spec.secrets)

    def emit(self, node: Node, status: str, **details: Any) -> None:
        with self.lock:
            event = {
                "schema_version": 1,
                "run_id": self.run_id,
                "sequence": self.sequence,
                "timestamp": datetime.now(UTC).isoformat(),
                "node_id": node.id,
                "rule": self.masker.mask(node.rule.name),
                "status": status,
                "source": {
                    "file": self.masker.mask(node.rule.source.file),
                    "line": node.rule.source.line,
                },
                "dependencies": node.dependencies,
                "kind": "group" if node.children else "command",
                "children": [child.id for group in node.children for child in group],
                **details,
            }
            self.sequence += 1
            if self.journal:
                self.journal.write(json.dumps(event) + "\n")
                self.journal.flush()
            self.output.write(f"{node.id} {event['rule']} {status}\n")
            if details.get("message"):
                self.output.write(details["message"] + "\n")

    def check_tools(self, root: Node) -> None:
        checked: set[tuple[Path, str]] = set()
        for node in nodes(root):
            command = node.rule.command
            tools = node.rule.tools
            if isinstance(command, tuple):
                tools += (Tool(command[0]),)
            for tool in tools:
                if "/" in tool.name:
                    path = node.rule.cwd / tool.name
                    found = str(path) if path.is_file() and os.access(path, os.X_OK) else None
                else:
                    paths = os.pathsep.join(
                        str(node.rule.cwd / entry) for entry in self.env["PATH"].split(os.pathsep)
                    )
                    found = shutil.which(tool.name, path=paths)
                label = safe_text(self.masker.mask(tool.name))
                rule = self.masker.mask(node.rule.name)
                if found is None:
                    raise ConfigError(f"{rule}: required executable unavailable ({label}).")
                if tool.digest is None:
                    continue
                check = (Path(found), tool.digest)
                if check in checked:
                    continue
                with check[0].open("rb") as handle:
                    digest = hashlib.file_digest(handle, "sha256").hexdigest()
                if tool.digest != f"sha256:{digest}":
                    raise ConfigError(f"{rule}: executable SHA-256 mismatch ({label}).")
                checked.add(check)

    def block(self, node: Node) -> None:
        for child in nodes(node):
            self.emit(child, "cancelled" if self.cancelled.is_set() else "blocked")

    def run(self, node: Node) -> int:
        if self.cancelled.is_set():
            self.block(node)
            return 130
        try:
            started = time.monotonic()
            self.emit(node, "running")
            if node.children:
                code = self.run_groups(node)
                self.emit(
                    node,
                    self.status(code),
                    exit_code=code,
                    duration_ms=round((time.monotonic() - started) * 1000),
                )
                return code
            return self.run_command(node)
        except BaseException:
            self.cancelled.set()
            self.emit(node, "cancelled")
            raise

    def status(self, code: int) -> str:
        if self.cancelled.is_set():
            return "cancelled"
        return "failed" if code else "success"

    def run_groups(self, node: Node) -> int:
        code = 0
        for group in node.children:
            if code or self.cancelled.is_set():
                for child in group:
                    self.block(child)
            elif len(group) == 1:
                code = self.run(group[0])
            else:
                with ThreadPoolExecutor(max_workers=len(group)) as pool:
                    work = [pool.submit(self.run, child) for child in group]
                    try:
                        code = next((code for code in [job.result() for job in work] if code), 0)
                    except BaseException:
                        self.cancelled.set()
                        raise
        return code

    def run_command(self, node: Node) -> int:
        command = node.rule.command
        if command is None:
            raise ExecutionError("Expected an executable rule.")
        values = {name: argument(value) for name, value in node.inputs.items()}
        env = self.env | {f"PIPEFORGE_INPUT_{key}": value for key, value in values.items()}
        if isinstance(command, tuple):
            command = (command[0],) + tuple(
                values[match[1]] if (match := ARG_INPUT.fullmatch(item)) else item
                for item in command[1:]
            )
        try:
            with self.slots:
                result = execute(
                    command, node.rule.cwd, env, node.rule.timeout, cancelled=self.cancelled
                )
        except ExecutionError as error:
            self.emit(node, "failed", exit_code=1, message=safe_text(str(error)))
            return 1
        code = result.code if result.code >= 0 else 128 - result.code
        if result.reason == "timeout":
            code = 124
        logs = safe_text(self.masker.mask(result.output or result.reason)).rstrip()
        if len(logs) > 8192:
            logs = logs[:8192] + "\n[Output truncated]"
        self.emit(
            node,
            self.status(code),
            exit_code=code,
            duration_ms=round(result.duration * 1000),
            message=logs,
        )
        return code

    def invoke(self, node: Node) -> int:
        self.check_tools(node)
        for child in nodes(node):
            self.emit(child, "pending")
        return self.run(node)


def write_events(path: Path) -> TextIO:
    # Exclusive creation prevents accidentally truncating source files and prior runs.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(descriptor, "w", encoding="utf-8")
