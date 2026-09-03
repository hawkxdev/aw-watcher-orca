"""Resolve Orca CLI attribution."""

import json
import subprocess
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Final, cast

from aw_watcher_orca.errors import (
    MalformedCliResultError,
    MalformedWorktreeDataError,
    MultipleActiveWorktreesError,
    NoActiveWorktreeError,
    OrcaCliPayloadError,
    OrcaCliResponseError,
    OrcaCliStatusError,
    OrcaCliTimeoutError,
)
from aw_watcher_orca.labels import (
    LABEL_SEPARATOR,
    is_absolute_public_name,
    normalize_public_name,
)
from aw_watcher_orca.models import ProjectAttribution

# === Constants ===


DEFAULT_ORCA_BINARY_PATH: Final = '/usr/local/bin/orca'
DEFAULT_ORCA_CLI_TIMEOUT_SECONDS: Final = 5.0
CLI_SCHEMA_SOURCE: Final = 'orca-cli-v1'


# === Subprocess runner ===


def run_orca_worktree_ps(
    binary_path: str = DEFAULT_ORCA_BINARY_PATH,
    timeout: float = DEFAULT_ORCA_CLI_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Execute the Orca CLI."""
    try:
        result = subprocess.run(  # noqa: S603 (fixed binary path, no shell)
            [binary_path, 'worktree', 'ps', '--json'],
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        raise OrcaCliTimeoutError(
            f'Orca CLI timed out after {timeout}s'
        ) from None
    if result.returncode != 0:
        raise OrcaCliStatusError(
            f'Orca CLI returned status {result.returncode}'
        )
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise OrcaCliPayloadError('Invalid Orca CLI JSON output') from None
    if not isinstance(payload, dict):
        raise OrcaCliPayloadError('Orca CLI output must be an object')
    return cast(dict[str, object], payload)


# === Pure parser ===


def parse_active_worktree(
    payload: Mapping[str, object],
    schema_source: str = CLI_SCHEMA_SOURCE,
) -> tuple[str, ProjectAttribution]:
    """Parse active worktree attribution."""
    if payload.get('ok') is not True:
        raise OrcaCliResponseError('Orca CLI returned ok=false')
    result = payload.get('result')
    if not isinstance(result, Mapping):
        raise MalformedCliResultError('Missing or invalid result object')
    worktrees = result.get('worktrees')
    if not isinstance(worktrees, list):
        raise MalformedCliResultError('Missing or invalid worktrees list')
    active_rows: list[Mapping[str, object]] = [
        row
        for row in worktrees
        if isinstance(row, Mapping) and row.get('isActive') is True
    ]
    if not active_rows:
        raise NoActiveWorktreeError('No active worktree found')
    if len(active_rows) > 1:
        raise MultipleActiveWorktreesError(
            f'Multiple active worktrees found: {len(active_rows)}'
        )
    row = active_rows[0]
    worktree_id = row.get('worktreeId')
    if not isinstance(worktree_id, str) or not worktree_id.strip():
        raise MalformedWorktreeDataError('Missing or blank worktreeId')
    repo = row.get('repo')
    if not isinstance(repo, str) or not repo.strip():
        raise MalformedWorktreeDataError('Missing or blank repo')
    if is_absolute_public_name(repo):
        raise MalformedWorktreeDataError('Absolute repository name')
    repo_name = normalize_public_name(repo.strip())
    is_main = row.get('isMainWorktree')
    if not isinstance(is_main, bool):
        raise MalformedWorktreeDataError('isMainWorktree must be a boolean')
    display_name = row.get('displayName')
    if (
        isinstance(display_name, str)
        and display_name.strip()
        and not is_absolute_public_name(display_name.strip())
    ):
        worktree_name = normalize_public_name(display_name.strip())
    else:
        path = row.get('path')
        if not isinstance(path, str) or not path.strip():
            raise MalformedWorktreeDataError(
                'Missing or unusable worktree path for fallback'
            )
        fallback_name = PurePosixPath(path.strip()).name
        if not fallback_name:
            raise MalformedWorktreeDataError('Unusable worktree path')
        worktree_name = normalize_public_name(fallback_name)
    label = (
        repo_name
        if is_main
        else f'{repo_name}{LABEL_SEPARATOR}{worktree_name}'
    )
    attribution = ProjectAttribution(
        repo=repo_name,
        worktree=worktree_name,
        label=label,
        is_main_worktree=is_main,
        schema_source=schema_source,
    )
    return worktree_id.strip(), attribution
