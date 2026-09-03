"""Test profile trigger reading and discovery."""

import json
from pathlib import Path

import pytest

from aw_watcher_orca.errors import (
    MalformedStateError,
    ProfileDiscoveryError,
    UnsupportedSchemaError,
)
from aw_watcher_orca.trigger import (
    SUPPORTED_ORCA_SCHEMA_VERSION,
    discover_profile_state_file,
    read_active_worktree_trigger,
)

# === Fixtures ===


def _make_profile(tmp_path: Path, profile_name: str = 'default') -> Path:
    """Create one synthetic profile path."""
    profile_dir = (
        tmp_path
        / 'Library'
        / 'Application Support'
        / 'orca'
        / 'profiles'
        / profile_name
    )
    profile_dir.mkdir(parents=True, exist_ok=True)
    return profile_dir / 'orca-data.json'


# === Discovery contract ===


def test_discover_profile_state_file_finds_single_candidate(
    tmp_path: Path,
) -> None:
    expected = _make_profile(tmp_path, 'my-profile')
    expected.write_text('{}', encoding='utf-8')

    discovered = discover_profile_state_file(tmp_path)

    assert discovered == expected


def test_discover_profile_state_file_rejects_zero_candidates(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ProfileDiscoveryError,
        match='No Orca profile state file found',
    ):
        discover_profile_state_file(tmp_path)


def test_discover_profile_state_file_rejects_multiple_candidates(
    tmp_path: Path,
) -> None:
    file_a = _make_profile(tmp_path, 'profile-a')
    file_b = _make_profile(tmp_path, 'profile-b')
    file_a.write_text('{}', encoding='utf-8')
    file_b.write_text('{}', encoding='utf-8')

    with pytest.raises(
        ProfileDiscoveryError,
        match='Multiple Orca profile state files found: 2',
    ):
        discover_profile_state_file(tmp_path)


# === Trigger read contract ===


def test_read_active_worktree_trigger_returns_id_for_valid_file(
    tmp_path: Path,
) -> None:
    state_file = tmp_path / 'orca-data.json'
    state_file.write_text(
        json.dumps(
            {
                'schemaVersion': SUPPORTED_ORCA_SCHEMA_VERSION,
                'workspaceSession': {
                    'activeWorktreeId': 'wt-123',
                },
            }
        ),
        encoding='utf-8',
    )

    result = read_active_worktree_trigger(state_file)

    assert result == 'wt-123'


def test_read_active_worktree_trigger_rejects_wrong_schema_version(
    tmp_path: Path,
) -> None:
    state_file = tmp_path / 'orca-data.json'
    state_file.write_text(
        json.dumps(
            {
                'schemaVersion': 999,
                'workspaceSession': {
                    'activeWorktreeId': 'wt-123',
                },
            }
        ),
        encoding='utf-8',
    )

    with pytest.raises(
        UnsupportedSchemaError,
        match='Unsupported Orca schema version: 999',
    ):
        read_active_worktree_trigger(state_file)


def test_read_active_worktree_trigger_rejects_missing_workspace_session(
    tmp_path: Path,
) -> None:
    state_file = tmp_path / 'orca-data.json'
    state_file.write_text(
        json.dumps(
            {
                'schemaVersion': SUPPORTED_ORCA_SCHEMA_VERSION,
            }
        ),
        encoding='utf-8',
    )

    with pytest.raises(
        MalformedStateError,
        match='Missing workspaceSession object',
    ):
        read_active_worktree_trigger(state_file)


@pytest.mark.parametrize(
    'bad_id',
    [None, '', '   ', 123, []],
    ids=['none', 'empty', 'blank', 'int', 'list'],
)
def test_read_active_worktree_trigger_rejects_missing_or_blank_id(
    tmp_path: Path,
    bad_id: object,
) -> None:
    state_file = tmp_path / 'orca-data.json'
    payload: dict[str, object] = {
        'schemaVersion': SUPPORTED_ORCA_SCHEMA_VERSION,
        'workspaceSession': {},
    }
    if bad_id is not None:
        payload['workspaceSession'] = {'activeWorktreeId': bad_id}
    state_file.write_text(json.dumps(payload), encoding='utf-8')

    with pytest.raises(
        MalformedStateError,
        match='Missing or blank activeWorktreeId',
    ):
        read_active_worktree_trigger(state_file)


def test_read_active_worktree_trigger_ignores_malformed_other_sections(
    tmp_path: Path,
) -> None:
    state_file = tmp_path / 'orca-data.json'
    payload = {
        'schemaVersion': SUPPORTED_ORCA_SCHEMA_VERSION,
        'workspaceSession': {
            'activeWorktreeId': 'wt-resilient',
        },
        'repositories': 'totally-broken-repositories-string',
        'worktrees': None,
        'otherGibberish': [1, 2, 'three'],
    }
    state_file.write_text(json.dumps(payload), encoding='utf-8')

    result = read_active_worktree_trigger(state_file)

    assert result == 'wt-resilient'


def test_read_active_worktree_trigger_rejects_invalid_json(
    tmp_path: Path,
) -> None:
    state_file = tmp_path / 'orca-data.json'
    state_file.write_text('{broken json', encoding='utf-8')

    with pytest.raises(
        MalformedStateError,
        match='Invalid Orca state JSON',
    ):
        read_active_worktree_trigger(state_file)


def test_read_active_worktree_trigger_rejects_non_object_root(
    tmp_path: Path,
) -> None:
    state_file = tmp_path / 'orca-data.json'
    state_file.write_text(
        json.dumps(['not', 'an', 'object']), encoding='utf-8'
    )

    with pytest.raises(
        MalformedStateError,
        match='Orca state root must be an object',
    ):
        read_active_worktree_trigger(state_file)
