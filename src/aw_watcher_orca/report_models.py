"""Define core data models, validation functions, and errors for reporting."""

import math
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final, Self

from aw_watcher_orca.errors import OrcaCoreError

# === Canonical Reason Constants ===

ABSENT_UNVERIFIED_STATUS: Final[str] = 'absent_unverified'
LIMIT_EXCEEDED_REASON: Final[str] = 'limit_exceeded'
MALFORMED_EVENT_REASON: Final[str] = 'malformed_event'
INCOMPLETE_SOURCE_REASON: Final[str] = 'incomplete_source'
QUALITY_CONFLICT_REASON: Final[str] = 'quality_conflict'
UNKNOWN_BOUNDARY_REASON: Final[str] = 'unknown_boundary'
CALCULATION_BUSY_REASON: Final[str] = 'calculation_busy'
API_UNAVAILABLE_REASON: Final[str] = 'api_unavailable'
STANDARD_COMPARISON_UNAVAILABLE_REASON: Final[str] = (
    'standard_comparison_unavailable'
)

AFK_STATUS_AFK: Final[str] = 'afk'
AFK_STATUS_NOT_AFK: Final[str] = 'not-afk'
KNOWN_AFK_STATUSES: Final[frozenset[str]] = frozenset(
    {AFK_STATUS_AFK, AFK_STATUS_NOT_AFK}
)

KNOWN_SCHEMA_SOURCES: Final[frozenset[str]] = frozenset(
    {'orca-cli-v1', 'orca-data-v1'}
)


# === Error Taxonomy ===


class ReportError(OrcaCoreError):
    """Base exception for all reporting errors."""


class ReportLimitError(ReportError):
    """Represent budget limit exceeded failures (LIMIT-01..06)."""


class ReportIncompleteError(ReportError):
    """Represent missing sources, missing AFK pairs, or broken metadata."""


class ReportMalformedPayloadError(ReportError):
    """Represent structurally invalid event payloads, bad fields or types."""


class ReportBusyError(ReportError):
    """Represent concurrent calculation rejection."""


class ReportApiError(ReportError):
    """Represent ActivityWatch API transport or status failures."""


# === Enumerations and Kinds ===


class ReportSourceKind(StrEnum):
    """Represent supported reporting source kinds."""

    TEST = 'test'
    PRODUCTION = 'production'
    STANDARD = 'standard'
    AFK = 'afk'


class AfkStatus(StrEnum):
    """Represent closed AFK statuses."""

    AFK = 'afk'
    NOT_AFK = 'not-afk'


# === Core Data Models ===


@dataclass(frozen=True, slots=True)
class EventKey:
    """Represent unique bucket and event identifier pair."""

    bucket_id: str
    event_id: int


@dataclass(frozen=True, slots=True)
class RawEventRecord:
    """Represent one validated raw ActivityWatch event."""

    event_id: int
    timestamp: datetime
    duration_us: int
    data: dict[str, object]


@dataclass(frozen=True, slots=True)
class TimeInterval:
    """Represent one half-open time interval [start_us, end_us)."""

    start_us: int
    end_us: int

    def __post_init__(self) -> None:
        """Validate interval ordering."""
        if self.start_us > self.end_us:
            raise ValueError(
                f'Invalid interval: start_us ({self.start_us}) '
                f'> end_us ({self.end_us})'
            )

    @property
    def duration_us(self) -> int:
        """Return span in microseconds."""
        return self.end_us - self.start_us

    @property
    def is_empty(self) -> bool:
        """Report whether interval duration is zero."""
        return self.start_us == self.end_us


@dataclass(frozen=True, slots=True)
class SpecializedIdentity:
    """Represent unique project identity with NFC normalized paths."""

    host_suffix: str
    bucket_id: str
    source_kind: str
    repo: str
    worktree: str

    @classmethod
    def create(
        cls,
        host_suffix: str,
        bucket_id: str,
        source_kind: str,
        repo: str,
        worktree: str,
    ) -> Self:
        """Create identity with NFC-normalized repo and worktree."""
        return cls(
            host_suffix=host_suffix,
            bucket_id=bucket_id,
            source_kind=source_kind,
            repo=unicodedata.normalize('NFC', repo),
            worktree=unicodedata.normalize('NFC', worktree),
        )


@dataclass(frozen=True, slots=True)
class SourceCatalogEntry:
    """Represent one cataloged source bucket entry."""

    host_suffix: str
    bucket_id: str
    source_kind: str
    client: str
    bucket_type: str
    hostname: str
    afk_bucket_id: str | None
    is_paired: bool
    is_corrupted: bool
    corruption_reason: str | None = None
    last_updated: datetime | None = None


@dataclass(frozen=True, slots=True)
class ReportFreshness:
    """Hold data freshness evaluation metrics (OUT-07)."""

    max_event_end: datetime | None
    last_updated: datetime | None
    observed_until: datetime
    stale: bool


@dataclass(frozen=True, slots=True)
class InventorySummary:
    """Represent source inventory counts (OUT-02)."""

    total_scanned: int
    supported: int
    paired: int
    ignored: int
    corrupted: int


@dataclass(frozen=True, slots=True)
class DataQualityCounter:
    """Represent distinct calculation event/segment counters (OUT-02/03)."""

    raw_events: int
    period_events: int
    matching_events: int
    contributing_events: int
    derived_segments: int


@dataclass(frozen=True, slots=True)
class QualityBreakdown:
    """Represent non-overlapping duration categories for a source (OUT-04)."""

    neutral_us: int
    confirmed_afk_us: int
    unknown_afk_us: int
    noise_us: int
    project_contributing_us: int
    is_reliable: bool
    unreliable_reason: str | None = None


@dataclass(frozen=True, slots=True)
class PeriodInterval:
    """Represent calendar reporting period in UTC with zone name."""

    start_utc: datetime
    end_utc: datetime
    zone_name: str


@dataclass(frozen=True, slots=True)
class NoiseFilterResult:
    """Represent the result of noise threshold filtering on intervals."""

    kept_intervals: tuple[TimeInterval, ...]
    noise_intervals: tuple[TimeInterval, ...]
    kept_duration_us: int
    noise_duration_us: int


# === Parsing and Validation Functions (SRC-06, SRC-07, TLR-05) ===


def parse_event_id(value: object) -> int:
    """Parse and validate positive integer event ID.

    Reject booleans, None, strings, floats, zero, and negative values.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReportMalformedPayloadError(
            f'Event ID must be positive int, got {type(value).__name__}'
        )
    if value <= 0:
        raise ReportMalformedPayloadError(
            f'Event ID must be positive, got {value}'
        )
    return value


def parse_event_timestamp(value: object) -> datetime:
    """Parse and validate timezone-aware ISO timestamp or datetime object."""
    if isinstance(value, datetime):
        if value.tzinfo is None or value.tzinfo.utcoffset(None) is None:
            raise ReportMalformedPayloadError(
                'Event timestamp must be timezone-aware datetime'
            )
        return value.astimezone(UTC)
    if not isinstance(value, str):
        raise ReportMalformedPayloadError(
            f'Event timestamp must be ISO string or datetime, '
            f'got {type(value).__name__}'
        )

    # Check sub-microsecond precision before fromisoformat (Finding 10)
    clean_str = value.replace('Z', '+00:00') if value.endswith('Z') else value
    if '.' in clean_str or ',' in clean_str:
        sep = '.' if '.' in clean_str else ','
        frac_part = clean_str.split(sep, 1)[1]
        for tz_sep in ('+', '-', 'Z'):
            if tz_sep in frac_part:
                frac_part = frac_part.split(tz_sep, 1)[0]
                break
        if len(frac_part) > 6:
            raise ReportMalformedPayloadError(
                f'Timestamp has sub-microsecond precision: {value}'
            )

    try:
        iso_str = clean_str
        dt_val = datetime.fromisoformat(iso_str)
    except (ValueError, TypeError) as err:
        raise ReportMalformedPayloadError(
            f'Malformed timestamp string: {value}'
        ) from err
    if dt_val.tzinfo is None or dt_val.tzinfo.utcoffset(None) is None:
        raise ReportMalformedPayloadError(
            f'Event timestamp must include timezone: {value}'
        )
    return dt_val.astimezone(UTC)


def parse_decimal_microseconds(value: object) -> int:
    """Parse duration in seconds to exact non-negative integer microseconds.

    Reject booleans, None, NaN, inf, negatives, and sub-microsecond fractions.
    """
    if isinstance(value, bool) or value is None:
        raise ReportMalformedPayloadError(
            f'Duration must be numeric, got {type(value).__name__}'
        )
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ReportMalformedPayloadError(
                'Duration must be finite number, got NaN or Inf'
            )
        str_val = str(value)
    elif isinstance(value, (int, str, Decimal)):
        str_val = str(value)
    else:
        raise ReportMalformedPayloadError(
            f'Duration must be numeric or decimal string, '
            f'got {type(value).__name__}'
        )

    try:
        dec = Decimal(str_val)
    except InvalidOperation as err:
        raise ReportMalformedPayloadError(
            f'Malformed duration numeric value: {value}'
        ) from err

    if dec.is_nan() or dec.is_infinite():
        raise ReportMalformedPayloadError(
            'Duration must be finite number, got NaN or Inf'
        )
    if dec < 0:
        raise ReportMalformedPayloadError(
            f'Duration must be non-negative, got {dec}'
        )

    us_dec = dec * Decimal('1000000')
    if us_dec != us_dec.to_integral_value():
        raise ReportMalformedPayloadError(
            f'Duration has sub-microsecond precision: {value}'
        )

    return int(us_dec)


def parse_event_duration_us(value: object) -> int:
    """Parse and validate event duration in microseconds."""
    return parse_decimal_microseconds(value)


def validate_raw_event_payload(payload: object) -> RawEventRecord:
    """Validate full raw event mapping and return RawEventRecord."""
    if not isinstance(payload, Mapping):
        raise ReportMalformedPayloadError(
            'Raw event payload must be a mapping, '
            f'got {type(payload).__name__}'
        )
    if (
        'id' not in payload
        or 'timestamp' not in payload
        or 'duration' not in payload
        or 'data' not in payload
    ):
        raise ReportMalformedPayloadError(
            'Raw event missing required fields (id, timestamp, duration, data)'
        )

    data_val = payload['data']
    if not isinstance(data_val, dict):
        raise ReportMalformedPayloadError(
            f'Raw event data field must be dict, got {type(data_val).__name__}'
        )

    event_id = parse_event_id(payload['id'])
    timestamp = parse_event_timestamp(payload['timestamp'])
    duration_us = parse_event_duration_us(payload['duration'])

    return RawEventRecord(
        event_id=event_id,
        timestamp=timestamp,
        duration_us=duration_us,
        data=dict(data_val),
    )


def validate_afk_event_data(data: Mapping[str, object]) -> str:
    """Validate AFK event data payload and return status string."""
    if 'status' not in data:
        raise ReportMalformedPayloadError(
            "AFK event data missing 'status' field"
        )
    status_val = data['status']
    if not isinstance(status_val, str) or status_val not in KNOWN_AFK_STATUSES:
        raise ReportMalformedPayloadError(
            f"AFK event status must be 'afk' or 'not-afk', got {status_val!r}"
        )
    return status_val


def validate_standard_event_data(
    data: Mapping[str, object],
) -> tuple[str, str]:
    """Validate standard window event data and return (app, title)."""
    if 'app' not in data or 'title' not in data:
        raise ReportMalformedPayloadError(
            "Standard event data missing 'app' or 'title' fields"
        )
    app_val = data['app']
    title_val = data['title']
    if not isinstance(app_val, str) or not isinstance(title_val, str):
        raise ReportMalformedPayloadError(
            'Standard event app and title must be strings'
        )
    return (app_val, title_val)


def validate_specialized_event_data(
    data: Mapping[str, object],
) -> tuple[bool, str, str, str, str | None]:
    """Validate specialized Orca event data payload.

    Returns:
        (is_active, repo, worktree, title, schema_source)
    """
    if 'app' not in data:
        raise ReportMalformedPayloadError(
            "Specialized event data missing 'app' field"
        )
    if data['app'] != 'Orca':
        raise ReportMalformedPayloadError(
            f"Specialized event app must be 'Orca', got {data['app']!r}"
        )

    for field in ('repo', 'worktree', 'title'):
        if field not in data:
            raise ReportMalformedPayloadError(
                f"Specialized event missing required field '{field}'"
            )
        if not isinstance(data[field], str):
            raise ReportMalformedPayloadError(
                f"Specialized event field '{field}' must be string"
            )

    repo = str(data['repo'])
    worktree = str(data['worktree'])
    title = str(data['title'])

    schema_source: str | None = None
    if 'schema_source' in data:
        raw_schema = data['schema_source']
        if not isinstance(raw_schema, str):
            raise ReportMalformedPayloadError(
                'schema_source field must be string'
            )
        if raw_schema not in KNOWN_SCHEMA_SOURCES:
            raise ReportMalformedPayloadError(
                f'Unknown schema_source form: {raw_schema!r}'
            )
        schema_source = raw_schema

    # Check active vs neutral vs corrupted
    is_all_empty = (repo == '') and (worktree == '') and (title == '')
    is_all_non_empty = (repo != '') and (worktree != '') and (title != '')

    if is_all_empty:
        return (False, '', '', '', schema_source)
    if is_all_non_empty:
        return (True, repo, worktree, title, schema_source)

    raise ReportMalformedPayloadError(
        f'Partially empty project attribution triplet: '
        f'repo={repo!r}, worktree={worktree!r}, title={title!r}'
    )
