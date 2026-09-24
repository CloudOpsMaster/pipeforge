"""Atomic checkpoints and retained artifacts for resumable, exclusively locked runs."""

import fcntl
import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from threading import RLock

from pipeforge.config import Config, Job
from pipeforge.errors import ExecutionError
from pipeforge.report import private_directory


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


class RunState:
    def __init__(self, config: Config, selected: tuple[Job, ...], resume: str | None, commit: str):
        self.mutex = RLock()
        self.config = config
        root = config.directory / ".pipeforge"
        private_directory(root)
        private_directory(root / "state")
        self.id = resume or uuid.uuid4().hex
        if not re.fullmatch(r"[0-9a-f]{32}", self.id):
            raise ExecutionError("Invalid resume ID; use the ID printed by the original run.")
        self.path = root / "state" / self.id
        if resume and not self.path.is_dir():
            raise ExecutionError("Saved run not found; restore its .pipeforge/state directory.")
        private_directory(self.path)
        self.artifacts = self.path / "artifacts"
        private_directory(self.artifacts)
        self.lock = os.open(self.path / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.lock)
            raise ExecutionError("This run is already executing in another process.") from None
        try:
            definition = asdict(config)
            definition.pop("directory")
            fingerprint = hashlib.sha256(
                json.dumps(definition, sort_keys=True).encode()
            ).hexdigest()
            if resume:
                try:
                    self.data = json.loads((self.path / "state.json").read_text())
                    if (
                        self.data["version"] != 1
                        or self.data["fingerprint"] != fingerprint
                        or self.data["commit"] != commit
                    ):
                        raise ExecutionError("Cannot resume after configuration or commit changes.")
                    jobs = self.data["jobs"]
                    if not isinstance(jobs, dict) or not set(jobs) <= {
                        job.name for job in config.execution_jobs
                    }:
                        raise ValueError
                    definitions = {job.name: job for job in config.execution_jobs}
                    if not jobs:
                        raise ValueError
                    if type(self.data.get("attempt")) is not int or self.data["attempt"] < 1:
                        raise ValueError
                    for name, entry in jobs.items():
                        if entry["status"] not in ("pending", "running", "failed", "success"):
                            raise ValueError
                        if entry["status"] == "success" and set(entry["artifacts"]) != set(
                            definitions[name].artifacts
                        ):
                            raise ValueError
                        for name, checksum in entry["artifacts"].items():
                            if digest(self.artifact(name)) != checksum:
                                raise ExecutionError(
                                    "Saved artifact changed; restore the original file."
                                )
                except (ValueError, KeyError, TypeError, AttributeError, OSError):
                    raise ExecutionError(
                        "Saved state or artifacts are missing or invalid."
                    ) from None
                self.data["attempt"] = int(self.data.get("attempt", 1)) + 1
                self.save()
            else:
                self.data = {
                    "version": 1,
                    "attempt": 1,
                    "fingerprint": fingerprint,
                    "commit": commit,
                    "jobs": {job.name: {"status": "pending", "artifacts": {}} for job in selected},
                }
                self.save()
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        os.close(self.lock)

    def artifact(self, name: str) -> Path:
        path = self.artifacts / name
        if (
            not name
            or Path(name).is_absolute()
            or ".." in Path(name).parts
            or not path.resolve().is_relative_to(self.artifacts.resolve())
            or any(
                parent.is_symlink()
                for parent in (path, *path.parents)
                if parent != self.artifacts.parent
            )
        ):
            raise ExecutionError("Artifact paths must stay inside the saved artifact directory.")
        return path

    def save(self) -> None:
        temporary = self.path / (".state-" + uuid.uuid4().hex)
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as handle:
                json.dump(self.data, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path / "state.json")
            descriptor = os.open(self.path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)

    def status(self, job: Job) -> str:
        return str(self.data["jobs"][job.name]["status"])

    def mark(self, job: Job, status: str) -> None:
        with self.mutex:
            entry = self.data["jobs"][job.name]
            if status == "success":
                checksums = {}
                for name in job.artifacts:
                    path = self.artifact(name)
                    if not path.is_file():
                        raise ExecutionError(
                            "Declared artifact is missing; refusing to checkpoint success."
                        )
                    checksums[name] = digest(path)
                entry["artifacts"] = checksums
            entry["status"] = status
            if status == "failed":
                self.data.setdefault("first_failure_at", time.time())
            self.save()

    def environment(self, job: Job) -> dict[str, str]:
        return {
            "PIPEFORGE_RUN_ID": self.id,
            "PIPEFORGE_ARTIFACTS": str(self.artifacts),
            "PIPEFORGE_JOB_KEY": hashlib.sha256(f"{self.id}:{job.name}".encode()).hexdigest(),
        }
