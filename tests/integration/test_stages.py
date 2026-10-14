import json

import pytest
import yaml


def pipeline():
    return {
        "name": "stages",
        "blocks": {
            "one": {"steps": [{"name": "Repeat", "run": "echo one >> calls"}]},
            "two": {"steps": [{"name": "Repeat", "run": "echo two >> calls"}]},
        },
        "jobs": {
            "prepare": {"stage": "prepare", "steps": [{"run": "echo prepare >> calls"}]},
            "a": {"stage": "publish", "needs": "prepare", "use": ["one", "two", "one"]},
            "b": {"stage": "publish", "needs": "a", "steps": [{"run": "echo b >> calls"}]},
        },
    }


def test_stage_rerun_and_repeated_steps(cli, tmp_path):
    source = yaml.safe_dump(pipeline())
    for _ in range(2):
        result = cli(source, "run", "--stage", "publish", "--max-parallel", "2")
        assert result.returncode == 0, result.stdout
    assert (tmp_path / "calls").read_text().splitlines() == ["one", "two", "one", "b"] * 2
    report = json.loads((tmp_path / ".pipeforge/latest/report.json").read_text())
    assert report["critical_path"] == ["a", "b"]
    steps = next(job for job in report["jobs"] if job["name"] == "a")["steps"]
    assert [step["index"] for step in steps] == [1, 2, 3]
    assert [step["block"] for step in steps] == ["one", "two", "one"]
    assert report["stages"][1]["duration"] >= 0


def test_stage_with_dependencies(cli, tmp_path):
    result = cli(yaml.safe_dump(pipeline()), "run", "--stage", "publish", "--with-needs")
    assert result.returncode == 0, result.stdout
    assert (tmp_path / "calls").read_text().splitlines() == ["prepare", "one", "two", "one", "b"]


def test_exact_job(cli, tmp_path):
    result = cli(yaml.safe_dump(pipeline()), "exec-job", "b")
    assert result.returncode == 0, result.stdout
    assert (tmp_path / "calls").read_text() == "b\n"


@pytest.mark.parametrize(
    "args",
    [
        ("plan", "--stage", "publish"),
        ("graph",),
        ("graph", "--format", "mermaid"),
        ("graph", "--format", "dot"),
    ],
)
def test_presentation_is_read_only(cli, tmp_path, args):
    result = cli(yaml.safe_dump(pipeline()), *args)
    assert result.returncode == 0, result.stdout
    assert "Dependency order:" not in result.stdout
    assert not (tmp_path / ".pipeforge").exists()
    assert not (tmp_path / "calls").exists()


def test_artifacts_between_isolated_job_runs(cli, tmp_path):
    source = yaml.safe_dump(
        {
            "name": "transport",
            "jobs": {
                "build": {
                    "artifacts": ["nested/file.txt"],
                    "steps": [
                        {
                            "run": 'mkdir -p "$PIPEFORGE_ARTIFACTS/nested"; '
                            'echo hello > "$PIPEFORGE_ARTIFACTS/nested/file.txt"'
                        }
                    ],
                },
                "consume": {
                    "needs": "build",
                    "steps": [{"run": 'cat "$PIPEFORGE_ARTIFACTS/nested/file.txt" > consumed'}],
                },
            },
        }
    )
    result = cli(source, "exec-job", "build", "--artifact-output", str(tmp_path / "transport"))
    assert result.returncode == 0, result.stdout
    result = cli(source, "exec-job", "consume", "--artifact-input", str(tmp_path / "transport"))
    assert result.returncode == 0, result.stdout
    assert (tmp_path / "consumed").read_text() == "hello\n"
    assert list((tmp_path / "transport").rglob("*")) == [
        tmp_path / "transport/nested",
        tmp_path / "transport/nested/file.txt",
    ]


def test_github_summary_and_redacted_graph(cli, tmp_path):
    definition = pipeline()
    definition["jobs"]["a"]["stage"] = "sensitive-token"
    definition["secrets"] = {"DEMO_TOKEN": {"from": "env"}}
    summary = tmp_path / "summary"
    result = cli(
        yaml.safe_dump(definition),
        "run",
        env={
            "DEMO_TOKEN": "sensitive-token",
            "GITHUB_STEP_SUMMARY": str(summary),
        },
    )
    assert result.returncode == 0, result.stdout
    content = summary.read_text()
    assert "| Stage | Job | Result | Duration |" in content
    assert "`\x60\x60mermaid" in content
    encoded_secret = "".join(f"#{ord(char)};" for char in "sensitive-token")
    assert encoded_secret not in content
    assert "sensitive-token" not in content


@pytest.mark.parametrize(
    "args",
    [
        ("run", "--stage", "absent"),
        ("run", "a", "--stage", "publish"),
        ("run", "--stage", "publish", "--from", "a"),
        ("run", "--stage", "publish", "--resume", "a" * 32),
        ("exec-job", "a", "--with-needs"),
    ],
)
def test_invalid_selection_has_no_side_effects(cli, tmp_path, args):
    result = cli(yaml.safe_dump(pipeline()), *args)
    assert result.returncode == 2, result.stdout
    assert not (tmp_path / "calls").exists()
    assert not (tmp_path / ".pipeforge").exists()


def test_existing_export_rejected_before_commands(cli, tmp_path):
    (tmp_path / "export").mkdir()
    result = cli(yaml.safe_dump(pipeline()), "run", "--artifact-output", str(tmp_path / "export"))
    assert result.returncode == 1, result.stdout
    assert not (tmp_path / "calls").exists()


def test_render_cli_writes_valid_workflow_without_execution(cli, tmp_path):
    output = tmp_path / ".github/workflows/generated.yml"
    result = cli(yaml.safe_dump(pipeline()), "render", "github", "-o", str(output))
    assert result.returncode == 0, result.stdout
    workflow = yaml.safe_load(output.read_text())
    assert workflow["jobs"]["b"]["needs"] == ["a"]
    assert workflow["jobs"]["a"]["steps"][-1]["run"] == ".pf/run exec-job a -f pipeforge.yml"
    assert not (tmp_path / ".pipeforge").exists()


def test_generated_workflow_across_fresh_workspaces(tmp_path):
    import os
    import shlex
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    from pipeforge.config import load_config
    from pipeforge.github import render_github

    source = (Path(__file__).parents[2] / "examples/pipelines/stages/pipeforge.yml").read_text()
    definition = tmp_path / "pipeforge.yml"
    definition.write_text(source)
    workflow = yaml.safe_load(render_github(load_config(definition), Path("pipeforge.yml")))
    store = tmp_path / "store"
    store.mkdir()
    env = {**os.environ, "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]}
    for name, job in workflow["jobs"].items():
        workspace = tmp_path / name
        workspace.mkdir()
        (workspace / "pipeforge.yml").write_text(source)
        for step in job["steps"]:
            if "download-artifact" in step.get("uses", ""):
                shutil.copytree(
                    store / step["with"]["name"],
                    workspace / step["with"]["path"],
                    dirs_exist_ok=True,
                )
            elif "run" in step:
                command = shlex.split(step["run"])
                result = subprocess.run(
                    [sys.executable, "-m", "pipeforge", *command[1:]],
                    cwd=workspace,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                assert result.returncode == 0, result.stdout + result.stderr
                report = json.loads((workspace / ".pipeforge/latest/report.json").read_text())
                assert [job["name"] for job in report["jobs"] if job["status"] == "success"] == [
                    name
                ]
            elif "upload-artifact" in step.get("uses", ""):
                shutil.copytree(workspace / step["with"]["path"], store / step["with"]["name"])
    assert (store / "pipeforge-generate/output/reel.txt").read_text() == "example reel\n"
