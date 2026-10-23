"""Resolve the active Orca project attribution."""

from aw_watcher_orca.errors import (
    UnknownRepositoryError,
    UnknownWorktreeError,
)
from aw_watcher_orca.labels import build_attribution
from aw_watcher_orca.models import ProjectAttribution, ProjectRegistry


def resolve_active_project(registry: ProjectRegistry) -> ProjectAttribution:
    """Resolve one public active project attribution."""
    worktree = registry.worktrees.get(registry.active_worktree_id)
    if worktree is None:
        raise UnknownWorktreeError('Unknown active Orca worktree')
    repository = registry.repositories.get(worktree.repo_id)
    if repository is None:
        raise UnknownRepositoryError('Unknown Orca repository id')
    return build_attribution(worktree, repository, registry.schema_version)
