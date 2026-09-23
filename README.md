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
