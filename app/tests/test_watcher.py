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
        """Serve one fake bucket reader."""
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
        """Serve one fake event reader."""
        return {
            'timestamp': REFERENCE_TIME.isoformat(),
            'duration': 5.0,
            'data': {'app': 'Orca', 'title': 'my-repo / feat-1'},
        }

    def fake_cli_runner() -> dict[str, Any]:
        """Serve one fake cli runner."""
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
        """Serve one fake heartbeat sender."""
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
        """Serve one fake bucket reader."""
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
        """Serve one fake event reader."""
        return {
            'timestamp': REFERENCE_TIME.isoformat(),
            'duration': 5.0,
            'data': {'app': 'Orca'},
        }

    def fake_trigger_reader(path: Path) -> str:
        """Serve one fake trigger reader."""
        return 'wt-1' if tick < 2 else 'wt-2'

    def fake_cli_runner() -> dict[str, Any]:
        """Serve one fake cli runner."""
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
        """Serve one fake heartbeat sender."""
        sent_heartbeats.append(dict(payload))

    def fake_sleep(s: float) -> None:
        """Serve one fake sleep."""
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
        """Serve one fake bucket reader."""
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
        """Serve one fake event reader."""
        return {
            'timestamp': REFERENCE_TIME.isoformat(),
            'duration': 5.0,
            'data': {'app': 'Browser', 'title': 'Web Page'},
        }

    def fake_trigger_reader(p: Path) -> str:
        """Serve one fake trigger reader."""
        nonlocal trigger_called
        trigger_called = True
        return 'wt-1'

    def fake_cli_runner() -> dict[str, Any]:
        """Serve one fake cli runner."""
        nonlocal cli_called
        cli_called = True
        return {}

    def fake_heartbeat_sender(
        bucket_id: str,
        payload: Mapping[str, Any],
        pulse_time: float,
    ) -> None:
        """Serve one fake heartbeat sender."""
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
        """Serve one fake bucket reader."""
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
        """Serve one fake event reader."""
        if failure_source == 'event_reader':
            raise ActivityWatchConnectionError('offline')
        return {
            'timestamp': REFERENCE_TIME.isoformat(),
            'duration': 5.0,
            'data': {'app': 'Orca'},
        }

    def fake_trigger_reader(p: Path) -> str:
        """Serve one fake trigger reader."""
        if failure_source == 'trigger_reader':
            raise MalformedStateError('bad json')
        return 'wt-1'

    def fake_cli_runner() -> dict[str, Any]:
        """Serve one fake cli runner."""
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
        """Serve one fake heartbeat sender."""
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


def test_loop_startup_failure_exits_nonzero(tmp_path: Path) -> None:
    def fail_bucket_reader(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Raise one fake bucket reader failure."""
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
        """Serve one fake bucket reader."""
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
        """Serve one interrupt sleep stub."""
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
