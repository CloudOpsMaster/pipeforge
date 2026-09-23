# Security policy

Security fixes target the latest 0.1.x release. PipeForge is an early MVP; report problems with the version and a minimal reproduction.

Do not post credentials, exploit details, or sensitive logs in public issues. If GitHub private vulnerability reporting is enabled, use the repository Security tab to report privately. Otherwise, open a minimal issue asking the maintainer for a private reporting channel, without disclosing vulnerability details. No response-time commitment is currently offered.

## Security model

- A pipeline executes code with the invoking user's permissions. Run only trusted configurations and modules. PipeForge is not a sandbox.
- Obtain secrets from the environment; do not commit secret values or `.env` files.
- Only values declared in `secrets` are masked. Child processes inherit the calling environment; this is not an environment isolation boundary.
- Masking known secret values reduces accidental disclosure but cannot prevent deliberate exfiltration, encoded/transformed values, or every third-party tool leak. Avoid printing secrets in the first place.
- Validate YAML with a safe loader. Treat shell steps as explicit code execution; do not interpolate untrusted configuration values into shell commands.
- Future Git modules must use verified immutable commit references. Tags alone are movable; a pinned commit provides reproducibility, not proof of safety.
- PR checks must not receive deployment credentials or run on privileged/self-hosted runners.

The current runner rejects unsafe YAML tags, duplicate keys, aliases, unknown fields and cyclic job dependencies. It passes resolved values through environment variables, masks streaming output before console/file sinks, enforces a timeout/output limit, and cleans up the child process group on exit, Ctrl+C and SIGTERM. Secret lookahead prevents matches spanning chunks from leaking. A pending secret prefix is conservatively redacted when output ends or is interrupted. Children deliberately escaping their process group are outside this cleanup guarantee.

Logs, JSON reports and Markdown summaries are redacted; scripts and resolved environment maps are never recorded. Keep `.pipeforge/` gitignored. Runs are private directories with private artifact files; inspect redacted logs before sharing. The launcher installs the local framework and pinned runtime dependencies into an isolated virtualenv, with a source/Python fingerprint and a lock to avoid concurrent installation. The first bootstrap requires package-index access. A pinned submodule is trusted executable code, not a sandbox or a provenance guarantee.

Security-related changes need regression tests and maintainer review. Module loading is not implemented yet; the Git-module requirement above applies to future work.
