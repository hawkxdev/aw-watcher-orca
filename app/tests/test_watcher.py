"""Test watcher polling loop."""

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import pytest

import aw_watcher_orca.watcher as watcher_module
from aw_watcher_orca.bucket_target import (
    PRODUCTION_BUCKET_TARGET,
    TEST_BUCKET_TARGET,
    BucketTargetProfile,
    ConfirmedBucketTarget,
    confirm_bucket_target,
)
from aw_watcher_orca.errors import (
    ActivityWatchConnectionError,
    MalformedStateError,
    OrcaCliStatusError,
)
from aw_watcher_orca.instance_lock import acquire_instance_lock
from aw_watcher_orca.watcher import main, run_watcher_loop

# === Constants ===


REFERENCE_TIME = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)


# === Helpers ===


def confirmed_target(
    profile: BucketTargetProfile,
    host_suffix: str,
) -> ConfirmedBucketTarget:
    """Build the target the publisher returns after a metadata match."""
    return confirm_bucket_target(
        profile,
        host_suffix,
        {
            'client': profile.client,
            'type': profile.bucket_type,
            'hostname': host_suffix,
        },
    )


# === Loop tests ===


def test_loop_publishes_on_every_tick_when_state_unchanged(
    tmp_path: Path,
) -> None:
    sent_heartbeats: list[dict[str, Any]] = []
    cli_calls = 0

    def fake_bucket_reader(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return fake bucket mapping."""
        return {
            'aw-watcher-window_host-a': {
                'type': 'currentwindow',
                'last_updated': REFERENCE_TIME.isoformat(),
            },
            'aw-watcher-afk_host-a': {
                'type': 'afkstatus',
                'last_updated': REFERENCE_TIME.isoformat(),
            },
        }

    def fake_event_reader(bucket_id: str) -> dict[str, Any]:
        """Return fake window event."""
        return {
            'timestamp': REFERENCE_TIME.isoformat(),
            'duration': 5.0,
            'data': {'app': 'Orca', 'title': 'my-repo / feat-1'},
        }

    def fake_cli_runner() -> dict[str, Any]:
        """Return fake CLI payload."""
        nonlocal cli_calls
        cli_calls += 1
        return {
            'ok': True,
            'result': {
                'worktrees': [
                    {
                        'worktreeId': 'wt-1',
                        'repo': 'my-repo',
                        'displayName': 'feat-1',
                        'isMainWorktree': False,
                        'path': '/path/to/feat-1',
                        'isActive': True,
                    }
                ]
            },
        }

    def fake_heartbeat_sender(
        target: ConfirmedBucketTarget,
        payload: Mapping[str, Any],
        pulse_time: float,
    ) -> None:
        """Record fake heartbeat payload."""
        sent_heartbeats.append(dict(payload))

    exit_code = run_watcher_loop(
        profile=TEST_BUCKET_TARGET,
        max_iterations=5,
        profile_path=tmp_path / 'orca-data.json',
        bucket_reader=fake_bucket_reader,
        event_reader=fake_event_reader,
        bucket_creator=confirmed_target,
        heartbeat_sender=fake_heartbeat_sender,
        cli_runner=fake_cli_runner,
        trigger_reader=lambda p: 'wt-1',
        clock=lambda: REFERENCE_TIME,
        sleep=lambda s: None,
    )

    assert exit_code == 0
    assert len(sent_heartbeats) == 5
    assert cli_calls == 2
    assert sent_heartbeats[0]['data']['title'] == ''
    for hb in sent_heartbeats[1:]:
        assert hb['data']['title'] == 'my-repo / feat-1'


def test_loop_publishes_new_attribution_only_after_stability(
    tmp_path: Path,
) -> None:
    sent_heartbeats: list[dict[str, Any]] = []
    tick = 0

    def fake_bucket_reader(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return fake bucket mapping."""
        return {
            'aw-watcher-window_host-a': {
                'type': 'currentwindow',
                'last_updated': REFERENCE_TIME.isoformat(),
            },
            'aw-watcher-afk_host-a': {
                'type': 'afkstatus',
                'last_updated': REFERENCE_TIME.isoformat(),
            },
        }

    def fake_event_reader(bucket_id: str) -> dict[str, Any]:
        """Return fake window event."""
        return {
            'timestamp': REFERENCE_TIME.isoformat(),
            'duration': 5.0,
            'data': {'app': 'Orca'},
        }

    def fake_trigger_reader(path: Path) -> str:
        """Return fake trigger identifier."""
        return 'wt-1' if tick < 2 else 'wt-2'

    def fake_cli_runner() -> dict[str, Any]:
        """Return fake CLI payload."""
        wt_id = 'wt-1' if tick < 2 else 'wt-2'
        name = 'feat-1' if tick < 2 else 'feat-2'
        return {
            'ok': True,
            'result': {
                'worktrees': [
                    {
                        'worktreeId': wt_id,
                        'repo': 'my-repo',
                        'displayName': name,
                        'isMainWorktree': False,
                        'path': f'/path/{name}',
                        'isActive': True,
                    }
                ]
            },
        }

    def fake_heartbeat_sender(
        target: ConfirmedBucketTarget,
        payload: Mapping[str, Any],
        pulse_time: float,
    ) -> None:
        """Record fake heartbeat payload."""
        sent_heartbeats.append(dict(payload))

    def fake_sleep(s: float) -> None:
        """Skip fake polling delay."""
        nonlocal tick
        tick += 1

    run_watcher_loop(
        profile=TEST_BUCKET_TARGET,
        max_iterations=4,
        profile_path=tmp_path / 'orca-data.json',
        bucket_reader=fake_bucket_reader,
        event_reader=fake_event_reader,
        bucket_creator=confirmed_target,
        heartbeat_sender=fake_heartbeat_sender,
        cli_runner=fake_cli_runner,
        trigger_reader=fake_trigger_reader,
        clock=lambda: REFERENCE_TIME,
        sleep=fake_sleep,
    )

    assert len(sent_heartbeats) == 4
    assert sent_heartbeats[0]['data']['title'] == ''
    assert sent_heartbeats[1]['data']['title'] == 'my-repo / feat-1'
    assert sent_heartbeats[2]['data']['title'] == 'my-repo / feat-1'
    assert sent_heartbeats[3]['data']['title'] == 'my-repo / feat-2'


def test_loop_not_in_foreground_never_touches_orca_sources(
    tmp_path: Path,
) -> None:
    sent_heartbeats: list[dict[str, Any]] = []
    trigger_called = False
    cli_called = False

    def fake_bucket_reader(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return fake bucket mapping."""
        return {
            'aw-watcher-window_host-a': {
                'type': 'currentwindow',
                'last_updated': REFERENCE_TIME.isoformat(),
            },
            'aw-watcher-afk_host-a': {
                'type': 'afkstatus',
                'last_updated': REFERENCE_TIME.isoformat(),
            },
        }

    def fake_event_reader(bucket_id: str) -> dict[str, Any]:
        """Return fake window event."""
        return {
            'timestamp': REFERENCE_TIME.isoformat(),
            'duration': 5.0,
            'data': {'app': 'Browser', 'title': 'Web Page'},
        }

    def fake_trigger_reader(p: Path) -> str:
        """Return fake trigger identifier."""
        nonlocal trigger_called
        trigger_called = True
        return 'wt-1'

    def fake_cli_runner() -> dict[str, Any]:
        """Return fake CLI payload."""
        nonlocal cli_called
        cli_called = True
        return {}

    def fake_heartbeat_sender(
        target: ConfirmedBucketTarget,
        payload: Mapping[str, Any],
        pulse_time: float,
    ) -> None:
        """Record fake heartbeat payload."""
        sent_heartbeats.append(dict(payload))

    exit_code = run_watcher_loop(
        profile=TEST_BUCKET_TARGET,
        max_iterations=3,
        profile_path=tmp_path / 'orca-data.json',
        bucket_reader=fake_bucket_reader,
        event_reader=fake_event_reader,
        bucket_creator=confirmed_target,
        heartbeat_sender=fake_heartbeat_sender,
        cli_runner=fake_cli_runner,
        trigger_reader=fake_trigger_reader,
        clock=lambda: REFERENCE_TIME,
        sleep=lambda s: None,
    )

    assert exit_code == 0
    assert len(sent_heartbeats) == 3
    assert not trigger_called
    assert not cli_called
    for hb in sent_heartbeats:
        assert hb['data']['title'] == ''
        assert hb['data']['repo'] == ''
        assert hb['data']['worktree'] == ''


@pytest.mark.parametrize(
    'failure_source',
    ['event_reader', 'trigger_reader', 'cli_runner'],
)
def test_loop_source_failure_publishes_neutral_event(
    tmp_path: Path,
    failure_source: str,
) -> None:
    sent_heartbeats: list[dict[str, Any]] = []

    def fake_bucket_reader(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return fake bucket mapping."""
        return {
            'aw-watcher-window_host-a': {
                'type': 'currentwindow',
                'last_updated': REFERENCE_TIME.isoformat(),
            },
            'aw-watcher-afk_host-a': {
                'type': 'afkstatus',
                'last_updated': REFERENCE_TIME.isoformat(),
            },
        }

    def fake_event_reader(bucket_id: str) -> dict[str, Any]:
        """Return fake window event."""
        if failure_source == 'event_reader':
            raise ActivityWatchConnectionError('offline')
        return {
            'timestamp': REFERENCE_TIME.isoformat(),
            'duration': 5.0,
            'data': {'app': 'Orca'},
        }

    def fake_trigger_reader(p: Path) -> str:
        """Return fake trigger identifier."""
        if failure_source == 'trigger_reader':
            raise MalformedStateError('bad json')
        return 'wt-1'

    def fake_cli_runner() -> dict[str, Any]:
        """Return fake CLI payload."""
        if failure_source == 'cli_runner':
            raise OrcaCliStatusError('exit 1')
        return {
            'ok': True,
            'result': {
                'worktrees': [
                    {
                        'worktreeId': 'wt-1',
                        'repo': 'my-repo',
                        'displayName': 'feat-1',
                        'isMainWorktree': False,
                        'path': '/path/feat-1',
                        'isActive': True,
                    }
                ]
            },
        }

    def fake_heartbeat_sender(
        target: ConfirmedBucketTarget,
        payload: Mapping[str, Any],
        pulse_time: float,
    ) -> None:
        """Record fake heartbeat payload."""
        sent_heartbeats.append(dict(payload))

    exit_code = run_watcher_loop(
        profile=TEST_BUCKET_TARGET,
        max_iterations=2,
        profile_path=tmp_path / 'orca-data.json',
        bucket_reader=fake_bucket_reader,
        event_reader=fake_event_reader,
        bucket_creator=confirmed_target,
        heartbeat_sender=fake_heartbeat_sender,
        cli_runner=fake_cli_runner,
        trigger_reader=fake_trigger_reader,
        clock=lambda: REFERENCE_TIME,
        sleep=lambda s: None,
    )

    assert exit_code == 0
    assert len(sent_heartbeats) == 2
    for hb in sent_heartbeats:
        assert hb['data']['title'] == ''


@pytest.mark.parametrize(
    ('failure_source', 'expected_titles'),
    [
        pytest.param(
            'event_reader',
            ['', 'my-repo', '', '', 'my-repo', 'my-repo'],
            id='event-reader',
        ),
        pytest.param(
            'trigger_reader',
            ['', 'my-repo', '', '', 'my-repo', 'my-repo'],
            id='trigger-reader',
        ),
        pytest.param(
            'heartbeat_sender',
            ['', 'my-repo', '', '', 'my-repo', 'my-repo'],
            id='heartbeat-sender',
        ),
        pytest.param(
            'activitywatch_outage',
            ['', 'my-repo', '', 'my-repo', 'my-repo'],
            id='activitywatch-outage',
        ),
    ],
)
def test_loop_recovers_same_attribution_after_transient_failure(
    tmp_path: Path,
    failure_source: str,
    expected_titles: list[str],
) -> None:
    sent_titles: list[str] = []
    tick = 0
    heartbeat_failed = False

    def fake_bucket_reader(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return fake bucket mapping."""
        return {
            'aw-watcher-window_host-a': {
                'type': 'currentwindow',
                'last_updated': REFERENCE_TIME.isoformat(),
            },
            'aw-watcher-afk_host-a': {
                'type': 'afkstatus',
                'last_updated': REFERENCE_TIME.isoformat(),
            },
        }

    def fake_event_reader(bucket_id: str) -> dict[str, Any]:
        """Fail foreground event read."""
        if (
            failure_source in {'event_reader', 'activitywatch_outage'}
            and tick == 2
        ):
            raise ActivityWatchConnectionError('controlled outage')
        return {
            'timestamp': REFERENCE_TIME.isoformat(),
            'duration': 5.0,
            'data': {'app': 'Orca'},
        }

    def fake_trigger_reader(path: Path) -> str:
        """Fail profile trigger read."""
        if failure_source == 'trigger_reader' and tick == 2:
            raise MalformedStateError('controlled invalid state')
        return 'wt-1'

    def fake_cli_runner() -> dict[str, Any]:
        """Return stable active worktree."""
        return {
            'ok': True,
            'result': {
                'worktrees': [
                    {
                        'worktreeId': 'wt-1',
                        'repo': 'my-repo',
                        'displayName': 'main',
                        'isMainWorktree': True,
                        'path': '/path/to/main',
                        'isActive': True,
                    }
                ]
            },
        }

    def fake_heartbeat_sender(
        target: ConfirmedBucketTarget,
        payload: Mapping[str, Any],
        pulse_time: float,
    ) -> None:
        """Fail heartbeat then recover."""
        nonlocal heartbeat_failed
        if failure_source == 'activitywatch_outage' and tick == 2:
            raise ActivityWatchConnectionError('controlled outage')
        if (
            failure_source == 'heartbeat_sender'
            and tick == 2
            and not heartbeat_failed
        ):
            heartbeat_failed = True
            raise ActivityWatchConnectionError('controlled outage')
        sent_titles.append(payload['data']['title'])

    def fake_sleep(seconds: float) -> None:
        """Advance synthetic polling tick."""
        nonlocal tick
        tick += 1

    exit_code = run_watcher_loop(
        profile=TEST_BUCKET_TARGET,
        max_iterations=6,
        profile_path=tmp_path / 'orca-data.json',
        bucket_reader=fake_bucket_reader,
        event_reader=fake_event_reader,
        bucket_creator=confirmed_target,
        heartbeat_sender=fake_heartbeat_sender,
        cli_runner=fake_cli_runner,
        trigger_reader=fake_trigger_reader,
        clock=lambda: REFERENCE_TIME,
        sleep=fake_sleep,
    )

    assert exit_code == 0
    assert sent_titles == expected_titles


def test_loop_failure_logs_do_not_include_exception_messages(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    forbidden_message = 'secret /Users/private prompt branch'
    bucket_reads = 0

    def fake_bucket_reader(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Fail rediscovery with secret."""
        nonlocal bucket_reads
        bucket_reads += 1
        if bucket_reads > 1:
            raise ActivityWatchConnectionError(forbidden_message)
        return {
            'aw-watcher-window_host-a': {
                'type': 'currentwindow',
                'last_updated': REFERENCE_TIME.isoformat(),
            },
            'aw-watcher-afk_host-a': {
                'type': 'afkstatus',
                'last_updated': REFERENCE_TIME.isoformat(),
            },
        }

    def fail_event_reader(bucket_id: str) -> dict[str, Any]:
        """Raise sensitive fake error."""
        raise ActivityWatchConnectionError(forbidden_message)

    with caplog.at_level('WARNING'):
        run_watcher_loop(
            profile=TEST_BUCKET_TARGET,
            max_iterations=2,
            profile_path=tmp_path / 'orca-data.json',
            bucket_reader=fake_bucket_reader,
            event_reader=fail_event_reader,
            bucket_creator=confirmed_target,
            heartbeat_sender=lambda *args: None,
            cli_runner=lambda: {},
            trigger_reader=lambda path: 'wt-1',
            clock=lambda: REFERENCE_TIME,
            sleep=lambda seconds: None,
        )

    assert caplog.text.count('ActivityWatchConnectionError') == 2
    assert forbidden_message not in caplog.text


def test_loop_rediscovers_bucket_pair_after_activitywatch_outage(
    tmp_path: Path,
) -> None:
    bucket_reads = 0
    tick = 0
    created_suffixes: list[str] = []
    event_bucket_ids: list[str] = []
    sent_heartbeats: list[tuple[str, str]] = []

    def pair_buckets(host_suffix: str) -> dict[str, dict[str, object]]:
        """Build synthetic bucket pair."""
        return {
            f'aw-watcher-window_{host_suffix}': {
                'type': 'currentwindow',
                'last_updated': REFERENCE_TIME.isoformat(),
            },
            f'aw-watcher-afk_{host_suffix}': {
                'type': 'afkstatus',
                'last_updated': REFERENCE_TIME.isoformat(),
            },
        }

    def fake_bucket_reader(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return staged bucket discovery."""
        nonlocal bucket_reads
        bucket_reads += 1
        if bucket_reads == 1:
            return pair_buckets('host-a')
        if bucket_reads == 2:
            return pair_buckets('host-a') | pair_buckets('host-b')
        return pair_buckets('host-b')

    def fake_event_reader(bucket_id: str) -> dict[str, Any]:
        """Fail original bucket read."""
        event_bucket_ids.append(bucket_id)
        if tick == 2:
            raise ActivityWatchConnectionError('controlled restart')
        return {
            'timestamp': REFERENCE_TIME.isoformat(),
            'duration': 5.0,
            'data': {'app': 'Orca'},
        }

    def fake_cli_runner() -> dict[str, Any]:
        """Return stable active worktree."""
        return {
            'ok': True,
            'result': {
                'worktrees': [
                    {
                        'worktreeId': 'wt-1',
                        'repo': 'my-repo',
                        'displayName': 'main',
                        'isMainWorktree': True,
                        'path': '/path/to/main',
                        'isActive': True,
                    }
                ]
            },
        }

    def fake_heartbeat_sender(
        target: ConfirmedBucketTarget,
        payload: Mapping[str, Any],
        pulse_time: float,
    ) -> None:
        """Fail outage heartbeat attempts."""
        if tick == 2:
            raise ActivityWatchConnectionError('controlled restart')
        sent_heartbeats.append((target.bucket_id, payload['data']['title']))

    def fake_sleep(seconds: float) -> None:
        """Advance synthetic polling tick."""
        nonlocal tick
        tick += 1

    def recording_creator(
        profile: BucketTargetProfile,
        host_suffix: str,
    ) -> ConfirmedBucketTarget:
        """Record the created suffix and confirm the target."""
        created_suffixes.append(host_suffix)
        return confirmed_target(profile, host_suffix)

    exit_code = run_watcher_loop(
        profile=TEST_BUCKET_TARGET,
        max_iterations=7,
        profile_path=tmp_path / 'orca-data.json',
        bucket_reader=fake_bucket_reader,
        event_reader=fake_event_reader,
        bucket_creator=recording_creator,
        heartbeat_sender=fake_heartbeat_sender,
        cli_runner=fake_cli_runner,
        trigger_reader=lambda path: 'wt-1',
        clock=lambda: REFERENCE_TIME,
        sleep=fake_sleep,
    )

    assert exit_code == 0
    assert bucket_reads == 3
    assert created_suffixes == ['host-a', 'host-b']
    assert event_bucket_ids == [
        'aw-watcher-window_host-a',
        'aw-watcher-window_host-a',
        'aw-watcher-window_host-a',
        'aw-watcher-window_host-b',
        'aw-watcher-window_host-b',
        'aw-watcher-window_host-b',
    ]
    assert sent_heartbeats == [
        ('aw-watcher-orca-test_host-a', ''),
        ('aw-watcher-orca-test_host-a', 'my-repo'),
        ('aw-watcher-orca-test_host-b', ''),
        ('aw-watcher-orca-test_host-b', 'my-repo'),
        ('aw-watcher-orca-test_host-b', 'my-repo'),
    ]


def test_loop_startup_failure_exits_nonzero(tmp_path: Path) -> None:
    def fail_bucket_reader(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Raise fake bucket failure."""
        return {}

    exit_code = run_watcher_loop(
        profile=TEST_BUCKET_TARGET,
        max_iterations=1,
        profile_path=tmp_path / 'orca-data.json',
        bucket_reader=fail_bucket_reader,
        event_reader=lambda id: None,
        bucket_creator=confirmed_target,
        heartbeat_sender=lambda *args: None,
        cli_runner=lambda: {},
        trigger_reader=lambda p: 'wt-1',
        clock=lambda: REFERENCE_TIME,
        sleep=lambda s: None,
    )

    assert exit_code == 1


def test_loop_handles_keyboard_interrupt(tmp_path: Path) -> None:
    def fake_bucket_reader(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return fake bucket mapping."""
        return {
            'aw-watcher-window_host-a': {
                'type': 'currentwindow',
                'last_updated': REFERENCE_TIME.isoformat(),
            },
            'aw-watcher-afk_host-a': {
                'type': 'afkstatus',
                'last_updated': REFERENCE_TIME.isoformat(),
            },
        }

    def interrupt_sleep(s: float) -> None:
        """Interrupt fake polling sleep."""
        raise KeyboardInterrupt

    exit_code = run_watcher_loop(
        profile=TEST_BUCKET_TARGET,
        max_iterations=5,
        profile_path=tmp_path / 'orca-data.json',
        bucket_reader=fake_bucket_reader,
        event_reader=lambda id: None,
        bucket_creator=confirmed_target,
        heartbeat_sender=lambda *args: None,
        cli_runner=lambda: {},
        trigger_reader=lambda p: 'wt-1',
        clock=lambda: REFERENCE_TIME,
        sleep=interrupt_sleep,
    )

    assert exit_code == 0


# === Logging contract ===


def test_log_handler_uses_bounded_rotation(tmp_path: Path) -> None:
    handler = watcher_module.build_log_handler(
        tmp_path / 'logs' / 'watcher.log'
    )

    try:
        assert isinstance(handler, RotatingFileHandler)
        assert handler.maxBytes == watcher_module.DEFAULT_LOG_MAX_BYTES
        assert watcher_module.DEFAULT_LOG_MAX_BYTES == 1_048_576
        assert handler.backupCount == watcher_module.DEFAULT_LOG_BACKUP_COUNT
        assert watcher_module.DEFAULT_LOG_BACKUP_COUNT == 3
        assert handler.encoding == 'utf-8'
    finally:
        handler.close()


def test_log_handler_creates_private_paths(tmp_path: Path) -> None:
    log_path = tmp_path / 'logs' / 'watcher.log'
    handler = watcher_module.build_log_handler(log_path)

    try:
        assert log_path.parent.stat().st_mode & 0o777 == 0o700
        assert log_path.stat().st_mode & 0o777 == 0o600
    finally:
        handler.close()


def test_log_handler_preserves_existing_directory_mode(tmp_path: Path) -> None:
    log_dir = tmp_path / 'logs'
    log_dir.mkdir(mode=0o755)
    log_path = log_dir / 'watcher.log'

    handler = watcher_module.build_log_handler(log_path)

    try:
        assert log_dir.stat().st_mode & 0o777 == 0o755
        assert log_path.stat().st_mode & 0o777 == 0o600
    finally:
        handler.close()


@pytest.mark.parametrize('symlink_kind', ['file', 'directory'])
def test_log_handler_rejects_symlink_paths(
    tmp_path: Path,
    symlink_kind: str,
) -> None:
    real_dir = tmp_path / 'real'
    real_dir.mkdir()
    if symlink_kind == 'file':
        protected_file = real_dir / 'protected.log'
        protected_file.write_text('protected', encoding='utf-8')
        log_path = tmp_path / 'watcher.log'
        log_path.symlink_to(protected_file)
    else:
        linked_dir = tmp_path / 'linked'
        linked_dir.symlink_to(real_dir, target_is_directory=True)
        log_path = linked_dir / 'watcher.log'

    try:
        handler = watcher_module.build_log_handler(log_path)
    except ValueError as error:
        assert str(error) == 'log path cannot use symlinks'
    else:
        handler.close()
        pytest.fail('symlink log path was accepted')


def test_log_handler_rotates_at_configured_boundary(tmp_path: Path) -> None:
    log_path = tmp_path / 'watcher.log'
    handler = watcher_module.build_log_handler(
        log_path,
        max_bytes=64,
        backup_count=1,
    )
    record = logging.LogRecord(
        name='test',
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg='x' * 80,
        args=(),
        exc_info=None,
    )

    try:
        handler.emit(record)
        handler.emit(record)
    finally:
        handler.close()

    assert log_path.exists()
    assert log_path.with_name('watcher.log.1').exists()
    assert log_path.stat().st_mode & 0o777 == 0o600
    assert log_path.with_name('watcher.log.1').stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    ('max_bytes', 'backup_count'),
    [(0, 1), (1, 0), (True, 1), (1, True)],
)
def test_log_handler_rejects_invalid_rotation_limits(
    tmp_path: Path,
    max_bytes: Any,
    backup_count: Any,
) -> None:
    with pytest.raises(ValueError, match='rotation limits must be positive'):
        watcher_module.build_log_handler(
            tmp_path / 'watcher.log',
            max_bytes=max_bytes,
            backup_count=backup_count,
        )


def test_main_accepts_log_configuration_with_a_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_handlers: list[logging.Handler | None] = []
    selected_profiles: list[BucketTargetProfile] = []
    log_path = tmp_path / 'watcher.log'

    def fake_configure_logging(
        selected_path: Path | None,
    ) -> logging.Handler | None:
        """Record selected log path."""
        configured_handlers.append(
            None if selected_path is None else logging.NullHandler()
        )
        assert selected_path == log_path
        return configured_handlers[-1]

    def fake_loop(*, profile: BucketTargetProfile) -> int:
        """Record the profile the entry point selected."""
        selected_profiles.append(profile)
        return 0

    monkeypatch.setattr(
        'aw_watcher_orca.watcher.configure_logging',
        fake_configure_logging,
    )
    monkeypatch.setattr('aw_watcher_orca.watcher.run_watcher_loop', fake_loop)

    exit_code = main(
        ['--mode', 'test', '--log-file', str(log_path)],
        lock_path=tmp_path / 'watcher.lock',
    )

    assert exit_code == 0
    assert len(configured_handlers) == 1
    assert selected_profiles == [TEST_BUCKET_TARGET]


def test_main_requires_an_explicit_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing mode must be refused before anything else happens."""
    loop_calls = 0

    def counting_loop(*, profile: BucketTargetProfile) -> int:
        """Count every entry into the polling loop."""
        nonlocal loop_calls
        loop_calls += 1
        return 0

    monkeypatch.setattr(
        'aw_watcher_orca.watcher.run_watcher_loop',
        counting_loop,
    )

    with pytest.raises(SystemExit, match='2'):
        main(
            ['--log-file', str(tmp_path / 'watcher.log')],
            lock_path=tmp_path / 'watcher.lock',
        )

    assert loop_calls == 0


def test_main_rejects_a_mode_outside_the_closed_set(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match='2'):
        main(['--mode', 'staging'], lock_path=tmp_path / 'watcher.lock')


def test_main_selects_the_production_profile_on_demand(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_profiles: list[BucketTargetProfile] = []

    def fake_loop(*, profile: BucketTargetProfile) -> int:
        """Record the profile the entry point selected."""
        selected_profiles.append(profile)
        return 0

    monkeypatch.setattr('aw_watcher_orca.watcher.run_watcher_loop', fake_loop)

    exit_code = main(
        ['--mode', 'production'],
        lock_path=tmp_path / 'watcher.lock',
    )

    assert exit_code == 0
    assert selected_profiles == [PRODUCTION_BUCKET_TARGET]


def test_main_refuses_to_start_while_the_lock_is_held(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop_calls = 0
    lock_path = tmp_path / 'watcher.lock'

    def counting_loop(*, profile: BucketTargetProfile) -> int:
        """Count every entry into the polling loop."""
        nonlocal loop_calls
        loop_calls += 1
        return 0

    monkeypatch.setattr(
        'aw_watcher_orca.watcher.run_watcher_loop',
        counting_loop,
    )

    holder = acquire_instance_lock(lock_path)
    try:
        exit_code = main(['--mode', 'test'], lock_path=lock_path)
    finally:
        holder.release()

    assert exit_code == 1
    assert loop_calls == 0


def test_main_runs_the_loop_when_the_lock_is_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop_calls = 0
    lock_path = tmp_path / 'watcher.lock'

    def counting_loop(*, profile: BucketTargetProfile) -> int:
        """Count every entry into the polling loop."""
        nonlocal loop_calls
        loop_calls += 1
        return 0

    monkeypatch.setattr(
        'aw_watcher_orca.watcher.run_watcher_loop',
        counting_loop,
    )

    exit_code = main(['--mode', 'test'], lock_path=lock_path)

    assert exit_code == 0
    assert loop_calls == 1


def test_main_releases_the_lock_when_the_loop_returns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / 'watcher.lock'
    monkeypatch.setattr(
        'aw_watcher_orca.watcher.run_watcher_loop',
        lambda *, profile: 0,
    )

    assert main(['--mode', 'test'], lock_path=lock_path) == 0

    successor = acquire_instance_lock(lock_path)
    successor.release()


def test_main_releases_the_lock_when_the_loop_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / 'watcher.lock'

    def failing_loop(*, profile: BucketTargetProfile) -> int:
        """Fail the polling loop."""
        raise MalformedStateError('broken state')

    monkeypatch.setattr(
        'aw_watcher_orca.watcher.run_watcher_loop',
        failing_loop,
    )

    with pytest.raises(MalformedStateError):
        main(['--mode', 'test'], lock_path=lock_path)

    successor = acquire_instance_lock(lock_path)
    successor.release()


def test_startup_log_line_names_the_selected_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        'aw_watcher_orca.watcher.run_watcher_loop',
        lambda *, profile: 0,
    )
    monkeypatch.setattr(
        'aw_watcher_orca.watcher.configure_logging',
        lambda selected_path: None,
    )

    with caplog.at_level(logging.INFO, logger='aw_watcher_orca.watcher'):
        main(['--mode', 'production'], lock_path=tmp_path / 'watcher.lock')

    assert 'production' in caplog.text
    assert 'test bucket mode' not in caplog.text


def test_main_rejects_bucket_configuration() -> None:
    with pytest.raises(SystemExit, match='2'):
        watcher_module.build_argument_parser().parse_args(
            ['--bucket-prefix', 'aw-watcher-orca']
        )


# === Mode threading ===


def test_production_profile_survives_a_host_suffix_change(
    tmp_path: Path,
) -> None:
    """The mode must not fall back to test on rediscovery."""
    sent_targets: list[str] = []
    created_clients: list[str] = []
    bucket_reads = 0
    tick = 0

    def fake_bucket_reader(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Serve host-a first and host-b after the outage."""
        nonlocal bucket_reads
        bucket_reads += 1
        suffix = 'host-a' if bucket_reads == 1 else 'host-b'
        return {
            f'aw-watcher-window_{suffix}': {
                'type': 'currentwindow',
                'last_updated': REFERENCE_TIME.isoformat(),
            },
            f'aw-watcher-afk_{suffix}': {
                'type': 'afkstatus',
                'last_updated': REFERENCE_TIME.isoformat(),
            },
        }

    def fake_event_reader(bucket_id: str) -> dict[str, Any]:
        """Return a foreground Orca event."""
        return {
            'timestamp': REFERENCE_TIME.isoformat(),
            'duration': 5.0,
            'data': {'app': 'Orca', 'title': 'my-repo'},
        }

    def fake_cli_runner() -> dict[str, Any]:
        """Return one active main worktree."""
        return {
            'ok': True,
            'result': {
                'worktrees': [
                    {
                        'worktreeId': 'wt-1',
                        'repo': 'my-repo',
                        'displayName': 'main',
                        'isMainWorktree': True,
                        'path': '/path/to/main',
                        'isActive': True,
                    }
                ]
            },
        }

    def recording_creator(
        profile: BucketTargetProfile,
        host_suffix: str,
    ) -> ConfirmedBucketTarget:
        """Record the client of every created bucket."""
        created_clients.append(profile.client)
        return confirmed_target(profile, host_suffix)

    def fake_heartbeat_sender(
        target: ConfirmedBucketTarget,
        payload: Mapping[str, Any],
        pulse_time: float,
    ) -> None:
        """Fail once to force a rediscovery, record every target."""
        if tick == 2:
            raise ActivityWatchConnectionError('controlled restart')
        sent_targets.append(target.bucket_id)

    def fake_sleep(seconds: float) -> None:
        """Advance the synthetic polling tick."""
        nonlocal tick
        tick += 1

    exit_code = run_watcher_loop(
        profile=PRODUCTION_BUCKET_TARGET,
        max_iterations=7,
        profile_path=tmp_path / 'orca-data.json',
        bucket_reader=fake_bucket_reader,
        event_reader=fake_event_reader,
        bucket_creator=recording_creator,
        heartbeat_sender=fake_heartbeat_sender,
        cli_runner=fake_cli_runner,
        trigger_reader=lambda path: 'wt-1',
        clock=lambda: REFERENCE_TIME,
        sleep=fake_sleep,
    )

    assert exit_code == 0
    assert 'aw-watcher-orca_host-a' in sent_targets
    assert 'aw-watcher-orca_host-b' in sent_targets
    assert created_clients == ['aw-watcher-orca', 'aw-watcher-orca']
    assert not [
        target
        for target in sent_targets
        if target.startswith('aw-watcher-orca-test_')
    ]


@pytest.mark.parametrize(
    ('profile', 'expected_prefix', 'forbidden_prefix'),
    [
        (TEST_BUCKET_TARGET, 'aw-watcher-orca-test_', 'aw-watcher-orca_'),
        (
            PRODUCTION_BUCKET_TARGET,
            'aw-watcher-orca_',
            'aw-watcher-orca-test_',
        ),
    ],
)
def test_loop_writes_only_into_its_own_profile(
    tmp_path: Path,
    profile: BucketTargetProfile,
    expected_prefix: str,
    forbidden_prefix: str,
) -> None:
    """Each mode must reach exactly one bucket family."""
    sent_targets: list[str] = []

    def fake_bucket_reader(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return one fresh pair."""
        return {
            'aw-watcher-window_host-a': {
                'type': 'currentwindow',
                'last_updated': REFERENCE_TIME.isoformat(),
            },
            'aw-watcher-afk_host-a': {
                'type': 'afkstatus',
                'last_updated': REFERENCE_TIME.isoformat(),
            },
        }

    def fake_heartbeat_sender(
        target: ConfirmedBucketTarget,
        payload: Mapping[str, Any],
        pulse_time: float,
    ) -> None:
        """Record the heartbeat target."""
        sent_targets.append(target.bucket_id)

    exit_code = run_watcher_loop(
        profile=profile,
        max_iterations=2,
        profile_path=tmp_path / 'orca-data.json',
        bucket_reader=fake_bucket_reader,
        event_reader=lambda bucket_id: None,
        bucket_creator=confirmed_target,
        heartbeat_sender=fake_heartbeat_sender,
        cli_runner=lambda: {'ok': True, 'result': {'worktrees': []}},
        trigger_reader=lambda path: 'wt-1',
        clock=lambda: REFERENCE_TIME,
        sleep=lambda seconds: None,
    )

    assert exit_code == 0
    assert sent_targets
    assert all(target.startswith(expected_prefix) for target in sent_targets)
    assert not [
        target
        for target in sent_targets
        if target.startswith(forbidden_prefix)
    ]
