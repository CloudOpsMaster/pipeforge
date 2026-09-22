# Contributing to PipeForge

PipeForge has a working pre-alpha shell runner. See the [configuration reference](docs/configuration.md) for the current contract and the [development plan](docs/development-plan.md) for architecture and future milestones.

## Local setup and checks

Use Python 3.11+ on Linux or macOS:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt -e .

ruff check .
ruff format --check .
mypy
pytest
bandit -r src -q
pip-audit --skip-editable
python -m build
twine check --strict dist/*
python scripts/smoke_package.py dist
```

These are the same checks used by CI. `ruff format .` applies formatting. Tests need no network or service credentials; installation, dependency auditing, isolated builds, and the wheel installation check need network access. The smoke script expects exactly one wheel; keep `dist/` free of older versions when rebuilding.

Run the example with `pipeforge run -f examples/hello-world/pipeforge.yml`. Keep runtime dependencies small. Add unit tests under `tests/unit` and subprocess-based CLI tests under `tests/integration`; use temporary directories and fake credentials.

## Propose a change

For a substantial feature or new integration, open a feature issue first to agree on scope. Small documentation and bug fixes can go straight to a pull request. One PR should solve one problem.

1. Fork `CloudOpsMaster/pipeforge` on GitHub.
2. Clone your fork and create a branch:

   ```sh
   git clone https://github.com/YOUR_USERNAME/pipeforge.git
   cd pipeforge
   git remote add upstream https://github.com/CloudOpsMaster/pipeforge.git
   git switch -c feature/short-description
   ```

3. Make the change. Include tests for new behavior or bug fixes when code exists, and update relevant documentation. Documentation-only changes do not need artificial tests.
4. Review your diff for unrelated files, credentials, and generated artifacts.
5. Commit and push the branch to your fork:

   ```sh
   git add README.md  # Replace with the files you changed.
   git commit -m "docs: clarify project setup"
   git push -u origin feature/short-description
   ```

6. Open a PR against `CloudOpsMaster/pipeforge:main`. Explain the problem, solution, and how you checked it. Draft PRs are welcome for unfinished work.
7. Address feedback on the same branch; pushing more commits updates the PR and reruns CI. A maintainer may first need to approve a fork workflow run.

Collaborators with write access use a branch in the main repository instead of a fork. Do not push directly to `main`.

## Checks and review

CI defines **Lint**, **Tests**, **Security**, and **Build**. **Tests** aggregates all matrix jobs and fails unless every job succeeds. GitHub ruleset enforcement must be enabled separately after the first successful server run. Do not rename these four checks without updating the ruleset.

The maintainer checks correctness, scope, tests, usability, compatibility, and handling of secrets. Automated checks support this review; they do not replace it. The maintainer performs the final squash merge. No release is created automatically from a PR or a merge.

Use clear commit messages such as `feat: add configuration validation` or `fix: preserve failed step exit code`. Conventional prefixes are helpful but do not trigger releases.

## Ready for merge

- The change solves the stated problem and avoids unrelated refactoring.
- Relevant tests cover success and failure behavior; applicable CI checks pass.
- Documentation describes actual behavior and any breaking changes.
- No credentials or sensitive output are included.
- Review discussions are resolved and the maintainer accepts the change.

Be respectful, give actionable feedback, and discuss the code rather than the person. Report suspected vulnerabilities according to [SECURITY.md](SECURITY.md).
