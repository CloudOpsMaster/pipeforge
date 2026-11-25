"""Parse trusted versioned rule files and expand calls to an execution graph."""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from yaml.nodes import MappingNode

from pipeforge.config import ENV_KEY, StrictLoader, mapping, read_yaml
from pipeforge.errors import ConfigError

LEGACY = frozenset({"run", "jobs", "plan", "graph", "exec-job", "render", "validate"})
RULE_COMMANDS = frozenset(
    {"list", "explain", "completion", "export", "import", "studio", "promote"}
)
FORMS = frozenset({"run", "script", "argv", "steps"})
METADATA = frozenset({"version", "include", "vars", "secrets"})
IDENTIFIER = re.compile(r"[a-zA-Z_][a-zA-Z0-9_-]{0,63}\Z")
INPUT_TYPES = {"string": str, "integer": int, "boolean": bool}
ARG_INPUT = re.compile(r"\$\{inputs\.([a-zA-Z_][a-zA-Z0-9_]*)\}\Z")
MAX_NODES = 512
SHA256 = re.compile(r"sha256:[a-f0-9]{64}\Z")


class LocatedMapping(dict[str, Any]):
    line: int


class RuleLoader(StrictLoader):
    def construct_yaml_map(self, node: MappingNode) -> Any:
        result = LocatedMapping()
        result.line = node.start_mark.line + 1
        yield result
        result.update(cast(dict[str, Any], self.construct_mapping(node)))


RuleLoader.add_constructor("tag:yaml.org,2002:map", RuleLoader.construct_yaml_map)


@dataclass(frozen=True)
class Source:
    file: str
    line: int


@dataclass(frozen=True)
class Input:
    kind: str
    default: str | int | bool | None


@dataclass(frozen=True)
class Call:
    rule: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Tool:
    name: str
    digest: str | None = None


@dataclass(frozen=True)
class Rule:
    name: str
    source: Source
    command: str | tuple[str, ...] | None
    cwd: Path
    inputs: dict[str, Input]
    steps: tuple[tuple[Call, ...], ...]
    timeout: int
    tools: tuple[Tool, ...]


@dataclass(frozen=True)
class Node:
    id: str
    rule: Rule
    inputs: dict[str, Any]
    dependencies: tuple[str, ...]
    children: tuple[tuple["Node", ...], ...]


@dataclass(frozen=True)
class RuleSet:
    root: Path
    rules: dict[str, Rule]
    secrets: frozenset[str]

    def graph(self, name: str, arguments: dict[str, Any]) -> Node:
        count = 0
        active: list[str] = []

        def expand(call: Call, dependencies: tuple[str, ...]) -> Node:
            nonlocal count
            count += 1
            if count > MAX_NODES:
                raise ConfigError("Rule invocation exceeds 512 nodes.")
            if len(active) >= 32:
                raise ConfigError("Rule invocation nesting exceeds 32 levels.")
            rule = get_rule(self.rules, call.rule)
            active.append(rule.name)
            inputs = bind(rule, call.arguments)
            node_id = f"n{count}"
            incoming = dependencies
            groups = []
            for group in rule.steps:
                children = tuple(
                    expand(Call(child.rule, forwarded(child.arguments, inputs)), dependencies)
                    for child in group
                )
                groups.append(children)
                dependencies = tuple(child.id for child in children)
            active.pop()
            return Node(node_id, rule, inputs, incoming, tuple(groups))

        return expand(Call(name, arguments), ())


def forwarded(arguments: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for key, value in arguments.items():
        reference = ARG_INPUT.fullmatch(value) if isinstance(value, str) else None
        result[key] = values[reference[1]] if reference else value
    return result


def get_rule(rules: dict[str, Rule], name: str) -> Rule:
    if name not in rules:
        raise ConfigError(f"Unknown rule: {name}")
    return rules[name]


def bind(rule: Rule, arguments: dict[str, Any]) -> dict[str, Any]:
    if arguments.keys() - rule.inputs.keys():
        raise ConfigError(f"{rule.name}: unknown input name.")
    result = {}
    for name, spec in rule.inputs.items():
        value = arguments.get(name, spec.default)
        if type(value) is not INPUT_TYPES[spec.kind] or "\0" in str(value):
            raise ConfigError(f"{rule.name}: input {name} requires {spec.kind}.")
        result[name] = value
    return result


def git_root(directory: Path) -> Path | None:
    return next(
        (path for path in (directory, *directory.parents) if (path / ".git").exists()), None
    )


def discover_file() -> Path | None:
    directory = Path.cwd().resolve()
    boundary = git_root(directory) or directory
    for parent in (directory, *directory.parents):
        candidates = [parent / name for name in ("pipeforge.yml", "pipeforge.yaml")]
        found = [path for path in candidates if path.exists() or path.is_symlink()]
        if len(found) > 1:
            raise ConfigError(
                "Both pipeforge.yml and pipeforge.yaml exist. Select one with --file."
            )
        if found:
            confined(parent, found[0].name, boundary)
            return found[0]
        if parent == boundary:
            break
    return None


def discover() -> Path:
    path = discover_file()
    if path is None:
        raise ConfigError("No rule configuration found. Supply --file.")
    return path


def confined(directory: Path, value: Any, root: Path) -> Path:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ConfigError("Expected a relative file path.")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ConfigError("Absolute paths and parent traversal are forbidden.")
    try:
        resolved = (directory / relative).resolve(strict=True)
    except (OSError, RuntimeError):
        raise ConfigError("Referenced file does not exist or cannot be resolved.") from None
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ConfigError("Referenced file must be a regular file inside the project.")
    return resolved


def strings(value: Any, *, nonempty: bool = True) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or (nonempty and not value)
        or any(not isinstance(item, str) or "\0" in item for item in value)
    ):
        raise ConfigError("Expected a list of strings.")
    return tuple(value)


def parse_call(raw: Any) -> Call:
    item = mapping(raw, "rule step")
    if item.keys() - {"use", "with"} or not isinstance(item.get("use"), str):
        raise ConfigError("Expected a rule step with use and optional with.")
    return Call(item["use"], mapping(item.get("with", {}), "step inputs"))


def parse_rule(name: str, item: dict[str, Any], path: Path, root: Path) -> Rule:
    forms = item.keys() & FORMS
    if len(forms) != 1 or item.keys() - FORMS - {"inputs", "timeout", "tools"}:
        raise ConfigError("A rule requires exactly one of run, script, argv or steps.")
    inputs = {}
    for key, raw in mapping(item.get("inputs", {}), "inputs").items():
        spec = mapping(raw, "input")
        kind = spec.get("type")
        if (
            not ENV_KEY.fullmatch(key)
            or not isinstance(kind, str)
            or kind not in INPUT_TYPES
            or spec.keys() - {"type", "default"}
            or (
                "default" in spec
                and (type(spec["default"]) is not INPUT_TYPES[kind] or "\0" in str(spec["default"]))
            )
        ):
            raise ConfigError("Invalid input declaration.")
        inputs[key] = Input(kind, spec.get("default"))
    timeout = item.get("timeout", 300)
    if type(timeout) is not int or not 1 <= timeout <= 86400:
        raise ConfigError("Timeout must be an integer between 1 and 86400 seconds.")
    raw_tools = item.get("tools", [])
    if isinstance(raw_tools, dict):
        if any(
            not isinstance(digest, str) or not SHA256.fullmatch(digest)
            for digest in raw_tools.values()
        ):
            raise ConfigError("Pinned tools require a full lowercase SHA-256 digest.")
        tools = tuple(Tool(tool, digest) for tool, digest in raw_tools.items())
    else:
        tools = tuple(Tool(tool) for tool in strings(raw_tools, nonempty=False))
    if any(not IDENTIFIER.fullmatch(tool.name) for tool in tools):
        raise ConfigError("Tool names must be executable names.")
    command: str | tuple[str, ...] | None = None
    steps = []
    form = next(iter(forms))
    value = item[form]
    if form == "run":
        command = value if isinstance(value, str) else " &&\n".join(strings(value))
        if not command.strip() or "\0" in command:
            raise ConfigError("Expected nonempty shell code.")
    elif form == "script":
        script = confined(path.parent, value, root)
        command = ("/bin/sh", os.fspath(script))
    elif form == "argv":
        command = strings(value)
        if not command[0] or ARG_INPUT.fullmatch(command[0]):
            raise ConfigError("argv must start with a fixed executable.")
        for arg in command[1:]:
            match = ARG_INPUT.fullmatch(arg)
            if match and match[1] not in inputs:
                raise ConfigError("argv references an undeclared input.")
    else:
        if "timeout" in item:
            raise ConfigError("Set timeouts on command rules called by steps.")
        if not isinstance(value, list) or not value:
            raise ConfigError("Expected a nonempty steps list.")
        for entry in value:
            if isinstance(entry, dict) and entry.keys() == {"parallel"}:
                parallel = entry["parallel"]
                if not isinstance(parallel, list) or not 1 <= len(parallel) <= 16:
                    raise ConfigError("Parallel groups require 1 to 16 calls.")
                steps.append(tuple(parse_call(child) for child in parallel))
            else:
                steps.append((parse_call(entry),))
    source = Source(path.relative_to(root).as_posix(), getattr(item, "line", 1))
    return Rule(name, source, command, path.parent, inputs, tuple(steps), timeout, tools)


def load_rules(path: Path) -> RuleSet:
    parent = path.absolute().parent.resolve()
    root = git_root(parent) or parent
    path = confined(parent, path.name, root)
    seen: set[Path] = set()
    active: set[Path] = set()
    rules: dict[str, Rule] = {}
    secrets: set[str] = set()

    def visit(item: Any, name: str, source: Path) -> None:
        branch = mapping(item, name)
        if not branch:
            raise ConfigError(f"Empty rule namespace: {name}")
        if any(key in FORMS and not isinstance(value, dict) for key, value in branch.items()):
            if name in rules:
                raise ConfigError(f"Duplicate rule: {name}")
            try:
                rules[name] = parse_rule(name, branch, source, root)
            except ConfigError as error:
                location = source.relative_to(root)
                raise ConfigError(f"{location}:{getattr(branch, 'line', 1)}: {error}") from None
            return
        for key, child in branch.items():
            if not IDENTIFIER.fullmatch(key):
                raise ConfigError("Invalid rule key.")
            visit(child, f"{name}.{key}", source)

    def load(source: Path) -> None:
        if source in active:
            raise ConfigError("Include cycle detected.")
        if source in seen:
            raise ConfigError("Duplicate include path.")
        if len(seen) >= 64 or len(active) >= 16:
            raise ConfigError("Include limit exceeded.")
        active.add(source)
        seen.add(source)
        data = mapping(read_yaml(source, RuleLoader), "rule configuration")
        version = data.get("version", 1 if source != path else None)
        if type(version) is not int or version != 1:
            raise ConfigError("Rule configuration requires version: 1.")
        variables = mapping(data.get("vars", {}), "vars")
        if any(type(value) not in (str, int, bool) for value in variables.values()):
            raise ConfigError("vars accepts scalar metadata.")
        for secret in strings(data.get("secrets", []), nonempty=False):
            if not ENV_KEY.fullmatch(secret):
                raise ConfigError("Invalid secret environment name.")
            secrets.add(secret)
        for include in strings(data.get("include", []), nonempty=False):
            load(confined(source.parent, include, root))
        for key, value in data.items():
            if key in METADATA:
                continue
            if key in LEGACY | RULE_COMMANDS | {"pipeline", "blocks", "name", "values"}:
                raise ConfigError(f"Reserved rule name: {key}")
            if not IDENTIFIER.fullmatch(key):
                raise ConfigError("Invalid rule key.")
            visit(value, key, source)
        active.remove(source)

    load(path)
    if not rules:
        raise ConfigError("No executable rules found.")
    for name in sorted(rules):
        if any(".".join(name.split(".")[:i]) in rules for i in range(1, name.count(".") + 1)):
            raise ConfigError(f"Rule and namespace conflict: {name}")

    sizes: dict[str, tuple[int, int]] = {}
    checking: set[str] = set()

    def validate(rule: Rule) -> tuple[int, int]:
        if rule.name in checking:
            raise ConfigError(f"Rule reference cycle at {rule.name}.")
        if rule.name in sizes:
            return sizes[rule.name]
        if len(checking) >= 32:
            raise ConfigError("Rule reference nesting exceeds 32 levels.")
        checking.add(rule.name)
        depth, count = 1, 1
        placeholders = {"string": "", "integer": 0, "boolean": False}
        sample = {key: placeholders[spec.kind] for key, spec in rule.inputs.items()}
        for group in rule.steps:
            for call in group:
                child = get_rule(rules, call.rule)
                for value in call.arguments.values():
                    reference = ARG_INPUT.fullmatch(value) if isinstance(value, str) else None
                    if reference and reference[1] not in rule.inputs:
                        raise ConfigError(f"{rule.name}: step references an undeclared input.")
                bind(child, forwarded(call.arguments, sample))
                child_depth, child_count = validate(child)
                depth = max(depth, 1 + child_depth)
                count += child_count
                if count > MAX_NODES:
                    raise ConfigError(f"{rule.name}: invocation exceeds 512 nodes.")
        checking.remove(rule.name)
        if depth > 32:
            raise ConfigError("Rule reference nesting exceeds 32 levels.")
        sizes[rule.name] = (depth, count)
        return depth, count

    for rule in rules.values():
        validate(rule)
    return RuleSet(root, rules, frozenset(secrets))
