"""Compile normalized jobs into native GitHub Actions jobs."""

import shlex
from pathlib import Path
from typing import Any

import yaml

from pipeforge.config import Config
from pipeforge.errors import ConfigError
from pipeforge.graph import ordered_jobs, select_jobs


def render_github(config: Config, file: Path, launcher: str = ".pf/run") -> str:
    if file.is_absolute():
        try:
            file = file.relative_to(Path.cwd())
        except ValueError:
            raise ConfigError(
                "Configuration must be inside the repository working directory."
            ) from None
    if ".." in file.parts:
        raise ConfigError("GitHub rendering requires a repository-relative configuration path.")
    # GitHub expressions are evaluated even inside shell quotes and YAML strings.
    if any("${{" in value or "\n" in value for value in (str(file), launcher, config.name)):
        raise ConfigError("GitHub expressions and newlines are not allowed in renderer arguments.")
    if not launcher or launcher.startswith("-"):
        raise ConfigError("Provide a launcher executable path.")
    ordered = ordered_jobs(config.execution_jobs)
    rendered: dict[str, Any] = {}
    for job in ordered:
        if "${{" in job.stage:
            raise ConfigError("GitHub expressions are not allowed in stage labels.")
        ancestors = [item for item in select_jobs(ordered, job.name) if item.name != job.name]
        owners: dict[str, str] = {}
        for ancestor in ancestors:
            for artifact in ancestor.artifacts:
                if any(
                    Path(artifact) == Path(existing)
                    or Path(artifact) in Path(existing).parents
                    or Path(existing) in Path(artifact).parents
                    for existing in owners
                ):
                    raise ConfigError("GitHub artifact paths must be unique across ancestors.")
                owners[artifact] = ancestor.name
        steps: list[dict[str, Any]] = [
            {
                "uses": "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
                "with": {"submodules": "recursive", "persist-credentials": False},
            },
            {
                "uses": "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1",
                "with": {"python-version": "3.12"},
            },
        ]
        base = file.parent / ".pipeforge" / "github"
        incoming, outgoing = base / "incoming", base / "outgoing"
        for ancestor in ancestors:
            if ancestor.artifacts:
                steps.append(
                    {
                        "name": f"Download {ancestor.name} artifacts",
                        "uses": "actions/download-artifact@v5",
                        "with": {"name": f"pipeforge-{ancestor.name}", "path": str(incoming)},
                    }
                )
        command = [launcher, "exec-job", job.name, "-f", str(file)]
        if owners:
            command.extend(["--artifact-input", str(incoming)])
        if job.artifacts:
            command.extend(["--artifact-output", str(outgoing)])
        steps.append(
            {
                "name": f"Run PipeForge job: {job.name}",
                "run": shlex.join(command),
                "env": {name: "${{ secrets." + name + " }}" for name in config.secrets},
            }
        )
        if job.artifacts:
            steps.append(
                {
                    "name": "Upload PipeForge artifacts",
                    "uses": "actions/upload-artifact@v4",
                    "with": {
                        "name": f"pipeforge-{job.name}",
                        "path": str(outgoing) + "/",
                        "if-no-files-found": "error",
                        "include-hidden-files": True,
                        "overwrite": True,
                    },
                }
            )
        native: dict[str, Any] = {
            "name": f"{job.stage} / {job.name}",
            "runs-on": "ubuntu-latest",
            "steps": steps,
        }
        if job.needs:
            native["needs"] = list(job.needs)
        rendered[job.name] = native
    workflow = {
        "name": config.name,
        "on": {"workflow_dispatch": {}},
        "permissions": {"contents": "read"},
        "jobs": rendered,
    }
    return yaml.safe_dump(workflow, sort_keys=False, allow_unicode=True)
