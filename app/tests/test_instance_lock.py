"""Test the single-instance watcher lock."""

import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from aw_watcher_orca.instance_lock import (
    LOCK_DIRECTORY_MODE,
    LOCK_FILE_MODE,
    InstanceLockUnavailableError,
    InstanceLockUnsafeError,
    acquire_instance_lock,
    default_lock_path,
    path_uses_symlink,
)

# === Constants ===


WORKER = Path(__file__).parent / 'instance_lock_worker.py'
GATE_TIMEOUT_SECONDS = 20.0


# === Helpers ===


def wait_for(condition: Callable[[], bool]) -> bool:
    """Wait for one condition within the gate timeout."""
    deadline = time.monotonic() + GATE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def run_worker(
    lock_path: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    """Run the lock worker as an independent process."""
    return subprocess.run(  # noqa: S603 (fixed interpreter, no shell)
        [sys.executable, str(WORKER), str(lock_path), *arguments],
        capture_output=True,
        text=True,
        timeout=GATE_TIMEOUT_SECONDS,
        check=False,
    )


# === Acquisition ===


def test_acquire_creates_private_directory_and_file(tmp_path: Path) -> None:
    lock_path = tmp_path / 'state' / 'watcher.lock'

    lock = acquire_instance_lock(lock_path)
    try:
        assert lock_path.is_file()
        assert lock_path.stat().st_mode & 0o777 == LOCK_FILE_MODE
        assert lock_path.parent.stat().st_mode & 0o777 == LOCK_DIRECTORY_MODE
    finally:
        lock.release()


def test_release_keeps_the_lock_file_on_disk(tmp_path: Path) -> None:
    lock_path = tmp_path / 'watcher.lock'

    acquire_instance_lock(lock_path).release()

    assert lock_path.is_file()


def test_second_acquisition_in_the_same_process_is_refused(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / 'watcher.lock'
    lock = acquire_instance_lock(lock_path)
    try:
        with pytest.raises(InstanceLockUnavailableError):
            acquire_instance_lock(lock_path)
    finally:
        lock.release()


def test_lock_is_reacquirable_after_release(tmp_path: Path) -> None:
    lock_path = tmp_path / 'watcher.lock'

    acquire_instance_lock(lock_path).release()
    second = acquire_instance_lock(lock_path)
    second.release()


def test_default_lock_path_is_outside_the_log_directory() -> None:
    path = default_lock_path()

    assert path.name == 'watcher.lock'
    assert 'Logs' not in path.parts


# === Unsafe paths ===


def test_symlinked_lock_file_is_refused(tmp_path: Path) -> None:
    real = tmp_path / 'real.lock'
    real.touch()
    link = tmp_path / 'watcher.lock'
    link.symlink_to(real)

    with pytest.raises(InstanceLockUnsafeError):
        acquire_instance_lock(link)


def test_symlinked_parent_directory_is_refused(tmp_path: Path) -> None:
    real_directory = tmp_path / 'real'
    real_directory.mkdir()
    linked_directory = tmp_path / 'linked'
    linked_directory.symlink_to(real_directory)

    with pytest.raises(InstanceLockUnsafeError):
        acquire_instance_lock(linked_directory / 'watcher.lock')


def test_path_uses_symlink_reports_a_linked_component(tmp_path: Path) -> None:
    real_directory = tmp_path / 'real'
    real_directory.mkdir()
    linked_directory = tmp_path / 'linked'
    linked_directory.symlink_to(real_directory)

    assert path_uses_symlink(linked_directory / 'watcher.lock')
    assert not path_uses_symlink(real_directory / 'watcher.lock')


def test_lock_path_must_be_a_regular_file(tmp_path: Path) -> None:
    directory_path = tmp_path / 'watcher.lock'
    directory_path.mkdir()

    with pytest.raises(InstanceLockUnsafeError):
        acquire_instance_lock(directory_path)


# === Identity recheck after acquisition ===


def test_replaced_file_is_detected_and_retried(tmp_path: Path) -> None:
    lock_path = tmp_path / 'watcher.lock'
    replacements = 0

    def replace_once() -> None:
        """Replace the locked file exactly once."""
        nonlocal replacements
        if replacements == 0:
            replacements += 1
            lock_path.unlink()
            lock_path.touch()

    lock = acquire_instance_lock(lock_path, after_lock=replace_once)
    try:
        assert replacements == 1
        held = os.fstat(lock.fileno())
        current = lock_path.stat()
        assert (held.st_dev, held.st_ino) == (current.st_dev, current.st_ino)
    finally:
        lock.release()


def test_persistently_replaced_file_refuses_the_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / 'watcher.lock'
    attempts = 0

    def replace_always() -> None:
        """Replace the locked file on every attempt."""
        nonlocal attempts
        attempts += 1
        lock_path.unlink()
        lock_path.touch()

    with pytest.raises(InstanceLockUnsafeError):
        acquire_instance_lock(lock_path, after_lock=replace_always)

    assert attempts > 1


def test_removed_file_is_recreated_on_retry(tmp_path: Path) -> None:
    lock_path = tmp_path / 'watcher.lock'
    removals = 0

    def remove_once() -> None:
        """Remove the locked file exactly once."""
        nonlocal removals
        if removals == 0:
            removals += 1
            lock_path.unlink()

    lock = acquire_instance_lock(lock_path, after_lock=remove_once)
    try:
        assert removals == 1
        assert lock_path.is_file()
    finally:
        lock.release()


# === Independent processes ===


def test_competing_process_is_refused_and_lock_survives_it(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / 'watcher.lock'
    holder_ready = tmp_path / 'holder-ready'
    holder_release = tmp_path / 'holder-release'

    with subprocess.Popen(  # noqa: S603 (fixed interpreter, no shell)
        [
            sys.executable,
            str(WORKER),
            str(lock_path),
            'hold',
            str(holder_ready),
            str(holder_release),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as holder:
        try:
            assert wait_for(holder_ready.exists), 'holder did not lock'

            competitor = run_worker(lock_path, 'try')

            assert competitor.stdout.strip() == 'REFUSED'
            assert competitor.returncode != 0
        finally:
            holder_release.touch()
            holder.wait(timeout=GATE_TIMEOUT_SECONDS)

    assert holder.returncode == 0


def test_lock_is_free_after_the_holder_process_exits(tmp_path: Path) -> None:
    lock_path = tmp_path / 'watcher.lock'
    holder_ready = tmp_path / 'holder-ready'
    holder_release = tmp_path / 'holder-release'

    with subprocess.Popen(  # noqa: S603 (fixed interpreter, no shell)
        [
            sys.executable,
            str(WORKER),
            str(lock_path),
            'hold',
            str(holder_ready),
            str(holder_release),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as holder:
        assert wait_for(holder_ready.exists), 'holder did not lock'
        holder_release.touch()
        holder.wait(timeout=GATE_TIMEOUT_SECONDS)

    successor = run_worker(lock_path, 'try')

    assert successor.stdout.strip() == 'ACQUIRED'
    assert successor.returncode == 0
