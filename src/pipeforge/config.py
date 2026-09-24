"""Strict configuration parsing; no evaluation or command execution."""

import math
import re
from collections.abc import Hashable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml
from yaml.events import AliasEvent
from yaml.nodes import MappingNode

from pipeforge.errors import ConfigError

MAX_CONFIG_BYTES = 1024 * 1024
ENV_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
VALUE_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*\Z")


class StrictLoader(yaml.SafeLoader):
    """Reject duplicate keys instead of silently accepting the last value."""

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Hashable, Any]:
        result: dict[Hashable, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ConfigError("YAML mapping keys must be unique strings.")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


@dataclass(frozen=True)
class Secret:
    required: bool = True


@dataclass(frozen=True)
class Step:
    name: str
    script: str
    env: dict[str, str]
    timeout: float = 300
    block: str = ""


@dataclass(frozen=True)
class StepBlock:
    name: str
    steps: tuple[Step, ...]
    description: str = ""


@dataclass(frozen=True)
class Job:
    name: str
    steps: tuple[Step, ...]
    needs: tuple[str, ...] = ()
    description: str = ""
    artifacts: tuple[str, ...] = ()
    verify: str = ""
    stage: str = "default"


@dataclass(frozen=True)
class Config:
    name: str
    values: dict[str, Any]
    secrets: dict[str, Secret]
    pipeline: tuple[Step, ...]
    directory: Path
    jobs: tuple[Job, ...] = ()
    legacy: bool = False
    blocks: tuple[StepBlock, ...] = ()

    @property
    def execution_jobs(self) -> tuple[Job, ...]:
        return self.jobs or (Job("default", self.pipeline),)


def mapping(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ConfigError(f"{location} must be a mapping with string keys.")
    return value


def fields(data: dict[str, Any], allowed: set[str], location: str) -> None:
    if data.keys() - allowed:
        raise ConfigError(f"{location} contains unsupported fields.")


def text(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ConfigError(f"{location} must be a non-empty string without NUL characters.")
    return value


def validate_values(values: dict[str, Any], depth: int = 0) -> None:
    if depth > 20:
        raise ConfigError("values nesting exceeds 20 levels.")
    for key, value in values.items():
        if not VALUE_KEY.fullmatch(key):
            raise ConfigError("values keys must be identifiers without dots.")
        if isinstance(value, dict):
            validate_values(mapping(value, "values"), depth + 1)
        elif type(value) not in (str, int, float, bool):
            raise ConfigError("values must contain only nested mappings and scalar values.")
        elif isinstance(value, float) and not math.isfinite(value):
            raise ConfigError("values numbers must be finite.")
        elif isinstance(value, str) and "\0" in value:
            raise ConfigError("values cannot contain NUL characters.")


def load_config(path: Path) -> Config:
    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_CONFIG_BYTES + 1)
        if len(content) > MAX_CONFIG_BYTES:
            raise ConfigError("Configuration exceeds the 1 MiB limit.")
        source = content.decode("utf-8")
        if any(isinstance(event, AliasEvent) for event in yaml.parse(source)):
            raise ConfigError("YAML aliases are not supported.")
        # StrictLoader extends SafeLoader only to reject duplicate/non-string keys.
        data = yaml.load(source, Loader=StrictLoader)  # nosec B506
    except (OSError, UnicodeError):
        raise ConfigError(
            "Cannot read configuration as UTF-8; check the file and permissions."
        ) from None
    except (yaml.YAMLError, RecursionError, ValueError):
        # Parser exceptions can contain entire source lines, including secrets.
        raise ConfigError(
            "Invalid YAML; check syntax, nesting, and supported YAML types."
        ) from None

    data = mapping(data, "configuration")
    fields(data, {"name", "values", "secrets", "pipeline", "jobs", "blocks"}, "configuration")
    name = text(data.get("name"), "name")
    values = mapping(data.get("values", {}), "values")
    validate_values(values)
    secrets: dict[str, Secret] = {}
    for key, raw in mapping(data.get("secrets", {}), "secrets").items():
        if not ENV_KEY.fullmatch(key):
            raise ConfigError("Secret names must be environment variable identifiers.")
        declaration = mapping(raw, "secret declaration")
        fields(declaration, {"from", "required"}, "secret declaration")
        if declaration.get("from") != "env":
            raise ConfigError("Secrets must declare 'from: env'.")
        required = declaration.get("required", True)
        if type(required) is not bool:
            raise ConfigError("Secret 'required' must be a boolean.")
        secrets[key] = Secret(required)

    if ("pipeline" in data) == ("jobs" in data):
        raise ConfigError("Declare either jobs or legacy pipeline, never both.")
    blocks: dict[str, StepBlock] = {}
    for key, raw in mapping(data.get("blocks", {}), "blocks").items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", key):
            raise ConfigError("Block IDs must be identifiers of at most 64 characters.")
        block = mapping(raw, "block")
        fields(block, {"description", "steps"}, "block")
        description = block.get("description", "")
        if not isinstance(description, str) or "\0" in description:
            raise ConfigError("Block description must be a string without NUL characters.")
        blocks[key] = StepBlock(key, parse_steps(block.get("steps"), {}), description)
    jobs: list[Job] = []
    if "pipeline" in data:
        jobs.append(Job("default", parse_steps(data["pipeline"], {}, legacy=True)))
    else:
        raw_jobs = mapping(data["jobs"], "jobs")
        if not raw_jobs:
            raise ConfigError("jobs must not be empty.")
        for key, raw in raw_jobs.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", key):
                raise ConfigError("Job IDs must be identifiers of at most 64 characters.")
            job = mapping(raw, "job")
            fields(
                job,
                {"steps", "needs", "env", "description", "artifacts", "verify", "use", "stage"},
                "job",
            )
            needs = job.get("needs", [])
            if isinstance(needs, str):
                needs = [needs]
            if not isinstance(needs, list) or any(not isinstance(item, str) for item in needs):
                raise ConfigError("needs must be a job ID or a list of job IDs.")
            if len(set(needs)) != len(needs):
                raise ConfigError("needs contains duplicates.")
            description = job.get("description", "")
            if not isinstance(description, str) or "\0" in description:
                raise ConfigError("Job description must be a string without NUL characters.")
            env = parse_env(job.get("env", {}))
            artifacts = job.get("artifacts", [])
            if not isinstance(artifacts, list) or any(
                not isinstance(item, str)
                or not item
                or "\0" in item
                or Path(item).is_absolute()
                or ".." in Path(item).parts
                or item == "."
                for item in artifacts
            ):
                raise ConfigError("artifacts must list relative file paths without '..'.")
            verify = job.get("verify", "")
            if "verify" in job:
                verify = text(verify, "verify")
                if any(prefix in verify for prefix in ("${values.", "${secrets.", "${git.")):
                    raise ConfigError("Use env for references in verify commands.")
            use = job.get("use", [])
            if isinstance(use, str):
                use = [use]
            if (
                not isinstance(use, list)
                or any(not isinstance(item, str) or item not in blocks for item in use)
                or ("use" in job and not use)
            ):
                raise ConfigError("use must name an existing block or a non-empty list of blocks.")
            expanded = tuple(
                replace(step, env={**env, **step.env}, block=block_name)
                for block_name in use
                for step in blocks[block_name].steps
            )
            local = parse_steps(job["steps"], env) if "steps" in job else ()
            if not expanded and not local:
                raise ConfigError("A job requires steps or use.")
            stage = text(job.get("stage", "default"), "stage")
            jobs.append(
                Job(
                    key,
                    expanded + local,
                    tuple(needs),
                    description,
                    tuple(artifacts),
                    verify,
                    stage,
                )
            )
    from pipeforge.graph import ordered_jobs

    ordered_jobs(tuple(jobs))  # Validate the entire graph before any selection or execution.
    return Config(
        name,
        values,
        secrets,
        tuple(step for job in jobs for step in job.steps),
        path.resolve().parent,
        tuple(jobs),
        "pipeline" in data,
        tuple(blocks.values()),
    )


def parse_env(raw: Any) -> dict[str, str]:
    env = mapping(raw, "env")
    for key, value in env.items():
        if not ENV_KEY.fullmatch(key) or not isinstance(value, str) or "\0" in value:
            raise ConfigError("env requires environment identifiers and string values.")
    return env


def parse_steps(
    raw_steps: Any, job_env: dict[str, str], *, legacy: bool = False
) -> tuple[Step, ...]:
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ConfigError("pipeline must be a non-empty list.")
    steps = []
    names: set[str] = set()
    for index, raw_step in enumerate(raw_steps, 1):
        location = f"pipeline step {index}"
        step = mapping(raw_step, location)
        fields(step, {"name", "run", "script", "env", "timeout"}, location)
        step_name = text(step.get("name", None if legacy else f"Step {index}"), f"{location} name")
        if step_name in names:
            raise ConfigError("Step names must be unique.")
        names.add(step_name)
        if ("script" in step) == ("run" in step):
            raise ConfigError("A step must contain exactly one of run or legacy script.")
        script = text(step.get("run", step.get("script")), f"{location} run")
        if any(prefix in script for prefix in ("${values.", "${secrets.", "${git.")):
            raise ConfigError(
                "Use step env for PipeForge references, not shell script interpolation."
            )
        env = {**job_env, **parse_env(step.get("env", {}))}
        timeout = step.get("timeout", 300)
        if type(timeout) not in (int, float) or not 0 < timeout <= 86400:
            raise ConfigError(
                "Step timeout must be a number greater than 0 and at most 86400 seconds."
            )
        steps.append(Step(step_name, script, env, float(timeout)))
    return tuple(steps)
