"""Test foreground Orca detection."""

from datetime import UTC, datetime, timedelta

from aw_watcher_orca.foreground import (
    DEFAULT_MAX_FOREGROUND_EVENT_AGE,
    ORCA_APP_NAME,
    is_orca_foreground,
)

# === Constants ===


REFERENCE_TIME = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)


# === Helpers ===


def _event(
    app: str = ORCA_APP_NAME,
    timestamp: str | None = None,
    duration: float = 5.0,
    title: str = 'repo / worktree',
) -> dict[str, object]:
    """Build one synthetic window event."""
    ts = (
        timestamp
        if timestamp is not None
        else (REFERENCE_TIME - timedelta(seconds=10)).isoformat()
    )
    return {
        'id': 1,
        'timestamp': ts,
        'duration': duration,
        'data': {
            'app': app,
            'title': title,
        },
    }


# === Predicate contract ===


def test_is_orca_foreground_returns_true_for_fresh_orca_event() -> None:
    event = _event(
        app='Orca',
        timestamp=(REFERENCE_TIME - timedelta(seconds=10)).isoformat(),
        duration=5.0,
    )

    assert is_orca_foreground(event, REFERENCE_TIME) is True


def test_is_orca_foreground_returns_false_for_stale_orca_event() -> None:
    event = _event(
        app='Orca',
        timestamp=(REFERENCE_TIME - timedelta(seconds=40)).isoformat(),
        duration=5.0,
    )

    assert is_orca_foreground(event, REFERENCE_TIME) is False


def test_is_orca_foreground_returns_false_for_another_application() -> None:
    event = _event(
        app='Code',
        timestamp=(REFERENCE_TIME - timedelta(seconds=5)).isoformat(),
        duration=2.0,
    )

    assert is_orca_foreground(event, REFERENCE_TIME) is False


def test_is_orca_foreground_returns_false_for_none_event() -> None:
    assert is_orca_foreground(None, REFERENCE_TIME) is False


def test_is_orca_foreground_returns_false_for_missing_app() -> None:
    event = {
        'timestamp': REFERENCE_TIME.isoformat(),
        'duration': 1.0,
        'data': {},
    }

    assert is_orca_foreground(event, REFERENCE_TIME) is False


def test_is_orca_foreground_returns_false_for_missing_duration() -> None:
    event = {
        'timestamp': REFERENCE_TIME.isoformat(),
        'data': {'app': 'Orca'},
    }

    assert is_orca_foreground(event, REFERENCE_TIME) is False


def test_is_orca_foreground_returns_false_for_unparseable_timestamp() -> None:
    event = {
        'timestamp': 'not-a-timestamp',
        'duration': 1.0,
        'data': {'app': 'Orca'},
    }

    assert is_orca_foreground(event, REFERENCE_TIME) is False


def test_is_orca_foreground_accepts_exact_age_boundary() -> None:
    boundary_ts = (
        REFERENCE_TIME
        - DEFAULT_MAX_FOREGROUND_EVENT_AGE
        - timedelta(seconds=5)
    ).isoformat()
    event = _event(timestamp=boundary_ts, duration=5.0)

    assert is_orca_foreground(event, REFERENCE_TIME) is True


def test_is_orca_foreground_rejects_beyond_age_boundary() -> None:
    beyond_ts = (
        REFERENCE_TIME
        - DEFAULT_MAX_FOREGROUND_EVENT_AGE
        - timedelta(seconds=5, microseconds=1)
    ).isoformat()
    event = _event(timestamp=beyond_ts, duration=5.0)

    assert is_orca_foreground(event, REFERENCE_TIME) is False


def test_is_orca_foreground_honours_max_age_override() -> None:
    event = _event(
        timestamp=(REFERENCE_TIME - timedelta(seconds=50)).isoformat(),
        duration=5.0,
    )

    assert (
        is_orca_foreground(
            event,
            REFERENCE_TIME,
            max_age=timedelta(seconds=60),
        )
        is True
    )


def test_is_orca_foreground_honours_app_name_override() -> None:
    event = _event(
        app='CustomOrca',
        timestamp=(REFERENCE_TIME - timedelta(seconds=5)).isoformat(),
        duration=2.0,
    )

    assert (
        is_orca_foreground(
            event,
            REFERENCE_TIME,
            app_name='CustomOrca',
        )
        is True
    )


def test_is_orca_foreground_rejects_non_finite_or_negative_duration() -> None:
    negative_event = {
        'timestamp': REFERENCE_TIME.isoformat(),
        'duration': -1.0,
        'data': {'app': 'Orca'},
    }
    infinite_event = {
        'timestamp': REFERENCE_TIME.isoformat(),
        'duration': float('inf'),
        'data': {'app': 'Orca'},
    }
    bool_event = {
        'timestamp': REFERENCE_TIME.isoformat(),
        'duration': True,
        'data': {'app': 'Orca'},
    }

    assert is_orca_foreground(negative_event, REFERENCE_TIME) is False
    assert is_orca_foreground(infinite_event, REFERENCE_TIME) is False
    assert is_orca_foreground(bool_event, REFERENCE_TIME) is False


def test_is_orca_foreground_rejects_non_mapping_data() -> None:
    event = {
        'timestamp': REFERENCE_TIME.isoformat(),
        'duration': 1.0,
        'data': 'not-a-mapping',
    }

    assert is_orca_foreground(event, REFERENCE_TIME) is False
