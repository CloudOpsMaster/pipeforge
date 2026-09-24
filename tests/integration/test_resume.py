import json

import pytest
import yaml


def source(verify=""):
    jobs = {
        "generate": {
            "artifacts": ["video"],
            "steps": [{"run": 'echo generate >> calls; echo video > "$PIPEFORGE_ARTIFACTS/video"'}],
        },
        "instagram": {
            "needs": "generate",
            "steps": [{"run": "echo instagram >> calls"}],
        },
        "facebook": {
            "needs": "instagram",
            "steps": [
                {"run": 'cat "$PIPEFORGE_ARTIFACTS/video"; echo facebook >> calls; test -f ready'}
            ],
        },
    }
    if verify:
        jobs["facebook"]["verify"] = verify
    return yaml.safe_dump({"name": "resume-demo", "jobs": jobs}, sort_keys=False)


def report(path):
    return json.loads((path / ".pipeforge/latest/report.json").read_text())


def test_resume_only_failed_job_and_keep_artifact(cli, tmp_path):
    config = source()
    assert cli(config, "run").returncode == 1
    run_id = report(tmp_path)["resume_id"]
    (tmp_path / "ready").touch()
    assert cli(config, "run", "--resume", run_id).returncode == 0
    assert (tmp_path / "calls").read_text().splitlines() == [
        "generate",
        "instagram",
        "facebook",
        "facebook",
    ]
    assert report(tmp_path)["jobs"][0]["reused"]
    assert report(tmp_path)["attempt"] == 2
    assert report(tmp_path)["reused_jobs"] == 2
    assert report(tmp_path)["recovery_seconds"] >= 0
    assert cli(config, "run", "--resume", run_id).returncode == 0
    assert len((tmp_path / "calls").read_text().splitlines()) == 4


def test_reconcile_publication_after_error(cli, tmp_path):
    config = source("test -f published && exit 0; exit 3")
    assert cli(config, "run").returncode == 1
    run_id = report(tmp_path)["resume_id"]
    (tmp_path / "published").touch()
    result = cli(config, "run", "--resume", run_id)
    assert result.returncode == 0, result.stdout
    assert report(tmp_path)["jobs"][-1]["reconciled"]
    assert (tmp_path / "calls").read_text().count("facebook") == 1


def test_uncertain_result_refuses_retry(cli, tmp_path):
    config = source("exit 1")
    assert cli(config, "run").returncode == 1
    run_id = report(tmp_path)["resume_id"]
    result = cli(config, "run", "--resume", run_id)
    assert result.returncode == 1
    assert "uncertain" in result.stdout
    assert (tmp_path / "calls").read_text().count("facebook") == 1


@pytest.mark.parametrize("damage", ["missing", "changed", "config"])
def test_invalid_resume_never_regenerates(cli, tmp_path, damage):
    config = source()
    assert cli(config, "run").returncode == 1
    run_id = report(tmp_path)["resume_id"]
    artifact = tmp_path / ".pipeforge/state" / run_id / "artifacts/video"
    if damage == "missing":
        artifact.unlink()
    elif damage == "changed":
        artifact.write_text("changed")
    else:
        config = config.replace("resume-demo", "changed")
    assert cli(config, "run", "--resume", run_id).returncode == 1
    assert (tmp_path / "calls").read_text().splitlines() == ["generate", "instagram", "facebook"]


def test_running_checkpoint_reconciled(cli, tmp_path):
    config = source("exit 0")
    assert cli(config, "run").returncode == 1
    run_id = report(tmp_path)["resume_id"]
    path = tmp_path / ".pipeforge/state" / run_id / "state.json"
    data = json.loads(path.read_text())
    data["jobs"]["facebook"]["status"] = "running"
    path.write_text(json.dumps(data))
    assert cli(config, "run", "--resume", run_id).returncode == 0
    assert (tmp_path / "calls").read_text().count("facebook") == 1


def test_absent_result_retries_then_confirms(cli, tmp_path):
    config = source("test -f published && exit 0; exit 3")
    document = yaml.safe_load(config)
    document["jobs"]["facebook"]["steps"][0]["run"] += " && touch published"
    config = yaml.safe_dump(document, sort_keys=False)
    assert cli(config, "run").returncode == 1
    run_id = report(tmp_path)["resume_id"]
    (tmp_path / "ready").touch()
    result = cli(config, "run", "--resume", run_id)
    assert result.returncode == 0, result.stdout
    assert (tmp_path / "calls").read_text().count("facebook") == 2
    assert [v["exit_code"] for v in report(tmp_path)["jobs"][-1]["verifications"]] == [3, 0]


def test_lock_prevents_concurrent_resume(cli, tmp_path):
    import fcntl

    config = source()
    assert cli(config, "run").returncode == 1
    run_id = report(tmp_path)["resume_id"]
    with (tmp_path / ".pipeforge/state" / run_id / "lock").open() as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = cli(config, "run", "--resume", run_id)
        assert result.returncode == 1
        assert "already executing" in result.stdout
    assert (tmp_path / "calls").read_text().count("facebook") == 1


def test_selected_run_restores_original_selection(cli, tmp_path):
    config = source()
    assert cli(config, "run", "generate").returncode == 0
    run_id = report(tmp_path)["resume_id"]
    assert cli(config, "run", "--resume", run_id).returncode == 0
    assert (tmp_path / "calls").read_text().splitlines() == ["generate"]


@pytest.mark.parametrize("artifact", ["../escape", "/absolute", ".", ""])
def test_reject_artifact_escape(cli, tmp_path, artifact):
    config = yaml.safe_load(source())
    config["jobs"]["generate"]["artifacts"] = [artifact]
    assert cli(yaml.safe_dump(config), "validate").returncode == 2


def test_missing_declared_output_fails_producer(cli, tmp_path):
    config = source().replace("artifacts:\n    - video", "artifacts:\n    - absent")
    result = cli(config, "run")
    assert result.returncode == 1
    assert "artifact is missing" in result.stdout
    assert (tmp_path / "calls").read_text().splitlines() == ["generate"]


def test_verification_after_success_requires_confirmation(cli, tmp_path):
    (tmp_path / "ready").touch()
    result = cli(source("exit 3"), "run")
    assert result.returncode == 1
    assert "not confirmed" in result.stdout
    assert report(tmp_path)["jobs"][-1]["status"] == "failed"


def test_job_key_stable_across_attempts(cli, tmp_path):
    config = source().replace("echo facebook >> calls", 'echo "$PIPEFORGE_JOB_KEY" >> keys')
    assert cli(config, "run").returncode == 1
    run_id = report(tmp_path)["resume_id"]
    (tmp_path / "ready").touch()
    assert cli(config, "run", "--resume", run_id).returncode == 0
    keys = (tmp_path / "keys").read_text().splitlines()
    assert len(keys) == 2
    assert len(keys[0]) == 64
    assert keys[0] == keys[1]
