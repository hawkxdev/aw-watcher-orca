"""Read and verify historical ActivityWatch reporting event snapshots."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import OpenerDirector, Request, urlopen

from aw_watcher_orca.bucket_target import PRODUCTION_BUCKET_TARGET
from aw_watcher_orca.report_models import (
    ABSENT_UNVERIFIED_STATUS,
    UNKNOWN_BOUNDARY_REASON,
    RawEventRecord,
    ReportApiError,
    ReportIncompleteError,
    ReportLimitError,
    ReportMalformedPayloadError,
    ReportSourceKind,
    validate_raw_event_payload,
)
from aw_watcher_orca.report_settings import (
    DEFAULT_READ_TIMEOUT_SECONDS,
    MAX_AFK_EVENTS_PER_SOURCE,
    MAX_RESPONSE_BYTES,
    MAX_SPECIALIZED_EVENTS_PER_SOURCE,
    STANDARD_QUERY_TIMEOUT_SECONDS,
    ReportSettings,
    validate_source_events_limit,
    validate_standard_query_events_limit,
    validate_total_response_bytes_limit,
)
from aw_watcher_orca.report_sources import SourceCatalog

# === Constants ===

USER_AGENT: Final[str] = 'aw-watcher-orca-reporting/0.1.0'
EPOCH_UTC: Final[datetime] = datetime(1970, 1, 1, 0, 0, 0, tzinfo=UTC)


# === Data Structures ===


@dataclass(frozen=True, slots=True)
class FullSnapshotResult:
    """Hold complete verified raw event snapshot and metadata."""

    catalog: SourceCatalog
    observed_until: datetime
    read_started_at: datetime
    read_finished_at: datetime
    events_by_bucket: Mapping[str, tuple[RawEventRecord, ...]]
    production_boundary: datetime | None
    boundary_status: str
    total_response_bytes: int
    last_updated: datetime | None = None


# === 4-Number Verification Helper (SRC-04, TLR-02) ===


def verify_four_numbers(
    count_before: int,
    returned_count: int,
    unique_ids_count: int,
    count_after: int,
    budget_limit: int,
) -> bool:
    """Verify pre-count, row count, unique IDs, and post-count all match."""
    if (
        count_before != returned_count
        or returned_count != unique_ids_count
        or unique_ids_count != count_after
    ):
        return False
    return count_before <= budget_limit


# === Production Boundary Computation (SRC-08, TLR-03) ===


def compute_production_boundary(
    catalog: SourceCatalog,
    events_by_bucket: Mapping[str, tuple[RawEventRecord, ...]],
    observed_until: datetime,
) -> tuple[datetime | None, str]:
    """Compute global production boundary across all valid production buckets.

    Considers all production buckets with valid metadata (even without AFK
    pair) per TIME-01 and TLR-03.
    """
    production_buckets = [
        e.bucket_id
        for e in catalog.entries
        if e.source_kind == ReportSourceKind.PRODUCTION
        and e.client == PRODUCTION_BUCKET_TARGET.client
        and e.bucket_type == PRODUCTION_BUCKET_TARGET.bucket_type
        and e.hostname == e.host_suffix
    ]

    if not production_buckets:
        return (None, ABSENT_UNVERIFIED_STATUS)

    production_events: list[RawEventRecord] = []
    for bucket_id in production_buckets:
        for event in events_by_bucket.get(bucket_id, ()):
            if event.timestamp <= observed_until:
                production_events.append(event)

    if not production_events:
        return (None, UNKNOWN_BOUNDARY_REASON)

    min_ts = min(event.timestamp for event in production_events)
    return (min_ts, 'known')


# === HTTP Helper Functions ===


def _http_get_json(
    url: str,
    timeout: float,
    max_bytes: int = MAX_RESPONSE_BYTES,
    opener: OpenerDirector | None = None,
) -> tuple[object, int]:
    """Execute HTTP GET request and return decoded JSON and payload size.

    Reads in bounded chunks to halt if body exceeds limit (LIMIT-04).
    """
    req = Request(  # noqa: S310 (local endpoint)
        url,
        headers={'Accept': 'application/json', 'User-Agent': USER_AGENT},
    )
    open_fn = opener.open if opener is not None else urlopen
    try:
        with open_fn(req, timeout=timeout) as resp:  # noqa: S310
            status = resp.status
            chunks: list[bytes] = []
            total_read = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                total_read += len(chunk)
                if total_read > max_bytes:
                    raise ReportLimitError(
                        f'Response body bytes ({total_read}) '
                        f'exceeds limit ({max_bytes})'
                    )
            body = b''.join(chunks)
    except HTTPError as err:
        err.close()
        raise ReportApiError(
            f'ActivityWatch request returned HTTP {err.code}'
        ) from None
    except (URLError, TimeoutError, OSError):
        raise ReportApiError('Unable to connect to ActivityWatch') from None

    if status != 200:
        raise ReportApiError(f'ActivityWatch request returned HTTP {status}')

    body_len = len(body)
    try:
        data = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise ReportMalformedPayloadError(
            'ActivityWatch returned malformed JSON response'
        ) from err

    return (data, body_len)


# === Single Bucket Reading with Verification (SRC-03, SRC-04) ===


def read_bucket_events_with_verification(
    bucket_id: str,
    base_url: str,
    observed_until: datetime,
    is_afk: bool,
    max_specialized_limit: int = MAX_SPECIALIZED_EVENTS_PER_SOURCE,
    max_afk_limit: int = MAX_AFK_EVENTS_PER_SOURCE,
    max_response_bytes: int = MAX_RESPONSE_BYTES,
    timeout: float = DEFAULT_READ_TIMEOUT_SECONDS,
    opener: OpenerDirector | None = None,
) -> tuple[tuple[RawEventRecord, ...], int]:
    """Read full bucket events without start parameter and verify 4 numbers."""
    budget_limit = max_afk_limit if is_afk else max_specialized_limit
    fetch_limit = budget_limit + 1
    observed_until_iso = observed_until.isoformat()

    encoded_bucket = quote(bucket_id, safe='')
    encoded_end = quote(observed_until_iso, safe='')

    # 1. Count before
    count_url = (
        f'{base_url}/api/0/buckets/{encoded_bucket}/events/count'
        f'?end={encoded_end}'
    )
    count_before_data, len_count1 = _http_get_json(
        count_url, timeout, max_response_bytes, opener=opener
    )
    if not isinstance(count_before_data, int) or isinstance(
        count_before_data, bool
    ):
        raise ReportMalformedPayloadError(
            'ActivityWatch events/count response must be integer'
        )
    count_before = count_before_data

    # 2. Events list without start parameter
    events_url = (
        f'{base_url}/api/0/buckets/{encoded_bucket}/events'
        f'?limit={fetch_limit}&end={encoded_end}'
    )
    events_data, len_events = _http_get_json(
        events_url, timeout, max_response_bytes, opener=opener
    )
    if not isinstance(events_data, list):
        raise ReportMalformedPayloadError(
            'ActivityWatch events response must be list'
        )

    returned_count = len(events_data)
    validate_source_events_limit(
        returned_count,
        is_afk=is_afk,
        specialized_limit=max_specialized_limit,
        afk_limit=max_afk_limit,
    )

    # Check duplicate IDs inside response safely
    raw_ids: list[int] = []
    for ev in events_data:
        if (
            not isinstance(ev, Mapping)
            or 'id' not in ev
            or isinstance(ev['id'], bool)
            or not isinstance(ev['id'], int)
        ):
            raise ReportMalformedPayloadError(
                'Event payload missing valid integer ID'
            )
        raw_ids.append(ev['id'])

    unique_ids_count = len(set(raw_ids))
    if unique_ids_count != returned_count:
        raise ReportMalformedPayloadError(
            'Duplicate event ID inside bucket response'
        )

    # 3. Count after
    count_after_data, len_count2 = _http_get_json(
        count_url, timeout, max_response_bytes, opener=opener
    )
    if not isinstance(count_after_data, int) or isinstance(
        count_after_data, bool
    ):
        raise ReportMalformedPayloadError(
            'ActivityWatch events/count response must be integer'
        )
    count_after = count_after_data

    # 4. Check 4 numbers
    is_verified = verify_four_numbers(
        count_before=count_before,
        returned_count=returned_count,
        unique_ids_count=unique_ids_count,
        count_after=count_after,
        budget_limit=budget_limit,
    )
    if not is_verified:
        raise ReportIncompleteError(
            f'Event count verification failed: '
            f'before={count_before}, rows={returned_count}, '
            f'unique={unique_ids_count}, after={count_after}'
        )

    # 5. Parse raw records
    parsed_events: list[RawEventRecord] = []
    for raw_ev in events_data:
        parsed_events.append(validate_raw_event_payload(raw_ev))

    total_bytes = len_count1 + len_events + len_count2
    return (tuple(parsed_events), total_bytes)


# === Full Reporting Snapshot Reader (SRC-04, SRC-05) ===


def _read_snapshot_attempt(
    catalog: SourceCatalog,
    base_url: str,
    observed_until: datetime,
    settings: ReportSettings,
    opener: OpenerDirector | None = None,
) -> tuple[
    dict[str, tuple[RawEventRecord, ...]],
    datetime | None,
    str,
    int,
]:
    """Execute one complete snapshot reading pass across all sources."""
    events_by_bucket: dict[str, tuple[RawEventRecord, ...]] = {}
    total_bytes = 0

    # Collect distinct buckets to read: all specialized entries, paired AFK,
    # and all valid production buckets (for boundary computation)
    buckets_to_read: list[tuple[str, bool]] = []
    seen_buckets: set[str] = set()

    for entry in catalog.entries:
        if (
            entry.source_kind == ReportSourceKind.PRODUCTION
            and entry.client == PRODUCTION_BUCKET_TARGET.client
            and entry.bucket_type == PRODUCTION_BUCKET_TARGET.bucket_type
            and entry.hostname == entry.host_suffix
            and entry.bucket_id not in seen_buckets
        ):
            buckets_to_read.append((entry.bucket_id, False))
            seen_buckets.add(entry.bucket_id)
    for entry in catalog.specialized_entries:
        if entry.bucket_id not in seen_buckets:
            buckets_to_read.append((entry.bucket_id, False))
            seen_buckets.add(entry.bucket_id)
        if entry.afk_bucket_id and entry.afk_bucket_id not in seen_buckets:
            buckets_to_read.append((entry.afk_bucket_id, True))
            seen_buckets.add(entry.afk_bucket_id)

    for bucket_id, is_afk in buckets_to_read:
        events, b_bytes = read_bucket_events_with_verification(
            bucket_id=bucket_id,
            base_url=base_url,
            observed_until=observed_until,
            is_afk=is_afk,
            max_specialized_limit=settings.max_specialized_events_per_source,
            max_afk_limit=settings.max_afk_events_per_source,
            max_response_bytes=settings.max_response_bytes,
            timeout=settings.default_read_timeout_seconds,
            opener=opener,
        )
        events_by_bucket[bucket_id] = events
        total_bytes += b_bytes
        validate_total_response_bytes_limit(
            total_bytes, settings.max_total_response_bytes
        )

    initial_boundary, initial_boundary_status = compute_production_boundary(
        catalog, events_by_bucket, observed_until
    )

    # Post-read verification (SRC-05, TLR-02):
    # (a) Re-fetch buckets mapping and verify metadata constancy
    buckets_url = f'{base_url}/api/0/buckets/'
    post_buckets_data, len_post = _http_get_json(
        buckets_url,
        settings.default_read_timeout_seconds,
        settings.max_response_bytes,
        opener=opener,
    )
    total_bytes += len_post
    validate_total_response_bytes_limit(
        total_bytes, settings.max_total_response_bytes
    )

    if not isinstance(post_buckets_data, Mapping):
        raise ReportMalformedPayloadError(
            'ActivityWatch buckets response must be mapping'
        )

    for bucket_id, _ in buckets_to_read:
        if bucket_id not in post_buckets_data:
            raise ReportIncompleteError(
                'Bucket disappeared during snapshot read'
            )
        b_meta = post_buckets_data[bucket_id]
        if not isinstance(b_meta, Mapping):
            raise ReportIncompleteError(
                'Bucket metadata corrupted during snapshot read'
            )
        cat_entry = catalog.get_entry(bucket_id)
        if cat_entry is not None and (
            str(b_meta.get('client', '')) != cat_entry.client
            or str(b_meta.get('type', '')) != cat_entry.bucket_type
            or str(b_meta.get('hostname', '')) != cat_entry.hostname
        ):
            raise ReportIncompleteError(
                'Bucket metadata changed during snapshot read'
            )

    # (b) Re-confirm minimal production timestamp with second probe
    production_buckets = [
        e.bucket_id
        for e in catalog.entries
        if e.source_kind == ReportSourceKind.PRODUCTION
        and e.client == PRODUCTION_BUCKET_TARGET.client
        and e.bucket_type == PRODUCTION_BUCKET_TARGET.bucket_type
        and e.hostname == e.host_suffix
    ]
    if production_buckets and initial_boundary is not None:
        rechecked_events_by_bucket: dict[str, tuple[RawEventRecord, ...]] = {}
        for p_bucket in production_buckets:
            p_events, p_bytes = read_bucket_events_with_verification(
                bucket_id=p_bucket,
                base_url=base_url,
                observed_until=observed_until,
                is_afk=False,
                max_specialized_limit=settings.max_specialized_events_per_source,
                max_afk_limit=settings.max_afk_events_per_source,
                max_response_bytes=settings.max_response_bytes,
                timeout=settings.default_read_timeout_seconds,
                opener=opener,
            )
            rechecked_events_by_bucket[p_bucket] = p_events
            total_bytes += p_bytes
            validate_total_response_bytes_limit(
                total_bytes, settings.max_total_response_bytes
            )

        post_boundary, _ = compute_production_boundary(
            catalog, rechecked_events_by_bucket, observed_until
        )
        if post_boundary != initial_boundary:
            raise ReportIncompleteError(
                'Production boundary timestamp changed during snapshot read'
            )

    return (
        events_by_bucket,
        initial_boundary,
        initial_boundary_status,
        total_bytes,
    )


def read_full_reporting_snapshot(
    catalog: SourceCatalog,
    base_url: str = 'http://localhost:5600',
    observed_until: datetime | None = None,
    settings: ReportSettings | None = None,
    opener: OpenerDirector | None = None,
) -> FullSnapshotResult:
    """Read verified snapshot of all sources with single retry on mismatch."""
    active_settings = settings or ReportSettings()
    target_observed_until = observed_until or datetime.now(UTC)
    read_started_at = datetime.now(UTC)

    try:
        events_by_bucket, boundary, boundary_status, total_bytes = (
            _read_snapshot_attempt(
                catalog,
                base_url,
                target_observed_until,
                active_settings,
                opener=opener,
            )
        )
    except ReportIncompleteError as first_err:
        # Allowed at most ONE retry of the entire snapshot (SRC-04)
        try:
            events_by_bucket, boundary, boundary_status, total_bytes = (
                _read_snapshot_attempt(
                    catalog,
                    base_url,
                    target_observed_until,
                    active_settings,
                    opener=opener,
                )
            )
        except ReportIncompleteError:
            raise ReportIncompleteError(
                'Snapshot reading failed after retry'
            ) from first_err

    read_finished_at = datetime.now(UTC)

    valid_last_updated = [
        e.last_updated for e in catalog.entries if e.last_updated is not None
    ]
    max_last_updated = max(valid_last_updated) if valid_last_updated else None

    return FullSnapshotResult(
        catalog=catalog,
        observed_until=target_observed_until,
        read_started_at=read_started_at,
        read_finished_at=read_finished_at,
        events_by_bucket=events_by_bucket,
        production_boundary=boundary,
        boundary_status=boundary_status,
        total_response_bytes=total_bytes,
        last_updated=max_last_updated,
    )


# === Standard Window Comparison Query (SRC-07, TIME-16, LIMIT-03) ===


def read_standard_comparison_events(
    bucket_id: str,
    base_url: str = 'http://localhost:5600',
    observed_until: datetime | None = None,
    settings: ReportSettings | None = None,
    timeout: float = STANDARD_QUERY_TIMEOUT_SECONDS,
    opener: OpenerDirector | None = None,
) -> tuple[RawEventRecord, ...]:
    """Execute wide standard query (app=Orca from 1970 to observed_until)."""
    active_settings = settings or ReportSettings()
    target_observed_until = observed_until or datetime.now(UTC)
    encoded_bucket = quote(bucket_id, safe='')

    # 1. Pre-check standard bucket event count
    count_url = f'{base_url}/api/0/buckets/{encoded_bucket}/events/count'
    count_data, _ = _http_get_json(
        count_url,
        active_settings.default_read_timeout_seconds,
        active_settings.max_response_bytes,
        opener=opener,
    )
    if not isinstance(count_data, int) or isinstance(count_data, bool):
        raise ReportMalformedPayloadError(
            'Standard events count response must be integer'
        )

    validate_standard_query_events_limit(
        count_data, active_settings.max_standard_events_query
    )

    # 2. Build fixed POST query
    observed_until_iso = target_observed_until.isoformat()
    time_period = f'1970-01-01T00:00:00+00:00/{observed_until_iso}'

    query_lines = [
        f'events = query_bucket("{bucket_id}");',
        'events = filter_keyvals(events, "app", ["Orca"]);',
        'RETURN = events;',
    ]
    query_payload = {
        'timeperiods': [time_period],
        'query': query_lines,
    }
    body_bytes = json.dumps(query_payload).encode('utf-8')

    req = Request(  # noqa: S310 (local endpoint)
        f'{base_url}/api/0/query/',
        data=body_bytes,
        headers={
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': USER_AGENT,
        },
    )

    open_fn = opener.open if opener is not None else urlopen
    try:
        with open_fn(req, timeout=timeout) as resp:  # noqa: S310
            status = resp.status
            chunks: list[bytes] = []
            total_read = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                total_read += len(chunk)
                if total_read > active_settings.max_response_bytes:
                    raise ReportLimitError(
                        f'Standard query response bytes ({total_read}) '
                        f'exceeds limit ({active_settings.max_response_bytes})'
                    )
            resp_body = b''.join(chunks)
    except HTTPError as err:
        err.close()
        raise ReportApiError(
            f'Standard query returned HTTP {err.code}'
        ) from None
    except (URLError, TimeoutError, OSError):
        raise ReportApiError('Unable to connect for standard query') from None

    if status != 200:
        raise ReportApiError(f'Standard query returned HTTP {status}')

    try:
        result_data = json.loads(resp_body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise ReportMalformedPayloadError(
            'Malformed standard query JSON response'
        ) from err

    if not isinstance(result_data, list) or not result_data:
        return ()

    raw_events_list = result_data[0]
    if not isinstance(raw_events_list, list):
        raise ReportMalformedPayloadError(
            'Standard query result is not an event list'
        )

    validate_standard_query_events_limit(
        len(raw_events_list), active_settings.max_standard_events_query
    )

    parsed_events: list[RawEventRecord] = []
    for raw_ev in raw_events_list:
        parsed_ev = validate_raw_event_payload(raw_ev)
        if parsed_ev.timestamp < EPOCH_UTC:
            raise ReportIncompleteError(
                'Standard window history contains events before 1970'
            )
        parsed_events.append(parsed_ev)

    return tuple(parsed_events)
