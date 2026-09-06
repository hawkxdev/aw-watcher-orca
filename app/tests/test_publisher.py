"""Test ActivityWatch event publisher."""

import dataclasses
import json
from datetime import UTC, datetime
from email.message import Message
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

import aw_watcher_orca.publisher as publisher_module
from aw_watcher_orca.bucket_target import (
    BUCKET_TARGET_TYPE,
    PRODUCTION_BUCKET_TARGET,
    TEST_BUCKET_TARGET,
    BucketMetadataMismatchError,
    BucketTargetProfile,
    UnknownBucketTargetError,
)
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
    create_bucket,
    create_test_bucket,
    generate_session_token,
    send_heartbeat,
)

# === Constants ===


REFERENCE_TIME = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)
BUCKETS_URL = 'http://localhost:5600/api/0/buckets/'
TEST_BUCKET_URL = f'{BUCKETS_URL}aw-watcher-orca-test_host-a'
PRODUCTION_BUCKET_URL = f'{BUCKETS_URL}aw-watcher-orca_host-a'
TEST_METADATA: dict[str, object] = {
    'client': 'aw-watcher-orca-test',
    'type': 'currentwindow',
    'hostname': 'host-a',
}
PRODUCTION_METADATA: dict[str, object] = {
    'client': 'aw-watcher-orca',
    'type': 'currentwindow',
    'hostname': 'host-a',
}
FOREIGN_PROFILE = BucketTargetProfile(
    mode='x',
    prefix='third-prefix',
    client='third-client',
    bucket_type=BUCKET_TARGET_TYPE,
)


# === Fakes ===


class FakeResponse:
    """Fake HTTP response."""

    def __init__(self, status: int, body: bytes) -> None:
        """Initialize one fake response."""
        self.status = status
        self._body = body

    def __enter__(self) -> 'FakeResponse':
        """Enter fake context."""
        return self

    def __exit__(self, *args: Any) -> None:
        """Exit fake context."""

    def read(self) -> bytes:
        """Read fake body."""
        return self._body


def select_posts(calls: list[Request]) -> list[Request]:
    """Select the POST requests out of recorded calls."""
    return [call for call in calls if call.get_method() == 'POST']


class FakeActivityWatch:
    """Route fake ActivityWatch bucket responses by request method."""

    def __init__(
        self,
        reads: list[object],
        post_status: int = 200,
        post_error: int | None = None,
    ) -> None:
        """Initialize one fake ActivityWatch endpoint."""
        self.reads = list(reads)
        self.post_status = post_status
        self.post_error = post_error
        self.calls: list[Request] = []

    def methods(self) -> list[str]:
        """Report the recorded request methods in order."""
        return [call.get_method() for call in self.calls]

    def __call__(self, request: Request, timeout: float) -> FakeResponse:
        """Serve one fake urlopen."""
        del timeout
        self.calls.append(request)
        if request.get_method() == 'POST':
            if self.post_error is not None:
                raise HTTPError(
                    request.full_url,
                    self.post_error,
                    'post error',
                    Message(),
                    None,
                )
            return FakeResponse(self.post_status, b'true')
        if not self.reads:
            raise AssertionError('unexpected metadata read')
        current = self.reads.pop(0)
        if isinstance(current, BaseException):
            raise current
        if current is None:
            raise HTTPError(
                request.full_url, 404, 'not found', Message(), None
            )
        return FakeResponse(200, json.dumps(current).encode('utf-8'))


# === Frozen contract ===


class TestFrozenPublisherContract:
    """Freeze publisher behaviour accepted before the Stage 7B rework.

    Characterization tests: written after the code, green on arrival.
    Assertions name the method, the URL and the body of each request and
    never the number of HTTP calls, because bucket metadata reads are
    added by Stage 7B task 4.
    """

    def test_build_test_bucket_id_appends_suffix(self) -> None:
        assert (
            build_test_bucket_id('192.0.2.45')
            == 'aw-watcher-orca-test_192.0.2.45'
        )

    def test_test_prefix_cannot_be_turned_into_production_id(self) -> None:
        assert TEST_BUCKET_PREFIX == 'aw-watcher-orca-test'
        assert TEST_BUCKET_CLIENT == 'aw-watcher-orca-test'
        assert TEST_BUCKET_TYPE == 'currentwindow'
        assert 'production' not in TEST_BUCKET_PREFIX
        assert not TEST_BUCKET_PREFIX.endswith('_')
        bucket_id = build_test_bucket_id('my-host')
        assert bucket_id.startswith('aw-watcher-orca-test_')

    def test_build_active_event_data_has_exact_keys(self) -> None:
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

    def test_build_neutral_event_data_has_exact_keys(self) -> None:
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

    def test_build_heartbeat_payload_has_duration_zero(self) -> None:
        payload = build_heartbeat_payload(
            timestamp=REFERENCE_TIME,
            data={'app': 'Orca'},
        )

        assert payload['timestamp'] == REFERENCE_TIME.isoformat()
        assert payload['duration'] == 0.0
        assert payload['data'] == {'app': 'Orca'}

    def test_create_test_bucket_performs_idempotent_post(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        endpoint = FakeActivityWatch(reads=[None, TEST_METADATA])
        monkeypatch.setattr(publisher_module, 'urlopen', endpoint)

        create_test_bucket(host_suffix='host-a')

        posts = select_posts(endpoint.calls)
        assert len(posts) == 1
        assert posts[0].full_url == TEST_BUCKET_URL
        assert isinstance(posts[0].data, bytes)
        body = json.loads(posts[0].data.decode('utf-8'))
        assert body == {
            'client': TEST_BUCKET_CLIENT,
            'type': TEST_BUCKET_TYPE,
            'hostname': 'host-a',
        }

    def test_create_test_bucket_accepts_already_existing_bucket(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        endpoint = FakeActivityWatch(reads=[TEST_METADATA])
        monkeypatch.setattr(publisher_module, 'urlopen', endpoint)

        create_test_bucket(host_suffix='host-a')

        assert endpoint.methods() == ['GET']
        assert endpoint.calls[0].full_url == TEST_BUCKET_URL

    def test_create_test_bucket_accepts_http_304_not_modified(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        endpoint = FakeActivityWatch(
            reads=[None, TEST_METADATA],
            post_error=304,
        )
        monkeypatch.setattr(publisher_module, 'urlopen', endpoint)

        create_test_bucket(host_suffix='host-a')

        posts = select_posts(endpoint.calls)
        assert len(posts) == 1
        assert posts[0].full_url == TEST_BUCKET_URL

    def test_send_heartbeat_performs_post_with_pulsetime(
        self,
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

        posts = select_posts(calls)
        assert len(posts) == 1
        assert (
            posts[0].full_url == f'{TEST_BUCKET_URL}/heartbeat?pulsetime=3.0'
        )
        assert isinstance(posts[0].data, bytes)
        body = json.loads(posts[0].data.decode('utf-8'))
        assert body['duration'] == 0.0
        assert body['data']['app'] == 'Orca'


# === Pure payload contract ===


def test_generate_session_token_is_random_string() -> None:
    tok1 = generate_session_token()
    tok2 = generate_session_token()

    assert isinstance(tok1, str)
    assert isinstance(tok2, str)
    assert tok1 != tok2
    assert len(tok1) >= 16


def test_named_constants_match_specification() -> None:
    assert DEFAULT_POLL_INTERVAL_SECONDS == 2.0
    assert DEFAULT_PULSE_TIME_SECONDS == 3.0


# === I/O Client contract ===


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
    endpoint = FakeActivityWatch(reads=[None], post_error=500)
    monkeypatch.setattr(publisher_module, 'urlopen', endpoint)

    with pytest.raises(
        ActivityWatchStatusError,
        match='ActivityWatch bucket creation request returned status 500',
    ):
        create_test_bucket(host_suffix='host-a')


def test_create_test_bucket_raises_on_metadata_status_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = FakeActivityWatch(
        reads=[
            HTTPError(TEST_BUCKET_URL, 500, 'boom', Message(), None),
        ]
    )
    monkeypatch.setattr(publisher_module, 'urlopen', endpoint)

    with pytest.raises(
        ActivityWatchStatusError,
        match='ActivityWatch bucket metadata request returned status 500',
    ):
        create_test_bucket(host_suffix='host-a')


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


# === Bucket target verification ===


class TestBucketTargetVerification:
    """Prove that only an exactly matching bucket may receive heartbeats."""

    def test_matching_existing_bucket_is_accepted_without_creation(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        endpoint = FakeActivityWatch(reads=[TEST_METADATA])
        monkeypatch.setattr(publisher_module, 'urlopen', endpoint)

        create_bucket(TEST_BUCKET_TARGET, host_suffix='host-a')

        assert endpoint.methods() == ['GET']

    @pytest.mark.parametrize(
        'metadata',
        [
            {
                'client': 'aw-watcher-orca',
                'type': 'currentwindow',
                'hostname': 'host-a',
            },
            {
                'client': 'aw-watcher-window',
                'type': 'currentwindow',
                'hostname': 'host-a',
            },
        ],
    )
    def test_client_mismatch_blocks_heartbeat(
        self,
        monkeypatch: pytest.MonkeyPatch,
        metadata: dict[str, object],
    ) -> None:
        endpoint = FakeActivityWatch(reads=[metadata])
        monkeypatch.setattr(publisher_module, 'urlopen', endpoint)

        with pytest.raises(BucketMetadataMismatchError):
            create_bucket(TEST_BUCKET_TARGET, host_suffix='host-a')

        assert select_posts(endpoint.calls) == []

    def test_type_mismatch_blocks_heartbeat(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        endpoint = FakeActivityWatch(
            reads=[
                {
                    'client': 'aw-watcher-orca-test',
                    'type': 'afkstatus',
                    'hostname': 'host-a',
                }
            ]
        )
        monkeypatch.setattr(publisher_module, 'urlopen', endpoint)

        with pytest.raises(BucketMetadataMismatchError):
            create_bucket(TEST_BUCKET_TARGET, host_suffix='host-a')

    def test_hostname_mismatch_blocks_heartbeat(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        endpoint = FakeActivityWatch(
            reads=[
                {
                    'client': 'aw-watcher-orca-test',
                    'type': 'currentwindow',
                    'hostname': 'host-b',
                }
            ]
        )
        monkeypatch.setattr(publisher_module, 'urlopen', endpoint)

        with pytest.raises(BucketMetadataMismatchError):
            create_bucket(TEST_BUCKET_TARGET, host_suffix='host-a')

    @pytest.mark.parametrize('missing', ['client', 'type', 'hostname'])
    def test_missing_metadata_key_is_a_mismatch_not_a_lookup_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        missing: str,
    ) -> None:
        metadata = {
            key: value
            for key, value in TEST_METADATA.items()
            if key != missing
        }
        endpoint = FakeActivityWatch(reads=[metadata])
        monkeypatch.setattr(publisher_module, 'urlopen', endpoint)

        with pytest.raises(BucketMetadataMismatchError):
            create_bucket(TEST_BUCKET_TARGET, host_suffix='host-a')

    @pytest.mark.parametrize('key', ['client', 'type', 'hostname'])
    def test_non_string_metadata_value_is_a_mismatch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        key: str,
    ) -> None:
        metadata = dict(TEST_METADATA)
        metadata[key] = 17
        endpoint = FakeActivityWatch(reads=[metadata])
        monkeypatch.setattr(publisher_module, 'urlopen', endpoint)

        with pytest.raises(BucketMetadataMismatchError):
            create_bucket(TEST_BUCKET_TARGET, host_suffix='host-a')

    def test_created_bucket_is_reread_before_heartbeat_is_allowed(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        endpoint = FakeActivityWatch(reads=[None, TEST_METADATA])
        monkeypatch.setattr(publisher_module, 'urlopen', endpoint)

        create_bucket(TEST_BUCKET_TARGET, host_suffix='host-a')

        assert endpoint.methods() == ['GET', 'POST', 'GET']
        assert endpoint.reads == []

    def test_created_bucket_is_reread_after_http_304(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        endpoint = FakeActivityWatch(
            reads=[None, TEST_METADATA],
            post_error=304,
        )
        monkeypatch.setattr(publisher_module, 'urlopen', endpoint)

        create_bucket(TEST_BUCKET_TARGET, host_suffix='host-a')

        assert endpoint.methods() == ['GET', 'POST', 'GET']
        assert endpoint.reads == []

    def test_mismatch_after_creation_blocks_heartbeat(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        endpoint = FakeActivityWatch(reads=[None, PRODUCTION_METADATA])
        monkeypatch.setattr(publisher_module, 'urlopen', endpoint)

        with pytest.raises(BucketMetadataMismatchError):
            create_bucket(TEST_BUCKET_TARGET, host_suffix='host-a')

        assert endpoint.methods() == ['GET', 'POST', 'GET']

    def test_mismatch_after_http_304_blocks_heartbeat(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        endpoint = FakeActivityWatch(
            reads=[None, PRODUCTION_METADATA],
            post_error=304,
        )
        monkeypatch.setattr(publisher_module, 'urlopen', endpoint)

        with pytest.raises(BucketMetadataMismatchError):
            create_bucket(TEST_BUCKET_TARGET, host_suffix='host-a')

    def test_absent_bucket_after_creation_blocks_heartbeat(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        endpoint = FakeActivityWatch(reads=[None, None])
        monkeypatch.setattr(publisher_module, 'urlopen', endpoint)

        with pytest.raises(BucketMetadataMismatchError):
            create_bucket(TEST_BUCKET_TARGET, host_suffix='host-a')

    def test_unreachable_recheck_blocks_heartbeat(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        endpoint = FakeActivityWatch(
            reads=[None, URLError('connection refused')]
        )
        monkeypatch.setattr(publisher_module, 'urlopen', endpoint)

        with pytest.raises(
            ActivityWatchConnectionError,
            match='Unable to connect to ActivityWatch',
        ):
            create_bucket(TEST_BUCKET_TARGET, host_suffix='host-a')

        assert endpoint.methods() == ['GET', 'POST', 'GET']

    def test_unreadable_recheck_status_blocks_heartbeat(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        endpoint = FakeActivityWatch(
            reads=[
                None,
                HTTPError(TEST_BUCKET_URL, 503, 'down', Message(), None),
            ]
        )
        monkeypatch.setattr(publisher_module, 'urlopen', endpoint)

        with pytest.raises(
            ActivityWatchStatusError,
            match='ActivityWatch bucket metadata request returned status 503',
        ):
            create_bucket(TEST_BUCKET_TARGET, host_suffix='host-a')


# === Closed profile targets ===


class TestClosedProfileTargets:
    """Prove that each profile reaches only its own bucket identifier."""

    def test_test_profile_targets_the_test_identifier(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        endpoint = FakeActivityWatch(reads=[None, TEST_METADATA])
        monkeypatch.setattr(publisher_module, 'urlopen', endpoint)

        create_bucket(TEST_BUCKET_TARGET, host_suffix='host-a')

        urls = {call.full_url for call in endpoint.calls}
        assert urls == {TEST_BUCKET_URL}
        post = select_posts(endpoint.calls)[0]
        assert isinstance(post.data, bytes)
        assert json.loads(post.data.decode('utf-8')) == {
            'client': 'aw-watcher-orca-test',
            'type': 'currentwindow',
            'hostname': 'host-a',
        }

    def test_production_profile_targets_the_production_identifier(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        endpoint = FakeActivityWatch(reads=[None, PRODUCTION_METADATA])
        monkeypatch.setattr(publisher_module, 'urlopen', endpoint)

        create_bucket(PRODUCTION_BUCKET_TARGET, host_suffix='host-a')

        urls = {call.full_url for call in endpoint.calls}
        assert urls == {PRODUCTION_BUCKET_URL}
        post = select_posts(endpoint.calls)[0]
        assert isinstance(post.data, bytes)
        assert json.loads(post.data.decode('utf-8')) == {
            'client': 'aw-watcher-orca',
            'type': 'currentwindow',
            'hostname': 'host-a',
        }

    def test_create_test_bucket_never_reaches_production_identifier(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        endpoint = FakeActivityWatch(reads=[None, TEST_METADATA])
        monkeypatch.setattr(publisher_module, 'urlopen', endpoint)

        create_test_bucket(host_suffix='host-a')

        assert endpoint.methods() == ['GET', 'POST', 'GET']
        assert [call.full_url for call in endpoint.calls] == [
            TEST_BUCKET_URL,
            TEST_BUCKET_URL,
            TEST_BUCKET_URL,
        ]
        assert PRODUCTION_BUCKET_URL not in {
            call.full_url for call in endpoint.calls
        }

    def test_create_bucket_rejects_a_profile_outside_the_registry(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        endpoint = FakeActivityWatch(reads=[None, TEST_METADATA])
        monkeypatch.setattr(publisher_module, 'urlopen', endpoint)

        with pytest.raises(
            UnknownBucketTargetError,
            match='Unknown bucket target profile',
        ):
            create_bucket(FOREIGN_PROFILE, host_suffix='host-a')

        assert endpoint.calls == []

    def test_create_bucket_accepts_an_equal_copy_of_the_test_profile(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        endpoint = FakeActivityWatch(reads=[None, TEST_METADATA])
        monkeypatch.setattr(publisher_module, 'urlopen', endpoint)
        copy = dataclasses.replace(TEST_BUCKET_TARGET)

        create_bucket(copy, host_suffix='host-a')

        assert copy is not TEST_BUCKET_TARGET
        assert endpoint.methods() == ['GET', 'POST', 'GET']
        assert {call.full_url for call in endpoint.calls} == {TEST_BUCKET_URL}
