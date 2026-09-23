import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).parents[2]
SCHEMA = json.loads((ROOT / "schema/pipeforge.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA)


def test_schema_and_all_pipeline_examples():
    Draft202012Validator.check_schema(SCHEMA)
    for path in [ROOT / "pipeforge.yml", *ROOT.glob("examples/pipelines/**/pipeforge.yml")]:
        VALIDATOR.validate(yaml.safe_load(path.read_text()))


@pytest.mark.parametrize(
    "step",
    [
        {"uses": "docker/build"},
        {"run": "echo ok", "script": "echo bad"},
        {"run": "echo ok", "timeout": 0},
        {"run": "echo ok", "env": {"PORT": 123}},
        {"run": "echo ${secrets.TOKEN}"},
    ],
)
def test_schema_rejects_unsupported_or_unsafe_steps(step):
    assert list(VALIDATOR.iter_errors({"name": "test", "jobs": {"test": {"steps": [step]}}}))
