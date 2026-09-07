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

## Stage 4: Foreground detection and the event publisher

- Whether Orca is in the foreground is read from the last event of the discovered window bucket: `data.app` must name Orca and the end of that event must not be older than a bounded age. The measured lag of that signal is up to 9 seconds, and the accepted bound is 30.
- The visible worktree is resolved as a hybrid. The profile file is read for one field only and acts as a change trigger at about 1 ms; `orca worktree ps --json` is the resolver at about 160 ms and returns already normalized public names. The stability rule keys on the identity the resolver returned, never on the one read from the file, because during a switch the file lags behind by up to about 1.3 seconds.
- The resolver reads five fields and ignores the rest. The command also returns terminal previews, branches, comments and linked issues; none of them may reach an event, a log line or an exception message.
- Heartbeats are published every 2 seconds with a pulse time of 3. An event carries `app`, `title`, `repo`, `worktree`, the schema source and a session token generated once per process. Leaving the foreground, or any failure of any source, publishes a neutral event whose title is empty, which cannot match a non empty report pattern in either matching mode and therefore can never be attributed to a project.
- A source or publication failure also discards the held attribution and the previous stabilizer state. Recovery of the same worktree therefore requires two fresh successful polls instead of remaining neutral until an unrelated worktree switch.
- An ActivityWatch failure also invalidates the cached bucket pair. Discovery retries while the server is unavailable or several pairs are fresh, never chooses a pair by hostname or recency alone, and resumes on the new test bucket after one pair remains.
- The loop holds the current attribution itself and republishes it on every tick. The stabilizer returns nothing both when an observation is not yet stable and when it is stable but unchanged, so treating that answer as "publish nothing" would stop publication after the first stabilization.

Stage 4 writes go to a separate test bucket. Adding that bucket also exposed a defect in Stage 3: discovery paired candidates within a host suffix by cartesian product, so a bucket published by this package doubled the candidates of its own suffix and made discovery fail on the next start. Buckets are now filtered by client, and the service no longer breaks on its own output. Stage 5 retains the test bucket after acceptance; enabling the production identifier requires a separate owner decision.

## Stage 5: Long-run validation

- The local run lasted 11 hours, 46 minutes and 31 seconds under one process token and produced 194 segments across 13 active labels and 121 transitions.
- No event gap exceeded 6 seconds while ActivityWatch was available; the largest measured gap was 3.967 seconds.
- The existing AFK query excluded 513.003 seconds, and closing Orca produced a neutral segment before the current CLI attribution resumed.
- An ActivityWatch restart exposed a cached-pair defect. The loop now invalidates the pair after an ActivityWatch failure, waits through zero or multiple fresh pairs without guessing, and resumes after strict discovery returns one pair.
- All inspected events used the allowed schema, no absolute path was found, and no production bucket was created.

The production bucket and LaunchAgent remained separate decisions after this local acceptance.

## Stage 6: User LaunchAgent

- A local manager renders and validates the plist, reports sanitized state, installs one exact user service and removes it after bounded confirmation.
- The service runs `app/.venv/bin/python -m aw_watcher_orca` directly with `WorkingDirectory=app`, `KeepAlive=true`, `ThrottleInterval=10` and `Umask=077`.
- The watcher log rotates at 1 MiB with three backups. The directory uses mode `0700`; the plist and log files use `0600`.
- Installation, `kickstart`, ActivityWatch recovery and automatic startup after a new login were validated with one process and a new session token at every process start.
- Uninstall removed the service, plist and process, retained the logs and produced no heartbeat during a 15 second observation window.

The live validation ended with the service uninstalled. Permanent installation and the production bucket remain separate owner decisions.

## Stage 7: production contour

- The output bucket is one profile from a closed set of `test` and `production`. Exact bucket metadata is verified before creation and again after `200` or `304`; a mismatch or an unreachable recheck blocks the heartbeat.
- A heartbeat can only be sent to a target returned by that verification, so an arbitrary bucket identifier is no longer accepted.
- One exclusive user-wide lock keeps a single watcher alive across every mode. It is acquired before Orca state and ActivityWatch are read, held until the process exits and never removed from disk.
- The holder re-checks that it still owns its path once per tick, because an external removal of the lock file otherwise leaves it holding an unlinked inode while a second process locks a fresh one.
- The run mode is a mandatory argument with no default.
- The manager carries that mode into the plist: `render` and `install` require it, `status` and `uninstall` do not. The gap left by Stage 6, where an installed service started and exited because the plist passed no mode, is closed.
- The installed mode is read back from the managed plist by comparing it with the manager's own candidate for each profile, so a file it does not recognise reads as an unknown configuration and blocks installation instead of being overwritten. Removal stays available for such a file.
- Before changing the service the manager probes the watcher's own instance lock, on both the unloaded path and after a confirmed `bootout`, within a bounded budget. The lock held by the watcher remains the final invariant against two publishers; the probe only turns a race into an early, readable refusal.
- No production bucket has been created, no service is installed and no watcher process runs.
