"""Command-line entry point."""

import argparse
import os
import signal
import sys
from pathlib import Path
from types import FrameType

from pipeforge import __version__
from pipeforge.config import load_config
from pipeforge.engine import run
from pipeforge.errors import ConfigError, ExecutionError
from pipeforge.logging import Logger, Masker
from pipeforge.resolver import Resolver


class Terminated(Exception):
    """Allow SIGTERM to unwind the executor's process cleanup."""


def terminate(signum: int, frame: FrameType | None) -> None:
    raise Terminated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pipeforge", description="Run pipelines locally and in CI."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "run"):
        subparser = commands.add_parser(command)
        subparser.add_argument("-f", "--file", type=Path, default=Path("pipeforge.yml"))
        if command == "run":
            subparser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    logger = Logger(sys.stdout, Masker(()))
    previous_handler = signal.signal(signal.SIGTERM, terminate)
    try:
        config = load_config(args.file)
        logger = Logger(sys.stdout, Masker(os.environ.get(name, "") for name in config.secrets))
        if args.command == "validate":
            Resolver(config, {}, validate=True).resolve()
            logger.write("Configuration valid; commands and secret availability were not checked.")
            return 0
        return run(config, os.environ, logger, dry_run=args.dry_run)
    except ConfigError as error:
        logger.write(f"Configuration error: {error}", failed=True)
        return 2
    except ExecutionError as error:
        logger.write(f"Execution error: {error}", failed=True)
        return 1
    except KeyboardInterrupt:
        logger.write("Interrupted; child processes stopped.", failed=True)
        return 130
    except Terminated:
        logger.write("Terminated; child processes stopped.", failed=True)
        return 143
    finally:
        signal.signal(signal.SIGTERM, previous_handler)


if __name__ == "__main__":
    raise SystemExit(main())
