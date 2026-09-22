import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml


def source(*steps, **kwargs):
    return yaml.safe_dump({"name": "test", "pipeline": list(steps), **kwargs})


def test_cli_help_and_version():
    for args, expected in [(["--help"], "validate"), (["--version"], "0.1.0.dev0")]:
        result = subprocess.run(
            [sys.executable, "-m", "pipeforge", *args], capture_output=True, text=True, timeout=5
        )
        assert result.returncode == 0
        assert expected in result.stdout


def test_real_pipeline_values_and_working_directory(cli, tmp_path):
    result = cli(
        source(
            {
                "name": "write",
                "env": {"MESSAGE": "${values.message}"},
                "script": 'printf "%s" "$MESSAGE" > result.txt',
            },
            {"name": "read", "script": "cat result.txt"},
            values={"message": "Hello PipeForge"},
        ),
        "run",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Hello PipeForge" in result.stdout
    assert (tmp_path / "result.txt").read_text() == "Hello PipeForge"


def test_failed_step_stops_pipeline_and_reports_stderr(cli, tmp_path):
    result = cli(
        source(
            {"name": "first", "script": "echo start"},
            {"name": "fail", "script": "echo failure >&2; exit 7"},
            {"name": "never", "script": "touch unexpected"},
        ),
        "run",
    )
    assert result.returncode == 1
    assert "exit code 7" in result.stdout
    assert "failure" in result.stdout
    assert not (tmp_path / "unexpected").exists()


@pytest.mark.parametrize("args", [("validate",), ("run", "--dry-run")])
def test_nonexecuting_modes_need_no_secrets(cli, tmp_path, args):
    result = cli(
        source(
            {
                "name": "never",
                "script": "touch unexpected",
                "env": {"TOKEN": "${secrets.DEMO_TOKEN}"},
            },
            secrets={"DEMO_TOKEN": {"from": "env"}},
        ),
        *args,
    )
    assert result.returncode == 0
    assert not (tmp_path / "unexpected").exists()


def test_all_references_validated_before_any_execution(cli, tmp_path):
    result = cli(
        source(
            {"name": "first", "script": "touch unexpected"},
            {"name": "invalid", "script": "true", "env": {"A": "${values.missing}"}},
        ),
        "run",
    )
    assert result.returncode == 2
    assert not (tmp_path / "unexpected").exists()


def test_missing_secret_fails_before_execution(cli, tmp_path):
    result = cli(
        source(
            {"name": "never", "script": "touch unexpected"},
            secrets={"DEMO_TOKEN": {"from": "env"}},
        ),
        "run",
    )
    assert result.returncode == 2
    assert not (tmp_path / "unexpected").exists()


def test_mask_multiline_secret_across_output_writes(cli):
    secret = "prefix-private\nsuffix-private"
    result = cli(
        source(
            {
                "name": "output",
                "env": {"TOKEN": "${secrets.DEMO_TOKEN}"},
                "script": 'python -c \'import os,sys,time; s=os.environ["TOKEN"]; '
                "sys.stdout.write(s[:8]); sys.stdout.flush(); time.sleep(.05); "
                "sys.stderr.write(s[8:])'",
            },
            secrets={"DEMO_TOKEN": {"from": "env"}},
        ),
        "run",
        env={"DEMO_TOKEN": secret},
    )
    assert result.returncode == 0
    assert "***" in result.stdout
    assert "private" not in result.stdout + result.stderr


def test_value_is_data_not_shell_code(cli, tmp_path):
    injection = "$(touch unexpected); `touch unexpected`; 'quoted'"
    result = cli(
        source(
            {
                "name": "safe",
                "env": {"VALUE": "${values.input}"},
                "script": 'printf "%s" "$VALUE"',
            },
            values={"input": injection},
        ),
        "run",
    )
    assert result.returncode == 0
    assert injection in result.stdout
    assert not (tmp_path / "unexpected").exists()


def test_timeout_kills_descendants_and_suppresses_partial_secret(cli, tmp_path):
    result = cli(
        source(
            {
                "name": "timeout",
                "timeout": 0.2,
                "script": "printf private-prefix; (sleep 1; touch unexpected) & wait",
            }
        ),
        "run",
    )
    assert result.returncode == 1
    assert "timeout" in result.stdout
    assert "private-prefix" not in result.stdout
    time.sleep(1.1)
    assert not (tmp_path / "unexpected").exists()


def test_output_limit(cli):
    result = cli(
        source(
            {
                "name": "verbose",
                "script": "python -c 'print(\"x\" * (9 * 1024 * 1024))'",
            }
        ),
        "run",
    )
    assert result.returncode == 1
    assert "8 MiB" in result.stdout
    assert len(result.stdout) < 1000


@pytest.mark.parametrize(
    "cancel_signal,expected_code", [(signal.SIGINT, 130), (signal.SIGTERM, 143)]
)
def test_cancellation_stops_child_processes(tmp_path, cancel_signal, expected_code):
    path = tmp_path / "pipeforge.yml"
    path.write_text(
        source(
            {
                "name": "wait",
                "script": "touch ready; (sleep 1; touch unexpected) & wait",
            }
        )
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "pipeforge", "run", "-f", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not (tmp_path / "ready").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (tmp_path / "ready").exists()
        os.kill(process.pid, cancel_signal)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == expected_code, stdout + stderr
        time.sleep(1.1)
        assert not (tmp_path / "unexpected").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_hello_world_example(cli):
    example = Path(__file__).parents[2] / "examples/hello-world/pipeforge.yml"
    result = cli(example.read_text(), "run")
    assert result.returncode == 0
    assert "Hello PipeForge" in result.stdout
