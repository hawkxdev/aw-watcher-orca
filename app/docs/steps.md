# Development stages

## Stage 1: Measuring the switch boundary

- A read-only diagnostic probe reads the persisted Orca state and prints anonymized snapshots as JSONL.
- The state source is discovered automatically from a single profile, or supplied explicitly.
- Both a one-shot snapshot and a time-bounded observation with a transition marker are supported.
- Output carries a short fingerprint and the final path component.

Orca state is persisted with a delay: across the measured transitions the new value appeared in the file hundreds of milliseconds, and in one case more than a second, after the switch command. A single read taken right after a switch is therefore not a signal, and the accepted rule requires two consecutive identical polls. Rewriting the file without an identity change also updates its modification time, which is why that time is excluded from the logical observation signature.

## Stage 2: Attribution core

- A state snapshot is parsed into an immutable registry of repositories and worktrees with strict validation of the required fields.
- The active visible worktree resolves to a public label: the repository name for a main worktree, `<repo> / <worktree>` for a child.
- The worktree name comes from its metadata, falling back to the final path component when that metadata is absent or unusable.
- Only a safe field set leaves the package: names, the label, the main-worktree flag and the schema source.

Public segments are normalized to NFC, and canonically equivalent labels are treated as a collision. Without this, two worktrees whose names look identical but differ in Unicode form would produce different labels and drift apart in reporting. The number of registered worktrees is mutable external state and is pinned neither in the code nor in the checks.

## Stage 3: Bucket pair discovery

- The ActivityWatch bucket list is read in a single request.
- The single fresh pair of `currentwindow` and `afkstatus` buckets sharing one host suffix is selected from it.
- Having no fresh pair and having several are reported as distinct errors.
- Reading distinguishes three failure classes: an unreachable connection, a response status other than `200`, and a malformed body.

The host suffix is never derived from the machine name. On a real configuration the machine name matched the suffix of a pair whose last update was more than two days old, while the live pair carried an entirely different suffix: selecting by machine name would have silently bound the service to dead buckets. Freshness is decided by `last_updated` rather than by the timestamp of the last event, because AFK events are long and extended in place, so a live bucket can carry a last event timestamp that is hours old.
