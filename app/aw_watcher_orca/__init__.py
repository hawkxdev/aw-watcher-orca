"""Provide Orca attribution and discovery APIs."""

from aw_watcher_orca.activitywatch import (
    DEFAULT_BUCKET_FRESHNESS_WINDOW,
    DEFAULT_EXCLUDED_CLIENTS,
    ActivityWatchBucketPair,
    select_fresh_bucket_pair,
)
from aw_watcher_orca.activitywatch_reader import (
    ACTIVITYWATCH_BUCKETS_URL,
    DEFAULT_ACTIVITYWATCH_TIMEOUT_SECONDS,
    read_activitywatch_buckets,
    read_last_bucket_event,
)
from aw_watcher_orca.cli_resolver import (
    CLI_SCHEMA_SOURCE,
    DEFAULT_ORCA_BINARY_PATH,
    DEFAULT_ORCA_CLI_TIMEOUT_SECONDS,
    parse_active_worktree,
    run_orca_worktree_ps,
)
from aw_watcher_orca.errors import (
    ActivityWatchConnectionError,
    ActivityWatchDiscoveryError,
    ActivityWatchStatusError,
    AmbiguousMappingError,
    DuplicateRepositoryError,
    MalformedActivityWatchPayloadError,
    MalformedCliResultError,
    MalformedStateError,
    MalformedWorktreeDataError,
    MultipleActiveWorktreesError,
    MultipleFreshBucketPairsError,
    NoActiveWorktreeError,
    NoFreshBucketPairError,
    OrcaCliError,
    OrcaCliExecutionError,
    OrcaCliPayloadError,
    OrcaCliResponseError,
    OrcaCliStatusError,
    OrcaCliTimeoutError,
    OrcaCoreError,
    OrcaStateError,
    ProfileDiscoveryError,
    UnknownRepositoryError,
    UnknownWorktreeError,
    UnsupportedSchemaError,
)
from aw_watcher_orca.foreground import (
    DEFAULT_MAX_FOREGROUND_EVENT_AGE,
    ORCA_APP_NAME,
    is_orca_foreground,
)
from aw_watcher_orca.labels import build_attribution, build_worktree_name
from aw_watcher_orca.models import (
    ProjectAttribution,
    ProjectRegistry,
    RepositoryEntry,
    WorktreeEntry,
)
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
from aw_watcher_orca.registry import load_registry
from aw_watcher_orca.resolver import resolve_active_project
from aw_watcher_orca.stability import (
    REQUIRED_STABLE_POLLS,
    ActiveProjectStabilizer,
)
from aw_watcher_orca.trigger import (
    SUPPORTED_ORCA_SCHEMA_VERSION,
    discover_profile_state_file,
    read_active_worktree_trigger,
)


def __getattr__(name: str) -> object:
    """Provide lazy attribute access."""
    if name == 'run_watcher_loop':
        from aw_watcher_orca.watcher import run_watcher_loop

        return run_watcher_loop
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


__all__ = [
    'ACTIVITYWATCH_BUCKETS_URL',
    'CLI_SCHEMA_SOURCE',
    'DEFAULT_ACTIVITYWATCH_TIMEOUT_SECONDS',
    'DEFAULT_BUCKET_FRESHNESS_WINDOW',
    'DEFAULT_EXCLUDED_CLIENTS',
    'DEFAULT_MAX_FOREGROUND_EVENT_AGE',
    'DEFAULT_ORCA_BINARY_PATH',
    'DEFAULT_ORCA_CLI_TIMEOUT_SECONDS',
    'DEFAULT_POLL_INTERVAL_SECONDS',
    'DEFAULT_PULSE_TIME_SECONDS',
    'ORCA_APP_NAME',
    'REQUIRED_STABLE_POLLS',
    'TEST_BUCKET_CLIENT',
    'TEST_BUCKET_PREFIX',
    'TEST_BUCKET_TYPE',
    'ActiveProjectStabilizer',
    'ActivityWatchBucketPair',
    'ActivityWatchConnectionError',
    'ActivityWatchDiscoveryError',
    'ActivityWatchStatusError',
    'AmbiguousMappingError',
    'DuplicateRepositoryError',
    'MalformedActivityWatchPayloadError',
    'MalformedCliResultError',
    'MalformedStateError',
    'MalformedWorktreeDataError',
    'MultipleActiveWorktreesError',
    'MultipleFreshBucketPairsError',
    'NoActiveWorktreeError',
    'NoFreshBucketPairError',
    'OrcaCliError',
    'OrcaCliExecutionError',
    'OrcaCliPayloadError',
    'OrcaCliResponseError',
    'OrcaCliStatusError',
    'OrcaCliTimeoutError',
    'OrcaCoreError',
    'OrcaStateError',
    'ProfileDiscoveryError',
    'ProjectAttribution',
    'ProjectRegistry',
    'RepositoryEntry',
    'SUPPORTED_ORCA_SCHEMA_VERSION',
    'UnknownRepositoryError',
    'UnknownWorktreeError',
    'UnsupportedSchemaError',
    'WorktreeEntry',
    'build_active_event_data',
    'build_attribution',
    'build_heartbeat_payload',
    'build_neutral_event_data',
    'build_test_bucket_id',
    'build_worktree_name',
    'create_test_bucket',
    'discover_profile_state_file',
    'generate_session_token',
    'is_orca_foreground',
    'load_registry',
    'parse_active_worktree',
    'read_activitywatch_buckets',
    'read_active_worktree_trigger',
    'read_last_bucket_event',
    'resolve_active_project',
    'run_orca_worktree_ps',
    'run_watcher_loop',
    'select_fresh_bucket_pair',
    'send_heartbeat',
]
