"""Provider detection and metadata for the application's checkout, not the framework."""

import re
import shutil
import subprocess  # nosec B404
from collections.abc import Mapping
from pathlib import Path


def detect_provider(env: Mapping[str, str]) -> str:
    if env.get("GITHUB_ACTIONS") == "true":
        return "github"
    if env.get("GITLAB_CI") == "true":
        return "gitlab"
    if env.get("JENKINS_URL") or env.get("JENKINS_HOME"):
        return "jenkins"
    return "local"


def git_metadata(directory: Path, env: Mapping[str, str]) -> dict[str, str]:
    git = shutil.which("git")

    def query(*args: str) -> str:
        if not git:
            return ""
        try:
            # No shell; fixed read-only git commands. Ignore Git-directory overrides.
            child_env = {k: v for k, v in env.items() if not k.startswith("GIT_")}
            result = subprocess.run(  # nosec B603
                [git, "-C", str(directory), *args],
                env=child_env,
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired, UnicodeError):
            return ""

    sha = query("rev-parse", "--verify", "HEAD")
    branch = query("symbolic-ref", "--quiet", "--short", "HEAD")
    tag = query("describe", "--tags", "--exact-match", "HEAD").split("\n")[0]
    provider = detect_provider(env)
    sha_key = {"github": "GITHUB_SHA", "gitlab": "CI_COMMIT_SHA", "jenkins": "GIT_COMMIT"}
    candidate = env.get(sha_key.get(provider, ""), "")
    if not sha:
        sha = candidate if re.fullmatch(r"[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?", candidate) else ""
    # Shallow CI checkouts often omit tag objects; only trust a label for this commit.
    if not tag and sha and sha == candidate:
        if provider == "github" and env.get("GITHUB_REF_TYPE") == "tag":
            tag = env.get("GITHUB_REF_NAME", "")
        elif provider == "gitlab":
            tag = env.get("CI_COMMIT_TAG", "")
    if not branch:
        if provider == "github":
            branch = env.get("GITHUB_HEAD_REF", "") or (
                env.get("GITHUB_REF_NAME", "") if env.get("GITHUB_REF_TYPE") == "branch" else ""
            )
        elif provider == "gitlab":
            branch = env.get("CI_MERGE_REQUEST_SOURCE_BRANCH_NAME", "") or env.get(
                "CI_COMMIT_BRANCH", ""
            )
        elif provider == "jenkins":
            branch = env.get("BRANCH_NAME", "") or env.get("GIT_BRANCH", "")
    return {"sha": sha, "short_sha": sha[:7], "branch": branch, "tag": tag}
