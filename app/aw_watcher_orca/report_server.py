"""Entry point and server runner for local project statistics report."""

import argparse
import secrets
import sys
import threading
import webbrowser
from collections.abc import Callable

from aw_watcher_orca.report_http import (
    create_report_http_server,
    create_safe_aw_opener,
)
from aw_watcher_orca.report_service import ReportService
from aw_watcher_orca.report_settings import (
    DEFAULT_ACTIVITYWATCH_BASE_URL,
    ReportSettings,
)


def run_report_server(
    host: str = '127.0.0.1',
    port: int = 0,
    open_browser: bool = True,
    browser_opener: Callable[[str], None] | None = None,
    shutdown_event: threading.Event | None = None,
    base_url: str = DEFAULT_ACTIVITYWATCH_BASE_URL,
    settings: ReportSettings | None = None,
    token: str | None = None,
) -> int:
    """Run local reporting HTTP server on loopback interface with token guard.

    Args:
        host: IP interface to bind (must be 127.0.0.1).
        port: TCP port to bind (0 selects any available free port).
        open_browser: Whether to open default web browser with startup URL.
        browser_opener: Optional custom callable to open browser in tests.
        shutdown_event: Optional threading.Event to signal graceful shutdown.
        base_url: Base URL of local ActivityWatch instance.
        settings: Optional reporting budgets and limit configuration.
        token: Optional explicit 256-bit token (otherwise generated).

    Returns:
        Process exit code (0 on clean shutdown).
    """
    if host not in ('127.0.0.1', 'localhost'):
        raise ValueError(
            f"Invalid host '{host}': report server only listens on loopback"
        )

    active_settings = settings or ReportSettings()
    active_token = token or secrets.token_hex(32)
    # 1. Build secure ActivityWatch transport opener (SEC-02)
    opener = create_safe_aw_opener()

    # 2. Instantiate ReportService composition root
    service = ReportService(
        base_url=base_url,
        settings=active_settings,
        opener=opener,
    )

    # 3. Create HTTP server listening on loopback (SEC-01)
    server = create_report_http_server(
        host=host,
        port=port,
        service=service,
        token=active_token,
        settings=active_settings,
    )
    bound_port = server.server_port
    start_url = f'http://{host}:{bound_port}/#token={active_token}'

    print(f'Orca Report Service listening on http://{host}:{bound_port}/')
    print(f'Open URL: {start_url}')

    # 4. Open browser if requested
    if open_browser:
        if browser_opener is not None:
            browser_opener(start_url)
        else:
            webbrowser.open(start_url)

    # 5. Handle graceful shutdown
    if shutdown_event is not None:
        event_ref = shutdown_event

        def _wait_for_shutdown() -> None:
            event_ref.wait()
            server.shutdown()

        waiter_thread = threading.Thread(
            target=_wait_for_shutdown, daemon=True
        )
        waiter_thread.start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments and run report server process."""
    parser = argparse.ArgumentParser(
        prog='aw-watcher-orca-report',
        description='Serve local Orca project statistics reporting UI.',
    )
    parser.add_argument(
        '--host',
        default='127.0.0.1',
        help='Loopback host address to bind (default: 127.0.0.1)',
    )
    parser.add_argument(
        '--port',
        type=int,
        default=0,
        help='TCP port to bind (default: 0 for automatic free port)',
    )
    parser.add_argument(
        '--no-browser',
        action='store_true',
        help='Do not open browser automatically on startup',
    )
    parser.add_argument(
        '--aw-url',
        default=DEFAULT_ACTIVITYWATCH_BASE_URL,
        help='ActivityWatch base URL (default: http://localhost:5600)',
    )

    args = parser.parse_args(argv)

    return run_report_server(
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
        base_url=args.aw_url,
    )


if __name__ == '__main__':
    sys.exit(main())
