"""Hold one exclusive user-wide lock for the watcher process."""

import fcntl
import os
from collections.abc import Callable
from pathlib import Path
from typing import Final

from aw_watcher_orca.errors import OrcaCoreError

# === Constants ===


LOCK_DIRECTORY: Final = (
    Path.home() / 'Library' / 'Application Support' / 'aw-watcher-orca'
)
LOCK_FILE_NAME: Final = 'watcher.lock'
LOCK_DIRECTORY_MODE: Final = 0o700
LOCK_FILE_MODE: Final = 0o600
IDENTITY_ATTEMPT_LIMIT: Final = 4


# === Errors ===


class InstanceLockError(OrcaCoreError):
    """Represent a failure to hold the single-instance lock."""


class InstanceLockUnavailableError(InstanceLockError):
    """Represent a lock already held by another watcher process."""


class InstanceLockUnsafeError(InstanceLockError):
    """Represent a lock path that cannot be trusted."""


# === Path safety ===


def path_uses_symlink(path: Path) -> bool:
    """Detect symlink path components."""
    return any(candidate.is_symlink() for candidate in (path, *path.parents))


def default_lock_path() -> Path:
    """Return the user-wide lock path shared by every watcher mode."""
    return LOCK_DIRECTORY / LOCK_FILE_NAME


# === Held lock ===


class InstanceLock:
    """Own one acquired lock descriptor until release."""

    def __init__(self, descriptor: int, path: Path) -> None:
        """Store the acquired descriptor and its path."""
        self._descriptor: int | None = descriptor
        self._path = path

    @property
    def path(self) -> Path:
        """Return the locked path."""
        return self._path

    def fileno(self) -> int:
        """Return the held descriptor."""
        if self._descriptor is None:
            raise InstanceLockError('Instance lock is already released')
        return self._descriptor

    def still_owns_its_path(self) -> bool:
        """Report whether the held descriptor is still the file at the path.

        The post-acquire check protects a claimant from locking an inode that
        was already unlinked. It does nothing for the incumbent: once the file
        is removed under a live holder, the path is free and the next process
        locks a fresh inode. Only the holder re-asking this question bounds
        that window.
        """
        if self._descriptor is None:
            return False
        return _holds_current_path(self._descriptor, self._path)

    def release(self) -> None:
        """Release the lock without removing its file."""
        if self._descriptor is None:
            return
        descriptor, self._descriptor = self._descriptor, None
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


# === Acquisition ===


def _prepare_directory(directory: Path) -> None:
    """Create the private lock directory when it is missing."""
    if directory.exists():
        if not directory.is_dir():
            raise InstanceLockUnsafeError('Lock directory path must be a dir')
        return
    directory.mkdir(mode=LOCK_DIRECTORY_MODE, parents=True)
    directory.chmod(LOCK_DIRECTORY_MODE)


def _open_locked_descriptor(path: Path) -> int:
    """Open the lock file and take the exclusive lock without blocking."""
    try:
        descriptor = os.open(
            path,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            LOCK_FILE_MODE,
        )
    except OSError:
        raise InstanceLockUnsafeError(
            'Unable to open the instance file'
        ) from None
    try:
        os.fchmod(descriptor, LOCK_FILE_MODE)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        raise InstanceLockUnavailableError(
            'Another watcher process holds the instance lock'
        ) from None
    except OSError:
        os.close(descriptor)
        raise InstanceLockUnsafeError(
            'Unable to lock the instance file'
        ) from None
    return descriptor


def _holds_current_path(descriptor: int, path: Path) -> bool:
    """Report whether the held descriptor is still the file at the path."""
    held = os.fstat(descriptor)
    try:
        current = os.stat(path)
    except FileNotFoundError:
        return False
    return (held.st_dev, held.st_ino) == (current.st_dev, current.st_ino)


def acquire_instance_lock(
    lock_path: Path | None = None,
    *,
    after_lock: Callable[[], None] | None = None,
) -> InstanceLock:
    """Acquire the single-instance lock and prove it owns its path.

    The lock file is never removed: unlinking it would open a window between
    releasing the lock and removing the file.

    The identity recheck below covers exactly one case: this caller locking an
    inode that is no longer the file at the path. It does NOT keep a live
    holder exclusive after an outside actor removes the file — the holder must
    ask ``still_owns_its_path`` again for that, which the polling loop does
    once per tick.

    The symlink guard is a start-time check, not an invariant: ``O_NOFOLLOW``
    covers only the final component, so a parent replaced between the check
    and the open is not caught.

    ``after_lock`` is a test seam invoked between locking and the identity
    check, so the replacement race can be reproduced deterministically.
    """
    path = lock_path or default_lock_path()
    if path_uses_symlink(path):
        raise InstanceLockUnsafeError('Lock path cannot use symlinks')
    if path.exists() and not path.is_file():
        raise InstanceLockUnsafeError('Lock path must be a regular file')
    _prepare_directory(path.parent)

    for _ in range(IDENTITY_ATTEMPT_LIMIT):
        descriptor = _open_locked_descriptor(path)
        if after_lock is not None:
            after_lock()
        if _holds_current_path(descriptor, path):
            return InstanceLock(descriptor, path)
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
    raise InstanceLockUnsafeError(
        'Lock file identity keeps changing under the watcher'
    )
