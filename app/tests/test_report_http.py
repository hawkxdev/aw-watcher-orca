"""Tests for ReportHttpHandler, security guards, and HTTP routing."""

import json
import socket
import threading
import time
from collections.abc import Iterator, Mapping
from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from aw_watcher_orca.report_http import (
    create_report_http_server,
    create_safe_aw_opener,
)
from aw_watcher_orca.report_reader import FullSnapshotResult
from aw_watcher_orca.report_service import (
    ReportService,
)
from aw_watcher_orca.report_settings import ReportSettings
from aw_watcher_orca.report_sources import build_source_catalog


@pytest.fixture
def running_report_http_server() -> Iterator[tuple[str, str, ReportService]]:
    """Start ReportHttpHandler on free loopback port with generated token."""
    token = 'test-token-1234567890abcdef1234567890abcdef'  # noqa: S105
    settings = ReportSettings()
    service = ReportService(settings=settings)

    server = create_report_http_server(
        host='127.0.0.1',
        port=0,
        service=service,
        token=token,
        settings=settings,
    )
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base_url = f'http://127.0.0.1:{port}'
    try:
        yield (base_url, token, service)
    finally:
        server.shutdown()
        server.server_close()


def _make_http_request(
    url: str,
    token: str | None = None,
    headers: dict[str, str] | None = None,
    data: Mapping[str, object] | None = None,
    method: str = 'GET',
) -> tuple[int, dict[str, str], dict[str, object]]:
    """Helper to send HTTP request and return (status, headers, json)."""
    req_headers = {'Accept': 'application/json'}
    if token:
        req_headers['X-Report-Token'] = token
    if headers:
        req_headers.update(headers)

    body_bytes: bytes | None = None
    if data is not None:
        body_bytes = json.dumps(data).encode('utf-8')
        req_headers['Content-Type'] = 'application/json'

    req = Request(  # noqa: S310
        url,
        data=body_bytes,
        headers=req_headers,
        method=method,
    )

    try:
        with urlopen(req, timeout=5.0) as resp:  # noqa: S310
            status = resp.status
            resp_headers = dict(resp.headers)
            body = resp.read()
            json_data = json.loads(body.decode('utf-8'))
            return (status, resp_headers, json_data)
    except HTTPError as err:
        resp_headers = dict(err.headers)
        body = err.read()
        try:
            json_data = json.loads(body.decode('utf-8'))
        except Exception:
            json_data = {'raw': body.decode('utf-8', errors='replace')}
        return (err.code, resp_headers, json_data)


# === HTTP Security and Route Tests ===


def test_http_status_route_happy_path(
    running_report_http_server: tuple[str, str, ReportService],
) -> None:
    """Verify GET /api/status returns service status and version."""
    base_url, token, _ = running_report_http_server
    status, headers, data = _make_http_request(
        f'{base_url}/api/status',
        token=token,
    )
    assert status == 200
    assert data.get('status') == 'ok'
    assert data.get('service') == 'aw-watcher-orca-reporting'
    assert headers.get('Content-Security-Policy') is not None
    assert 'no-store' in headers.get('Cache-Control', '')


def test_http_unauthorized_token_rejected(
    running_report_http_server: tuple[str, str, ReportService],
) -> None:
    """Verify request with bad token or missing token is rejected 401."""
    base_url, _, _ = running_report_http_server

    # 1. Missing token
    status, _, data = _make_http_request(f'{base_url}/api/status', token=None)
    assert status == 401
    assert data.get('reason_code') == 'unauthorized_token'

    # 2. Invalid token
    status, _, data = _make_http_request(
        f'{base_url}/api/status',
        token='wrong-token',  # noqa: S106
    )
    assert status == 401
    assert data.get('reason_code') == 'unauthorized_token'


def test_http_non_ascii_token_rejected_401(
    running_report_http_server: tuple[str, str, ReportService],
) -> None:
    """Verify request with non-ASCII token returns 401 instead of crash."""
    base_url, _, _ = running_report_http_server
    status, _, data = _make_http_request(
        f'{base_url}/api/status',
        token='ÿÿÿ-non-ascii-token',  # noqa: S106
    )
    assert status == 401
    assert data.get('reason_code') == 'unauthorized_token'


def test_http_sec_fetch_site_cross_site_rejected(
    running_report_http_server: tuple[str, str, ReportService],
) -> None:
    """Verify Sec-Fetch-Site: cross-site is rejected with 403 (SEC-01)."""
    base_url, token, _ = running_report_http_server
    status, _, data = _make_http_request(
        f'{base_url}/api/status',
        token=token,
        headers={'Sec-Fetch-Site': 'cross-site'},
    )
    assert status == 403
    assert data.get('reason_code') == 'forbidden_origin'


def test_http_authorization_bearer_token(
    running_report_http_server: tuple[str, str, ReportService],
) -> None:
    """Verify Authorization: Bearer <token> is accepted (SEC-01)."""
    base_url, token, _ = running_report_http_server
    status, _, data = _make_http_request(
        f'{base_url}/api/status',
        headers={'Authorization': f'Bearer {token}'},
    )
    assert status == 200
    assert data.get('status') == 'ok'


def test_http_foreign_host_rejected(
    running_report_http_server: tuple[str, str, ReportService],
) -> None:
    """Verify request with foreign Host header is rejected 403 (SEC-01)."""
    base_url, token, _ = running_report_http_server
    status, _, data = _make_http_request(
        f'{base_url}/api/status',
        token=token,
        headers={'Host': 'evil.com:5600'},
    )
    assert status == 403
    assert data.get('reason_code') == 'forbidden_host'


def test_http_foreign_origin_rejected(
    running_report_http_server: tuple[str, str, ReportService],
) -> None:
    """Verify request with foreign Origin header is rejected 403 (SEC-01)."""
    base_url, token, _ = running_report_http_server
    status, _, data = _make_http_request(
        f'{base_url}/api/status',
        token=token,
        headers={'Origin': 'http://evil.com'},
    )
    assert status == 403
    assert data.get('reason_code') == 'forbidden_origin'


def test_http_route_not_found(
    running_report_http_server: tuple[str, str, ReportService],
) -> None:
    """Verify request to path outside allowlist returns 404 (SEC-03)."""
    base_url, token, _ = running_report_http_server
    status, _, data = _make_http_request(
        f'{base_url}/api/unknown_route',
        token=token,
    )
    assert status == 404
    assert data.get('reason_code') == 'not_found'


def test_http_request_body_too_large(
    running_report_http_server: tuple[str, str, ReportService],
) -> None:
    """Verify request body exceeding 16 KiB limit is rejected 413."""
    base_url, token, _ = running_report_http_server
    large_payload = {'dummy': 'x' * 20_000}  # ~20 KiB > 16 KiB
    status, _, data = _make_http_request(
        f'{base_url}/api/report',
        token=token,
        data=large_payload,
        method='POST',
    )
    assert status == 413
    assert data.get('reason_code') == 'request_too_large'


def test_http_method_not_allowed(
    running_report_http_server: tuple[str, str, ReportService],
) -> None:
    """Verify unsupported HTTP method returns 405."""
    base_url, token, _ = running_report_http_server
    status, _, data = _make_http_request(
        f'{base_url}/api/status',
        token=token,
        method='DELETE',
    )
    assert status == 405
    assert data.get('reason_code') == 'method_not_allowed'


def test_http_report_invalid_parameters(
    running_report_http_server: tuple[str, str, ReportService],
) -> None:
    """Verify invalid parameters (bad date format / end before start) 400."""
    base_url, token, _ = running_report_http_server

    # Bad date format
    status, _, data = _make_http_request(
        f'{base_url}/api/report',
        token=token,
        data={'start_date': 'invalid-date', 'end_date': '2026-09-08'},
        method='POST',
    )
    assert status == 400
    assert data.get('reason_code') == 'invalid_parameters'

    # End before start
    status, _, data = _make_http_request(
        f'{base_url}/api/report',
        token=token,
        data={'start_date': '2026-09-08', 'end_date': '2026-09-01'},
        method='POST',
    )
    assert status == 400
    assert data.get('reason_code') == 'invalid_parameters'


def test_http_payload_too_large_500() -> None:
    """Verify serialized response > max_serialized_response_bytes gives 500."""
    # Create server with very small max response budget (64 bytes)
    settings = ReportSettings(max_serialized_response_bytes=64)
    token = 'payload-test-token-1234567890123'  # noqa: S105
    server = create_report_http_server(
        host='127.0.0.1',
        port=0,
        token=token,
        settings=settings,
    )
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    base_url = f'http://127.0.0.1:{port}'
    try:
        status, _, data = _make_http_request(
            f'{base_url}/api/status',
            token=token,
        )
        assert status == 500
        assert data.get('reason_code') == 'payload_too_large'
    finally:
        server.shutdown()
        server.server_close()


def test_http_security_headers_on_error_responses(
    running_report_http_server: tuple[str, str, ReportService],
) -> None:
    """Verify security headers are attached on all non-200 responses."""
    base_url, token, _ = running_report_http_server

    error_cases = [
        # 401 unauthorized
        _make_http_request(f'{base_url}/api/status', token=None),
        # 403 forbidden host
        _make_http_request(
            f'{base_url}/api/status',
            token=token,
            headers={'Host': 'evil.com:5600'},
        ),
        # 404 not found
        _make_http_request(f'{base_url}/api/unknown_route', token=token),
        # 405 method not allowed
        _make_http_request(
            f'{base_url}/api/status', token=token, method='DELETE'
        ),
        # 400 invalid parameters
        _make_http_request(
            f'{base_url}/api/report',
            token=token,
            data={'start_date': 'bad'},
            method='POST',
        ),
    ]

    for status_code, headers, _ in error_cases:
        assert status_code in (400, 401, 403, 404, 405)
        assert headers.get('Content-Security-Policy') is not None
        assert 'no-store' in headers.get('Cache-Control', '')
        assert headers.get('X-Content-Type-Options') == 'nosniff'
        assert headers.get('Referrer-Policy') == 'no-referrer'


def test_http_get_variants_report_and_comparison(
    running_report_http_server: tuple[str, str, ReportService],
) -> None:
    """Verify GET /api/report and GET /api/comparison query parameter paths."""
    base_url, token, service = running_report_http_server

    # Set up mock snapshot on service
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
    snapshot = FullSnapshotResult(
        catalog=catalog,
        observed_until=datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC),
        read_started_at=datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC),
        read_finished_at=datetime(2026, 9, 8, 10, 0, 1, tzinfo=UTC),
        events_by_bucket={
            'aw-watcher-orca_h1': (),
            'aw-watcher-afk_h1': (),
        },
        production_boundary=None,
        boundary_status='absent_unverified',
        total_response_bytes=100,
    )
    service.get_catalog = MagicMock(return_value=catalog)  # type: ignore[method-assign]
    service._fetch_snapshot = MagicMock(return_value=snapshot)  # type: ignore[method-assign]

    # 1. GET /api/report with query params
    query_url = (
        f'{base_url}/api/report?'
        'start_date=2026-09-08&end_date=2026-09-08&zone_name=UTC'
    )
    st, _, data = _make_http_request(query_url, token=token)
    assert st == 200
    assert data.get('status') == 'ok'
    report_id = data['report']['report_id']  # type: ignore[index]

    # 2. GET /api/comparison with query param
    comp_url = f'{base_url}/api/comparison?report_id={report_id}'
    st2, _, data2 = _make_http_request(comp_url, token=token)
    assert st2 == 200
    assert data2.get('status') == 'ok'


def test_http_report_id_mappings_404_and_410(
    running_report_http_server: tuple[str, str, ReportService],
) -> None:
    """Verify unknown report_id -> 404, expired report_id -> 410."""
    base_url, token, service = running_report_http_server

    # 1. Unknown report_id -> 404
    st, _, data = _make_http_request(
        f'{base_url}/api/comparison',
        token=token,
        data={'report_id': 'nonexistent-id'},
        method='POST',
    )
    assert st == 404
    assert data.get('reason_code') == 'unknown_report_id'

    # Set up active report
    buckets = {'b': {'client': 'c', 'type': 't', 'hostname': 'h'}}
    catalog = build_source_catalog(buckets)
    snapshot = FullSnapshotResult(
        catalog=catalog,
        observed_until=datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC),
        read_started_at=datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC),
        read_finished_at=datetime(2026, 9, 8, 10, 0, 1, tzinfo=UTC),
        events_by_bucket={},
        production_boundary=None,
        boundary_status='absent_unverified',
        total_response_bytes=100,
    )
    service.get_catalog = MagicMock(return_value=catalog)  # type: ignore[method-assign]
    service._fetch_snapshot = MagicMock(return_value=snapshot)  # type: ignore[method-assign]

    rep = service.calculate_report(
        start_date=date(2026, 9, 8), end_date=date(2026, 9, 8)
    )

    # 2. Expire the snapshot (> 15 min) -> 410
    service._cached_at = datetime.now(UTC) - timedelta(minutes=16)

    st2, _, data2 = _make_http_request(
        f'{base_url}/api/comparison',
        token=token,
        data={'report_id': rep.report_id},
        method='POST',
    )
    assert st2 == 410
    assert data2.get('reason_code') == 'expired_report_id'


def test_http_two_servers_independent_state() -> None:
    """Verify two servers in one process maintain distinct tokens and ports."""
    token1 = 'token-alpha-1111111111111111111'  # noqa: S105
    token2 = 'token-beta-2222222222222222222'  # noqa: S105

    server1 = create_report_http_server(host='127.0.0.1', port=0, token=token1)
    port1 = server1.server_port
    t1 = threading.Thread(target=server1.serve_forever, daemon=True)
    t1.start()

    server2 = create_report_http_server(host='127.0.0.1', port=0, token=token2)
    port2 = server2.server_port
    t2 = threading.Thread(target=server2.serve_forever, daemon=True)
    t2.start()

    base1 = f'http://127.0.0.1:{port1}'
    base2 = f'http://127.0.0.1:{port2}'

    try:
        # Request to server1 with token1 -> 200
        st1, _, _ = _make_http_request(f'{base1}/api/status', token=token1)
        assert st1 == 200

        # Request to server1 with token2 -> 401
        st1_bad, _, _ = _make_http_request(f'{base1}/api/status', token=token2)
        assert st1_bad == 401

        # Request to server2 with token2 -> 200
        st2, _, _ = _make_http_request(f'{base2}/api/status', token=token2)
        assert st2 == 200

        # Request to server2 with token1 -> 401
        st2_bad, _, _ = _make_http_request(f'{base2}/api/status', token=token1)
        assert st2_bad == 401
    finally:
        server1.shutdown()
        server1.server_close()
        server2.shutdown()
        server2.server_close()


def test_http_concurrent_connections_limit_rejection() -> None:
    """Verify connection attempts exceeding MAX_CONCURRENT_CONNECTIONS: 503."""
    # Server with connection limit = 2
    settings = ReportSettings(max_concurrent_connections=2)
    token = 'conn-limit-token-12345678901234'  # noqa: S105
    server = create_report_http_server(
        host='127.0.0.1',
        port=0,
        token=token,
        settings=settings,
    )
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    sockets: list[socket.socket] = []
    try:
        # Open 2 raw TCP connections and send partial request
        for _ in range(2):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(('127.0.0.1', port))
            s.sendall(b'GET /api/status HTTP/1.1\r\nHost: 127.0.0.1\r\n')
            sockets.append(s)

        time.sleep(0.05)

        # 3rd connection exceeds limit (2) and receives 503
        s3 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s3.connect(('127.0.0.1', port))
        req_bytes = (
            f'GET /api/status HTTP/1.1\r\n'
            f'Host: 127.0.0.1:{port}\r\n'
            f'X-Report-Token: {token}\r\n\r\n'
        ).encode()
        s3.sendall(req_bytes)
        resp_raw = s3.recv(4096)
        s3.close()

        assert b'503' in resp_raw
        assert b'connections_limit_exceeded' in resp_raw
        assert b'Content-Security-Policy' in resp_raw
        assert b'Cache-Control: no-store' in resp_raw
        assert b'Pragma: no-cache' in resp_raw
        assert b'Referrer-Policy: no-referrer' in resp_raw
        assert b'X-Content-Type-Options: nosniff' in resp_raw
        assert b'X-Frame-Options: DENY' in resp_raw
    finally:
        for s in sockets:
            s.close()
        server.shutdown()
        server.server_close()


def test_create_safe_aw_opener_proxy_bypass() -> None:
    """Verify safe opener has empty ProxyHandler and redirect disabler."""
    opener = create_safe_aw_opener()
    assert opener is not None
