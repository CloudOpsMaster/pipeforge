"""Command-line entry point for the installed tool and the bootstrap launcher."""

import argparse
import os
import signal
import sys
from pathlib import Path
from types import FrameType

from pipeforge import __version__
from pipeforge.config import load_config
from pipeforge.engine import run
from pipeforge.errors import ConfigError, ExecutionError, Terminated
from pipeforge.graph import ordered_jobs, select_jobs
from pipeforge.logging import Logger, Masker
from pipeforge.resolver import Resolver


def terminate(signum: int, frame: FrameType | None) -> None:
    raise Terminated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pipeforge", description="Define once. Run anywhere. Debug locally."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "run", "jobs"):
        child = commands.add_parser(command)
        child.add_argument("-f", "--file", type=Path, default=Path("pipeforge.yml"))
        child.add_argument("--color", choices=("auto", "always", "never"), default="auto")
        if command == "run":
            child.add_argument("job", nargs="?")
            child.add_argument("--dry-run", action="store_true")
            child.add_argument("--list", action="store_true", dest="list_jobs")
            child.add_argument("-v", "--verbose", action="count", default=0)
            needs = child.add_mutually_exclusive_group()
            needs.add_argument("--with-needs", dest="with_needs", action="store_true", default=True)
            needs.add_argument("--no-needs", dest="with_needs", action="store_false")
            child.add_argument("--from", dest="from_job")
            child.add_argument("--until", dest="until_job")
    args = parser.parse_args(argv)
    logger = Logger(sys.stdout, Masker(()), color=args.color)
    previous_handler = signal.signal(signal.SIGTERM, terminate)
    try:
        config = load_config(args.file)
        logger = Logger(
            sys.stdout,
            Masker(os.environ.get(name, "") for name in config.secrets),
            color=args.color,
            verbosity=getattr(args, "verbose", 0),
        )
        if args.command == "validate":
            Resolver(config, {}, validate=True).resolve()
            logger.write(
                "Configuration valid; commands and secret availability were not checked.",
                style="success",
            )
            return 0
        if args.command == "jobs" or args.list_jobs:
            Resolver(config, {}, validate=True).resolve()
            logger.write("Available jobs", style="title")
            for job in ordered_jobs(config.execution_jobs):
                logger.write(
                    f"{job.name:16} {job.description}  needs: {', '.join(job.needs) or '—'}"
                )
            return 0
        selected = select_jobs(
            config.execution_jobs,
            args.job,
            with_needs=args.with_needs,
            from_job=args.from_job,
            until_job=args.until_job,
        )
        return run(config, os.environ, logger, dry_run=args.dry_run, selected=selected)
    except ConfigError as error:
        logger.write(f"Configuration error: {error}", failed=True)
        return 2
    except ExecutionError as error:
        logger.write(f"Execution error: {error}", failed=True)
        return 1
    except OSError:
        logger.write(
            "Cannot read or write runtime files; check .pipeforge permissions and free space.",
            failed=True,
        )
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
