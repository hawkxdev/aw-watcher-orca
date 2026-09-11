"""Tests for static asset serving and transport security guards (Stage R4)."""

import json
import secrets
import threading
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from aw_watcher_orca.report_http import (
    create_report_http_server,
)
from aw_watcher_orca.report_service import ReportService
from aw_watcher_orca.report_settings import ReportSettings


@pytest.fixture
def running_asset_server() -> Iterator[tuple[str, str, int]]:
    """Start ReportHttpServer on loopback interface with generated token."""
    token = secrets.token_hex(16)
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
        yield (base_url, token, port)
    finally:
        server.shutdown()
        server.server_close()


def _make_raw_request(
    url: str,
    headers: dict[str, str] | None = None,
    method: str = 'GET',
) -> tuple[int, dict[str, str], bytes]:
    """Send raw HTTP request and return (status_code, headers, body_bytes)."""
    req_headers = headers or {}
    req = Request(  # noqa: S310
        url,
        headers=req_headers,
        method=method,
    )
    try:
        with urlopen(req, timeout=5.0) as resp:  # noqa: S310
            return (resp.status, dict(resp.headers), resp.read())
    except HTTPError as err:
        return (err.code, dict(err.headers), err.read())


def test_static_assets_served_without_token_200(
    running_asset_server: tuple[str, str, int],
) -> None:
    """Verify all three assets are served with correct MIME without token."""
    base_url, _, _ = running_asset_server

    expected_assets = {
        '/': 'text/html; charset=utf-8',
        '/report.css': 'text/css; charset=utf-8',
        '/report.js': 'text/javascript; charset=utf-8',
    }

    for path, expected_mime in expected_assets.items():
        status, headers, body = _make_raw_request(f'{base_url}{path}')
        assert status == 200
        assert headers.get('Content-Type') == expected_mime
        assert 'no-store' in headers.get('Cache-Control', '')
        assert headers.get('Pragma') == 'no-cache'
        assert headers.get('X-Content-Type-Options') == 'nosniff'
        assert headers.get('X-Frame-Options') == 'DENY'
        assert headers.get('Referrer-Policy') == 'no-referrer'
        csp = headers.get('Content-Security-Policy', '')
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert len(body) > 0
        assert headers.get('Content-Length') == str(len(body))


def test_static_assets_foreign_host_rejected_403(
    running_asset_server: tuple[str, str, int],
) -> None:
    """Verify static assets reject foreign Host header with 403."""
    base_url, _, _ = running_asset_server

    for path in ('/', '/report.css', '/report.js'):
        status, headers, body = _make_raw_request(
            f'{base_url}{path}',
            headers={'Host': 'evil.attacker.com'},
        )
        assert status == 403
        data = json.loads(body.decode('utf-8'))
        assert data.get('status') == 'error'
        assert data.get('reason_code') == 'forbidden_host'


def test_static_assets_foreign_origin_rejected_403(
    running_asset_server: tuple[str, str, int],
) -> None:
    """Verify static assets reject foreign Origin header with 403."""
    base_url, _, _ = running_asset_server

    for path in ('/', '/report.css', '/report.js'):
        status, headers, body = _make_raw_request(
            f'{base_url}{path}',
            headers={'Origin': 'http://evil.attacker.com'},
        )
        assert status == 403
        data = json.loads(body.decode('utf-8'))
        assert data.get('status') == 'error'
        assert data.get('reason_code') == 'forbidden_origin'


def test_static_assets_cross_site_rejected_403(
    running_asset_server: tuple[str, str, int],
) -> None:
    """Verify static assets reject Sec-Fetch-Site: cross-site with 403."""
    base_url, _, _ = running_asset_server

    for path in ('/', '/report.css', '/report.js'):
        status, headers, body = _make_raw_request(
            f'{base_url}{path}',
            headers={'Sec-Fetch-Site': 'cross-site'},
        )
        assert status == 403
        data = json.loads(body.decode('utf-8'))
        assert data.get('status') == 'error'
        assert data.get('reason_code') == 'forbidden_origin'


def test_api_routes_still_require_token_401(
    running_asset_server: tuple[str, str, int],
) -> None:
    """Verify API routes still reject requests without token with 401."""
    base_url, _, _ = running_asset_server

    status, _, body = _make_raw_request(f'{base_url}/api/status')
    assert status == 401
    data = json.loads(body.decode('utf-8'))
    assert data.get('reason_code') == 'unauthorized_token'

    status, _, body = _make_raw_request(f'{base_url}/api/catalog')
    assert status == 401
    data = json.loads(body.decode('utf-8'))
    assert data.get('reason_code') == 'unauthorized_token'


def test_non_matching_and_traversal_paths_return_404(
    running_asset_server: tuple[str, str, int],
) -> None:
    """Verify non-matching paths and path traversal attempts return 404."""
    base_url, _, _ = running_asset_server

    non_matching_paths = (
        '/report.css/',
        '/index.html',
        '/report_assets/index.html',
        '/report_assets/report.css',
        '//etc/passwd',
        '/%2e%2e/report.css',
        '/unknown_asset.txt',
    )

    for path in non_matching_paths:
        status, _, body = _make_raw_request(f'{base_url}{path}')
        assert status == 404
        data = json.loads(body.decode('utf-8'))
        assert data.get('status') == 'error'
        assert data.get('reason_code') == 'not_found'


def test_missing_asset_file_on_disk_returns_500(
    running_asset_server: tuple[str, str, int],
) -> None:
    """Verify missing static asset on filesystem returns 500 internal_error."""
    base_url, _, _ = running_asset_server

    def _failing_read_bytes(self: Path) -> bytes:
        """Simulate missing file on disk."""
        raise OSError('File not found')

    with patch.object(Path, 'read_bytes', _failing_read_bytes):
        status, _, body = _make_raw_request(f'{base_url}/')
        assert status == 500
        data = json.loads(body.decode('utf-8'))
        assert data.get('status') == 'error'
        assert data.get('reason_code') == 'internal_error'


def test_html_asset_contains_semantic_structure(
    running_asset_server: tuple[str, str, int],
) -> None:
    """Verify index.html contains required UI elements and script refs."""
    base_url, _, _ = running_asset_server
    status, _, body = _make_raw_request(f'{base_url}/')
    assert status == 200
    html_text = body.decode('utf-8')
    assert '<html lang="ru">' in html_text
    assert '<link rel="stylesheet" href="/report.css">' in html_text
    assert '<script src="/report.js"></script>' in html_text
    assert 'id="zone-select"' in html_text
    assert 'id="start-date-input"' in html_text
    assert 'id="end-date-input"' in html_text
    assert 'id="project-filter"' in html_text
    assert 'id="load-btn"' in html_text
    assert 'id="compare-btn"' in html_text
    assert 'id="projects-table"' in html_text
    assert 'id="daily-chart"' in html_text
    assert 'id="daily-table"' in html_text
    assert 'id="freshness-banner"' in html_text
    assert 'id="comparison-section"' in html_text
