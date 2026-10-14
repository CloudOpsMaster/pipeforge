import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from pipeforge.config import load_config
from pipeforge.errors import ConfigError
from pipeforge.github import render_github
from pipeforge.graph import critical_path, render_graph, select_stage


@pytest.fixture
def definition():
    return {
        "name": "reels",
        "blocks": {
            "first": {"steps": [{"name": "Repeated", "run": "true", "env": {"A": "block"}}]},
            "second": {"steps": [{"name": "Repeated", "run": "true"}]},
        },
        "jobs": {
            "setup": {"stage": "prepare", "use": "first"},
            "generate": {
                "stage": "build",
                "needs": "setup",
                "steps": [{"run": "true"}],
                "artifacts": ["nested/reel.mp4"],
            },
            "instagram": {
                "stage": "publish",
                "needs": "generate",
                "use": ["first", "second", "first"],
                "env": {"A": "job", "B": "job"},
                "steps": [{"name": "Repeated", "run": "true"}],
            },
            "facebook": {"stage": "publish", "needs": "generate", "use": "second"},
        },
    }


def parse(tmp_path, definition):
    path = tmp_path / "pipeforge.yml"
    path.write_text(yaml.safe_dump(definition, sort_keys=False))
    return load_config(path)


def test_expansion_repetition_and_environment(tmp_path, definition):
    config = parse(tmp_path, definition)
    job = config.jobs[2]
    assert [step.name for step in job.steps] == ["Repeated"] * 4
    assert [step.block for step in job.steps] == ["first", "second", "first", ""]
    assert job.steps[0].env == {"A": "block", "B": "job"}
    assert job.steps[1].env == {"A": "job", "B": "job"}
    assert job.steps[0] is not job.steps[2]
    assert config.blocks[0].steps[0].env == {"A": "block"}
    schema = json.loads((Path(__file__).parents[2] / "schema/pipeforge.schema.json").read_text())
    Draft202012Validator(schema).validate(definition)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["jobs"]["setup"].update(use="missing"),
        lambda d: d["jobs"]["setup"].update(use=[]),
        lambda d: d["jobs"]["setup"].update(use=[True]),
        lambda d: d["jobs"]["setup"].update(stage=""),
        lambda d: d["blocks"]["first"].update(use="second"),
        lambda d: d["blocks"]["first"].update(steps=[]),
        lambda d: d["jobs"]["setup"].pop("use"),
    ],
)
def test_invalid_blocks_and_stages(tmp_path, definition, mutation):
    mutation(definition)
    with pytest.raises(ConfigError):
        parse(tmp_path, definition)


def test_stage_is_metadata_and_selection_is_exact(tmp_path, definition):
    jobs = parse(tmp_path, definition).jobs
    assert [job.name for job in select_stage(jobs, "publish")] == ["instagram", "facebook"]
    assert len(select_stage(jobs, "publish", with_needs=True)) == 4
    with pytest.raises(ConfigError):
        select_stage(jobs, "absent")
    definition["jobs"]["setup"]["stage"] = "publish"
    assert not parse(tmp_path, definition).jobs[0].needs


def test_graphs_and_critical_path(tmp_path, definition):
    jobs = parse(tmp_path, definition).jobs
    mermaid = render_graph(jobs, "mermaid")
    assert "n1 --> n2" in mermaid and "n1 --> n3" in mermaid
    assert "n2 --> n3" not in mermaid
    assert "subgraph s2" in mermaid
    dot = render_graph(jobs, "dot")
    assert "n1 -> n2;" in dot and 'label="publish"' in dot
    text = render_graph(jobs)
    assert "instagram  <-  generate" in text
    assert "facebook  <-  generate" in text
    assert critical_path(jobs, {"setup": 2, "generate": 3, "instagram": 4, "facebook": 5}) == (
        ["setup", "generate", "facebook"],
        10,
    )


def test_renderer_exact_jobs_and_transitive_transport(tmp_path, definition):
    definition["jobs"]["setup"]["artifacts"] = ["setup.txt"]
    definition["secrets"] = {"TOKEN": {"from": "env"}}
    config = parse(tmp_path, definition)
    document = yaml.safe_load(render_github(config, Path("pipelines/reels.yml")))
    assert document["on"] == {"workflow_dispatch": {}}
    native = document["jobs"]["instagram"]
    assert native["needs"] == ["generate"]
    assert native["name"] == "publish / instagram"
    downloads = [
        step["with"] for step in native["steps"] if "download-artifact" in step.get("uses", "")
    ]
    assert [step["name"] for step in downloads] == ["pipeforge-setup", "pipeforge-generate"]
    run = next(step for step in native["steps"] if "run" in step)
    assert run["run"].startswith(".pf/run exec-job instagram -f pipelines/reels.yml")
    assert run["env"] == {"TOKEN": "${{ secrets.TOKEN }}"}
    assert "--artifact-input pipelines/.pipeforge/github/incoming" in run["run"]
    upload = document["jobs"]["generate"]["steps"][-1]
    assert upload["with"]["path"] == "pipelines/.pipeforge/github/outgoing/"
    assert upload["with"]["include-hidden-files"]


def test_renderer_rejects_ambiguous_artifacts_and_expression_injection(tmp_path, definition):
    config = parse(tmp_path, definition)
    with pytest.raises(ConfigError):
        render_github(config, Path("${{ secrets.TOKEN }}.yml"))
    definition["jobs"]["setup"]["artifacts"] = ["nested/reel.mp4"]
    with pytest.raises(ConfigError):
        render_github(parse(tmp_path, definition), Path("pipeforge.yml"))
