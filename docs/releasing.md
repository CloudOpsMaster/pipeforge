# Releasing PipeForge

`dev` is the integration branch; `main` is the release branch. A successful push/manual CI run on `main` publishes the version from `pyproject.toml`. PR and `dev` runs never publish.

1. Change `project.version` to the next `X.Y.Z` and add a matching nonempty `## X.Y.Z` section to `CHANGELOG.md`.
2. Update the schema `$id` and version examples in README when changing versions.
3. Open a PR into `main`; review and wait for Lint, Tests, Security and Build.
4. Squash merge. Main CI tests the merged commit, builds and smoke-tests wheel/sdist, then uploads those exact artifacts for the Release job.
5. The Release job verifies checksums, creates `vX.Y.Z` at the tested SHA, uploads wheel/sdist/schema/SHA256SUMS to a draft, then publishes it.

Merges with an already published version leave that release unchanged. Versions must increase; tags are never moved. If publication fails midway, rerun the original main workflow: a draft at the same commit can be completed safely. Do not delete or retarget published tags. If the old unpublished tag belongs to an earlier commit, rerun that original run rather than a newer commit.

Only the release job has `contents: write`; all checks have read-only repository tokens. No registry credentials are needed. PyPI Trusted Publishing is a separate future integration. Checksums detect damaged downloads; they are not cryptographic provenance signatures.

For a local package check, use a clean output directory:

```sh
python -m build --outdir /tmp/pipeforge-release-dist
python scripts/smoke_package.py /tmp/pipeforge-release-dist
python scripts/release.py prepare /tmp/pipeforge-release-dist
```
