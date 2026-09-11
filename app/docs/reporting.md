# Launching the statistics page

The reporting page is a local, read-only view over the ActivityWatch history
collected by the watcher. It runs from the checkout as a separate process and
never starts, stops or writes to the watcher or to ActivityWatch.

## Requirements

- ActivityWatch running locally (`http://localhost:5600`).
- The repository checkout with the environment prepared once:
  `uv sync --project app`.

## Launch

From the repository root:

```bash
PYTHONPATH=app app/.venv/bin/python -m aw_watcher_orca.report_server
```

Flags: `--port <port>` (default: a free port), `--no-browser`,
`--aw-url <url>` (default: `http://localhost:5600`).

The process prints the start URL
`http://127.0.0.1:<port>/#token=<token>` and opens the default browser.
Stopping the process (Ctrl-C) ends the service and frees the port.

## Opening the page

The `#token=` fragment carries a per-process access token. The page reads it,
stores it in the tab memory only and removes the fragment from the address
bar. Reloading the page later requires opening the start URL again — there is
no stored secret by design. Every API request sends the token in the
`X-Report-Token` header; the service listens on `127.0.0.1` only.

## Using the page

- Pick the start and end dates (inclusive calendar days, up to 31),
  a time zone (default `Europe/Minsk`) and an optional project filter
  (literal substring, case-insensitive), then press «Загрузить отчёт».
- The projects table lists repositories with expandable worktree rows
  (click or Enter). The second-level panel shows per-source diagnostics:
  alias, host, kind, conflict reason, quality and the event counters
  (raw, in-period, matching, contributing, segments). The meta bar shows
  the production boundary.
- «Сравнить с окнами» loads the standard Orca stream for the shown report
  (`report_id`). It never adds to the project totals.
- A parameter change hides the previous result until you reload.

## States

The page distinguishes: initial, loading, result, empty selection, stale data
(no fresh events for 30 s), incomplete sources, source conflict, API
unavailable, busy, limit exceeded and expired snapshot. Conflicts and
incompleteness are shown with their reasons; a conflict hides the combined
totals instead of showing a partial sum as a total.

## Privacy

The page receives aggregates and allowed project names only. No tokens or
raw events leave the service; responses carry only the aggregate fields of
the API contract (host and bucket identifiers included — the page renders
them without the domain part), no external resources are loaded, and every
