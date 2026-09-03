"""Test Orca registry validation."""

import unicodedata
from typing import Any

import pytest

from aw_watcher_orca.errors import (
    AmbiguousMappingError,
    DuplicateRepositoryError,
    MalformedStateError,
    UnknownRepositoryError,
    UnknownWorktreeError,
    UnsupportedSchemaError,
)
from aw_watcher_orca.models import (
    ProjectRegistry,
    RepositoryEntry,
    WorktreeEntry,
)
from aw_watcher_orca.registry import load_registry
from aw_watcher_orca.resolver import resolve_active_project
from synthetic_state import (
    CHILD_WORKTREE_PATH,
    COMPOSED_REPO_NAME,
    DECOMPOSED_REPO_NAME,
    MAIN_REPO_ID,
    MAIN_REPO_NAME,
    MAIN_REPO_PATH,
    SECOND_REPO_ID,
    SECOND_REPO_NAME,
    SECOND_REPO_PATH,
    WORKTREE_ROOT,
    build_repo,
    build_single_repo_state,
    build_state,
    build_worktree_key,
    build_worktree_meta,
)

# === Accepted registry ===


def test_load_registry_indexes_registered_worktrees() -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)

    registry = load_registry(state)

    child_key = build_worktree_key(MAIN_REPO_ID, CHILD_WORKTREE_PATH)
    assert registry.schema_version == 1
    assert registry.active_worktree_id == child_key
    assert set(registry.repositories) == {MAIN_REPO_ID}
    assert registry.repositories[MAIN_REPO_ID].display_name == MAIN_REPO_NAME
    assert set(registry.worktrees) == {
        build_worktree_key(MAIN_REPO_ID, MAIN_REPO_PATH),
        child_key,
    }
    assert registry.worktrees[child_key].repo_id == MAIN_REPO_ID
    assert registry.worktrees[child_key].path == CHILD_WORKTREE_PATH


def test_load_registry_normalizes_active_identity_prefix() -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    child_key = build_worktree_key(MAIN_REPO_ID, CHILD_WORKTREE_PATH)
    state['workspaceSession']['activeWorktreeId'] = f'worktree:{child_key}'

    registry = load_registry(state)

    assert registry.active_worktree_id == child_key


def test_load_registry_ignores_active_workspace_key() -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    state['workspaceSession']['activeWorkspaceKey'] = build_worktree_key(
        MAIN_REPO_ID,
        MAIN_REPO_PATH,
    )

    registry = load_registry(state)

    assert registry.active_worktree_id == build_worktree_key(
        MAIN_REPO_ID,
        CHILD_WORKTREE_PATH,
    )


def test_load_registry_reads_absent_display_name_as_none() -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    child_key = build_worktree_key(MAIN_REPO_ID, CHILD_WORKTREE_PATH)

    registry = load_registry(state)

    assert registry.worktrees[child_key].display_name is None


@pytest.mark.parametrize('display_name', ['', '   ', 17, None, ['name']])
def test_load_registry_reads_unusable_display_name_as_none(
    display_name: object,
) -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    child_key = build_worktree_key(MAIN_REPO_ID, CHILD_WORKTREE_PATH)
    state['worktreeMeta'][child_key]['displayName'] = display_name

    registry = load_registry(state)

    assert registry.worktrees[child_key].display_name is None


def test_load_registry_strips_worktree_display_name() -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH, '  Topic A  ')
    child_key = build_worktree_key(MAIN_REPO_ID, CHILD_WORKTREE_PATH)

    registry = load_registry(state)

    assert registry.worktrees[child_key].display_name == 'Topic A'


# === Registry immutability ===


def test_registry_rejects_direct_repository_mutation() -> None:
    registry = load_registry(build_single_repo_state(CHILD_WORKTREE_PATH))
    replacement = registry.repositories[MAIN_REPO_ID]

    with pytest.raises(TypeError):
        registry.repositories[SECOND_REPO_ID] = replacement  # type: ignore[index]


def test_registry_rejects_direct_worktree_mutation() -> None:
    registry = load_registry(build_single_repo_state(CHILD_WORKTREE_PATH))
    child_key = build_worktree_key(MAIN_REPO_ID, CHILD_WORKTREE_PATH)
    replacement = registry.worktrees[child_key]

    with pytest.raises(TypeError):
        registry.worktrees['repo-0001::/sandbox/new'] = replacement  # type: ignore[index]


def test_registry_ignores_later_mutation_of_input_maps() -> None:
    repositories = {
        MAIN_REPO_ID: RepositoryEntry(
            repo_id=MAIN_REPO_ID,
            display_name=MAIN_REPO_NAME,
            path=MAIN_REPO_PATH,
        )
    }
    main_key = build_worktree_key(MAIN_REPO_ID, MAIN_REPO_PATH)
    worktrees = {
        main_key: WorktreeEntry(
            worktree_id=main_key,
            repo_id=MAIN_REPO_ID,
            path=MAIN_REPO_PATH,
            display_name=None,
        )
    }
    registry = ProjectRegistry(
        schema_version=1,
        active_worktree_id=main_key,
        repositories=repositories,
        worktrees=worktrees,
    )

    repositories.clear()
    worktrees.clear()

    assert set(registry.repositories) == {MAIN_REPO_ID}
    assert len(registry.worktrees) == 1


# === Root and schema guards ===


@pytest.mark.parametrize('payload', [None, [], 'state', 7])
def test_load_registry_rejects_non_object_root(payload: object) -> None:
    with pytest.raises(
        MalformedStateError,
        match='Orca state root must be an object',
    ):
        load_registry(payload)


@pytest.mark.parametrize('schema_version', [2, 0, -1])
def test_load_registry_rejects_unsupported_schema(
    schema_version: object,
) -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    state['schemaVersion'] = schema_version

    with pytest.raises(
        UnsupportedSchemaError,
        match='Unsupported Orca schema version',
    ):
        load_registry(state)


@pytest.mark.parametrize('schema_version', [True, 1.0, '1', None])
def test_load_registry_rejects_non_integer_schema(
    schema_version: object,
) -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    state['schemaVersion'] = schema_version

    with pytest.raises(
        UnsupportedSchemaError,
        match='Unsupported Orca schema version',
    ):
        load_registry(state)


def test_load_registry_rejects_missing_schema_version() -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    del state['schemaVersion']

    with pytest.raises(
        UnsupportedSchemaError,
        match='Unsupported Orca schema version',
    ):
        load_registry(state)


# === Session guards ===


@pytest.mark.parametrize('workspace_session', [None, [], 'session'])
def test_load_registry_rejects_malformed_session(
    workspace_session: object,
) -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    state['workspaceSession'] = workspace_session

    with pytest.raises(
        MalformedStateError,
        match='Missing workspaceSession object',
    ):
        load_registry(state)


def test_load_registry_rejects_missing_session() -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    del state['workspaceSession']

    with pytest.raises(
        MalformedStateError,
        match='Missing workspaceSession object',
    ):
        load_registry(state)


@pytest.mark.parametrize('active_worktree_id', ['', None, 17, ['id']])
def test_load_registry_rejects_invalid_active_identity(
    active_worktree_id: object,
) -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    state['workspaceSession']['activeWorktreeId'] = active_worktree_id

    with pytest.raises(
        MalformedStateError,
        match='Missing workspaceSession.activeWorktreeId',
    ):
        load_registry(state)


@pytest.mark.parametrize(
    'active_worktree_id',
    ['missing-separator', '::/sandbox/repos/alpha', 'repo-0001::'],
)
def test_load_registry_rejects_malformed_active_identity(
    active_worktree_id: str,
) -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    state['workspaceSession']['activeWorktreeId'] = active_worktree_id

    with pytest.raises(
        MalformedStateError,
        match='Invalid Orca worktree identity',
    ):
        load_registry(state)


def test_load_registry_rejects_unknown_active_worktree() -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    state['workspaceSession']['activeWorktreeId'] = build_worktree_key(
        MAIN_REPO_ID,
        '/sandbox/worktrees/alpha/never-registered',
    )

    with pytest.raises(
        UnknownWorktreeError,
        match='Unknown active Orca worktree',
    ):
        load_registry(state)


# === Repository guards ===


@pytest.mark.parametrize('repos', [None, {}, 'repos'])
def test_load_registry_rejects_malformed_repos(repos: object) -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    state['repos'] = repos

    with pytest.raises(MalformedStateError, match='Missing repos list'):
        load_registry(state)


def test_load_registry_rejects_missing_repos() -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    del state['repos']

    with pytest.raises(MalformedStateError, match='Missing repos list'):
        load_registry(state)


@pytest.mark.parametrize('raw_repo', [None, 'repo', ['repo']])
def test_load_registry_rejects_malformed_repo_entry(
    raw_repo: object,
) -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    state['repos'].append(raw_repo)

    with pytest.raises(
        MalformedStateError,
        match='Invalid Orca repository entry',
    ):
        load_registry(state)


@pytest.mark.parametrize('repo_id', ['', '  ', None, 17])
def test_load_registry_rejects_invalid_repo_id(repo_id: object) -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    state['repos'][0]['id'] = repo_id

    with pytest.raises(
        MalformedStateError,
        match='Invalid Orca repository id',
    ):
        load_registry(state)


@pytest.mark.parametrize('display_name', ['', '   ', None, 17, ['name']])
def test_load_registry_rejects_unusable_repo_name(
    display_name: object,
) -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    state['repos'][0]['displayName'] = display_name

    with pytest.raises(
        MalformedStateError,
        match='Unusable Orca repository name',
    ):
        load_registry(state)


@pytest.mark.parametrize('repo_path', ['', '   ', None, 17])
def test_load_registry_rejects_invalid_repo_path(repo_path: object) -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    state['repos'][0]['path'] = repo_path

    with pytest.raises(
        MalformedStateError,
        match='Invalid Orca repository path',
    ):
        load_registry(state)


@pytest.mark.parametrize(
    'display_name',
    ['/sandbox/repos/alpha', '/', '/alpha'],
)
def test_load_registry_rejects_absolute_repo_name(
    display_name: str,
) -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    state['repos'][0]['displayName'] = display_name

    with pytest.raises(
        MalformedStateError,
        match='Unusable Orca repository name',
    ):
        load_registry(state)


def test_load_registry_hides_absolute_repo_name_value() -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    state['repos'][0]['displayName'] = '/sandbox/repos/alpha'

    with pytest.raises(MalformedStateError) as raised:
        load_registry(state)

    assert '/sandbox' not in str(raised.value)


def test_load_registry_rejects_duplicate_repo_ids() -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    state['repos'].append(
        build_repo(MAIN_REPO_ID, SECOND_REPO_NAME, SECOND_REPO_PATH)
    )

    with pytest.raises(
        DuplicateRepositoryError,
        match='Duplicate Orca repository id',
    ):
        load_registry(state)


def test_load_registry_hides_duplicate_repo_id_value() -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    state['repos'].append(
        build_repo(MAIN_REPO_ID, SECOND_REPO_NAME, SECOND_REPO_PATH)
    )

    with pytest.raises(DuplicateRepositoryError) as raised:
        load_registry(state)

    assert MAIN_REPO_ID not in str(raised.value)
    assert SECOND_REPO_PATH not in str(raised.value)


# === Canonical identifier and path guards ===


@pytest.mark.parametrize(
    'repo_id',
    [' repo-0001', 'repo-0001 ', ' repo-0001 ', '\trepo-0001', 'repo-0001\n'],
)
def test_load_registry_rejects_non_canonical_repo_id(repo_id: str) -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    state['repos'][0]['id'] = repo_id

    with pytest.raises(
        MalformedStateError,
        match='Invalid Orca repository id',
    ):
        load_registry(state)


@pytest.mark.parametrize(
    'repo_path',
    [f' {MAIN_REPO_PATH}', f'{MAIN_REPO_PATH} ', f'{MAIN_REPO_PATH}\n'],
)
def test_load_registry_rejects_non_canonical_repo_path(
    repo_path: str,
) -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    state['repos'][0]['path'] = repo_path

    with pytest.raises(
        MalformedStateError,
        match='Invalid Orca repository path',
    ):
        load_registry(state)


@pytest.mark.parametrize(
    'worktree_key',
    [
        f' {MAIN_REPO_ID}::{CHILD_WORKTREE_PATH}',
        f'{MAIN_REPO_ID} ::{CHILD_WORKTREE_PATH}',
        f'{MAIN_REPO_ID}:: {CHILD_WORKTREE_PATH}',
        f'{MAIN_REPO_ID}::{CHILD_WORKTREE_PATH} ',
    ],
)
def test_load_registry_rejects_non_canonical_worktree_key(
    worktree_key: str,
) -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    state['worktreeMeta'][worktree_key] = build_worktree_meta()

    with pytest.raises(
        MalformedStateError,
        match='Invalid Orca worktree identity',
    ):
        load_registry(state)


@pytest.mark.parametrize(
    'active_worktree_id',
    [
        f' {MAIN_REPO_ID}::{CHILD_WORKTREE_PATH}',
        f'{MAIN_REPO_ID} ::{CHILD_WORKTREE_PATH}',
        f'{MAIN_REPO_ID}:: {CHILD_WORKTREE_PATH}',
        f'{MAIN_REPO_ID}::{CHILD_WORKTREE_PATH} ',
    ],
)
def test_load_registry_rejects_non_canonical_active_identity(
    active_worktree_id: str,
) -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    state['workspaceSession']['activeWorktreeId'] = active_worktree_id

    with pytest.raises(
        MalformedStateError,
        match='Invalid Orca worktree identity',
    ):
        load_registry(state)


def test_load_registry_keeps_repository_name_trimming() -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    state['repos'][0]['displayName'] = f'  {MAIN_REPO_NAME}  '

    registry = load_registry(state)

    assert registry.repositories[MAIN_REPO_ID].display_name == MAIN_REPO_NAME


# === Worktree registry guards ===


@pytest.mark.parametrize('worktree_meta', [None, [], 'meta'])
def test_load_registry_rejects_malformed_worktree_meta(
    worktree_meta: object,
) -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    state['worktreeMeta'] = worktree_meta

    with pytest.raises(
        MalformedStateError,
        match='Missing worktreeMeta object',
    ):
        load_registry(state)


def test_load_registry_rejects_missing_worktree_meta() -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    del state['worktreeMeta']

    with pytest.raises(
        MalformedStateError,
        match='Missing worktreeMeta object',
    ):
        load_registry(state)


@pytest.mark.parametrize('raw_meta', [None, 'meta', ['meta']])
def test_load_registry_rejects_malformed_worktree_entry(
    raw_meta: object,
) -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    child_key = build_worktree_key(MAIN_REPO_ID, CHILD_WORKTREE_PATH)
    state['worktreeMeta'][child_key] = raw_meta

    with pytest.raises(
        MalformedStateError,
        match='Invalid Orca worktree metadata',
    ):
        load_registry(state)


@pytest.mark.parametrize(
    'worktree_key',
    ['missing-separator', '::/sandbox/repos/alpha', 'repo-0001::'],
)
def test_load_registry_rejects_malformed_worktree_key(
    worktree_key: str,
) -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    state['worktreeMeta'][worktree_key] = build_worktree_meta()

    with pytest.raises(
        MalformedStateError,
        match='Invalid Orca worktree identity',
    ):
        load_registry(state)


def test_load_registry_rejects_non_string_worktree_key() -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    state['worktreeMeta'][17] = build_worktree_meta()

    with pytest.raises(
        MalformedStateError,
        match='Invalid Orca worktree identity',
    ):
        load_registry(state)


def test_load_registry_rejects_unknown_repository_id() -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    orphan_key = build_worktree_key(
        SECOND_REPO_ID,
        f'{SECOND_REPO_PATH}/topic-b',
    )
    state['worktreeMeta'][orphan_key] = build_worktree_meta()

    with pytest.raises(
        UnknownRepositoryError,
        match='Unknown Orca repository id',
    ):
        load_registry(state)


def test_load_registry_rejects_ambiguous_worktree_identity() -> None:
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    child_key = build_worktree_key(MAIN_REPO_ID, CHILD_WORKTREE_PATH)
    state['worktreeMeta'][f'worktree:{child_key}'] = build_worktree_meta()

    with pytest.raises(
        AmbiguousMappingError,
        match='Ambiguous Orca worktree identity',
    ):
        load_registry(state)


def test_load_registry_rejects_colliding_labels() -> None:
    child_key = build_worktree_key(MAIN_REPO_ID, CHILD_WORKTREE_PATH)
    twin_key = build_worktree_key(
        MAIN_REPO_ID,
        '/sandbox/worktrees/alpha-mirror/topic-a',
    )
    state = build_state(
        active_worktree_id=child_key,
        repos=[build_repo(MAIN_REPO_ID, MAIN_REPO_NAME, MAIN_REPO_PATH)],
        worktree_meta={
            child_key: build_worktree_meta(),
            twin_key: build_worktree_meta(),
        },
    )

    with pytest.raises(
        AmbiguousMappingError,
        match='Ambiguous Orca project label',
    ):
        load_registry(state)


def test_load_registry_rejects_canonically_equivalent_repo_labels() -> None:
    first_key = build_worktree_key(MAIN_REPO_ID, MAIN_REPO_PATH)
    second_key = build_worktree_key(SECOND_REPO_ID, SECOND_REPO_PATH)
    state = build_state(
        active_worktree_id=first_key,
        repos=[
            build_repo(MAIN_REPO_ID, COMPOSED_REPO_NAME, MAIN_REPO_PATH),
            build_repo(SECOND_REPO_ID, DECOMPOSED_REPO_NAME, SECOND_REPO_PATH),
        ],
        worktree_meta={
            first_key: build_worktree_meta(),
            second_key: build_worktree_meta(),
        },
    )

    with pytest.raises(
        AmbiguousMappingError,
        match='Ambiguous Orca project label',
    ):
        load_registry(state)


def test_load_registry_rejects_canonically_equivalent_worktree_labels() -> (
    None
):
    first_key = build_worktree_key(MAIN_REPO_ID, MAIN_REPO_PATH)
    composed_key = build_worktree_key(
        MAIN_REPO_ID,
        f'{WORKTREE_ROOT}/alpha/one',
    )
    decomposed_key = build_worktree_key(
        MAIN_REPO_ID,
        f'{WORKTREE_ROOT}/alpha/two',
    )
    state = build_state(
        active_worktree_id=first_key,
        repos=[build_repo(MAIN_REPO_ID, MAIN_REPO_NAME, MAIN_REPO_PATH)],
        worktree_meta={
            first_key: build_worktree_meta(),
            composed_key: build_worktree_meta(COMPOSED_REPO_NAME),
            decomposed_key: build_worktree_meta(DECOMPOSED_REPO_NAME),
        },
    )

    with pytest.raises(
        AmbiguousMappingError,
        match='Ambiguous Orca project label',
    ):
        load_registry(state)


def test_public_names_are_normalized_to_composed_form() -> None:
    state = build_single_repo_state(
        CHILD_WORKTREE_PATH,
        DECOMPOSED_REPO_NAME,
    )
    state['repos'][0]['displayName'] = DECOMPOSED_REPO_NAME

    attribution = resolve_active_project(load_registry(state))

    assert attribution.repo == COMPOSED_REPO_NAME
    assert attribution.worktree == COMPOSED_REPO_NAME
    assert unicodedata.is_normalized('NFC', attribution.label)


def test_load_registry_rejects_unusable_worktree_path() -> None:
    root_key = build_worktree_key(MAIN_REPO_ID, '/')
    state = build_single_repo_state(CHILD_WORKTREE_PATH)
    state['worktreeMeta'][root_key] = build_worktree_meta()

    with pytest.raises(
        MalformedStateError,
        match='Unusable Orca worktree path',
    ):
        load_registry(state)


def test_load_registry_accepts_distinct_repositories() -> None:
    first_key = build_worktree_key(MAIN_REPO_ID, MAIN_REPO_PATH)
    second_key = build_worktree_key(SECOND_REPO_ID, SECOND_REPO_PATH)
    state: dict[str, Any] = build_state(
        active_worktree_id=first_key,
        repos=[
            build_repo(MAIN_REPO_ID, MAIN_REPO_NAME, MAIN_REPO_PATH),
            build_repo(SECOND_REPO_ID, SECOND_REPO_NAME, SECOND_REPO_PATH),
        ],
        worktree_meta={
            first_key: build_worktree_meta(),
            second_key: build_worktree_meta(),
        },
    )

    registry = load_registry(state)

    assert set(registry.repositories) == {MAIN_REPO_ID, SECOND_REPO_ID}
    assert set(registry.worktrees) == {first_key, second_key}
