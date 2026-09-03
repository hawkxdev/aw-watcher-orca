"""Hold pure Orca attribution models."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

# === Registry entries ===


@dataclass(frozen=True, slots=True)
class RepositoryEntry:
    """Hold one registered repository."""

    repo_id: str
    display_name: str
    path: str


@dataclass(frozen=True, slots=True)
class WorktreeEntry:
    """Hold one registered worktree."""

    worktree_id: str
    repo_id: str
    path: str
    display_name: str | None


@dataclass(frozen=True, slots=True)
class ProjectRegistry:
    """Hold one validated Orca registry."""

    schema_version: int
    active_worktree_id: str
    repositories: Mapping[str, RepositoryEntry]
    worktrees: Mapping[str, WorktreeEntry]

    def __post_init__(self) -> None:
        """Freeze caller owned maps."""
        object.__setattr__(
            self,
            'repositories',
            MappingProxyType(dict(self.repositories)),
        )
        object.__setattr__(
            self,
            'worktrees',
            MappingProxyType(dict(self.worktrees)),
        )


# === Public attribution ===


@dataclass(frozen=True, slots=True)
class ProjectAttribution:
    """Hold one public project attribution."""

    repo: str
    worktree: str
    label: str
    is_main_worktree: bool
    schema_source: str

    def to_public_dict(self) -> dict[str, object]:
        """Build one public attribution mapping."""
        return {
            'repo': self.repo,
            'worktree': self.worktree,
            'label': self.label,
            'is_main_worktree': self.is_main_worktree,
            'schema_source': self.schema_source,
        }
