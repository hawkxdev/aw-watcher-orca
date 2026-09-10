"""Test report models, settings, and payload validation."""

import ast
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from aw_watcher_orca.report_models import (
    AFK_STATUS_AFK,
    AFK_STATUS_NOT_AFK,
    ReportApiError,
    ReportBusyError,
    ReportError,
    ReportIncompleteError,
    ReportLimitError,
    ReportMalformedPayloadError,
    SpecializedIdentity,
    TimeInterval,
    parse_decimal_microseconds,
    parse_event_duration_us,
    parse_event_id,
    parse_event_timestamp,
    validate_afk_event_data,
    validate_raw_event_payload,
    validate_specialized_event_data,
    validate_standard_event_data,
)
from aw_watcher_orca.report_settings import (
    DEFAULT_ACTIVITYWATCH_BASE_URL,
    DEFAULT_READ_TIMEOUT_SECONDS,
    DEFAULT_TIMEZONE,
    FRESHNESS_THRESHOLD_SECONDS,
    MAX_AFK_EVENTS_PER_SOURCE,
    MAX_CALENDAR_DAYS,
    MAX_CONCURRENT_CALCULATIONS,
    MAX_INVENTORY_BUCKETS,
    MAX_RESPONSE_BYTES,
    MAX_SNAPSHOT_BYTES,
    MAX_SPECIALIZED_EVENTS_PER_SOURCE,
    MAX_STANDARD_EVENTS_QUERY,
    MAX_SUPPORTED_HOSTS,
    MAX_TOTAL_RESPONSE_BYTES,
    NOISE_THRESHOLD_MICROSECONDS,
    NOISE_THRESHOLD_SECONDS,
    SNAPSHOT_TTL_SECONDS,
    STANDARD_QUERY_TIMEOUT_SECONDS,
    TOTAL_CALCULATION_TIMEOUT_SECONDS,
    ReportSettings,
    validate_calendar_days_limit,
    validate_inventory_buckets_limit,
    validate_response_bytes_limit,
    validate_source_events_limit,
    validate_standard_query_events_limit,
    validate_supported_hosts_limit,
    validate_total_response_bytes_limit,
)

# === Settings Budget Tests (LIMIT-01..06) ===


def test_report_settings_has_no_import_side_effects() -> None:
    """Verify report_settings AST does not perform I/O or network on import."""
    settings_path = (
        Path(__file__).parent.parent / 'aw_watcher_orca' / 'report_settings.py'
    )
    tree = ast.parse(settings_path.read_text(encoding='utf-8'))
    for node in tree.body:
        assert isinstance(
            node,
            (
                ast.Import,
                ast.ImportFrom,
                ast.Assign,
                ast.AnnAssign,
                ast.FunctionDef,
                ast.ClassDef,
                ast.Expr,
            ),
        ), f'Unexpected top-level node: {type(node)}'


def test_report_settings_constants_values() -> None:
    """Verify canonical budget constants per LIMIT-01..06."""
    assert MAX_CALENDAR_DAYS == 31
    assert MAX_SUPPORTED_HOSTS == 8
    assert MAX_INVENTORY_BUCKETS == 32
    assert MAX_SPECIALIZED_EVENTS_PER_SOURCE == 100_000
    assert MAX_AFK_EVENTS_PER_SOURCE == 50_000
    assert MAX_STANDARD_EVENTS_QUERY == 500_000
    assert MAX_RESPONSE_BYTES == 32 * 1024 * 1024
    assert MAX_TOTAL_RESPONSE_BYTES == 128 * 1024 * 1024
    assert MAX_CONCURRENT_CALCULATIONS == 1
    assert SNAPSHOT_TTL_SECONDS == 900.0
    assert MAX_SNAPSHOT_BYTES == 32 * 1024 * 1024
    assert DEFAULT_READ_TIMEOUT_SECONDS == 10.0
    assert STANDARD_QUERY_TIMEOUT_SECONDS == 45.0
    assert TOTAL_CALCULATION_TIMEOUT_SECONDS == 60.0
    assert DEFAULT_TIMEZONE == 'Europe/Minsk'
    assert DEFAULT_ACTIVITYWATCH_BASE_URL == 'http://localhost:5600'
    assert NOISE_THRESHOLD_MICROSECONDS == 1_000_000
    assert NOISE_THRESHOLD_SECONDS == 1.0
    assert FRESHNESS_THRESHOLD_SECONDS == 30.0


def test_report_settings_defaults() -> None:
    """Verify ReportSettings dataclass default instantiation."""
    settings = ReportSettings()
    assert settings.max_calendar_days == 31
    assert settings.max_supported_hosts == 8
    assert settings.max_inventory_buckets == 32
    assert settings.max_specialized_events_per_source == 100_000
    assert settings.max_afk_events_per_source == 50_000
    assert settings.max_standard_events_query == 500_000
    assert settings.max_response_bytes == 32 * 1024 * 1024
    assert settings.max_total_response_bytes == 128 * 1024 * 1024
    assert settings.default_timezone == 'Europe/Minsk'
    assert settings.base_url == 'http://localhost:5600'


def test_budget_calendar_days_limit_boundary() -> None:
    """Verify exact limit passes and limit+1 raises ReportLimitError."""
    validate_calendar_days_limit(31)
    with pytest.raises(ReportLimitError) as exc_info:
        validate_calendar_days_limit(32)
    assert '31' in str(exc_info.value)


def test_budget_supported_hosts_limit_boundary() -> None:
    """Verify exact host limit passes and limit+1 raises ReportLimitError."""
    validate_supported_hosts_limit(8)
    with pytest.raises(ReportLimitError) as exc_info:
        validate_supported_hosts_limit(9)
    assert '8' in str(exc_info.value)


def test_budget_inventory_buckets_limit_boundary() -> None:
    """Verify exact bucket limit passes and limit+1 raises ReportLimitError."""
    validate_inventory_buckets_limit(32)
    with pytest.raises(ReportLimitError) as exc_info:
        validate_inventory_buckets_limit(33)
    assert '32' in str(exc_info.value)


def test_budget_specialized_events_limit_boundary() -> None:
    """Verify specialized source events limit passes and limit+1 raises."""
    validate_source_events_limit(100_000, is_afk=False)
    with pytest.raises(ReportLimitError) as exc_info:
        validate_source_events_limit(100_001, is_afk=False)
    assert '100000' in str(exc_info.value)


def test_budget_afk_events_limit_boundary() -> None:
    """Verify exact AFK source events limit passes and limit+1 raises."""
    validate_source_events_limit(50_000, is_afk=True)
    with pytest.raises(ReportLimitError) as exc_info:
        validate_source_events_limit(50_001, is_afk=True)
    assert '50000' in str(exc_info.value)


def test_budget_standard_query_events_limit_boundary() -> None:
    """Verify exact standard query events limit passes and limit+1 raises."""
    validate_standard_query_events_limit(500_000)
    with pytest.raises(ReportLimitError) as exc_info:
        validate_standard_query_events_limit(500_001)
    assert '500000' in str(exc_info.value)


def test_budget_response_bytes_limit_boundary() -> None:
    """Verify exact response bytes limit passes and limit+1 raises."""
    validate_response_bytes_limit(32 * 1024 * 1024)
    with pytest.raises(ReportLimitError) as exc_info:
        validate_response_bytes_limit(32 * 1024 * 1024 + 1)
    assert '33554432' in str(exc_info.value)


def test_budget_total_response_bytes_limit_boundary() -> None:
    """Verify exact total response bytes limit passes and limit+1 raises."""
    validate_total_response_bytes_limit(128 * 1024 * 1024)
    with pytest.raises(ReportLimitError) as exc_info:
        validate_total_response_bytes_limit(128 * 1024 * 1024 + 1)
    assert '134217728' in str(exc_info.value)


# === Error Hierarchy Tests ===


def test_error_hierarchy() -> None:
    """Verify all report errors inherit from ReportError and OrcaCoreError."""
    assert issubclass(ReportLimitError, ReportError)
    assert issubclass(ReportIncompleteError, ReportError)
    assert issubclass(ReportMalformedPayloadError, ReportError)
    assert issubclass(ReportBusyError, ReportError)
    assert issubclass(ReportApiError, ReportError)


# === Event ID Parsing (SRC-06) ===


def test_parse_event_id_valid() -> None:
    """Verify valid positive integer event IDs."""
    assert parse_event_id(1) == 1
    assert parse_event_id(501) == 501
    assert parse_event_id(999999999) == 999999999


@pytest.mark.parametrize(
    'invalid_id',
    [
        True,
        False,
        None,
        '1',
        'abc',
        1.0,
        0,
        -1,
        -100,
        [],
        {},
    ],
)
def test_parse_event_id_invalid(invalid_id: object) -> None:
    """Verify invalid event IDs raise ReportMalformedPayloadError."""
    with pytest.raises(ReportMalformedPayloadError):
        parse_event_id(invalid_id)


# === Event Timestamp Parsing (SRC-06) ===


def test_parse_event_timestamp_valid() -> None:
    """Verify timezone-aware ISO string and datetime objects."""
    iso_str = '2026-09-08T12:30:45.123456+00:00'
    parsed = parse_event_timestamp(iso_str)
    assert parsed.tzinfo is not None
    assert parsed.year == 2026
    assert parsed.microsecond == 123456

    dt_obj = datetime(2026, 9, 8, 12, 30, 45, 123456, tzinfo=UTC)
    assert parse_event_timestamp(dt_obj) == dt_obj


@pytest.mark.parametrize(
    'invalid_ts',
    [
        '2026-09-08T12:30:45',  # naive
        datetime(2026, 9, 8, 12, 30, 45),  # naive datetime
        '2026-09-08T12:00:00.1234567+00:00',  # sub-microsecond precision
        None,
        123456789,
        True,
        'invalid-date',
        '',
    ],
)
def test_parse_event_timestamp_invalid(invalid_ts: object) -> None:
    """Verify naive, unparseable, or non-string timestamps raise."""
    with pytest.raises(ReportMalformedPayloadError):
        parse_event_timestamp(invalid_ts)


# === Event Duration Parsing (SRC-06) ===


def test_parse_decimal_microseconds_valid() -> None:
    """Verify exact microsecond representation without float roundoff."""
    assert parse_decimal_microseconds(0) == 0
    assert parse_decimal_microseconds(0.0) == 0
    assert parse_decimal_microseconds(1) == 1_000_000
    assert parse_decimal_microseconds(1.5) == 1_500_000
    assert parse_decimal_microseconds('1.5') == 1_500_000
    assert parse_decimal_microseconds(Decimal('0.123456')) == 123456
    assert parse_decimal_microseconds('0.000001') == 1


@pytest.mark.parametrize(
    'invalid_duration',
    [
        True,
        False,
        None,
        'nan',
        float('nan'),
        float('inf'),
        float('-inf'),
        -1,
        -0.000001,
        '-1.0',
        '0.0000001',
        'not-a-number',
        [],
    ],
)
def test_parse_decimal_microseconds_invalid(invalid_duration: object) -> None:
    """Verify invalid, negative, NaN/inf, or bad durations raise."""
    with pytest.raises(ReportMalformedPayloadError):
        parse_decimal_microseconds(invalid_duration)


def test_parse_event_duration_us() -> None:
    """Verify parse_event_duration_us delegates to parser."""
    assert parse_event_duration_us(10.0) == 10_000_000
    assert parse_event_duration_us(0) == 0


# === AFK Data Validation (SRC-06) ===


def test_validate_afk_event_data_valid() -> None:
    """Verify valid AFK status strings."""
    assert validate_afk_event_data({'status': 'afk'}) == AFK_STATUS_AFK
    assert validate_afk_event_data({'status': 'not-afk'}) == AFK_STATUS_NOT_AFK


@pytest.mark.parametrize(
    'invalid_afk_data',
    [
        {},
        {'status': 'idle'},
        {'status': 'active'},
        {'status': None},
        {'status': 1},
        {'status': True},
        {'other': 'value'},
    ],
)
def test_validate_afk_event_data_invalid(
    invalid_afk_data: dict[str, object],
) -> None:
    """Verify invalid AFK data payloads raise ReportMalformedPayloadError."""
    with pytest.raises(ReportMalformedPayloadError):
        validate_afk_event_data(invalid_afk_data)


# === Standard Event Data Validation (SRC-06) ===


def test_validate_standard_event_data_valid() -> None:
    """Verify standard window app and title strings."""
    app, title = validate_standard_event_data(
        {'app': 'Orca', 'title': 'main.py'}
    )
    assert app == 'Orca'
    assert title == 'main.py'


@pytest.mark.parametrize(
    'invalid_standard_data',
    [
        {},
        {'app': 'Orca'},
        {'title': 'main.py'},
        {'app': None, 'title': 'main.py'},
        {'app': 'Orca', 'title': 123},
        {'app': 123, 'title': 'main.py'},
    ],
)
def test_validate_standard_event_data_invalid(
    invalid_standard_data: dict[str, object],
) -> None:
    """Verify missing or non-string app/title in standard event data raise."""
    with pytest.raises(ReportMalformedPayloadError):
        validate_standard_event_data(invalid_standard_data)


# === Specialized Event Data Validation (SRC-07) ===


def test_validate_specialized_event_data_active() -> None:
    """Verify active specialized event data."""
    is_active, repo, worktree, title, schema = validate_specialized_event_data(
        {
            'app': 'Orca',
            'repo': 'my-repo',
            'worktree': 'main',
            'title': 'my-repo / main',
            'schema_source': 'orca-cli-v1',
        }
    )
    assert is_active is True
    assert repo == 'my-repo'
    assert worktree == 'main'
    assert title == 'my-repo / main'
    assert schema == 'orca-cli-v1'


def test_validate_specialized_event_data_neutral() -> None:
    """Verify neutral specialized event data with empty tuple."""
    is_active, repo, worktree, title, schema = validate_specialized_event_data(
        {
            'app': 'Orca',
            'repo': '',
            'worktree': '',
            'title': '',
        }
    )
    assert is_active is False
    assert repo == ''
    assert worktree == ''
    assert title == ''
    assert schema is None


@pytest.mark.parametrize(
    'corrupted_data',
    [
        # Partially empty project triplet (corrupted, not neutral)
        {'app': 'Orca', 'repo': 'my-repo', 'worktree': '', 'title': 'title'},
        {'app': 'Orca', 'repo': '', 'worktree': 'main', 'title': 'title'},
        {'app': 'Orca', 'repo': 'my-repo', 'worktree': 'main', 'title': ''},
        # Non-Orca app
        {'app': 'VSCode', 'repo': 'r', 'worktree': 'w', 'title': 't'},
        # Unknown non-empty schema_source
        {
            'app': 'Orca',
            'repo': 'r',
            'worktree': 'w',
            'title': 't',
            'schema_source': 'unknown-v2',
        },
        # Non-string schema_source (including None)
        {
            'app': 'Orca',
            'repo': 'r',
            'worktree': 'w',
            'title': 't',
            'schema_source': 123,
        },
        {
            'app': 'Orca',
            'repo': 'r',
            'worktree': 'w',
            'title': 't',
            'schema_source': None,
        },
        # Non-string triplet fields
        {'app': 'Orca', 'repo': 123, 'worktree': 'w', 'title': 't'},
    ],
)
def test_validate_specialized_event_data_invalid(
    corrupted_data: dict[str, object],
) -> None:
    """Verify corrupted, non-Orca, or malformed specialized data raise."""
    with pytest.raises(ReportMalformedPayloadError):
        validate_specialized_event_data(corrupted_data)


# === Raw Event Payload Validation (SRC-06) ===


def test_validate_raw_event_payload_valid() -> None:
    """Verify raw event payload structure parsing."""
    raw = {
        'id': 42,
        'timestamp': '2026-09-08T12:00:00.000000+00:00',
        'duration': 5.5,
        'data': {'status': 'not-afk'},
    }
    record = validate_raw_event_payload(raw)
    assert record.event_id == 42
    assert record.timestamp == datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
    assert record.duration_us == 5_500_000
    assert record.data == {'status': 'not-afk'}


@pytest.mark.parametrize(
    'invalid_payload',
    [
        [],
        'not a dict',
        {'timestamp': '2026-09-08T12:00:00Z', 'duration': 1, 'data': {}},
        {'id': 1, 'duration': 1, 'data': {}},
        {'id': 1, 'timestamp': '2026-09-08T12:00:00Z', 'data': {}},
        {'id': 1, 'timestamp': '2026-09-08T12:00:00Z', 'duration': 1},
        {
            'id': 1,
            'timestamp': '2026-09-08T12:00:00Z',
            'duration': 1,
            'data': 'not a dict',
        },
    ],
)
def test_validate_raw_event_payload_invalid(invalid_payload: object) -> None:
    """Verify missing fields or wrong types in payload raise."""
    with pytest.raises(ReportMalformedPayloadError):
        validate_raw_event_payload(invalid_payload)


# === Interval Models and NFC Normalization (TIME-07, TIME-13) ===


def test_specialized_identity_nfc_normalization() -> None:
    """Verify NFC normalization in SpecializedIdentity."""
    decomposed = 're\u0301po'
    precomposed = 'r\u00e9po'
    assert decomposed != precomposed

    id1 = SpecializedIdentity.create(
        host_suffix='mac.local',
        bucket_id='aw-watcher-orca_mac.local',
        source_kind='production',
        repo=decomposed,
        worktree='main',
    )
    id2 = SpecializedIdentity.create(
        host_suffix='mac.local',
        bucket_id='aw-watcher-orca_mac.local',
        source_kind='production',
        repo=precomposed,
        worktree='main',
    )
    assert id1.repo == precomposed
    assert id1 == id2


def test_time_interval_valid_and_span() -> None:
    """Verify TimeInterval creation and duration properties."""
    interval = TimeInterval(start_us=100, end_us=500)
    assert interval.start_us == 100
    assert interval.end_us == 500
    assert interval.duration_us == 400
    assert interval.is_empty is False

    zero_interval = TimeInterval(start_us=500, end_us=500)
    assert zero_interval.duration_us == 0
    assert zero_interval.is_empty is True


def test_time_interval_invalid_start_after_end() -> None:
    """Verify start_us > end_us raises ValueError."""
    with pytest.raises(ValueError):
        TimeInterval(start_us=600, end_us=500)
