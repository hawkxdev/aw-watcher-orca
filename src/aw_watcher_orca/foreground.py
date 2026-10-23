"""Detect Orca foreground state."""

import math
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Final

# === Constants ===


ORCA_APP_NAME: Final = 'Orca'
DEFAULT_MAX_FOREGROUND_EVENT_AGE: Final = timedelta(seconds=30)


# === Predicate ===


def _is_representable_duration(duration: int | float) -> bool:
    """Check duration converts to a finite float."""
    try:
        return math.isfinite(float(duration))
    except OverflowError:
        return False


def is_orca_foreground(
    event: Mapping[str, object] | None,
    reference_time: datetime,
    max_age: timedelta = DEFAULT_MAX_FOREGROUND_EVENT_AGE,
    app_name: str = ORCA_APP_NAME,
) -> bool:
    """Check Orca foreground status."""
    if not isinstance(event, Mapping):
        return False
    data = event.get('data')
    if not isinstance(data, Mapping):
        return False
    if data.get('app') != app_name:
        return False
    return is_recent_window_event(event, reference_time, max_age)


def is_recent_window_event(
    event: Mapping[str, object] | None,
    reference_time: datetime,
    max_age: timedelta = DEFAULT_MAX_FOREGROUND_EVENT_AGE,
) -> bool:
    """Check window event freshness."""
    if not isinstance(event, Mapping):
        return False
    duration = event.get('duration')
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not _is_representable_duration(duration)
        or duration < 0
    ):
        return False
    raw_timestamp = event.get('timestamp')
    if not isinstance(raw_timestamp, str):
        return False
    try:
        parsed_timestamp = datetime.fromisoformat(raw_timestamp)
    except ValueError:
        return False
    try:
        event_end = parsed_timestamp + timedelta(seconds=duration)
        age = reference_time - event_end
    except (TypeError, OverflowError):
        return False
    return age <= max_age
