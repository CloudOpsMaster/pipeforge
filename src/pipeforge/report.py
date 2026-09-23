"""Private run artifacts containing only redacted text and structured metadata."""

import html
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from pipeforge.errors import ExecutionError
from pipeforge.logging import Masker, safe_text


def private_directory(path: Path) -> None:
    if path.is_symlink():
        raise ExecutionError("Runtime directories must not be symlinks.")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir():
        raise ExecutionError("Runtime path is not a directory.")


class RunReport:
    def __init__(self, directory: Path, masker: Masker, data: dict[str, Any]):
        self.masker = masker
        self.root = directory / ".pipeforge"
        private_directory(self.root)
        private_directory(self.root / "runs")
        run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f") + "-" + uuid.uuid4().hex[:8]
        self.path = self.root / "runs" / run_id
        self.path.mkdir(mode=0o700)
        self.data = data
        self.data.update(schema_version=1, run_id=run_id, started_at=datetime.now(UTC).isoformat())

    def open_log(self, job: str, index: int) -> tuple[TextIO, str]:
        # Dots are forbidden in job IDs, so fallback names cannot collide with a real job.
        name = job if self.masker.mask(job) == job else f"redacted.{index}"
        filename = f"{name}.log"
        return self._open(filename), filename

    def _open(self, filename: str) -> TextIO:
        descriptor = os.open(self.path / filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        return os.fdopen(descriptor, "w", encoding="utf-8")

    def redact(self, value: Any) -> Any:
        if isinstance(value, str):
            return safe_text(self.masker.mask(value))
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, dict):
            return {key: self.redact(item) for key, item in value.items()}
        return value

    def finish(self, result: str, duration: float) -> None:
        self.data.update(result=result, duration=round(duration, 3))
        clean = self.redact(self.data)
        with self._open("report.json") as handle:
            json.dump(clean, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        with self._open("summary.md") as handle:
            # HTML pre blocks avoid Markdown link/table injection from names and descriptions.
            handle.write("# PipeForge\n\n<pre>\n")
            handle.write(
                html.escape(f"{clean['pipeline']} • {clean['provider']} • {clean['result']}\n")
            )
            for job in clean["jobs"]:
                handle.write(
                    html.escape(f"{job['name']}: {job['status']} ({job['duration']:.2f}s)\n")
                )
            handle.write(f"Total: {duration:.2f}s\n</pre>\n")
        latest = self.root / "latest"
        if latest.exists() and not latest.is_symlink():
            raise ExecutionError(
                ".pipeforge/latest must be absent or a symlink; existing data preserved."
            )
        temporary = self.root / f".latest-{uuid.uuid4().hex}"
        try:
            temporary.symlink_to(Path("runs") / self.path.name)
            os.replace(temporary, latest)
        finally:
            temporary.unlink(missing_ok=True)
