# aw-watcher-orca

Attribute active Orca time to the repository and worktree you are actually working in, for [ActivityWatch](https://activitywatch.net/) on macOS.

When Orca's window events carry a generic title, the standard window stream cannot distinguish repositories or worktrees. This service checks whether Orca is in the foreground and queries `orca worktree ps --json` for the active worktree.

## What it does today

- Resolves the active visible worktree into a public label: `<repo>` for a main worktree, `<repo> / <worktree>` for a child.
- Normalizes public names to NFC and rejects absolute paths used as display names.
- Confirms an observation only after two consecutive identical polls.
- Discovers the single fresh pair of `currentwindow` and `afkstatus` buckets sharing one host suffix, ignoring buckets published by this package itself.
- Decides whether Orca is in the foreground from the last event of that window bucket, with no extra runtime dependency.
- Resolves the visible worktree from `orca worktree ps --json` on every foreground poll and normalizes the selected names itself. The service uses the CLI rather than reading Orca profile files.
- Publishes active heartbeats with `app`, `title`, `repo`, `worktree`, `schema_source` and a per-process `session_token`. When a fresh window event identifies another application it publishes a neutral heartbeat with empty `title`, `repo` and `worktree`, without `schema_source`. After a polling error it clears the held attribution and attempts a neutral heartbeat; an unavailable ActivityWatch server can prevent that write. ActivityWatch read or publication errors and missing or stale window events trigger strict bucket rediscovery before normal polling resumes. A recovered source requires two new identical foreground CLI observations before active attribution resumes.
- Provides a user LaunchAgent manager that renders, validates, installs, reports and removes the service for an explicitly chosen profile, without depending on a shell, `uv` or the user `PATH` at runtime.
- Includes a parser and read-only JSONL probe for supported persisted JSON snapshots. These diagnostic tools are separate from the running watcher's CLI source.
- Ships a local read-only statistics page that turns the collected ActivityWatch history into repository, worktree and daily totals, with the production boundary, excluded AFK time and source conflicts shown explicitly.

The mandatory `--mode` argument selects one of two bucket profiles: `aw-watcher-orca-test_<host-suffix>` for testing or `aw-watcher-orca_<host-suffix>` for regular collection. Other prefixes and clients cannot be selected through arguments or environment variables. Orca state is read-only, and event payloads exclude absolute paths, branches, comments and terminal content.

The service depends on ActivityWatch's window and AFK producers. It does not start or repair them. Missing or stale window data can stop project attribution even while ActivityWatch's server and this process are running; missing history is unknown time, not zero work.

## Prerequisites

| Component | Requirement |
|---|---|
| macOS | required by the user LaunchAgent and file-lock integration |
| [Python](https://www.python.org/) | 3.12 |
| [uv](https://docs.astral.sh/uv/) | any recent version |
| [ActivityWatch](https://docs.activitywatch.net/) | reachable at `http://localhost:5600` |
| [Orca](https://github.com/stablyai/orca) | CLI at `/usr/local/bin/orca` supporting `worktree ps --json` |

Run ActivityWatch's window and AFK watchers as well as its server. Startup requires exactly one fresh window/AFK bucket pair with a shared host suffix; a responding server alone is insufficient.

## Installation

```
git clone https://github.com/hawkxdev/aw-watcher-orca.git
cd aw-watcher-orca
uv sync --locked
```

`uv` creates the virtual environment at `.venv` and installs the locked development tools. The package has no third-party Python runtime dependencies and needs no `.env` file. The commands below require no manual environment activation; commands that import the package set `PYTHONPATH=src` explicitly.

## Usage

Run regular collection from the repository root with an explicit mode:

```
PYTHONPATH=src uv run --locked python -m aw_watcher_orca --mode production
```

For development, use `--mode test` only when no production watcher is loaded. Both modes share one user lock.

The optional snapshot probe reads `orca-data.json` with `schemaVersion=1`. Use it only with an Orca installation or supplied snapshot that still provides the supported JSON format; it does not query the CLI or inspect other profile storage. An unchanged snapshot does not establish the current foreground worktree.

Print one supported persisted snapshot:

```
uv run --locked python scripts/orca_state_probe.py --once
```

Observe a worktree switch, marking the moment the transition was requested:

```
uv run --locked python scripts/orca_state_probe.py --duration-seconds 15 --interval-ms 100 --marker switch-to-main
```

`--once` cannot be combined with `--interval-ms`, `--duration-seconds` or `--marker`. Defaults are 100 ms between polls and 15 seconds of observation. The state file is discovered automatically and can be overridden with `--state-file`; discovery accepts exactly one profile, and zero or several candidates raise an error. Discovery and one-shot read errors emit JSON to stderr and exit with code 2; invalid command options use argparse's text errors. A timed probe exits successfully if it obtained an observation, so inspect its `read_errors` summary as well as its exit code.

Successful snapshot records contain shortened identities, timing and schema metadata, without full paths, window titles or working file contents. The remaining names can still identify projects. Errors for unsupported input can echo the supplied schema value; inspect and redact diagnostic output before sharing it.

Only one watcher runs per user. The first process holds an exclusive lock on `~/Library/Application Support/aw-watcher-orca/watcher.lock` until it exits, and any second start refuses before it reads Orca state or contacts ActivityWatch.

The package is imported from `src/` rather than installed into the environment. Watcher, manager and reporting commands set `PYTHONPATH=src`; test import paths come from `pyproject.toml`.

Manage the user LaunchAgent from the repository root when you want the watcher to persist across logins:

```
PYTHONPATH=src .venv/bin/python scripts/launch_agent.py status
PYTHONPATH=src .venv/bin/python scripts/launch_agent.py install --mode production
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

Attribution and ActivityWatch domain errors derive from `OrcaCoreError`. Reading ActivityWatch distinguishes `ActivityWatchConnectionError`, `ActivityWatchStatusError` and `MalformedActivityWatchPayloadError`. The standalone snapshot probe and LaunchAgent manager use their own error classes and command-line error responses.

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
