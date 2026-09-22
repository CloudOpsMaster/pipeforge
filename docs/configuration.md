# Configuration reference

PipeForge reads `pipeforge.yml` in the current directory, or the file passed through `-f` / `--file` after the subcommand. Relative paths used by commands are relative to the configuration file's directory.

```yaml
name: application

values:
  app:
    name: demo
    port: 8080
  greeting: "Hello ${values.app.name}"

secrets:
  API_TOKEN:
    from: env
    required: true

pipeline:
  - name: Greet
    timeout: 30
    env:
      GREETING: "${values.greeting}"
      PORT: "${values.app.port}"
      TOKEN: "${secrets.API_TOKEN}"
    script: |
      set -eu
      printf '%s\n' "$GREETING"
      python -c 'import os; print("Port:", os.environ["PORT"])'
```

## Fields

| Field | Contract |
| --- | --- |
| `name` | Required non-empty string |
| `values` | Optional nested mappings of string, finite number, or boolean values |
| `secrets` | Optional mapping of environment variable names to declarations |
| `pipeline` | Required non-empty list of steps |
| Step `name` | Required non-empty string, unique within the pipeline |
| Step `script` | Required non-empty POSIX shell script |
| Step `env` | Optional mapping of environment variable names to strings |
| Step `timeout` | Seconds, greater than 0 and at most 86400; default 300 |

Unknown fields, duplicate YAML keys, aliases, non-string mapping keys, Python YAML tags, lists/nulls in values, and NUL characters in strings are rejected. Configuration size is limited to 1 MiB; values nesting to 20 levels; reference chains to 50; each resolved string to 64 Ki characters. These are guardrails, not a sandbox for hostile input.

## Values and references

`${values.app.name}` resolves a nested scalar. Values can reference other values; missing and cyclic references fail before any step executes. Values cannot reference secrets. Value keys use letters, digits, underscores or hyphens, starting with a letter or underscore; dots separate nesting levels.

References are expanded only inside `values` strings and step `env` strings. A numeric value becomes text; booleans become `true` or `false`. Keep `env` values quoted even for numbers.

Scripts do not accept `${values.*}` or `${secrets.*}`. Pass data through `env`, then use quoted shell variables such as `"$GREETING"`, or read `os.environ` in Python. Shell expressions such as `$(...)` in a resolved value remain data unless your script explicitly evaluates them. Do not use `eval` on untrusted data.

## Secrets

Each secret declares `from: env`; PipeForge reads the same-named environment variable. `required` defaults to `true`. Missing or empty required secrets fail before execution. An absent optional secret (`required: false`) becomes an empty string.

The process environment is inherited. Declared secret variables are available under their original names; step `env` can map them to another name. Step `env` overrides inherited variables for that step. Environment changes or `cd` inside a step do not persist into subsequent steps.

PipeForge does not load `.env` automatically. Use your shell or CI secret store to populate the environment. Never store real values in configuration, commit messages, issues, or examples.

All non-empty declared secret values are masked in framework messages and merged child stdout/stderr, including exact multiline values and overlapping matches. Undeclared, encoded, transformed, partially printed, or deliberately exfiltrated secrets cannot be reliably masked. Avoid printing secrets even when masking is enabled.

## Execution and output

- Each step executes as `/bin/sh -c SCRIPT` with stdin disconnected.
- Steps run sequentially; the first nonzero shell exit stops the pipeline.
- Within a script, normal shell rules apply: use `set -eu` when every failed command should stop the step. A failed command earlier in a shell pipe may need explicit checking; portable `/bin/sh` does not guarantee `pipefail`.
- Output is combined and buffered until step completion, so long-running steps do not stream logs yet.
- Output is limited to 8 MiB per step. Timeout or output overflow fails the step and suppresses its entire captured output, including possibly truncated secret prefixes.
- The runner terminates the process group after each step and on cancellation. Background daemons are not supported. Processes that deliberately detach into another group are outside this guarantee.
- Logs show the step, result, duration, and failed command's exit code. Control characters are sanitized, and GitHub workflow command delimiters are neutralized (`::` becomes `: :`, `##[` becomes `# #[`) so child output cannot trigger annotations or runner commands. Lines are indented for readability.
- Terminal colors are automatic; set `NO_COLOR` to disable them. CI output is plain text.

## Commands and exit codes

```sh
pipeforge --version
pipeforge validate -f pipeforge.yml
pipeforge run --dry-run -f pipeforge.yml
pipeforge run -f pipeforge.yml
```

`validate` checks syntax, fields, and reference structure, without requiring secret values or checking installed external tools. `--dry-run` performs the same checks and lists steps and timeouts, without running commands or displaying scripts or environment values. A successful dry run does not guarantee that a real run has all required secrets or tools.

| Code | Meaning |
| --- | --- |
| 0 | Validation, dry run, or pipeline succeeded |
| 1 | Step failed, timed out, exceeded output limit, or could not execute |
| 2 | CLI usage, configuration, references, or required secret error |
| 130 | Interrupted by Ctrl+C / SIGINT |
| 143 | Terminated by SIGTERM |

The original shell exit code is shown in the log; the CLI normalizes execution failures to `1`.

See [hello-world](../examples/hello-world/pipeforge.yml) and the [fake-secret example](../examples/secrets/pipeforge.yml). The latter can be run using an explicitly fake value:

```sh
DEMO_TOKEN=example-only-not-a-real-secret pipeforge run -f examples/secrets/pipeforge.yml
```

Modules, `variables`, `${git.sha}`, environment overlays, Windows execution, deployment adapters, and `doctor`/`explain` commands are not implemented yet.
