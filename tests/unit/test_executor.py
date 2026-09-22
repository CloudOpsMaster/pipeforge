import os

import pytest

from pipeforge.errors import ExecutionError
from pipeforge.executor import execute


def test_start_failure_is_user_facing(tmp_path):
    with pytest.raises(ExecutionError, match="Cannot start shell"):
        execute("true", tmp_path / "absent", dict(os.environ), 1)


def test_non_utf8_output_is_decoded_safely(tmp_path):
    result = execute("printf '\\377'", tmp_path, dict(os.environ), 1)
    assert result.code == 0
    assert result.output == "\ufffd"


def test_closed_stdout_does_not_bypass_timeout(tmp_path):
    result = execute("exec 1>&- 2>&-; sleep 5", tmp_path, dict(os.environ), 0.1)
    assert result.reason == "timeout"
    assert result.duration < 3
