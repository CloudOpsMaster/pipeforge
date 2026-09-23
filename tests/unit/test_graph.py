import pytest

from pipeforge.config import Job
from pipeforge.errors import ConfigError
from pipeforge.graph import ordered_jobs, select_jobs


def graph():
    return (
        Job("deploy", (), ("package", "security")),
        Job("test", ()),
        Job("build", (), ("test",)),
        Job("security", (), ("build",)),
        Job("package", (), ("build",)),
        Job("unrelated", ()),
    )


def names(jobs):
    return [job.name for job in jobs]


def test_stable_topological_order():
    assert names(ordered_jobs(graph())) == [
        "test",
        "build",
        "security",
        "package",
        "deploy",
        "unrelated",
    ]


def test_target_includes_transitive_needs_once():
    assert names(select_jobs(graph(), "deploy")) == [
        "test",
        "build",
        "security",
        "package",
        "deploy",
    ]
    assert names(select_jobs(graph(), "deploy", with_needs=False)) == ["deploy"]


def test_graph_slice_not_declaration_slice():
    assert names(select_jobs(graph(), until_job="security")) == ["test", "build", "security"]
    assert names(select_jobs(graph(), from_job="build")) == [
        "build",
        "security",
        "package",
        "deploy",
    ]
    assert names(select_jobs(graph(), from_job="build", until_job="package")) == [
        "build",
        "package",
    ]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target": "absent"},
        {"from_job": "absent"},
        {"with_needs": False},
        {"target": "build", "until_job": "deploy"},
        {"from_job": "package", "until_job": "security"},
    ],
)
def test_invalid_selection(kwargs):
    with pytest.raises(ConfigError):
        select_jobs(graph(), **kwargs)


@pytest.mark.parametrize(
    "jobs",
    [
        (Job("a", (), ("a",)),),
        (Job("a", (), ("b",)),),
        (Job("a", (), ("b",)), Job("b", (), ("a",))),
    ],
)
def test_invalid_dependency_graph(jobs):
    with pytest.raises(ConfigError):
        ordered_jobs(jobs)
