import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "release", Path(__file__).parents[2] / "scripts/release.py"
)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
VERSION = release.version_info()[0]
TAG = f"v{VERSION}"


def test_version_requires_changelog_and_stable_version(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.1.0"')
    (tmp_path / "CHANGELOG.md").write_text("## 0.1.0\n\nFirst release.\n\n## 0.0.1\nOld")
    assert release.version_info(tmp_path) == ("0.1.0", "First release.\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.0"')
    with pytest.raises(ValueError, match="changelog"):
        release.version_info(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.0.dev0"')
    with pytest.raises(ValueError, match="X.Y.Z"):
        release.version_info(tmp_path)


def test_prepare_checks_versions_and_writes_checksums(tmp_path):
    with pytest.raises(ValueError, match="Distribution"):
        release.prepare(tmp_path)
    for name in [f"pipeforge-{VERSION}-py3-none-any.whl", f"pipeforge-{VERSION}.tar.gz"]:
        (tmp_path / name).write_bytes(b"example")
    release.prepare(tmp_path)
    assert len((tmp_path / "SHA256SUMS").read_text().splitlines()) == 3
    assert json.loads((tmp_path / "pipeforge.schema.json").read_text())["title"]


@pytest.mark.parametrize(
    "event,ref", [("pull_request", "refs/heads/main"), ("push", "refs/heads/dev")]
)
def test_publish_rejects_untrusted_context(monkeypatch, tmp_path, event, ref):
    monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    monkeypatch.setenv("GITHUB_REF", ref)
    with pytest.raises(ValueError, match="restricted"):
        release.publish(tmp_path)


@pytest.mark.parametrize("published,same_commit", [(True, True), (True, False), (False, False)])
def test_existing_tag_is_never_moved(monkeypatch, tmp_path, published, same_commit):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_SHA", "new")
    calls = []

    def command(*args):
        calls.append(args)
        if args == ("git", "rev-parse", "HEAD"):
            return "new"
        if args == ("git", "tag", "--list"):
            return TAG
        if args[:2] == ("gh", "api"):
            return json.dumps([[{"tag_name": TAG, "draft": not published}]])
        if args == ("git", "rev-parse", f"{TAG}^{{commit}}"):
            return "new" if same_commit else "old"
        raise AssertionError(args)

    monkeypatch.setattr(release, "command", command)
    monkeypatch.setattr(release.subprocess, "run", lambda *a, **kw: None)
    if not published:
        with pytest.raises(ValueError, match="older commit"):
            release.publish(tmp_path)
    else:
        release.publish(tmp_path)
    assert not any("POST" in c or "upload" in c or "edit" in c for c in calls)


@pytest.mark.parametrize("draft", [False, True])
def test_publish_creates_or_resumes_draft(monkeypatch, tmp_path, draft):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_SHA", "tested-sha")
    calls = []

    def command(*args):
        calls.append(args)
        if args[:2] == ("git", "rev-parse"):
            return "tested-sha"
        if args[:2] == ("git", "tag"):
            return TAG if draft else ""
        if args[:3] == ("gh", "api", "repos/{owner}/{repo}/releases"):
            return json.dumps([[{"tag_name": TAG, "draft": True}]] if draft else [[]])
        return ""

    monkeypatch.setattr(release, "command", command)
    monkeypatch.setattr(release.subprocess, "run", lambda *a, **kw: calls.append(a[0]))
    release.publish(tmp_path)
    assert any("upload" in c for c in calls)
    assert any("--draft=false" in c for c in calls)
    assert any("POST" in c for c in calls) is not draft
    assert any("create" in c for c in calls) is not draft
    assert next(i for i, c in enumerate(calls) if "SHA256SUMS" in c) < next(
        i for i, c in enumerate(calls) if "upload" in c
    )


def test_older_new_version_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_SHA", "sha")
    answers = iter(["sha", "v99999.0.0", "[[]]"])
    monkeypatch.setattr(release, "command", lambda *args: next(answers))
    with pytest.raises(ValueError, match="increase"):
        release.publish(tmp_path)
