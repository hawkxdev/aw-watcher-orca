# aw-watcher-orca

Attribute active Orca time to the repository and worktree you are actually working in, for [ActivityWatch](https://activitywatch.net/) on macOS.

Orca reports `app=Orca` and `title=Orca` to the system regardless of which repository or worktree is on screen, so the standard window watcher collapses every project into one indistinguishable entry. This package recovers the missing attribution from Orca's local state.

## What it does today

- Parses the persisted Orca state into an immutable registry of repositories and worktrees, rejecting incomplete and non-canonical entries.
- Resolves the active visible worktree into a public label: `<repo>` for a main worktree, `<repo> / <worktree>` for a child.
- Normalizes public names to NFC and rejects absolute paths used as display names.
- Confirms an observation only after two consecutive identical polls.
- Discovers the single fresh pair of `currentwindow` and `afkstatus` buckets sharing one host suffix, ignoring buckets published by this package itself.
- Decides whether Orca is in the foreground from the last event of that window bucket, with no extra runtime dependency.
- Resolves the visible worktree from `orca worktree ps --json` on every foreground poll; the command returns already normalized public names. The service uses the CLI rather than reading Orca profile files.
- Publishes heartbeats carrying `app`, `title`, `repo`, `worktree`, the schema source and a per-process session token, and a neutral event with an empty title whenever Orca leaves the foreground or any source fails. After a source failure, the previous stability state is discarded and the attribution must pass two fresh polls before publication resumes. An ActivityWatch failure also invalidates the cached bucket pair; discovery retries without choosing arbitrarily among several fresh pairs and resumes only after one pair remains.
- Provides a user LaunchAgent manager that renders, validates, installs, reports and removes the service for an explicitly chosen profile, without depending on a shell, `uv` or the user `PATH` at runtime.
- Ships a read-only diagnostic probe that prints anonymized state snapshots as JSONL.
- Ships a local read-only statistics page that turns the collected ActivityWatch history into repository, worktree and daily totals, with the production boundary, excluded AFK time and source conflicts shown explicitly.

The mandatory `--mode` argument selects one of two bucket profiles: `aw-watcher-orca-test_<host-suffix>` for testing or `aw-watcher-orca_<host-suffix>` for regular collection. Other prefixes and clients cannot be selected through arguments or environment variables. Orca state is read-only, and event payloads exclude absolute paths, branches, comments and terminal content.

## Prerequisites

| Component | Requirement |
|---|---|
| macOS | Orca stores its state under the user library |
| [Python](https://www.python.org/) | 3.12 |
| [uv](https://docs.astral.sh/uv/) | any recent version |
| [ActivityWatch](https://docs.activitywatch.net/) | reachable at `http://localhost:5600` |
| [Orca](https://github.com/stablyai/orca) | CLI at `/usr/local/bin/orca` supporting `worktree ps --json` |

## Installation

```
git clone https://github.com/hawkxdev/aw-watcher-orca.git
cd aw-watcher-orca
uv sync --locked
```

`uv` creates and manages the virtual environment at `.venv`; no manual activation is required for the commands below. There are no environment variables to configure and no runtime dependencies to install.

## Usage

Print one snapshot of the current Orca state:

```
uv run --locked python scripts/orca_state_probe.py --once
```

Observe a worktree switch, marking the moment the transition was requested:

```
uv run --locked python scripts/orca_state_probe.py --duration-seconds 15 --interval-ms 100 --marker switch-to-main
```

`--once` cannot be combined with `--interval-ms`, `--duration-seconds` or `--marker`. Defaults are 100 ms between polls and 15 seconds of observation. The state file is discovered automatically and can be overridden with `--state-file`; discovery accepts exactly one profile, and zero or several candidates raise an error. Failures print a JSON object to stderr and exit with code 2.

Output carries a short fingerprint and the final path component only. Absolute paths, window titles and working file contents never appear in it.

Run the watcher from the repository root. The mode is a mandatory argument with no default, so a silent command line is refused rather than assumed:

```
PYTHONPATH=src uv run --locked python -m aw_watcher_orca --mode test
```

Only one watcher runs per user. The first process holds an exclusive lock on `~/Library/Application Support/aw-watcher-orca/watcher.lock` until it exits, and any second start refuses before it reads Orca state or contacts ActivityWatch.

The package is imported from `src/` rather than installed into the environment. Watcher, manager and reporting commands set `PYTHONPATH=src`; test import paths come from `pyproject.toml`.

Manage the user LaunchAgent from the repository root when you want the watcher to persist across logins:

```
PYTHONPATH=src .venv/bin/python scripts/launch_agent.py status
PYTHONPATH=src .venv/bin/python scripts/launch_agent.py install --mode test
PYTHONPATH=src .venv/bin/python scripts/launch_agent.py uninstall
```

The package is not installed into the virtual environment: the manager imports it from the checkout, so its commands set `PYTHONPATH=src`.

`install` takes the same mandatory `--mode` as the watcher and writes it into the plist, so the installed service starts in exactly the profile you named. `status` reports the mode it finds in the managed plist rather than a fixed value, and refuses to guess: a plist it does not recognise reads as an unknown configuration and blocks installation until you remove it with `uninstall`. `status` and `uninstall` take no mode of their own.

`install` replaces an existing managed service. Check its status first and do not replace a production installation with a test run. See the [LaunchAgent guide](docs/launchagent.md) before changing the service.

## Public API

| Name | Purpose |
|---|---|
| `load_registry` | parse a state snapshot into a validated registry |
| `resolve_active_project` | resolve the active worktree into a public attribution |
| `build_attribution`, `build_worktree_name` | build the label and the worktree name |
| `ActiveProjectStabilizer` | confirm an observation across consecutive polls |
| `read_activitywatch_buckets` | perform one read of the bucket list |
| `select_fresh_bucket_pair` | select the single fresh bucket pair |

Constants: `REQUIRED_STABLE_POLLS` is 2, `DEFAULT_BUCKET_FRESHNESS_WINDOW` is 5 minutes, `DEFAULT_ACTIVITYWATCH_TIMEOUT_SECONDS` is 5.0, and `ACTIVITYWATCH_BUCKETS_URL` points at `http://localhost:5600/api/0/buckets/`.

Every failure derives from `OrcaCoreError`. Reading ActivityWatch distinguishes `ActivityWatchConnectionError`, `ActivityWatchStatusError` and `MalformedActivityWatchPayloadError`.

## Testing

```
scripts/check-tests.sh
```

Run the full quality gate, which adds linting and type checking to the test run:

```
scripts/check-quality.sh
```

Both scripts resolve the repository root and use the locked environment created by `uv sync --locked`. They work from another working directory without manual environment activation.

## Project structure

```
src/aw_watcher_orca/  attribution and reporting modules
tests/                automated checks
scripts/              runtime utilities and local checks
docs/                 operating and development documentation
pyproject.toml        Python and quality configuration
uv.lock               locked development dependencies
```

## Contributing

Issues and pull requests are welcome. Before opening a pull request, run `scripts/check-quality.sh` and make sure it passes: the gate runs [Ruff](https://docs.astral.sh/ruff/), [mypy](https://mypy-lang.org/) and [pytest](https://docs.pytest.org/) over the whole package.

See [Development](docs/development.md) for setup and contribution checks.

New behaviour is expected to arrive with tests that fail before the change and pass after it. Guards are expected to be proven by mutation: break the guard on purpose, confirm the intended test turns red, then restore it.

Orca profile state is read-only. Use `--mode test` for development and keep test data separate from production history. Do not run a test watcher while a production service is loaded; both profiles share the same user lock.

## Maintainers

- [@hawkxdev](https://github.com/hawkxdev)

## Documentation

- [Architecture overview](docs/overview.md)
- [LaunchAgent guide](docs/launchagent.md)
- [Statistics page](docs/reporting.md)

## License

[MIT](LICENSE)
