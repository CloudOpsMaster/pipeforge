import subprocess

import pytest

from pipeforge.context import detect_provider, git_metadata


@pytest.mark.parametrize(
    "env,expected",
    [
        ({}, "local"),
        ({"GITHUB_ACTIONS": "true"}, "github"),
        ({"GITLAB_CI": "true"}, "gitlab"),
        ({"JENKINS_URL": "https://example.invalid"}, "jenkins"),
    ],
)
def test_provider(env, expected):
    assert detect_provider(env) == expected


def test_checkout_metadata_overrides_ci_sha_and_handles_detached_head(tmp_path):
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-b", "feature/test")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "test",
    )
    sha = git("rev-parse", "HEAD")
    git("tag", "v-test")
    env = {"GITHUB_ACTIONS": "true", "GITHUB_SHA": "a" * 40}
    data = git_metadata(tmp_path, env)
    assert data == {"sha": sha, "short_sha": sha[:7], "branch": "feature/test", "tag": "v-test"}
    git("checkout", "--detach")
    assert git_metadata(tmp_path, env)["branch"] == ""


def test_missing_git_metadata(tmp_path):
    assert git_metadata(tmp_path, {}) == {"sha": "", "short_sha": "", "branch": "", "tag": ""}


def test_ci_tag_without_git_objects(tmp_path):
    metadata = git_metadata(
        tmp_path,
        {
            "GITHUB_ACTIONS": "true",
            "GITHUB_SHA": "a" * 40,
            "GITHUB_REF_TYPE": "tag",
            "GITHUB_REF_NAME": "v0.1.0",
        },
    )
    assert metadata["tag"] == "v0.1.0"
    assert metadata["sha"] == "a" * 40
