"""Define pure Orca core failures."""

# === Base ===


class OrcaCoreError(Exception):
    """Represent pure Orca core failures."""


# === Orca state ===


class OrcaStateError(OrcaCoreError):
    """Represent invalid persisted Orca state."""


class UnsupportedSchemaError(OrcaStateError):
    """Represent unsupported persisted schema versions."""


class MalformedStateError(OrcaStateError):
    """Represent structurally invalid persisted state."""


class DuplicateRepositoryError(OrcaStateError):
    """Represent duplicate repository identifiers."""


class UnknownRepositoryError(OrcaStateError):
    """Represent worktrees bound to unknown repositories."""


class UnknownWorktreeError(OrcaStateError):
    """Represent an unregistered active worktree."""


class AmbiguousMappingError(OrcaStateError):
    """Represent ambiguous registry mappings."""


class ProfileDiscoveryError(OrcaStateError):
    """Represent profile discovery failures."""


# === Orca CLI ===


class OrcaCliError(OrcaCoreError):
    """Represent Orca CLI failures."""


class OrcaCliExecutionError(OrcaCliError):
    """Represent CLI execution failures."""


class OrcaCliTimeoutError(OrcaCliExecutionError):
    """Represent CLI timeout failures."""


class OrcaCliStatusError(OrcaCliExecutionError):
    """Represent non-zero CLI statuses."""


class OrcaCliPayloadError(OrcaCliError):
    """Represent unparseable CLI output."""


class OrcaCliResponseError(OrcaCliError):
    """Represent unfulfilled CLI responses."""


class MalformedCliResultError(OrcaCliError):
    """Represent unexpected result structures."""


class NoActiveWorktreeError(OrcaCliError):
    """Represent zero active worktrees."""


class MultipleActiveWorktreesError(OrcaCliError):
    """Represent multiple active worktrees."""


class MalformedWorktreeDataError(OrcaCliError):
    """Represent invalid worktree records."""


# === ActivityWatch discovery ===


class ActivityWatchDiscoveryError(OrcaCoreError):
    """Represent ActivityWatch discovery failures."""


class NoFreshBucketPairError(ActivityWatchDiscoveryError):
    """Represent absent fresh bucket pairs."""


class MultipleFreshBucketPairsError(ActivityWatchDiscoveryError):
    """Represent ambiguous fresh bucket pairs."""


class ActivityWatchConnectionError(ActivityWatchDiscoveryError):
    """Represent ActivityWatch connection failures."""


class ActivityWatchStatusError(ActivityWatchDiscoveryError):
    """Represent unsuccessful ActivityWatch statuses."""


class MalformedActivityWatchPayloadError(ActivityWatchDiscoveryError):
    """Represent malformed ActivityWatch responses."""
