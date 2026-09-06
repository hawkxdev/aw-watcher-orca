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
    UnknownBucketModeError,
    UnknownBucketTargetError,
    build_bucket_id,
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
