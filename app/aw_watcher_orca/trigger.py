"""Read Orca profile trigger state."""

import json
from pathlib import Path
from typing import Final

from aw_watcher_orca.errors import (
    MalformedStateError,
    ProfileDiscoveryError,
    UnsupportedSchemaError,
)

# === Constants ===


SUPPORTED_ORCA_SCHEMA_VERSION: Final = 1


# === Profile discovery ===


def discover_profile_state_file(home_dir: Path | None = None) -> Path:
    """Discover one profile state file."""
    resolved_home = home_dir or Path.home()
    profiles_dir = (
        resolved_home / 'Library' / 'Application Support' / 'orca' / 'profiles'
    )
    candidates = sorted(profiles_dir.glob('*/orca-data.json'))
    if not candidates:
        raise ProfileDiscoveryError('No Orca profile state file found')
    if len(candidates) > 1:
        raise ProfileDiscoveryError(
            f'Multiple Orca profile state files found: {len(candidates)}'
        )
    return candidates[0]


# === Trigger reading ===


def read_active_worktree_trigger(state_file: Path) -> str:
    """Read active worktree trigger identifier."""
    try:
        raw_text = state_file.read_text(encoding='utf-8')
    except OSError as exc:
        raise MalformedStateError(
            f'Unable to read Orca state file: {exc}'
        ) from exc
    try:
        payload = json.loads(raw_text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MalformedStateError('Invalid Orca state JSON') from exc
    if not isinstance(payload, dict):
        raise MalformedStateError('Orca state root must be an object')
    schema_version = payload.get('schemaVersion')
    if (
        type(schema_version) is not int
        or schema_version != SUPPORTED_ORCA_SCHEMA_VERSION
    ):
        raise UnsupportedSchemaError(
            f'Unsupported Orca schema version: {schema_version}'
        )
    workspace_session = payload.get('workspaceSession')
    if not isinstance(workspace_session, dict):
        raise MalformedStateError('Missing workspaceSession object')
    active_worktree_id = workspace_session.get('activeWorktreeId')
    if (
        not isinstance(active_worktree_id, str)
        or not active_worktree_id.strip()
    ):
        raise MalformedStateError('Missing or blank activeWorktreeId')
    return active_worktree_id
