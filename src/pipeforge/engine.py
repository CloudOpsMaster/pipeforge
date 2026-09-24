"""Plan and execute jobs with live, redacted output and durable run reports."""

import shutil
import time
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import ExitStack
from pathlib import Path
from threading import Event
from typing import Any, TextIO

from pipeforge.config import Config, Job
from pipeforge.context import detect_provider, git_metadata
from pipeforge.errors import ExecutionError, Terminated
from pipeforge.executor import execute
from pipeforge.graph import critical_path, render_graph, select_jobs
from pipeforge.logging import Logger, StreamingMasker, safe_text
from pipeforge.report import RunReport
from pipeforge.resolver import Resolver
from pipeforge.state import RunState


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
    resume: str | None = None,
    max_parallel: int = 1,
    artifact_input: Path | None = None,
    artifact_output: Path | None = None,
) -> int:
    with ExitStack() as stack:
        return _run(
            config,
            environ,
            logger,
            dry_run=dry_run,
            selected=selected,
            resume=resume,
            max_parallel=max_parallel,
            stack=stack,
            artifact_input=artifact_input,
            artifact_output=artifact_output,
        )


def _run(
    config: Config,
    environ: Mapping[str, str],
    logger: Logger,
    *,
    dry_run: bool,
    selected: tuple[Job, ...] | None,
    resume: str | None,
    stack: ExitStack,
    max_parallel: int,
    artifact_input: Path | None,
    artifact_output: Path | None,
) -> int:
    if not 1 <= max_parallel <= 64:
        raise ExecutionError("max_parallel must be between 1 and 64.")

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
        logger.write(render_graph(config.execution_jobs))
        logger.write(f"Up to {max_parallel} independent jobs at once")
        logger.write(f"{len({job.stage for job in selected})} selected stages")
        logger.write(f"{len(selected)} jobs • 0 commands executed", style="success")
        return 0

    if artifact_output and (artifact_output.exists() or artifact_output.is_symlink()):
        raise ExecutionError("Artifact output directory must not already exist.")
    state = RunState(config, selected, resume, git["sha"])
    stack.callback(state.close)
    if artifact_input:
        # Import only declared artifacts belonging to excluded ancestors.
        ancestors = {
            ancestor.name
            for job in selected
            for ancestor in select_jobs(config.execution_jobs, job.name)
            if ancestor.name not in selected_names
        }
        for job in config.execution_jobs:
            if job.name in ancestors:
                for name in job.artifacts:
                    source = artifact_input / name
                    if not source.is_file() or not source.resolve().is_relative_to(
                        artifact_input.resolve()
                    ):
                        raise ExecutionError("Required input artifact is missing or unsafe.")
                    destination = state.artifact(name)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
    selected = tuple(job for job in selected if job.name in state.data["jobs"])
    selected_names = {job.name for job in selected}
    logger.write(f"Resume ID: {state.id}", style="muted")
    started = time.monotonic()
    job_data: list[dict[str, Any]] = [
        {
            "name": job.name,
            "stage": job.stage,
            "status": "skipped",
            "duration": 0.0,
            "steps": [],
            "log": None,
        }
        for job in config.execution_jobs
    ]
    data = {
        "resume_id": state.id,
        "attempt": state.data["attempt"],
        "resumed": bool(resume),
        "max_parallel": max_parallel,
        "pipeline": config.name,
        "provider": provider,
        "commit": git["sha"],
        "branch": git["branch"],
        "tag": git["tag"],
        "jobs": job_data,
        "stages": [
            {
                "name": stage,
                "jobs": [job.name for job in config.execution_jobs if job.stage == stage],
            }
            for stage in dict.fromkeys(job.stage for job in config.execution_jobs)
        ],
        "graph": render_graph(config.execution_jobs, "mermaid", redact=logger.masker.mask),
    }
    report = RunReport(config.directory, logger.masker, data)
    by_name = {entry["name"]: entry for entry in job_data}
    status = "cancelled"
    try:
        failed = False
        cancelled = Event()
        pending = dict(enumerate(selected, 1))
        completed: set[str] = set()
        futures: dict[Future[None], Job] = {}
        pool = ThreadPoolExecutor(max_workers=max_parallel)
        try:
            while pending or futures:
                for index, job in list(pending.items()):
                    dependencies = set(job.needs) & selected_names
                    if not dependencies <= completed:
                        continue
                    if any(by_name[name]["status"] != "success" for name in dependencies):
                        by_name[job.name]["reason"] = "Prerequisite did not succeed."
                        completed.add(job.name)
                        del pending[index]
                        continue
                    if len(futures) >= max_parallel:
                        continue
                    future = pool.submit(
                        run_job,
                        index,
                        job,
                        config,
                        environ,
                        logger,
                        resolver,
                        resolved,
                        state,
                        report,
                        by_name,
                        selected_names,
                        cancelled,
                    )
                    futures[future] = job
                    del pending[index]
                if not futures:
                    if pending:
                        raise ExecutionError("No runnable jobs remain in the selected graph.")
                    break
                done, _ = wait(futures, timeout=0.1, return_when=FIRST_COMPLETED)
                for future in done:
                    job = futures.pop(future)
                    future.result()
                    completed.add(job.name)
                    failed = failed or by_name[job.name]["status"] == "failed"
                # Keep the existing fail-fast contract for explicitly sequential runs.
                if failed and max_parallel == 1:
                    break
        finally:
            cancelled.set()
            pool.shutdown(wait=True, cancel_futures=True)
        status = "failed" if failed else "success"
        if not failed and artifact_output:
            # A fresh export contains declared outputs only, never secrets or run state.
            if artifact_output.exists():
                raise ExecutionError("Artifact output directory must not already exist.")
            artifact_output.mkdir(parents=True, mode=0o700)
            for job in selected:
                for name in job.artifacts:
                    destination = artifact_output / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(state.artifact(name), destination)
        passed = sum(entry["status"] == "success" for entry in job_data)
        skipped = sum(entry["status"] == "skipped" for entry in job_data)
        logger.write("─" * 48, style="muted")
        logger.write(
            "✗ PIPELINE FAILED" if failed else "✓ PIPELINE PASSED", failed=failed, style="success"
        )
        logger.write(
            f"{len(job_data)} jobs • {passed} passed"
            f" • {sum(entry['status'] == 'failed' for entry in job_data)} failed"
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
        report.data["reused_jobs"] = sum(bool(entry.get("reused")) for entry in job_data)
        if status == "success" and "first_failure_at" in state.data:
            report.data["recovery_seconds"] = round(time.time() - state.data["first_failure_at"], 3)
        for entry in job_data:
            for key in ("started_offset", "finished_offset"):
                if key in entry:
                    entry[key] = round(entry[key] - started, 3)
        durations = {
            entry["name"]: entry["duration"]
            for entry in job_data
            if entry["status"] != "skipped" and not entry.get("reused")
        }
        path, path_duration = critical_path(config.execution_jobs, durations)
        report.data["critical_path"] = path
        report.data["critical_path_duration"] = round(path_duration, 3)
        elapsed = time.monotonic() - started
        report.data["parallel_time_saved"] = round(max(0.0, sum(durations.values()) - elapsed), 3)
        for stage in report.data["stages"]:
            entries = [entry for entry in job_data if entry["stage"] == stage["name"]]
            starts = [entry["started_offset"] for entry in entries if "started_offset" in entry]
            ends = [entry["finished_offset"] for entry in entries if "finished_offset" in entry]
            stage["duration"] = round(max(ends) - min(starts), 3) if starts and ends else 0.0
        for stage in report.data["stages"]:
            logger.write(f"Stage {stage['name']}: {stage['duration']:.2f}s")
        if path:
            logger.write("Critical path: " + " → ".join(path) + f" ({path_duration:.2f}s)")
        report.finish(status, elapsed)
        if environ.get("GITHUB_STEP_SUMMARY"):
            with Path(environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
                summary.write((report.path / "summary.md").read_text())
        logger.write("Report: .pipeforge/latest/report.json", style="muted")


def run_job(
    index: int,
    job: Job,
    config: Config,
    environ: Mapping[str, str],
    logger: Logger,
    resolver: Resolver,
    resolved: dict[int, dict[str, str]],
    state: RunState,
    report: RunReport,
    by_name: dict[str, dict[str, Any]],
    selected_names: set[str],
    cancelled: Event,
) -> None:
    failed = False
    entry = by_name[job.name]
    prior_status = state.status(job)
    if prior_status == "success":
        entry["status"] = "success"
        entry["reused"] = True
        logger.write(f"✓ {job.name} (saved success)", style="success")
        return
    if cancelled.is_set():
        return
    entry["status"] = "running"
    job_started = time.monotonic()
    entry["started_offset"] = job_started
    logger.write(f"▶ {job.name} [{job.stage}]")
    missing = set(job.needs) - selected_names
    if missing:
        logger.write(
            "Assuming prerequisites already satisfied: " + ", ".join(sorted(missing)),
            style="warn",
        )
    try:
        handle, filename = report.open_log(job.name, index)
        entry["log"] = filename
        with handle:

            def verify(
                job: Job = job, handle: TextIO = handle, entry: dict[str, Any] = entry
            ) -> int:
                sink = StepOutput(logger, handle)
                env = {
                    **environ,
                    **resolver.secrets,
                    **resolved[id(job.steps[0])],
                    **state.environment(job),
                }
                try:
                    result = execute(
                        job.verify,
                        config.directory,
                        env,
                        job.steps[0].timeout,
                        sink.feed,
                        cancelled=cancelled,
                    )
                finally:
                    sink.finish()
                entry.setdefault("verifications", []).append(
                    {
                        "exit_code": result.code,
                        "reason": result.reason,
                        "duration": round(result.duration, 3),
                    }
                )
                if result.reason == "cancelled":
                    raise Terminated
                if result.reason or result.code not in (0, 3):
                    raise ExecutionError(
                        "Publication result is uncertain; verification must return "
                        "0 (confirmed) or 3 (definitely absent). No retry performed."
                    )
                return result.code

            if prior_status != "pending" and job.verify and verify() == 0:
                state.mark(job, "success")
                entry["status"] = "success"
                entry["reconciled"] = True
                logger.write(f"✓ {job.name} (verified existing result)", style="success")
                return
            state.mark(job, "running")
            for step_index, step in enumerate(job.steps, 1):
                label = f"{step.block} / {step.name}" if step.block else step.name
                if logger.verbosity:
                    logger.write(f"  ▶ {label} • timeout {step.timeout:g}s")
                handle.write(safe_text(logger.masker.mask(f"\n=== {step_index}: {label} ===\n")))
                sink = StepOutput(logger, handle)
                env = {
                    **environ,
                    **resolver.secrets,
                    **resolved[id(step)],
                    **state.environment(job),
                }
                # Python commands stream promptly; shell semantics stay unchanged.
                env.setdefault("PYTHONUNBUFFERED", "1")
                try:
                    result = execute(
                        step.script,
                        config.directory,
                        env,
                        step.timeout,
                        sink.feed,
                        cancelled=cancelled,
                    )
                finally:
                    sink.finish()
                if result.reason == "cancelled":
                    raise Terminated
                failed = result.code != 0 or bool(result.reason)
                entry["steps"].append(
                    {
                        "name": step.name,
                        "index": step_index,
                        "block": step.block,
                        "status": "failed" if failed else "success",
                        "exit_code": result.code,
                        "duration": round(result.duration, 3),
                        "reason": result.reason,
                    }
                )
                if failed:
                    logger.write("╭─ FAILURE ───────────────────────────────────────╮", failed=True)
                    logger.write(f"Job: {job.name} • Step {step_index}: {label}", failed=True)
                    logger.write(
                        f"{result.reason or f'exit code {result.code}'} • {result.duration:.2f}s",
                        failed=True,
                    )
                    # Bounded context; the full redacted log is on disk.
                    context = "\n".join(sink.tail.splitlines()[-12:])[-1200:]
                    if context:
                        logger.write(context, failed=True)
                    logger.write("╰────────────────────────────────────────────────╯", failed=True)
                    logger.write(f"Full log: {report.path / filename}", style="muted")
                    break
                if logger.verbosity:
                    logger.write(f"  ✓ {label}  {result.duration:.2f}s", style="success")
            if not failed and job.verify and verify() != 0:
                raise ExecutionError("Job finished but its result was not confirmed.")
            state.mark(job, "failed" if failed else "success")
        entry["status"] = "failed" if failed else "success"
    except (KeyboardInterrupt, Terminated):
        entry["status"] = "cancelled"
        raise
    except (ExecutionError, OSError) as error:
        failed = True
        entry["status"] = "failed"
        entry["reason"] = (
            str(error)
            if isinstance(error, ExecutionError)
            else "Cannot read or write runtime files."
        )
        logger.write(f"{job.name}: {entry['reason']}", failed=True)
        state.mark(job, "failed")
    except Exception:
        entry["status"] = "failed"
        state.mark(job, "failed")
        raise
    finally:
        entry["finished_offset"] = time.monotonic()
        entry["duration"] = round(entry["finished_offset"] - job_started, 3)
        if entry["status"] == "running":
            entry["status"] = "cancelled"
    logger.write(
        f"{'✗' if failed else '✓'} {job.name}  {entry['duration']:.2f}s",
        failed=failed,
        style="success",
    )
