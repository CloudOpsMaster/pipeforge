from pathlib import Path

import pytest

from pipeforge.config import Config, Secret, Step
from pipeforge.errors import ConfigError
from pipeforge.resolver import Resolver


def config(values=None, secrets=None, env=None):
    return Config(
        "demo", values or {}, secrets or {}, (Step("one", "true", env or {}),), Path.cwd()
    )


def test_nested_values_and_scalars():
    resolver = Resolver(
        config(
            values={"app": {"port": 8080}, "address": "localhost:${values.app.port}", "tls": False},
            env={"URL": "http://${values.address}", "TLS": "${values.tls}"},
        ),
        {},
    )
    assert resolver.resolve() == ({"URL": "http://localhost:8080", "TLS": "false"},)


@pytest.mark.parametrize(
    "values,env",
    [
        ({"a": "${values.a}"}, {}),
        ({"a": "${values.b}", "b": "${values.a}"}, {}),
        ({"unused": "${values.missing}"}, {}),
        ({}, {"A": "${secrets.undeclared}"}),
        ({}, {"A": "${git.sha}"}),
        ({}, {"A": "${values.unclosed"}),
        ({"nested": {"a": 1}}, {"A": "${values.nested}"}),
        ({"a": "${secrets.TOKEN}"}, {}),
        ({"large": "a" * 65537}, {}),
    ],
)
def test_invalid_references(values, env):
    with pytest.raises(ConfigError):
        Resolver(config(values=values, env=env), {}, validate=True).resolve()


def test_required_secret_not_needed_for_validation():
    cfg = config(secrets={"TOKEN": Secret()}, env={"AUTH": "${secrets.TOKEN}"})
    assert Resolver(cfg, {}, validate=True).resolve() == ({"AUTH": ""},)
    for environ in ({}, {"TOKEN": ""}):
        with pytest.raises(ConfigError, match="required secret"):
            Resolver(cfg, environ)


def test_optional_secret_and_literal_substitution():
    cfg = config(
        secrets={"TOKEN": Secret(), "OPTIONAL": Secret(False)},
        env={"AUTH": "${secrets.TOKEN}", "OTHER": "${secrets.OPTIONAL}"},
    )
    literal = "${values.do_not_evaluate}"
    assert Resolver(cfg, {"TOKEN": literal}).resolve() == ({"AUTH": literal, "OTHER": ""},)
