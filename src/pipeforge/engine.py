"""Resolve all inputs before executing a sequential, fail-fast pipeline."""

from collections.abc import Mapping

from pipeforge.config import Config
from pipeforge.executor import execute
from pipeforge.logging import Logger
from pipeforge.resolver import Resolver


def run(
    config: Config, environ: Mapping[str, str], logger: Logger, *, dry_run: bool = False
) -> int:
    resolver = Resolver(config, environ, validate=dry_run)
    environments = resolver.resolve()
    logger.write(f"PipeForge | {config.name}")
    for index, (step, resolved_env) in enumerate(
        zip(config.pipeline, environments, strict=True), 1
    ):
        logger.write(f"[{index}/{len(config.pipeline)}] {step.name}")
        if dry_run:
            logger.write(f"DRY RUN | timeout={step.timeout:g}s | command and env values hidden")
            continue
        env = dict(environ)
        # Missing optional secrets become empty; explicit step env wins.
        env.update(resolver.secrets)
        env.update(resolved_env)
        result = execute(step.script, config.directory, env, step.timeout)
        if result.output:
            logger.write(result.output.rstrip("\n"))
        if result.code != 0 or result.reason:
            detail = result.reason or f"exit code {result.code}"
            logger.write(f"FAILED | {detail} | {result.duration:.2f}s", failed=True)
            return 1
        logger.write(f"OK | {result.duration:.2f}s")
    logger.write("Dry run complete; no commands executed." if dry_run else "Pipeline completed.")
    return 0
