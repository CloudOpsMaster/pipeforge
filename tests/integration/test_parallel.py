import json
import os
import signal
import subprocess
import sys
import time

import pytest
import yaml


def source(jobs):
    return yaml.safe_dump({"name": "parallel", "jobs": jobs}, sort_keys=False)


def report(path):
    return json.loads((path / ".pipeforge/latest/report.json").read_text())


def job(script, needs=()):
    return {"needs": list(needs), "steps": [{"run": script, "timeout": 5}]}


def test_siblings_overlap_after_dependency_and_fan_in_waits(cli, tmp_path):
    # A sequential implementation deadlocks at the handshake and fails the timeout.
    config = source(
        {
            "generate": job('echo generate >> calls; echo video > "$PIPEFORGE_ARTIFACTS/video"'),
            "instagram": job(
                'test -f "$PIPEFORGE_ARTIFACTS/video" || exit 8; '
                "touch ig_ready; while [ ! -f fb_ready ]; do sleep .02; done; touch ig_done",
                ["generate"],
            ),
            "facebook": job(
                'test -f "$PIPEFORGE_ARTIFACTS/video" || exit 8; '
                "touch fb_ready; while [ ! -f ig_ready ]; do sleep .02; done; touch fb_done",
                ["generate"],
            ),
            "finish": job("test -f ig_done && test -f fb_done", ["instagram", "facebook"]),
        }
    )
    result = cli(config, "run", "--max-parallel", "2")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "calls").read_text().splitlines() == ["generate"]
    assert all(entry["status"] == "success" for entry in report(tmp_path)["jobs"])


def test_failure_isolates_branch_and_resume_reuses_success(cli, tmp_path):
    config = source(
        {
            "generate": job("echo generate >> calls"),
            "instagram": job("echo instagram >> calls; test -f fixed", ["generate"]),
            "facebook": job("sleep .1; echo facebook >> calls", ["generate"]),
            "ig_child": job("echo child >> calls", ["instagram"]),
            "ig_grandchild": job("echo grandchild >> calls", ["ig_child"]),
            "fb_child": job("echo independent >> calls", ["facebook"]),
        }
    )
    assert cli(config, "run", "--max-parallel", "2").returncode == 1
    data = report(tmp_path)
    assert [entry["status"] for entry in data["jobs"]] == [
        "success",
        "failed",
        "success",
        "skipped",
        "skipped",
        "success",
    ]
    (tmp_path / "fixed").touch()
    result = cli(config, "run", "--max-parallel", "2", "--resume", data["resume_id"])
    assert result.returncode == 0, result.stdout
    calls = (tmp_path / "calls").read_text().splitlines()
    assert calls.count("generate") == calls.count("facebook") == calls.count("independent") == 1
    assert calls.count("instagram") == 2
    assert calls.count("child") == calls.count("grandchild") == 1
    assert report(tmp_path)["reused_jobs"] == 3


def test_verification_failure_does_not_cancel_sibling(cli, tmp_path):
    jobs = {"uncertain": job("true"), "other": job("sleep .1; touch complete")}
    jobs["uncertain"]["verify"] = "exit 9"
    result = cli(source(jobs), "run", "--max-parallel", "2")
    assert result.returncode == 1
    assert (tmp_path / "complete").exists()
    assert "uncertain" in report(tmp_path)["jobs"][0]["reason"]


def test_concurrency_limit_and_atomic_checkpoints(cli, tmp_path):
    (tmp_path / "worker.py").write_text("""import fcntl, json, time
from pathlib import Path

def update(delta):
    with open("counter.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = Path("counter.json")
        active, peak = json.loads(path.read_text()) if path.exists() else (0, 0)
        active += delta
        path.write_text(json.dumps([active, max(active, peak)]))
update(1)
time.sleep(.1)
update(-1)
""")
    result = cli(
        source({f"job_{i}": job("python worker.py") for i in range(12)}),
        "run",
        "--max-parallel",
        "3",
    )
    assert result.returncode == 0, result.stdout
    active, peak = json.loads((tmp_path / "counter.json").read_text())
    assert active == 0 and peak == 3
    data = report(tmp_path)
    state = json.loads(
        (tmp_path / ".pipeforge/state" / data["resume_id"] / "state.json").read_text()
    )
    assert all(entry["status"] == "success" for entry in state["jobs"].values())


@pytest.mark.parametrize("number", ["0", "-1", "65", "two"])
def test_invalid_parallel_limit_never_executes(cli, tmp_path, number):
    result = cli(source({"job": job("touch unexpected")}), "run", "--max-parallel", number)
    assert result.returncode == 2
    assert not (tmp_path / "unexpected").exists()


@pytest.mark.parametrize("cancel_signal,code", [(signal.SIGTERM, 143), (signal.SIGINT, 130)])
def test_parallel_cancel_kills_all_process_groups(tmp_path, cancel_signal, code):
    path = tmp_path / "pipeforge.yml"
    path.write_text(
        source(
            {
                name: job(f"touch {name}; (sleep 1; touch orphan_{name}) & wait")
                for name in ("first", "second")
            }
        )
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "pipeforge", "run", "--max-parallel", "2", "-f", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not all((tmp_path / name).exists() for name in ("first", "second")):
            assert time.monotonic() < deadline
            time.sleep(0.02)
        os.kill(process.pid, cancel_signal)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == code, stdout + stderr
        assert report(tmp_path)["result"] == "cancelled"
        assert all(entry["status"] == "cancelled" for entry in report(tmp_path)["jobs"])
        time.sleep(1.1)
        assert not list(tmp_path.glob("orphan_*"))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
