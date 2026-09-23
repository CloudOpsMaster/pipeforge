"""Plan and execute jobs with live, redacted output and durable run reports."""

import time
from collections.abc import Mapping
from typing import Any, TextIO

from pipeforge.config import Config, Job
from pipeforge.context import detect_provider, git_metadata
from pipeforge.errors import Terminated
from pipeforge.executor import execute
from pipeforge.logging import Logger, StreamingMasker, safe_text
from pipeforge.report import RunReport
from pipeforge.resolver import Resolver


class StepOutput:
    def __init__(self, logger: Logger, handle: TextIO):
        self.logger = logger
        self.handle = handle
        self.redactor = StreamingMasker(logger.masker)
        self.tail = ""

    def emit(self, text: str) -> None:
        if not text:
            return
        clean = safe_text(text)
        self.handle.write(clean)
        self.handle.flush()
        self.tail = (self.tail + clean)[-4096:]
        if self.logger.verbosity >= 2:
            self.logger.write(clean.rstrip("\n"), style="muted")

    def feed(self, text: str) -> None:
        self.emit(self.redactor.feed(text))

    def finish(self) -> None:
        self.emit(self.redactor.feed("", final=True))


def run(
    config: Config,
    environ: Mapping[str, str],
    logger: Logger,
    *,
    dry_run: bool = False,
    selected: tuple[Job, ...] | None = None,
) -> int:
    from pipeforge.graph import select_jobs

    selected = selected if selected is not None else select_jobs(config.execution_jobs)
    git = git_metadata(config.directory, environ)
    provider = detect_provider(environ)
    resolver = Resolver(config, environ, validate=dry_run, git=git)
    environments = resolver.resolve()
    resolved = {id(step): env for step, env in zip(config.pipeline, environments, strict=True)}
    logger.banner(config.name, provider, git["short_sha"])
    if config.legacy:
        logger.write("Legacy pipeline/script format; migrate to jobs/steps/run.", style="warn")
    selected_names = {job.name for job in selected}
    if dry_run:
        logger.write("PipeForge Plan", style="title")
        for job in config.execution_jobs:
            mode = "RUN" if job.name in selected_names else "SKIPPED"
            logger.write(f"{mode:7} {job.name}  needs: {', '.join(job.needs) or '—'}")
        logger.write("Execution order: " + " → ".join(job.name for job in selected))
        logger.write(f"{len(selected)} jobs • 0 commands executed", style="success")
        return 0

    started = time.monotonic()
    job_data: list[dict[str, Any]] = [
        {"name": job.name, "status": "skipped", "duration": 0.0, "steps": [], "log": None}
        for job in config.execution_jobs
    ]
    data = {
        "pipeline": config.name,
        "provider": provider,
        "commit": git["sha"],
        "branch": git["branch"],
        "tag": git["tag"],
        "jobs": job_data,
    }
    report = RunReport(config.directory, logger.masker, data)
    by_name = {entry["name"]: entry for entry in job_data}
    status = "cancelled"
    try:
        failed = False
        for index, job in enumerate(selected, 1):
            entry = by_name[job.name]
            entry["status"] = "running"
            job_started = time.monotonic()
            logger.write(f"▶ {job.name}")
            missing = set(job.needs) - selected_names
            if missing:
                logger.write(
                    "Assuming prerequisites already satisfied: " + ", ".join(sorted(missing)),
                    style="warn",
                )
            handle, filename = report.open_log(job.name, index)
            entry["log"] = filename
            try:
                with handle:
                    for step in job.steps:
                        if logger.verbosity:
                            logger.write(f"  ▶ {step.name} • timeout {step.timeout:g}s")
                        handle.write(safe_text(logger.masker.mask(f"\n=== {step.name} ===\n")))
                        sink = StepOutput(logger, handle)
                        env = {**environ, **resolver.secrets, **resolved[id(step)]}
                        # Python commands stream promptly; shell semantics stay unchanged.
                        env.setdefault("PYTHONUNBUFFERED", "1")
                        try:
                            result = execute(
                                step.script, config.directory, env, step.timeout, sink.feed
                            )
                        finally:
                            sink.finish()
                        failed = result.code != 0 or bool(result.reason)
                        entry["steps"].append(
                            {
                                "name": step.name,
                                "status": "failed" if failed else "success",
                                "exit_code": result.code,
                                "duration": round(result.duration, 3),
                                "reason": result.reason,
                            }
                        )
                        if failed:
                            logger.write(
                                "╭─ FAILURE ───────────────────────────────────────╮", failed=True
                            )
                            logger.write(f"Job: {job.name} • Step: {step.name}", failed=True)
                            logger.write(
                                f"{result.reason or f'exit code {result.code}'}"
                                f" • {result.duration:.2f}s",
                                failed=True,
                            )
                            # Bounded context; the full redacted log is on disk.
                            context = "\n".join(sink.tail.splitlines()[-12:])[-1200:]
                            if context:
                                logger.write(context, failed=True)
                            logger.write(
                                "╰────────────────────────────────────────────────╯", failed=True
                            )
                            logger.write(f"Full log: {report.path / filename}", style="muted")
                            break
                        if logger.verbosity:
                            logger.write(
                                f"  ✓ {step.name}  {result.duration:.2f}s", style="success"
                            )
                entry["status"] = "failed" if failed else "success"
            except (KeyboardInterrupt, Terminated):
                entry["status"] = "cancelled"
                raise
            except Exception:
                entry["status"] = "failed"
                raise
            finally:
                entry["duration"] = round(time.monotonic() - job_started, 3)
                if entry["status"] == "running":
                    entry["status"] = "cancelled"
            logger.write(
                f"{'✗' if failed else '✓'} {job.name}  {entry['duration']:.2f}s",
                failed=failed,
                style="success",
            )
            if failed:
                break
        status = "failed" if failed else "success"
        passed = sum(entry["status"] == "success" for entry in job_data)
        skipped = sum(entry["status"] == "skipped" for entry in job_data)
        logger.write("─" * 48, style="muted")
        logger.write(
            "✗ PIPELINE FAILED" if failed else "✓ PIPELINE PASSED", failed=failed, style="success"
        )
        logger.write(
            f"{len(job_data)} jobs • {passed} passed • {int(failed)} failed"
            f" • {skipped} skipped • {time.monotonic() - started:.2f}s"
        )
        return 1 if failed else 0
    except (KeyboardInterrupt, Terminated):
        status = "cancelled"
        raise
    except Exception:
        status = "failed"
        raise
    finally:
        report.finish(status, time.monotonic() - started)
        logger.write("Report: .pipeforge/latest/report.json", style="muted")
