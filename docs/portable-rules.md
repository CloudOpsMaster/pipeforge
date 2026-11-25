# Portable rules and Studio

The installed `pipeforge` command accepts both the original job syntax and the new versioned rule syntax. Existing job files keep their `name`, `jobs` or `pipeline` keys and use the existing commands and launcher. A rule file starts with `version: 1`. Keep legacy jobs and versioned rules in separate files during migration and select a file with `--file`. The job renderer remains available as `pipeforge render github`.

## Run and inspect a rule

```sh
pipeforge validate -f examples/rules/portable.yml
pipeforge list -f examples/rules/portable.yml
pipeforge explain demo.report -f examples/rules/portable.yml
pipeforge demo.print -f examples/rules/portable.yml --input text='hello world'
pipeforge demo.report -f examples/rules/portable.yml --dry-run
pipeforge demo.report -f examples/rules/portable.yml --events events.jsonl
pipeforge studio events.jsonl -o studio.html
pipeforge studio events.jsonl --format svg -o studio.svg
pipeforge studio events.jsonl --format json -o studio.json
```

The archive’s Docker and scanner example is available in `examples/rules/integrations.yml`. Inspect it with `pipeforge docker.build -f examples/rules/integrations.yml --dry-run`. A real run needs Docker and a suitable build context.

The demonstration exits 3 and emits a blocked downstream node. Run the report command separately after the intentional failure. Event and report outputs require new filenames. Open the HTML file locally. Blue Ocean, Neural 3D and Timeline share one journal and selection. Neural 3D projects XYZ coordinates and offers rotation and tilt. Logs are bounded, masked and displayed as text. JSON is an array of events conforming to `schema/event.schema.json`.

An explicit file takes precedence. Otherwise the nearest directory containing either `pipeforge.yml` or `pipeforge.yaml` wins, up to and including the Git root. Outside Git only the current directory is searched. Two candidates in the winning directory are an error. Top-level CLI command names are reserved. Load Bash or Zsh completions by reviewing the shell source printed by `pipeforge completion bash|zsh`. Regenerate the completion after adding rules or switching projects.

## Migrate incrementally

1. Keep the original file and its workflows in place. Add a versioned file such as `ci/rules.yml`.
2. Move a single command into a named rule. Use `argv` for direct execution or `run` for intentionally trusted shell code. A `script` rule invokes a repository-relative file with `/bin/sh`.
3. Move ordering into `steps`. A `use` call runs once per occurrence. A `parallel` group waits for its started calls and blocks later steps if any fails.
4. Validate, inspect the dry run, then compare outputs from the rule and original job before switching CI. Keep existing job-based artifact publication and credentials in place until the migration explicitly covers them.

`include` is a list of local paths relative to the declaring file. Includes merge namespaces, with duplicate files, duplicate rules, reference cycles, parent traversal and escaping symlinks rejected. Included files can omit `version`. A command's working directory is the directory of the file that declares it. Each leaf uses exactly one of `argv`, `run`, `script` or `steps`. A `run` list chains shell snippets with `&&`. `vars` holds scalar metadata in version 1.

Inputs support `string`, `integer` and `boolean`. A missing default makes the input required. CLI integers and booleans use JSON literal syntax. Use a whole-token `${inputs.name}` in `argv` or in a step's `with` values. Shell commands read a quoted variable such as `"$PIPEFORGE_INPUT_name"`. Rules do not insert input values into shell source. Review the behavior of the receiving executable and quote shell variables when using them as data.

Add `tools: [docker]` to check presence before starting. To check an executable's bytes, use a mapping such as `tools: {docker: 'sha256:<64 lowercase hex digits>'}` with the digest for your runner. The runner supplies installed binaries. Pin the rule config, CLI and runner image together. Dry runs inspect the configuration and graph without launching processes, reading declared secret environment values or creating files.

Rules have a 300-second default timeout per command. A timeout exits 124 and terminating the CLI stops foreground process groups. Each run allows at most 512 invocations and 16 concurrent leaf executions. Outside the small base environment, only names listed in `secrets` pass through from the calling process. Masking covers the declared literal values. Trusted processes still have the runner account's filesystem, network and local service access.

## CI compatibility

```sh
pipeforge export github demo.report --file examples/rules/portable.yml
pipeforge export gitlab demo.report --file examples/rules/portable.yml
pipeforge export jenkins demo.report --file examples/rules/portable.yml
pipeforge import github workflow.yml --file examples/rules/portable.yml
```

| Capability | GitHub Actions | GitLab CI | Jenkins |
| --- | --- | --- | --- |
| Rule wrapper | Manual dispatch, labeled runner, pinned checkout with read-only permissions | Manual web pipeline on a protected ref, tagged runner | Declarative pipeline, labeled agent |
| Runner setup | Preinstall a pinned PipeForge version, restrict the runner pool to trusted refs | Preinstall a pinned PipeForge version and use a protected runner | Preinstall a pinned PipeForge version and configure trusted SCM checkout |
| Local steps and parallel groups | One job and workspace | One job and workspace | One agent and workspace |
| Import | Generated wrapper shape | Generated wrapper shape | Generated declarative text |
| Standalone native DAG, provider approvals, secret binding, cross-job artifacts | Unsupported | Unsupported | Unsupported |

Import returns the matched wrapper and rule as JSON after validating the supplied workflow against the configuration. Export and import reject features outside their supported subset and report a source location. Standalone export and export of rule files with declared secrets fail closed. A wrapper preserves local work within one native job. Moving work across native jobs requires a registry or an authenticated artifact handoff. Provider linting and real runner execution are separate validation gates.

## GitOps promotion draft

```sh
pipeforge promote deploy/app.yml --container app --digest "sha256:$REVIEWED_DIGEST" -o promotion.patch
git apply --check --unidiff-zero promotion.patch
git apply --unidiff-zero promotion.patch
```

Supply 64 lowercase hex digits in `REVIEWED_DIGEST`. The generated patch updates one container image in an `apps/v1` Deployment and preserves surrounding YAML. Review the change through your existing PR process, then let your GitOps controller reconcile the approved ref. Supply `--observed-digest` to compare an independently observed runtime digest to the proposed digest. Live drift collection, controller health and PR creation remain future work.

## Studio captures

- [Blue Ocean](studio/blue-ocean.png)
- [Neural 3D](studio/neural-3d.png)
- [Timeline](studio/timeline.png)

These captures show the same run of `demo.report`. Event JSON can feed a read-only consumer. AI analysis, MCP transport, large-graph clustering, live events and a WebGL-based Studio remain outside this slice.

## Verification record

Baseline `42acc153e8faaa983b85efc8907027f952ca02b8` on macOS returned 199 passing tests and one version check failure. The local environment still had PipeForge 0.1.0 installed. After installing a 0.2.0 wheel, the legacy suite and the new rule tests passed together.

Local verification covered the full pytest suite, Ruff, mypy, Bandit, wheel and source distribution builds, Twine metadata checks, an installed-wheel smoke test and SHA-256 verification of prepared release files. The runtime requirements audit reported zero known vulnerabilities and produced a CycloneDX inventory. The existing development environment audit reported advisories in certifi, filelock, msgpack and setuptools. Provider round trips used golden fixtures. Real provider runners, Linux, Windows, production staging and release signing remain unverified.
