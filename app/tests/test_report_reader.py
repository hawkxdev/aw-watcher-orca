"""Test report reader, 4-number check, boundary, and standard query."""

import json
import threading
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar
from urllib.parse import urlparse

import pytest

from aw_watcher_orca.report_models import (
    ABSENT_UNVERIFIED_STATUS,
    UNKNOWN_BOUNDARY_REASON,
    RawEventRecord,
    ReportApiError,
    ReportIncompleteError,
    ReportLimitError,
    ReportMalformedPayloadError,
)
from aw_watcher_orca.report_reader import (
    FullSnapshotResult,
    _http_get_json,
    compute_production_boundary,
    read_bucket_events_with_verification,
    read_full_reporting_snapshot,
    read_standard_comparison_events,
    verify_four_numbers,
)
from aw_watcher_orca.report_sources import build_source_catalog
from report_fixtures import (
    EXPECTED_C20,
    EXPECTED_C21,
    EXPECTED_CONTROL_COMPLETE,
)

# === 4-Number Verification Tests (C20, C21, control-complete) ===


def test_verify_four_numbers_matching() -> None:
    """Verify control-complete case: all 4 numbers match and within budget."""
    assert (
        verify_four_numbers(501, 501, 501, 501, 100_000)
        is EXPECTED_CONTROL_COMPLETE
    )


def test_verify_four_numbers_c20_truncated_rows() -> None:
    """Verify C20: before=501, rows=500, unique=500, after=501 fails."""
    assert verify_four_numbers(501, 500, 500, 501, 100_000) is EXPECTED_C20


def test_verify_four_numbers_c21_duplicate_id() -> None:
    """Verify C21: before=501, rows=501, unique=500, after=501 fails."""
    assert verify_four_numbers(501, 501, 500, 501, 100_000) is EXPECTED_C21


def test_verify_four_numbers_budget_exceeded() -> None:
    """Verify count exceeding budget fails."""
    assert (
        verify_four_numbers(100_001, 100_001, 100_001, 100_001, 100_000)
        is False
    )


# === Production Boundary Tests (SRC-08, TLR-03) ===


def test_compute_production_boundary_known() -> None:
    """Verify known boundary is minimal initial production timestamp."""
    buckets = {
        'aw-watcher-orca_h1': {
            'client': 'aw-watcher-orca',
            'type': 'currentwindow',
            'hostname': 'h1',
        },
        'aw-watcher-afk_h1': {
            'client': 'aw-watcher-afk',
            'type': 'afkstatus',
            'hostname': 'h1',
        },
    }
    catalog = build_source_catalog(buckets)
    t0 = datetime(2026, 9, 7, 9, 39, 11, 472000, tzinfo=UTC)
    t1 = datetime(2026, 9, 7, 9, 45, 0, 0, tzinfo=UTC)

    events = [
        RawEventRecord(
            event_id=1,
            timestamp=t1,
            duration_us=3000000,
            data={'app': 'Orca', 'repo': 'r', 'worktree': 'w', 'title': 't'},
        ),
        RawEventRecord(
            event_id=2,
            timestamp=t0,
            duration_us=0,  # Zero-duration event sets boundary!
            data={'app': 'Orca', 'repo': '', 'worktree': '', 'title': ''},
        ),
    ]
    events_by_bucket = {'aw-watcher-orca_h1': tuple(events)}
    observed_until = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)

    boundary, status = compute_production_boundary(
        catalog, events_by_bucket, observed_until
    )
    assert boundary == t0
    assert status == 'known'


def test_compute_production_boundary_unpaired_production() -> None:
    """Verify boundary includes production bucket even if unpaired."""
    buckets = {
        'aw-watcher-orca_h1': {
            'client': 'aw-watcher-orca',
            'type': 'currentwindow',
            'hostname': 'h1',
        },
    }
    catalog = build_source_catalog(buckets)
    assert catalog.has_incompleteness is True

    t0 = datetime(2026, 9, 7, 10, 0, 0, tzinfo=UTC)
    events = [
        RawEventRecord(
            event_id=1,
            timestamp=t0,
            duration_us=60_000_000,
            data={'app': 'Orca', 'repo': 'r', 'worktree': 'w', 'title': 't'},
        ),
    ]
    events_by_bucket = {'aw-watcher-orca_h1': tuple(events)}
    observed_until = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)

    boundary, status = compute_production_boundary(
        catalog, events_by_bucket, observed_until
    )
    assert boundary == t0
    assert status == 'known'


def test_compute_production_boundary_unknown_when_production_empty() -> None:
    """Verify empty production history gives unknown boundary (SRC-08)."""
    buckets = {
        'aw-watcher-orca_h1': {
            'client': 'aw-watcher-orca',
            'type': 'currentwindow',
            'hostname': 'h1',
        },
        'aw-watcher-afk_h1': {
            'client': 'aw-watcher-afk',
            'type': 'afkstatus',
            'hostname': 'h1',
        },
    }
    catalog = build_source_catalog(buckets)
    events_by_bucket = {'aw-watcher-orca_h1': ()}
    observed_until = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)

    boundary, status = compute_production_boundary(
        catalog, events_by_bucket, observed_until
    )
    assert boundary is None
    assert status == UNKNOWN_BOUNDARY_REASON


def test_compute_production_boundary_absent_when_no_production() -> None:
    """Verify no production buckets gives absent_unverified status (SRC-08)."""
    buckets = {
        'aw-watcher-orca-test_h1': {
            'client': 'aw-watcher-orca-test',
            'type': 'currentwindow',
            'hostname': 'h1',
        },
        'aw-watcher-afk_h1': {
            'client': 'aw-watcher-afk',
            'type': 'afkstatus',
            'hostname': 'h1',
        },
    }
    catalog = build_source_catalog(buckets)
    events_by_bucket = {'aw-watcher-orca-test_h1': ()}
    observed_until = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)

    boundary, status = compute_production_boundary(
        catalog, events_by_bucket, observed_until
    )
    assert boundary is None
    assert status == ABSENT_UNVERIFIED_STATUS


# === Synthetic HTTP Server Fixture for Network Tests ===


class SyntheticAwServer(BaseHTTPRequestHandler):
    """Synthetic server mimicking ActivityWatch endpoints."""

    buckets_data: ClassVar[Mapping[str, object]] = {}
    events_data: ClassVar[dict[str, list[dict[str, object]]]] = {}
    query_response_data: ClassVar[list[dict[str, object]]] = []
    fail_first_count: ClassVar[int] = 0
    fail_on_retry_status: ClassVar[int] = 0
    mutate_post_hostname: ClassVar[bool] = False
    mutate_post_production_event: ClassVar[bool] = False
    request_log: ClassVar[list[str]] = []
    prod_events_read_count: ClassVar[int] = 0

    def log_message(self, format: str, *args: object) -> None:
        """Suppress stdout logging."""

    def do_GET(self) -> None:  # noqa: N802
        """Handle GET requests for buckets, events, count."""
        parsed = urlparse(self.path)
        path = parsed.path
        SyntheticAwServer.request_log.append(self.path)

        if (
            SyntheticAwServer.fail_on_retry_status > 0
            and len(SyntheticAwServer.request_log) > 4
        ):
            self.send_response(SyntheticAwServer.fail_on_retry_status)
            self.end_headers()
            return

        if path == '/api/0/buckets/':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            b_data = dict(SyntheticAwServer.buckets_data)
            if (
                SyntheticAwServer.mutate_post_hostname
                and len(SyntheticAwServer.request_log) > 2
            ):
                b_data = {
                    k: {**v, 'hostname': 'mutated-host'}  # type: ignore[dict-item]
                    for k, v in b_data.items()
                }
            self.wfile.write(json.dumps(b_data).encode('utf-8'))
            return

        if '/events/count' in path:
            bucket_id = path.split('/api/0/buckets/')[1].split(
                '/events/count'
            )[0]
            events = SyntheticAwServer.events_data.get(bucket_id, [])
            count = len(events)
            if SyntheticAwServer.fail_first_count > 0:
                SyntheticAwServer.fail_first_count -= 1
                count += 99

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(count).encode('utf-8'))
            return

        if '/events' in path:
            bucket_id = path.split('/api/0/buckets/')[1].split('/events')[0]
            events = [
                dict(ev)
                for ev in SyntheticAwServer.events_data.get(bucket_id, [])
            ]
            if 'orca' in bucket_id and 'test' not in bucket_id:
                SyntheticAwServer.prod_events_read_count += 1
                if (
                    SyntheticAwServer.mutate_post_production_event
                    and SyntheticAwServer.prod_events_read_count % 2 == 0
                    and events
                ):
                    events[0]['timestamp'] = '2026-09-01T00:00:00+00:00'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(events).encode('utf-8'))
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        """Handle POST /api/0/query/."""
        content_len = int(self.headers.get('Content-Length', 0))
        if content_len > 0:
            self.rfile.read(content_len)

        parsed = urlparse(self.path)
        if parsed.path == '/api/0/query/':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(
                json.dumps([SyntheticAwServer.query_response_data]).encode(
                    'utf-8'
                )
            )
            return
        self.send_response(404)
        self.end_headers()


@pytest.fixture
def synthetic_aw_server() -> Iterator[str]:
    """Start a synthetic ActivityWatch test server on a free port."""
    SyntheticAwServer.request_log = []
    SyntheticAwServer.fail_first_count = 0
    SyntheticAwServer.fail_on_retry_status = 0
    SyntheticAwServer.mutate_post_hostname = False
    SyntheticAwServer.mutate_post_production_event = False
    SyntheticAwServer.prod_events_read_count = 0
    SyntheticAwServer.events_data = {}
    SyntheticAwServer.buckets_data = {}
    SyntheticAwServer.query_response_data = []

    server = HTTPServer(('127.0.0.1', 0), SyntheticAwServer)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base_url = f'http://127.0.0.1:{port}'
    try:
        yield base_url
    finally:
        SyntheticAwServer.request_log = []
        SyntheticAwServer.fail_first_count = 0
        SyntheticAwServer.fail_on_retry_status = 0
        SyntheticAwServer.mutate_post_hostname = False
        SyntheticAwServer.mutate_post_production_event = False
        SyntheticAwServer.prod_events_read_count = 0
        SyntheticAwServer.events_data = {}
        SyntheticAwServer.buckets_data = {}
        SyntheticAwServer.query_response_data = []
        server.shutdown()
        server.server_close()


# === Network Reading Tests ===


def test_read_bucket_events_happy_path(synthetic_aw_server: str) -> None:
    """Verify single bucket event reading with 4-number verification."""
    raw_events = [
        {
            'id': 1,
            'timestamp': '2026-09-08T10:00:00+00:00',
            'duration': 60.0,
            'data': {'status': 'not-afk'},
        },
        {
            'id': 2,
            'timestamp': '2026-09-08T10:01:00+00:00',
            'duration': 60.0,
            'data': {'status': 'afk'},
        },
    ]
    SyntheticAwServer.events_data['aw-watcher-afk_h1'] = raw_events
    observed_until = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)

    events, body_bytes = read_bucket_events_with_verification(
        bucket_id='aw-watcher-afk_h1',
        base_url=synthetic_aw_server,
        observed_until=observed_until,
        is_afk=True,
    )
    assert len(events) == 2
    assert events[0].event_id == 1
    assert events[1].event_id == 2
    assert body_bytes > 0


def test_read_bucket_events_duplicate_id_raises(
    synthetic_aw_server: str,
) -> None:
    """Verify duplicate event ID raises ReportMalformedPayloadError."""
    raw_events = [
        {
            'id': 1,
            'timestamp': '2026-09-08T10:00:00+00:00',
            'duration': 1.0,
            'data': {'status': 'not-afk'},
        },
        {
            'id': 1,  # duplicate ID
            'timestamp': '2026-09-08T10:01:00+00:00',
            'duration': 1.0,
            'data': {'status': 'not-afk'},
        },
    ]
    SyntheticAwServer.events_data['aw-watcher-afk_h1'] = raw_events
    observed_until = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)

    with pytest.raises(ReportMalformedPayloadError):
        read_bucket_events_with_verification(
            bucket_id='aw-watcher-afk_h1',
            base_url=synthetic_aw_server,
            observed_until=observed_until,
            is_afk=True,
        )


def test_read_bucket_events_invalid_id_type_raises(
    synthetic_aw_server: str,
) -> None:
    """Verify non-int or bool event ID raises ReportMalformedPayloadError."""
    raw_events = [
        {
            'id': True,
            'timestamp': '2026-09-08T10:00:00+00:00',
            'duration': 1.0,
            'data': {'status': 'not-afk'},
        },
    ]
    SyntheticAwServer.events_data['aw-watcher-afk_h1'] = raw_events
    observed_until = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)

    with pytest.raises(ReportMalformedPayloadError):
        read_bucket_events_with_verification(
            bucket_id='aw-watcher-afk_h1',
            base_url=synthetic_aw_server,
            observed_until=observed_until,
            is_afk=True,
        )


def test_read_full_reporting_snapshot_with_retry(
    synthetic_aw_server: str,
) -> None:
    """Verify reader succeeds with single retry after count mismatch."""
    buckets_data: dict[str, object] = {
        'aw-watcher-orca_h1': {
            'client': 'aw-watcher-orca',
            'type': 'currentwindow',
            'hostname': 'h1',
        },
        'aw-watcher-afk_h1': {
            'client': 'aw-watcher-afk',
            'type': 'afkstatus',
            'hostname': 'h1',
        },
    }
    SyntheticAwServer.buckets_data = buckets_data
    SyntheticAwServer.events_data = {
        'aw-watcher-orca_h1': [
            {
                'id': 1,
                'timestamp': '2026-09-08T10:00:00+00:00',
                'duration': 5.0,
                'data': {
                    'app': 'Orca',
                    'repo': 'r',
                    'worktree': 'w',
                    'title': 't',
                },
            }
        ],
        'aw-watcher-afk_h1': [
            {
                'id': 1,
                'timestamp': '2026-09-08T10:00:00+00:00',
                'duration': 5.0,
                'data': {'status': 'not-afk'},
            }
        ],
    }
    catalog = build_source_catalog(buckets_data)
    observed_until = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)

    SyntheticAwServer.fail_first_count = 1

    snapshot = read_full_reporting_snapshot(
        catalog=catalog,
        base_url=synthetic_aw_server,
        observed_until=observed_until,
    )
    assert isinstance(snapshot, FullSnapshotResult)
    assert len(snapshot.events_by_bucket['aw-watcher-orca_h1']) == 1
    assert snapshot.boundary_status == 'known'
    assert snapshot.production_boundary == datetime(
        2026, 9, 8, 10, 0, 0, tzinfo=UTC
    )


def test_pre_post_metadata_hostname_change_detected(
    synthetic_aw_server: str,
) -> None:
    """Verify changing metadata between pre/post fails snapshot (Finding 1)."""
    buckets_data: dict[str, object] = {
        'aw-watcher-orca_h1': {
            'client': 'aw-watcher-orca',
            'type': 'currentwindow',
            'hostname': 'h1',
        },
        'aw-watcher-afk_h1': {
            'client': 'aw-watcher-afk',
            'type': 'afkstatus',
            'hostname': 'h1',
        },
    }
    SyntheticAwServer.buckets_data = buckets_data
    SyntheticAwServer.events_data = {
        'aw-watcher-orca_h1': [],
        'aw-watcher-afk_h1': [],
    }
    catalog = build_source_catalog(buckets_data)
    observed_until = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)

    SyntheticAwServer.mutate_post_hostname = True

    with pytest.raises(ReportIncompleteError):
        read_full_reporting_snapshot(
            catalog=catalog,
            base_url=synthetic_aw_server,
            observed_until=observed_until,
        )


def test_pre_post_production_boundary_change_detected(
    synthetic_aw_server: str,
) -> None:
    """Verify changing production min-ts on second probe fails (Finding 1)."""
    buckets_data: dict[str, object] = {
        'aw-watcher-orca_h1': {
            'client': 'aw-watcher-orca',
            'type': 'currentwindow',
            'hostname': 'h1',
        },
        'aw-watcher-afk_h1': {
            'client': 'aw-watcher-afk',
            'type': 'afkstatus',
            'hostname': 'h1',
        },
    }
    SyntheticAwServer.buckets_data = buckets_data
    SyntheticAwServer.events_data = {
        'aw-watcher-orca_h1': [
            {
                'id': 1,
                'timestamp': '2026-09-08T10:00:00+00:00',
                'duration': 5.0,
                'data': {
                    'app': 'Orca',
                    'repo': 'r',
                    'worktree': 'w',
                    'title': 't',
                },
            }
        ],
        'aw-watcher-afk_h1': [],
    }
    catalog = build_source_catalog(buckets_data)
    observed_until = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)

    SyntheticAwServer.mutate_post_production_event = True

    with pytest.raises(ReportIncompleteError):
        read_full_reporting_snapshot(
            catalog=catalog,
            base_url=synthetic_aw_server,
            observed_until=observed_until,
        )


def test_retry_preserves_api_error_class(
    synthetic_aw_server: str,
) -> None:
    """Verify HTTP error on retry is raised as ReportApiError (Finding 8)."""
    buckets_data: dict[str, object] = {
        'aw-watcher-orca_h1': {
            'client': 'aw-watcher-orca',
            'type': 'currentwindow',
            'hostname': 'h1',
        },
        'aw-watcher-afk_h1': {
            'client': 'aw-watcher-afk',
            'type': 'afkstatus',
            'hostname': 'h1',
        },
    }
    SyntheticAwServer.buckets_data = buckets_data
    SyntheticAwServer.events_data = {
        'aw-watcher-orca_h1': [],
        'aw-watcher-afk_h1': [],
    }
    catalog = build_source_catalog(buckets_data)
    observed_until = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)

    # Trigger count mismatch to force retry, and fail retry with HTTP 503
    SyntheticAwServer.fail_first_count = 1
    SyntheticAwServer.fail_on_retry_status = 503

    with pytest.raises(ReportApiError):
        read_full_reporting_snapshot(
            catalog=catalog,
            base_url=synthetic_aw_server,
            observed_until=observed_until,
        )


def test_response_bytes_limit_guard(
    synthetic_aw_server: str,
) -> None:
    """Verify chunked reading raises ReportLimitError on exceeding limit."""
    SyntheticAwServer.buckets_data = {'b': {}}
    url = f'{synthetic_aw_server}/api/0/buckets/'
    with pytest.raises(ReportLimitError):
        _http_get_json(url, timeout=5.0, max_bytes=2)


def test_read_standard_comparison_events_happy_path(
    synthetic_aw_server: str,
) -> None:
    """Verify standard window query returns filtered events."""
    raw_query_events = [
        {
            'id': 10,
            'timestamp': '2026-09-08T10:00:00+00:00',
            'duration': 120.0,
            'data': {'app': 'Orca', 'title': 'workspace'},
        }
    ]
    SyntheticAwServer.events_data['aw-watcher-window_h1'] = raw_query_events
    SyntheticAwServer.query_response_data = raw_query_events
    observed_until = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)

    events = read_standard_comparison_events(
        bucket_id='aw-watcher-window_h1',
        base_url=synthetic_aw_server,
        observed_until=observed_until,
    )
    assert len(events) == 1
    assert events[0].event_id == 10
    assert events[0].duration_us == 120_000_000


def test_read_standard_comparison_pre_1970_raises(
    synthetic_aw_server: str,
) -> None:
    """Verify pre-1970 standard events raise ReportIncompleteError."""
    raw_query_events = [
        {
            'id': 10,
            'timestamp': '1969-12-31T23:59:59+00:00',
            'duration': 1.0,
            'data': {'app': 'Orca', 'title': 'workspace'},
        }
    ]
    SyntheticAwServer.events_data['aw-watcher-window_h1'] = raw_query_events
    SyntheticAwServer.query_response_data = raw_query_events
    observed_until = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)

    with pytest.raises(ReportIncompleteError):
        read_standard_comparison_events(
            bucket_id='aw-watcher-window_h1',
            base_url=synthetic_aw_server,
            observed_until=observed_until,
        )


def test_read_bucket_events_long_afk_c19_without_start(
    synthetic_aw_server: str,
) -> None:
    """C19 pipeline: long AFK starting 48h before observed_until read intact.

    Verifies that bucket event listing does not pass a start parameter,
    preserving long AFK intervals covering the full period.
    """
    # Event starting 48 hours earlier with duration 48h (172800s)
    raw_events = [
        {
            'id': 1,
            'timestamp': '2026-09-06T12:00:00+00:00',
            'duration': 172800.0,
            'data': {'status': 'not-afk'},
        },
    ]
    SyntheticAwServer.events_data['aw-watcher-afk_h1'] = raw_events
    observed_until = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)

    events, _ = read_bucket_events_with_verification(
        bucket_id='aw-watcher-afk_h1',
        base_url=synthetic_aw_server,
        observed_until=observed_until,
        is_afk=True,
    )
    assert len(events) == 1
    assert events[0].event_id == 1
    assert events[0].duration_us == 172_800_000_000
    assert events[0].timestamp == datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)

    # Assert no request contained start parameter
    for req_path in SyntheticAwServer.request_log:
        if '/api/0/buckets/aw-watcher-afk_h1/events' in req_path:
            assert 'start=' not in req_path
            assert 'end=' in req_path
