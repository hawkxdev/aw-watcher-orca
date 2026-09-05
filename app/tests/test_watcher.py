"""Test watcher polling loop."""

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from aw_watcher_orca.errors import (
    ActivityWatchConnectionError,
    MalformedStateError,
    OrcaCliStatusError,
)
from aw_watcher_orca.watcher import run_watcher_loop

# === Constants ===


REFERENCE_TIME = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)


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
        bucket_id: str,
        payload: Mapping[str, Any],
        pulse_time: float,
    ) -> None:
        """Record fake heartbeat payload."""
        sent_heartbeats.append(dict(payload))

    exit_code = run_watcher_loop(
        max_iterations=5,
        profile_path=tmp_path / 'orca-data.json',
        bucket_reader=fake_bucket_reader,
        event_reader=fake_event_reader,
        bucket_creator=lambda *args: None,
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
        bucket_id: str,
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
        max_iterations=4,
        profile_path=tmp_path / 'orca-data.json',
        bucket_reader=fake_bucket_reader,
        event_reader=fake_event_reader,
        bucket_creator=lambda *args: None,
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
        bucket_id: str,
        payload: Mapping[str, Any],
        pulse_time: float,
    ) -> None:
        """Record fake heartbeat payload."""
        sent_heartbeats.append(dict(payload))

    exit_code = run_watcher_loop(
        max_iterations=3,
        profile_path=tmp_path / 'orca-data.json',
        bucket_reader=fake_bucket_reader,
        event_reader=fake_event_reader,
        bucket_creator=lambda *args: None,
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
        bucket_id: str,
        payload: Mapping[str, Any],
        pulse_time: float,
    ) -> None:
        """Record fake heartbeat payload."""
        sent_heartbeats.append(dict(payload))

    exit_code = run_watcher_loop(
        max_iterations=2,
        profile_path=tmp_path / 'orca-data.json',
        bucket_reader=fake_bucket_reader,
        event_reader=fake_event_reader,
        bucket_creator=lambda *args: None,
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
        bucket_id: str,
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
        max_iterations=6,
        profile_path=tmp_path / 'orca-data.json',
        bucket_reader=fake_bucket_reader,
        event_reader=fake_event_reader,
        bucket_creator=lambda *args: None,
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
            max_iterations=2,
            profile_path=tmp_path / 'orca-data.json',
            bucket_reader=fake_bucket_reader,
            event_reader=fail_event_reader,
            bucket_creator=lambda *args: None,
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
        bucket_id: str,
        payload: Mapping[str, Any],
        pulse_time: float,
    ) -> None:
        """Fail outage heartbeat attempts."""
        if tick == 2:
            raise ActivityWatchConnectionError('controlled restart')
        sent_heartbeats.append((bucket_id, payload['data']['title']))

    def fake_sleep(seconds: float) -> None:
        """Advance synthetic polling tick."""
        nonlocal tick
        tick += 1

    exit_code = run_watcher_loop(
        max_iterations=7,
        profile_path=tmp_path / 'orca-data.json',
        bucket_reader=fake_bucket_reader,
        event_reader=fake_event_reader,
        bucket_creator=created_suffixes.append,
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
        max_iterations=1,
        profile_path=tmp_path / 'orca-data.json',
        bucket_reader=fail_bucket_reader,
        event_reader=lambda id: None,
        bucket_creator=lambda *args: None,
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
        max_iterations=5,
        profile_path=tmp_path / 'orca-data.json',
        bucket_reader=fake_bucket_reader,
        event_reader=lambda id: None,
        bucket_creator=lambda *args: None,
        heartbeat_sender=lambda *args: None,
        cli_runner=lambda: {},
        trigger_reader=lambda p: 'wt-1',
        clock=lambda: REFERENCE_TIME,
        sleep=interrupt_sleep,
    )

    assert exit_code == 0
