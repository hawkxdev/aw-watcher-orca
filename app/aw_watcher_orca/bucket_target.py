"""Define closed ActivityWatch bucket target profiles."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from aw_watcher_orca.errors import OrcaCoreError

# === Constants ===


BUCKET_TARGET_TYPE: Final = 'currentwindow'
TEST_BUCKET_MODE: Final = 'test'
PRODUCTION_BUCKET_MODE: Final = 'production'


# === Errors ===


class UnknownBucketModeError(OrcaCoreError):
    """Represent an unknown bucket target mode."""


class BucketMetadataMismatchError(OrcaCoreError):
    """Represent bucket metadata that does not match the target profile."""


class UnknownBucketTargetError(OrcaCoreError):
    """Represent a bucket target profile outside the closed registry."""


# === Profile ===


@dataclass(frozen=True, slots=True)
class BucketTargetProfile:
    """Represent one closed bucket target profile."""

    mode: str
    prefix: str
    client: str
    bucket_type: str


TEST_BUCKET_TARGET: Final = BucketTargetProfile(
    mode=TEST_BUCKET_MODE,
    prefix='aw-watcher-orca-test',
    client='aw-watcher-orca-test',
    bucket_type=BUCKET_TARGET_TYPE,
)
PRODUCTION_BUCKET_TARGET: Final = BucketTargetProfile(
    mode=PRODUCTION_BUCKET_MODE,
    prefix='aw-watcher-orca',
    client='aw-watcher-orca',
    bucket_type=BUCKET_TARGET_TYPE,
)
BUCKET_TARGET_PROFILES: Final[Mapping[str, BucketTargetProfile]] = (
    MappingProxyType(
        {
            TEST_BUCKET_TARGET.mode: TEST_BUCKET_TARGET,
            PRODUCTION_BUCKET_TARGET.mode: PRODUCTION_BUCKET_TARGET,
        }
    )
)


# === Resolution ===


def resolve_bucket_target(mode: str) -> BucketTargetProfile:
    """Resolve one closed bucket target profile by mode."""
    profile = BUCKET_TARGET_PROFILES.get(mode)
    if profile is None:
        raise UnknownBucketModeError('Unknown bucket target mode')
    return profile


def require_registered_target(profile: BucketTargetProfile) -> None:
    """Require one profile to belong to the closed target registry."""
    if profile not in BUCKET_TARGET_PROFILES.values():
        raise UnknownBucketTargetError('Unknown bucket target profile')


def build_bucket_id(profile: BucketTargetProfile, host_suffix: str) -> str:
    """Build the bucket identifier of one registered profile."""
    require_registered_target(profile)
    return f'{profile.prefix}_{host_suffix}'
