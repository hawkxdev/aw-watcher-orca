"""Discover ActivityWatch bucket pairs."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from aw_watcher_orca.errors import (
    MultipleFreshBucketPairsError,
    NoFreshBucketPairError,
)

# === Constants ===


DEFAULT_BUCKET_FRESHNESS_WINDOW: Final = timedelta(minutes=5)


# === Models ===


@dataclass(frozen=True, slots=True)
class ActivityWatchBucketPair:
    """Hold one ActivityWatch bucket pair."""

    host_suffix: str
    window_bucket_id: str
    afk_bucket_id: str


# === Candidate handling ===


@dataclass(frozen=True, slots=True)
class _BucketCandidate:
    """Hold one fresh bucket candidate."""

    bucket_id: str
    bucket_type: str
    host_suffix: str


def _parse_last_updated(value: object) -> datetime | None:
    """Parse one timezone-aware update timestamp."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed


def _fresh_candidate(
    bucket_id: str,
    metadata: object,
    reference_time: datetime,
    freshness_window: timedelta,
) -> _BucketCandidate | None:
    """Build one fresh supported bucket candidate."""
    if not isinstance(metadata, Mapping):
        return None
    bucket_type = metadata.get('type')
    if bucket_type not in {'currentwindow', 'afkstatus'}:
        return None
    _, separator, host_suffix = bucket_id.partition('_')
    if not separator or not host_suffix:
        return None
    last_updated = _parse_last_updated(metadata.get('last_updated'))
    if last_updated is None:
        return None
    try:
        age = reference_time - last_updated
    except TypeError:
        return None
    if age > freshness_window:
        return None
    return _BucketCandidate(
        bucket_id=bucket_id,
        bucket_type=bucket_type,
        host_suffix=host_suffix,
    )


# === Selection ===


def select_fresh_bucket_pair(
    buckets: Mapping[str, object],
    reference_time: datetime,
    freshness_window: timedelta = DEFAULT_BUCKET_FRESHNESS_WINDOW,
) -> ActivityWatchBucketPair:
    """Select one fresh ActivityWatch bucket pair."""
    candidates_by_suffix: dict[str, dict[str, list[str]]] = {}
    for bucket_id, metadata in buckets.items():
        candidate = _fresh_candidate(
            bucket_id,
            metadata,
            reference_time,
            freshness_window,
        )
        if candidate is None:
            continue
        typed_candidates = candidates_by_suffix.setdefault(
            candidate.host_suffix,
            {'currentwindow': [], 'afkstatus': []},
        )
        typed_candidates[candidate.bucket_type].append(candidate.bucket_id)

    pairs = [
        ActivityWatchBucketPair(
            host_suffix=host_suffix,
            window_bucket_id=window_bucket_id,
            afk_bucket_id=afk_bucket_id,
        )
        for host_suffix, typed_candidates in candidates_by_suffix.items()
        for window_bucket_id in typed_candidates['currentwindow']
        for afk_bucket_id in typed_candidates['afkstatus']
    ]
    if not pairs:
        raise NoFreshBucketPairError(
            'No fresh ActivityWatch bucket pair found'
        )
    if len(pairs) > 1:
        raise MultipleFreshBucketPairsError(
            'Multiple fresh ActivityWatch bucket pairs found'
        )
    return pairs[0]
