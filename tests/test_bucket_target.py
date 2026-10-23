"""Test closed ActivityWatch bucket target profiles."""

import dataclasses

import pytest

from aw_watcher_orca.bucket_target import (
    BUCKET_TARGET_PROFILES,
    BUCKET_TARGET_TYPE,
    PRODUCTION_BUCKET_MODE,
    PRODUCTION_BUCKET_TARGET,
    TEST_BUCKET_MODE,
    TEST_BUCKET_TARGET,
    BucketMetadataMismatchError,
    BucketTargetProfile,
    ConfirmedBucketTarget,
    UnknownBucketModeError,
    UnknownBucketTargetError,
    build_bucket_id,
    confirm_bucket_target,
    metadata_matches_target,
    resolve_bucket_target,
)
from aw_watcher_orca.errors import OrcaCoreError

# === Constants ===


UNKNOWN_MODES = [
    '',
    'prod',
    'Production',
    'PRODUCTION',
    'test ',
    'staging',
    'aw-watcher-orca',
]
FOREIGN_PROFILE = BucketTargetProfile(
    mode='x',
    prefix='third-prefix',
    client='third-client',
    bucket_type=BUCKET_TARGET_TYPE,
)


# === Closed profile identity ===


def test_test_profile_has_exact_identity() -> None:
    assert TEST_BUCKET_TARGET.mode == 'test'
    assert TEST_BUCKET_TARGET.prefix == 'aw-watcher-orca-test'
    assert TEST_BUCKET_TARGET.client == 'aw-watcher-orca-test'
    assert TEST_BUCKET_TARGET.bucket_type == 'currentwindow'


def test_production_profile_has_exact_identity() -> None:
    assert PRODUCTION_BUCKET_TARGET.mode == 'production'
    assert PRODUCTION_BUCKET_TARGET.prefix == 'aw-watcher-orca'
    assert PRODUCTION_BUCKET_TARGET.client == 'aw-watcher-orca'
    assert PRODUCTION_BUCKET_TARGET.bucket_type == 'currentwindow'


def test_production_profile_is_not_the_test_profile() -> None:
    assert PRODUCTION_BUCKET_TARGET.prefix != TEST_BUCKET_TARGET.prefix
    assert PRODUCTION_BUCKET_TARGET.client != TEST_BUCKET_TARGET.client
    assert not PRODUCTION_BUCKET_TARGET.prefix.endswith('-test')
    assert not PRODUCTION_BUCKET_TARGET.client.endswith('-test')


def test_both_profiles_share_the_currentwindow_type() -> None:
    assert BUCKET_TARGET_TYPE == 'currentwindow'
    assert TEST_BUCKET_TARGET.bucket_type == 'currentwindow'
    assert PRODUCTION_BUCKET_TARGET.bucket_type == 'currentwindow'


def test_profile_fields_are_immutable() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        TEST_BUCKET_TARGET.prefix = 'aw-watcher-orca'  # type: ignore[misc]


# === Mode resolution ===


def test_resolve_bucket_target_returns_the_test_profile() -> None:
    assert resolve_bucket_target(TEST_BUCKET_MODE) is TEST_BUCKET_TARGET
    assert resolve_bucket_target('test') is TEST_BUCKET_TARGET


def test_resolve_bucket_target_returns_the_production_profile() -> None:
    resolved = resolve_bucket_target(PRODUCTION_BUCKET_MODE)

    assert resolved is PRODUCTION_BUCKET_TARGET
    assert resolve_bucket_target('production') is PRODUCTION_BUCKET_TARGET


@pytest.mark.parametrize('mode', UNKNOWN_MODES)
def test_resolve_bucket_target_rejects_unknown_mode(mode: str) -> None:
    with pytest.raises(
        UnknownBucketModeError,
        match='Unknown bucket target mode',
    ):
        resolve_bucket_target(mode)


def test_bucket_target_errors_are_orca_core_errors() -> None:
    assert issubclass(UnknownBucketModeError, OrcaCoreError)
    assert issubclass(BucketMetadataMismatchError, OrcaCoreError)
    assert issubclass(UnknownBucketTargetError, OrcaCoreError)


# === Registry immutability ===


def test_registry_rejects_assignment_of_a_third_profile() -> None:
    with pytest.raises(TypeError):
        BUCKET_TARGET_PROFILES['staging'] = FOREIGN_PROFILE  # type: ignore[index]

    assert set(BUCKET_TARGET_PROFILES) == {'test', 'production'}
    with pytest.raises(UnknownBucketModeError):
        resolve_bucket_target('staging')


def test_registry_rejects_deletion_of_a_registered_profile() -> None:
    with pytest.raises(TypeError):
        del BUCKET_TARGET_PROFILES['production']  # type: ignore[attr-defined]

    assert resolve_bucket_target('production') is PRODUCTION_BUCKET_TARGET


# === Identifier construction ===


def test_build_bucket_id_builds_the_test_identifier() -> None:
    bucket_id = build_bucket_id(TEST_BUCKET_TARGET, '192.0.2.45')

    assert bucket_id == 'aw-watcher-orca-test_192.0.2.45'


def test_build_bucket_id_builds_the_production_identifier() -> None:
    bucket_id = build_bucket_id(PRODUCTION_BUCKET_TARGET, '192.0.2.45')

    assert bucket_id == 'aw-watcher-orca_192.0.2.45'


def test_only_two_identifiers_are_reachable() -> None:
    reachable = {
        build_bucket_id(profile, 'host-a')
        for profile in BUCKET_TARGET_PROFILES.values()
    }

    assert reachable == {
        'aw-watcher-orca-test_host-a',
        'aw-watcher-orca_host-a',
    }
    assert set(BUCKET_TARGET_PROFILES) == {'test', 'production'}


# === Registry membership ===


def test_build_bucket_id_rejects_a_profile_outside_the_registry() -> None:
    with pytest.raises(
        UnknownBucketTargetError,
        match='Unknown bucket target profile',
    ):
        build_bucket_id(FOREIGN_PROFILE, 'host-a')


def test_build_bucket_id_accepts_a_resolved_profile() -> None:
    resolved = resolve_bucket_target(PRODUCTION_BUCKET_MODE)

    assert build_bucket_id(resolved, 'host-a') == 'aw-watcher-orca_host-a'


def test_build_bucket_id_accepts_an_equal_copy_of_a_registered_profile() -> (
    None
):
    copy = dataclasses.replace(TEST_BUCKET_TARGET)

    assert copy is not TEST_BUCKET_TARGET
    assert copy == TEST_BUCKET_TARGET
    assert build_bucket_id(copy, 'host-a') == 'aw-watcher-orca-test_host-a'


# === Confirmed target ===


def matching_metadata(
    profile: BucketTargetProfile,
    host_suffix: str,
) -> dict[str, object]:
    """Build metadata that matches one profile exactly."""
    return {
        'client': profile.client,
        'type': profile.bucket_type,
        'hostname': host_suffix,
    }


def test_confirm_bucket_target_returns_the_registered_identifier() -> None:
    target = confirm_bucket_target(
        PRODUCTION_BUCKET_TARGET,
        'host-a',
        matching_metadata(PRODUCTION_BUCKET_TARGET, 'host-a'),
    )

    assert target.bucket_id == 'aw-watcher-orca_host-a'
    assert target.profile is PRODUCTION_BUCKET_TARGET
    assert target.host_suffix == 'host-a'


def test_confirm_bucket_target_rejects_absent_metadata() -> None:
    with pytest.raises(BucketMetadataMismatchError):
        confirm_bucket_target(TEST_BUCKET_TARGET, 'host-a', None)


@pytest.mark.parametrize(
    ('key', 'value'),
    [
        ('client', 'aw-watcher-window'),
        ('type', 'afkstatus'),
        ('hostname', 'host-b'),
        ('client', 42),
    ],
)
def test_confirm_bucket_target_rejects_mismatched_metadata(
    key: str,
    value: object,
) -> None:
    metadata = matching_metadata(TEST_BUCKET_TARGET, 'host-a')
    metadata[key] = value

    with pytest.raises(BucketMetadataMismatchError):
        confirm_bucket_target(TEST_BUCKET_TARGET, 'host-a', metadata)


def test_confirm_bucket_target_rejects_a_missing_metadata_key() -> None:
    metadata = matching_metadata(TEST_BUCKET_TARGET, 'host-a')
    del metadata['hostname']

    with pytest.raises(BucketMetadataMismatchError):
        confirm_bucket_target(TEST_BUCKET_TARGET, 'host-a', metadata)


def test_confirm_bucket_target_rejects_an_outside_profile() -> None:
    outside = BucketTargetProfile(
        mode='shadow',
        prefix='aw-watcher-shadow',
        client='aw-watcher-shadow',
        bucket_type='currentwindow',
    )

    with pytest.raises(UnknownBucketTargetError):
        confirm_bucket_target(
            outside,
            'host-a',
            matching_metadata(outside, 'host-a'),
        )


def test_confirmed_target_rejects_an_identifier_of_another_profile() -> None:
    with pytest.raises(UnknownBucketTargetError):
        ConfirmedBucketTarget(
            profile=TEST_BUCKET_TARGET,
            host_suffix='host-a',
            bucket_id='aw-watcher-orca_host-a',
        )


def test_confirmed_target_rejects_an_identifier_of_another_host() -> None:
    with pytest.raises(UnknownBucketTargetError):
        ConfirmedBucketTarget(
            profile=TEST_BUCKET_TARGET,
            host_suffix='host-a',
            bucket_id='aw-watcher-orca-test_host-b',
        )


def test_metadata_matches_target_reads_only_text_fields() -> None:
    metadata = matching_metadata(TEST_BUCKET_TARGET, 'host-a')
    metadata['client'] = ['aw-watcher-orca-test']

    assert not metadata_matches_target(TEST_BUCKET_TARGET, 'host-a', metadata)
