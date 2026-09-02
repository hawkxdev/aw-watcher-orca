"""Test Orca state probing."""

import json
from collections.abc import Iterator
from io import StringIO
from pathlib import Path

import pytest

import orca_state_probe as probe
from orca_state_probe import (
    OrcaStateError,
    StateDiscoveryError,
    UnsupportedSchemaError,
    discover_state_file,
    load_snapshot,
    main,
    run_probe,
    sanitize_identity,
)

# === Fixtures ===


def _state_payload() -> dict[str, object]:
    """Build an Orca fixture."""
    return {
        'schemaVersion': 1,
        'workspaceSession': {
            'activeWorktreeId': (
                'repo-123::/Users/example/orca/workspaces/repo/feature-a'
            ),
            'activeWorkspaceKey': (
                'worktree:repo-999::/Users/example/orca/workspaces/repo/other'
            ),
        },
    }


def _write_state(target_path: Path, payload: object) -> None:
    """Write an Orca fixture."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(json.dumps(payload), encoding='utf-8')


# === Snapshot contract ===


def test_load_snapshot_sanitizes_persisted_paths(tmp_path: Path) -> None:
    """Sanitize persisted worktree paths."""
    state_file = tmp_path / 'orca-data.json'
    _write_state(state_file, _state_payload())

    snapshot = load_snapshot(state_file)
    public = snapshot.to_public_dict(elapsed_ms=125)

    assert public == {
        'kind': 'snapshot',
        'elapsed_ms': 125,
        'schema_version': 1,
        'source_mtime_ns': state_file.stat().st_mtime_ns,
        'active_worktree': {
            'fingerprint': '3aaf6917cc53',
            'name': 'feature-a',
        },
        'active_workspace': {
            'fingerprint': '8c2b5696e000',
            'name': 'other',
        },
    }
    assert '/Users/example' not in json.dumps(public)


def test_load_snapshot_keeps_disagreeing_signals(tmp_path: Path) -> None:
    """Preserve disagreeing Orca signals."""
    state_file = tmp_path / 'orca-data.json'
    _write_state(state_file, _state_payload())

    snapshot = load_snapshot(state_file)

    assert snapshot.active_worktree.name == 'feature-a'
    assert snapshot.active_workspace.name == 'other'
    assert snapshot.active_worktree != snapshot.active_workspace


def test_load_snapshot_rejects_unsupported_schema(tmp_path: Path) -> None:
    """Reject unsupported Orca schemas."""
    state_file = tmp_path / 'orca-data.json'
    payload = _state_payload()
    payload['schemaVersion'] = 2
    _write_state(state_file, payload)

    with pytest.raises(
        UnsupportedSchemaError,
        match='Unsupported Orca schema version: 2',
    ):
        load_snapshot(state_file)


@pytest.mark.parametrize('schema_version', [True, 1.0])
def test_load_snapshot_rejects_non_integer_schema(
    tmp_path: Path,
    schema_version: object,
) -> None:
    """Reject noninteger schema versions."""
    state_file = tmp_path / 'orca-data.json'
    payload = _state_payload()
    payload['schemaVersion'] = schema_version
    _write_state(state_file, payload)

    with pytest.raises(
        UnsupportedSchemaError,
        match='Unsupported Orca schema version',
    ):
        load_snapshot(state_file)


@pytest.mark.parametrize(
    'missing_field',
    ['activeWorktreeId', 'activeWorkspaceKey'],
)
def test_load_snapshot_rejects_missing_session_field(
    tmp_path: Path,
    missing_field: str,
) -> None:
    """Reject incomplete workspace sessions."""
    state_file = tmp_path / 'orca-data.json'
    payload = _state_payload()
    workspace_session = payload['workspaceSession']
    assert isinstance(workspace_session, dict)
    del workspace_session[missing_field]
    _write_state(state_file, payload)

    with pytest.raises(
        OrcaStateError,
        match=f'Missing workspaceSession.{missing_field}',
    ):
        load_snapshot(state_file)


def test_load_snapshot_rejects_invalid_json(tmp_path: Path) -> None:
    """Reject malformed Orca JSON."""
    state_file = tmp_path / 'orca-data.json'
    state_file.write_text('{broken', encoding='utf-8')

    with pytest.raises(OrcaStateError, match='Invalid Orca state JSON'):
        load_snapshot(state_file)


def test_load_snapshot_rejects_invalid_utf8(tmp_path: Path) -> None:
    """Reject undecodable Orca state."""
    state_file = tmp_path / 'orca-data.json'
    state_file.write_bytes(b'\xff')

    with pytest.raises(OrcaStateError, match='Unable to decode Orca state'):
        load_snapshot(state_file)


def test_load_snapshot_rejects_concurrent_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject concurrent state changes."""
    state_file = tmp_path / 'orca-data.json'
    _write_state(state_file, _state_payload())
    changing_metadata = iter([(100, 200), (101, 200)])

    def fake_metadata(_: Path) -> tuple[int, int]:
        """Return changing file metadata."""
        return next(changing_metadata)

    monkeypatch.setattr(probe, '_state_metadata', fake_metadata)

    with pytest.raises(
        OrcaStateError,
        match='Orca state changed during read',
    ):
        load_snapshot(state_file)


def test_sanitize_identity_rejects_malformed_value() -> None:
    """Reject malformed worktree identities."""
    with pytest.raises(
        OrcaStateError,
        match='Invalid Orca worktree identity',
    ):
        sanitize_identity('missing-separator')


def test_sanitize_identity_normalizes_optional_prefix() -> None:
    """Normalize optional identity prefixes."""
    identity = 'repo-999::/Users/example/orca/workspaces/repo/other'

    assert sanitize_identity(f'worktree:{identity}') == sanitize_identity(
        identity
    )


# === Discovery contract ===


def test_discover_state_file_returns_unique_profile(tmp_path: Path) -> None:
    """Discover one Orca profile."""
    state_file = (
        tmp_path
        / 'Library'
        / 'Application Support'
        / 'orca'
        / 'profiles'
        / 'local-default'
        / 'orca-data.json'
    )
    _write_state(state_file, _state_payload())

    assert discover_state_file(tmp_path) == state_file


def test_discover_state_file_rejects_missing_profile(tmp_path: Path) -> None:
    """Reject absent Orca profiles."""
    with pytest.raises(
        StateDiscoveryError,
        match='No Orca state file found',
    ):
        discover_state_file(tmp_path)


def test_discover_state_file_rejects_ambiguous_profiles(
    tmp_path: Path,
) -> None:
    """Reject ambiguous Orca profiles."""
    profiles_dir = (
        tmp_path / 'Library' / 'Application Support' / 'orca' / 'profiles'
    )
    _write_state(profiles_dir / 'first' / 'orca-data.json', _state_payload())
    _write_state(profiles_dir / 'second' / 'orca-data.json', _state_payload())

    with pytest.raises(
        StateDiscoveryError,
        match='Multiple Orca state files found: 2',
    ):
        discover_state_file(tmp_path)


# === Command contract ===


@pytest.mark.parametrize('raw_value', ['nan', 'inf', '-inf'])
def test_parser_rejects_non_finite_duration(raw_value: str) -> None:
    """Reject non-finite probe durations."""
    parser = probe.build_parser()

    with pytest.raises(SystemExit) as raised:
        parser.parse_args(['--duration-seconds', raw_value])

    assert raised.value.code == 2


def test_main_once_emits_safe_snapshot(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Emit one safe snapshot."""
    state_file = tmp_path / 'orca-data.json'
    _write_state(state_file, _state_payload())

    exit_code = main(['--state-file', str(state_file), '--once'])
    captured = capsys.readouterr()
    output = json.loads(captured.out)

    assert exit_code == 0
    assert captured.err == ''
    assert output['active_worktree']['name'] == 'feature-a'
    assert output['active_workspace']['name'] == 'other'
    assert '/Users/example' not in captured.out


@pytest.mark.parametrize(
    'probe_args',
    [
        ['--marker', 'switch-to-b'],
        ['--duration-seconds', '1'],
        ['--interval-ms', '100'],
    ],
)
def test_main_once_rejects_probe_options(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    probe_args: list[str],
) -> None:
    """Reject conflicting probe options."""
    state_file = tmp_path / 'orca-data.json'
    _write_state(state_file, _state_payload())

    with pytest.raises(SystemExit) as raised:
        main(
            [
                '--state-file',
                str(state_file),
                '--once',
                *probe_args,
            ]
        )

    captured = capsys.readouterr()
    assert raised.value.code == 2
    assert '--once cannot be combined with probe options' in captured.err


def test_run_probe_emits_bounded_measurements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Emit bounded probe measurements."""
    state_file = tmp_path / 'orca-data.json'
    _write_state(state_file, _state_payload())
    monotonic_values = iter([0, 0, 1_000_000_000, 1_000_000_000])
    monkeypatch.setattr(
        probe.time,
        'monotonic_ns',
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(probe.time, 'sleep', lambda _: None)
    output_stream = StringIO()

    exit_code = run_probe(
        state_file=state_file,
        interval_ms=100,
        duration_seconds=1.0,
        marker='switch-to-b',
        output_stream=output_stream,
    )
    records = [
        json.loads(line) for line in output_stream.getvalue().splitlines()
    ]

    assert exit_code == 0
    assert [record['kind'] for record in records] == [
        'marker',
        'snapshot',
        'summary',
    ]
    assert records[-1] == {
        'kind': 'summary',
        'elapsed_ms': 1_000,
        'samples': 2,
        'observations': 1,
        'read_errors': 0,
    }


def test_run_probe_ignores_mtime_only_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ignore metadata-only state changes."""
    state_file = tmp_path / 'orca-data.json'
    _write_state(state_file, _state_payload())
    first_snapshot = load_snapshot(state_file)
    second_snapshot = probe.WorkspaceSnapshot(
        schema_version=first_snapshot.schema_version,
        source_mtime_ns=first_snapshot.source_mtime_ns + 1,
        active_worktree=first_snapshot.active_worktree,
        active_workspace=first_snapshot.active_workspace,
    )
    snapshots = iter([first_snapshot, second_snapshot])
    monotonic_values = iter([0, 0, 1_000_000_000, 1_000_000_000])
    monkeypatch.setattr(probe, 'load_snapshot', lambda _: next(snapshots))
    monkeypatch.setattr(
        probe.time,
        'monotonic_ns',
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(probe.time, 'sleep', lambda _: None)
    output_stream = StringIO()

    exit_code = run_probe(
        state_file=state_file,
        interval_ms=100,
        duration_seconds=1.0,
        marker=None,
        output_stream=output_stream,
    )
    records = [
        json.loads(line) for line in output_stream.getvalue().splitlines()
    ]

    assert exit_code == 0
    assert [record['kind'] for record in records] == ['snapshot', 'summary']
    assert records[-1]['observations'] == 1


@pytest.mark.parametrize('interval_ms', [0, -1])
def test_run_probe_rejects_nonpositive_interval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interval_ms: int,
) -> None:
    """Reject nonpositive probe intervals."""
    state_file = tmp_path / 'orca-data.json'
    _write_state(state_file, _state_payload())
    monotonic_values = iter([0, 0, 1_000_000_000, 1_000_000_000])
    monkeypatch.setattr(
        probe.time,
        'monotonic_ns',
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(probe.time, 'sleep', lambda _: None)

    with pytest.raises(ValueError, match='interval_ms must be positive'):
        run_probe(
            state_file=state_file,
            interval_ms=interval_ms,
            duration_seconds=1.0,
            marker=None,
            output_stream=StringIO(),
        )


@pytest.mark.parametrize(
    'duration_seconds',
    [0.0, -1.0, float('nan'), float('inf')],
)
def test_run_probe_rejects_invalid_duration(
    tmp_path: Path,
    duration_seconds: float,
) -> None:
    """Reject invalid probe durations."""
    state_file = tmp_path / 'orca-data.json'
    _write_state(state_file, _state_payload())

    with pytest.raises(
        ValueError,
        match='duration_seconds must be finite and positive',
    ):
        run_probe(
            state_file=state_file,
            interval_ms=100,
            duration_seconds=duration_seconds,
            marker=None,
            output_stream=StringIO(),
        )


def test_run_probe_reports_transient_read_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report transient read failures."""
    state_file = tmp_path / 'orca-data.json'
    _write_state(state_file, _state_payload())
    valid_snapshot = load_snapshot(state_file)
    probe_results: Iterator[OrcaStateError | probe.WorkspaceSnapshot] = iter(
        [
            OrcaStateError('Orca state changed during read'),
            valid_snapshot,
        ]
    )
    monotonic_values = iter([0, 0, 1_000_000_000, 1_000_000_000])

    def fake_load(_: Path) -> probe.WorkspaceSnapshot:
        """Return changing probe results."""
        result = next(probe_results)
        if isinstance(result, OrcaStateError):
            raise result
        return result

    monkeypatch.setattr(probe, 'load_snapshot', fake_load)
    monkeypatch.setattr(
        probe.time,
        'monotonic_ns',
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(probe.time, 'sleep', lambda _: None)
    output_stream = StringIO()

    exit_code = run_probe(
        state_file=state_file,
        interval_ms=100,
        duration_seconds=1.0,
        marker=None,
        output_stream=output_stream,
    )
    records = [
        json.loads(line) for line in output_stream.getvalue().splitlines()
    ]

    assert exit_code == 0
    assert [record['kind'] for record in records] == [
        'read_error',
        'snapshot',
        'summary',
    ]
    assert records[0]['message'] == 'Orca state changed during read'
    assert records[-1]['read_errors'] == 1


def test_run_probe_suppresses_repeated_errors_and_returns_two(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Suppress repeated probe errors."""
    state_file = tmp_path / 'orca-data.json'
    _write_state(state_file, _state_payload())
    monotonic_values = iter([0, 0, 1_000_000_000, 1_000_000_000])

    def fail_load(_: Path) -> probe.WorkspaceSnapshot:
        """Raise one repeated error."""
        raise OrcaStateError('Orca state changed during read')

    monkeypatch.setattr(probe, 'load_snapshot', fail_load)
    monkeypatch.setattr(
        probe.time,
        'monotonic_ns',
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(probe.time, 'sleep', lambda _: None)
    output_stream = StringIO()

    exit_code = run_probe(
        state_file=state_file,
        interval_ms=100,
        duration_seconds=1.0,
        marker=None,
        output_stream=output_stream,
    )
    records = [
        json.loads(line) for line in output_stream.getvalue().splitlines()
    ]

    assert exit_code == 2
    assert [record['kind'] for record in records] == [
        'read_error',
        'summary',
    ]
    assert records[-1]['observations'] == 0
    assert records[-1]['read_errors'] == 2


def test_main_hides_failed_state_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Hide failed state paths."""
    missing_file = tmp_path / 'private-location' / 'orca-data.json'

    exit_code = main(['--state-file', str(missing_file), '--once'])
    captured = capsys.readouterr()
    output = json.loads(captured.err)

    assert exit_code == 2
    assert captured.out == ''
    assert output == {
        'kind': 'fatal_error',
        'error': 'OrcaStateError',
        'message': 'Unable to read Orca state file',
    }
    assert str(missing_file) not in captured.err
