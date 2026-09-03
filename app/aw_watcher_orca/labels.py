"""Build public Orca project labels."""

import unicodedata
from pathlib import PurePosixPath
from typing import Final, Literal

from aw_watcher_orca.errors import MalformedStateError
from aw_watcher_orca.models import (
    ProjectAttribution,
    RepositoryEntry,
    WorktreeEntry,
)

# === Constants ===


LABEL_SEPARATOR = ' / '
SCHEMA_SOURCE_PREFIX = 'orca-data-v'
PUBLIC_NAME_FORM: Final[Literal['NFC']] = 'NFC'


# === Label construction ===


def is_absolute_public_name(value: str) -> bool:
    """Detect one absolute public name."""
    return PurePosixPath(value).is_absolute()


def normalize_public_name(value: str) -> str:
    """Normalize one public name to NFC."""
    return unicodedata.normalize(PUBLIC_NAME_FORM, value)


def build_worktree_name(worktree: WorktreeEntry) -> str:
    """Build one public worktree name."""
    display_name = worktree.display_name
    if display_name is not None and not is_absolute_public_name(display_name):
        return normalize_public_name(display_name)
    fallback_name = PurePosixPath(worktree.path).name
    if not fallback_name:
        raise MalformedStateError('Unusable Orca worktree path')
    return normalize_public_name(fallback_name)


def build_attribution(
    worktree: WorktreeEntry,
    repository: RepositoryEntry,
    schema_version: int,
) -> ProjectAttribution:
    """Build one public project attribution."""
    worktree_name = build_worktree_name(worktree)
    repo_name = normalize_public_name(repository.display_name)
    is_main = worktree.path == repository.path
    label = (
        repo_name
        if is_main
        else f'{repo_name}{LABEL_SEPARATOR}{worktree_name}'
    )
    return ProjectAttribution(
        repo=repo_name,
        worktree=worktree_name,
        label=label,
        is_main_worktree=is_main,
        schema_source=f'{SCHEMA_SOURCE_PREFIX}{schema_version}',
    )
