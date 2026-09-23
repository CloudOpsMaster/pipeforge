"""Prepare verified assets and publish a version once, from a successful main CI run."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def version_info(root=ROOT):
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    if not re.fullmatch(r"0|[1-9]\d*", version.split(".")[0]) or not re.fullmatch(
        r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version
    ):
        raise ValueError("Release requires an X.Y.Z version")
    changelog = (root / "CHANGELOG.md").read_text()
    match = re.search(rf"^## {re.escape(version)}\s*\n(.*?)(?=^## |\Z)", changelog, re.M | re.S)
    if not match or not match[1].strip():
        raise ValueError("Release requires a nonempty changelog section")
    return version, match[1].strip() + "\n"


def prepare(directory):
    version, _ = version_info()
    expected = {f"pipeforge-{version}-py3-none-any.whl", f"pipeforge-{version}.tar.gz"}
    if {p.name for p in directory.glob("*.whl")} | {
        p.name for p in directory.glob("*.tar.gz")
    } != expected:
        raise ValueError("Distribution files do not match the project version")
    shutil.copyfile(ROOT / "schema/pipeforge.schema.json", directory / "pipeforge.schema.json")
    names = sorted(expected | {"pipeforge.schema.json"})
    (directory / "SHA256SUMS").write_text(
        "".join(f"{hashlib.sha256((directory / n).read_bytes()).hexdigest()}  {n}\n" for n in names)
    )


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def publish(directory):
    if os.environ.get("GITHUB_REF") != "refs/heads/main" or os.environ.get(
        "GITHUB_EVENT_NAME"
    ) not in {"push", "workflow_dispatch"}:
        raise ValueError("Publishing is restricted to main push/manual runs")
    sha = os.environ["GITHUB_SHA"]
    if command("git", "rev-parse", "HEAD") != sha:
        raise ValueError("Checkout does not match CI commit")
    version, notes = version_info()
    tag = f"v{version}"
    tags = command("git", "tag", "--list").splitlines()
    pages = json.loads(
        command("gh", "api", "repos/{owner}/{repo}/releases", "--paginate", "--slurp")
    )
    releases = [release for page in pages for release in page]
    release = next((r for r in releases if r["tag_name"] == tag), None)
    if tag in tags:
        target = command("git", "rev-parse", f"{tag}^{{commit}}")
        if target != sha:
            subprocess.run(["git", "merge-base", "--is-ancestor", target, sha], check=True)
            if release and not release["draft"]:
                print(f"{tag} already published; bump version and changelog for the next release.")
                return
            raise ValueError("Unpublished version belongs to an older commit; rerun its CI")
        if release and not release["draft"]:
            print(f"{tag} already published.")
            return
    else:
        versions = [
            tuple(map(int, t[1:].split("."))) for t in tags if re.fullmatch(r"v\d+\.\d+\.\d+", t)
        ]
        if versions and tuple(map(int, version.split("."))) <= max(versions):
            raise ValueError("New release version must increase")
    subprocess.run(["shasum", "-a", "256", "-c", "SHA256SUMS"], cwd=directory, check=True)
    if tag not in tags:
        command(
            "gh",
            "api",
            "repos/{owner}/{repo}/git/refs",
            "-X",
            "POST",
            "-f",
            f"ref=refs/tags/{tag}",
            "-f",
            f"sha={sha}",
        )
    notes_path = directory.parent / "release-notes.md"
    notes_path.write_text(notes)
    if release is None:
        command(
            "gh",
            "release",
            "create",
            tag,
            "--verify-tag",
            "--draft",
            "--title",
            f"PipeForge {tag}",
            "--notes-file",
            str(notes_path),
        )
    assets = [
        directory / n
        for n in (
            f"pipeforge-{version}-py3-none-any.whl",
            f"pipeforge-{version}.tar.gz",
            "pipeforge.schema.json",
            "SHA256SUMS",
        )
    ]
    command("gh", "release", "upload", tag, *map(str, assets), "--clobber")
    command("gh", "release", "edit", tag, "--draft=false", "--latest")
    print(f"Published {tag} at {sha}")


if __name__ == "__main__":
    {"prepare": prepare, "publish": publish}[sys.argv[1]](Path(sys.argv[2]).resolve())
