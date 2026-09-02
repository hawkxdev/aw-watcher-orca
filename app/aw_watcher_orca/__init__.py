"""Provide Orca attribution and discovery APIs."""

from aw_watcher_orca.activitywatch import (
    DEFAULT_BUCKET_FRESHNESS_WINDOW,
    ActivityWatchBucketPair,
    select_fresh_bucket_pair,
)
from aw_watcher_orca.activitywatch_reader import (
    ACTIVITYWATCH_BUCKETS_URL,
    DEFAULT_ACTIVITYWATCH_TIMEOUT_SECONDS,
    read_activitywatch_buckets,
)
from aw_watcher_orca.errors import (
    ActivityWatchConnectionError,
    ActivityWatchDiscoveryError,
    ActivityWatchStatusError,
    AmbiguousMappingError,
    DuplicateRepositoryError,
    MalformedActivityWatchPayloadError,
    MalformedStateError,
    MultipleFreshBucketPairsError,
    NoFreshBucketPairError,
    OrcaCoreError,
    OrcaStateError,
    UnknownRepositoryError,
    UnknownWorktreeError,
    UnsupportedSchemaError,
)
from aw_watcher_orca.labels import build_attribution, build_worktree_name
from aw_watcher_orca.models import (
    ProjectAttribution,
    ProjectRegistry,
    RepositoryEntry,
    WorktreeEntry,
)
from aw_watcher_orca.registry import load_registry
from aw_watcher_orca.resolver import resolve_active_project
from aw_watcher_orca.stability import (
    REQUIRED_STABLE_POLLS,
    ActiveProjectStabilizer,
)

__all__ = [
    'ACTIVITYWATCH_BUCKETS_URL',
    'DEFAULT_ACTIVITYWATCH_TIMEOUT_SECONDS',
    'DEFAULT_BUCKET_FRESHNESS_WINDOW',
    'REQUIRED_STABLE_POLLS',
    'ActiveProjectStabilizer',
    'ActivityWatchBucketPair',
    'ActivityWatchConnectionError',
    'ActivityWatchDiscoveryError',
    'ActivityWatchStatusError',
    'AmbiguousMappingError',
    'DuplicateRepositoryError',
    'MalformedStateError',
    'MalformedActivityWatchPayloadError',
    'MultipleFreshBucketPairsError',
    'NoFreshBucketPairError',
    'OrcaCoreError',
    'OrcaStateError',
    'ProjectAttribution',
    'ProjectRegistry',
    'RepositoryEntry',
    'UnknownRepositoryError',
    'UnknownWorktreeError',
    'UnsupportedSchemaError',
    'WorktreeEntry',
    'build_attribution',
    'build_worktree_name',
    'load_registry',
    'read_activitywatch_buckets',
    'resolve_active_project',
    'select_fresh_bucket_pair',
]
