"""Read ActivityWatch bucket metadata."""

import json
from typing import Final, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from aw_watcher_orca.errors import (
    ActivityWatchConnectionError,
    ActivityWatchStatusError,
    MalformedActivityWatchPayloadError,
)

# === Constants ===


ACTIVITYWATCH_BUCKETS_URL: Final = 'http://localhost:5600/api/0/buckets/'
DEFAULT_ACTIVITYWATCH_TIMEOUT_SECONDS: Final = 5.0


# === Read-only client ===


def read_activitywatch_buckets(
    timeout: float = DEFAULT_ACTIVITYWATCH_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Read the ActivityWatch bucket mapping."""
    # Request
    request = Request(
        ACTIVITYWATCH_BUCKETS_URL,
        method='GET',
    )  # noqa: S310 (fixed local HTTP endpoint)
    # Transport and status
    try:
        with urlopen(  # noqa: S310 (fixed URL above)
            request,
            timeout=timeout,
        ) as response:
            status = response.status
            body = response.read()
    except HTTPError as error:
        raise ActivityWatchStatusError(
            f'ActivityWatch bucket request returned status {error.code}'
        ) from None
    except (URLError, TimeoutError, OSError):
        raise ActivityWatchConnectionError(
            'Unable to connect to ActivityWatch'
        ) from None
    if status != 200:
        raise ActivityWatchStatusError(
            f'ActivityWatch bucket request returned status {status}'
        )
    # Payload shape
    try:
        payload = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MalformedActivityWatchPayloadError(
            'Malformed ActivityWatch bucket payload'
        ) from None
    if not isinstance(payload, dict) or any(
        not isinstance(metadata, dict) for metadata in payload.values()
    ):
        raise MalformedActivityWatchPayloadError(
            'Malformed ActivityWatch bucket payload'
        )
    return cast(dict[str, object], payload)


def read_last_bucket_event(
    bucket_id: str,
    timeout: float = DEFAULT_ACTIVITYWATCH_TIMEOUT_SECONDS,
) -> dict[str, object] | None:
    """Read the latest bucket event."""
    encoded_id = quote(bucket_id, safe='')
    url = f'{ACTIVITYWATCH_BUCKETS_URL}{encoded_id}/events?limit=1'
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
            f'ActivityWatch event request returned status {error.code}'
        ) from None
    except (URLError, TimeoutError, OSError):
        raise ActivityWatchConnectionError(
            'Unable to connect to ActivityWatch'
        ) from None
    if status == 404:
        return None
    if status != 200:
        raise ActivityWatchStatusError(
            f'ActivityWatch event request returned status {status}'
        )
    try:
        payload = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MalformedActivityWatchPayloadError(
            'Malformed ActivityWatch event payload'
        ) from None
    if not isinstance(payload, list):
        raise MalformedActivityWatchPayloadError(
            'Malformed ActivityWatch event payload'
        )
    if not payload:
        return None
    event = payload[0]
    if not isinstance(event, dict):
        raise MalformedActivityWatchPayloadError(
            'Malformed ActivityWatch event payload'
        )
    return cast(dict[str, object], event)
