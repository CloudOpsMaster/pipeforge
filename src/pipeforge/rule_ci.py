"""Compile a rule invocation into a single trusted runner job."""

import re
import shlex
from pathlib import Path

import yaml

from pipeforge.errors import ConfigError
from pipeforge.rules import RuleSet, get_rule

CHECKOUT = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"


def export(spec: RuleSet, file: Path, name: str, provider: str, mode: str) -> str:
    rule = get_rule(spec.rules, name)
    source = f"{rule.source.file}:{rule.source.line}"
    if mode != "wrapper":
        raise ConfigError(f"{source}: standalone execution is unsupported. Use wrapper mode.")
    if spec.secrets:
        raise ConfigError(
            f"{source}: provider secret bindings are unsupported. "
            "Use a rule configuration without declared secrets."
        )
    spec.graph(name, {})
    if not file.resolve().is_relative_to(spec.root):
        raise ConfigError(f"{source}: configuration must be relative to checkout.")
    config = file.resolve().relative_to(spec.root).as_posix()
    # Provider expressions and Groovy have distinct interpolation rules.
    if any(not char.isascii() or not (char.isalnum() or char in "/_.-") for char in config):
        raise ConfigError(f"{source}: rename the configuration to a portable path.")
    command = shlex.join(["pipeforge", name, "--file", config])
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


def inspect_wrapper(spec: RuleSet, file: Path, provider: str, workflow: Path) -> dict[str, str]:
    """Recognize the exact wrapper contract, rejecting extra native semantics."""
    from pipeforge.config import MAX_CONFIG_BYTES, read_yaml

    location = f"{workflow.name}:1"
    unsupported = ConfigError(
        f"{location}: unsupported {provider} wrapper. "
        "Use the generated single-job wrapper or migrate the provider configuration manually."
    )
    if not workflow.is_file():
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
        data = read_yaml(workflow)
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
