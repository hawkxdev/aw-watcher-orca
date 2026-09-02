"""Validate persisted Orca registry state."""

from collections.abc import Mapping

from aw_watcher_orca.errors import (
    AmbiguousMappingError,
    DuplicateRepositoryError,
    MalformedStateError,
    UnknownRepositoryError,
    UnknownWorktreeError,
    UnsupportedSchemaError,
)
from aw_watcher_orca.labels import (
    build_attribution,
    is_absolute_public_name,
)
from aw_watcher_orca.models import (
    ProjectRegistry,
    RepositoryEntry,
    WorktreeEntry,
)

# === Constants ===


SUPPORTED_SCHEMA_VERSION = 1
IDENTITY_SEPARATOR = '::'
IDENTITY_PREFIX = 'worktree:'


# === Identity handling ===


def normalize_worktree_identity(raw_identity: str) -> str:
    """Normalize one persisted worktree identity."""
    return raw_identity.removeprefix(IDENTITY_PREFIX)


def split_worktree_identity(identity: str) -> tuple[str, str]:
    """Split one worktree identity into repository and path."""
    repo_id, separator, worktree_path = identity.partition(IDENTITY_SEPARATOR)
    if not separator or not repo_id or not worktree_path:
        raise MalformedStateError('Invalid Orca worktree identity')
    if repo_id != repo_id.strip() or worktree_path != worktree_path.strip():
        raise MalformedStateError('Invalid Orca worktree identity')
    return repo_id, worktree_path


# === Field helpers ===


def _require_text(
    source: Mapping[str, object],
    field_name: str,
    message: str,
) -> str:
    """Require one non-blank text field."""
    value = source.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise MalformedStateError(message)
    return value


def _require_canonical_text(
    source: Mapping[str, object],
    field_name: str,
    message: str,
) -> str:
    """Require one non-blank text field free of edge whitespace."""
    value = _require_text(source, field_name, message)
    if value != value.strip():
        raise MalformedStateError(message)
    return value


def _require_repository_name(raw_repo: Mapping[str, object]) -> str:
    """Require one usable public repository display name."""
    message = 'Unusable Orca repository name'
    display_name = _require_text(raw_repo, 'displayName', message).strip()
    if is_absolute_public_name(display_name):
        raise MalformedStateError(message)
    return display_name


def _optional_display_name(raw_meta: Mapping[str, object]) -> str | None:
    """Read one optional worktree display name."""
    value = raw_meta.get('displayName')
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


# === Section loading ===


def _require_schema_version(payload: Mapping[str, object]) -> int:
    """Require one supported schema version."""
    schema_version = payload.get('schemaVersion')
    if (
        type(schema_version) is not int
        or schema_version != SUPPORTED_SCHEMA_VERSION
    ):
        raise UnsupportedSchemaError(
            f'Unsupported Orca schema version: {schema_version}'
        )
    return schema_version


def _require_active_worktree_id(payload: Mapping[str, object]) -> str:
    """Require one usable active worktree identity."""
    workspace_session = payload.get('workspaceSession')
    if not isinstance(workspace_session, dict):
        raise MalformedStateError('Missing workspaceSession object')
    raw_identity = workspace_session.get('activeWorktreeId')
    if not isinstance(raw_identity, str) or not raw_identity.strip():
        raise MalformedStateError('Missing workspaceSession.activeWorktreeId')
    identity = normalize_worktree_identity(raw_identity)
    split_worktree_identity(identity)
    return identity


def _load_repositories(
    payload: Mapping[str, object],
) -> dict[str, RepositoryEntry]:
    """Load every registered repository."""
    raw_repos = payload.get('repos')
    if not isinstance(raw_repos, list):
        raise MalformedStateError('Missing repos list')
    repositories: dict[str, RepositoryEntry] = {}
    for raw_repo in raw_repos:
        if not isinstance(raw_repo, dict):
            raise MalformedStateError('Invalid Orca repository entry')
        repo_id = _require_canonical_text(
            raw_repo,
            'id',
            'Invalid Orca repository id',
        )
        display_name = _require_repository_name(raw_repo)
        repo_path = _require_canonical_text(
            raw_repo,
            'path',
            'Invalid Orca repository path',
        )
        if repo_id in repositories:
            raise DuplicateRepositoryError('Duplicate Orca repository id')
        repositories[repo_id] = RepositoryEntry(
            repo_id=repo_id,
            display_name=display_name,
            path=repo_path,
        )
    return repositories


def _load_worktrees(
    payload: Mapping[str, object],
    repositories: Mapping[str, RepositoryEntry],
) -> dict[str, WorktreeEntry]:
    """Load every registered worktree."""
    raw_worktrees = payload.get('worktreeMeta')
    if not isinstance(raw_worktrees, dict):
        raise MalformedStateError('Missing worktreeMeta object')
    worktrees: dict[str, WorktreeEntry] = {}
    for raw_key, raw_meta in raw_worktrees.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise MalformedStateError('Invalid Orca worktree identity')
        if not isinstance(raw_meta, dict):
            raise MalformedStateError('Invalid Orca worktree metadata')
        identity = normalize_worktree_identity(raw_key)
        repo_id, worktree_path = split_worktree_identity(identity)
        if repo_id not in repositories:
            raise UnknownRepositoryError('Unknown Orca repository id')
        if identity in worktrees:
            raise AmbiguousMappingError('Ambiguous Orca worktree identity')
        worktrees[identity] = WorktreeEntry(
            worktree_id=identity,
            repo_id=repo_id,
            path=worktree_path,
            display_name=_optional_display_name(raw_meta),
        )
    return worktrees


def _require_unique_labels(registry: ProjectRegistry) -> None:
    """Require unique labels across the registered set."""
    seen: set[str] = set()
    for worktree in registry.worktrees.values():
        attribution = build_attribution(
            worktree,
            registry.repositories[worktree.repo_id],
            registry.schema_version,
        )
        if attribution.label in seen:
            raise AmbiguousMappingError('Ambiguous Orca project label')
        seen.add(attribution.label)


# === Registry loading ===


def load_registry(payload: object) -> ProjectRegistry:
    """Load one validated Orca registry."""
    if not isinstance(payload, dict):
        raise MalformedStateError('Orca state root must be an object')
    schema_version = _require_schema_version(payload)
    active_worktree_id = _require_active_worktree_id(payload)
    repositories = _load_repositories(payload)
    worktrees = _load_worktrees(payload, repositories)
    if active_worktree_id not in worktrees:
        raise UnknownWorktreeError('Unknown active Orca worktree')
    registry = ProjectRegistry(
        schema_version=schema_version,
        active_worktree_id=active_worktree_id,
        repositories=repositories,
        worktrees=worktrees,
    )
    _require_unique_labels(registry)
    return registry
