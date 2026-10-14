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
from pipeforge.graph import ordered_jobs, render_graph, select_jobs, select_stage
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
    for command in ("validate", "run", "jobs", "plan", "graph", "exec-job", "render"):
        child = commands.add_parser(command)
        child.add_argument("-f", "--file", type=Path, default=Path("pipeforge.yml"))
        child.add_argument("--color", choices=("auto", "always", "never"), default="auto")
        if command == "graph":
            child.add_argument("--format", choices=("text", "mermaid", "dot"), default="text")
        if command == "render":
            child.add_argument("backend", choices=("github",))
            child.add_argument("-o", "--output", type=Path)
            child.add_argument("--launcher", default=".pf/run")
        if command in ("run", "plan", "exec-job"):
            child.add_argument("job", nargs=None if command == "exec-job" else "?")
            child.add_argument("--stage")
            child.add_argument("--artifact-input", type=Path)
            child.add_argument("--artifact-output", type=Path)
            child.add_argument("--dry-run", action="store_true")
            child.add_argument("--resume", metavar="RUN_ID")
            child.add_argument(
                "--max-parallel",
                type=int,
                default=1,
                metavar="N",
                help="Run up to N independent jobs concurrently (1–64; default 1)",
            )
            child.add_argument("--list", action="store_true", dest="list_jobs")
            child.add_argument("-v", "--verbose", action="count", default=0)
            needs = child.add_mutually_exclusive_group()
            needs.add_argument("--with-needs", dest="with_needs", action="store_true", default=None)
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
        if args.command in ("graph", "render"):
            Resolver(config, {}, validate=True).resolve()
            if args.command == "graph":
                logger.write(
                    render_graph(config.execution_jobs, args.format, redact=logger.masker.mask)
                )
            else:
                from pipeforge.github import render_github

                content = render_github(config, args.file, args.launcher)
                if args.output:
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(content)
                else:
                    sys.stdout.write(content)
            return 0
        if args.command == "jobs" or args.list_jobs:
            Resolver(config, {}, validate=True).resolve()
            logger.write("Available jobs", style="title")
            for job in ordered_jobs(config.execution_jobs):
                logger.write(
                    f"{job.name:16} [{job.stage}] {job.description}  "
                    f"needs: {', '.join(job.needs) or '—'}"
                )
            return 0
        if args.command == "exec-job":
            if args.stage or args.from_job or args.until_job or args.with_needs:
                raise ConfigError("exec-job executes exactly one job; selection flags are invalid.")
            args.with_needs = False
        if args.command == "plan":
            args.dry_run = True
        if args.stage and (args.job or args.from_job or args.until_job):
            raise ConfigError("--stage cannot be combined with job selection.")
        if args.resume and (
            args.dry_run
            or args.job
            or args.stage
            or args.from_job
            or args.until_job
            or args.with_needs is False
            or args.artifact_input
            or args.artifact_output
        ):
            raise ConfigError("--resume cannot be combined with planning or job selection.")
        if not 1 <= args.max_parallel <= 64:
            raise ConfigError("--max-parallel must be between 1 and 64.")
        selected = (
            select_stage(config.execution_jobs, args.stage, with_needs=args.with_needs is True)
            if args.stage
            else select_jobs(
                config.execution_jobs,
                args.job,
                with_needs=args.with_needs is not False,
                from_job=args.from_job,
                until_job=args.until_job,
            )
        )
        return run(
            config,
            os.environ,
            logger,
            dry_run=args.dry_run,
            selected=selected,
            resume=args.resume,
            max_parallel=args.max_parallel,
            artifact_input=args.artifact_input,
            artifact_output=args.artifact_output,
        )
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
