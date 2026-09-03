"""Build sanitized synthetic Orca state documents."""

from typing import Any

# === Constants ===


SANDBOX_ROOT = '/sandbox/repos'
WORKTREE_ROOT = '/sandbox/worktrees'
MAIN_REPO_ID = 'repo-0001'
MAIN_REPO_NAME = 'alpha'
MAIN_REPO_PATH = f'{SANDBOX_ROOT}/alpha'
CHILD_WORKTREE_PATH = f'{WORKTREE_ROOT}/alpha/topic-a'
SECOND_REPO_ID = 'repo-0002'
SECOND_REPO_NAME = 'beta'
SECOND_REPO_PATH = f'{SANDBOX_ROOT}/beta'
ABSOLUTE_DISPLAY_NAME = f'{WORKTREE_ROOT}/alpha/topic-a'
COMPOSED_REPO_NAME = 'caf\u00e9'
DECOMPOSED_REPO_NAME = 'cafe\u0301'


# === Builders ===


def build_repo(
    repo_id: str,
    display_name: str,
    path: str,
) -> dict[str, Any]:
    """Build one synthetic repository entry."""
    return {
        'id': repo_id,
        'displayName': display_name,
        'path': path,
        'kind': 'local',
    }


def build_worktree_key(repo_id: str, worktree_path: str) -> str:
    """Build one synthetic worktree registry key."""
    return f'{repo_id}::{worktree_path}'


def build_worktree_meta(display_name: object = None) -> dict[str, Any]:
    """Build one synthetic worktree metadata entry."""
    meta: dict[str, Any] = {'isArchived': False, 'isPinned': False}
    if display_name is not None:
        meta['displayName'] = display_name
    return meta


def build_state(
    active_worktree_id: str,
    repos: list[Any],
    worktree_meta: dict[str, Any],
    schema_version: object = 1,
) -> dict[str, Any]:
    """Build one synthetic Orca state document."""
    return {
        'schemaVersion': schema_version,
        'workspaceSession': {
            'activeWorktreeId': active_worktree_id,
            'activeWorkspaceKey': 'worktree:repo-9999::/sandbox/unrelated',
        },
        'repos': repos,
        'worktreeMeta': worktree_meta,
    }


def build_single_repo_state(
    active_worktree_path: str,
    worktree_display_name: object = None,
) -> dict[str, Any]:
    """Build one synthetic single repository state."""
    main_key = build_worktree_key(MAIN_REPO_ID, MAIN_REPO_PATH)
    child_key = build_worktree_key(MAIN_REPO_ID, CHILD_WORKTREE_PATH)
    active_key = build_worktree_key(MAIN_REPO_ID, active_worktree_path)
    worktree_meta = {
        main_key: build_worktree_meta(),
        child_key: build_worktree_meta(),
    }
    worktree_meta[active_key] = build_worktree_meta(worktree_display_name)
    return build_state(
        active_worktree_id=active_key,
        repos=[
            build_repo(MAIN_REPO_ID, MAIN_REPO_NAME, MAIN_REPO_PATH),
        ],
        worktree_meta=worktree_meta,
    )


def build_large_synthetic_set_state(
    repo_count: int,
    child_counts: list[int],
    named_worktrees: int,
) -> dict[str, Any]:
    """Build one large synthetic set."""
    repos: list[Any] = []
    worktree_meta: dict[str, Any] = {}
    named_remaining = named_worktrees
    for repo_index in range(repo_count):
        repo_id = f'repo-{repo_index:04d}'
        repo_name = f'project-{repo_index:02d}'
        repo_path = f'{SANDBOX_ROOT}/{repo_name}'
        repos.append(build_repo(repo_id, repo_name, repo_path))
        main_key = build_worktree_key(repo_id, repo_path)
        worktree_meta[main_key] = build_worktree_meta()
        for child_index in range(child_counts[repo_index]):
            child_path = (
                f'{WORKTREE_ROOT}/{repo_name}/branch-{child_index:02d}'
            )
            child_key = build_worktree_key(repo_id, child_path)
            display_name: object = None
            if named_remaining > 0:
                display_name = f'Feature {repo_index:02d}-{child_index:02d}'
                named_remaining -= 1
            worktree_meta[child_key] = build_worktree_meta(display_name)
    active_worktree_id = next(iter(worktree_meta))
    return build_state(
        active_worktree_id=active_worktree_id,
        repos=repos,
        worktree_meta=worktree_meta,
    )
