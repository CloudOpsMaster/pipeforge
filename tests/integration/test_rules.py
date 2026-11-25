import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from jsonschema import validate

from pipeforge.cli import main
from pipeforge.rules import load_rules

SCHEMA = Path(__file__).resolve().parents[2] / "schema/event.schema.json"


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    return tmp_path


def write(project, source, name="pipeforge.yml"):
    path = project / name
    path.write_text(source)
    return path


def events(path):
    schema = json.loads(SCHEMA.read_text())
    result = [json.loads(line) for line in path.read_text().splitlines()]
    for event in result:
        validate(event, schema)
    assert list(range(len(result))) == [event["sequence"] for event in result]
    return result


def test_discovery_and_explicit_selection(project, monkeypatch, capsys):
    write(project, "version: 1\naws: {s3: {sync: {argv: [printf, hi]}}}\n")
    subdir = project / "nested"
    subdir.mkdir()
    monkeypatch.chdir(subdir)
    assert main(["aws.s3.sync"]) == 0
    assert "hi" in capsys.readouterr().out
    alternate = write(project, "version: 1\nother: {run: ':'}\n", "pipeforge.yaml")
    assert main(["list"]) == 2
    assert main(["validate"]) == 2
    assert main(["--file", str(alternate), "other"]) == 0


@pytest.mark.parametrize(
    "source",
    [
        "version: 1\nlist: {run: ':'}\n",
        "version: 1\na: {run: ':', run: 'false'}\n",
        "version: 1\na: {run: ':', argv: [true]}\n",
        "version: 1\na: {steps: [{use: missing}]}\n",
        "version: 1\na: {steps: [{use: b}]}\nb: {steps: [{use: a}]}\n",
        "version: 1\na: &a {run: ':'}\nb: *a\n",
        "version: 1\na: [bad\n",
        "version: 1\ninclude: [../elsewhere.yml]\na: {run: ':'}\n",
    ],
)
def test_invalid_whole_config(project, source):
    write(project, source)
    assert main(["validate"]) == 2


def test_include_paths_and_source(project, capsys):
    folder = project / "rules"
    folder.mkdir()
    write(project, "version: 1\ninclude: [rules/child.yml]\n")
    write(folder, "a: {script: echo.sh}\n", "child.yml")
    write(folder, "printf 'from script'\n", "echo.sh")
    assert main(["a"]) == 0
    assert "from script" in capsys.readouterr().out
    assert load_rules(project / "pipeforge.yml").rules["a"].source.file == "rules/child.yml"
    write(folder, "include: [child.yml]\n", "child.yml")
    assert main(["validate"]) == 2
    write(project, "version: 1\ninclude: [rules/child.yml, rules/child.yml]\n")
    write(folder, "a: {script: echo.sh}\n", "child.yml")
    assert main(["validate"]) == 2
    link = project / "link.yml"
    link.symlink_to("/dev/null")
    write(project, "version: 1\ninclude: [link.yml]\n")
    assert main(["validate"]) == 2


def test_parallel_status_masking_and_dry_run(project, monkeypatch, capsys):
    monkeypatch.setenv("DEMO_SECRET", "test-redaction-value")
    write(
        project,
        """version: 1
secrets: [DEMO_SECRET]
test:
  echo:
    inputs: {text: {type: string, default: harmless}}
    argv: [printf, '%s', '${inputs.text}']
  fail: {run: 'printf "%s" "$DEMO_SECRET"; exit 7'}
  started: {run: 'sleep 0.05; touch started'}
  blocked: {run: 'touch blocked'}
  graph:
    steps:
      - use: test.echo
      - parallel: [{use: test.fail}, {use: test.started}, {use: test.echo}]
      - use: test.blocked
""",
    )
    journal = project / "events.jsonl"
    assert main(["test.graph", "--dry-run", "--events", str(journal)]) == 0
    assert not journal.exists()
    assert not (project / "started").exists()
    assert main(["test.graph", "--events", str(journal)]) == 7
    assert (project / "started").exists()
    assert not (project / "blocked").exists()
    recorded = events(journal)
    assert "test-redaction-value" not in journal.read_text() + capsys.readouterr().out
    assert any(event.get("message") == "***" for event in recorded)
    assert any(
        event["rule"] == "test.blocked" and event["status"] == "blocked" for event in recorded
    )
    assert len({event["node_id"] for event in recorded if event["rule"] == "test.echo"}) == 2
    assert main(["test.echo", "--input", "text=$(touch unwanted); <tag>"]) == 0
    assert "$(touch unwanted); <tag>" in capsys.readouterr().out
    assert not (project / "unwanted").exists()


def test_required_types_and_timeout(project):
    write(
        project,
        """version: 1
typed:
  inputs: {enabled: {type: boolean}, count: {type: integer, default: 2}}
  argv: [printf, '%s', '${inputs.count}', '${inputs.enabled}']
slow: {run: 'sleep 10', timeout: 1}
""",
    )
    assert main(["typed"]) == 2
    assert main(["typed", "--input", "enabled=1"]) == 2
    assert main(["typed", "--input", "enabled=true", "--input", "count=true"]) == 2
    assert main(["typed", "--input", "enabled=true"]) == 0
    assert main(["slow"]) == 124


def test_cancellation(project):
    write(
        project,
        """version: 1
slow: {run: 'touch started; sleep 8; touch unexpected'}
both:
  steps:
    - parallel: [{use: slow}, {use: slow}]
""",
    )
    journal = project / "events.jsonl"
    root = str(Path(__file__).resolve().parents[2] / "src")
    with subprocess.Popen(
        [sys.executable, "-m", "pipeforge", "both", "--events", str(journal)],
        env=os.environ | {"PYTHONPATH": root},
        stdout=subprocess.PIPE,
    ) as process:
        try:
            deadline = time.monotonic() + 5
            while not (project / "started").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert (project / "started").exists()
            process.send_signal(signal.SIGTERM)
            assert process.communicate(timeout=3)[0]
            assert process.returncode == 143
        finally:
            if process.poll() is None:
                process.kill()
    assert not (project / "unexpected").exists()
    assert any(event["status"] == "cancelled" for event in events(journal))


def test_reports_and_wrapper_exports(project, capsys):
    write(
        project,
        """version: 1
safe: {argv: [printf, '%s', '</script><script>alert(1)</script>']}
""",
    )
    journal = project / "events.jsonl"
    assert main(["safe", "--events", str(journal)]) == 0
    capsys.readouterr()
    for fmt in ("html", "svg", "json"):
        output = project / f"report.{fmt}"
        assert main(["studio", str(journal), "--format", fmt, "-o", str(output)]) == 0
        assert "</script><script>alert" not in output.read_text() or fmt == "json"
    for backend in ("github", "gitlab", "jenkins"):
        assert main(["export", backend, "safe"]) == 0
        first = capsys.readouterr().out
        assert main(["export", backend, "safe"]) == 0
        assert capsys.readouterr().out == first
        assert "pipeforge safe --file pipeforge.yml" in first
        if backend != "jenkins":
            assert isinstance(yaml.safe_load(first), dict)
    assert main(["export", "github", "safe", "--mode", "standalone"]) == 2
    assert "pipeforge.yml:2" in capsys.readouterr().out


def test_wrapper_round_trip_and_rejection(project, capsys):
    write(project, "version: 1\nsafe: {run: ':'}\n")
    for provider in ("github", "gitlab", "jenkins"):
        wrapper = project / provider
        assert main(["export", provider, "safe", "-o", str(wrapper)]) == 0
        assert main(["import", provider, str(wrapper)]) == 0
        assert json.loads(capsys.readouterr().out) == {
            "provider": provider,
            "mode": "wrapper",
            "rule": "safe",
        }
        wrapper.write_text(wrapper.read_text().replace("pipeforge safe", "pipeforge unknown"))
        assert main(["import", provider, str(wrapper)]) == 2
        assert f"{provider}:1" in capsys.readouterr().out


def test_promotion_patch_preserves_manifest_and_reports_drift(project, capsys):
    source = """apiVersion: apps/v1
kind: Deployment
spec:
  template:
    spec:
      containers:
        # retain operator comments
        - name: app
          image: 'registry.example:8443/apps/app:old'
        - name: other
          image: example/sidecar
"""
    manifest = write(project, source, "deployment.yml")
    patch = project / "promo.patch"
    digest = "sha256:" + "a" * 64
    assert (
        main(
            [
                "promote",
                str(manifest),
                "--container",
                "app",
                "--digest",
                digest,
                "--observed-digest",
                "sha256:" + "b" * 64,
                "-o",
                str(patch),
            ]
        )
        == 0
    )
    assert manifest.read_text() == source
    assert "Drift yes" in capsys.readouterr().err
    subprocess.run(["git", "apply", "--unidiff-zero", str(patch)], check=True, cwd=project)
    assert manifest.read_text() == source.replace(
        "registry.example:8443/apps/app:old", "registry.example:8443/apps/app@" + digest
    )


def test_pinned_dependency_and_forwarded_input(project, capsys):
    import hashlib
    import shutil

    printf = shutil.which("printf")
    assert printf is not None
    digest = hashlib.sha256(Path(printf).read_bytes()).hexdigest()
    config = """version: 1
child:
  tools: {printf: 'sha256:DIGEST'}
  inputs: {quantity: {type: integer}}
  argv: [printf, '%s', '${inputs.quantity}']
parent:
  inputs: {count: {type: integer}}
  steps:
    - use: child
      with: {quantity: '${inputs.count}'}
"""
    write(project, config.replace("DIGEST", digest))
    assert main(["parent", "--input", "count=123"]) == 0
    assert "123" in capsys.readouterr().out
    write(project, config.replace("DIGEST", "0" * 64))
    assert main(["parent", "--input", "count=123", "--dry-run"]) == 0
    assert main(["parent", "--input", "count=123"]) == 2
    assert "SHA-256 mismatch" in capsys.readouterr().out
    write(
        project,
        config.replace("DIGEST", digest).replace(
            "{count: {type: integer}}", "{count: {type: string}}"
        ),
    )
    assert main(["validate"]) == 2


@pytest.mark.parametrize(
    ("provider", "fixture"),
    [("github", "github.yml"), ("gitlab", "gitlab.yml"), ("jenkins", "Jenkinsfile")],
)
def test_portable_example_and_golden_workflow(provider, fixture, monkeypatch, capsys):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.chdir(root)
    example = "examples/rules/portable.yml"
    schema = json.loads((root / "schema/rules.schema.json").read_text())
    validate(yaml.safe_load((root / example).read_text()), schema)
    assert main(["export", provider, "demo.report", "-f", example]) == 0
    expected = root / "tests/fixtures/portable-ci" / fixture
    assert capsys.readouterr().out == expected.read_text()
    assert main(["import", provider, str(expected), "-f", example]) == 0
    assert json.loads(capsys.readouterr().out)["rule"] == "demo.report"
