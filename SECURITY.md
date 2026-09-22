# Security policy

PipeForge has a pre-alpha implementation and no supported releases yet. Report problems against the latest source checkout.

Do not post credentials, exploit details, or sensitive logs in public issues. If GitHub private vulnerability reporting is enabled, use the repository Security tab to report privately. Otherwise, open a minimal issue asking the maintainer for a private reporting channel, without disclosing vulnerability details. No response-time commitment is currently offered.

## Security model

- A pipeline executes code with the invoking user's permissions. Run only trusted configurations and modules. PipeForge is not a sandbox.
- Obtain secrets from the environment; do not commit secret values or `.env` files.
- Only values declared in `secrets` are masked. Child processes inherit the calling environment; this is not an environment isolation boundary.
- Masking known secret values reduces accidental disclosure but cannot prevent deliberate exfiltration, encoded/transformed values, or every third-party tool leak. Avoid printing secrets in the first place.
- Validate YAML with a safe loader. Treat shell steps as explicit code execution; do not interpolate untrusted configuration values into shell commands.
- Future Git modules must use verified immutable commit references. Tags alone are movable; a pinned commit provides reproducibility, not proof of safety.
- PR checks must not receive deployment credentials or run on privileged/self-hosted runners.

The current runner rejects unsafe YAML tags, duplicate keys, aliases, and unknown fields. It passes resolved values through environment variables, buffers and masks child output, enforces a timeout and output limit, and cleans up the child process group on exit, Ctrl+C, and SIGTERM. Output interrupted by timeout or the output limit is discarded to avoid leaking truncated secret values. Children deliberately escaping their process group are outside this cleanup guarantee.

Security-related changes need regression tests and maintainer review. Module loading is not implemented yet; the Git-module requirement above applies to future work.
