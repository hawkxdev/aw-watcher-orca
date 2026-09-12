# Launching the statistics page

The reporting page is a local, read-only view over the ActivityWatch history collected by the watcher. It runs from the checkout as a separate process and never starts, stops or writes to the watcher or to ActivityWatch.

## Requirements

- ActivityWatch running locally at `http://localhost:5600`.
- The repository checkout with the environment prepared once: `uv sync --project app`.

## Launch

From the repository root:

```bash
PYTHONPATH=app app/.venv/bin/python -m aw_watcher_orca.report_server
```

Flags: `--port <port>` (default: a free port), `--no-browser`, `--host <host>` (only `127.0.0.1` or `localhost`) and `--aw-url <url>` (default: `http://localhost:5600`). Keep the default ActivityWatch address for normal local use. The process accepts an override through `--aw-url`; the page API does not accept a backend URL.

The process prints the start URL `http://127.0.0.1:<port>/#token=<token>` and opens the default browser. Stopping the process with Ctrl-C ends the page service and frees its port. The watcher remains managed separately by its LaunchAgent.

## Opening the page

The `#token=` fragment carries a per-process access token. The page reads it, stores it in tab memory and removes the fragment from the address bar. Reloading the page requires opening the start URL again; no secret is stored persistently. Every API request sends the token in the `X-Report-Token` header. Page assets load without that header, while data routes require it. Host, Origin and cross-site checks apply to the page and its API.

## Using the page

- Pick inclusive start and end dates, up to 31 calendar days, a time zone (default `Europe/Minsk`) and an optional project filter. The filter is a literal, case-insensitive substring. Press «Загрузить отчёт».
- The projects table lists repositories with expandable worktree rows, opened by click or Enter. The daily table and chart use the same calculated values.
- The second-level panel shows source diagnostics: alias, host, kind, conflict reason, quality and separate counts for raw events, events in the period, matching events, contributing events and derived segments. The meta bar shows the production boundary.
- «Сравнить с окнами» loads the standard Orca window stream for the displayed `report_id`. It never adds to project totals. An unavailable comparison does not remove the project report.
- Changing a parameter hides the previous result until another report is loaded.

## Time calculation

The earliest raw production event fixes the transition boundary before date, project, AFK or noise filters. Any test event ending at or after that boundary blocks the combined specialized total. Test and production remain identifiable as separate sources; the standard window stream is a separate comparison.

Opposite-status AFK overlaps of at most 150 milliseconds are switch seams. The tolerance applies to the whole pair overlap within the selected period before it is split into fragments. The overlap is removed from the earlier interval, which may split in two; the later interval stays whole. With equal starts, the `afk` interval yields. Intervals of the same status are merged after seam resolution. A larger overlap in the period blocks the exact total for that host.

Time without AFK coverage is unknown, not active time. Noise is removed before splitting accepted intervals at local midnight, so short parts of an accepted interval remain in their respective days.

## States and limits

The page distinguishes initial input, loading, results, empty selection, stale data, incomplete sources, source conflicts, unavailable API, busy calculation, exceeded limits and an expired snapshot. Conflicts and incomplete data show their reasons; an unavailable total is not displayed as zero work.

Freshness uses the latest event end across the loaded snapshot, including AFK events. An age above 30 seconds is stale. This indicator does not prove that the watcher process is running: a live AFK stream can keep the snapshot fresh while project events are old. Historical totals remain available regardless of current freshness.

The process retains one successful report snapshot for 15 minutes. A replaced or expired `report_id` requires loading the report again before comparison. Only one calculation runs at a time.

History reads are bounded: 100,000 events per specialized source, 50,000 per AFK source and 500,000 raw events per standard source for comparison. The catalog allows 8 supported hosts and 32 buckets. Exceeding a limit produces an explicit refusal. A shorter displayed period does not bypass a full-history read limit.

## Privacy

API responses contain aggregates, permitted project names and source metadata, not raw event payloads or watcher session tokens. Full host and bucket identifiers are currently present in JSON; the page strips the domain part when displaying them. Display shortening does not remove those identifiers from the browser's network responses.

The page loads no external scripts, fonts or telemetry. Responses use `Cache-Control: no-store` and `Referrer-Policy: no-referrer`; the access token remains in tab memory and is not included in report data. The service is intended for local use by the owner of the ActivityWatch history.
