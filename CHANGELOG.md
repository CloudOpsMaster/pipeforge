# Changelog

## Unreleased

- Resume runs from atomic job checkpoints with retained, checksum-verified artifacts.
- Verify external publication results before retrying attempted jobs.
- Run independent jobs concurrently with `--max-parallel N`, isolate failed branches,
  and cancel all active process groups together.
- Record retry attempts, reused jobs, verification results and recovery time in reports.

## 0.1.0

First public MVP release. Linux/macOS, Python 3.11+, host execution.

- Bootstrap launcher for a pinned `.pf` submodule; isolated cached runtime.
- Jobs, steps, dependencies, stable DAG ordering and partial job selection.
- Safe YAML loading, strict configuration validation, values/environment secrets and Git metadata.
- Streaming redaction, colored summaries, failure context and private JSON/Markdown run reports.
- GitHub Actions, GitLab and Jenkins wrapper examples; runnable basic/Python examples.
- Editor JSON Schema, packaged with the wheel and available as a release asset.
- Gitleaks history scanning alongside lint, typing, dependency audit, cross-platform tests and packaging smoke tests.
- Automatic version tags and GitHub Releases after successful main CI, using the version in pyproject.toml.

Known limits: no containers, native deployment modules, parallel execution, environment overlays or ForgeAI yet. Pipelines execute trusted shell code with the user's permissions. Application tools and credentials remain the application's responsibility. The launcher needs network access on its first run. The pre-1.0 API may evolve; review changelog entries when updating the pinned version.
