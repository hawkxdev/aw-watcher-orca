"""Manage user LaunchAgent lifecycle."""

import argparse
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# === Constants ===


LABEL: Final = 'io.github.hawkxdev.aw-watcher-orca'
PLIST_FILENAME: Final = f'{LABEL}.plist'
LOG_DIRECTORY_NAME: Final = 'aw-watcher-orca'
WATCHER_LOG_FILENAME: Final = 'watcher.log'
LAUNCHER_LOG_FILENAME: Final = 'launcher.log'
LAUNCHCTL_PATH: Final = Path('/bin/launchctl')
PLUTIL_PATH: Final = Path('/usr/bin/plutil')
ORCA_PATH: Final = Path('/usr/local/bin/orca')
DIRECTORY_MODE: Final = 0o700
FILE_MODE: Final = 0o600
THROTTLE_INTERVAL_SECONDS: Final = 10
COMMAND_TIMEOUT_SECONDS: Final = 10.0
SERVICE_NOT_FOUND_EXIT_CODE: Final = 113
DEFAULT_POLL_ATTEMPTS: Final = 20
DEFAULT_POLL_INTERVAL_SECONDS: Final = 0.1

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
Sleeper = Callable[[float], None]


# === Errors ===


class LaunchAgentError(Exception):
    """Represent manager failures."""


class LaunchAgentValidationError(LaunchAgentError):
    """Represent invalid local state."""


class LaunchAgentCommandError(LaunchAgentError):
    """Represent failed system commands."""


class LaunchAgentRollbackError(LaunchAgentError):
    """Represent failed state restoration."""


# === Models ===


@dataclass(frozen=True, slots=True)
class LaunchAgentPaths:
    """Hold resolved manager paths."""

    project_root: Path
    home_dir: Path
    app_dir: Path
    python_path: Path
    launchctl_path: Path
    plutil_path: Path
    orca_path: Path
    launch_agents_dir: Path
    plist_path: Path
    log_dir: Path
    watcher_log_path: Path
    launcher_log_path: Path


@dataclass(frozen=True, slots=True)
class LaunchAgentStatus:
    """Hold sanitized service status."""

    loaded: bool
    plist_exists: bool


# === Path contract ===


def build_paths(
    project_root: Path | None = None,
    home_dir: Path | None = None,
    launchctl_path: Path = LAUNCHCTL_PATH,
    plutil_path: Path = PLUTIL_PATH,
    orca_path: Path = ORCA_PATH,
) -> LaunchAgentPaths:
    """Build resolved manager paths."""
    resolved_root = (
        project_root or Path(__file__).resolve().parents[1]
    ).resolve()
    resolved_home = (home_dir or Path.home()).resolve()
    app_dir = resolved_root / 'app'
    launch_agents_dir = resolved_home / 'Library' / 'LaunchAgents'
    log_dir = resolved_home / 'Library' / 'Logs' / LOG_DIRECTORY_NAME
    return LaunchAgentPaths(
        project_root=resolved_root,
        home_dir=resolved_home,
        app_dir=app_dir,
        python_path=app_dir / '.venv' / 'bin' / 'python',
        launchctl_path=launchctl_path.resolve(),
        plutil_path=plutil_path.resolve(),
        orca_path=orca_path.resolve(),
        launch_agents_dir=launch_agents_dir,
        plist_path=launch_agents_dir / PLIST_FILENAME,
        log_dir=log_dir,
        watcher_log_path=log_dir / WATCHER_LOG_FILENAME,
        launcher_log_path=log_dir / LAUNCHER_LOG_FILENAME,
    )


def domain_target(uid: int) -> str:
    """Build user domain target."""
    return f'gui/{uid}'


def service_target(uid: int) -> str:
    """Build exact service target."""
    return f'{domain_target(uid)}/{LABEL}'


# === Plist contract ===


def build_plist(paths: LaunchAgentPaths) -> dict[str, object]:
    """Build LaunchAgent plist payload."""
    return {
        'KeepAlive': True,
        'Label': LABEL,
        'ProcessType': 'Background',
        'ProgramArguments': [
            str(paths.python_path),
            '-m',
            'aw_watcher_orca',
            '--log-file',
            str(paths.watcher_log_path),
        ],
        'StandardErrorPath': str(paths.launcher_log_path),
        'ThrottleInterval': THROTTLE_INTERVAL_SECONDS,
        'Umask': '077',
        'WorkingDirectory': str(paths.app_dir),
    }


def render_plist(paths: LaunchAgentPaths) -> bytes:
    """Render LaunchAgent plist bytes."""
    return plistlib.dumps(
        build_plist(paths),
        fmt=plistlib.FMT_XML,
        sort_keys=True,
    )


# === Process boundary ===


def _run_command(
    arguments: Sequence[str],
    runner: CommandRunner,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one bounded command."""
    try:
        return runner(
            list(arguments),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        raise LaunchAgentCommandError('System command timed out') from None
    except OSError:
        raise LaunchAgentCommandError('System command unavailable') from None


def _require_success(
    result: subprocess.CompletedProcess[str],
    message: str,
) -> None:
    """Require successful command result."""
    if result.returncode != 0:
        raise LaunchAgentCommandError(message)


def _ensure_user_domain(
    paths: LaunchAgentPaths,
    uid: int,
    runner: CommandRunner,
) -> None:
    """Validate launchctl user domain."""
    result = _run_command(
        [str(paths.launchctl_path), 'print', domain_target(uid)],
        runner,
    )
    _require_success(result, 'launchctl user domain unavailable')


def get_status(
    paths: LaunchAgentPaths,
    uid: int,
    runner: CommandRunner = subprocess.run,
) -> LaunchAgentStatus:
    """Read sanitized service status."""
    _ensure_user_domain(paths, uid, runner)
    service_result = _run_command(
        [str(paths.launchctl_path), 'print', service_target(uid)],
        runner,
    )
    if service_result.returncode not in {0, SERVICE_NOT_FOUND_EXIT_CODE}:
        raise LaunchAgentCommandError('launchctl service status failed')
    return LaunchAgentStatus(
        loaded=service_result.returncode == 0,
        plist_exists=paths.plist_path.exists(),
    )


def _wait_for_service_removal(
    paths: LaunchAgentPaths,
    uid: int,
    runner: CommandRunner,
    sleeper: Sleeper,
    poll_attempts: int,
    poll_interval: float,
) -> None:
    """Wait for service removal."""
    for attempt in range(poll_attempts):
        if not get_status(paths, uid, runner).loaded:
            return
        if attempt + 1 < poll_attempts:
            sleeper(poll_interval)
    raise LaunchAgentCommandError('LaunchAgent remained loaded after bootout')


def _validate_poll_attempts(poll_attempts: int) -> None:
    """Validate polling attempt count."""
    if poll_attempts < 1:
        raise ValueError('poll_attempts must be positive')


# === Installation preflight ===


def _validate_executable(path: Path, label: str) -> None:
    """Validate executable file path."""
    if not path.is_file() or not os.access(path, os.X_OK):
        raise LaunchAgentValidationError(f'{label} executable unavailable')


def _path_uses_symlink(path: Path) -> bool:
    """Detect symlink path components."""
    return any(candidate.is_symlink() for candidate in (path, *path.parents))


def _validate_target(paths: LaunchAgentPaths) -> None:
    """Validate managed target paths."""
    if _path_uses_symlink(paths.plist_path):
        raise LaunchAgentValidationError(
            'LaunchAgent plist cannot be a symlink'
        )
    if _path_uses_symlink(paths.log_dir):
        raise LaunchAgentValidationError(
            'LaunchAgent log directory cannot be a symlink'
        )


def validate_installation(
    paths: LaunchAgentPaths,
    uid: int,
    runner: CommandRunner = subprocess.run,
) -> None:
    """Validate installation prerequisites."""
    _validate_target(paths)
    if not paths.project_root.is_absolute() or not paths.app_dir.is_dir():
        raise LaunchAgentValidationError('Application directory unavailable')
    if not paths.launch_agents_dir.is_dir():
        raise LaunchAgentValidationError('LaunchAgents directory unavailable')
    _validate_executable(paths.python_path, 'Python')
    _validate_executable(paths.orca_path, 'Orca')
    _validate_executable(paths.launchctl_path, 'launchctl')
    _validate_executable(paths.plutil_path, 'plutil')
    _ensure_user_domain(paths, uid, runner)
    import_result = _run_command(
        [str(paths.python_path), '-c', 'import aw_watcher_orca'],
        runner,
        cwd=paths.app_dir,
    )
    _require_success(import_result, 'Python import check failed')


# === Private file writes ===


def _write_private_temp(
    directory: Path,
    prefix: str,
    data: bytes,
) -> Path:
    """Write private temporary plist."""
    descriptor, raw_path = tempfile.mkstemp(prefix=prefix, dir=directory)
    temp_path = Path(raw_path)
    try:
        os.fchmod(descriptor, FILE_MODE)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        with suppress(OSError):
            os.close(descriptor)
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path


def _atomic_write(path: Path, data: bytes, mode: int = FILE_MODE) -> None:
    """Replace one private file."""
    temp_path = _write_private_temp(path.parent, f'.{path.name}.', data)
    try:
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


# === Installation lifecycle ===


def _restore_previous_service(
    paths: LaunchAgentPaths,
    uid: int,
    previous_bytes: bytes | None,
    previous_mode: int,
    was_loaded: bool,
    runner: CommandRunner,
) -> None:
    """Restore previous service state."""
    try:
        if previous_bytes is None:
            paths.plist_path.unlink(missing_ok=True)
        else:
            _atomic_write(paths.plist_path, previous_bytes, previous_mode)
        if was_loaded:
            reload_result = _run_command(
                [
                    str(paths.launchctl_path),
                    'bootstrap',
                    domain_target(uid),
                    str(paths.plist_path),
                ],
                runner,
            )
            if reload_result.returncode != 0:
                raise LaunchAgentRollbackError(
                    'Previous LaunchAgent reload failed'
                )
    except LaunchAgentRollbackError:
        raise
    except (OSError, LaunchAgentCommandError):
        raise LaunchAgentRollbackError(
            'Previous LaunchAgent restoration failed'
        ) from None


def install(
    paths: LaunchAgentPaths,
    uid: int,
    runner: CommandRunner = subprocess.run,
    sleeper: Sleeper = time.sleep,
    poll_attempts: int = DEFAULT_POLL_ATTEMPTS,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> None:
    """Install one user LaunchAgent."""
    _validate_poll_attempts(poll_attempts)
    # 1. Validate candidate
    validate_installation(paths, uid, runner)
    rendered = render_plist(paths)
    paths.log_dir.mkdir(mode=DIRECTORY_MODE, parents=True, exist_ok=True)
    paths.log_dir.chmod(DIRECTORY_MODE)
    candidate_path = _write_private_temp(
        paths.launch_agents_dir,
        f'.{PLIST_FILENAME}.',
        rendered,
    )
    try:
        lint_result = _run_command(
            [str(paths.plutil_path), '-lint', str(candidate_path)],
            runner,
        )
        _require_success(lint_result, 'plutil validation failed')
        # 2. Capture previous state
        status = get_status(paths, uid, runner)
        previous_bytes = (
            paths.plist_path.read_bytes() if status.plist_exists else None
        )
        previous_mode = (
            paths.plist_path.stat().st_mode & 0o777
            if status.plist_exists
            else FILE_MODE
        )
        if status.loaded and previous_bytes is None:
            raise LaunchAgentValidationError(
                'Loaded LaunchAgent has no managed plist'
            )
        # 3. Stop previous service
        if status.loaded:
            bootout_result = _run_command(
                [
                    str(paths.launchctl_path),
                    'bootout',
                    service_target(uid),
                ],
                runner,
            )
            _require_success(bootout_result, 'launchctl bootout failed')
            _wait_for_service_removal(
                paths,
                uid,
                runner,
                sleeper,
                poll_attempts,
                poll_interval,
            )
        # 4. Replace and bootstrap
        try:
            os.replace(candidate_path, paths.plist_path)
            paths.plist_path.chmod(FILE_MODE)
            bootstrap_result = _run_command(
                [
                    str(paths.launchctl_path),
                    'bootstrap',
                    domain_target(uid),
                    str(paths.plist_path),
                ],
                runner,
            )
            _require_success(bootstrap_result, 'launchctl bootstrap failed')
        except (OSError, LaunchAgentCommandError):
            _restore_previous_service(
                paths,
                uid,
                previous_bytes,
                previous_mode,
                status.loaded,
                runner,
            )
            raise
    finally:
        candidate_path.unlink(missing_ok=True)


def uninstall(
    paths: LaunchAgentPaths,
    uid: int,
    runner: CommandRunner = subprocess.run,
    sleeper: Sleeper = time.sleep,
    poll_attempts: int = DEFAULT_POLL_ATTEMPTS,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> None:
    """Uninstall one user LaunchAgent."""
    _validate_poll_attempts(poll_attempts)
    _validate_target(paths)
    _validate_executable(paths.launchctl_path, 'launchctl')
    # 1. Stop loaded service
    status = get_status(paths, uid, runner)
    if status.loaded:
        bootout_result = _run_command(
            [
                str(paths.launchctl_path),
                'bootout',
                service_target(uid),
            ],
            runner,
        )
        _require_success(bootout_result, 'launchctl bootout failed')
        # 2. Confirm bounded removal
        _wait_for_service_removal(
            paths,
            uid,
            runner,
            sleeper,
            poll_attempts,
            poll_interval,
        )
    # 3. Remove managed plist
    paths.plist_path.unlink(missing_ok=True)


# === Command interface ===


def status_payload(status: LaunchAgentStatus) -> dict[str, object]:
    """Build sanitized status payload."""
    return {
        'label': LABEL,
        'loaded': status.loaded,
        'plist_exists': status.plist_exists,
        'bucket_mode': 'test',
    }


def build_argument_parser() -> argparse.ArgumentParser:
    """Build manager argument parser."""
    parser = argparse.ArgumentParser(prog='launch-agent')
    subparsers = parser.add_subparsers(dest='command', required=True)
    subparsers.add_parser('render')
    subparsers.add_parser('status')
    subparsers.add_parser('install')
    subparsers.add_parser('uninstall')
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run LaunchAgent manager command."""
    arguments = build_argument_parser().parse_args(argv)
    try:
        paths = build_paths()
        uid = os.getuid()
        if arguments.command == 'render':
            sys.stdout.buffer.write(render_plist(paths))
            return 0
        if arguments.command == 'status':
            print(json.dumps(status_payload(get_status(paths, uid))))
            return 0
        if arguments.command == 'install':
            install(paths, uid)
        elif arguments.command == 'uninstall':
            uninstall(paths, uid)
    except OSError:
        print(
            json.dumps(
                {
                    'ok': False,
                    'error': 'LaunchAgentCommandError',
                    'message': 'Local filesystem operation failed',
                }
            ),
            file=sys.stderr,
        )
        return 2
    except LaunchAgentError as exc:
        print(
            json.dumps(
                {
                    'ok': False,
                    'error': type(exc).__name__,
                    'message': str(exc),
                }
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps({'ok': True, 'command': arguments.command}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
