"""HTTP dispatcher, security guards, and handlers for local report service."""

import json
import secrets
import threading
from datetime import date
from http.client import HTTPMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import IO, Any, Final
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import (
    HTTPRedirectHandler,
    OpenerDirector,
    ProxyHandler,
    Request,
    build_opener,
)
from zoneinfo import ZoneInfo

from aw_watcher_orca.report_models import (
    CALCULATION_BUSY_REASON,
    ReportApiError,
    ReportBusyError,
    ReportIncompleteError,
    ReportLimitError,
    ReportMalformedPayloadError,
)
from aw_watcher_orca.report_service import (
    ReportService,
    ReportServiceError,
    ServiceComparisonResult,
    ServiceReportResult,
)
from aw_watcher_orca.report_settings import (
    ReportSettings,
)
from aw_watcher_orca.report_sources import SourceCatalog

# === Static Asset Routes (SEC-03) ===

STATIC_ASSET_ROUTES: Final[dict[str, tuple[str, str]]] = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/report.css': ('report.css', 'text/css; charset=utf-8'),
    '/report.js': ('report.js', 'text/javascript; charset=utf-8'),
}


# === Outgoing ActivityWatch Safe Opener (SEC-02) ===


class NoRedirectHandler(HTTPRedirectHandler):
    """Prohibit following HTTP redirects for ActivityWatch requests."""

    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes] | None,
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> None:
        """Reject redirect by returning None (no redirect request)."""
        return None

    def http_error_301(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
    ) -> Any:
        """Reject 301 redirect by raising HTTPError."""
        raise HTTPError(
            req.full_url,
            code,
            'Redirects prohibited for ActivityWatch transport',
            headers,
            fp,
        )

    def http_error_302(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
    ) -> Any:
        """Reject 302 redirect by raising HTTPError."""
        raise HTTPError(
            req.full_url,
            code,
            'Redirects prohibited for ActivityWatch transport',
            headers,
            fp,
        )

    def http_error_303(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
    ) -> Any:
        """Reject 303 redirect by raising HTTPError."""
        raise HTTPError(
            req.full_url,
            code,
            'Redirects prohibited for ActivityWatch transport',
            headers,
            fp,
        )

    def http_error_307(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
    ) -> Any:
        """Reject 307 redirect by raising HTTPError."""
        raise HTTPError(
            req.full_url,
            code,
            'Redirects prohibited for ActivityWatch transport',
            headers,
            fp,
        )

    def http_error_308(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
    ) -> Any:
        """Reject 308 redirect by raising HTTPError."""
        raise HTTPError(
            req.full_url,
            code,
            'Redirects prohibited for ActivityWatch transport',
            headers,
            fp,
        )


def create_safe_aw_opener() -> OpenerDirector:
    """Create OpenerDirector bypassing proxies and prohibiting redirects."""
    return build_opener(ProxyHandler({}), NoRedirectHandler())


# === JSON Serialization Helpers (OUT-01..08) ===


def serialize_catalog(catalog: SourceCatalog) -> dict[str, object]:
    """Serialize SourceCatalog to JSON-compatible dictionary."""
    entries: list[dict[str, object]] = []
    for idx, e in enumerate(catalog.entries):
        entries.append(
            {
                'alias': f'source_{idx}',
                'bucket_id': e.bucket_id,
                'host_suffix': e.host_suffix,
                'source_kind': e.source_kind,
                'client': e.client,
                'bucket_type': e.bucket_type,
                'hostname': e.hostname,
                'afk_bucket_id': e.afk_bucket_id,
                'is_paired': e.is_paired,
                'is_corrupted': e.is_corrupted,
                'corruption_reason': e.corruption_reason,
            }
        )

    return {
        'inventory': {
            'total_scanned': catalog.inventory.total_scanned,
            'supported': catalog.inventory.supported,
            'paired': catalog.inventory.paired,
            'ignored': catalog.inventory.ignored,
            'corrupted': catalog.inventory.corrupted,
        },
        'has_incompleteness': catalog.has_incompleteness,
        'incompleteness_reasons': list(catalog.incompleteness_reasons),
        'entries': entries,
    }


def serialize_report_result(res: ServiceReportResult) -> dict[str, object]:
    """Serialize ServiceReportResult to JSON-compatible dictionary (OUT-01)."""
    source_summaries: list[dict[str, object]] = []
    for s in res.source_summaries:
        source_summaries.append(
            {
                'alias': s.alias,
                'bucket_id': s.bucket_id,
                'host_suffix': s.host_suffix,
                'source_kind': s.source_kind,
                'project_duration_us': s.project_duration_us,
                'project_seconds': s.project_seconds,
                'is_conflict': s.is_conflict,
                'conflict_reason': s.conflict_reason,
                'quality': {
                    'neutral_us': s.quality.neutral_us,
                    'confirmed_afk_us': s.quality.confirmed_afk_us,
                    'unknown_afk_us': s.quality.unknown_afk_us,
                    'noise_us': s.quality.noise_us,
                    'project_contributing_us': (
                        s.quality.project_contributing_us
                    ),
                    'is_reliable': s.quality.is_reliable,
                    'unreliable_reason': s.quality.unreliable_reason,
                },
            }
        )

    project_rows: list[dict[str, object]] = []
    for p in res.project_rows:
        d_rows: list[dict[str, object]] = []
        for d in p.daily_rows:
            d_rows.append(
                {
                    'day': d.day,
                    'duration_us': d.duration_us,
                    'seconds': d.seconds,
                    'derived_segments_count': d.derived_segments_count,
                }
            )
        project_rows.append(
            {
                'repo': p.repo,
                'worktree': p.worktree,
                'duration_us': p.duration_us,
                'seconds': p.seconds,
                'daily_rows': d_rows,
            }
        )

    daily_rows: list[dict[str, object]] = []
    for d_agg in res.daily_rows:
        daily_rows.append(
            {
                'day': d_agg.day,
                'duration_us': d_agg.duration_us,
                'seconds': d_agg.seconds,
                'contributing_events': d_agg.contributing_events,
                'derived_segments': d_agg.derived_segments,
            }
        )

    boundary_iso = (
        res.production_boundary.isoformat()
        if res.production_boundary
        else None
    )

    return {
        'report_id': res.report_id,
        'observed_until': res.observed_until.isoformat(),
        'read_started_at': res.read_started_at.isoformat(),
        'read_finished_at': res.read_finished_at.isoformat(),
        'period': {
            'start_utc': res.period.start_utc.isoformat(),
            'end_utc': res.period.end_utc.isoformat(),
            'zone_name': res.period.zone_name,
        },
        'zone_name': res.zone_name,
        'inventory': {
            'total_scanned': res.inventory.total_scanned,
            'supported': res.inventory.supported,
            'paired': res.inventory.paired,
            'ignored': res.inventory.ignored,
            'corrupted': res.inventory.corrupted,
        },
        'production_boundary': boundary_iso,
        'boundary_status': res.boundary_status,
        'combined_allowed': res.combined_allowed,
        'combined_prohibition_reason': res.combined_prohibition_reason,
        'source_summaries': source_summaries,
        'project_rows': project_rows,
        'daily_rows': daily_rows,
        'counters': {
            'raw_events': res.counters.raw_events,
            'period_events': res.counters.period_events,
            'matching_events': res.counters.matching_events,
            'contributing_events': res.counters.contributing_events,
            'derived_segments': res.counters.derived_segments,
        },
        'freshness': {
            'max_event_end': (
                res.freshness.max_event_end.isoformat()
                if res.freshness.max_event_end
                else None
            ),
            'last_updated': (
                res.freshness.last_updated.isoformat()
                if res.freshness.last_updated
                else None
            ),
            'observed_until': res.freshness.observed_until.isoformat(),
            'stale': res.freshness.stale,
        },
    }


def serialize_comparison_result(
    res: ServiceComparisonResult,
) -> dict[str, object]:
    """Serialize ServiceComparisonResult to JSON-compatible dictionary."""
    host_rows: list[dict[str, object]] = []
    for h in res.host_rows:
        host_rows.append(
            {
                'host_suffix': h.host_suffix,
                'bucket_id': h.bucket_id,
                'duration_us': h.duration_us,
                'seconds': h.seconds,
                'event_count': h.event_count,
            }
        )

    return {
        'report_id': res.report_id,
        'observed_until': res.observed_until.isoformat(),
        'read_started_at': res.read_started_at.isoformat(),
        'read_finished_at': res.read_finished_at.isoformat(),
        'standard_duration_us': res.standard_duration_us,
        'standard_seconds': res.standard_seconds,
        'host_rows': host_rows,
        'has_cross_host_conflict': res.has_cross_host_conflict,
        'conflict_reason': res.conflict_reason,
        'is_available': res.is_available,
        'unavailable_reason': res.unavailable_reason,
    }


# === HTTP Request Handler (SEC-01..04) ===


class ReportHttpServer(ThreadingHTTPServer):
    """Threading HTTP server holding per-server state and semaphore."""

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        service: ReportService,
        token: str,
        settings: ReportSettings,
    ) -> None:
        """Initialize server with dedicated service, token, and semaphore."""
        super().__init__(server_address, handler_class)
        self.service = service
        self.expected_token = token
        self.settings = settings
        self.connection_semaphore = threading.BoundedSemaphore(
            settings.max_concurrent_connections
        )


class ReportHttpHandler(BaseHTTPRequestHandler):
    """Serve JSON reporting endpoints with token, Host, and Origin guards."""

    @property
    def report_server(self) -> ReportHttpServer:
        """Return parent ReportHttpServer instance."""
        return self.server  # type: ignore[return-value]

    @property
    def service(self) -> ReportService:
        """Return parent server's ReportService."""
        return self.report_server.service

    @property
    def expected_token(self) -> str:
        """Return parent server's expected token."""
        return self.report_server.expected_token

    @property
    def bound_port(self) -> int:
        """Return parent server's bound port."""
        return self.report_server.server_port

    @property
    def settings(self) -> ReportSettings:
        """Return parent server's settings."""
        return self.report_server.settings

    def handle(self) -> None:
        """Process incoming connection bounded by concurrency semaphore."""
        acquired = self.report_server.connection_semaphore.acquire(
            blocking=False
        )
        if not acquired:
            err_body = json.dumps(
                {
                    'status': 'error',
                    'reason_code': 'connections_limit_exceeded',
                    'message': 'Concurrent connection limit exceeded',
                }
            ).encode()
            response_bytes = (
                b'HTTP/1.1 503 Service Unavailable\r\n'
                b'Content-Type: application/json; charset=utf-8\r\n'
                b'Cache-Control: no-store, no-cache, must-revalidate\r\n'
                b'Pragma: no-cache\r\n'
                b"Content-Security-Policy: default-src 'self'; "
                b"script-src 'self'; connect-src 'self'; "
                b"style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; "
                b"object-src 'none'; base-uri 'none'\r\n"
                b'Referrer-Policy: no-referrer\r\n'
                b'X-Content-Type-Options: nosniff\r\n'
                b'X-Frame-Options: DENY\r\n'
                b'Retry-After: 1\r\n'
                b'Connection: close\r\n'
                + f'Content-Length: {len(err_body)}\r\n\r\n'.encode()
                + err_body
            )
            self.wfile.write(response_bytes)
            return
        try:
            super().handle()
        finally:
            self.report_server.connection_semaphore.release()

    def log_message(self, format: str, *args: object) -> None:
        """Suppress standard HTTP server stderr logging."""

    def send_json_response(
        self,
        status_code: int,
        payload: dict[str, object],
    ) -> None:
        """Serialize and send JSON response with security headers (SEC-03)."""
        body_bytes = json.dumps(payload, indent=2).encode('utf-8')
        if len(body_bytes) > self.settings.max_serialized_response_bytes:
            err_payload = {
                'status': 'error',
                'reason_code': 'payload_too_large',
                'message': 'Serialized response exceeds 8 MiB budget',
            }
            body_bytes = json.dumps(err_payload).encode('utf-8')
            status_code = 500
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header(
            'Cache-Control', 'no-store, no-cache, must-revalidate'
        )
        self.send_header('Pragma', 'no-cache')
        self.send_header(
            'Content-Security-Policy',
            "default-src 'self'; script-src 'self'; connect-src 'self'; "
            "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; "
            "object-src 'none'; base-uri 'none'",
        )
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Length', str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def send_json_error(
        self,
        status_code: int,
        reason_code: str,
        message: str,
    ) -> None:
        """Send standardized JSON error response (OUT-06, SEC-04)."""
        self.send_json_response(
            status_code=status_code,
            payload={
                'status': 'error',
                'reason_code': reason_code,
                'message': message,
            },
        )

    def _verify_transport_security(self) -> bool:
        """Verify request length, Host, Origin, and Sec-Fetch-Site."""
        # 1. Content-Length check (LIMIT-04)
        content_length_header = self.headers.get('Content-Length')
        if content_length_header:
            try:
                content_length = int(content_length_header)
                if content_length > self.settings.max_request_bytes:
                    self.send_json_error(
                        413,
                        'request_too_large',
                        'Request body exceeds 16 KiB limit',
                    )
                    return False
            except ValueError:
                self.send_json_error(
                    400,
                    'invalid_parameters',
                    'Invalid Content-Length header',
                )
                return False

        # 2. Host check (SEC-01)
        host_header = self.headers.get('Host', '')
        allowed_hosts = {
            f'127.0.0.1:{self.bound_port}',
            f'localhost:{self.bound_port}',
        }
        if host_header.lower() not in allowed_hosts:
            self.send_json_error(
                403,
                'forbidden_host',
                'Host header must match bound loopback port',
            )
            return False

        # 3. Origin check (SEC-01)
        origin_header = self.headers.get('Origin')
        if origin_header:
            allowed_origins = {
                f'http://127.0.0.1:{self.bound_port}',
                f'http://localhost:{self.bound_port}',
            }
            if origin_header.lower() not in allowed_origins:
                self.send_json_error(
                    403,
                    'forbidden_origin',
                    'Cross-origin requests are forbidden',
                )
                return False

        # 4. Sec-Fetch-Site check (SEC-01)
        sec_fetch_site = self.headers.get('Sec-Fetch-Site', '').lower()
        if sec_fetch_site == 'cross-site':
            self.send_json_error(
                403,
                'forbidden_origin',
                'Cross-site requests are forbidden',
            )
            return False

        return True

    def _verify_token_security(self) -> bool:
        """Verify token authentication (SEC-01)."""
        token = self.headers.get('X-Report-Token')
        if not token:
            auth_header = self.headers.get('Authorization', '')
            if auth_header.startswith('Bearer '):
                token = auth_header[7:].strip()

        if (
            not token
            or not token.isascii()
            or not secrets.compare_digest(token, self.expected_token)
        ):
            self.send_json_error(
                401,
                'unauthorized_token',
                'Missing or invalid process authorization token',
            )
            return False

        return True

    def _verify_security(self) -> bool:
        """Verify full security: transport security and token check."""
        if not self._verify_transport_security():
            return False
        return self._verify_token_security()

    def _serve_static_asset(self, route_path: str) -> None:
        """Serve static asset with strict security headers (SEC-03)."""
        asset_info = STATIC_ASSET_ROUTES.get(route_path)
        if asset_info is None:
            self.send_json_error(404, 'not_found', 'Route not found')
            return

        file_name, content_type = asset_info
        asset_path = Path(__file__).parent / 'report_assets' / file_name
        try:
            content_bytes = asset_path.read_bytes()
        except OSError:
            self.send_json_error(
                500, 'internal_error', 'Static asset file missing'
            )
            return

        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header(
            'Cache-Control', 'no-store, no-cache, must-revalidate'
        )
        self.send_header('Pragma', 'no-cache')
        self.send_header(
            'Content-Security-Policy',
            "default-src 'self'; script-src 'self'; connect-src 'self'; "
            "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; "
            "object-src 'none'; base-uri 'none'",
        )
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Length', str(len(content_bytes)))
        self.end_headers()
        self.wfile.write(content_bytes)

    def _read_json_body(self) -> dict[str, object] | None:
        """Read and parse JSON request body."""
        content_length_header = self.headers.get('Content-Length')
        if not content_length_header:
            return {}
        try:
            length = int(content_length_header)
            if length == 0:
                return {}
            body_bytes = self.rfile.read(length)
            data = json.loads(body_bytes.decode('utf-8'))
            if not isinstance(data, dict):
                return None
            return data
        except Exception:
            return None

    def do_GET(self) -> None:  # noqa: N802
        """Handle GET requests."""
        if not self._verify_transport_security():
            return

        parsed = urlparse(self.path)
        path = parsed.path
        query_params = parse_qs(parsed.query)

        if path in STATIC_ASSET_ROUTES:
            self._serve_static_asset(path)
            return

        if not path.startswith('/api/'):
            self.send_json_error(404, 'not_found', 'Route not found')
            return

        if not self._verify_token_security():
            return

        if path == '/api/status':
            self.send_json_response(
                200,
                {
                    'status': 'ok',
                    'service': 'aw-watcher-orca-reporting',
                    'version': '0.1.0',
                },
            )
            return

        if path == '/api/catalog':
            try:
                catalog = self.service.get_catalog()
                self.send_json_response(
                    200,
                    {'status': 'ok', 'catalog': serialize_catalog(catalog)},
                )
            except ReportApiError as err:
                self.send_json_error(503, 'api_unavailable', str(err))
            except ReportMalformedPayloadError as err:
                self.send_json_error(502, 'malformed_payload', str(err))
            except ReportLimitError as err:
                self.send_json_error(422, 'limit_exceeded', str(err))
            except Exception:
                self.send_json_error(500, 'internal_error', 'Internal error')
            return

        if path == '/api/report':
            start_str = query_params.get('start_date', [''])[0]
            end_str = query_params.get('end_date', [''])[0]
            zone_str = query_params.get('zone_name', ['Europe/Minsk'])[0]
            proj_str = query_params.get('project_filter', [None])[0]
            exact_str = query_params.get('exact_project_match', ['false'])[0]
            exact_match = exact_str.lower() in ('true', '1')

            self._handle_report_calculation(
                start_str, end_str, zone_str, proj_str, exact_match
            )
            return

        if path == '/api/comparison':
            report_id = query_params.get('report_id', [''])[0]
            self._handle_comparison_calculation(report_id)
            return

        self.send_json_error(404, 'not_found', 'Route not found')

    def do_POST(self) -> None:  # noqa: N802
        """Handle POST requests."""
        if not self._verify_transport_security():
            return

        parsed = urlparse(self.path)
        path = parsed.path

        if not path.startswith('/api/'):
            self.send_json_error(404, 'not_found', 'Route not found')
            return

        if not self._verify_token_security():
            return

        if path in ('/api/status', '/api/catalog'):
            self.send_json_error(
                405, 'method_not_allowed', 'Method not allowed for this route'
            )
            return

        if path == '/api/report':
            body_data = self._read_json_body()
            if body_data is None:
                self.send_json_error(
                    400, 'invalid_parameters', 'Malformed JSON request body'
                )
                return

            start_str = str(body_data.get('start_date', ''))
            end_str = str(body_data.get('end_date', ''))
            zone_str = str(body_data.get('zone_name', 'Europe/Minsk'))
            proj_filter = body_data.get('project_filter')
            proj_str = str(proj_filter) if proj_filter is not None else None
            exact_match = bool(body_data.get('exact_project_match', False))

            self._handle_report_calculation(
                start_str, end_str, zone_str, proj_str, exact_match
            )
            return

        if path == '/api/comparison':
            body_data = self._read_json_body()
            if body_data is None:
                self.send_json_error(
                    400, 'invalid_parameters', 'Malformed JSON request body'
                )
                return

            report_id = str(body_data.get('report_id', ''))
            self._handle_comparison_calculation(report_id)
            return

        self.send_json_error(404, 'not_found', 'Route not found')

    def do_DELETE(self) -> None:  # noqa: N802
        """Handle DELETE requests."""
        self._handle_unsupported_method()

    def do_PUT(self) -> None:  # noqa: N802
        """Handle PUT requests."""
        self._handle_unsupported_method()

    def do_PATCH(self) -> None:  # noqa: N802
        """Handle PATCH requests."""
        self._handle_unsupported_method()

    def _handle_unsupported_method(self) -> None:
        """Return 405 Method Not Allowed."""
        if not self._verify_transport_security():
            return
        if (
            urlparse(self.path).path.startswith('/api/')
            and not self._verify_token_security()
        ):
            return
        self.send_json_error(405, 'method_not_allowed', 'Method not allowed')

    def _handle_report_calculation(
        self,
        start_str: str,
        end_str: str,
        zone_str: str,
        proj_str: str | None,
        exact_match: bool,
    ) -> None:
        """Validate parameters and execute report calculation."""
        try:
            start_date = date.fromisoformat(start_str)
            end_date = date.fromisoformat(end_str)
        except (ValueError, TypeError):
            self.send_json_error(
                400,
                'invalid_parameters',
                'Invalid start_date or end_date format (expected YYYY-MM-DD)',
            )
            return

        if end_date < start_date:
            self.send_json_error(
                400,
                'invalid_parameters',
                'end_date cannot be earlier than start_date',
            )
            return

        try:
            ZoneInfo(zone_str)
        except Exception:
            self.send_json_error(
                400,
                'invalid_parameters',
                f'Unsupported or invalid IANA timezone: {zone_str}',
            )
            return

        try:
            result = self.service.calculate_report(
                start_date=start_date,
                end_date=end_date,
                zone_name=zone_str,
                project_filter=proj_str,
                exact_project_match=exact_match,
            )
            self.send_json_response(
                200,
                {'status': 'ok', 'report': serialize_report_result(result)},
            )
        except ReportBusyError as err:
            self.send_json_error(429, CALCULATION_BUSY_REASON, str(err))
        except ReportLimitError as err:
            self.send_json_error(422, 'limit_exceeded', str(err))
        except ReportIncompleteError as err:
            self.send_json_error(422, 'incomplete_source', str(err))
        except ReportApiError as err:
            self.send_json_error(503, 'api_unavailable', str(err))
        except ReportMalformedPayloadError as err:
            self.send_json_error(502, 'malformed_payload', str(err))
        except Exception:
            self.send_json_error(500, 'internal_error', 'Calculation failed')

    def _handle_comparison_calculation(self, report_id: str) -> None:
        """Validate report_id and execute standard comparison calculation."""
        if not report_id:
            self.send_json_error(
                400, 'invalid_parameters', 'Missing report_id parameter'
            )
            return

        try:
            comp_result = self.service.calculate_comparison(report_id)
            self.send_json_response(
                200,
                {
                    'status': 'ok',
                    'comparison': serialize_comparison_result(comp_result),
                },
            )
        except ReportServiceError as err:
            if err.reason_code == 'unknown_report_id':
                self.send_json_error(404, err.reason_code, str(err))
            elif err.reason_code == 'expired_report_id':
                self.send_json_error(410, err.reason_code, str(err))
            else:
                self.send_json_error(400, err.reason_code, str(err))
        except ReportBusyError as err:
            self.send_json_error(429, CALCULATION_BUSY_REASON, str(err))
        except ReportApiError as err:
            self.send_json_error(503, 'api_unavailable', str(err))
        except ReportMalformedPayloadError as err:
            self.send_json_error(502, 'malformed_payload', str(err))
        except ReportLimitError as err:
            self.send_json_error(422, 'limit_exceeded', str(err))
        except Exception:
            self.send_json_error(
                500, 'internal_error', 'Comparison calculation failed'
            )


# === Server Factory (SEC-01) ===


def create_report_http_server(
    host: str = '127.0.0.1',
    port: int = 0,
    service: ReportService | None = None,
    token: str | None = None,
    settings: ReportSettings | None = None,
) -> ReportHttpServer:
    """Create configured ReportHttpServer on loopback interface."""
    active_settings = settings or ReportSettings()
    active_service = service or ReportService(settings=active_settings)
    active_token = token or secrets.token_hex(32)

    return ReportHttpServer(
        server_address=(host, port),
        handler_class=ReportHttpHandler,
        service=active_service,
        token=active_token,
        settings=active_settings,
    )
