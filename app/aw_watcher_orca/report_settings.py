"""Define canonical reporting budgets, limits, and configuration settings."""

from dataclasses import dataclass
from typing import Final

# === Canonical Limit Constants (LIMIT-01..06) ===

MAX_CALENDAR_DAYS: Final[int] = 31
MAX_SUPPORTED_HOSTS: Final[int] = 8
MAX_INVENTORY_BUCKETS: Final[int] = 32

MAX_SPECIALIZED_EVENTS_PER_SOURCE: Final[int] = 100_000
MAX_AFK_EVENTS_PER_SOURCE: Final[int] = 50_000
MAX_STANDARD_EVENTS_QUERY: Final[int] = 500_000

MAX_RESPONSE_BYTES: Final[int] = 32 * 1024 * 1024
MAX_TOTAL_RESPONSE_BYTES: Final[int] = 128 * 1024 * 1024
MAX_REQUEST_BYTES: Final[int] = 16 * 1024
MAX_SERIALIZED_RESPONSE_BYTES: Final[int] = 8 * 1024 * 1024
MAX_CONCURRENT_CONNECTIONS: Final[int] = 4

MAX_CONCURRENT_CALCULATIONS: Final[int] = 1
SNAPSHOT_TTL_SECONDS: Final[float] = 900.0
MAX_SNAPSHOT_BYTES: Final[int] = 32 * 1024 * 1024
DEFAULT_READ_TIMEOUT_SECONDS: Final[float] = 10.0
STANDARD_QUERY_TIMEOUT_SECONDS: Final[float] = 45.0
TOTAL_CALCULATION_TIMEOUT_SECONDS: Final[float] = 60.0

DEFAULT_TIMEZONE: Final[str] = 'Europe/Minsk'
DEFAULT_ACTIVITYWATCH_BASE_URL: Final[str] = 'http://localhost:5600'
NOISE_THRESHOLD_MICROSECONDS: Final[int] = 1_000_000
NOISE_THRESHOLD_SECONDS: Final[float] = 1.0
FRESHNESS_THRESHOLD_SECONDS: Final[float] = 30.0


# === Settings Dataclass ===


@dataclass(frozen=True, slots=True)
class ReportSettings:
    """Hold immutable reporting budgets and settings."""

    max_calendar_days: int = MAX_CALENDAR_DAYS
    max_supported_hosts: int = MAX_SUPPORTED_HOSTS
    max_inventory_buckets: int = MAX_INVENTORY_BUCKETS
    max_specialized_events_per_source: int = MAX_SPECIALIZED_EVENTS_PER_SOURCE
    max_afk_events_per_source: int = MAX_AFK_EVENTS_PER_SOURCE
    max_standard_events_query: int = MAX_STANDARD_EVENTS_QUERY
    max_response_bytes: int = MAX_RESPONSE_BYTES
    max_total_response_bytes: int = MAX_TOTAL_RESPONSE_BYTES
    max_concurrent_calculations: int = MAX_CONCURRENT_CALCULATIONS
    snapshot_ttl_seconds: float = SNAPSHOT_TTL_SECONDS
    max_snapshot_bytes: int = MAX_SNAPSHOT_BYTES
    max_request_bytes: int = MAX_REQUEST_BYTES
    max_serialized_response_bytes: int = MAX_SERIALIZED_RESPONSE_BYTES
    max_concurrent_connections: int = MAX_CONCURRENT_CONNECTIONS
    default_read_timeout_seconds: float = DEFAULT_READ_TIMEOUT_SECONDS
    standard_query_timeout_seconds: float = STANDARD_QUERY_TIMEOUT_SECONDS
    total_calculation_timeout_seconds: float = (
        TOTAL_CALCULATION_TIMEOUT_SECONDS
    )
    default_timezone: str = DEFAULT_TIMEZONE
    base_url: str = DEFAULT_ACTIVITYWATCH_BASE_URL
    noise_threshold_us: int = NOISE_THRESHOLD_MICROSECONDS
    freshness_threshold_seconds: float = FRESHNESS_THRESHOLD_SECONDS


# === Validation Helpers for Limits ===


def validate_calendar_days_limit(
    days: int, limit: int = MAX_CALENDAR_DAYS
) -> None:
    """Validate calendar days range against limit."""
    if days > limit:
        from aw_watcher_orca.report_models import ReportLimitError

        raise ReportLimitError(
            f'Requested calendar days ({days}) exceeds budget limit ({limit})'
        )


def validate_supported_hosts_limit(
    hosts_count: int, limit: int = MAX_SUPPORTED_HOSTS
) -> None:
    """Validate supported hosts count against limit."""
    if hosts_count > limit:
        from aw_watcher_orca.report_models import ReportLimitError

        raise ReportLimitError(
            f'Supported hosts ({hosts_count}) exceeds limit ({limit})'
        )


def validate_inventory_buckets_limit(
    buckets_count: int, limit: int = MAX_INVENTORY_BUCKETS
) -> None:
    """Validate inventory buckets count against limit."""
    if buckets_count > limit:
        from aw_watcher_orca.report_models import ReportLimitError

        raise ReportLimitError(
            f'Inventory buckets ({buckets_count}) exceeds limit ({limit})'
        )


def validate_source_events_limit(
    event_count: int,
    is_afk: bool,
    specialized_limit: int = MAX_SPECIALIZED_EVENTS_PER_SOURCE,
    afk_limit: int = MAX_AFK_EVENTS_PER_SOURCE,
) -> None:
    """Validate source event count against specialized or AFK limit."""
    limit = afk_limit if is_afk else specialized_limit
    source_type = 'AFK' if is_afk else 'Specialized'
    if event_count > limit:
        from aw_watcher_orca.report_models import ReportLimitError

        raise ReportLimitError(
            f'{source_type} event count ({event_count}) '
            f'exceeds limit ({limit})'
        )


def validate_standard_query_events_limit(
    event_count: int, limit: int = MAX_STANDARD_EVENTS_QUERY
) -> None:
    """Validate standard window query event count against limit."""
    if event_count > limit:
        from aw_watcher_orca.report_models import ReportLimitError

        raise ReportLimitError(
            f'Standard query events ({event_count}) exceeds limit ({limit})'
        )


def validate_response_bytes_limit(
    body_bytes: int, limit: int = MAX_RESPONSE_BYTES
) -> None:
    """Validate single response size against limit."""
    if body_bytes > limit:
        from aw_watcher_orca.report_models import ReportLimitError

        raise ReportLimitError(
            f'Response body bytes ({body_bytes}) exceeds limit ({limit})'
        )


def validate_total_response_bytes_limit(
    total_bytes: int, limit: int = MAX_TOTAL_RESPONSE_BYTES
) -> None:
    """Validate total response bytes across calculation against limit."""
    if total_bytes > limit:
        from aw_watcher_orca.report_models import ReportLimitError

        raise ReportLimitError(
            f'Total response bytes ({total_bytes}) exceeds limit ({limit})'
        )
