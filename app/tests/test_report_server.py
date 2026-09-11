"""Tests for ReportServer: process lifecycle and real HTTP matrix."""

import contextlib
import json
import secrets
import threading
import time
from collections.abc import Iterator, Mapping
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar
from unittest.mock import MagicMock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from aw_watcher_orca.report_http import (
    create_report_http_server,
    create_safe_aw_opener,
)
from aw_watcher_orca.report_server import (
    run_report_server,
)
from aw_watcher_orca.report_service import ReportService
from aw_watcher_orca.report_settings import ReportSettings

# === Synthetic ActivityWatch Server for Server Matrix Tests ===


class SyntheticAwMatrixServer(BaseHTTPRequestHandler):
    """Synthetic server supporting all reading scenarios and edge cases."""

    buckets_data: ClassVar[Mapping[str, object]] = {}
    events_data: ClassVar[dict[str, list[dict[str, object]]]] = {}
    query_response_data: ClassVar[list[dict[str, object]]] = []
    redirect_all: ClassVar[bool] = False
    slow_delay: ClassVar[float] = 0.0

    def log_message(self, format: str, *args: object) -> None:
        """Suppress standard logging."""

    def do_GET(self) -> None:  # noqa: N802
        """Handle GET requests."""
        if SyntheticAwMatrixServer.slow_delay > 0:
            time.sleep(SyntheticAwMatrixServer.slow_delay)

        if SyntheticAwMatrixServer.redirect_all:
            self.send_response(302)
            self.send_header('Location', 'http://127.0.0.1:9999/redirected')
            self.end_headers()
            return

        path = self.path
        if path.startswith('/api/0/buckets/'):
            if '/events/count' in path:
                bucket_id = path.split('/api/0/buckets/')[1].split(
                    '/events/count'
                )[0]
                evs = SyntheticAwMatrixServer.events_data.get(bucket_id, [])
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(len(evs)).encode('utf-8'))
                return

            if '/events' in path:
                bucket_id = path.split('/api/0/buckets/')[1].split('/events')[
                    0
                ]
                evs = SyntheticAwMatrixServer.events_data.get(bucket_id, [])
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(evs).encode('utf-8'))
                return

            # Buckets listing
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(
                json.dumps(dict(SyntheticAwMatrixServer.buckets_data)).encode(
                    'utf-8'
                )
            )
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        """Handle POST requests."""
        if SyntheticAwMatrixServer.slow_delay > 0:
            time.sleep(SyntheticAwMatrixServer.slow_delay)

        if SyntheticAwMatrixServer.redirect_all:
            self.send_response(302)
            self.send_header('Location', 'http://127.0.0.1:9999/redirected')
            self.end_headers()
            return

        if self.path == '/api/0/query/':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    [SyntheticAwMatrixServer.query_response_data]
                ).encode('utf-8')
            )
            return

        self.send_response(404)
        self.end_headers()


@pytest.fixture
def synthetic_aw_matrix() -> Iterator[str]:
    """Start synthetic ActivityWatch server on loopback port."""
    SyntheticAwMatrixServer.buckets_data = {
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
        'aw-watcher-window_h1': {
            'client': 'aw-watcher-window',
            'type': 'currentwindow',
            'hostname': 'h1',
        },
    }
    SyntheticAwMatrixServer.events_data = {
        'aw-watcher-orca_h1': [
            {
                'id': 1,
                'timestamp': '2026-09-08T10:00:00+00:00',
                'duration': 300.0,
                'data': {
                    'app': 'Orca',
                    'repo': 'my-repo',
                    'worktree': 'main',
                    'title': 'my-repo / main',
                },
            }
        ],
        'aw-watcher-afk_h1': [
            {
                'id': 1,
                'timestamp': '2026-09-08T09:00:00+00:00',
                'duration': 7200.0,
                'data': {'status': 'not-afk'},
            }
        ],
    }
    SyntheticAwMatrixServer.query_response_data = [
        {
            'id': 10,
            'timestamp': '2026-09-08T10:00:00+00:00',
            'duration': 300.0,
            'data': {'app': 'Orca', 'title': 'my-repo'},
        }
    ]
    SyntheticAwMatrixServer.redirect_all = False
    SyntheticAwMatrixServer.slow_delay = 0.0

    server = HTTPServer(('127.0.0.1', 0), SyntheticAwMatrixServer)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base_url = f'http://127.0.0.1:{port}'
    try:
        yield base_url
    finally:
        SyntheticAwMatrixServer.redirect_all = False
        SyntheticAwMatrixServer.slow_delay = 0.0
        server.shutdown()
        server.server_close()


def _request_api(
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
        with urlopen(req, timeout=10.0) as resp:  # noqa: S310
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


# === Process and Server Lifecycle Tests ===


def test_report_server_startup_and_clean_shutdown() -> None:
    """Verify server starts on port 0, opens browser, and stops cleanly."""
    shutdown_event = threading.Event()
    opened_urls: list[str] = []

    def stub_browser_opener(url: str) -> None:
        opened_urls.append(url)

    server_thread = threading.Thread(
        target=run_report_server,
        kwargs={
            'host': '127.0.0.1',
            'port': 0,
            'open_browser': True,
            'browser_opener': stub_browser_opener,
            'shutdown_event': shutdown_event,
        },
        daemon=True,
    )
    server_thread.start()

    # Wait for startup and browser opener invocation
    for _ in range(50):
        if opened_urls:
            break
        time.sleep(0.05)

    assert len(opened_urls) == 1
    start_url = opened_urls[0]
    assert '127.0.0.1' in start_url
    assert '#token=' in start_url

    # Signal shutdown and verify thread completes
    shutdown_event.set()
    server_thread.join(timeout=5.0)
    assert not server_thread.is_alive()


def test_report_server_port_freed_after_shutdown() -> None:
    """Verify stopping server immediately frees port for another server."""
    shutdown_event = threading.Event()
    server_holder: list[HTTPServer] = []

    def run_fn() -> None:
        server = create_report_http_server(host='127.0.0.1', port=0)
        server_holder.append(server)
        while not shutdown_event.is_set():
            server.handle_request()
        server.server_close()

    t1 = threading.Thread(target=run_fn, daemon=True)
    t1.start()
    time.sleep(0.1)

    bound_port = server_holder[0].server_port
    shutdown_event.set()
    # Trigger one dummy request to break handle_request
    with contextlib.suppress(Exception):
        urlopen(f'http://127.0.0.1:{bound_port}/api/status', timeout=0.2)  # noqa: S310
    t1.join(timeout=3.0)

    # Now bind another server to the EXACT same port
    server2 = create_report_http_server(host='127.0.0.1', port=bound_port)
    assert server2.server_port == bound_port
    server2.server_close()


def test_report_server_watcher_path_never_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify watcher loop and instance lock are NEVER called (Finding 5)."""
    watcher_mock = MagicMock()
    lock_mock = MagicMock()

    import aw_watcher_orca.instance_lock as inst_lock
    import aw_watcher_orca.watcher as watcher_mod

    monkeypatch.setattr(watcher_mod, 'run_watcher_loop', watcher_mock)
    monkeypatch.setattr(inst_lock, 'acquire_instance_lock', lock_mock)

    shutdown_event = threading.Event()
    token = secrets.token_hex(16)

    server_thread = threading.Thread(
        target=run_report_server,
        kwargs={
            'host': '127.0.0.1',
            'port': 0,
            'open_browser': False,
            'shutdown_event': shutdown_event,
            'token': token,
        },
        daemon=True,
    )
    server_thread.start()
    time.sleep(0.05)

    # Signal shutdown
    shutdown_event.set()
    server_thread.join(timeout=3.0)

    # Prove that watcher and instance lock were NEVER called
    watcher_mock.assert_not_called()
    lock_mock.assert_not_called()


def test_report_server_non_loopback_host_rejected() -> None:
    """Verify non-loopback host (e.g. 0.0.0.0) is rejected (SEC-01)."""
    with pytest.raises(ValueError) as exc_info:
        run_report_server(host='0.0.0.0')  # noqa: S104
    assert 'loopback' in str(exc_info.value)


# === Real HTTP Scenario Matrix Tests (Task 5) ===


def test_matrix_synthetic_aw_redirect_rejected(
    synthetic_aw_matrix: str,
) -> None:
    """Matrix: redirects from synthetic ActivityWatch are rejected (SEC-02)."""
    SyntheticAwMatrixServer.redirect_all = True
    token = secrets.token_hex(16)
    opener = create_safe_aw_opener()
    service = ReportService(
        base_url=synthetic_aw_matrix,
        opener=opener,
    )

    server = create_report_http_server(
        host='127.0.0.1',
        port=0,
        service=service,
        token=token,
    )
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    base_url = f'http://127.0.0.1:{port}'
    try:
        status, _, data = _request_api(
            f'{base_url}/api/catalog',
            token=token,
        )
        assert status == 503
        assert data.get('reason_code') == 'api_unavailable'
    finally:
        server.shutdown()
        server.server_close()


def test_matrix_http_proxy_env_bypassed(
    synthetic_aw_matrix: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Matrix: http_proxy in env is bypassed by safe opener (SEC-02)."""
    # Set non-routable proxy in environment
    monkeypatch.setenv('http_proxy', 'http://192.0.2.1:8080')
    monkeypatch.setenv('https_proxy', 'http://192.0.2.1:8080')
    monkeypatch.setenv('all_proxy', 'http://192.0.2.1:8080')

    token = secrets.token_hex(16)
    opener = create_safe_aw_opener()
    service = ReportService(
        base_url=synthetic_aw_matrix,
        opener=opener,
    )

    server = create_report_http_server(
        host='127.0.0.1',
        port=0,
        service=service,
        token=token,
    )
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    base_url = f'http://127.0.0.1:{port}'
    try:
        # Request succeeds because proxy was bypassed
        status, _, data = _request_api(
            f'{base_url}/api/catalog',
            token=token,
        )
        assert status == 200
        assert data.get('status') == 'ok'
    finally:
        server.shutdown()
        server.server_close()


def test_matrix_parallel_requests_busy_guard(
    synthetic_aw_matrix: str,
) -> None:
    """Matrix: parallel calculation requests trigger 429 calculation_busy."""
    token = secrets.token_hex(16)
    opener = create_safe_aw_opener()
    service = ReportService(
        base_url=synthetic_aw_matrix,
        opener=opener,
    )

    server = create_report_http_server(
        host='127.0.0.1',
        port=0,
        service=service,
        token=token,
    )
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    base_url = f'http://127.0.0.1:{port}'

    # Simulate slow delay on ActivityWatch to hold the calculation lock
    SyntheticAwMatrixServer.slow_delay = 0.05
    results: list[int] = []

    def req_task() -> None:
        st, _, _ = _request_api(
            f'{base_url}/api/report',
            token=token,
            data={'start_date': '2026-09-08', 'end_date': '2026-09-08'},
            method='POST',
        )
        results.append(st)

    t1 = threading.Thread(target=req_task)
    t2 = threading.Thread(target=req_task)

    try:
        t1.start()
        time.sleep(0.05)  # Let t1 enter calculation
        t2.start()

        t1.join(timeout=10.0)
        t2.join(timeout=10.0)

        # One should be 200 and one should be 429 calculation_busy
        assert 200 in results
        assert 429 in results
    finally:
        SyntheticAwMatrixServer.slow_delay = 0.0
        server.shutdown()
        server.server_close()


def test_matrix_comparison_failure_does_not_erase_project(
    synthetic_aw_matrix: str,
) -> None:
    """Matrix: comparison failure does NOT erase project (OUT-05, UI-07)."""
    token = secrets.token_hex(16)
    opener = create_safe_aw_opener()
    service = ReportService(
        base_url=synthetic_aw_matrix,
        opener=opener,
    )

    server = create_report_http_server(
        host='127.0.0.1',
        port=0,
        service=service,
        token=token,
    )
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    base_url = f'http://127.0.0.1:{port}'

    try:
        # 1. Successful report calculation
        st1, _, data1 = _request_api(
            f'{base_url}/api/report',
            token=token,
            data={'start_date': '2026-09-08', 'end_date': '2026-09-08'},
            method='POST',
        )
        assert st1 == 200
        report_id = data1['report']['report_id']  # type: ignore[index]

        # 2. Break ActivityWatch query endpoint
        SyntheticAwMatrixServer.redirect_all = True

        # 3. Attempt comparison -> fails
        st2, _, data2 = _request_api(
            f'{base_url}/api/comparison',
            token=token,
            data={'report_id': report_id},
            method='POST',
        )
        assert st2 == 503

        # 4. Verify original project report is still cached and accessible
        assert service.get_cached_report(report_id) is not None
    finally:
        SyntheticAwMatrixServer.redirect_all = False
        server.shutdown()
        server.server_close()


def test_matrix_slow_activitywatch_timeout(
    synthetic_aw_matrix: str,
) -> None:
    """Matrix: slow ActivityWatch responses exceeding timeout trigger 503."""
    settings = ReportSettings(default_read_timeout_seconds=0.1)
    SyntheticAwMatrixServer.slow_delay = 0.3

    token = secrets.token_hex(16)
    opener = create_safe_aw_opener()
    service = ReportService(
        base_url=synthetic_aw_matrix,
        settings=settings,
        opener=opener,
    )

    server = create_report_http_server(
        host='127.0.0.1',
        port=0,
        service=service,
        token=token,
        settings=settings,
    )
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    base_url = f'http://127.0.0.1:{port}'
    try:
        status, _, data = _request_api(
            f'{base_url}/api/catalog',
            token=token,
        )
        assert status == 503
        assert data.get('reason_code') == 'api_unavailable'
    finally:
        SyntheticAwMatrixServer.slow_delay = 0.0
        server.shutdown()
        server.server_close()
