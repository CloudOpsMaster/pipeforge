"""Stable topological planning and explicit job selection; concurrency is handled by the engine."""

from collections.abc import Callable
from typing import TYPE_CHECKING

from pipeforge.errors import ConfigError

if TYPE_CHECKING:
    from pipeforge.config import Job


def ordered_jobs(jobs: tuple["Job", ...]) -> tuple["Job", ...]:
    names = {job.name for job in jobs}
    if len(names) != len(jobs):
        raise ConfigError("Job IDs must be unique.")
    if any(set(job.needs) - names for job in jobs):
        raise ConfigError("A job depends on an unknown job.")
    result = []
    done: set[str] = set()
    remaining = list(jobs)
    while remaining:
        ready = next((job for job in remaining if set(job.needs) <= done), None)
        if ready is None:
            raise ConfigError("Job dependency graph contains a cycle.")
        result.append(ready)
        done.add(ready.name)
        remaining.remove(ready)
    return tuple(result)


def select_jobs(
    jobs: tuple["Job", ...],
    target: str | None = None,
    *,
    with_needs: bool = True,
    from_job: str | None = None,
    until_job: str | None = None,
) -> tuple["Job", ...]:
    ordered = ordered_jobs(jobs)
    by_name = {job.name: job for job in ordered}
    if any(name is not None and name not in by_name for name in (target, from_job, until_job)):
        raise ConfigError("Unknown selected job; use --list to see available jobs.")
    if target and (from_job or until_job):
        raise ConfigError("Choose a job or --from/--until, not both.")
    if not with_needs and not target:
        raise ConfigError("--no-needs requires a job name.")

    def ancestors(name: str) -> set[str]:
        selected = {name}
        pending = [name]
        while pending:
            for dependency in by_name[pending.pop()].needs:
                if dependency not in selected:
                    selected.add(dependency)
                    pending.append(dependency)
        return selected

    selected = set(by_name)
    if target:
        selected = ancestors(target) if with_needs else {target}
    if until_job:
        selected &= ancestors(until_job)
    if from_job:
        descendants = {from_job}
        for job in ordered:
            if set(job.needs) & descendants:
                descendants.add(job.name)
        selected &= descendants
        if from_job not in selected or (until_job and until_job not in selected):
            raise ConfigError("--until must be reachable from --from.")
    return tuple(job for job in ordered if job.name in selected)


def select_stage(
    jobs: tuple["Job", ...], stage: str, *, with_needs: bool = False
) -> tuple["Job", ...]:
    ordered = ordered_jobs(jobs)
    members = [job for job in ordered if job.stage == stage]
    if not members:
        raise ConfigError("Unknown stage.")
    names = {job.name for job in members}
    if with_needs:
        for job in members:
            names.update(item.name for item in select_jobs(jobs, job.name))
    return tuple(job for job in ordered if job.name in names)


def render_graph(
    jobs: tuple["Job", ...],
    format: str = "text",
    *,
    redact: Callable[[str], str] = str,
) -> str:
    import json

    ordered = ordered_jobs(jobs)
    ids = {job.name: f"n{index}" for index, job in enumerate(ordered)}
    stages = list(dict.fromkeys(job.stage for job in ordered))
    if format == "text":
        lines = ["Execution graph (edges are dependencies):"]
        for stage in stages:
            lines.append(f"[{stage}]")
            for job in ordered:
                if job.stage == stage:
                    lines.append(f"  {job.name}  <-  {', '.join(job.needs) or '(root)'}")
        return "\n".join(lines)
    if format == "dot":
        lines = ["digraph pipeforge {"]
        for index, stage in enumerate(stages):
            lines.extend([f"  subgraph cluster_{index} {{", f"    label={json.dumps(stage)};"])
            for job in ordered:
                if job.stage == stage:
                    lines.append(f"    {ids[job.name]} [label={json.dumps(job.name)}];")
            lines.append("  }")
        for job in ordered:
            lines.extend(f"  {ids[dep]} -> {ids[job.name]};" for dep in job.needs)
        return "\n".join([*lines, "}"])
    if format != "mermaid":
        raise ConfigError("Unknown graph format.")

    # Encode labels as Mermaid numeric entities, including newlines and delimiters.
    def label(value: str) -> str:
        return "".join(
            char if char.isascii() and (char.isalnum() or char in " _-.") else f"#{ord(char)};"
            for char in redact(value)
        )

    lines = ["flowchart TD"]
    for index, stage in enumerate(stages):
        lines.append(f'  subgraph s{index}["{label(stage)}"]')
        for job in ordered:
            if job.stage == stage:
                lines.append(f'    {ids[job.name]}["{label(job.name)}"]')
        lines.append("  end")
    for job in ordered:
        lines.extend(f"  {ids[dep]} --> {ids[job.name]}" for dep in job.needs)
    return "\n".join(lines)


def critical_path(jobs: tuple["Job", ...], durations: dict[str, float]) -> tuple[list[str], float]:
    paths: dict[str, tuple[list[str], float]] = {}
    for job in ordered_jobs(jobs):
        if job.name not in durations:
            continue
        path, duration = max(
            (paths[dep] for dep in job.needs if dep in paths),
            key=lambda item: item[1],
            default=([], 0.0),
        )
        paths[job.name] = ([*path, job.name], duration + durations[job.name])
    return max(paths.values(), key=lambda item: item[1], default=([], 0.0))
