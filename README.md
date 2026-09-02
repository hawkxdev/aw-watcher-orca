# aw-watcher-orca

Attribute active Orca time to the repository and worktree you are actually working in, for [ActivityWatch](https://activitywatch.net/) on macOS.

Orca reports `app=Orca` and `title=Orca` to the system regardless of which repository or worktree is on screen, so the standard window watcher collapses every project into one indistinguishable entry. This package recovers the missing attribution from Orca's local state.

## What it does today

- Parses the persisted Orca state into an immutable registry of repositories and worktrees, rejecting incomplete and non-canonical entries.
- Resolves the active visible worktree into a public label: `<repo>` for a main worktree, `<repo> / <worktree>` for a child.
- Normalizes public names to NFC and rejects absolute paths used as display names.
- Confirms an observation only after two consecutive identical polls.
- Discovers the single fresh pair of `currentwindow` and `afkstatus` buckets sharing one host suffix.
- Ships a read-only diagnostic probe that prints anonymized state snapshots as JSONL.

It writes nothing to ActivityWatch: no buckets are created, no events or heartbeats are sent. Orca state is read and never modified.

## Prerequisites

| Component | Requirement |
|---|---|
| macOS | Orca stores its state under the user library |
| [Python](https://www.python.org/) | 3.12 |
| [uv](https://docs.astral.sh/uv/) | any recent version |
| [ActivityWatch](https://docs.activitywatch.net/) | reachable at `http://localhost:5600` |
| [Orca](https://github.com/stablyai/orca) | 1.4.194 |

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
├── check-tests.sh
└── check-quality.sh
```

## Contributing

Issues and pull requests are welcome. Before opening a pull request, run `tools/check-quality.sh` and make sure it passes: the gate runs [Ruff](https://docs.astral.sh/ruff/), [mypy](https://mypy-lang.org/) and [pytest](https://docs.pytest.org/) over the whole package.

New behaviour is expected to arrive with tests that fail before the change and pass after it. Guards are expected to be proven by mutation: break the guard on purpose, confirm the intended test turns red, then restore it.

Orca state and ActivityWatch data are read-only in this project. A change that writes to either needs to say so explicitly in its description.

## Maintainers

- [@hawkxdev](https://github.com/hawkxdev)

## Documentation

- [Architecture overview](app/docs/overview.md)
- [Development stages](app/docs/steps.md)

## License

[MIT](LICENSE)
