# Run the same jobs in CI

These wrappers belong in an **application repository** that has `pipeforge.yml` and the pinned `.pf` submodule. The CI agent needs Python 3.11+, Git, network access on first bootstrap, and any application-specific tools.

- Copy [Jenkinsfile](jenkins/Jenkinsfile) to the application root. Jenkins performs its normal SCM checkout; the wrapper initializes pinned submodules and archives run artifacts.
- Copy [GitLab config](gitlab/.gitlab-ci.yml) to `.gitlab-ci.yml`. `GIT_SUBMODULE_STRATEGY: recursive` fetches the recorded commits before launch.
- Copy [GitHub workflow](github-actions/workflow.yml) into `.github/workflows/`. Checkout includes submodules; the final step publishes the generated Markdown summary even after a job failure.

Basic/Python examples are executed in smoke tests under each provider's environment flags. Wrapper YAML is checked and launcher/submodule wiring is verified. This does not replace a live Jenkins/GitLab server test. Containers and equality of host tool versions are not provided by PipeForge yet.

Use approved runner isolation for untrusted PRs. Do not give PR jobs deployment credentials or a privileged self-hosted runner. Reference: [GitLab submodules](https://docs.gitlab.com/ci/runners/git_submodules/).
