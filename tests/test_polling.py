"""Test observation capture bounds."""

from datetime import UTC, datetime, timedelta

import pytest

from aw_watcher_orca.polling import PollObservation, capture_observation

REFERENCE_TIME = datetime(2026, 11, 9, tzinfo=UTC)


@pytest.mark.parametrize(
    ('wall_gap', 'started', 'finished', 'continuous'),
    [
        (2.0, 2.0, 2.0, True),
        (3.0, 3.0, 3.0, True),
        (3.001, 3.001, 3.001, False),
        (3.0, 3.0005, 3.0005, False),
        (3.0005, 3.0, 3.0, False),
        (3.001, 2.0, 2.0, False),
        (2.0, 3.001, 3.001, False),
        (2.0, 1.8, 2.2, True),
        (2.0, 2.1, 2.2, False),
        (2.0, 1.8, 1.9, False),
        (-1.0, 2.0, 2.0, False),
        (1.0, -2.0, -1.0, False),
        (0.0, -0.0005, -0.0005, False),
        (-0.0005, 0.0, 0.0, False),
        (2.0, 2.0005, 1.9995, False),
    ],
)
def test_continuity_requires_agreeing_bounded_clocks(
    wall_gap: float,
    started: float,
    finished: float,
    continuous: bool,
) -> None:
    previous = PollObservation(REFERENCE_TIME, 0.0, 0.0)
    current = PollObservation(
        REFERENCE_TIME + timedelta(seconds=wall_gap),
        started,
        finished,
    )
    assert current.follows(previous, 3.0) is continuous


def test_capture_includes_clock_read_latency() -> None:
    readings = iter([1.0, 1.5])
    observation = capture_observation(
        lambda: REFERENCE_TIME,
        lambda: next(readings),
    )
    assert observation == PollObservation(REFERENCE_TIME, 1.0, 1.5)
