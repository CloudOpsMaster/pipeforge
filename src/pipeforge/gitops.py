"""Draft a Kubernetes Deployment image promotion as a reviewable patch."""

import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from pipeforge.config import mapping, read_yaml_document
from pipeforge.errors import ConfigError
from pipeforge.rules import git_root

DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
IMAGE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_./:-]*\Z")


@dataclass(frozen=True)
class Promotion:
    patch: str
    desired: str
    observed: str | None
    drift: bool | None


def draft(file: Path, name: str, digest: str, observed: str | None) -> Promotion:
    if not DIGEST.fullmatch(digest) or (observed is not None and not DIGEST.fullmatch(observed)):
        raise ConfigError("Use a full lowercase SHA-256 digest.")
    if not file.is_file():
        raise ConfigError("Expected a regular Deployment file.")
    data, before = read_yaml_document(file)
    data = mapping(data, "Deployment")
    if (data.get("apiVersion"), data.get("kind")) != ("apps/v1", "Deployment"):
        raise ConfigError("Expected a single apps/v1 Deployment.")
    spec = mapping(data.get("spec"), "Deployment spec")
    template = mapping(spec.get("template"), "pod template")
    pod = mapping(template.get("spec"), "pod spec")
    containers = pod.get("containers")
    if not isinstance(containers, list):
        raise ConfigError("Deployment requires a list of pod containers.")
    matches = [item for item in containers if isinstance(item, dict) and item.get("name") == name]
    if len(matches) != 1:
        raise ConfigError("Select a unique named container.")
    selected = matches[0]
    image = selected.get("image")
    if not isinstance(image, str):
        raise ConfigError("Expected a container image reference.")
    base, separator, current = image.partition("@")
    if not IMAGE.fullmatch(base) or (separator and not DIGEST.fullmatch(current)):
        raise ConfigError("The existing image reference uses an unsupported format.")
    if ":" in base.rpartition("/")[2]:
        base = base.rsplit(":", 1)[0]
    desired = f"{base}@{digest}"
    if image == desired:
        patch = ""
    else:
        if not before.endswith("\n") or "\r" in before:
            raise ConfigError("Save the Deployment with LF line endings and a final newline.")
        node = yaml.compose(before, Loader=yaml.SafeLoader)
        for part in ("spec", "template", "spec", "containers"):
            node = child(node, part)
        if not isinstance(node, SequenceNode):
            raise ConfigError("Expected a sequence of containers.")
        target = child(node.value[containers.index(selected)], "image")
        if not isinstance(target, ScalarNode):
            raise ConfigError("Expected a scalar container image.")
        start, end = target.start_mark.index, target.end_mark.index
        token = before[start:end]
        literals = {image: desired, f"'{image}'": f"'{desired}'", f'"{image}"': f'"{desired}"'}
        if token not in literals:
            raise ConfigError("Use a plain or simply quoted image scalar.")
        after = before[:start] + literals[token] + before[end:]
        resolved = file.resolve()
        root = git_root(resolved.parent) or Path.cwd()
        if not resolved.is_relative_to(root):
            raise ConfigError("Place the Deployment inside the project before drafting promotion.")
        path = resolved.relative_to(root).as_posix()
        if not IMAGE.fullmatch(path) or ":" in path:
            raise ConfigError("Use a portable Deployment filename for the patch.")

        patch = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                n=0,
            )
        )
    return Promotion(patch, desired, observed, None if observed is None else observed != digest)


def child(node: Node | None, key: str) -> Node:
    if not isinstance(node, MappingNode):
        raise ConfigError("Invalid Deployment structure.")
    fields = cast(list[tuple[Node, Node]], node.value)
    return next(
        value for name, value in fields if isinstance(name, ScalarNode) and name.value == key
    )
