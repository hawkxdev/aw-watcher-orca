---
name: project-guide
description: >
  Public engineering guidance for aw-watcher-orca on macOS. WHEN: implement or
  review aw-watcher-orca changes, разберись/разбирайся в коде aw-watcher-orca,
  проверь/проверяй сбор времени Orca, run its local checks. WHEN NOT: unrelated
  ActivityWatch components or general macOS application development.
allowed-tools: [Read, Grep, Bash]
---

# Project Guide

## Role

Explain the project structure, commands, conventions and checks from this repository. Read `AGENTS.md`, [the knowledge index](references/kb-index.md) and the issue or question before dependent work. Use only the context needed for that task. With no assignment, recover the public project context and ask which issue or question to address. Guidance does not authorize service installation, publication or production changes.

## Degrees of Freedom: MEDIUM

## Commands and conventions

Run `uv sync --locked` from the repository root. `scripts/check-tests.sh` runs the configured suite; `scripts/check-quality.sh` adds Ruff and mypy. Both resolve the root independently of the caller and require no activated environment. Pass an existing `tests/...` path to the test script for a focused run.

Code is under `src/aw_watcher_orca`, tests under `tests/`, and utilities under `scripts/`. Python 3.12 and tool constraints come from `pyproject.toml` and `uv.lock`. The package is imported through `PYTHONPATH=src` for its command entrypoints; test import paths are configured separately. There is no wheel installation step.

For attribution, read `docs/overview.md` before changing discovery, CLI resolution, stability or payloads. Inspect their actual implementation and tests. The service queries Orca through its CLI; persisted JSON parsing is diagnostic only. Keep the closed bucket profiles, unique-pair selection, selected event fields and two-poll confirmation rule.

Read `docs/launchagent.md` before service operations. Mode must be explicit. Test and production share a user lock, so do not start a test watcher while production is loaded. Render and inspect plists before an authorized installation; moving a checkout or environment invalidates pinned paths. Never use lifecycle operations merely to check documentation or run unit tests.

Read `docs/reporting.md` for interval rules, limits and the separate reporting service. Use the synthetic contract cases and existing tests; do not insert real ActivityWatch history into fixtures. Distinguish tests from actual permissions, foreground switching, login startup and long-running collection checks.

## Knowledge upkeep

Correct affected public commands and contracts within the task, and report missing sources or unperformed checks. Keep evidence appropriate for publication. Product facts belong in their linked documentation; this guide owns navigation and must remain usable from the repository alone.

## Harness enhancements

Use native skill loading or read these files directly. Map commands to available local tools and report a missing capability instead of inventing access or results.
