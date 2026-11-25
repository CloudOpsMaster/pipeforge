# Changelog

## 0.3.0

- Add versioned named rules, typed inputs, local includes, parallel calls and dry runs alongside the legacy jobs CLI.
- Record ordered execution events and render offline Studio reports in HTML, SVG and JSON.
- Generate and inspect one-job GitHub Actions, GitLab CI and Jenkins wrappers. Existing GitHub job rendering remains available.
- Draft digest-only GitOps promotion patches and report Kubernetes Deployment rollout health from read-only snapshots.
- Add a restricted standalone native profile, WebGL rendering for Neural 3D and read-only MCP graph inspection.

Portable rule execution and reports use the installed CLI. Native export supports a restricted sequential profile. Provider-wide workflow translation remains outside this release.

## 0.2.1

### Security and maintenance

- Update the pinned development/CI dependency setuptools from 84.0.0 to 84.2.1,
  addressing [GHSA-xmj3-9gf7-h52w](https://github.com/advisories/GHSA-xmj3-9gf7-h52w).
  The advisory concerns a Windows path escape in legacy wheel installation:
  crafted namespace metadata can create a fixed-content `__init__.py` outside
  the intended installation directory. The first patched setuptools version is 84.1.0.
- This dependency fix applies to contributor and CI environments. PipeForge supports
  Linux/macOS host execution; its runtime code and runtime dependencies are unchanged.
- Update commit-pinned GitHub Actions: checkout to 7.0.1, setup-python to 7.1.0,
  upload-artifact to 7.0.2, and download-artifact to 8.0.2. These are CI maintenance
  updates; no additional PipeForge vulnerability fix is claimed.
- Update development tools to mypy 2.4.1, Ruff 0.17.3, and types-PyYAML
  6.0.12.20261030. Update Hatchling to 1.33.0 and its build-system minimum accordingly.

### Documentation

- Translate the development plan and diagrams into English and establish English
  as the project language. Preserve multibyte Unicode secret-masking test coverage.

## 0.2.0

- Add reusable step blocks, ordered job `use`, stage metadata and exact stage reruns.
- Add stage-aware plans, text/Mermaid/DOT graphs and `exec-job`.
- Render native GitHub Actions jobs with dependency edges and declared artifact transport.
- Include block occurrences, stage timing and critical paths in reports; append GitHub summaries automatically.
- Extend the bootstrap launcher to forward the new commands.


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
