import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "bootstrap", Path(__file__).parents[2] / "scripts/bootstrap.py"
)
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


def test_discovery_uses_application_not_framework(tmp_path):
    (tmp_path / "pipeforge.yml").touch()
    (tmp_path / "src/nested").mkdir(parents=True)
    assert bootstrap.config_path([], tmp_path / "src/nested") == tmp_path / "pipeforge.yml"


def test_fingerprint_changes_with_source_and_runtime_requirements(tmp_path):
    (tmp_path / "src/pipeforge").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text("test")
    (tmp_path / "requirements-runtime.txt").write_text("one")
    source = tmp_path / "src/pipeforge/cli.py"
    source.write_text("one")
    first = bootstrap.fingerprint(tmp_path)
    assert bootstrap.fingerprint(tmp_path) == first
    source.write_text("two")
    second = bootstrap.fingerprint(tmp_path)
    assert second != first
    (tmp_path / "requirements-runtime.txt").write_text("two")
    assert bootstrap.fingerprint(tmp_path) != second


def test_bootstrap_refuses_symlink_runtime(tmp_path):
    (tmp_path / "outside").mkdir()
    (tmp_path / "runtime").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError):
        bootstrap.ensure_directory(tmp_path / "runtime")


@pytest.mark.parametrize("command", ["run", "plan", "graph", "exec-job", "render"])
def test_launcher_forwards_explicit_commands(tmp_path, monkeypatch, command):
    import sys

    (tmp_path / "pipeforge.yml").write_text("name: test")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["run", command])
    monkeypatch.setattr(bootstrap, "prepare_runtime", lambda *args: Path(sys.executable))
    calls = []
    monkeypatch.setattr(bootstrap.os, "execve", lambda *args: calls.append(args))
    assert bootstrap.main() == 0
    assert calls[0][1][3] == command
    assert calls[0][1][4:] == ["--file", str(tmp_path / "pipeforge.yml")]
