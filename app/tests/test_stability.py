"""Test Orca attribution stabilization."""

import json

import pytest

from aw_watcher_orca.models import ProjectAttribution
from aw_watcher_orca.stability import (
    REQUIRED_STABLE_POLLS,
    ActiveProjectStabilizer,
)
from synthetic_state import (
    CHILD_WORKTREE_PATH,
    MAIN_REPO_ID,
    SECOND_REPO_ID,
    build_worktree_key,
)

# === Constants ===


FIRST_WORKTREE_ID = build_worktree_key(MAIN_REPO_ID, CHILD_WORKTREE_PATH)
SECOND_WORKTREE_ID = build_worktree_key(
    SECOND_REPO_ID,
    '/sandbox/worktrees/beta/topic-a',
)


# === Fixtures ===


def _attribution(label: str) -> ProjectAttribution:
    """Build one distinct attribution."""
    return ProjectAttribution(
        repo='alpha',
        worktree=label,
        label=f'alpha / {label}',
        is_main_worktree=False,
        schema_source='orca-data-v1',
    )


# === Stability rule ===


def test_stabilizer_withholds_first_observation() -> None:
    """Withhold a single observation."""
    stabilizer = ActiveProjectStabilizer()
    candidate = _attribution('topic-a')

    assert stabilizer.observe(FIRST_WORKTREE_ID, candidate) is None


def test_stabilizer_emits_after_two_consecutive_polls() -> None:
    """Emit after two consecutive identical polls."""
    stabilizer = ActiveProjectStabilizer()
    candidate = _attribution('topic-a')

    stabilizer.observe(FIRST_WORKTREE_ID, candidate)

    assert stabilizer.observe(FIRST_WORKTREE_ID, candidate) == candidate


def test_stabilizer_does_not_repeat_stable_result() -> None:
    """Withhold repeats of an emitted result."""
    stabilizer = ActiveProjectStabilizer()
    candidate = _attribution('topic-a')
    stabilizer.observe(FIRST_WORKTREE_ID, candidate)
    stabilizer.observe(FIRST_WORKTREE_ID, candidate)

    assert stabilizer.observe(FIRST_WORKTREE_ID, candidate) is None
    assert stabilizer.observe(FIRST_WORKTREE_ID, candidate) is None


def test_stabilizer_resets_count_on_change() -> None:
    """Reset the poll count when the identity changes."""
    stabilizer = ActiveProjectStabilizer()
    first = _attribution('topic-a')
    second = _attribution('topic-b')
    stabilizer.observe(FIRST_WORKTREE_ID, first)

    assert stabilizer.observe(FIRST_WORKTREE_ID, second) is None
    assert stabilizer.observe(FIRST_WORKTREE_ID, second) == second


def test_stabilizer_ignores_single_poll_flicker() -> None:
    """Ignore an unconfirmed single poll change."""
    stabilizer = ActiveProjectStabilizer()
    stable = _attribution('topic-a')
    flicker = _attribution('topic-b')
    stabilizer.observe(FIRST_WORKTREE_ID, stable)
    stabilizer.observe(FIRST_WORKTREE_ID, stable)

    assert stabilizer.observe(FIRST_WORKTREE_ID, flicker) is None
    assert stabilizer.observe(FIRST_WORKTREE_ID, stable) is None
    assert stabilizer.observe(FIRST_WORKTREE_ID, stable) is None


def test_stabilizer_reemits_after_confirmed_change_and_return() -> None:
    """Emit again after a confirmed change and return."""
    stabilizer = ActiveProjectStabilizer()
    first = _attribution('topic-a')
    second = _attribution('topic-b')
    stabilizer.observe(FIRST_WORKTREE_ID, first)
    stabilizer.observe(FIRST_WORKTREE_ID, first)
    stabilizer.observe(FIRST_WORKTREE_ID, second)
    stabilizer.observe(FIRST_WORKTREE_ID, second)

    assert stabilizer.observe(FIRST_WORKTREE_ID, first) is None
    assert stabilizer.observe(FIRST_WORKTREE_ID, first) == first


def test_stabilizer_honours_longer_confirmation_window() -> None:
    """Honour a longer confirmation window."""
    stabilizer = ActiveProjectStabilizer(required_polls=3)
    candidate = _attribution('topic-a')

    assert stabilizer.observe(FIRST_WORKTREE_ID, candidate) is None
    assert stabilizer.observe(FIRST_WORKTREE_ID, candidate) is None
    assert stabilizer.observe(FIRST_WORKTREE_ID, candidate) == candidate


@pytest.mark.parametrize('required_polls', [1, 0, -1])
def test_stabilizer_rejects_short_confirmation_window(
    required_polls: int,
) -> None:
    """Reject a confirmation window shorter than the accepted rule."""
    with pytest.raises(
        ValueError,
        match='required_polls must be at least 2',
    ):
        ActiveProjectStabilizer(required_polls=required_polls)


@pytest.mark.parametrize(
    'required_polls',
    [2.5, 2.0, '2', None, True, False, [2]],
    ids=[
        'float-fraction',
        'float-whole',
        'string',
        'none',
        'bool-true',
        'bool-false',
        'list',
    ],
)
def test_stabilizer_rejects_non_integer_confirmation_window(
    required_polls: object,
) -> None:
    """Reject a non-integer confirmation window with one stable failure."""
    with pytest.raises(
        ValueError,
        match='required_polls must be at least 2',
    ):
        ActiveProjectStabilizer(required_polls=required_polls)  # type: ignore[arg-type]


# === Internal identity contract ===


def test_stabilizer_separates_distinct_worktrees_with_equal_values() -> None:
    """Keep distinct worktree identities apart despite equal public values."""
    stabilizer = ActiveProjectStabilizer()
    candidate = _attribution('topic-a')

    assert stabilizer.observe(FIRST_WORKTREE_ID, candidate) is None
    assert stabilizer.observe(SECOND_WORKTREE_ID, candidate) is None


def test_stabilizer_emits_confirmed_move_between_equal_values() -> None:
    """Emit a confirmed move between worktrees carrying equal values."""
    stabilizer = ActiveProjectStabilizer()
    candidate = _attribution('topic-a')
    stabilizer.observe(FIRST_WORKTREE_ID, candidate)
    stabilizer.observe(FIRST_WORKTREE_ID, candidate)

    assert stabilizer.observe(SECOND_WORKTREE_ID, candidate) is None
    assert stabilizer.observe(SECOND_WORKTREE_ID, candidate) == candidate


def test_stabilizer_reemits_when_only_public_values_change() -> None:
    """Emit again when one identity changes its public values."""
    stabilizer = ActiveProjectStabilizer()
    first = _attribution('topic-a')
    renamed = _attribution('Topic A')
    stabilizer.observe(FIRST_WORKTREE_ID, first)
    stabilizer.observe(FIRST_WORKTREE_ID, first)

    assert stabilizer.observe(FIRST_WORKTREE_ID, renamed) is None
    assert stabilizer.observe(FIRST_WORKTREE_ID, renamed) == renamed


def test_stabilizer_returns_only_public_attribution() -> None:
    """Return only the public attribution, never the internal identity."""
    stabilizer = ActiveProjectStabilizer()
    candidate = _attribution('topic-a')
    stabilizer.observe(FIRST_WORKTREE_ID, candidate)

    emitted = stabilizer.observe(FIRST_WORKTREE_ID, candidate)

    assert emitted == candidate
    assert isinstance(emitted, ProjectAttribution)
    assert FIRST_WORKTREE_ID not in json.dumps(emitted.to_public_dict())


def test_required_stable_polls_matches_accepted_rule() -> None:
    """Match the accepted two poll stability rule."""
    assert REQUIRED_STABLE_POLLS == 2
