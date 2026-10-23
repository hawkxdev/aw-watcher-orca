"""Publish ActivityWatch project events."""

import json
import secrets
from collections.abc import Mapping
from datetime import datetime
from typing import Final, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from aw_watcher_orca.activitywatch_reader import (
    ACTIVITYWATCH_BUCKETS_URL,
    DEFAULT_ACTIVITYWATCH_TIMEOUT_SECONDS,
)
from aw_watcher_orca.bucket_target import (
    TEST_BUCKET_TARGET,
    BucketTargetProfile,
    ConfirmedBucketTarget,
    build_bucket_id,
    confirm_bucket_target,
)
from aw_watcher_orca.errors import (
    ActivityWatchConnectionError,
    ActivityWatchStatusError,
    MalformedActivityWatchPayloadError,
)
from aw_watcher_orca.foreground import ORCA_APP_NAME
from aw_watcher_orca.models import ProjectAttribution

# === Constants ===


TEST_BUCKET_PREFIX: Final = TEST_BUCKET_TARGET.prefix
TEST_BUCKET_CLIENT: Final = TEST_BUCKET_TARGET.client
TEST_BUCKET_TYPE: Final = TEST_BUCKET_TARGET.bucket_type
DEFAULT_POLL_INTERVAL_SECONDS: Final = 2.0
DEFAULT_PULSE_TIME_SECONDS: Final = 3.0


# === Pure payload builders ===


def build_test_bucket_id(host_suffix: str) -> str:
    """Build the test bucket identifier."""
    return build_bucket_id(TEST_BUCKET_TARGET, host_suffix)


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


# === Bucket metadata verification ===


def _read_bucket_metadata(
    bucket_id: str,
    base_url: str,
    timeout: float,
) -> dict[str, object] | None:
    """Read the metadata of one bucket, or None when it is absent."""
    encoded_id = quote(bucket_id, safe='')
    url = f'{base_url}{encoded_id}'
    request = Request(url, method='GET')  # noqa: S310 (fixed local endpoint)
    try:
        with urlopen(  # noqa: S310 (fixed URL above)
            request,
            timeout=timeout,
        ) as response:
            status = response.status
            body = response.read()
    except HTTPError as error:
        if error.code == 404:
            return None
        raise ActivityWatchStatusError(
            f'ActivityWatch bucket metadata request returned '
            f'status {error.code}'
        ) from None
    except (URLError, TimeoutError, OSError):
        raise ActivityWatchConnectionError(
            'Unable to connect to ActivityWatch'
        ) from None
    if status == 404:
        return None
    if status != 200:
        raise ActivityWatchStatusError(
            f'ActivityWatch bucket metadata request returned status {status}'
        )
    try:
        payload = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MalformedActivityWatchPayloadError(
            'Malformed ActivityWatch bucket payload'
        ) from None
    if not isinstance(payload, dict):
        raise MalformedActivityWatchPayloadError(
            'Malformed ActivityWatch bucket payload'
        )
    return cast(dict[str, object], payload)


# === I/O Operations ===


def create_bucket(
    profile: BucketTargetProfile,
    host_suffix: str,
    base_url: str = ACTIVITYWATCH_BUCKETS_URL,
    timeout: float = DEFAULT_ACTIVITYWATCH_TIMEOUT_SECONDS,
) -> ConfirmedBucketTarget:
    """Create or accept one bucket and return its confirmed target."""
    bucket_id = build_bucket_id(profile, host_suffix)
    existing = _read_bucket_metadata(bucket_id, base_url, timeout)
    if existing is not None:
        return confirm_bucket_target(profile, host_suffix, existing)
    _post_bucket(profile, host_suffix, bucket_id, base_url, timeout)
    confirmed = _read_bucket_metadata(bucket_id, base_url, timeout)
    return confirm_bucket_target(profile, host_suffix, confirmed)


def _post_bucket(
    profile: BucketTargetProfile,
    host_suffix: str,
    bucket_id: str,
    base_url: str,
    timeout: float,
) -> None:
    """Post one idempotent bucket creation request."""
    encoded_id = quote(bucket_id, safe='')
    url = f'{base_url}{encoded_id}'
    body = json.dumps(
        {
            'client': profile.client,
            'type': profile.bucket_type,
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


def create_test_bucket(
    host_suffix: str,
    base_url: str = ACTIVITYWATCH_BUCKETS_URL,
    timeout: float = DEFAULT_ACTIVITYWATCH_TIMEOUT_SECONDS,
) -> ConfirmedBucketTarget:
    """Create one test ActivityWatch bucket."""
    return create_bucket(TEST_BUCKET_TARGET, host_suffix, base_url, timeout)


def send_heartbeat(
    target: ConfirmedBucketTarget,
    payload: Mapping[str, object],
    pulse_time: float = DEFAULT_PULSE_TIME_SECONDS,
    base_url: str = ACTIVITYWATCH_BUCKETS_URL,
    timeout: float = DEFAULT_ACTIVITYWATCH_TIMEOUT_SECONDS,
) -> None:
    """Send one heartbeat event into a confirmed bucket target."""
    encoded_id = quote(target.bucket_id, safe='')
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
