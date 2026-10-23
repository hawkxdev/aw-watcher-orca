# Architecture overview

## Problem

The standard ActivityWatch window stream records an application name and window title. A generic Orca title does not identify the visible repository or worktree. This package reads the active worktree through Orca's CLI while the window stream identifies Orca as the foreground application.

## Data flow

Diagnostic profile parsing:

```
Orca profile file
  -> load_registry            validate the schema, build the registry
  -> resolve_active_project   resolve the active worktree
  -> build_attribution        build the public project label
  -> ActiveProjectStabilizer  confirm across two polls
```

ActivityWatch discovery:

```
GET /api/0/buckets/
  -> read_activitywatch_buckets   one read, parse the response
  -> select_fresh_bucket_pair     select the single fresh pair
```

The running watcher resolves the active worktree through `orca worktree ps --json` on each foreground poll. The diagnostic profile parser and bucket discovery are independent: parsing a supplied state snapshot performs no network access, and bucket discovery never reads Orca state.

## Modules

| Module | Responsibility |
|---|---|
| `models.py` | immutable registry and public attribution structures |
| `errors.py` | error hierarchy rooted at `OrcaCoreError` |
| `registry.py` | strict snapshot parsing, label uniqueness check |
| `labels.py` | worktree name and public label, NFC normalization |
| `resolver.py` | binding the active identity to the registry |
| `stability.py` | confirmation by internal identity |
| `activitywatch.py` | pure selection of the fresh bucket pair, own clients excluded |
| `activitywatch_reader.py` | the single HTTP read of the bucket list and of one last event |
| `foreground.py` | pure predicate deciding whether Orca is in the foreground |
| `cli_resolver.py` | the `orca worktree ps --json` call and its pure parser |
| `bucket_target.py` | the closed set of bucket profiles and the confirmed target built from matching metadata |
| `publisher.py` | idempotent bucket creation for one closed profile, heartbeats into a confirmed target, pure payload builders |
| `instance_lock.py` | the exclusive user-wide lock that keeps a single watcher alive |
| `watcher.py` | the polling loop and the module entry point |
| `report_models.py` | report result types, source summaries and event counters |
| `report_settings.py` | reporting budgets: history and size limits, timeouts |
| `report_sources.py` | the historical catalog of supported sources and their AFK pairs |
| `report_reader.py` | bounded full-history reads verified by counts and identities |
| `report_intervals.py` | interval algebra: AFK union, switch-seam tolerance, conflicts, calendar days |
| `report_service.py` | one report snapshot with filters, aggregates and quality |
| `report_http.py` | token-guarded local routes, serialization and connection limits |
| `report_server.py` | the standalone page entry point |

Parsing, resolution, label building, pair selection, the foreground predicate and payload building take loaded data and a reference time as parameters. File, network and subprocess access is confined to the probe, readers, CLI resolver, publisher and reporting process. Reporting uses bounded ActivityWatch reads and a local loopback HTTP service.

## Orca state contract

The running service reads the visible worktree from the `isActive` row of `orca worktree ps --json` on every foreground poll. The diagnostic probe and registry separately support profile snapshots discovered at `~/Library/Application Support/orca/profiles/<profile>/orca-data.json` or supplied explicitly. Discovery accepts exactly one profile: zero or several candidates raise an error.

The diagnostic JSON schema is `schemaVersion=1` with a `workspaceSession` object. The registry parser resolves `workspaceSession.activeWorktreeId`. The standalone probe reads both `activeWorktreeId` and `activeWorkspaceKey` for comparison; the latter does not select the running watcher's attribution. A worktree identity has the shape `<repo-id>::<worktree-path>` and may carry a `worktree:` prefix. These tools do not read other Orca storage formats or establish that a persisted file reflects the current UI.

The running watcher confirms a CLI identity after two consecutive identical foreground polls. The snapshot probe instead emits changed persisted identities without that two-poll rule and excludes file modification time from its logical observation signature. Snapshot timing does not establish the live CLI state.

## Label contract

| Case | Label |
|---|---|
| Main worktree | `<repo>` |
| Child worktree | `<repo> / <worktree>` |

The worktree name comes from `worktreeMeta.displayName`, falling back to the final path component when it is absent or unusable. Public segments are normalized to NFC, and canonically equivalent labels are treated as a collision and rejected during parsing. An absolute path is rejected as a repository name.

The attribution result carries the repository name, worktree name, label, main-worktree flag and schema source. Event payload builders omit internal worktree identities and full filesystem paths. Names remain visible and should not contain sensitive information. A caught polling error clears the held attribution and stabilizer. An ActivityWatch read or publication error, a missing window event or an event with unusable timing or an end older than the configured maximum age also requires bucket rediscovery. The default maximum event age is 30 seconds, measured from timestamp plus duration, with an inclusive boundary. A fresh event identifying another application follows the ordinary neutral path without rediscovery.

After an unhealthy window, the loop attempts a neutral heartbeat to the old confirmed target and retries discovery on the next polling tick. During rediscovery it waits one polling interval between attempts while zero or several fresh pairs exist and publishes no active attribution. Once exactly one pair is selected, it confirms the target in the same bucket profile, including when the host suffix changes, and requires two new identical foreground CLI observations before publishing an active label. This also applies when the recovered suffix is unchanged. Startup discovery failures exit the process instead. Rediscovery does not restart or repair the native producer or reconstruct missing history.

## Bucket pair contract

Candidates are buckets of type `currentwindow` and `afkstatus`. The host suffix is the text after the first `_`; an identifier without a separator is not a candidate.

Freshness is decided by `last_updated`. A missing, unparseable or timezone-incomparable value makes that bucket stale, which is not an error on its own. The window boundary is inclusive, the default is 5 minutes, and the value is overridable by parameter.

A pair forms only within one suffix. Exactly one fresh pair returns a result; no pair and several pairs raise distinct, specific errors.

The reader performs one `GET` against a fixed local address and distinguishes three failure classes: an unreachable connection, a response status other than `200`, and a malformed body.

## Statistics page

A separate entry point serves a local, read-only page over the ActivityWatch history the watcher has collected. It never starts, stops or writes to the watcher or to ActivityWatch, and the launch command and flags are documented in [Launching the statistics page](reporting.md).

```
ActivityWatch history
  -> report_sources     catalogue supported sources, pair each with its AFK bucket
  -> report_reader      bounded full reads verified by counts and identities
  -> report_intervals   AFK union, switch-seam tolerance, conflicts, calendar days
  -> report_service     one snapshot with filters, aggregates and quality
  -> report_http        token-guarded routes on 127.0.0.1
  -> report_server      process entry point and lifecycle
```

Opposite-status AFK overlaps of at most 150 milliseconds are switch seams. The tolerance applies to the whole pair overlap within the selected period before fragment carving. The overlap is removed from the earlier interval, which may split in two; the later interval stays whole. With equal starts, the afk interval yields. Same-status intervals merge after seam resolution. A larger pair overlap in the period blocks the exact total for that host as a conflict; test and production retain separate source summaries.

## Boundaries

Orca state is read and never modified. Writes to ActivityWatch go to one bucket, created idempotently, whose identity comes from the mandatory run mode: `aw-watcher-orca-test_<host-suffix>` or `aw-watcher-orca_<host-suffix>`. The profile set is closed and no flag, argument or environment variable can select a prefix or client outside it, but the choice between the two profiles is made by the operator on the command line rather than fixed at build time. Event builders select attribution fields rather than forwarding CLI rows: active data contains exactly `app`, `title`, `repo`, `worktree`, `schema_source` and `session_token`; neutral data contains `app`, empty `title`, `repo` and `worktree`, and `session_token`, without `schema_source`. Labels remain visible. Successful probe snapshots shorten identities, but malformed-input errors can echo unsupported schema values. Do not treat diagnostic text as an unconditional privacy filter.

The statistics page is read-only by construction: it listens on `127.0.0.1` behind a per-process token, sends read requests on fixed API routes to the backend chosen at process startup, and returns aggregates, allowed project names and source metadata. The default backend is local ActivityWatch. Full host and bucket identifiers remain in JSON even though the page shortens their display.
