import json
import os
import selectors
import subprocess
import sys
import time

import pytest
import yaml


def config(jobs, **kwargs):
    return yaml.safe_dump({"name": "jobs-demo", "jobs": jobs, **kwargs})


def report(tmp_path):
    return json.loads((tmp_path / ".pipeforge/latest/report.json").read_text())


def test_jobs_selection_environment_and_report(cli, tmp_path):
    source = config(
        {
            "build": {
                "needs": "test",
                "env": {"MESSAGE": "job"},
                "steps": [{"run": 'printf "%s" "$MESSAGE" > artifact', "env": {"MESSAGE": "step"}}],
            },
            "test": {"steps": [{"run": "echo passed"}]},
            "other": {"steps": [{"run": "touch unexpected"}]},
        }
    )
    result = cli(source, "run", "build", "--color", "always")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.index("▶ test") < result.stdout.index("▶ build")
    assert "\x1b[32m" in result.stdout
    assert (tmp_path / "artifact").read_text() == "step"
    assert not (tmp_path / "unexpected").exists()
    data = report(tmp_path)
    assert data["result"] == "success"
    assert {job["name"]: job["status"] for job in data["jobs"]} == {
        "build": "success",
        "test": "success",
        "other": "skipped",
    }
    for path in (tmp_path / ".pipeforge/latest").iterdir():
        assert "\x1b" not in path.read_text()
        assert path.stat().st_mode & 0o077 == 0


def test_default_console_is_short_but_log_complete(cli, tmp_path):
    result = cli(config({"hello": {"steps": [{"run": "echo unique-child-output"}]}}), "run")
    assert result.returncode == 0
    assert "unique-child-output" not in result.stdout
    assert "unique-child-output" in (tmp_path / ".pipeforge/latest/hello.log").read_text()
    assert "PIPELINE PASSED" in result.stdout


def test_failure_report_and_skipped_dependants(cli, tmp_path):
    result = cli(
        config(
            {
                "test": {"steps": [{"name": "Check", "run": "echo useful-error >&2; exit 9"}]},
                "build": {"needs": "test", "steps": [{"run": "touch unexpected"}]},
            }
        ),
        "run",
    )
    assert result.returncode == 1
    assert "useful-error" in result.stdout
    assert "exit code 9" in result.stdout
    data = report(tmp_path)
    assert data["result"] == "failed"
    by_name = {job["name"]: job for job in data["jobs"]}
    assert by_name["test"]["steps"][0]["exit_code"] == 9
    assert by_name["build"]["status"] == "skipped"
    assert not (tmp_path / "unexpected").exists()


@pytest.mark.parametrize("args", [("run", "--dry-run"), ("run", "--list"), ("jobs",)])
def test_plan_list_create_no_artifacts(cli, tmp_path, args):
    result = cli(config({"test": {"steps": [{"run": "touch unexpected"}]}}), *args)
    assert result.returncode == 0
    assert not (tmp_path / ".pipeforge").exists()
    assert not (tmp_path / "unexpected").exists()


def test_secrets_redacted_in_console_logs_and_reports(cli, tmp_path):
    secret = "sensitive-token"
    result = cli(
        config(
            {secret: {"steps": [{"name": secret, "run": 'printf "%s" "$DEMO_TOKEN"'}]}},
            secrets={"DEMO_TOKEN": {"from": "env"}},
        ),
        "run",
        "-vv",
        env={"DEMO_TOKEN": secret},
    )
    assert result.returncode == 0
    assert secret not in result.stdout + result.stderr
    assert "***" in result.stdout
    for path in (tmp_path / ".pipeforge/latest").iterdir():
        assert secret not in path.name
        assert secret not in path.read_text()


@pytest.mark.parametrize(
    "jobs",
    [
        {"../escape": {"steps": [{"run": "true"}]}},
        {"x": {"image": "python", "steps": [{"run": "true"}]}},
        {"x": {"steps": [{"uses": "docker/build"}]}},
        {"x": {"steps": [{"run": "true", "script": "false"}]}},
        {"x": {"needs": ["x"], "steps": [{"run": "true"}]}},
    ],
)
def test_unsupported_or_unsafe_schema(cli, tmp_path, jobs):
    result = cli(config(jobs), "run")
    assert result.returncode == 2
    assert not (tmp_path / ".pipeforge").exists()


def test_stream_is_available_before_step_finishes(tmp_path):
    path = tmp_path / "pipeforge.yml"
    script = (
        'python -u -c \'import time; from pathlib import Path; print("STREAM_READY", flush=True); '
        'exec("while not Path(\\"release\\").exists(): time.sleep(.02)")\''
    )
    path.write_text(config({"live": {"steps": [{"run": script, "timeout": 5}]}}))
    env = {
        **os.environ,
        "PATH": str(__import__("pathlib").Path(sys.executable).parent)
        + os.pathsep
        + os.environ["PATH"],
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "pipeforge", "run", "-vv", "-f", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        output = b""
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + 4
            while b"STREAM_READY" not in output and time.monotonic() < deadline:
                for key, _ in selector.select(0.1):
                    output += os.read(key.fd, 4096)
        assert b"STREAM_READY" in output
        assert process.poll() is None
        logs = list((tmp_path / ".pipeforge/runs").glob("*/live.log"))
        assert "STREAM_READY" in logs[0].read_text()
        (tmp_path / "release").touch()
        process.communicate(timeout=5)
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=5)


def test_runtime_symlink_rejected_before_execution(cli, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".pipeforge").symlink_to(outside)
    result = cli(config({"test": {"steps": [{"run": "touch unexpected"}]}}), "run")
    assert result.returncode == 1
    assert not (tmp_path / "unexpected").exists()
    assert not list(outside.iterdir())


def test_redacted_job_log_does_not_collide_with_job_name(cli, tmp_path):
    source = config(
        {
            "private": {"steps": [{"run": "true"}]},
            "job-1": {"needs": "private", "steps": [{"run": "true"}]},
        },
        secrets={"DEMO_TOKEN": {"from": "env"}},
    )
    result = cli(source, "run", env={"DEMO_TOKEN": "private"})
    assert result.returncode == 0, result.stdout
    logs = list((tmp_path / ".pipeforge/latest").glob("*.log"))
    assert len(logs) == 2
