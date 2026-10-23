# Development

## Setup

Use Python 3.12 and uv from the repository root:

```bash
uv sync --locked
```

Setup does not install a LaunchAgent or start ActivityWatch. See [the service guide](launchagent.md) before changing an installation.

## Checks

```bash
scripts/check-tests.sh
scripts/check-quality.sh
```

The quality script runs Ruff, mypy and pytest. Both scripts work from another working directory without activating the environment. To run one existing test file, use `scripts/check-tests.sh tests/test_launch_agent.py`.

Automated checks do not verify macOS permissions, foreground switching or login startup on the target machine. Describe any relevant manual checks in the pull request.

## Contributions

For a bug report, include expected and observed behavior, reproduction steps and relevant versions. Redact window titles, repository names, local paths and credentials from examples and logs.

Keep changes focused, include tests for changed behavior and link the relevant issue when one exists. Run the quality script before submitting a pull request. Do not run a test watcher while a production service is loaded; they share one user lock.
