"""Orca project resolver tests."""

import json

import pytest

from aw_watcher_orca.errors import (
    UnknownRepositoryError,
    UnknownWorktreeError,
)
from aw_watcher_orca.labels import build_attribution, build_worktree_name
from aw_watcher_orca.models import (
    ProjectRegistry,
    RepositoryEntry,
    WorktreeEntry,
)
from aw_watcher_orca.registry import load_registry
from aw_watcher_orca.resolver import resolve_active_project
from synthetic_state import (
    ABSOLUTE_DISPLAY_NAME,
    CHILD_WORKTREE_PATH,
    MAIN_REPO_ID,
    MAIN_REPO_NAME,
    MAIN_REPO_PATH,
    SECOND_REPO_ID,
    build_large_synthetic_set_state,
    build_single_repo_state,
    build_worktree_key,
)

# === Label contract ===


def test_resolve_labels_main_worktree_with_repository_name() -> None:
    state = build_single_repo_state(MAIN_REPO_PATH)

    attribution = resolve_active_project(load_registry(state))

    assert attribution.label == MAIN_REPO_NAME
    assert attribution.repo == MAIN_REPO_NAME
    assert attribution.worktree == 'alpha'
    assert attribution.is_main_worktree is True


def test_resolve_labels_child_worktree_with_path_component() -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)

    attribution = resolve_active_project(load_registry(state))

    assert attribution.label == 'alpha / topic-a'
    assert attribution.repo == MAIN_REPO_NAME
    assert attribution.worktree == 'topic-a'
    assert attribution.is_main_worktree is False


def test_resolve_prefers_worktree_display_name() -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH, 'Topic Alpha')

    attribution = resolve_active_project(load_registry(state))

    assert attribution.label == 'alpha / Topic Alpha'
    assert attribution.worktree == 'Topic Alpha'


@pytest.mark.parametrize('display_name', ['', '   ', 17, ['name']])
def test_resolve_falls_back_on_unusable_display_name(
    display_name: object,
) -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH, display_name)

    attribution = resolve_active_project(load_registry(state))

    assert attribution.label == 'alpha / topic-a'


def test_resolve_reports_supported_schema_source() -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)

    attribution = resolve_active_project(load_registry(state))

    assert attribution.schema_source == 'orca-data-v1'


def test_resolve_labels_main_worktree_without_separator() -> None:
    state = build_single_repo_state(MAIN_REPO_PATH, 'Main Checkout')

    attribution = resolve_active_project(load_registry(state))

    assert attribution.label == MAIN_REPO_NAME
    assert '/' not in attribution.label


# === Absolute display name policy ===


def test_resolve_falls_back_on_absolute_worktree_display_name() -> None:
    state = build_single_repo_state(
        CHILD_WORKTREE_PATH,
        ABSOLUTE_DISPLAY_NAME,
    )

    attribution = resolve_active_project(load_registry(state))

    assert attribution.worktree == 'topic-a'
    assert attribution.label == 'alpha / topic-a'


def test_public_mapping_hides_absolute_worktree_display_name() -> None:
    state = build_single_repo_state(
        CHILD_WORKTREE_PATH,
        ABSOLUTE_DISPLAY_NAME,
    )

    public = resolve_active_project(load_registry(state)).to_public_dict()

    assert ABSOLUTE_DISPLAY_NAME not in public.values()
    assert public['worktree'] == 'topic-a'
    assert public['label'] == 'alpha / topic-a'


def test_public_json_hides_absolute_worktree_display_name() -> None:
    state = build_single_repo_state(
        CHILD_WORKTREE_PATH,
        ABSOLUTE_DISPLAY_NAME,
    )

    attribution = resolve_active_project(load_registry(state))
    serialized = json.dumps(attribution.to_public_dict(), ensure_ascii=False)

    assert ABSOLUTE_DISPLAY_NAME not in serialized
    assert '/sandbox' not in serialized


def test_attribution_repr_hides_absolute_worktree_display_name() -> None:
    state = build_single_repo_state(
        CHILD_WORKTREE_PATH,
        ABSOLUTE_DISPLAY_NAME,
    )

    rendered = repr(resolve_active_project(load_registry(state)))

    assert ABSOLUTE_DISPLAY_NAME not in rendered
    assert '/sandbox' not in rendered


def test_build_worktree_name_rejects_absolute_display_name() -> None:
    worktree = WorktreeEntry(
        worktree_id=build_worktree_key(MAIN_REPO_ID, CHILD_WORKTREE_PATH),
        repo_id=MAIN_REPO_ID,
        path=CHILD_WORKTREE_PATH,
        display_name=ABSOLUTE_DISPLAY_NAME,
    )

    assert build_worktree_name(worktree) == 'topic-a'


# === Privacy contract ===


def test_public_attribution_hides_identifiers_and_paths() -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)

    attribution = resolve_active_project(load_registry(state))
    public = attribution.to_public_dict()
    serialized = json.dumps(public, ensure_ascii=False)

    assert set(public) == {
        'repo',
        'worktree',
        'label',
        'is_main_worktree',
        'schema_source',
    }
    assert MAIN_REPO_ID not in serialized
    assert MAIN_REPO_PATH not in serialized
    assert CHILD_WORKTREE_PATH not in serialized
    assert '/sandbox' not in serialized
    assert '::' not in serialized


def test_public_attribution_hides_paths_for_main_worktree() -> None:
    state = build_single_repo_state(MAIN_REPO_PATH)

    attribution = resolve_active_project(load_registry(state))
    serialized = json.dumps(attribution.to_public_dict())

    assert '/sandbox' not in serialized
    assert MAIN_REPO_ID not in serialized


# === Large synthetic set contract ===


MIXED_NAMING_SHAPE: tuple[int, list[int], int] = (
    18,
    [1, 0, 2, 1, 0, 1, 0, 3, 1, 0, 2, 0, 1, 0, 1, 0, 1, 1],
    8,
)
LARGE_SYNTHETIC_SHAPES = [
    pytest.param(
        17,
        [2, 1, 0, 3, 1, 0, 2, 1, 0, 1, 2, 0, 1, 0, 1, 0, 0],
        15,
        id='seventeen-repos-all-children-named',
    ),
    pytest.param(
        *MIXED_NAMING_SHAPE,
        id='eighteen-repos-named-and-fallback-children',
    ),
]


@pytest.mark.parametrize(
    ('repo_count', 'child_counts', 'named_worktrees'),
    LARGE_SYNTHETIC_SHAPES,
)
def test_large_synthetic_set_produces_unique_labels(
    repo_count: int,
    child_counts: list[int],
    named_worktrees: int,
) -> None:
    state = build_large_synthetic_set_state(
        repo_count=repo_count,
        child_counts=child_counts,
        named_worktrees=named_worktrees,
    )

    registry = load_registry(state)
    attributions = [
        build_attribution(
            worktree,
            registry.repositories[worktree.repo_id],
            registry.schema_version,
        )
        for worktree in registry.worktrees.values()
    ]
    labels = [item.label for item in attributions]

    assert len(registry.repositories) == repo_count
    assert len(labels) == repo_count + sum(child_counts)
    assert len(set(labels)) == len(labels)


def test_large_synthetic_set_mixes_named_and_fallback_children() -> None:
    repo_count, child_counts, named_worktrees = MIXED_NAMING_SHAPE
    state = build_large_synthetic_set_state(
        repo_count=repo_count,
        child_counts=child_counts,
        named_worktrees=named_worktrees,
    )

    registry = load_registry(state)
    child_names = [
        build_attribution(
            worktree,
            registry.repositories[worktree.repo_id],
            registry.schema_version,
        ).worktree
        for worktree in registry.worktrees.values()
        if worktree.path != registry.repositories[worktree.repo_id].path
    ]
    named = [name for name in child_names if name.startswith('Feature ')]
    fallback = [name for name in child_names if name.startswith('branch-')]

    assert len(child_names) == sum(child_counts)
    assert len(named) == named_worktrees
    assert len(fallback) == sum(child_counts) - named_worktrees
    assert named
    assert fallback


def test_large_synthetic_set_separates_main_and_child_labels() -> None:
    child_counts = [1, 1, 0]
    state = build_large_synthetic_set_state(
        repo_count=3,
        child_counts=child_counts,
        named_worktrees=0,
    )

    registry = load_registry(state)
    attributions = [
        build_attribution(
            worktree,
            registry.repositories[worktree.repo_id],
            registry.schema_version,
        )
        for worktree in registry.worktrees.values()
    ]
    main_labels = {
        item.label for item in attributions if item.is_main_worktree
    }
    child_labels = {
        item.label for item in attributions if not item.is_main_worktree
    }

    assert main_labels == {'project-00', 'project-01', 'project-02'}
    assert child_labels == {
        'project-00 / branch-00',
        'project-01 / branch-00',
    }


# === Resolution guards ===


def _registry_without_active_worktree() -> ProjectRegistry:
    """Build inactive worktree registry."""
    return ProjectRegistry(
        schema_version=1,
        active_worktree_id=build_worktree_key(MAIN_REPO_ID, '/sandbox/gone'),
        repositories={
            MAIN_REPO_ID: RepositoryEntry(
                repo_id=MAIN_REPO_ID,
                display_name=MAIN_REPO_NAME,
                path=MAIN_REPO_PATH,
            )
        },
        worktrees={},
    )


def test_resolve_rejects_unknown_active_worktree() -> None:
    registry = _registry_without_active_worktree()

    with pytest.raises(
        UnknownWorktreeError,
        match='Unknown active Orca worktree',
    ):
        resolve_active_project(registry)


def test_resolve_rejects_unknown_repository() -> None:
    active_key = build_worktree_key(SECOND_REPO_ID, '/sandbox/repos/beta')
    registry = ProjectRegistry(
        schema_version=1,
        active_worktree_id=active_key,
        repositories={},
        worktrees={
            active_key: WorktreeEntry(
                worktree_id=active_key,
                repo_id=SECOND_REPO_ID,
                path='/sandbox/repos/beta',
                display_name=None,
            )
        },
    )

    with pytest.raises(
        UnknownRepositoryError,
        match='Unknown Orca repository id',
    ):
        resolve_active_project(registry)
