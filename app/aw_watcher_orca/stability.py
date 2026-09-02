"""Stabilize observed Orca project attributions."""

from aw_watcher_orca.models import ProjectAttribution

# === Constants ===


REQUIRED_STABLE_POLLS = 2


# === Internal observation ===


Observation = tuple[str, ProjectAttribution]


# === Stabilizer ===


class ActiveProjectStabilizer:
    """Emit attributions confirmed by consecutive polls."""

    def __init__(self, required_polls: int = REQUIRED_STABLE_POLLS) -> None:
        """Initialize one stabilizer."""
        if (
            type(required_polls) is not int
            or required_polls < REQUIRED_STABLE_POLLS
        ):
            raise ValueError('required_polls must be at least 2')
        self._required_polls = required_polls
        self._pending: Observation | None = None
        self._pending_polls = 0
        self._emitted: Observation | None = None

    def observe(
        self,
        worktree_id: str,
        candidate: ProjectAttribution,
    ) -> ProjectAttribution | None:
        """Emit one newly stabilized attribution."""
        observation = (worktree_id, candidate)
        if observation == self._pending:
            self._pending_polls = min(
                self._pending_polls + 1,
                self._required_polls,
            )
        else:
            self._pending = observation
            self._pending_polls = 1
        if self._pending_polls < self._required_polls:
            return None
        if observation == self._emitted:
            return None
        self._emitted = observation
        return candidate
