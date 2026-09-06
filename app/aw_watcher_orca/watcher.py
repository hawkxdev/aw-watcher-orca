"""Run Orca watcher service."""

import argparse
import logging
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from io import TextIOWrapper
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final

from aw_watcher_orca.activitywatch import select_fresh_bucket_pair
from aw_watcher_orca.activitywatch_reader import (
    read_activitywatch_buckets,
    read_last_bucket_event,
)
from aw_watcher_orca.bucket_target import (
    BUCKET_TARGET_PROFILES,
    BucketTargetProfile,
    ConfirmedBucketTarget,
    resolve_bucket_target,
)
from aw_watcher_orca.cli_resolver import (
    parse_active_worktree,
    run_orca_worktree_ps,
)
from aw_watcher_orca.errors import ActivityWatchDiscoveryError, OrcaCoreError
from aw_watcher_orca.foreground import (
    DEFAULT_MAX_FOREGROUND_EVENT_AGE,
    ORCA_APP_NAME,
    is_orca_foreground,
)
from aw_watcher_orca.instance_lock import (
    InstanceLock,
    InstanceLockError,
    acquire_instance_lock,
    path_uses_symlink,
)
from aw_watcher_orca.models import ProjectAttribution
from aw_watcher_orca.publisher import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_PULSE_TIME_SECONDS,
    build_active_event_data,
    build_heartbeat_payload,
    build_neutral_event_data,
    create_bucket,
    generate_session_token,
    send_heartbeat,
)
from aw_watcher_orca.stability import ActiveProjectStabilizer
from aw_watcher_orca.trigger import (
    discover_profile_state_file,
    read_active_worktree_trigger,
)

# === Logger ===


DEFAULT_LOG_MAX_BYTES: Final = 1_048_576
DEFAULT_LOG_BACKUP_COUNT: Final = 3
LOG_DIRECTORY_MODE: Final = 0o700
LOG_FILE_MODE: Final = 0o600
LOG_FORMAT: Final = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'

logger = logging.getLogger(__name__)


class PrivateRotatingFileHandler(RotatingFileHandler):
    """Maintain private rotating logs."""

    def _open(self) -> TextIOWrapper:
        """Open private log stream."""
        stream = super()._open()
        Path(self.baseFilename).chmod(LOG_FILE_MODE)
        return stream


def build_log_handler(
    log_file: Path,
    max_bytes: int = DEFAULT_LOG_MAX_BYTES,
    backup_count: int = DEFAULT_LOG_BACKUP_COUNT,
) -> RotatingFileHandler:
    """Build private rotating handler."""
    if (
        type(max_bytes) is not int
        or max_bytes <= 0
        or type(backup_count) is not int
        or backup_count <= 0
    ):
        raise ValueError('rotation limits must be positive integers')
    if path_uses_symlink(log_file):
        raise ValueError('log path cannot use symlinks')
    if log_file.exists() and not log_file.is_file():
        raise ValueError('log file path must be regular')
    log_directory_exists = log_file.parent.exists()
    if log_directory_exists and not log_file.parent.is_dir():
        raise ValueError('log directory unavailable')
    if not log_directory_exists:
        log_file.parent.mkdir(
            mode=LOG_DIRECTORY_MODE,
            parents=True,
        )
        log_file.parent.chmod(LOG_DIRECTORY_MODE)
    handler = PrivateRotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8',
    )
    log_file.chmod(LOG_FILE_MODE)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    return handler


def configure_logging(log_file: Path | None) -> logging.Handler | None:
    """Configure watcher logging."""
    if log_file is None:
        logging.basicConfig(
            level=logging.INFO,
            format=LOG_FORMAT,
            force=True,
        )
        return None
    handler = build_log_handler(log_file)
    logging.basicConfig(
        level=logging.INFO,
        handlers=[handler],
        force=True,
    )
    return handler


def build_argument_parser() -> argparse.ArgumentParser:
    """Build watcher argument parser."""
    parser = argparse.ArgumentParser(prog='aw-watcher-orca')
    parser.add_argument(
        '--mode',
        required=True,
        choices=sorted(BUCKET_TARGET_PROFILES),
    )
    parser.add_argument('--log-file', type=Path)
    return parser


# === Polling loop ===


def run_watcher_loop(
    *,
    profile: BucketTargetProfile,
    lock: InstanceLock | None = None,
    max_iterations: int | None = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    pulse_time: float = DEFAULT_PULSE_TIME_SECONDS,
    max_event_age: timedelta = DEFAULT_MAX_FOREGROUND_EVENT_AGE,
    app_name: str = ORCA_APP_NAME,
    profile_path: Path | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
    bucket_reader: Callable[
        ..., dict[str, object]
    ] = read_activitywatch_buckets,
    event_reader: Callable[
        [str], dict[str, object] | None
    ] = read_last_bucket_event,
    bucket_creator: Callable[
        [BucketTargetProfile, str], ConfirmedBucketTarget
    ] = create_bucket,
    heartbeat_sender: Callable[
        [ConfirmedBucketTarget, Mapping[str, object], float], None
    ] = send_heartbeat,
    cli_runner: Callable[..., dict[str, object]] = run_orca_worktree_ps,
    trigger_reader: Callable[[Path], str] = read_active_worktree_trigger,
    active_logger: logging.Logger | None = None,
) -> int:
    """Execute the watcher loop."""
    log = active_logger or logger
    # 1. Startup phase
    try:
        now = clock()
        buckets = bucket_reader()
        pair = select_fresh_bucket_pair(buckets, now)
        target = bucket_creator(profile, pair.host_suffix)
        state_file = profile_path or discover_profile_state_file()
    except (OrcaCoreError, OSError, ValueError) as exc:
        log.error('Failed to start watcher: %s', type(exc).__name__)
        return 1

    # 2. State initialization
    stabilizer = ActiveProjectStabilizer()
    session_token = generate_session_token()
    last_trigger_id: str | None = None
    is_seeking_stabilization = False
    held_attribution: ProjectAttribution | None = None
    needs_bucket_discovery = False
    iterations = 0

    # 3. Main loop
    while max_iterations is None or iterations < max_iterations:
        if lock is not None and not lock.still_owns_its_path():
            log.error('Instance lock no longer owns its path')
            return 1
        now = clock()
        if needs_bucket_discovery:
            try:
                buckets = bucket_reader()
                pair = select_fresh_bucket_pair(buckets, now)
                target = bucket_creator(profile, pair.host_suffix)
            except (OrcaCoreError, OSError, ValueError) as exc:
                log.warning(
                    'ActivityWatch rediscovery failed: %s',
                    type(exc).__name__,
                )
                iterations += 1
                if max_iterations is None or iterations < max_iterations:
                    try:
                        sleep(poll_interval)
                    except KeyboardInterrupt:
                        return 0
                continue
            needs_bucket_discovery = False
        try:
            # Foreground check
            event = event_reader(pair.window_bucket_id)
            in_foreground = is_orca_foreground(
                event,
                reference_time=now,
                max_age=max_event_age,
                app_name=app_name,
            )
            if not in_foreground:
                neutral_payload = build_heartbeat_payload(
                    now,
                    build_neutral_event_data(session_token, app_name=app_name),
                )
                heartbeat_sender(target, neutral_payload, pulse_time)
                iterations += 1
                if max_iterations is None or iterations < max_iterations:
                    sleep(poll_interval)
                continue

            # Read trigger id
            trigger_id = trigger_reader(state_file)
            if trigger_id != last_trigger_id:
                last_trigger_id = trigger_id
                is_seeking_stabilization = True

            # Resolve CLI if seeking stabilization
            if is_seeking_stabilization:
                raw_cli = cli_runner()
                cli_wt_id, candidate = parse_active_worktree(raw_cli)
                emitted = stabilizer.observe(cli_wt_id, candidate)
                if emitted is not None:
                    held_attribution = emitted
                    is_seeking_stabilization = False

            # Publish heartbeat
            if held_attribution is not None:
                active_payload = build_heartbeat_payload(
                    now,
                    build_active_event_data(
                        held_attribution,
                        session_token,
                        app_name=app_name,
                    ),
                )
                heartbeat_sender(target, active_payload, pulse_time)
            else:
                neutral_payload = build_heartbeat_payload(
                    now,
                    build_neutral_event_data(session_token, app_name=app_name),
                )
                heartbeat_sender(target, neutral_payload, pulse_time)

        except (OrcaCoreError, OSError, ValueError) as exc:
            log.warning('Watcher tick failed: %s', type(exc).__name__)
            stabilizer = ActiveProjectStabilizer()
            held_attribution = None
            is_seeking_stabilization = True
            if isinstance(exc, ActivityWatchDiscoveryError):
                needs_bucket_discovery = True
            try:
                neutral_payload = build_heartbeat_payload(
                    now,
                    build_neutral_event_data(session_token, app_name=app_name),
                )
                heartbeat_sender(target, neutral_payload, pulse_time)
            except (OrcaCoreError, OSError):
                pass
        except KeyboardInterrupt:
            return 0

        iterations += 1
        if max_iterations is None or iterations < max_iterations:
            try:
                sleep(poll_interval)
            except KeyboardInterrupt:
                return 0

    return 0


# === Entry point ===


def main(
    argv: Sequence[str] | None = None,
    *,
    lock_path: Path | None = None,
    lock_acquirer: Callable[
        [Path | None], InstanceLock
    ] = acquire_instance_lock,
) -> int:
    """Run the watcher application."""
    arguments = build_argument_parser().parse_args(argv)
    configure_logging(arguments.log_file)
    profile = resolve_bucket_target(arguments.mode)
    try:
        lock = lock_acquirer(lock_path)
    except InstanceLockError as exc:
        logger.error('Watcher is already running: %s', type(exc).__name__)
        return 1
    try:
        logger.info('Starting aw-watcher-orca in %s mode', profile.mode)
        return run_watcher_loop(profile=profile, lock=lock)
    finally:
        lock.release()


if __name__ == '__main__':
    sys.exit(main())
