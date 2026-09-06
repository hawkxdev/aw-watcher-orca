# Architecture overview

## Problem

ActivityWatch receives only the application name and the window title from the system. Orca reports `app=Orca` and `title=Orca` no matter which repository or worktree is on screen, so all of that time collapses into one indistinguishable entry in the window bucket. This package recovers the missing attribution from Orca's local state.

## Data flow

Orca state:

```
Orca profile file
  → load_registry            validate the schema, build the registry
  → resolve_active_project   resolve the active worktree
  → build_attribution        build the public project label
  → ActiveProjectStabilizer  confirm across two polls
```

ActivityWatch discovery:

```
GET /api/0/buckets/
  → read_activitywatch_buckets   one read, parse the response
  → select_fresh_bucket_pair     select the single fresh pair
```

The two flows are independent: parsing state performs no network access, and bucket discovery never reads Orca state.

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
| `trigger.py` | profile discovery and the one field read as a change trigger |
| `cli_resolver.py` | the `orca worktree ps --json` call and its pure parser |
| `bucket_target.py` | the closed set of bucket profiles and the confirmed target built from matching metadata |
| `publisher.py` | idempotent bucket creation for one closed profile, heartbeats into a confirmed target, pure payload builders |
| `instance_lock.py` | the exclusive user-wide lock that keeps a single watcher alive |
| `watcher.py` | the polling loop and the module entry point |

Parsing, resolution, label building, pair selection, the foreground predicate and payload building perform no I/O: they take already loaded data and the reference time as parameters. File, network and subprocess access is confined to the probe, the reader, the trigger, the resolver and the publisher, each of which does one thing and maps its failures to specific exceptions.

## Orca state contract

The state source is discovered at `~/Library/Application Support/orca/profiles/<profile>/orca-data.json` or supplied explicitly. Discovery accepts exactly one profile: zero or several candidates raise an error.

The supported schema is `schemaVersion=1` with a `workspaceSession` object. The visible worktree comes from `workspaceSession.activeWorktreeId`. A worktree identity has the shape `<repo-id>::<worktree-path>` and may carry a `worktree:` prefix.

State is persisted with a delay, so a single read taken right after a switch is not a signal: a value is accepted after two consecutive identical polls. The file modification time is excluded from the logical observation signature, because it changes without an identity transition.

## Label contract

| Case | Label |
|---|---|
| Main worktree | `<repo>` |
| Child worktree | `<repo> / <worktree>` |

The worktree name comes from `worktreeMeta.displayName`, falling back to the final path component when it is absent or unusable. Public segments are normalized to NFC, and canonically equivalent labels are treated as a collision and rejected during parsing. An absolute path is rejected as a repository name.

The public result carries only the repository name, the worktree name, the label, the main-worktree flag and the schema source. Internal identifiers and absolute paths never leave the package. A source or publication failure clears both the held attribution and the stabilizer state, so even the previously active identity must pass two fresh successful polls before publication resumes. An ActivityWatch failure also invalidates the cached bucket pair; the loop keeps discovery strict while zero or several fresh pairs exist and resumes only after one pair can be selected.

## Bucket pair contract

Candidates are buckets of type `currentwindow` and `afkstatus`. The host suffix is the text after the first `_`; an identifier without a separator is not a candidate.

Freshness is decided by `last_updated`. A missing, unparseable or timezone-incomparable value makes that bucket stale, which is not an error on its own. The window boundary is inclusive, the default is 5 minutes, and the value is overridable by parameter.

A pair forms only within one suffix. Exactly one fresh pair returns a result; no pair and several pairs raise distinct, specific errors.

The reader performs one `GET` against a fixed local address and distinguishes three failure classes: an unreachable connection, a response status other than `200`, and a malformed body.

## Boundaries

Orca state is read and never modified. Writes to ActivityWatch are confined to one test bucket, `aw-watcher-orca-test_<host-suffix>`, created idempotently; the production identifier is unreachable by construction rather than by convention, since the test prefix is a module constant with no flag, argument or environment override. Absolute paths, window titles, branch names, terminal previews, comments, linked issues, working file contents and personal data appear neither in diagnostic output, nor in logs, nor in exception messages, nor in any published event: an active event carries exactly `app`, `title`, `repo`, `worktree`, the schema source and a session token, and a neutral one carries the same keys with the public values emptied.
