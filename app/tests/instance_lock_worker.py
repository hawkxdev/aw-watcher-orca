"""Acquire the watcher instance lock as an independent process.

Run as a script, never imported: a spawned child has no pytest, no injected
``pythonpath`` and no test import machinery, so the worker resolves the package
itself and exits with a code the parent can assert on.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aw_watcher_orca.instance_lock import (  # noqa: E402
    InstanceLockError,
    acquire_instance_lock,
)

GATE_TIMEOUT_SECONDS = 60.0
POLL_SECONDS = 0.02


def try_once(lock_path: Path) -> int:
    """Attempt one acquisition and report the outcome."""
    try:
        lock = acquire_instance_lock(lock_path)
    except InstanceLockError:
        print('REFUSED')
        return 3
    lock.release()
    print('ACQUIRED')
    return 0


def hold(lock_path: Path, ready_path: Path, release_path: Path) -> int:
    """Hold the lock until the release gate appears."""
    try:
        lock = acquire_instance_lock(lock_path)
    except InstanceLockError:
        print('REFUSED')
        return 3
    try:
        ready_path.touch()
        deadline = time.monotonic() + GATE_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if release_path.exists():
                print('RELEASED')
                return 0
            time.sleep(POLL_SECONDS)
        print('TIMEOUT')
        return 4
    finally:
        lock.release()


def hold_forever(lock_path: Path, ready_path: Path) -> int:
    """Hold the lock without ever releasing it, to be killed by a signal."""
    try:
        acquire_instance_lock(lock_path)
    except InstanceLockError:
        print('REFUSED', flush=True)
        return 3
    ready_path.touch()
    while True:
        time.sleep(POLL_SECONDS)


def main(argv: list[str]) -> int:
    """Dispatch the requested worker mode."""
    lock_path = Path(argv[1])
    mode = argv[2]
    if mode == 'try':
        return try_once(lock_path)
    if mode == 'hold':
        return hold(lock_path, Path(argv[3]), Path(argv[4]))
    if mode == 'hold-forever':
        return hold_forever(lock_path, Path(argv[3]))
    print('UNKNOWN-MODE')
    return 5


if __name__ == '__main__':
    sys.exit(main(sys.argv))
