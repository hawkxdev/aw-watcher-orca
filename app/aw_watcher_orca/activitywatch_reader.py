"""Read ActivityWatch bucket metadata."""

import json
from typing import Final, cast
from urllib.error import HTTPError, URLError
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
