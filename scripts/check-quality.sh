#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"
uv run --locked ruff check src tests scripts/launch_agent.py scripts/orca_state_probe.py
uv run --locked ruff format --check src tests scripts/launch_agent.py scripts/orca_state_probe.py
uv run --locked mypy --config-file pyproject.toml
uv run --locked pytest
