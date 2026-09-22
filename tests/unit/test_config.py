import pytest

from pipeforge.config import MAX_CONFIG_BYTES, load_config
from pipeforge.errors import ConfigError

VALID = "name: demo\npipeline:\n  - name: Hello\n    script: echo hello\n"


def test_defaults_and_config_directory(tmp_path):
    path = tmp_path / "pipeforge.yml"
    path.write_text(VALID)
    config = load_config(path)
    assert config.name == "demo"
    assert config.pipeline[0].timeout == 300
    assert config.directory == tmp_path


@pytest.mark.parametrize(
    "source",
    [
        "",
        "[]",
        "name: demo\npipeline: []",
        VALID + "unknown: true\n",
        VALID + "name: duplicate\n",
        VALID.replace("script: echo hello", "script: ''"),
        VALID.replace("script: echo hello", "use: docker.build"),
        VALID.replace("script: echo hello", 'script: "${secrets.TOKEN}"'),
        VALID.replace("script: echo hello", "script: echo hello\n    timeout: true"),
        VALID.replace("script: echo hello", "script: echo hello\n    timeout: 0"),
        VALID.replace("script: echo hello", "script: echo hello\n    timeout: .inf"),
        VALID.replace("script: echo hello", "script: echo hello\n    env: {BAD-KEY: ok}"),
        VALID.replace("script: echo hello", "script: echo hello\n    env: {PORT: 80}"),
        VALID + "values: {bad.key: nope}\n",
        VALID + "values: {items: [a, b]}\n",
        VALID + "values: {date: 2025-01-01}\n",
        VALID + "values: {number: .nan}\n",
        VALID + "secrets: {TOKEN: {from: file}}\n",
        VALID + "secrets: {TOKEN: {from: env, required: 'yes'}}\n",
        VALID + "values: {a: &a value, b: *a}\n",
        VALID + "values: {a: !!python/object:os.system {}}\n",
        VALID + "values: {a: !!int invalid}\n",
        VALID + "values: {1: bad}\n",
        VALID + "  - name: Hello\n    script: echo duplicate\n",
    ],
)
def test_invalid_config_is_rejected(tmp_path, source):
    path = tmp_path / "pipeforge.yml"
    path.write_text(source)
    with pytest.raises(ConfigError):
        load_config(path)


def test_parser_error_does_not_echo_input(tmp_path):
    path = tmp_path / "pipeforge.yml"
    path.write_text("name: [very-private-token\n")
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert "very-private-token" not in str(caught.value)


def test_config_size_limit(tmp_path):
    path = tmp_path / "pipeforge.yml"
    path.write_bytes(b"x" * (MAX_CONFIG_BYTES + 1))
    with pytest.raises(ConfigError, match="1 MiB"):
        load_config(path)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="Cannot read"):
        load_config(tmp_path / "absent.yml")
