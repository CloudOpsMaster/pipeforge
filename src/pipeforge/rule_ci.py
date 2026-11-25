"""Compile a rule invocation into a single trusted runner job."""

import re
import shlex
from pathlib import Path
from typing import Any

import yaml

from pipeforge.config import MAX_CONFIG_BYTES, StrictLoader, read_yaml
from pipeforge.errors import ConfigError
from pipeforge.rules import Node, RuleSet, get_rule

CHECKOUT = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"

BUILTIN = re.compile(r":|true|false|exit (?:0|[1-9][0-9]?)")


class ProviderLoader(StrictLoader):
    # GitHub's bare "on" is a string in workflow YAML.
    yaml_implicit_resolvers = {
        key: [(tag, pattern) for tag, pattern in values if tag != "tag:yaml.org,2002:bool"]
        for key, values in StrictLoader.yaml_implicit_resolvers.items()
    }


ProviderLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF")
)


def standalone(node: Node) -> list[str]:
    """Lower only deterministic shell builtins with no ambient inputs or effects."""
    rule = node.rule
    location = f"{rule.source.file}:{rule.source.line}"
    if node.inputs or rule.tools:
        raise ConfigError(f"{location}: standalone cannot preserve inputs or tool checks.")
    if node.children:
        if any(len(group) != 1 for group in node.children):
            raise ConfigError(f"{location}: standalone cannot preserve parallel execution.")
        return [command for group in node.children for command in standalone(group[0])]
    command = rule.command
    if isinstance(command, str) and BUILTIN.fullmatch(command):
        # Isolate exit so a successful child cannot terminate the enclosing job.
        return [f"({command})"]
    raise ConfigError(
        f"{location}: standalone supports only literal :, true, false and exit 0–99. "
        "Use wrapper mode for arbitrary commands."
    )


def export(spec: RuleSet, file: Path, name: str, provider: str, mode: str) -> str:
    rule = get_rule(spec.rules, name)
    source = f"{rule.source.file}:{rule.source.line}"
    if spec.secrets:
        raise ConfigError(
            f"{source}: provider secret bindings are unsupported. "
            "Use a rule configuration without declared secrets."
        )
    graph = spec.graph(name, {})
    if not file.resolve().is_relative_to(spec.root):
        raise ConfigError(f"{source}: configuration must be relative to checkout.")
    config = file.resolve().relative_to(spec.root).as_posix()
    # Provider expressions and Groovy have distinct interpolation rules.
    if any(not char.isascii() or not (char.isalnum() or char in "/_.-") for char in config):
        raise ConfigError(f"{source}: rename the configuration to a portable path.")
    commands = (
        standalone(graph)
        if mode == "standalone"
        else [shlex.join(["pipeforge", name, "--file", config])]
    )
    return render_job(" && ".join(commands), provider)


def render_job(command: str, provider: str) -> str:
    if provider == "jenkins":
        return (
            "pipeline {\n"
            "  agent { label 'pipeforge' }\n"
            "  stages {\n"
            "    stage('PipeForge') {\n"
            f"      steps {{ sh '{command}' }}\n"
            "    }\n"
            "  }\n"
            "}\n"
        )
    if provider == "github":
        data = {
            "name": "PipeForge",
            "on": {"workflow_dispatch": {}},
            "permissions": {"contents": "read"},
            "jobs": {
                "pipeforge": {
                    "runs-on": ["self-hosted", "pipeforge"],
                    "steps": [
                        {"uses": CHECKOUT, "with": {"persist-credentials": False}},
                        {"run": command},
                    ],
                },
            },
        }
    else:
        data = {
            "workflow": {
                "rules": [
                    {"if": '$CI_PIPELINE_SOURCE == "web" && $CI_COMMIT_REF_PROTECTED == "true"'}
                ]
            },
            "pipeforge": {"tags": ["pipeforge"], "script": [command]},
        }
    return yaml.safe_dump(data, sort_keys=False)


def inspect_wrapper(
    spec: RuleSet, file: Path, provider: str, workflow: Path, rule: str | None = None
) -> dict[str, str]:
    """Recognize the exact wrapper contract, rejecting extra native semantics."""
    location = f"{workflow.name}:1"
    unsupported = ConfigError(
        f"{location}: unsupported {provider} wrapper. "
        "Use the generated single-job wrapper or migrate the provider configuration manually."
    )
    if not workflow.is_file():
        raise unsupported
    if rule is not None:
        expected = export(spec, file, rule, provider, "standalone")
        if provider == "jenkins":
            with workflow.open("rb") as handle:
                actual = handle.read(MAX_CONFIG_BYTES + 1)
            same = actual == expected.encode()
        else:
            same = yaml.safe_dump(read_yaml(workflow, ProviderLoader)) == yaml.safe_dump(
                yaml.safe_load(expected)
            )
        if same:
            return {"provider": provider, "mode": "standalone", "rule": rule}
        raise unsupported
    if provider == "jenkins":
        with workflow.open("rb") as handle:
            content = handle.read(MAX_CONFIG_BYTES + 1)
        if len(content) > MAX_CONFIG_BYTES:
            raise unsupported
        match = re.search(rb"steps \{ sh 'pipeforge ([a-zA-Z_][a-zA-Z0-9_.-]*) --file ", content)
        if match:
            name = match[1].decode("ascii")
            try:
                if export(spec, file, name, provider, "wrapper").encode() == content:
                    return {"provider": provider, "mode": "wrapper", "rule": name}
            except ConfigError:
                pass
        raise unsupported
    try:
        data = read_yaml(workflow, ProviderLoader)
        if provider == "github":
            command = data["jobs"]["pipeforge"]["steps"][1]["run"]
        else:
            command = data["pipeforge"]["script"][0]
        prefix, name, flag, _ = shlex.split(command)
        if (prefix, flag) != ("pipeforge", "--file"):
            raise unsupported
        expected = yaml.safe_load(export(spec, file, name, provider, "wrapper"))
        if yaml.safe_dump(data) != yaml.safe_dump(expected):
            raise unsupported
    except (ConfigError, KeyError, IndexError, TypeError, ValueError, AttributeError):
        raise unsupported from None
    return {"provider": provider, "mode": "wrapper", "rule": name}


def import_native(provider: str, path: Path) -> dict[str, Any]:
    """Recover a portable pipeline from the supported native runner profile."""
    failure = ConfigError(
        f"{path.name}:1: native workflow is outside the supported single-job profile. "
        "Preserve provider-specific semantics in the original workflow."
    )
    if not path.is_file():
        raise failure
    try:
        if provider == "jenkins":
            with path.open("rb") as handle:
                raw = handle.read(MAX_CONFIG_BYTES + 1)
            if len(raw) > MAX_CONFIG_BYTES:
                raise failure
            source = raw.decode("utf-8")
            match = re.search(r"steps \{ sh '([^']*)' \}", source)
            if match is None:
                raise failure
            command = match[1]
            if source != render_job(command, provider):
                raise failure
        else:
            data = read_yaml(path, ProviderLoader)
            command = (
                data["jobs"]["pipeforge"]["steps"][1]["run"]
                if provider == "github"
                else data["pipeforge"]["script"][0]
            )
            if not isinstance(command, str):
                raise failure
            if yaml.safe_dump(data) != yaml.safe_dump(
                yaml.safe_load(render_job(command, provider))
            ):
                raise failure
        parts = command.split(" && ")
        if len(parts) > 511 or not all(
            part.startswith("(") and part.endswith(")") and BUILTIN.fullmatch(part[1:-1])
            for part in parts
        ):
            raise failure
    except (ConfigError, KeyError, IndexError, TypeError, ValueError, AttributeError):
        raise failure from None
    steps = {f"step_{index}": {"run": part[1:-1]} for index, part in enumerate(parts, 1)}
    entry = {"steps": [{"use": f"native.{name}"} for name in steps]}
    return {"version": 1, "native": {**steps, "run": entry}}
