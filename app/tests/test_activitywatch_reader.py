"""ActivityWatch reader tests."""

import json
from email.message import Message
from types import TracebackType
from typing import Self
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

import aw_watcher_orca.activitywatch_reader as reader
from aw_watcher_orca.activitywatch_reader import (
    read_activitywatch_buckets,
    read_last_bucket_event,
)
from aw_watcher_orca.errors import (
    ActivityWatchConnectionError,
    ActivityWatchStatusError,
    MalformedActivityWatchPayloadError,
)

# === Fakes ===


class FakeResponse:
    """Fake HTTP response."""

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
        """Enter fake response context."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Exit fake response context."""
        del exc_type, exc_value, traceback

    def read(self) -> bytes:
        """Read fake response body."""
        return self._body


def _install_opener(
    monkeypatch: pytest.MonkeyPatch,
    response: FakeResponse,
) -> list[tuple[Request, float]]:
    """Install recording fake opener."""
    calls: list[tuple[Request, float]] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        """Record fake HTTP request."""
        calls.append((request, timeout))
        return response

    monkeypatch.setattr(reader, 'urlopen', fake_urlopen)
    return calls


# === Read contract ===


def test_read_activitywatch_buckets_performs_one_get(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    calls: list[Request] = []

    def fail_urlopen(request: Request, timeout: float) -> FakeResponse:
        """Raise fake connection failure."""
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
    calls: list[Request] = []

    def fail_urlopen(request: Request, timeout: float) -> FakeResponse:
        """Raise fake HTTP error."""
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


# === Last event contract ===


def test_read_last_bucket_event_performs_one_get(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = [
        {
            'id': 1,
            'timestamp': '2026-09-02T12:00:00+00:00',
            'duration': 5.0,
            'data': {'app': 'Orca', 'title': 'repo / worktree'},
        }
    ]
    calls = _install_opener(monkeypatch, FakeResponse(200, payload))

    result = read_last_bucket_event('aw-watcher-window_host-a')

    assert result == payload[0]
    assert len(calls) == 1
    request, timeout = calls[0]
    assert (
        request.full_url
        == 'http://localhost:5600/api/0/buckets/aw-watcher-window_host-a/events?limit=1'
    )
    assert request.get_method() == 'GET'
    assert timeout > 0


def test_read_last_bucket_event_returns_none_on_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_opener(monkeypatch, FakeResponse(200, []))

    result = read_last_bucket_event('aw-watcher-window_host-a')

    assert result is None
    assert len(calls) == 1


def test_read_last_bucket_event_returns_none_on_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Request] = []

    def fail_urlopen(request: Request, timeout: float) -> FakeResponse:
        """Raise fake urlopen failure."""
        del timeout
        calls.append(request)
        raise HTTPError(request.full_url, 404, 'not found', Message(), None)

    monkeypatch.setattr(reader, 'urlopen', fail_urlopen)

    result = read_last_bucket_event('missing-bucket')

    assert result is None
    assert len(calls) == 1


@pytest.mark.parametrize(
    'connection_error',
    [
        URLError('connection refused'),
        TimeoutError('timed out'),
        OSError('network unavailable'),
    ],
    ids=['url-error', 'timeout', 'os-error'],
)
def test_read_last_bucket_event_distinguishes_connection_failure(
    monkeypatch: pytest.MonkeyPatch,
    connection_error: OSError,
) -> None:
    calls: list[Request] = []

    def fail_urlopen(request: Request, timeout: float) -> FakeResponse:
        """Raise fake urlopen failure."""
        del timeout
        calls.append(request)
        raise connection_error

    monkeypatch.setattr(reader, 'urlopen', fail_urlopen)

    with pytest.raises(
        ActivityWatchConnectionError,
        match='Unable to connect to ActivityWatch',
    ):
        read_last_bucket_event('aw-watcher-window_host-a')

    assert len(calls) == 1


def test_read_last_bucket_event_distinguishes_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Request] = []

    def fail_urlopen(request: Request, timeout: float) -> FakeResponse:
        """Raise fake urlopen failure."""
        del timeout
        calls.append(request)
        raise HTTPError(request.full_url, 500, 'server error', Message(), None)

    monkeypatch.setattr(reader, 'urlopen', fail_urlopen)

    with pytest.raises(
        ActivityWatchStatusError,
        match='ActivityWatch event request returned status 500',
    ):
        read_last_bucket_event('aw-watcher-window_host-a')

    assert len(calls) == 1


def test_read_last_bucket_event_distinguishes_non_200_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_opener(monkeypatch, FakeResponse(503, []))

    with pytest.raises(
        ActivityWatchStatusError,
        match='ActivityWatch event request returned status 503',
    ):
        read_last_bucket_event('aw-watcher-window_host-a')

    assert len(calls) == 1


@pytest.mark.parametrize(
    'raw_body',
    [b'{broken', b'\xff'],
    ids=['invalid-json', 'invalid-utf8'],
)
def test_read_last_bucket_event_rejects_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
    raw_body: bytes,
) -> None:
    calls = _install_opener(
        monkeypatch,
        FakeResponse(200, raw_body=raw_body),
    )

    with pytest.raises(
        MalformedActivityWatchPayloadError,
        match='Malformed ActivityWatch event payload',
    ):
        read_last_bucket_event('aw-watcher-window_host-a')

    assert len(calls) == 1


def test_read_last_bucket_event_rejects_non_list_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_opener(monkeypatch, FakeResponse(200, {}))

    with pytest.raises(
        MalformedActivityWatchPayloadError,
        match='Malformed ActivityWatch event payload',
    ):
        read_last_bucket_event('aw-watcher-window_host-a')

    assert len(calls) == 1


def test_read_last_bucket_event_rejects_non_dict_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_opener(monkeypatch, FakeResponse(200, ['not-a-dict']))

    with pytest.raises(
        MalformedActivityWatchPayloadError,
        match='Malformed ActivityWatch event payload',
    ):
        read_last_bucket_event('aw-watcher-window_host-a')

    assert len(calls) == 1
