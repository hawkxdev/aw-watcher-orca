"""Test read-only ActivityWatch bucket reads."""

import json
from email.message import Message
from types import TracebackType
from typing import Self
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

import aw_watcher_orca.activitywatch_reader as reader
from aw_watcher_orca.activitywatch_reader import read_activitywatch_buckets
from aw_watcher_orca.errors import (
    ActivityWatchConnectionError,
    ActivityWatchStatusError,
    MalformedActivityWatchPayloadError,
)

# === Fakes ===


class FakeResponse:
    """Provide one fake HTTP response."""

    def __init__(
        self,
        status: int,
        payload: object = None,
        *,
        raw_body: bytes | None = None,
    ) -> None:
        """Initialize one fake response."""
        self.status = status
        self._body = (
            raw_body if raw_body is not None else json.dumps(payload).encode()
        )

    def __enter__(self) -> Self:
        """Enter one fake response context."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Exit one fake response context."""
        del exc_type, exc_value, traceback

    def read(self) -> bytes:
        """Read the fake response body."""
        return self._body


def _install_opener(
    monkeypatch: pytest.MonkeyPatch,
    response: FakeResponse,
) -> list[tuple[Request, float]]:
    """Install one recording fake opener."""
    calls: list[tuple[Request, float]] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        """Record one fake HTTP request."""
        calls.append((request, timeout))
        return response

    monkeypatch.setattr(reader, 'urlopen', fake_urlopen)
    return calls


# === Read contract ===


def test_read_activitywatch_buckets_performs_one_get(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Perform exactly one read-only bucket request."""
    payload = {
        'aw-watcher-window_host-a': {
            'type': 'currentwindow',
            'last_updated': '2026-09-02T12:00:00+00:00',
        }
    }
    calls = _install_opener(monkeypatch, FakeResponse(200, payload))

    result = read_activitywatch_buckets()

    assert result == payload
    assert len(calls) == 1
    request, timeout = calls[0]
    assert request.full_url == 'http://localhost:5600/api/0/buckets/'
    assert request.get_method() == 'GET'
    assert timeout > 0


# === Failure contract ===


@pytest.mark.parametrize(
    'connection_error',
    [
        URLError('connection refused'),
        TimeoutError('timed out'),
        OSError('network unavailable'),
    ],
    ids=['url-error', 'timeout', 'os-error'],
)
def test_read_activitywatch_buckets_distinguishes_connection_failure(
    monkeypatch: pytest.MonkeyPatch,
    connection_error: OSError,
) -> None:
    """Distinguish an ActivityWatch connection failure."""
    calls: list[Request] = []

    def fail_urlopen(request: Request, timeout: float) -> FakeResponse:
        """Raise one fake connection failure."""
        del timeout
        calls.append(request)
        raise connection_error

    monkeypatch.setattr(reader, 'urlopen', fail_urlopen)

    with pytest.raises(
        ActivityWatchConnectionError,
        match='Unable to connect to ActivityWatch',
    ):
        read_activitywatch_buckets()

    assert len(calls) == 1


def test_read_activitywatch_buckets_distinguishes_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distinguish an ActivityWatch HTTP error."""
    calls: list[Request] = []

    def fail_urlopen(request: Request, timeout: float) -> FakeResponse:
        """Raise one fake HTTP error."""
        del timeout
        calls.append(request)
        raise HTTPError(request.full_url, 404, 'missing', Message(), None)

    monkeypatch.setattr(reader, 'urlopen', fail_urlopen)

    with pytest.raises(
        ActivityWatchStatusError,
        match='ActivityWatch bucket request returned status 404',
    ):
        read_activitywatch_buckets()

    assert len(calls) == 1


def test_read_activitywatch_buckets_distinguishes_non_200_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distinguish an unsuccessful ActivityWatch status."""
    calls = _install_opener(monkeypatch, FakeResponse(503, {}))

    with pytest.raises(
        ActivityWatchStatusError,
        match='ActivityWatch bucket request returned status 503',
    ):
        read_activitywatch_buckets()

    assert len(calls) == 1


@pytest.mark.parametrize(
    'raw_body',
    [b'{broken', b'\xff'],
    ids=['invalid-json', 'invalid-utf8'],
)
def test_read_activitywatch_buckets_rejects_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
    raw_body: bytes,
) -> None:
    """Reject invalid ActivityWatch JSON."""
    calls = _install_opener(
        monkeypatch,
        FakeResponse(200, raw_body=raw_body),
    )

    with pytest.raises(
        MalformedActivityWatchPayloadError,
        match='Malformed ActivityWatch bucket payload',
    ):
        read_activitywatch_buckets()

    assert len(calls) == 1


def test_read_activitywatch_buckets_rejects_non_object_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a non-object ActivityWatch payload."""
    calls = _install_opener(monkeypatch, FakeResponse(200, []))

    with pytest.raises(
        MalformedActivityWatchPayloadError,
        match='Malformed ActivityWatch bucket payload',
    ):
        read_activitywatch_buckets()

    assert len(calls) == 1


def test_read_activitywatch_buckets_rejects_non_object_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject non-object bucket metadata."""
    calls = _install_opener(
        monkeypatch,
        FakeResponse(200, {'aw-watcher-window_host-a': []}),
    )

    with pytest.raises(
        MalformedActivityWatchPayloadError,
        match='Malformed ActivityWatch bucket payload',
    ):
        read_activitywatch_buckets()

    assert len(calls) == 1
