import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from pipeforge.config import load_config
from pipeforge.resolver import Resolver

ROOT = Path(__file__).parents[2]
PIPELINES = sorted((ROOT / "examples").rglob("pipeforge.yml")) + [ROOT / "pipeforge.yml"]


@pytest.mark.parametrize("path", PIPELINES, ids=lambda path: str(path.relative_to(ROOT)))
def test_every_pipeline_example_validates(path):
    Resolver(load_config(path), {}, validate=True).resolve()


@pytest.mark.parametrize("example", ["basic", "python"])
@pytest.mark.parametrize("provider", ["local", "github", "gitlab", "jenkins"])
def test_real_examples_with_each_provider(tmp_path, example, provider):
    destination = tmp_path / example
    shutil.copytree(
        ROOT / "examples/pipelines" / example,
        destination,
        ignore=shutil.ignore_patterns(".pipeforge", "__pycache__"),
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_URL", "JENKINS_HOME"}
    }
    flags = {
        "github": {"GITHUB_ACTIONS": "true"},
        "gitlab": {"GITLAB_CI": "true"},
        "jenkins": {"JENKINS_URL": "https://example.invalid"},
    }
    env.update(flags.get(provider, {}))
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
    result = subprocess.run(
        [sys.executable, "-m", "pipeforge", "run", "-vv"],
        cwd=destination,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert provider in result.stdout
    assert "Hello PipeForge" in result.stdout
    assert (destination / ".pipeforge/latest/report.json").exists()


def test_ci_examples_invoke_launcher_and_initialize_submodules():
    github = yaml.load(
        (ROOT / "examples/ci/github-actions/workflow.yml").read_text(), Loader=yaml.BaseLoader
    )
    steps = github["jobs"]["pipeline"]["steps"]
    assert steps[0]["with"]["submodules"] == "recursive"
    assert any("./.pf/run" in step.get("run", "") for step in steps)
    gitlab = yaml.safe_load((ROOT / "examples/ci/gitlab/.gitlab-ci.yml").read_text())
    assert gitlab["variables"]["GIT_SUBMODULE_STRATEGY"] == "recursive"
    assert "./.pf/run" in gitlab["pipeforge"]["script"][0]
    jenkins = (ROOT / "examples/ci/jenkins/Jenkinsfile").read_text()
    assert "git submodule update --init --recursive" in jenkins
    assert "./.pf/run" in jenkins
