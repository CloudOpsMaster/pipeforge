"""Bounded, supervised POSIX shell execution of explicitly trusted scripts."""

import os
import selectors
import signal

# Executing explicitly trusted pipeline scripts is the runner's purpose.
import subprocess  # nosec B404
import time
from dataclasses import dataclass
from pathlib import Path

from pipeforge.errors import ExecutionError

MAX_OUTPUT_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class Result:
    code: int
    output: str
    duration: float
    reason: str = ""


def execute(script: str, directory: Path, env: dict[str, str], timeout: float) -> Result:
    if os.name != "posix":
        raise ExecutionError("Shell execution currently requires Linux or macOS.")
    started = time.monotonic()
    try:
        # The script is explicit code; resolved configuration stays in env, never shell text.
        process = subprocess.Popen(  # nosec B603
            ["/bin/sh", "-c", script],
            cwd=directory,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except (OSError, ValueError):
        raise ExecutionError(
            "Cannot start shell; check the working directory and environment."
        ) from None
    output = bytearray()
    reason = ""
    code = 1
    try:
        if process.stdout is None:
            raise ExecutionError("Cannot capture command output.")
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map() or process.poll() is None:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    reason = "timeout"
                    break
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    elif len(output) + len(chunk) > MAX_OUTPUT_BYTES:
                        reason = "output exceeds 8 MiB limit"
                        break
                    else:
                        output.extend(chunk)
                if reason:
                    break
            if not reason:
                code = process.wait()
    except OSError:
        raise ExecutionError("Cannot supervise command output.") from None
    finally:
        # Kill the whole process group, including children left by a completed shell.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        if process.stdout is not None:
            process.stdout.close()
    # On truncation, suppress output altogether: it might end with a secret prefix.
    rendered = "" if reason else output.decode("utf-8", errors="replace")
    return Result(code, rendered, time.monotonic() - started, reason)
