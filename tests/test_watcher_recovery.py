"""Test source recovery boundaries."""

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest

from aw_watcher_orca.bucket_target import (
    PRODUCTION_BUCKET_TARGET,
    BucketTargetProfile,
    ConfirmedBucketTarget,
    confirm_bucket_target,
)
from aw_watcher_orca.watcher import run_watcher_loop

REFERENCE_TIME = datetime(2026, 9, 2, 12, tzinfo=UTC)


class SourceRecoveryScenario:
    """Record synthetic source transitions."""

    def __init__(
        self,
        missing: bool = False,
        recovered_host: str = 'host-b',
        unavailable: str = '',
        background: bool = False,
    ) -> None:
        """Initialize one recovery scenario."""
        self.missing = missing
        self.recovered_host = recovered_host
        self.unavailable = unavailable
        self.background = background
        self.tick = 0
        self.bucket_reads: list[int] = []
        self.event_reads: list[tuple[int, str]] = []
        self.cli_ticks: list[int] = []
        self.sent: list[tuple[int, str, object]] = []
        self.created: list[str] = []
        self.sleeps: list[float] = []

    def clock(self) -> datetime:
        """Advance past source freshness."""
        seconds = self.tick * 2 if self.tick < 2 else 600 + self.tick * 2
        return REFERENCE_TIME + timedelta(seconds=seconds)

    def sleep(self, seconds: float) -> None:
        """Record one polling interval."""
        self.sleeps.append(seconds)
        self.tick += 1

    def pair(self, host: str, updated: datetime) -> dict[str, object]:
        """Build synthetic paired metadata."""
        return {
            f'aw-watcher-window_{host}': {
                'type': 'currentwindow',
                'last_updated': updated.isoformat(),
            },
            f'aw-watcher-afk_{host}': {
                'type': 'afkstatus',
                'last_updated': updated.isoformat(),
            },
        }

    def buckets(self) -> dict[str, object]:
        """Return current source catalog."""
        self.bucket_reads.append(self.tick)
        old = self.pair('host-a', REFERENCE_TIME)
        if self.tick < 2:
            return old
        if self.tick == 3 and self.unavailable == 'zero':
            return old
        fresh = self.pair(self.recovered_host, self.clock())
        if self.tick == 3 and self.unavailable == 'ambiguous':
            fresh.update(self.pair('host-c', self.clock()))
        return old | fresh

    def event(self, bucket_id: str) -> dict[str, object] | None:
        """Read only the requested source."""
        self.event_reads.append((self.tick, bucket_id))
        known = {
            'aw-watcher-window_host-a',
            f'aw-watcher-window_{self.recovered_host}',
        }
        assert bucket_id in known
        recovered_tick = 4 if self.unavailable else 3
        recovered = (
            self.tick >= recovered_tick
            and bucket_id == f'aw-watcher-window_{self.recovered_host}'
        )
        healthy = self.tick < 2 or recovered or self.background
        if not healthy and self.missing:
            return None
        return {
            'timestamp': (
                self.clock() if healthy else REFERENCE_TIME
            ).isoformat(),
            'duration': 0.0,
            'data': {
                'app': 'Chrome'
                if self.background and self.tick >= 2
                else 'Orca'
            },
        }

    def create(
        self, profile: BucketTargetProfile, host: str
    ) -> ConfirmedBucketTarget:
        """Confirm the selected profile target."""
        self.created.append(host)
        return confirm_bucket_target(
            profile,
            host,
            {
                'client': profile.client,
                'type': profile.bucket_type,
                'hostname': host,
            },
        )

    def send(
        self,
        target: ConfirmedBucketTarget,
        payload: Mapping[str, object],
        pulse_time: float,
    ) -> None:
        """Record attributed target heartbeats."""
        data = payload['data']
        assert isinstance(data, Mapping)
        self.sent.append((self.tick, target.bucket_id, data['title']))

    def cli(self) -> dict[str, object]:
        """Return one unchanged visible identity."""
        self.cli_ticks.append(self.tick)
        return {
            'ok': True,
            'result': {
                'worktrees': [
                    {
                        'worktreeId': 'wt-1',
                        'repo': 'demo',
                        'displayName': 'task',
                        'isMainWorktree': False,
                        'isActive': True,
                    }
                ]
            },
        }

    def run(self, iterations: int) -> int:
        """Run the real polling loop."""
        return run_watcher_loop(
            profile=PRODUCTION_BUCKET_TARGET,
            max_iterations=iterations,
            clock=self.clock,
            sleep=self.sleep,
            bucket_reader=self.buckets,
            event_reader=self.event,
            bucket_creator=self.create,
            heartbeat_sender=self.send,
            cli_runner=self.cli,
        )


class CorruptDurationScenario(SourceRecoveryScenario):
    """Serve one unrepresentable window duration."""

    def __init__(self, duration: int | float) -> None:
        """Initialize one corrupt duration."""
        super().__init__()
        self.duration = duration

    def event(self, bucket_id: str) -> dict[str, object] | None:
        """Serve a fresh other application event on tick two."""
        event = super().event(bucket_id)
        if self.tick == 2 and bucket_id == 'aw-watcher-window_host-a':
            assert event is not None
            serialized = json.loads(json.dumps({'duration': self.duration}))
            injected: dict[str, object] = dict(event)
            injected['timestamp'] = self.clock().isoformat()
            injected['duration'] = serialized['duration']
            injected['data'] = {'app': 'Chrome'}
            return injected
        return event


class OrdinaryDurationScenario(SourceRecoveryScenario):
    """Serve one ordinary window duration for another application."""

    def __init__(self, duration: int | float) -> None:
        """Initialize one ordinary duration."""
        super().__init__(background=True)
        self.duration = duration

    def event(self, bucket_id: str) -> dict[str, object] | None:
        """Override the background event duration."""
        event = super().event(bucket_id)
        if self.tick >= 2 and bucket_id == 'aw-watcher-window_host-a':
            assert event is not None
            return dict(event, duration=self.duration)
        return event


@pytest.mark.parametrize('missing', [False, True], ids=['stale', 'missing'])
@pytest.mark.parametrize('recovered_host', ['host-a', 'host-b'])
@pytest.mark.parametrize('unavailable', ['', 'zero', 'ambiguous'])
def test_unhealthy_window_rebinds_and_requires_fresh_confirmation(
    missing: bool, recovered_host: str, unavailable: str
) -> None:
    scenario = SourceRecoveryScenario(missing, recovered_host, unavailable)
    recovered_tick = 4 if unavailable else 3

    assert scenario.run(recovered_tick + 2) == 0

    old = 'aw-watcher-orca_host-a'
    new = f'aw-watcher-orca_{recovered_host}'
    assert scenario.sent == [
        (0, old, ''),
        (1, old, 'demo / task'),
        (2, old, ''),
        (recovered_tick, new, ''),
        (recovered_tick + 1, new, 'demo / task'),
    ]
    assert scenario.created == ['host-a', recovered_host]
    assert scenario.cli_ticks == [0, 1, recovered_tick, recovered_tick + 1]
    assert scenario.bucket_reads == ([0, 3, 4] if unavailable else [0, 3])
    assert scenario.event_reads == [
        (0, 'aw-watcher-window_host-a'),
        (1, 'aw-watcher-window_host-a'),
        (2, 'aw-watcher-window_host-a'),
        (recovered_tick, f'aw-watcher-window_{recovered_host}'),
        (recovered_tick + 1, f'aw-watcher-window_{recovered_host}'),
    ]
    assert scenario.sleeps == [2.0] * (recovered_tick + 1)


def test_fresh_other_application_does_not_rediscover() -> None:
    scenario = SourceRecoveryScenario(background=True)

    assert scenario.run(5) == 0

    target = 'aw-watcher-orca_host-a'
    assert scenario.sent == [
        (0, target, ''),
        (1, target, 'demo / task'),
        (2, target, ''),
        (3, target, ''),
        (4, target, ''),
    ]
    assert scenario.bucket_reads == [0]
    assert scenario.created == ['host-a']
    assert scenario.cli_ticks == [0, 1]


@pytest.mark.parametrize(
    'duration',
    [10**400, -(10**400), 1e20],
    ids=['large-int', 'large-negative-int', 'large-finite-float'],
)
def test_unrepresentable_duration_recovers_through_rediscovery(
    duration: int | float,
) -> None:
    scenario = CorruptDurationScenario(duration)

    assert scenario.run(5) == 0

    old = 'aw-watcher-orca_host-a'
    new = 'aw-watcher-orca_host-b'
    assert scenario.sent == [
        (0, old, ''),
        (1, old, 'demo / task'),
        (2, old, ''),
        (3, new, ''),
        (4, new, 'demo / task'),
    ]
    assert scenario.created == ['host-a', 'host-b']
    assert scenario.cli_ticks == [0, 1, 3, 4]
    assert scenario.bucket_reads == [0, 3]
    assert scenario.event_reads == [
        (0, 'aw-watcher-window_host-a'),
        (1, 'aw-watcher-window_host-a'),
        (2, 'aw-watcher-window_host-a'),
        (3, 'aw-watcher-window_host-b'),
        (4, 'aw-watcher-window_host-b'),
    ]
    assert scenario.sleeps == [2.0] * 4


@pytest.mark.parametrize('duration', [0, 0.0], ids=['int', 'float'])
def test_fresh_other_application_keeps_ordinary_durations(
    duration: int | float,
) -> None:
    scenario = OrdinaryDurationScenario(duration)

    assert scenario.run(5) == 0

    target = 'aw-watcher-orca_host-a'
    assert scenario.sent == [
        (0, target, ''),
        (1, target, 'demo / task'),
        (2, target, ''),
        (3, target, ''),
        (4, target, ''),
    ]
    assert scenario.bucket_reads == [0]
    assert scenario.created == ['host-a']
    assert scenario.cli_ticks == [0, 1]
