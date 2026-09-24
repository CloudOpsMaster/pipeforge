# PipeForge

**Define once. Run anywhere. Debug locally.**

One `pipeforge.yml` owns your jobs and dependencies. Run the same job locally, in Jenkins, GitLab CI, or GitHub Actions. CI supplies the runner and credentials; PipeForge owns the build logic.

**MVP release [`v0.1.0`](https://github.com/CloudOpsMaster/pipeforge/releases/tag/v0.1.0).** Linux/macOS, Python 3.11+. Jobs execute on the host; identical tool versions are still your responsibility. Container execution and native modules are future work.

## Add to an application

Pin the framework to a released version:

```sh
git submodule add https://github.com/CloudOpsMaster/pipeforge.git .pf
git -C .pf checkout v0.1.0
printf '.pipeforge/\n' >> .gitignore
```

Commit `.gitmodules`, `.pf` and `.gitignore` in your application. The submodule is pinned by its recorded commit. Review and commit submodule updates deliberately; do not automatically track the latest development commit in production.

Create `pipeforge.yml`:

```yaml
name: hello
jobs:
  test:
    steps:
      - run: python -c 'assert 2 + 2 == 4'
  build:
    needs: test
    steps:
      - run: python -c 'print("Hello PipeForge")'
```

Then run:

```sh
./.pf/run
./.pf/run build                 # Includes test first
./.pf/run build --no-needs      # Explicitly assume prerequisites are satisfied
./.pf/run --list
./.pf/run --dry-run
./.pf/run --from build
./.pf/run --until build
./.pf/run -v                    # Step progress
./.pf/run -vv --color always    # Live, redacted command output
```

No manual `pip install` or virtualenv activation. The launcher prepares an isolated, cached runtime on first use; Python with `venv` support and package-index access are required. Warm runs reuse the runtime. `.pf/` is the framework; `.pipeforge/` holds application runtime, cache, logs, and reports.

## Output

The terminal uses purple headings, cyan progress, green successes, amber notices, and red failures. Colors are automatic for terminals, can be forced with `--color always`, and disabled with `--color never` or `NO_COLOR` in automatic mode.

```text
  ╭─ PipeForge ─────────────────────────────────────╮
  hello
  local • a83f21c
  ╰────────────────────────────────────────────────╯
  ▶ test
  ✓ test  0.03s
  ▶ build
  ✓ build  0.02s
  ────────────────────────────────────────────────
  ✓ PIPELINE PASSED
  2 jobs • 2 passed • 0 failed • 0 skipped • 0.05s
  Report: .pipeforge/latest/report.json
```

Successful command output stays in per-job logs. Failures automatically show a bounded error excerpt and a full-log path. `-vv` streams command output; all modes write live redacted logs, plus `report.json` and `summary.md`. Reports include job/step outcomes, duration, provider, and Git metadata.

## Try this checkout

```sh
./run --list
./run --dry-run
./run                           # PipeForge checks itself
./run tests                     # Setup, lint, types, tests
./run -vv -f examples/pipelines/basic/pipeforge.yml
```

[Basic](examples/pipelines/basic/pipeforge.yml) and [Python](examples/pipelines/python/pipeforge.yml) examples execute in automated smoke tests. [CI wrappers](examples/ci/README.md) initialize the pinned submodule and invoke the same launcher. Docker, Terraform, Kubernetes, environment overlays, remote modules, and ForgeAI remain on the roadmap.

- [Configuration and command reference](docs/configuration.md)
- [Contributing and exact CI checks](CONTRIBUTING.md)
- [Development plan — українською](docs/development-plan.md)
- [Security policy](SECURITY.md)

CI runs on pushes to `dev`/`main` and on PRs: **Lint**, **Tests**, **Security**, **Build**. Contributors propose PRs; the maintainer reviews and squash-merges. GitHub required-check enforcement is configured separately.

Licensed under [Apache-2.0](LICENSE).

## Editor support and releases

With a YAML language-server extension, add this line to your application's `pipeforge.yml` for completion and structural validation:

```yaml
# yaml-language-server: $schema=.pf/schema/pipeforge.schema.json
```

`pipeforge validate` additionally checks dependency cycles and references. The schema is also bundled in the wheel and attached to each release.

GitHub Releases contain a wheel, source distribution, schema and `SHA256SUMS`. Download the assets and run `shasum -a 256 -c SHA256SUMS` before installing the wheel. PyPI publishing is not configured.

A version-changing PR merged into `main` publishes automatically **after all CI checks pass**. Ordinary merges with the same version do not create another release. See [release procedure](docs/releasing.md) and [changelog](CHANGELOG.md).

### Parallel jobs

```sh
pipeforge run --max-parallel 2
# Repository launcher: ./run --max-parallel 2
pipeforge run --resume <run-id> --max-parallel 2
```

Ready jobs run concurrently up to the specified limit (1–64). A job starts only
after all selected `needs` succeed; its steps remain sequential. With a limit
greater than 1, a failure skips dependent jobs but allows independent branches to
finish. The overall run still fails. The default limit is 1, preserving sequential,
fail-fast execution. Cancellation stops all running process groups.

For `instagram` and `facebook` jobs that both declare `needs: generate`, generation
runs once and the two publishers then run together. Jobs share a working directory
and artifact directory: use separate output/receipt files and treat shared inputs
as read-only. Logs are separate per job; checkpoints serialize concurrent updates.

### Resume a failed run

Every execution prints a **Resume ID**. Continue that same run with:

```sh
pipeforge run --resume <run-id>
# With the repository launcher: ./run --resume <run-id>
```

PipeForge checkpoints each successful job and skips it on resume. Failed,
interrupted, and not-yet-started jobs run in dependency order; a failed job restarts
from its first step. Put generation and each platform's publication in separate
jobs. The original job selection is retained. Resume requires the same pipeline
configuration and Git commit; it cannot be combined with job selection or dry-run.
Keep the same application files and relevant environment when resuming; uncommitted
file changes and inherited environment variables are not fingerprinted.

```yaml
name: social-video
jobs:
  generate:
    artifacts: [video.mp4]
    steps:
      - run: python generate.py --output "$PIPEFORGE_ARTIFACTS/video.mp4"
  instagram:
    needs: generate
    verify: python publish.py instagram --check --key "$PIPEFORGE_JOB_KEY"
    steps:
      - run: python publish.py instagram --video "$PIPEFORGE_ARTIFACTS/video.mp4" --key "$PIPEFORGE_JOB_KEY"
  facebook:
    needs: generate
    verify: python publish.py facebook --check --key "$PIPEFORGE_JOB_KEY"
    steps:
      - run: python publish.py facebook --video "$PIPEFORGE_ARTIFACTS/video.mp4" --key "$PIPEFORGE_JOB_KEY"
```

The scripts above are application-provided adapters. `PIPEFORGE_RUN_ID` stays the
same across attempts. `PIPEFORGE_JOB_KEY` is a stable per-run, per-job key that an
adapter can use for provider idempotency or publication lookup.
`PIPEFORGE_ARTIFACTS` is an absolute directory retained with the run. Declare
immutable output files relative to it in `artifacts`; PipeForge records their
SHA-256 hashes on success and refuses resume if they are missing or changed.
Create nested directories in your script. Files outside this directory are not
retained by the framework.

For jobs with external side effects, supply `verify`. This trusted shell command
runs before retrying an attempted job and after a successful execution:

- Exit **0**: the external result is confirmed; skip retrying the job.
- Exit **3**: the result is definitely absent; retry is allowed. After execution,
  this code means the result was not confirmed and the job fails.
- Any other exit code, timeout, or execution error: the outcome is uncertain;
  stop without retrying publication.

Verification uses the first step's resolved environment and timeout. It must query
authoritative platform state, including pending publications, and must not publish.
Store any lookup IDs needed after a crash in the retained artifact directory.
A local receipt alone cannot rule out a request accepted before the receipt was
written. Use the provider's idempotency support where available. Without a reliable
adapter, a generic shell runner cannot guarantee exactly-once publication; jobs
without `verify` retry normally. Successful jobs are trusted from their checkpoints.

State and artifacts live in `.pipeforge/state/<run-id>/`. On a fresh CI runner,
restore that entire directory before resuming, and persist it even when the job
fails. It can contain application-generated sensitive files: apply appropriate
access and retention settings. File locking prevents concurrent resume on the
same filesystem; for separate CI runners use workflow concurrency to prevent two
copies of the same run from executing. Reports remain in `.pipeforge/runs/`, one
per attempt. `report.json` includes `resume_id`, `attempt`, `reused_jobs`, verification
results, and `recovery_seconds` on success after a recorded failure (wall time from
first failure, including waiting between attempts).
