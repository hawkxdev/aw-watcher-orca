"""Define pure Orca core failures."""


class OrcaCoreError(Exception):
    """Represent pure Orca core failures."""


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
