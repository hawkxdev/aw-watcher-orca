"""Test ActivityWatch event publisher."""

import json
from datetime import UTC, datetime
from email.message import Message
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

import aw_watcher_orca.publisher as publisher_module
from aw_watcher_orca.errors import (
    ActivityWatchConnectionError,
    ActivityWatchStatusError,
)
from aw_watcher_orca.models import ProjectAttribution
from aw_watcher_orca.publisher import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_PULSE_TIME_SECONDS,
    TEST_BUCKET_CLIENT,
    TEST_BUCKET_PREFIX,
    TEST_BUCKET_TYPE,
    build_active_event_data,
    build_heartbeat_payload,
    build_neutral_event_data,
    build_test_bucket_id,
    create_test_bucket,
    generate_session_token,
    send_heartbeat,
)

# === Constants ===


REFERENCE_TIME = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)


# === Pure payload contract ===


def test_build_test_bucket_id_appends_suffix() -> None:
    assert (
        build_test_bucket_id('192.0.2.45') == 'aw-watcher-orca-test_192.0.2.45'
    )


def test_test_prefix_cannot_be_turned_into_production_id() -> None:
    assert TEST_BUCKET_PREFIX == 'aw-watcher-orca-test'
    assert TEST_BUCKET_CLIENT == 'aw-watcher-orca-test'
    assert TEST_BUCKET_TYPE == 'currentwindow'
    assert 'production' not in TEST_BUCKET_PREFIX
    assert not TEST_BUCKET_PREFIX.endswith('_')
    bucket_id = build_test_bucket_id('my-host')
    assert bucket_id.startswith('aw-watcher-orca-test_')


def test_build_active_event_data_has_exact_keys() -> None:
    attribution = ProjectAttribution(
        repo='my-repo',
        worktree='feat-1',
        label='my-repo / feat-1',
        is_main_worktree=False,
        schema_source='orca-cli-v1',
    )
    session_id = 'session-tok-123'

    data = build_active_event_data(attribution, session_id)

    expected_keys = {
        'app',
        'title',
        'repo',
        'worktree',
        'schema_source',
        'session_token',
    }
    assert set(data.keys()) == expected_keys
    assert data['app'] == 'Orca'
    assert data['title'] == 'my-repo / feat-1'
    assert data['repo'] == 'my-repo'
    assert data['worktree'] == 'feat-1'
    assert data['schema_source'] == 'orca-cli-v1'
    assert data['session_token'] == session_id


def test_build_neutral_event_data_has_exact_keys() -> None:
    session_id = 'session-tok-456'

    data = build_neutral_event_data(session_id)

    expected_keys = {
        'app',
        'title',
        'repo',
        'worktree',
        'session_token',
    }
    assert set(data.keys()) == expected_keys
    assert data['app'] == 'Orca'
    assert data['title'] == ''
    assert data['repo'] == ''
    assert data['worktree'] == ''
    assert data['session_token'] == session_id


def test_generate_session_token_is_random_string() -> None:
    tok1 = generate_session_token()
    tok2 = generate_session_token()

    assert isinstance(tok1, str)
    assert isinstance(tok2, str)
    assert tok1 != tok2
    assert len(tok1) >= 16


def test_build_heartbeat_payload_has_duration_zero() -> None:
    payload = build_heartbeat_payload(
        timestamp=REFERENCE_TIME,
        data={'app': 'Orca'},
    )

    assert payload['timestamp'] == REFERENCE_TIME.isoformat()
    assert payload['duration'] == 0.0
    assert payload['data'] == {'app': 'Orca'}


def test_named_constants_match_specification() -> None:
    assert DEFAULT_POLL_INTERVAL_SECONDS == 2.0
    assert DEFAULT_PULSE_TIME_SECONDS == 3.0


# === I/O Client contract ===


class FakeResponse:
    """Provide one fake HTTP response."""

    def __init__(self, status: int, body: bytes) -> None:
        """Initialize one fake response."""
        self.status = status
        self._body = body

    def __enter__(self) -> 'FakeResponse':
        """Enter context."""
        return self

    def __exit__(self, *args: Any) -> None:
        """Exit context."""

    def read(self) -> bytes:
        """Read body."""
        return self._body


def test_create_test_bucket_performs_idempotent_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Request] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        """Serve one fake urlopen."""
        del timeout
        calls.append(request)
        return FakeResponse(200, b'true')

    monkeypatch.setattr(publisher_module, 'urlopen', fake_urlopen)

    create_test_bucket(host_suffix='host-a')

    assert len(calls) == 1
    req = calls[0]
    assert (
        req.full_url
        == 'http://localhost:5600/api/0/buckets/aw-watcher-orca-test_host-a'
    )
    assert req.get_method() == 'POST'
    assert isinstance(req.data, bytes)
    body = json.loads(req.data.decode('utf-8'))
    assert body == {
        'client': TEST_BUCKET_CLIENT,
        'type': TEST_BUCKET_TYPE,
        'hostname': 'host-a',
    }


def test_create_test_bucket_accepts_already_existing_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Request] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        """Serve one fake urlopen."""
        del timeout
        calls.append(request)
        return FakeResponse(200, b'false')

    monkeypatch.setattr(publisher_module, 'urlopen', fake_urlopen)

    create_test_bucket(host_suffix='host-a')

    assert len(calls) == 1


def test_create_test_bucket_accepts_http_304_not_modified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Request] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        """Serve one fake urlopen."""
        del timeout
        calls.append(request)
        raise HTTPError(request.full_url, 304, 'not modified', Message(), None)

    monkeypatch.setattr(publisher_module, 'urlopen', fake_urlopen)

    create_test_bucket(host_suffix='host-a')

    assert len(calls) == 1


def test_create_test_bucket_raises_on_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        """Serve one fake urlopen."""
        del request, timeout
        raise URLError('connection refused')

    monkeypatch.setattr(publisher_module, 'urlopen', fake_urlopen)

    with pytest.raises(
        ActivityWatchConnectionError,
        match='Unable to connect to ActivityWatch',
    ):
        create_test_bucket(host_suffix='host-a')


def test_create_test_bucket_raises_on_server_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        """Serve one fake urlopen."""
        del timeout
        raise HTTPError(request.full_url, 500, 'server error', Message(), None)

    monkeypatch.setattr(publisher_module, 'urlopen', fake_urlopen)

    with pytest.raises(
        ActivityWatchStatusError,
        match='ActivityWatch bucket creation request returned status 500',
    ):
        create_test_bucket(host_suffix='host-a')


def test_send_heartbeat_performs_post_with_pulsetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Request] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        """Serve one fake urlopen."""
        del timeout
        calls.append(request)
        return FakeResponse(200, b'{"id": 1}')

    monkeypatch.setattr(publisher_module, 'urlopen', fake_urlopen)

    payload = build_heartbeat_payload(
        timestamp=REFERENCE_TIME,
        data={'app': 'Orca', 'title': 'my-repo'},
    )
    send_heartbeat(
        bucket_id='aw-watcher-orca-test_host-a',
        payload=payload,
        pulse_time=3.0,
    )

    assert len(calls) == 1
    req = calls[0]
    assert (
        req.full_url
        == 'http://localhost:5600/api/0/buckets/aw-watcher-orca-test_host-a/heartbeat?pulsetime=3.0'
    )
    assert req.get_method() == 'POST'
    assert isinstance(req.data, bytes)
    body = json.loads(req.data.decode('utf-8'))
    assert body['duration'] == 0.0
    assert body['data']['app'] == 'Orca'


def test_send_heartbeat_raises_on_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        """Serve one fake urlopen."""
        del request, timeout
        raise TimeoutError('timed out')

    monkeypatch.setattr(publisher_module, 'urlopen', fake_urlopen)

    with pytest.raises(
        ActivityWatchConnectionError,
        match='Unable to connect to ActivityWatch',
    ):
        send_heartbeat(
            bucket_id='aw-watcher-orca-test_host-a',
            payload={
                'timestamp': REFERENCE_TIME.isoformat(),
                'duration': 0.0,
                'data': {},
            },
            pulse_time=3.0,
        )


def test_send_heartbeat_raises_on_status_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        """Serve one fake urlopen."""
        del timeout
        raise HTTPError(request.full_url, 400, 'bad request', Message(), None)

    monkeypatch.setattr(publisher_module, 'urlopen', fake_urlopen)

    with pytest.raises(
        ActivityWatchStatusError,
        match='ActivityWatch heartbeat request returned status 400',
    ):
        send_heartbeat(
            bucket_id='aw-watcher-orca-test_host-a',
            payload={
                'timestamp': REFERENCE_TIME.isoformat(),
                'duration': 0.0,
                'data': {},
            },
            pulse_time=3.0,
        )
