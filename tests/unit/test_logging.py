from io import StringIO

import pytest

from pipeforge.logging import Logger, Masker


@pytest.mark.parametrize(
    "secrets,text,expected",
    [
        ([""], "unchanged", "unchanged"),
        (["token"], "token/token", "***/***"),
        (["abc", "abcdef"], "abcdef", "***"),
        (["abc", "bcd"], "abcd", "***"),
        (["aa"], "aaaaa", "***"),
        (["one\ntwo"], "before one\ntwo after", "before *** after"),
        (["token"], "no match", "no match"),
    ],
)
def test_masking(secrets, text, expected):
    assert Masker(secrets).mask(text) == expected


def test_logger_masks_and_neutralizes_terminal_and_workflow_commands():
    stream = StringIO()
    Logger(stream, Masker(["private"])).write(
        "private\n::error::hello\n\x1b[31mtext\r\n  ::stop-commands::token\ntext ##[error]"
    )
    output = stream.getvalue()
    assert "private" not in output
    assert "\x1b" not in output
    assert "\r" not in output
    assert "::" not in output
    assert "##[" not in output
    assert all(line.startswith("  ") for line in output.splitlines())
