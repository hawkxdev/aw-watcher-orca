#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root/app"
uv run ruff check aw_watcher_orca tests ../tools
uv run ruff format --check aw_watcher_orca tests ../tools
uv run mypy --config-file pyproject.toml
uv run pytest
