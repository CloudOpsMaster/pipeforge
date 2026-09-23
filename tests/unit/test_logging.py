from io import StringIO

import pytest

from pipeforge.logging import Logger, Masker, StreamingMasker


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


@pytest.mark.parametrize(
    "values,text",
    [
        (["secret"], "before secret after"),
        (["abc", "cde"], "abcde"),
        (["aa"], "aaaaa"),
        (["one\ntwo"], "one\ntwo complete!"),
        (["🔐пароль"], "hello 🔐пароль done"),
    ],
)
def test_every_stream_chunk_boundary_masks_secrets(values, text):
    expected = Masker(values).mask(text)
    for split in range(len(text) + 1):
        redactor = StreamingMasker(Masker(values))
        actual = (
            redactor.feed(text[:split])
            + redactor.feed(text[split:])
            + redactor.feed("", final=True)
        )
        assert actual == expected


def test_stream_hides_truncated_secret_prefix():
    redactor = StreamingMasker(Masker(["long-secret-value"]))
    assert redactor.feed("safe long-se") + redactor.feed("", final=True) == "safe ***"


def test_no_color_and_forced_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    stream = StringIO()
    stream.isatty = lambda: True
    Logger(stream, Masker(())).write("message")
    assert "\x1b" not in stream.getvalue()
    Logger(stream, Masker(()), color="always").write("passed", style="success")
    assert "\x1b[32m" in stream.getvalue()
