"""Strict configuration parsing; no evaluation or command execution."""

import math
import re
from collections.abc import Hashable
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Config:
    name: str
    values: dict[str, Any]
    secrets: dict[str, Secret]
    pipeline: tuple[Step, ...]
    directory: Path


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
    fields(data, {"name", "values", "secrets", "pipeline"}, "configuration")
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

    raw_steps = data.get("pipeline")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ConfigError("pipeline must be a non-empty list.")
    steps = []
    names: set[str] = set()
    for index, raw_step in enumerate(raw_steps, 1):
        location = f"pipeline step {index}"
        step = mapping(raw_step, location)
        fields(step, {"name", "script", "env", "timeout"}, location)
        step_name = text(step.get("name"), f"{location} name")
        if step_name in names:
            raise ConfigError("Step names must be unique.")
        names.add(step_name)
        script = text(step.get("script"), f"{location} script")
        if "${values." in script or "${secrets." in script:
            raise ConfigError(
                "Use step env for PipeForge references, not shell script interpolation."
            )
        env = mapping(step.get("env", {}), f"{location} env")
        for key, value in env.items():
            if not ENV_KEY.fullmatch(key) or not isinstance(value, str) or "\0" in value:
                raise ConfigError("Step env requires environment identifiers and string values.")
        timeout = step.get("timeout", 300)
        if type(timeout) not in (int, float) or not 0 < timeout <= 86400:
            raise ConfigError(
                "Step timeout must be a number greater than 0 and at most 86400 seconds."
            )
        steps.append(Step(step_name, script, env, float(timeout)))
    return Config(name, values, secrets, tuple(steps), path.resolve().parent)
