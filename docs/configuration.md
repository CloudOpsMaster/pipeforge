# Configuration and command reference

The launcher `./.pf/run` discovers `pipeforge.yml` from the current directory upward, or uses `-f PATH`. It never silently uses the framework's own pipeline when called from an application. Commands run in the configuration file's directory. The installed CLI accepts an explicit subcommand: `pipeforge run`, `pipeforge jobs`, or `pipeforge validate`.

## Jobs and steps

```yaml
name: application
values:
  app: payment-api
  greeting: "Hello ${values.app}"
secrets:
  API_TOKEN:
    from: env
    required: true
jobs:
  test:
    description: Run tests
    steps:
      - run: python -c 'assert 2 + 2 == 4'
  build:
    needs: test
    env:
      GREETING: "${values.greeting}"
      TOKEN: "${secrets.API_TOKEN}"
    steps:
      - name: Build application
        timeout: 30
        run: |
          set -eu
          printf '%s\n' "$GREETING"
```

| Field | Contract |
| --- | --- |
| `name` | Required non-empty string |
| `values` | Nested scalar mappings: strings, finite numbers, booleans |
| `secrets` | Environment-variable names, each with `from: env`, optional `required` (default true) |
| `jobs` | Required non-empty mapping of job IDs to jobs |
| Job ID | Up to 64 letters/digits/underscores/hyphens, starting with a letter/underscore |
| `description` | Optional text shown by `--list` |
| `needs` | A job ID or list of IDs; dependencies must exist and be acyclic |
| Job `env` | String environment values inherited by its steps |
| `steps` | Non-empty list; optional when `use` supplies steps |
| Step `name` | Optional; defaults to `Step 1`, etc.; unique within each declared steps list; may repeat across blocks |
| Step `run` | Required non-empty POSIX shell script |
| Step `env` | Overrides job environment for this step |
| Step `timeout` | Seconds, `0 < timeout <= 86400`, default 300 |

Unknown fields, duplicate YAML keys, aliases, non-string mapping keys, unsafe YAML tags, null/list values, and NUL characters are rejected. Configuration size is limited to 1 MiB, values nesting to 20 levels, reference chains to 50, each resolved string to 65536 characters, and output to 8 MiB per step.

Legacy `pipeline: [{name: ..., script: ...}]` is still accepted as one job named `default`, with a migration notice. `script` is an alias for `run`; never specify both in a step. Never mix top-level `pipeline` and `jobs`.

`image`, `uses`, `with`, `modules`, and `variables` are not implemented and are rejected rather than silently ignored.

## Planning and selecting jobs

```sh
./.pf/run                       # All jobs in stable topological order
./.pf/run build                 # Build and transitive needs, each exactly once
./.pf/run build --with-needs     # Same default behavior
./.pf/run build --no-needs       # Only build
./.pf/run --until build         # Build plus its ancestors
./.pf/run --from build          # Build plus its descendants
./.pf/run --from build --until deploy
./.pf/run --dry-run
./.pf/run --list
./.pf/run validate
```

`--from` skips upstream prerequisites; `--no-needs` skips all prerequisites of the selected job. The execution output states when prerequisites are assumed satisfied. These modes are for deliberate partial reruns and do not restore previous artifacts. `--until` follows dependencies, not YAML line order: unrelated jobs are excluded. Combined `--from/--until` selects the intersection of descendants and ancestors; the end must be reachable from the start. A positional job cannot be combined with these bounds.

The graph is fully validated before selection. Ready jobs use declaration order as a tiebreaker. The default is sequential, fail-fast execution. `--max-parallel N` (1–64) bounds concurrent jobs. With N greater than 1, failed jobs skip their dependants while independent branches continue; the overall run fails. Steps inside each job stay sequential. Jobs share a working directory, so concurrent writers must use distinct output files. Cancellation stops all active process groups. Each step has its own shell; filesystem artifacts persist, but `cd` or environment changes in a step do not carry to another step.

`validate`, `--list`, and `--dry-run` do not execute pipeline commands, require real secret values, or create run reports. A first invocation of the launcher still installs its runtime. Dry-run shows dependencies, RUN/SKIPPED state and a stage-grouped dependency graph. It does not display scripts or resolved environment values and does not verify external tools or artifacts.

## Values, environment and secrets

`${values.app}` resolves a scalar; values can reference other values and Git metadata. Missing/cyclic references fail before execution. Values cannot reference secrets. Keys start with a letter/underscore and can contain letters, digits, underscores, or hyphens; dots express nesting.

References are expanded only in `values` and job/step `env`. Numbers become text; booleans become `true`/`false`. Scripts do not interpolate `${values.*}`, `${secrets.*}`, or `${git.*}`. Use quoted shell variables or `os.environ`; do not `eval` untrusted values.

Current precedence is **step env → job env → inherited process environment**. Required secrets are read from the inherited environment before any step executes. Missing/empty required secrets fail; absent optional secrets become empty. CI credentials are already environment variables, not a separate resolver tier. All declared required secrets must be present for a real run, including a partial run. `.env` is not automatically read.

The launcher prepends its runtime's `bin` directory to PATH so `python` runs the isolated interpreter. It does not install application tools such as Java, Docker or Terraform. Install application dependencies in an explicit setup job locally, or reuse a runtime block in each native CI job. Environment overlays and CLI value overrides are future work; there is no implied generic precedence system for those features yet.

## Git and CI metadata

Supported references: `${git.sha}`, `${git.short_sha}`, `${git.branch}`, `${git.tag}`. Read from the **application checkout**, not `.pf`. `short_sha` has seven characters; `tag` is an exact tag on HEAD. Missing metadata returns a clear error if referenced during a real run; validation and dry-run only check reference names.

The checked-out SHA takes precedence over CI metadata (important for PR merge commits). Recognized CI variables supply a SHA when no checkout metadata is available, and branch labels for detached CI checkouts. Without any metadata the banner shows `no commit`.

Providers: `github` (`GITHUB_ACTIONS=true`), `gitlab` (`GITLAB_CI=true`), `jenkins` (`JENKINS_URL` or `JENKINS_HOME`), otherwise `local`. Provider detection changes metadata only, not jobs or command behavior.

Provider references: [GitHub variables](https://docs.github.com/en/actions/reference/workflows-and-actions/variables), [GitLab variables](https://docs.gitlab.com/ci/variables/predefined_variables/), [Jenkins environment](https://www.jenkins.io/doc/book/pipeline/jenkinsfile/#using-environment-variables).

## Logs, colors and reports

- Default: compact job progress, outcome and totals. Failure: last 12 lines / 1200 characters, step, exit code, duration and log path.
- `-v`: adds step progress and timings.
- `-vv`: streams stdout/stderr (combined in arrival order), still redacted. It never bypasses masking or prints the shell script/env automatically.
- `--color auto|always|never`: terminal detection by default; `NO_COLOR` disables automatic colors. `always` explicitly overrides it. Files contain no ANSI colors.
- Logs stream to disk in all modes. Child tools may buffer output; Python commands get `PYTHONUNBUFFERED=1` unless already configured. Secret lookahead can delay a suffix by the length of the longest declared secret.

Only exact, non-empty **declared** secret values are masked, including multiline and overlapping matches spanning chunks. A possible secret prefix left at end of output is conservatively redacted; this can hide an innocent matching suffix. Transformed, encoded, undeclared or deliberately exfiltrated secrets cannot be reliably masked. Never print credentials intentionally.

Control characters and GitHub workflow command delimiters are neutralized. Normalized logs are not a byte-for-byte reproduction of terminal output. Output before a timeout remains available in redacted logs; the pending suffix is redacted before flushing. Process groups are cleaned up on completion, timeout, output overflow, Ctrl+C and SIGTERM. Deliberately detached processes and SIGKILL are outside that guarantee.

```text
.pipeforge/
  runtime/<source-and-python-fingerprint>/
  cache/pip/
  runs/<UTC-time-and-unique-id>/
    test.log
    build.log
    report.json
    summary.md
  latest -> runs/<most-recently-completed-run>/
```

Run directories and artifact files are private (0700/0600). Each run has a unique directory; `latest` updates atomically when reporting finishes. Runtime/cache directories cannot be symlinks. Keep `.pipeforge/` out of Git and only archive redacted `runs/` reports you intend to share, not runtime/cache.

`report.json` has `schema_version`, pipeline, provider, commit, branch, tag, start time, duration, result and jobs. Jobs include status, duration, log filename, and completed step results with exit code and failure reason. `summary.md` is suitable for GitHub Job Summary or Jenkins artifacts. Reports contain no scripts or resolved environment maps. Validation errors occur before a run is created; cancellation creates a partial report when normal cleanup is possible. Cache and run retention is manual for now; no automatic deletion occurs.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Execution, timeout, output limit or runtime I/O failure |
| 2 | CLI, configuration, secret or bootstrap error |
| 130 | SIGINT / Ctrl+C |
| 143 | SIGTERM |

The shell's original exit code is recorded; the CLI returns `1` for failed commands. Commands use `/bin/sh -c`: use `set -eu` for fail-fast multiline scripts. Portable `/bin/sh` does not guarantee `pipefail`, so check shell-pipeline failures deliberately.

Not implemented yet: containers, `--step`, environment overlays, plugins, remote Git modules, Windows execution, `doctor`, `logs`, `explain`, and ForgeAI.


## Blocks and stages

`blocks` maps reusable names to `description` and a non-empty `steps` list.
A job's `use` accepts one block name or a non-empty list. Expansion is ordered:
all steps from the first block, then the second, then the job's own steps.
Blocks are expanded before DAG validation; they are never scheduler nodes.
Nested `use` inside blocks is not supported.

```yaml
blocks:
  runtime:
    description: Prepare Python
    steps:
      - name: Install dependencies
        run: python -m pip install -r requirements.txt
  checks:
    steps:
      - name: Validate
        run: python validate.py
  final-checks:
    steps:
      - name: Validate
        run: python validate.py

jobs:
  build:
    stage: build
    use: runtime
    steps:
      - run: python build.py
  publish:
    stage: publish
    needs: build
    use: [runtime, checks, final-checks]
    steps:
      - run: python publish.py
```

Identical steps and names in different blocks are allowed. A block may be used
in multiple jobs, or repeated in one `use` list: each occurrence executes.
Reports distinguish occurrences with a one-based step `index` and source `block`.
Block step environment overrides the consuming job environment; each expansion
has its own environment mapping.

`stage` is a non-empty label, defaulting to `default`. It affects presentation
and selection only. It never adds dependencies, barriers or ordering. `needs`
remains the single source of execution dependencies.

```sh
pipeforge run --stage publish --max-parallel 2
pipeforge run --stage publish --with-needs --max-parallel 2
pipeforge exec-job publish
pipeforge plan --stage publish
pipeforge graph
pipeforge graph --format mermaid
pipeforge graph --format dot
```

Stage selection runs only that stage by default; dependencies between selected
jobs are still respected. External prerequisites are assumed satisfied.
`--with-needs` adds transitive prerequisites. Unknown stages are errors.
Stage selection cannot be combined with a positional job, `--from`, `--until`
or `--resume`. `exec-job JOB` is the exact-job equivalent of `run JOB --no-needs`.

Running a stage again creates a fresh run and executes its steps again, including
publication commands. Use existing `--resume RUN_ID` for checkpoint-aware
recovery that reuses completed jobs and preserves idempotency keys.

Stage reruns do not implicitly restore old artifact directories. To reuse saved
inputs from excluded ancestors, provide them explicitly:

```sh
pipeforge run --stage publish --artifact-input .pipeforge/state/RUN_ID/artifacts
```

## Native GitHub Actions

```sh
pipeforge render github -o .github/workflows/pipeline.yml
# In the PipeForge repository itself:
pipeforge render github --launcher ./run -o /tmp/pipeforge-native.yml
```

The default launcher is `.pf/run`; pin that submodule to a revision containing
these commands. The renderer creates a manually triggered workflow with one
Ubuntu job per PipeForge job, stage/job display names, and native `needs` edges.
Checkout initializes submodules, Python is prepared on every runner, and only
`exec-job JOB` executes. Declared secrets map to same-named GitHub secrets.
The renderer never reads secret values or executes pipeline commands.
Commit the generated workflow to use it; regenerate it when dependencies change.
Add application-specific triggers, inputs and environment variables to the
generated workflow as needed; rendering again replaces that file.

Each GitHub job has a fresh runner. Runtime installations and arbitrary workspace
files do not transfer. Put required runtime blocks in every consuming job's
`use`. GitHub displays parallel branches automatically; runner availability
and account concurrency determine when they start. Local `--max-parallel`
does not constrain native GitHub jobs.

Artifact paths keep the existing contract: files relative to
`$PIPEFORGE_ARTIFACTS`, not the checkout. A producer writes there and declares
the same relative file paths in `artifacts`. The renderer exports only those
files, uploads them with their nested paths, downloads artifacts from all
transitive ancestors, and imports them into each consuming run's artifact
directory. Files need not be redeclared by intermediate jobs.
Conflicting ancestor paths are rejected instead of silently overwriting data.
Transport directories are under the configuration directory's
`.pipeforge/github/`. Only declared exports, including declared hidden files,
are uploaded; runtime caches, secrets and checkpoint state are not exported.
Uploads replace the same artifact name on a GitHub job rerun.

`--artifact-input DIR` imports declared files from excluded ancestors before
execution. Missing or escaping paths fail. `--artifact-output DIR` exports
selected jobs' declared outputs after success; the output directory must be
new. These flags cannot be combined with `--resume`. Native GitHub reruns are
fresh executions, not checkpoint resumes; publication adapters should make
repeated publication safe when that behavior is required.

The artifact transport uses GitHub's documented
[upload](https://github.com/actions/upload-artifact) and
[download](https://github.com/actions/download-artifact) actions.
The generated workflow targets GitHub.com, not GitHub Enterprise Server.

## Stage timing and summaries

Reports include stage membership and elapsed stage spans, per-job stage labels,
step/block occurrence metadata, the weighted `critical_path`, its duration,
and `parallel_time_saved`. The critical path is the longest dependency path
using measured job durations for this attempt, excluding skipped/reused jobs.
Saved time is summed job duration minus wall time, bounded below by zero;
stage spans can overlap because stages do not impose barriers.

When `GITHUB_STEP_SUMMARY` is present, PipeForge automatically appends its redacted
summary with stage/job results and a Mermaid graph, including failed runs.
A wrapper should not append the same summary a second time. Each native job
reports its own execution; a single full-DAG run reports the full attempt.
