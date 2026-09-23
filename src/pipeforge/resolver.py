"""Resolve configuration references as data, never as shell code."""

import re
from collections.abc import Mapping

from pipeforge.config import Config
from pipeforge.errors import ConfigError

REFERENCE = re.compile(r"\$\{([^{}]+)\}")
MAX_RESOLVED_LENGTH = 65536


class Resolver:
    def __init__(
        self,
        config: Config,
        environ: Mapping[str, str],
        *,
        validate: bool = False,
        git: Mapping[str, str] | None = None,
    ):
        self.config = config
        self.secrets: dict[str, str] = {}
        self.cache: dict[str, str] = {}
        self.git = git or {}
        self.validating = validate
        for name, declaration in config.secrets.items():
            value = environ.get(name, "")
            if not validate and declaration.required and not value:
                raise ConfigError(
                    "A required secret is missing or empty; check declared environment variables."
                )
            self.secrets[name] = value

    def value(self, path: str, stack: tuple[str, ...] = ()) -> str:
        if path in stack or len(stack) >= 50:
            raise ConfigError("Cyclic or excessively deep values reference.")
        if path in self.cache:
            return self.cache[path]
        value: object = self.config.values
        for part in path.split("."):
            if not isinstance(value, dict) or part not in value:
                raise ConfigError("Unknown values reference.")
            value = value[part]
        if isinstance(value, dict):
            raise ConfigError("A values reference must resolve to a scalar.")
        raw = str(value).lower() if isinstance(value, bool) else str(value)
        result = self.render(raw, stack=(*stack, path), allow_secrets=False)
        self.cache[path] = result
        return result

    def render(
        self, template: str, *, stack: tuple[str, ...] = (), allow_secrets: bool = True
    ) -> str:
        def replace(match: re.Match[str]) -> str:
            reference = match.group(1)
            if reference.startswith("values."):
                return self.value(reference.removeprefix("values."), stack)
            if reference.startswith("git."):
                key = reference.removeprefix("git.")
                if key not in {"sha", "short_sha", "branch", "tag"}:
                    raise ConfigError("Unknown Git metadata reference.")
                value = self.git.get(key, "")
                if not self.validating and not value:
                    raise ConfigError("Requested Git metadata is unavailable in this checkout.")
                return value
            if reference.startswith("secrets.") and allow_secrets:
                name = reference.removeprefix("secrets.")
                if name in self.secrets:
                    return self.secrets[name]
            raise ConfigError(
                "Unknown or disallowed reference; use values or declared secrets in step env."
            )

        # Check malformed syntax in the template, not in substituted secret values.
        if "${" in REFERENCE.sub("", template):
            raise ConfigError("Malformed configuration reference.")
        parts: list[str] = []
        length = 0
        end = 0
        for match in REFERENCE.finditer(template):
            literal, replacement = template[end : match.start()], replace(match)
            length += len(literal) + len(replacement)
            if length > MAX_RESOLVED_LENGTH:
                raise ConfigError("Resolved value exceeds the 64 KiB limit.")
            parts.extend((literal, replacement))
            end = match.end()
        tail = template[end:]
        if length + len(tail) > MAX_RESOLVED_LENGTH:
            raise ConfigError("Resolved value exceeds the 64 KiB limit.")
        parts.append(tail)
        return "".join(parts)

    def resolve(self) -> tuple[dict[str, str], ...]:
        def walk(values: dict[str, object], prefix: str = "") -> None:
            for key, value in values.items():
                path = f"{prefix}.{key}" if prefix else key
                if isinstance(value, dict):
                    walk(value, path)
                else:
                    self.value(path)

        walk(self.config.values)
        return tuple(
            {key: self.render(value) for key, value in step.env.items()}
            for step in self.config.pipeline
        )
