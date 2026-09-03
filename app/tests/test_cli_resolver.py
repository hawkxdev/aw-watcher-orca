"""Test Orca CLI resolver and parsing."""

import subprocess
import unicodedata
from typing import Any
from unittest.mock import MagicMock

import pytest

from aw_watcher_orca.cli_resolver import (
    CLI_SCHEMA_SOURCE,
    DEFAULT_ORCA_BINARY_PATH,
    MalformedCliResultError,
    MalformedWorktreeDataError,
    MultipleActiveWorktreesError,
    NoActiveWorktreeError,
    OrcaCliPayloadError,
    OrcaCliResponseError,
    OrcaCliStatusError,
    OrcaCliTimeoutError,
    parse_active_worktree,
    run_orca_worktree_ps,
)

# === Fixtures ===


def _make_payload(
    worktrees: list[dict[str, Any]],
    ok: bool = True,
) -> dict[str, Any]:
    """Build one synthetic CLI payload."""
    return {
        'ok': ok,
        'id': 'test-query-id',
        'result': {
            'worktrees': worktrees,
            'totalCount': len(worktrees),
            'truncated': False,
        },
    }


def _make_row(
    worktree_id: str = 'wt-12345',
    repo: str = 'my-repo',
    display_name: str | None = 'feature-x',
    is_main: bool = False,
    path: str = '/Users/test/dev/my-repo/worktrees/feature-x',
    is_active: bool = True,
    **extra: Any,
) -> dict[str, Any]:
    """Build one synthetic worktree row."""
    row: dict[str, Any] = {
        'worktreeId': worktree_id,
        'repo': repo,
        'displayName': display_name,
        'isMainWorktree': is_main,
        'path': path,
        'isActive': is_active,
    }
    row.update(extra)
    return row


# === Pure parser contract ===


def test_parse_active_worktree_resolves_child_worktree() -> None:
    row = _make_row(
        worktree_id='wt-child',
        repo='project-a',
        display_name='feat-1',
        is_main=False,
    )
    payload = _make_payload([row])

    wt_id, attr = parse_active_worktree(payload)

    assert wt_id == 'wt-child'
    assert attr.repo == 'project-a'
    assert attr.worktree == 'feat-1'
    assert attr.label == 'project-a / feat-1'
    assert attr.is_main_worktree is False
    assert attr.schema_source == CLI_SCHEMA_SOURCE


def test_parse_active_worktree_resolves_main_worktree() -> None:
    row = _make_row(
        worktree_id='wt-main',
        repo='project-a',
        display_name='main',
        is_main=True,
    )
    payload = _make_payload([row])

    wt_id, attr = parse_active_worktree(payload)

    assert wt_id == 'wt-main'
    assert attr.repo == 'project-a'
    assert attr.worktree == 'main'
    assert attr.label == 'project-a'
    assert attr.is_main_worktree is True
    assert attr.schema_source == CLI_SCHEMA_SOURCE


def test_parse_active_worktree_rejects_zero_active_rows() -> None:
    row = _make_row(is_active=False)
    payload = _make_payload([row])

    with pytest.raises(
        NoActiveWorktreeError,
        match='No active worktree found',
    ):
        parse_active_worktree(payload)


def test_parse_active_worktree_rejects_multiple_active_rows() -> None:
    row_a = _make_row(worktree_id='wt-1', is_active=True)
    row_b = _make_row(worktree_id='wt-2', is_active=True)
    payload = _make_payload([row_a, row_b])

    with pytest.raises(
        MultipleActiveWorktreesError,
        match='Multiple active worktrees found',
    ):
        parse_active_worktree(payload)


def test_parse_active_worktree_rejects_ok_false() -> None:
    payload = _make_payload([_make_row()], ok=False)

    with pytest.raises(
        OrcaCliResponseError,
        match='Orca CLI returned ok=false',
    ):
        parse_active_worktree(payload)


@pytest.mark.parametrize(
    'bad_result',
    [None, 'not-a-dict', {}, {'worktrees': 'not-a-list'}],
    ids=['none', 'string', 'missing-worktrees', 'worktrees-not-list'],
)
def test_parse_active_worktree_rejects_absent_or_invalid_result_shape(
    bad_result: Any,
) -> None:
    payload = {'ok': True}
    if bad_result is not None:
        payload['result'] = bad_result

    with pytest.raises(
        MalformedCliResultError,
        match='Missing or invalid (result|worktrees)',
    ):
        parse_active_worktree(payload)


@pytest.mark.parametrize(
    'bad_repo',
    [None, '', '   ', 123],
    ids=['none', 'empty', 'blank', 'int'],
)
def test_parse_active_worktree_rejects_missing_or_blank_repo(
    bad_repo: Any,
) -> None:
    row = _make_row(repo=bad_repo)
    payload = _make_payload([row])

    with pytest.raises(
        MalformedWorktreeDataError,
        match='Missing or blank repo',
    ):
        parse_active_worktree(payload)


def test_parse_active_worktree_rejects_absolute_repo() -> None:
    row = _make_row(repo='/abs/path/repo')
    payload = _make_payload([row])

    with pytest.raises(
        MalformedWorktreeDataError,
        match='Absolute repository name',
    ):
        parse_active_worktree(payload)


def test_parse_active_worktree_falls_back_on_blank_display_name() -> None:
    row = _make_row(
        display_name='   ',
        path='/Users/hawkx/dev/my-project/worktrees/branch-fallback',
    )
    payload = _make_payload([row])

    _, attr = parse_active_worktree(payload)

    assert attr.worktree == 'branch-fallback'
    assert attr.label == 'my-repo / branch-fallback'


def test_parse_active_worktree_falls_back_on_absolute_display_name() -> None:
    row = _make_row(
        display_name='/Users/hawkx/dev/my-project/worktrees/abs-name',
        path='/Users/hawkx/dev/my-project/worktrees/fallback-name',
    )
    payload = _make_payload([row])

    _, attr = parse_active_worktree(payload)

    assert attr.worktree == 'fallback-name'


def test_parse_active_worktree_normalizes_to_nfc() -> None:
    decomposed_name = unicodedata.normalize('NFD', 'café')
    assert decomposed_name != unicodedata.normalize('NFC', 'café')

    row = _make_row(repo=decomposed_name, display_name=decomposed_name)
    payload = _make_payload([row])

    _, attr = parse_active_worktree(payload)

    assert attr.repo == unicodedata.normalize('NFC', 'café')
    assert attr.worktree == unicodedata.normalize('NFC', 'café')
    assert attr.label == unicodedata.normalize('NFC', 'café / café')


def test_parse_active_worktree_preserves_privacy_boundary() -> None:
    forbidden_content = 'super-secret-branch-output'
    forbidden_comment = 'do not leak this comment'
    row = _make_row(
        preview=forbidden_content,
        comment=forbidden_comment,
        branch='feature/confidential-pr',
        linkedIssue='#1234',
        linkedPR='#5678',
        agents=['agent-1'],
        status='running',
    )
    payload = _make_payload([row])

    _, attr = parse_active_worktree(payload)
    public_dict = attr.to_public_dict()

    expected_keys = {
        'repo',
        'worktree',
        'label',
        'is_main_worktree',
        'schema_source',
    }
    assert set(public_dict.keys()) == expected_keys
    for val in public_dict.values():
        val_str = str(val)
        assert forbidden_content not in val_str
        assert forbidden_comment not in val_str
        assert 'confidential' not in val_str


# === Subprocess runner contract ===


def test_run_orca_worktree_ps_executes_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_payload = {'ok': True, 'result': {'worktrees': []}}

    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(
            args=[DEFAULT_ORCA_BINARY_PATH, 'worktree', 'ps', '--json'],
            returncode=0,
            stdout='{"ok": true, "result": {"worktrees": []}}',
            stderr='',
        )
    )
    monkeypatch.setattr(subprocess, 'run', mock_run)

    result = run_orca_worktree_ps()

    assert result == expected_payload
    mock_run.assert_called_once_with(
        [DEFAULT_ORCA_BINARY_PATH, 'worktree', 'ps', '--json'],
        capture_output=True,
        text=True,
        timeout=5.0,
        shell=False,
    )


def test_run_orca_worktree_ps_raises_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args: Any, **kwargs: Any) -> Any:
        """Serve one fake run."""
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=5.0)

    monkeypatch.setattr(subprocess, 'run', fake_run)

    with pytest.raises(
        OrcaCliTimeoutError,
        match='Orca CLI timed out after 5.0s',
    ):
        run_orca_worktree_ps()


def test_run_orca_worktree_ps_raises_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(
            args=[DEFAULT_ORCA_BINARY_PATH, 'worktree', 'ps', '--json'],
            returncode=1,
            stdout='',
            stderr='orca daemon not running',
        )
    )
    monkeypatch.setattr(subprocess, 'run', mock_run)

    with pytest.raises(
        OrcaCliStatusError,
        match='Orca CLI returned status 1',
    ):
        run_orca_worktree_ps()


def test_run_orca_worktree_ps_raises_on_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(
            args=[DEFAULT_ORCA_BINARY_PATH, 'worktree', 'ps', '--json'],
            returncode=0,
            stdout='not valid json',
            stderr='',
        )
    )
    monkeypatch.setattr(subprocess, 'run', mock_run)

    with pytest.raises(
        OrcaCliPayloadError,
        match='Invalid Orca CLI JSON output',
    ):
        run_orca_worktree_ps()
