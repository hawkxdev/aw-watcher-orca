"""Test ActivityWatch bucket discovery."""

from datetime import UTC, datetime, timedelta

import pytest

from aw_watcher_orca.activitywatch import select_fresh_bucket_pair
from aw_watcher_orca.errors import (
    MultipleFreshBucketPairsError,
    NoFreshBucketPairError,
)

# === Constants ===


REFERENCE_TIME = datetime(2026, 9, 2, 12, tzinfo=UTC)
MACHINE_HOSTNAME = 'Hawkxs-MacBook-Pro.local'


# === Fixtures ===


def _timestamp(age: timedelta) -> str:
    """Build one timestamp at a known age."""
    return (REFERENCE_TIME - age).isoformat()


def _bucket(bucket_type: str, age: timedelta) -> dict[str, object]:
    """Build one synthetic bucket metadata record."""
    return {
        'type': bucket_type,
        'last_updated': _timestamp(age),
    }


def _pair(
    suffix: str,
    age: timedelta,
) -> dict[str, dict[str, object]]:
    """Build one synthetic bucket pair."""
    return {
        f'aw-watcher-window_{suffix}': _bucket('currentwindow', age),
        f'aw-watcher-afk_{suffix}': _bucket('afkstatus', age),
    }


# === Selection contract ===


def test_select_fresh_bucket_pair_returns_single_matching_pair() -> None:
    """Return one fresh matching pair."""
    buckets = _pair('host-a', timedelta(minutes=1))

    pair = select_fresh_bucket_pair(buckets, REFERENCE_TIME)

    assert pair.host_suffix == 'host-a'
    assert pair.window_bucket_id == 'aw-watcher-window_host-a'
    assert pair.afk_bucket_id == 'aw-watcher-afk_host-a'


def test_select_fresh_bucket_pair_rejects_zero_candidates() -> None:
    """Reject an empty candidate set."""
    buckets = {
        'aw-stopwatch': _bucket('general.stopwatch', timedelta(seconds=1))
    }

    with pytest.raises(
        NoFreshBucketPairError,
        match='No fresh ActivityWatch bucket pair',
    ):
        select_fresh_bucket_pair(buckets, REFERENCE_TIME)


def test_select_fresh_bucket_pair_rejects_two_fresh_pairs() -> None:
    """Reject multiple fresh matching pairs."""
    buckets = {
        **_pair('host-a', timedelta(minutes=1)),
        **_pair('host-b', timedelta(minutes=2)),
    }

    with pytest.raises(
        MultipleFreshBucketPairsError,
        match='Multiple fresh ActivityWatch bucket pairs',
    ):
        select_fresh_bucket_pair(buckets, REFERENCE_TIME)


def test_select_fresh_bucket_pair_rejects_only_stale_pairs() -> None:
    """Reject matching stale pairs."""
    buckets = _pair('host-a', timedelta(minutes=6))

    with pytest.raises(
        NoFreshBucketPairError,
        match='No fresh ActivityWatch bucket pair',
    ):
        select_fresh_bucket_pair(buckets, REFERENCE_TIME)


def test_select_fresh_bucket_pair_requires_matching_suffixes() -> None:
    """Reject unmatched fresh bucket suffixes."""
    buckets = {
        'aw-watcher-window_host-a': _bucket(
            'currentwindow',
            timedelta(minutes=1),
        ),
        'aw-watcher-afk_host-b': _bucket(
            'afkstatus',
            timedelta(minutes=1),
        ),
    }

    with pytest.raises(
        NoFreshBucketPairError,
        match='No fresh ActivityWatch bucket pair',
    ):
        select_fresh_bucket_pair(buckets, REFERENCE_TIME)


def test_select_fresh_bucket_pair_treats_missing_update_as_stale() -> None:
    """Treat an absent update timestamp as stale."""
    buckets = _pair('host-a', timedelta(minutes=1))
    del buckets['aw-watcher-afk_host-a']['last_updated']
    buckets['aw-watcher-afk_host-a']['timestamp'] = _timestamp(
        timedelta(seconds=1)
    )

    with pytest.raises(
        NoFreshBucketPairError,
        match='No fresh ActivityWatch bucket pair',
    ):
        select_fresh_bucket_pair(buckets, REFERENCE_TIME)


def test_select_fresh_bucket_pair_treats_unparseable_update_as_stale() -> None:
    """Treat an unparseable update timestamp as stale."""
    buckets = _pair('host-a', timedelta(minutes=1))
    buckets['aw-watcher-window_host-a']['last_updated'] = 'not-a-timestamp'

    with pytest.raises(
        NoFreshBucketPairError,
        match='No fresh ActivityWatch bucket pair',
    ):
        select_fresh_bucket_pair(buckets, REFERENCE_TIME)


def test_select_fresh_bucket_pair_accepts_comparable_naive_update() -> None:
    """Accept comparable timezone-free timestamps."""
    buckets = _pair('host-a', timedelta(minutes=1))
    buckets['aw-watcher-window_host-a']['last_updated'] = '2026-09-02T11:59:00'
    buckets['aw-watcher-afk_host-a']['last_updated'] = '2026-09-02T11:59:00'
    naive_reference = REFERENCE_TIME.replace(tzinfo=None)

    pair = select_fresh_bucket_pair(buckets, naive_reference)

    assert pair.host_suffix == 'host-a'


def test_select_fresh_bucket_pair_rejects_timezone_mismatch() -> None:
    """Reject an incomparable reference timestamp."""
    buckets = _pair('host-a', timedelta(minutes=1))
    naive_reference = REFERENCE_TIME.replace(tzinfo=None)

    with pytest.raises(
        NoFreshBucketPairError,
        match='No fresh ActivityWatch bucket pair',
    ):
        select_fresh_bucket_pair(buckets, naive_reference)


def test_select_fresh_bucket_pair_ignores_non_object_metadata() -> None:
    """Ignore non-object metadata in pure selection."""
    buckets = {
        'aw-watcher-window_host-a': [],
        'aw-watcher-afk_host-a': _bucket(
            'afkstatus',
            timedelta(minutes=1),
        ),
    }

    with pytest.raises(
        NoFreshBucketPairError,
        match='No fresh ActivityWatch bucket pair',
    ):
        select_fresh_bucket_pair(buckets, REFERENCE_TIME)


def test_select_fresh_bucket_pair_ignores_id_without_separator() -> None:
    """Ignore a candidate without a suffix separator."""
    buckets = {
        'window-without-separator': _bucket(
            'currentwindow',
            timedelta(minutes=1),
        ),
        'aw-watcher-afk_window-without-separator': _bucket(
            'afkstatus',
            timedelta(minutes=1),
        ),
    }

    with pytest.raises(
        NoFreshBucketPairError,
        match='No fresh ActivityWatch bucket pair',
    ):
        select_fresh_bucket_pair(buckets, REFERENCE_TIME)


def test_select_fresh_bucket_pair_ignores_empty_suffix() -> None:
    """Ignore a candidate with an empty suffix."""
    buckets = {
        'aw-watcher-window_': _bucket(
            'currentwindow',
            timedelta(minutes=1),
        ),
        'aw-watcher-afk_': _bucket(
            'afkstatus',
            timedelta(minutes=1),
        ),
    }

    with pytest.raises(
        NoFreshBucketPairError,
        match='No fresh ActivityWatch bucket pair',
    ):
        select_fresh_bucket_pair(buckets, REFERENCE_TIME)


def test_select_fresh_bucket_pair_ignores_non_candidate_type() -> None:
    """Ignore non-candidate bucket types."""
    buckets = {
        'aw-watcher-window_host-a': _bucket(
            'general.stopwatch',
            timedelta(minutes=1),
        ),
        'aw-watcher-afk_host-a': _bucket(
            'afkstatus',
            timedelta(minutes=1),
        ),
    }

    with pytest.raises(
        NoFreshBucketPairError,
        match='No fresh ActivityWatch bucket pair',
    ):
        select_fresh_bucket_pair(buckets, REFERENCE_TIME)


def test_select_fresh_bucket_pair_accepts_exact_freshness_boundary() -> None:
    """Accept the exact freshness boundary."""
    buckets = _pair('host-a', timedelta(minutes=5))

    pair = select_fresh_bucket_pair(buckets, REFERENCE_TIME)

    assert pair.host_suffix == 'host-a'


def test_select_fresh_bucket_pair_rejects_beyond_freshness_boundary() -> None:
    """Reject a timestamp beyond the freshness boundary."""
    buckets = _pair(
        'host-a',
        timedelta(minutes=5, microseconds=1),
    )

    with pytest.raises(
        NoFreshBucketPairError,
        match='No fresh ActivityWatch bucket pair',
    ):
        select_fresh_bucket_pair(buckets, REFERENCE_TIME)


def test_select_fresh_bucket_pair_honours_window_override() -> None:
    """Honour an explicit freshness window."""
    buckets = _pair('host-a', timedelta(minutes=9))

    pair = select_fresh_bucket_pair(
        buckets,
        REFERENCE_TIME,
        freshness_window=timedelta(minutes=10),
    )

    assert pair.host_suffix == 'host-a'


def test_select_fresh_bucket_pair_uses_last_updated_not_event_time() -> None:
    """Use bucket freshness instead of the last event time."""
    buckets = _pair('host-a', timedelta(seconds=1))
    for metadata in buckets.values():
        metadata['last_event_timestamp'] = _timestamp(timedelta(hours=2))

    pair = select_fresh_bucket_pair(buckets, REFERENCE_TIME)

    assert pair.host_suffix == 'host-a'


def test_select_fresh_bucket_pair_uses_text_after_first_separator() -> None:
    """Use the complete suffix after the first separator."""
    buckets = _pair('host_with_underscores', timedelta(minutes=1))

    pair = select_fresh_bucket_pair(buckets, REFERENCE_TIME)

    assert pair.host_suffix == 'host_with_underscores'


def test_select_fresh_bucket_pair_does_not_prefer_machine_hostname() -> None:
    """Choose freshness instead of the machine hostname."""
    buckets = {
        **_pair(MACHINE_HOSTNAME, timedelta(hours=54)),
        **_pair('192.0.2.45', timedelta(seconds=1)),
    }

    pair = select_fresh_bucket_pair(buckets, REFERENCE_TIME)

    assert pair.host_suffix == '192.0.2.45'
    assert pair.window_bucket_id == 'aw-watcher-window_192.0.2.45'
    assert pair.afk_bucket_id == 'aw-watcher-afk_192.0.2.45'


def test_select_fresh_bucket_pair_rejects_duplicate_pair_members() -> None:
    """Reject multiple fresh members for one suffix."""
    buckets = {
        **_pair('host-a', timedelta(minutes=1)),
        'alternate-window_host-a': _bucket(
            'currentwindow',
            timedelta(minutes=1),
        ),
    }

    with pytest.raises(
        MultipleFreshBucketPairsError,
        match='Multiple fresh ActivityWatch bucket pairs',
    ):
        select_fresh_bucket_pair(buckets, REFERENCE_TIME)
