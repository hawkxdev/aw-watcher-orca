"""Test observed heartbeat continuity."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

import aw_watcher_orca.watcher as watcher_module
from aw_watcher_orca.bucket_target import (
    TEST_BUCKET_TARGET,
    BucketTargetProfile,
    ConfirmedBucketTarget,
    confirm_bucket_target,
)
from aw_watcher_orca.errors import ActivityWatchConnectionError

REFERENCE_TIME = datetime(2026, 11, 9, tzinfo=UTC)


class CadenceScenario:
    """Advance both observation clocks."""

    def __init__(self, cli_cost: float = 1.0) -> None:
        """Initialize synthetic collection boundaries."""
        self.elapsed = 0.0
        self.wall_offset = 0.0
        self.tick = 0
        self.cli_cost = cli_cost
        self.read_cost = 0.2
        self.send_cost = 0.1
        self.bucket_cost = 0.0
        self.cli_costs: dict[int, float] = {}
        self.clock_steps: dict[int, float] = {}
        self.pauses: dict[int, float] = {}
        self.failed_ticks: set[int] = set()
        self.missing_ticks: set[int] = set()
        self.ambiguous_ticks: set[int] = set()
        self.background_ticks: set[int] = set()
        self.identities: dict[int, str] = {}
        self.window_ages: dict[int, float] = {}
        self.sent: list[dict[str, Any]] = []
        self.merged: list[dict[str, Any]] = []
        self.sleeps: list[float] = []

    def clock(self) -> datetime:
        """Read synthetic UTC time."""
        return REFERENCE_TIME + timedelta(
            seconds=self.elapsed + self.wall_offset
        )

    def monotonic(self) -> float:
        """Read synthetic elapsed time."""
        return self.elapsed

    def sleep(self, seconds: float) -> None:
        """Advance the polling schedule."""
        self.sleeps.append(seconds)
        self.elapsed += seconds + self.pauses.get(self.tick, 0.0)
        self.wall_offset += self.clock_steps.get(self.tick, 0.0)
        self.tick += 1

    def buckets(self) -> dict[str, object]:
        """Return fresh synthetic metadata."""
        self.elapsed += self.bucket_cost
        if self.tick in self.ambiguous_ticks:
            return {}
        return {
            f'{prefix}_synthetic': {
                'type': bucket_type,
                'last_updated': self.clock().isoformat(),
            }
            for prefix, bucket_type in (
                ('aw-watcher-window', 'currentwindow'),
                ('aw-watcher-afk', 'afkstatus'),
            )
        }

    def window(self, _bucket_id: str) -> dict[str, object] | None:
        """Observe synthetic foreground state."""
        self.elapsed += self.read_cost
        if self.tick in self.failed_ticks:
            raise ActivityWatchConnectionError('Synthetic read failure')
        if self.tick in self.missing_ticks:
            return None
        return {
            'timestamp': (
                self.clock()
                - timedelta(seconds=self.window_ages.get(self.tick, 0.0))
            ).isoformat(),
            'duration': 0.0,
            'data': {
                'app': 'Chrome'
                if self.tick in self.background_ticks
                else 'Orca',
            },
        }

    def cli(self) -> dict[str, object]:
        """Resolve one synthetic worktree."""
        self.elapsed += self.cli_costs.get(self.tick, self.cli_cost)
        name = self.identities.get(self.tick, 'main')
        return {
            'ok': True,
            'result': {
                'worktrees': [
                    {
                        'worktreeId': name,
                        'repo': 'sample-repository',
                        'displayName': name,
                        'isMainWorktree': name == 'main',
                        'isActive': True,
                    }
                ]
            },
        }

    def create(
        self, profile: BucketTargetProfile, suffix: str
    ) -> ConfirmedBucketTarget:
        """Confirm the synthetic profile."""
        return confirm_bucket_target(
            profile,
            suffix,
            {
                'client': profile.client,
                'type': profile.bucket_type,
                'hostname': suffix,
            },
        )

    def send(
        self,
        _target: ConfirmedBucketTarget,
        payload: Mapping[str, object],
        pulse_time: float,
    ) -> None:
        """Apply documented merge boundaries."""
        timestamp = datetime.fromisoformat(str(payload['timestamp']))
        data = payload['data']
        assert isinstance(data, Mapping)
        event = {
            'timestamp': timestamp,
            'duration': 0.0,
            'data': dict(data),
            'tick': self.tick,
        }
        self.sent.append(dict(event))
        if self.merged:
            previous = self.merged[-1]
            end = previous['timestamp'] + timedelta(
                seconds=previous['duration']
            )
            if previous['data'] == data and previous[
                'timestamp'
            ] <= timestamp <= end + timedelta(seconds=pulse_time):
                previous['duration'] = max(
                    previous['duration'],
                    (timestamp - previous['timestamp']).total_seconds(),
                )
            else:
                self.merged.append(event)
        else:
            self.merged.append(event)
        self.elapsed += self.send_cost

    def run(
        self,
        monkeypatch: pytest.MonkeyPatch,
        iterations: int = 6,
    ) -> None:
        """Drive the actual watcher."""
        monkeypatch.setattr(
            watcher_module,
            'time',
            SimpleNamespace(monotonic=self.monotonic),
        )
        assert (
            watcher_module.run_watcher_loop(
                profile=TEST_BUCKET_TARGET,
                max_iterations=iterations,
                clock=self.clock,
                sleep=self.sleep,
                bucket_reader=self.buckets,
                event_reader=self.window,
                cli_runner=self.cli,
                bucket_creator=self.create,
                heartbeat_sender=self.send,
            )
            == 0
        )

    def titles(self) -> list[str]:
        """Read emitted public labels."""
        return [event['data']['title'] for event in self.sent]


@pytest.mark.parametrize('cli_cost', [0.0, 1.0, 1.5])
def test_work_cost_is_inside_polling_period(
    monkeypatch: pytest.MonkeyPatch,
    cli_cost: float,
) -> None:
    scenario = CadenceScenario(cli_cost)
    scenario.run(monkeypatch)
    active = [event for event in scenario.merged if event['data']['repo']]
    assert len(active) == 1
    assert active[0]['duration'] == 8.0
    assert scenario.titles() == ['', *['sample-repository'] * 5]


def test_long_pause_restarts_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = CadenceScenario()
    scenario.pauses[1] = 4.0
    scenario.run(monkeypatch)
    assert scenario.titles() == [
        '',
        'sample-repository',
        '',
        'sample-repository',
        'sample-repository',
        'sample-repository',
    ]
    active = [event for event in scenario.merged if event['data']['repo']]
    assert [event['duration'] for event in active] == [0.0, 4.0]


@pytest.mark.parametrize('clock_step', [-1.0, 1.0])
def test_clock_change_restarts_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    clock_step: float,
) -> None:
    scenario = CadenceScenario()
    scenario.clock_steps[1] = clock_step
    scenario.run(monkeypatch)
    assert scenario.titles() == [
        '',
        'sample-repository',
        '',
        'sample-repository',
        'sample-repository',
        'sample-repository',
    ]


def test_clock_rollback_does_not_publish_older_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = CadenceScenario()
    scenario.clock_steps[1] = -10.0
    scenario.run(monkeypatch, iterations=10)
    timestamps = [event['timestamp'] for event in scenario.sent]
    assert timestamps == sorted(timestamps)
    assert [event['tick'] for event in scenario.sent] == [0, 1, 6, 7, 8, 9]
    assert scenario.titles()[2:4] == ['', 'sample-repository']


def test_slow_observation_is_discarded_before_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = CadenceScenario()
    scenario.cli_costs[2] = 4.0
    scenario.run(monkeypatch)
    assert scenario.titles() == [
        '',
        'sample-repository',
        '',
        '',
        'sample-repository',
        'sample-repository',
    ]
    assert scenario.sleeps[2] == 0.0


def test_window_expiring_during_resolution_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = CadenceScenario()
    scenario.window_ages[2] = 29.5
    scenario.run(monkeypatch)
    assert scenario.titles() == [
        '',
        'sample-repository',
        '',
        '',
        'sample-repository',
        'sample-repository',
    ]


def test_read_failure_uses_remaining_poll_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = CadenceScenario()
    scenario.failed_ticks.add(2)
    scenario.run(monkeypatch)
    assert scenario.sleeps[2] == pytest.approx(1.7)
    assert scenario.titles()[2:5] == ['', '', 'sample-repository']


def test_rediscovery_uses_remaining_poll_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = CadenceScenario()
    scenario.bucket_cost = 0.7
    scenario.missing_ticks.add(2)
    scenario.ambiguous_ticks.add(3)
    scenario.run(monkeypatch, iterations=7)
    assert scenario.sleeps[3] == pytest.approx(1.3)
    assert [event['tick'] for event in scenario.sent] == [0, 1, 2, 4, 5, 6]
    assert scenario.titles() == [
        '',
        'sample-repository',
        '',
        '',
        'sample-repository',
        'sample-repository',
    ]


def test_foreground_changes_preserve_neutral_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = CadenceScenario()
    scenario.background_ticks.add(2)
    scenario.identities = {3: 'feature', 4: 'feature', 5: 'feature'}
    scenario.run(monkeypatch)
    assert scenario.titles() == [
        '',
        'sample-repository',
        '',
        '',
        'sample-repository / feature',
        'sample-repository / feature',
    ]


@pytest.mark.parametrize(
    ('pause', 'title_after_pause'),
    [(1.0, 'sample-repository'), (1.001, '')],
)
def test_merge_bound_is_inclusive(
    monkeypatch: pytest.MonkeyPatch,
    pause: float,
    title_after_pause: str,
) -> None:
    scenario = CadenceScenario()
    scenario.pauses[1] = pause
    scenario.run(monkeypatch)
    assert scenario.titles()[2] == title_after_pause


@pytest.mark.parametrize(
    ('clock_step', 'title_after_step'),
    [
        (0.001, 'sample-repository'),
        (-0.001, 'sample-repository'),
        (0.001001, ''),
        (-0.001001, ''),
    ],
)
def test_clock_precision_does_not_hide_larger_steps(
    monkeypatch: pytest.MonkeyPatch,
    clock_step: float,
    title_after_step: str,
) -> None:
    scenario = CadenceScenario()
    scenario.clock_steps[1] = clock_step
    scenario.run(monkeypatch)
    assert scenario.titles()[2] == title_after_step
