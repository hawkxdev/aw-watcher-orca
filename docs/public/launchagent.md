# User LaunchAgent

The LaunchAgent manager runs the watcher after login and keeps it alive without a shell, `uv` or the user `PATH`.

The manager operates the watcher service only. The statistics page is a separate read-only process described in [reporting.md](reporting.md); no LaunchAgent manages it.

The plist carries an explicit test or production mode, which the manager validates before installation. `install` replaces the managed service. Check the current mode with `status` before changing it, and do not install a test service over a production service.

## Prerequisites

- Run `uv sync --locked` in the current clone.
- Keep the clone and `.venv` at their installed paths.
- Install Orca at `/usr/local/bin/orca`.
- Keep ActivityWatch available at `http://localhost:5600`.

The package is not installed into `.venv`; the manager imports it from the checkout, so every manager command below sets `PYTHONPATH=src`.

## Inspect the configuration

Run these commands from the repository root:

```
PYTHONPATH=src .venv/bin/python scripts/launch_agent.py render --mode test | /usr/bin/plutil -lint -
PYTHONPATH=src .venv/bin/python scripts/launch_agent.py status
```

`render` and `install` require `--mode`, chosen from the closed profile set the watcher itself accepts; `status` and `uninstall` take none. `status` reports the label, loaded state, plist presence and the mode it detects in the managed plist, which is empty when no plist is installed and also empty when the file is present but not recognised. Exit code 113 from the exact service target means absent only after the user domain check succeeds. Any other unexpected status blocks mutation.

## Install

```
PYTHONPATH=src .venv/bin/python scripts/launch_agent.py install --mode test
```

The manager validates Python, package import, Orca, the user domain, managed paths and a temporary plist before changing the service. It writes `~/Library/LaunchAgents/io.github.hawkxdev.aw-watcher-orca.plist`, confirms a previous service has left the domain, atomically replaces the plist and calls `bootstrap`. A failed bootstrap restores the previous plist and reloads the previous service when it was active.

Verify the sanitized state:

```
PYTHONPATH=src .venv/bin/python scripts/launch_agent.py status
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
PYTHONPATH=src .venv/bin/python scripts/launch_agent.py uninstall
```

The manager calls `bootout` for the exact service, polls until the service is absent, removes the managed plist and leaves the logs intact. It does not remove ActivityWatch buckets.

## Verify collection

Setup does not install the service automatically. After an installation, check the managed status and fresh window, AFK and project events in ActivityWatch. A loaded LaunchAgent alone does not prove collection. Permissions, foreground attribution and startup after login require checks on the target Mac.
