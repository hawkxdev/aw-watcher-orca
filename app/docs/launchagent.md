# User LaunchAgent

The LaunchAgent manager runs the watcher after login and keeps it alive without a shell, `uv` or the user `PATH`. It always writes to `aw-watcher-orca-test_<host-suffix>`. No option or environment variable enables a production bucket.

## Prerequisites

- Run `uv sync --project app` in the current clone.
- Keep the clone and `app/.venv` at their installed paths.
- Install Orca at `/usr/local/bin/orca`.
- Keep ActivityWatch available at `http://localhost:5600`.

## Inspect the candidate

Run these commands from the repository root:

```
app/.venv/bin/python tools/launch_agent.py render | /usr/bin/plutil -lint -
app/.venv/bin/python tools/launch_agent.py status
```

`status` reports only the label, loaded state, plist presence and fixed bucket mode. Exit code 113 from the exact service target means absent only after the user domain check succeeds. Any other unexpected status blocks mutation.

## Install

```
app/.venv/bin/python tools/launch_agent.py install
```

The manager validates Python, package import, Orca, the user domain, managed paths and a temporary plist before changing the service. It writes `~/Library/LaunchAgents/io.github.hawkxdev.aw-watcher-orca.plist`, confirms a previous service has left the domain, atomically replaces the plist and calls `bootstrap`. A failed bootstrap restores the previous plist and reloads the previous service when it was active.

Verify the sanitized state:

```
app/.venv/bin/python tools/launch_agent.py status
```

Moving the clone or replacing `.venv` invalidates the absolute paths in the plist. Run `uninstall` before moving the clone, then install again from the new location.

## Restart

```
/bin/launchctl kickstart -k "gui/$(id -u)/io.github.hawkxdev.aw-watcher-orca"
```

The service should return with one new PID and one new session token. ActivityWatch outages do not require a watcher restart: the running process invalidates its cached bucket pair and resumes after discovering one fresh pair.

## Logs

Logs are stored in `~/Library/Logs/aw-watcher-orca/`:

- `watcher.log` contains bounded application diagnostics and rotates at 1 MiB with three backups.
- `launcher.log` receives failures that happen before Python logging starts.

The directory uses mode `0700`; log files use mode `0600`. Uninstall preserves logs as diagnostic evidence.

## Uninstall

```
app/.venv/bin/python tools/launch_agent.py uninstall
```

The manager calls `bootout` for the exact service, polls until the service is absent, removes the managed plist and leaves the logs intact. It does not remove ActivityWatch buckets.

## Validation boundary

The lifecycle was validated on macOS 26.6.2 arm64 with Orca 1.4.195, ActivityWatch 0.13.2 and Python 3.12. The accepted run covered installation, `kickstart`, ActivityWatch recovery, automatic startup after a new login and complete removal. The repository does not install the service automatically, and the accepted run ended with no loaded service or plist.
