"""A single output boundary for secret masking and terminal-safe text."""

import os
import re
from collections.abc import Iterable
from typing import TextIO


class Masker:
    def __init__(self, secrets: Iterable[str]):
        values = sorted({value for value in secrets if value}, key=len, reverse=True)
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


class Logger:
    def __init__(self, stream: TextIO, masker: Masker):
        self.stream = stream
        self.masker = masker
        self.color = stream.isatty() and "NO_COLOR" not in os.environ

    def write(self, message: str, *, failed: bool = False) -> None:
        masked = self.masker.mask(message)
        clean = re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", "?", masked)
        # GitHub may trim leading whitespace or recognize legacy commands mid-line.
        # Neutralize command delimiters themselves; indentation alone is insufficient.
        clean = clean.replace("::", ": :").replace("##[", "# #[")
        clean = "\n".join(f"  {line}" for line in clean.split("\n"))
        if self.color:
            color = "31" if failed else "36"
            clean = f"\033[{color}m{clean}\033[0m"
        self.stream.write(clean + "\n")
        self.stream.flush()
