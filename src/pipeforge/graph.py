"""Stable topological planning and explicit job selection; concurrency is handled by the engine."""

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
