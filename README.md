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
- Resolves the visible worktree as a hybrid: the profile file is a cheap change trigger, `orca worktree ps --json` is the resolver that returns already normalized public names.
- Publishes heartbeats carrying `app`, `title`, `repo`, `worktree`, the schema source and a per-process session token, and a neutral event with an empty title whenever Orca leaves the foreground or any source fails. After a source failure, the previous stability state is discarded and the attribution must pass two fresh polls before publication resumes. An ActivityWatch failure also invalidates the cached bucket pair; discovery retries without choosing arbitrarily among several fresh pairs and resumes only after one pair remains.
- Provides a user LaunchAgent manager that renders, validates, installs, reports and removes the test-bucket watcher without depending on a shell, `uv` or the user `PATH` at runtime.
- Ships a read-only diagnostic probe that prints anonymized state snapshots as JSONL.

Writes remain deliberately confined to a **test bucket** named `aw-watcher-orca-test_<host-suffix>` after local long-run acceptance. Moving to the production identifier is a separate decision: no flag, argument or environment variable can turn the test prefix into it. Orca state is read and never modified, and no absolute path, branch, comment or terminal content ever reaches an event.

## Prerequisites

| Component | Requirement |
|---|---|
| macOS | Orca stores its state under the user library |
| [Python](https://www.python.org/) | 3.12 |
| [uv](https://docs.astral.sh/uv/) | any recent version |
| [ActivityWatch](https://docs.activitywatch.net/) | reachable at `http://localhost:5600` |
| [Orca](https://github.com/stablyai/orca) | 1.4.195 (behaviour first measured on 1.4.194 and re-confirmed on 1.4.195) |

## Installation

```
git clone https://github.com/hawkxdev/aw-watcher-orca.git
cd aw-watcher-orca
uv sync --project app
```

`uv` creates and manages the virtual environment at `app/.venv`; no manual activation is required for the commands below. There are no environment variables to configure and no runtime dependencies to install.

## Usage

Print one snapshot of the current Orca state:

```
uv run --project app python tools/orca_state_probe.py --once
```

Observe a worktree switch, marking the moment the transition was requested:

```
uv run --project app python tools/orca_state_probe.py --duration-seconds 15 --interval-ms 100 --marker switch-to-main
```

`--once` cannot be combined with `--interval-ms`, `--duration-seconds` or `--marker`. Defaults are 100 ms between polls and 15 seconds of observation. The state file is discovered automatically and can be overridden with `--state-file`; discovery accepts exactly one profile, and zero or several candidates raise an error. Failures print a JSON object to stderr and exit with code 2.

Output carries a short fingerprint and the final path component only. Absolute paths, window titles and working file contents never appear in it.

Run the watcher from the repository root. The mode is a mandatory argument with no
default, so a silent command line is refused rather than assumed:

```
uv run --directory app python -m aw_watcher_orca --mode test
```

Only one watcher runs per user. The first process holds an exclusive lock on
`~/Library/Application Support/aw-watcher-orca/watcher.lock` until it exits, and any
second start refuses before it reads Orca state or contacts ActivityWatch.

`uv run --project app` selects the environment but does not change the working directory, so it cannot import the package module from the repository root.

Run the accepted user LaunchAgent lifecycle from the repository root only when you want the watcher to persist across logins:

```
app/.venv/bin/python tools/launch_agent.py status
app/.venv/bin/python tools/launch_agent.py install
app/.venv/bin/python tools/launch_agent.py uninstall
```

The installed plist predates the mandatory mode argument and does not pass it, so a
service installed from this revision starts and exits immediately. Passing the mode
through the plist belongs to the next stage; until then, install nothing and run the
watcher by hand. See the [LaunchAgent guide](app/docs/launchagent.md) before changing the
user service.

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
tools/check-tests.sh
```

Run the full quality gate, which adds linting and type checking to the test run:

```
tools/check-quality.sh
```

Both scripts operate on `app/` and expect the environment created by `uv sync --project app`.

## Project structure

```
app/
├── aw_watcher_orca/
│   ├── models.py
│   ├── errors.py
│   ├── registry.py
│   ├── labels.py
│   ├── resolver.py
│   ├── stability.py
│   ├── activitywatch.py
│   └── activitywatch_reader.py
├── docs/
├── tests/
└── pyproject.toml
tools/
├── orca_state_probe.py
├── launch_agent.py
├── check-tests.sh
└── check-quality.sh
```

## Contributing

Issues and pull requests are welcome. Before opening a pull request, run `tools/check-quality.sh` and make sure it passes: the gate runs [Ruff](https://docs.astral.sh/ruff/), [mypy](https://mypy-lang.org/) and [pytest](https://docs.pytest.org/) over the whole package.

New behaviour is expected to arrive with tests that fail before the change and pass after it. Guards are expected to be proven by mutation: break the guard on purpose, confirm the intended test turns red, then restore it.

Orca profile state is read-only. ActivityWatch writes are confined to the dedicated test bucket until a separate production decision is made.

## Maintainers

- [@hawkxdev](https://github.com/hawkxdev)

## Documentation

- [Architecture overview](app/docs/overview.md)
- [Development stages](app/docs/steps.md)
- [LaunchAgent guide](app/docs/launchagent.md)

## License

[MIT](LICENSE)
