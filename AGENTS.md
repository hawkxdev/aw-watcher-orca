# aw-watcher-orca

This macOS Python service attributes foreground Orca time to repository and worktree labels in ActivityWatch. Read the project guide at `.agents/skills/project-guide/SKILL.md` and its knowledge index for the affected area. The issue and its acceptance criteria define the requested change; a documentation or code change does not authorize installation or data operations.

## Repository and checks

Application modules are under `src/aw_watcher_orca/`, tests under `tests/`, and public utilities under `scripts/`. Read `docs/development.md` for setup and contribution checks. Use Python 3.12 and `uv sync --locked`, then run `scripts/check-quality.sh`; `scripts/check-tests.sh tests/test_watcher.py` selects an existing test file. Keep the lockfile, strict test settings and quality rules unless the task explicitly changes them.

Use synthetic data in tests. The reporting contract cases are in `docs/evidence/project-statistics-contract-cases.json`. Public documentation, issue and PR text use English. Describe checks and untested platform behavior accurately. A loaded job, neutral heartbeat or successful API call alone does not establish valid project collection.

## Collection contracts

- The running service uses the unique `isActive` row from `orca worktree ps --json` on each foreground poll. The JSON snapshot probe is a separate diagnostic tool. Do not substitute `activeWorkspaceKey` or a profile-file update trigger for live attribution.
- Preserve NFC labels and the two-identical-poll confirmation rule. Do not forward CLI rows: event builders select only the documented attribution fields. Use synthetic repository names, redact diagnostics and keep credentials and real window data out of examples.
- Discover one fresh window/AFK bucket pair by shared host suffix, excluding this package's own clients. Do not infer the suffix from the machine hostname or choose arbitrarily among ambiguous pairs.
- Require an explicit mode from the closed test/production profiles. Never fall back from production to test automatically. Both profiles share one persistent user lock; do not run a test watcher alongside production or remove its lock path.
- Preserve bucket history and logs. Missing coverage is unknown time, not zero work. For reporting, preserve the documented production boundary, AFK seam tolerance, conflicts and resource limits in `docs/reporting.md`.

## Changes and delivery

Use focused topic branches and pull requests. Run checks locally; do not add or trigger hosted execution as a side effect. Preserve concurrent work and existing review requirements. Document behavior changes with a regression case and a relevant contrasting case. Maintainers control integration and release.

Read `docs/launchagent.md` before service operations. Moving a checkout or replacing its environment invalidates installed paths; installation, removal, restart and production acceptance require their own task authority. Ordinary setup and tests must not alter a running installation or recorded ActivityWatch data.
