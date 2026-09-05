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
MACHINE_HOSTNAME = 'test-machine.local'


# === Fixtures ===


def _timestamp(age: timedelta) -> str:
    """Build one aged timestamp."""
    return (REFERENCE_TIME - age).isoformat()


def _bucket(bucket_type: str, age: timedelta) -> dict[str, object]:
    """Build synthetic bucket metadata."""
    return {
        'type': bucket_type,
        'last_updated': _timestamp(age),
    }


def _pair(
    suffix: str,
    age: timedelta,
) -> dict[str, dict[str, object]]:
    """Build synthetic bucket pair."""
    return {
        f'aw-watcher-window_{suffix}': _bucket('currentwindow', age),
        f'aw-watcher-afk_{suffix}': _bucket('afkstatus', age),
    }


# === Selection contract ===


def test_select_fresh_bucket_pair_returns_single_matching_pair() -> None:
    buckets = _pair('host-a', timedelta(minutes=1))

    pair = select_fresh_bucket_pair(buckets, REFERENCE_TIME)

    assert pair.host_suffix == 'host-a'
    assert pair.window_bucket_id == 'aw-watcher-window_host-a'
    assert pair.afk_bucket_id == 'aw-watcher-afk_host-a'


def test_select_fresh_bucket_pair_rejects_zero_candidates() -> None:
    buckets = {
        'aw-stopwatch': _bucket('general.stopwatch', timedelta(seconds=1))
    }

    with pytest.raises(
        NoFreshBucketPairError,
        match='No fresh ActivityWatch bucket pair',
    ):
        select_fresh_bucket_pair(buckets, REFERENCE_TIME)


def test_select_fresh_bucket_pair_rejects_two_fresh_pairs() -> None:
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
    buckets = _pair('host-a', timedelta(minutes=6))

    with pytest.raises(
        NoFreshBucketPairError,
        match='No fresh ActivityWatch bucket pair',
    ):
        select_fresh_bucket_pair(buckets, REFERENCE_TIME)


def test_select_fresh_bucket_pair_requires_matching_suffixes() -> None:
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
    buckets = _pair('host-a', timedelta(minutes=1))
    buckets['aw-watcher-window_host-a']['last_updated'] = 'not-a-timestamp'

    with pytest.raises(
        NoFreshBucketPairError,
        match='No fresh ActivityWatch bucket pair',
    ):
        select_fresh_bucket_pair(buckets, REFERENCE_TIME)


def test_select_fresh_bucket_pair_accepts_comparable_naive_update() -> None:
    buckets = _pair('host-a', timedelta(minutes=1))
    buckets['aw-watcher-window_host-a']['last_updated'] = '2026-09-02T11:59:00'
    buckets['aw-watcher-afk_host-a']['last_updated'] = '2026-09-02T11:59:00'
    naive_reference = REFERENCE_TIME.replace(tzinfo=None)

    pair = select_fresh_bucket_pair(buckets, naive_reference)

    assert pair.host_suffix == 'host-a'


def test_select_fresh_bucket_pair_rejects_timezone_mismatch() -> None:
    buckets = _pair('host-a', timedelta(minutes=1))
    naive_reference = REFERENCE_TIME.replace(tzinfo=None)

    with pytest.raises(
        NoFreshBucketPairError,
        match='No fresh ActivityWatch bucket pair',
    ):
        select_fresh_bucket_pair(buckets, naive_reference)


def test_select_fresh_bucket_pair_ignores_non_object_metadata() -> None:
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
    buckets = _pair('host-a', timedelta(minutes=5))

    pair = select_fresh_bucket_pair(buckets, REFERENCE_TIME)

    assert pair.host_suffix == 'host-a'


def test_select_fresh_bucket_pair_rejects_beyond_freshness_boundary() -> None:
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
    buckets = _pair('host-a', timedelta(minutes=9))

    pair = select_fresh_bucket_pair(
        buckets,
        REFERENCE_TIME,
        freshness_window=timedelta(minutes=10),
    )

    assert pair.host_suffix == 'host-a'


def test_select_fresh_bucket_pair_uses_last_updated_not_event_time() -> None:
    buckets = _pair('host-a', timedelta(seconds=1))
    for metadata in buckets.values():
        metadata['last_event_timestamp'] = _timestamp(timedelta(hours=2))

    pair = select_fresh_bucket_pair(buckets, REFERENCE_TIME)

    assert pair.host_suffix == 'host-a'


def test_select_fresh_bucket_pair_uses_text_after_first_separator() -> None:
    buckets = _pair('host_with_underscores', timedelta(minutes=1))

    pair = select_fresh_bucket_pair(buckets, REFERENCE_TIME)

    assert pair.host_suffix == 'host_with_underscores'


def test_select_fresh_bucket_pair_does_not_prefer_machine_hostname() -> None:
    buckets = {
        **_pair(MACHINE_HOSTNAME, timedelta(hours=54)),
        **_pair('192.0.2.45', timedelta(seconds=1)),
    }

    pair = select_fresh_bucket_pair(buckets, REFERENCE_TIME)

    assert pair.host_suffix == '192.0.2.45'
    assert pair.window_bucket_id == 'aw-watcher-window_192.0.2.45'
    assert pair.afk_bucket_id == 'aw-watcher-afk_192.0.2.45'


def test_select_fresh_bucket_pair_rejects_duplicate_pair_members() -> None:
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


def test_select_fresh_bucket_pair_excludes_own_client_buckets() -> None:
    buckets = {
        **_pair('192.0.2.45', timedelta(seconds=1)),
        'aw-watcher-orca-test_192.0.2.45': {
            'type': 'currentwindow',
            'client': 'aw-watcher-orca-test',
            'last_updated': _timestamp(timedelta(seconds=1)),
        },
        'aw-watcher-orca_192.0.2.45': {
            'type': 'currentwindow',
            'client': 'aw-watcher-orca',
            'last_updated': _timestamp(timedelta(seconds=1)),
        },
    }

    pair = select_fresh_bucket_pair(buckets, REFERENCE_TIME)

    assert pair.host_suffix == '192.0.2.45'
    assert pair.window_bucket_id == 'aw-watcher-window_192.0.2.45'
    assert pair.afk_bucket_id == 'aw-watcher-afk_192.0.2.45'


def test_select_fresh_bucket_pair_accepts_fresh_machine_hostname() -> None:
    buckets = {
        **_pair('192.0.2.45', timedelta(hours=54)),
        **_pair(MACHINE_HOSTNAME, timedelta(seconds=1)),
    }

    pair = select_fresh_bucket_pair(buckets, REFERENCE_TIME)

    assert pair.host_suffix == MACHINE_HOSTNAME
    assert pair.window_bucket_id == f'aw-watcher-window_{MACHINE_HOSTNAME}'
    assert pair.afk_bucket_id == f'aw-watcher-afk_{MACHINE_HOSTNAME}'


def test_select_fresh_bucket_pair_honours_excluded_clients_override() -> None:
    buckets = {
        **_pair('host-a', timedelta(minutes=1)),
        'custom-window_host-a': {
            'type': 'currentwindow',
            'client': 'custom-client',
            'last_updated': _timestamp(timedelta(minutes=1)),
        },
    }

    pair = select_fresh_bucket_pair(
        buckets,
        REFERENCE_TIME,
        excluded_clients=frozenset({'custom-client'}),
    )

    assert pair.host_suffix == 'host-a'
