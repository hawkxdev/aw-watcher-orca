"""Publish ActivityWatch project events."""

import json
import secrets
from collections.abc import Mapping
from datetime import datetime
from typing import Final
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from aw_watcher_orca.activitywatch_reader import (
    ACTIVITYWATCH_BUCKETS_URL,
    DEFAULT_ACTIVITYWATCH_TIMEOUT_SECONDS,
)
from aw_watcher_orca.errors import (
    ActivityWatchConnectionError,
    ActivityWatchStatusError,
)
from aw_watcher_orca.foreground import ORCA_APP_NAME
from aw_watcher_orca.models import ProjectAttribution

# === Constants ===


TEST_BUCKET_PREFIX: Final = 'aw-watcher-orca-test'
TEST_BUCKET_CLIENT: Final = 'aw-watcher-orca-test'
TEST_BUCKET_TYPE: Final = 'currentwindow'
DEFAULT_POLL_INTERVAL_SECONDS: Final = 2.0
DEFAULT_PULSE_TIME_SECONDS: Final = 3.0


# === Pure payload builders ===


def build_test_bucket_id(host_suffix: str) -> str:
    """Build the test bucket identifier."""
    return f'{TEST_BUCKET_PREFIX}_{host_suffix}'


def generate_session_token() -> str:
    """Generate one random session token."""
    return secrets.token_hex(16)


def build_active_event_data(
    attribution: ProjectAttribution,
    session_token: str,
    app_name: str = ORCA_APP_NAME,
) -> dict[str, object]:
    """Build active event data payload."""
    return {
        'app': app_name,
        'title': attribution.label,
        'repo': attribution.repo,
        'worktree': attribution.worktree,
        'schema_source': attribution.schema_source,
        'session_token': session_token,
    }


def build_neutral_event_data(
    session_token: str,
    app_name: str = ORCA_APP_NAME,
) -> dict[str, object]:
    """Build neutral event data payload."""
    return {
        'app': app_name,
        'title': '',
        'repo': '',
        'worktree': '',
        'session_token': session_token,
    }


def build_heartbeat_payload(
    timestamp: datetime,
    data: Mapping[str, object],
    duration: float = 0.0,
) -> dict[str, object]:
    """Build one heartbeat event payload."""
    return {
        'timestamp': timestamp.isoformat(),
        'duration': duration,
        'data': dict(data),
    }


# === I/O Operations ===


def create_test_bucket(
    host_suffix: str,
    base_url: str = ACTIVITYWATCH_BUCKETS_URL,
    timeout: float = DEFAULT_ACTIVITYWATCH_TIMEOUT_SECONDS,
) -> None:
    """Create one test ActivityWatch bucket."""
    bucket_id = build_test_bucket_id(host_suffix)
    encoded_id = quote(bucket_id, safe='')
    url = f'{base_url}{encoded_id}'
    body = json.dumps(
        {
            'client': TEST_BUCKET_CLIENT,
            'type': TEST_BUCKET_TYPE,
            'hostname': host_suffix,
        }
    ).encode('utf-8')
    request = Request(  # noqa: S310 (fixed local endpoint)
        url,
        data=body,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urlopen(  # noqa: S310 (fixed URL above)
            request,
            timeout=timeout,
        ) as response:
            status = response.status
    except HTTPError as error:
        if error.code == 304:
            return
        raise ActivityWatchStatusError(
            f'ActivityWatch bucket creation request returned '
            f'status {error.code}'
        ) from None
    except (URLError, TimeoutError, OSError):
        raise ActivityWatchConnectionError(
            'Unable to connect to ActivityWatch'
        ) from None
    if status not in {200, 304}:
        raise ActivityWatchStatusError(
            f'ActivityWatch bucket creation request returned status {status}'
        )


def send_heartbeat(
    bucket_id: str,
    payload: Mapping[str, object],
    pulse_time: float = DEFAULT_PULSE_TIME_SECONDS,
    base_url: str = ACTIVITYWATCH_BUCKETS_URL,
    timeout: float = DEFAULT_ACTIVITYWATCH_TIMEOUT_SECONDS,
) -> None:
    """Send one heartbeat event."""
    encoded_id = quote(bucket_id, safe='')
    url = f'{base_url}{encoded_id}/heartbeat?pulsetime={pulse_time}'
    body = json.dumps(payload).encode('utf-8')
    request = Request(  # noqa: S310 (fixed local endpoint)
        url,
        data=body,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urlopen(  # noqa: S310 (fixed URL above)
            request,
            timeout=timeout,
        ) as response:
            status = response.status
    except HTTPError as error:
        raise ActivityWatchStatusError(
            f'ActivityWatch heartbeat request returned status {error.code}'
        ) from None
    except (URLError, TimeoutError, OSError):
        raise ActivityWatchConnectionError(
            'Unable to connect to ActivityWatch'
        ) from None
    if status != 200:
        raise ActivityWatchStatusError(
            f'ActivityWatch heartbeat request returned status {status}'
        )
