"""A single output boundary for secret masking and terminal-safe text."""

import os
import re
from collections.abc import Iterable
from typing import TextIO


class Masker:
    def __init__(self, secrets: Iterable[str]):
        values = sorted({value for value in secrets if value}, key=len, reverse=True)
        self.values = values
        self.pattern = (
            re.compile("(?=(" + "|".join(re.escape(value) for value in values) + "))")
            if values
            else None
        )

    def mask(self, text: str) -> str:
        if self.pattern is None:
            return text
        # Lookahead finds overlaps; merge lazily without storing every match.
        result: list[str] = []
        end = 0
        for match in self.pattern.finditer(text):
            start, stop = match.start(), match.start() + len(match.group(1))
            if start >= end:
                result.extend((text[end:start], "***"))
            end = max(end, stop)
        result.append(text[end:])
        return "".join(result)


class StreamingMasker:
    """Hold lookahead so secret matches spanning chunks never reach a sink unmasked."""

    def __init__(self, masker: Masker):
        self.masker = masker
        self.keep = max((len(value) for value in masker.values), default=1) - 1
        self.pending = ""
        self.hidden = bytearray()
        self.in_secret = False

    def feed(self, text: str, *, final: bool = False) -> str:
        self.pending += text
        self.hidden.extend(b"\0" * len(text))
        if self.masker.pattern:
            for match in self.masker.pattern.finditer(self.pending):
                start, end = match.start(), match.start() + len(match.group(1))
                self.hidden[start:end] = b"\1" * (end - start)
        if final:
            # A truncated output can end halfway through a secret; redact that suffix too.
            for secret in self.masker.values:
                for length in range(min(len(secret) - 1, len(self.pending)), 0, -1):
                    if self.pending.endswith(secret[:length]):
                        self.hidden[-length:] = b"\1" * length
                        break
        count = len(self.pending) if final else max(0, len(self.pending) - self.keep)
        parts = []
        for char, hidden in zip(self.pending[:count], self.hidden[:count], strict=True):
            if hidden:
                if not self.in_secret:
                    parts.append("***")
            else:
                parts.append(char)
            self.in_secret = bool(hidden)
        self.pending = self.pending[count:]
        del self.hidden[:count]
        return "".join(parts)


def safe_text(text: str) -> str:
    clean = re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", "?", text)
    return clean.replace("::", ": :").replace("##[", "# #[")


class Logger:
    def __init__(self, stream: TextIO, masker: Masker, *, color: str = "auto", verbosity: int = 0):
        self.stream = stream
        self.masker = masker
        self.color = color == "always" or (
            color == "auto" and stream.isatty() and "NO_COLOR" not in os.environ
        )
        self.verbosity = verbosity

    def write(self, message: str, *, failed: bool = False, style: str = "info") -> None:
        masked = self.masker.mask(message)
        clean = safe_text(masked)
        clean = "\n".join(f"  {line}" for line in clean.split("\n"))
        if self.color:
            code = (
                "31"
                if failed
                else {
                    "info": "36",
                    "success": "32",
                    "muted": "2",
                    "warn": "33",
                    "title": "1;35",
                }.get(style, "36")
            )
            clean = f"\033[{code}m{clean}\033[0m"
        self.stream.write(clean + "\n")
        self.stream.flush()

    def banner(self, name: str, provider: str, commit: str) -> None:
        self.write("╭─ PipeForge ─────────────────────────────────────╮", style="title")
        self.write(name, style="title")
        self.write(f"{provider} • {commit or 'no commit'}", style="muted")
        self.write("╰────────────────────────────────────────────────╯", style="title")
