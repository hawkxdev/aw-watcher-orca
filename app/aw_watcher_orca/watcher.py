"""Run Orca watcher service."""

import logging
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aw_watcher_orca.activitywatch import select_fresh_bucket_pair
from aw_watcher_orca.activitywatch_reader import (
    read_activitywatch_buckets,
    read_last_bucket_event,
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
from aw_watcher_orca.models import ProjectAttribution
from aw_watcher_orca.publisher import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_PULSE_TIME_SECONDS,
    build_active_event_data,
    build_heartbeat_payload,
    build_neutral_event_data,
    build_test_bucket_id,
    create_test_bucket,
    generate_session_token,
    send_heartbeat,
)
from aw_watcher_orca.stability import ActiveProjectStabilizer
from aw_watcher_orca.trigger import (
    discover_profile_state_file,
    read_active_worktree_trigger,
)

# === Logger ===


logger = logging.getLogger(__name__)


# === Polling loop ===


def run_watcher_loop(
    *,
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
    bucket_creator: Callable[[str], None] = create_test_bucket,
    heartbeat_sender: Callable[
        [str, Mapping[str, object], float], None
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
        bucket_creator(pair.host_suffix)
        test_bucket_id = build_test_bucket_id(pair.host_suffix)
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
        now = clock()
        if needs_bucket_discovery:
            try:
                buckets = bucket_reader()
                pair = select_fresh_bucket_pair(buckets, now)
                bucket_creator(pair.host_suffix)
                test_bucket_id = build_test_bucket_id(pair.host_suffix)
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
                heartbeat_sender(test_bucket_id, neutral_payload, pulse_time)
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
                heartbeat_sender(test_bucket_id, active_payload, pulse_time)
            else:
                neutral_payload = build_heartbeat_payload(
                    now,
                    build_neutral_event_data(session_token, app_name=app_name),
                )
                heartbeat_sender(test_bucket_id, neutral_payload, pulse_time)

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
                heartbeat_sender(test_bucket_id, neutral_payload, pulse_time)
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


def main(argv: Sequence[str] | None = None) -> int:
    """Run the watcher application."""
    del argv
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    )
    logger.info('Starting aw-watcher-orca in test bucket mode')
    return run_watcher_loop()


if __name__ == '__main__':
    sys.exit(main())
