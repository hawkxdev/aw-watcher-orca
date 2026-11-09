"""Bound observation clock continuity."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

CLOCK_PRECISION: Final = timedelta(milliseconds=1)


@dataclass(frozen=True, slots=True)
class PollObservation:
    """Pair wall and monotonic clocks."""

    timestamp: datetime
    started: float
    finished: float

    def follows(
        self,
        previous: 'PollObservation',
        pulse_time: float,
    ) -> bool:
        """Check bounded observation continuity."""
        wall_gap = (self.timestamp - previous.timestamp).total_seconds()
        minimum_gap = self.started - previous.finished
        maximum_gap = self.finished - previous.started
        precision = CLOCK_PRECISION.total_seconds()
        return (
            0 <= minimum_gap <= maximum_gap <= pulse_time
            and 0 <= wall_gap <= pulse_time
            and minimum_gap - precision <= wall_gap <= maximum_gap + precision
        )


def capture_observation(
    clock: Callable[[], datetime],
    monotonic: Callable[[], float],
) -> PollObservation:
    """Bound wall clock capture."""
    started = monotonic()
    timestamp = clock()
    return PollObservation(timestamp, started, monotonic())
