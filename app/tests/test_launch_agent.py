"""Test LaunchAgent lifecycle manager."""

import fcntl
import json
import os
import plistlib
import subprocess
import sys
import threading
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

import launch_agent as manager
from aw_watcher_orca.instance_lock import acquire_instance_lock

# === Fakes ===


DOMAIN_STATUS = 'domain status'
SERVICE_STATUS = 'service status'
PYTHON_IMPORT = 'python import'
PLIST_LINT = 'plist lint'
BOOTOUT = 'bootout'
BOOTSTRAP = 'bootstrap'


def _command_kind(arguments: list[str]) -> str:
    """Classify one fake command."""
    executable = Path(arguments[0]).name
    action = arguments[1]
    if executable == 'launchctl' and action == 'print':
        target = arguments[2]
        return SERVICE_STATUS if target.count('/') > 1 else DOMAIN_STATUS
    if executable == 'python' and action == '-c':
        return PYTHON_IMPORT
    if executable == 'plutil' and action == '-lint':
        return PLIST_LINT
    if executable == 'launchctl' and action in {BOOTOUT, BOOTSTRAP}:
        return action
    return f'{executable} {action}'


class RecordingRunner:
    """Record fake command execution."""

    def __init__(self, responses: list[tuple[str, int]]) -> None:
        """Initialize fake command runner."""
        self.responses = responses.copy()
        self.calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    def __call__(
        self,
        arguments: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        """Return queued command result."""
        if not self.responses:
            raise AssertionError('Unexpected command execution')
        expected_kind, returncode = self.responses.pop(0)
        actual_kind = _command_kind(arguments)
        if actual_kind != expected_kind:
            raise AssertionError(
                f'Expected {expected_kind}, received {actual_kind}'
            )
        self.calls.append((tuple(arguments), kwargs))
        return subprocess.CompletedProcess(
            arguments,
            returncode,
            stdout='',
            stderr='secret /Users/private branch prompt',
        )

    def assert_complete(self) -> None:
        """Require consumed fake responses."""
        assert self.responses == []


class EffectRunner:
    """Run one side effect when a command kind is observed."""

    def __init__(
        self,
        inner: RecordingRunner,
        trigger: str,
        effect: Callable[[], None],
    ) -> None:
        """Store the inner runner, trigger kind, and effect."""
        self.inner = inner
        self.trigger = trigger
        self.effect = effect

    def __call__(
        self,
        arguments: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        """Trigger the effect before delegating to the inner runner."""
        if _command_kind(list(arguments)) == self.trigger:
            self.effect()
        return self.inner(arguments, **kwargs)


def _hold_flock(path: Path) -> int:
    """Hold a real inter-process flock on the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return descriptor


def _release_flock(descriptor: int) -> None:
    """Release one held flock descriptor, tolerating double release."""
    with suppress(OSError):
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    with suppress(OSError):
        os.close(descriptor)


def test_recording_runner_rejects_wrong_command(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    runner = RecordingRunner([(DOMAIN_STATUS, 0)])

    with pytest.raises(
        AssertionError,
        match='Expected domain status, received service status',
    ):
        runner(
            [
                str(paths.launchctl_path),
                'print',
                manager.service_target(501),
            ]
        )


def test_recording_runner_reports_unused_response() -> None:
    runner = RecordingRunner([(DOMAIN_STATUS, 0)])

    with pytest.raises(AssertionError):
        runner.assert_complete()


def _manager_paths(tmp_path: Path) -> Any:
    """Build isolated manager paths."""
    project_root = tmp_path / 'Project With Spaces'
    home_dir = tmp_path / 'Home With Spaces'
    launch_agents_dir = home_dir / 'Library' / 'LaunchAgents'
    python_path = project_root / 'app' / '.venv' / 'bin' / 'python'
    launchctl_path = tmp_path / 'bin' / 'launchctl'
    plutil_path = tmp_path / 'bin' / 'plutil'
    orca_path = tmp_path / 'bin' / 'orca'
    launch_agents_dir.mkdir(parents=True)
    for executable in (python_path, launchctl_path, plutil_path, orca_path):
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text('', encoding='utf-8')
        executable.chmod(0o700)
    return manager.build_paths(
        project_root=project_root,
        home_dir=home_dir,
        launchctl_path=launchctl_path,
        plutil_path=plutil_path,
        orca_path=orca_path,
    )


# === Plist contract ===


def test_build_paths_supports_spaces(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)

    assert paths.project_root.name == 'Project With Spaces'
    assert paths.python_path == (
        paths.project_root / 'app' / '.venv' / 'bin' / 'python'
    )
    assert paths.plist_path == (
        paths.home_dir
        / 'Library'
        / 'LaunchAgents'
        / 'io.github.hawkxdev.aw-watcher-orca.plist'
    )


def test_build_plist_has_exact_runtime_contract(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)

    payload = manager.build_plist(paths, 'test')

    assert payload == {
        'KeepAlive': True,
        'Label': 'io.github.hawkxdev.aw-watcher-orca',
        'ProcessType': 'Background',
        'ProgramArguments': [
            str(paths.python_path),
            '-m',
            'aw_watcher_orca',
            '--mode',
            'test',
            '--log-file',
            str(paths.watcher_log_path),
        ],
        'StandardErrorPath': str(paths.launcher_log_path),
        'ThrottleInterval': 10,
        'Umask': '077',
        'WorkingDirectory': str(paths.app_dir),
    }


def test_build_plist_rejects_unknown_mode(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)

    with pytest.raises(
        manager.LaunchAgentValidationError,
        match='Bucket run mode is unknown',
    ):
        manager.build_plist(paths, 'staging')


def test_build_plist_differs_only_in_mode_argument(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)

    test_payload = manager.build_plist(paths, 'test')
    production_payload = manager.build_plist(paths, 'production')

    for key in test_payload:
        if key == 'ProgramArguments':
            continue
        assert test_payload[key] == production_payload[key]
    test_arguments = test_payload['ProgramArguments']
    production_arguments = production_payload['ProgramArguments']
    assert isinstance(test_arguments, list)
    assert isinstance(production_arguments, list)
    differing = [
        index
        for index, (left, right) in enumerate(
            zip(test_arguments, production_arguments, strict=True)
        )
        if left != right
    ]
    assert differing == [4]
    assert test_arguments[2] == 'aw_watcher_orca'
    assert test_arguments[3] == '--mode'
    assert test_arguments[4] == 'test'
    assert production_arguments[4] == 'production'


def test_render_plist_round_trips_exact_payload(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)

    rendered = manager.render_plist(paths, 'test')

    assert plistlib.loads(rendered) == manager.build_plist(paths, 'test')


def test_render_plist_excludes_production_configuration(
    tmp_path: Path,
) -> None:
    paths = _manager_paths(tmp_path)

    rendered = manager.render_plist(paths, 'test')

    assert b'aw-watcher-orca_' not in rendered
    assert b'EnvironmentVariables' not in rendered
    assert b'RunAtLoad' not in rendered


# === Status contract ===


def test_status_reports_unloaded_service(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    runner = RecordingRunner([(DOMAIN_STATUS, 0), (SERVICE_STATUS, 113)])

    status = manager.get_status(paths, uid=501, runner=runner)

    assert status.loaded is False
    assert status.plist_exists is False
    assert [call[0] for call in runner.calls] == [
        (str(paths.launchctl_path), 'print', 'gui/501'),
        (
            str(paths.launchctl_path),
            'print',
            'gui/501/io.github.hawkxdev.aw-watcher-orca',
        ),
    ]
    assert all(call[1]['shell'] is False for call in runner.calls)
    runner.assert_complete()


def test_status_reports_loaded_service(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    paths.plist_path.write_bytes(manager.render_plist(paths, 'test'))
    runner = RecordingRunner([(DOMAIN_STATUS, 0), (SERVICE_STATUS, 0)])

    status = manager.get_status(paths, uid=501, runner=runner)

    assert status.loaded is True
    assert status.plist_exists is True
    assert status.mode == 'test'
    runner.assert_complete()


def _foreign_plist_bytes(paths: Any) -> bytes:
    """Build a foreign plist sharing only the managed label."""
    return plistlib.dumps(
        {
            'Label': 'io.github.hawkxdev.aw-watcher-orca',
            'ProgramArguments': [
                '/usr/bin/foreign-python',
                '-m',
                'aw_watcher_orca',
                '--mode',
                'test',
                '--log-file',
                str(paths.watcher_log_path),
            ],
        },
        fmt=plistlib.FMT_XML,
        sort_keys=True,
    )


@pytest.mark.parametrize('mode', ['test', 'production'])
def test_status_detects_installed_mode(tmp_path: Path, mode: str) -> None:
    paths = _manager_paths(tmp_path)
    paths.plist_path.write_bytes(manager.render_plist(paths, mode))
    runner = RecordingRunner([(DOMAIN_STATUS, 0), (SERVICE_STATUS, 113)])

    status = manager.get_status(paths, uid=501, runner=runner)

    assert status.mode == mode
    runner.assert_complete()


def test_status_reports_unknown_mode_for_foreign_plist(
    tmp_path: Path,
) -> None:
    paths = _manager_paths(tmp_path)
    paths.plist_path.write_bytes(_foreign_plist_bytes(paths))
    runner = RecordingRunner([(DOMAIN_STATUS, 0), (SERVICE_STATUS, 113)])

    status = manager.get_status(paths, uid=501, runner=runner)

    assert status.plist_exists is True
    assert status.mode is None
    runner.assert_complete()


@pytest.mark.parametrize(
    'payload',
    [
        b'<xml bytes that are not a plist',
        plistlib.dumps(['unexpected-root-list'], fmt=plistlib.FMT_XML),
        plistlib.dumps(
            {'Label': 'io.github.hawkxdev.aw-watcher-orca'},
            fmt=plistlib.FMT_XML,
        ),
        plistlib.dumps('scalar-root', fmt=plistlib.FMT_XML),
    ],
)
def test_status_reports_unknown_mode_for_unreadable_configuration(
    tmp_path: Path,
    payload: bytes,
) -> None:
    paths = _manager_paths(tmp_path)
    paths.plist_path.write_bytes(payload)
    runner = RecordingRunner([(DOMAIN_STATUS, 0), (SERVICE_STATUS, 113)])

    status = manager.get_status(paths, uid=501, runner=runner)

    assert status.plist_exists is True
    assert status.mode is None
    assert 'secret' not in repr(status)
    runner.assert_complete()


def test_status_handles_fifo_plist_without_blocking(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    os.mkfifo(paths.plist_path)
    runner = RecordingRunner([(DOMAIN_STATUS, 0), (SERVICE_STATUS, 113)])
    detected: list[str | None] = []

    def probe_status() -> None:
        """Read the service status from the fifo plist path."""
        detected.append(manager.get_status(paths, uid=501, runner=runner).mode)

    reader = threading.Thread(target=probe_status, daemon=True)
    reader.start()
    reader.join(timeout=5.0)

    assert not reader.is_alive()
    assert detected == [None]
    assert paths.plist_path.exists()
    runner.assert_complete()


def test_detect_mode_does_not_mask_manager_defects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _manager_paths(tmp_path)
    paths.plist_path.write_bytes(manager.render_plist(paths, 'test'))

    def broken_build_plist(paths: Any, mode: str) -> dict[str, object]:
        """Raise a manager-side defect instead of building a payload."""
        raise ValueError('candidate defect')

    monkeypatch.setattr(manager, 'build_plist', broken_build_plist)

    with pytest.raises(ValueError, match='candidate defect'):
        manager._detect_installed_mode(paths)


def _tampered_payload_bytes(paths: Any, extra_key: str) -> bytes:
    """Build a managed plist payload with one foreign key added."""
    payload = manager.build_plist(paths, 'test')
    payload[extra_key] = True
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)


def test_status_reports_unknown_mode_for_tampered_payload(
    tmp_path: Path,
) -> None:
    paths = _manager_paths(tmp_path)
    paths.plist_path.write_bytes(_tampered_payload_bytes(paths, 'RunAtLoad'))
    runner = RecordingRunner([(DOMAIN_STATUS, 0), (SERVICE_STATUS, 113)])

    status = manager.get_status(paths, uid=501, runner=runner)

    assert status.plist_exists is True
    assert status.mode is None
    runner.assert_complete()


def _log_file_tampered_bytes(paths: Any) -> bytes:
    """Build a managed plist differing only in the log file argument."""
    payload = manager.build_plist(paths, 'test')
    arguments = payload['ProgramArguments']
    assert isinstance(arguments, list)
    payload['ProgramArguments'] = [
        *arguments[:-1],
        f'{arguments[-1]}.tampered',
    ]
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)


def test_status_reports_unknown_mode_when_log_file_differs(
    tmp_path: Path,
) -> None:
    paths = _manager_paths(tmp_path)
    tampered = _log_file_tampered_bytes(paths)
    assert b'.tampered' in tampered
    paths.plist_path.write_bytes(tampered)
    runner = RecordingRunner([(DOMAIN_STATUS, 0), (SERVICE_STATUS, 113)])

    status = manager.get_status(paths, uid=501, runner=runner)

    assert status.plist_exists is True
    assert status.mode is None
    runner.assert_complete()


def test_status_rejects_unavailable_user_domain(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    runner = RecordingRunner([(DOMAIN_STATUS, 1)])

    with pytest.raises(
        manager.LaunchAgentCommandError,
        match='launchctl user domain unavailable',
    ):
        manager.get_status(paths, uid=501, runner=runner)

    assert 'secret' not in str(runner.calls)
    runner.assert_complete()


def test_status_rejects_unexpected_service_error(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    runner = RecordingRunner([(DOMAIN_STATUS, 0), (SERVICE_STATUS, 2)])

    with pytest.raises(
        manager.LaunchAgentCommandError,
        match='launchctl service status failed',
    ):
        manager.get_status(paths, uid=501, runner=runner)

    runner.assert_complete()


def test_status_maps_missing_launchctl_to_safe_error(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)

    def missing_runner(*args: Any, **kwargs: Any) -> Any:
        """Raise missing command error."""
        raise FileNotFoundError('secret /Users/private')

    with pytest.raises(
        manager.LaunchAgentCommandError,
        match='System command unavailable',
    ) as error:
        manager.get_status(paths, uid=501, runner=missing_runner)

    assert 'secret' not in str(error.value)
    assert '/Users/private' not in str(error.value)


def test_status_maps_command_timeout_to_safe_error(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)

    def timeout_runner(*args: Any, **kwargs: Any) -> Any:
        """Raise command timeout error."""
        raise subprocess.TimeoutExpired(
            cmd='secret /Users/private',
            timeout=10,
        )

    with pytest.raises(
        manager.LaunchAgentCommandError,
        match='System command timed out',
    ) as error:
        manager.get_status(paths, uid=501, runner=timeout_runner)

    assert 'secret' not in str(error.value)
    assert '/Users/private' not in str(error.value)


# === Preflight contract ===


def test_preflight_rejects_missing_python(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    paths.python_path.unlink()
    runner = RecordingRunner([])

    with pytest.raises(
        manager.LaunchAgentValidationError,
        match='Python executable unavailable',
    ):
        manager.validate_installation(paths, uid=501, runner=runner)

    assert runner.calls == []
    runner.assert_complete()


def test_preflight_hides_import_failure_output(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    runner = RecordingRunner([(DOMAIN_STATUS, 0), (PYTHON_IMPORT, 1)])

    with pytest.raises(
        manager.LaunchAgentCommandError,
        match='Python import check failed',
    ) as error:
        manager.validate_installation(paths, uid=501, runner=runner)

    assert 'secret' not in str(error.value)
    assert '/Users/private' not in str(error.value)
    runner.assert_complete()


# === Installation contract ===


def test_install_rejects_invalid_poll_attempts(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    runner = RecordingRunner([])

    with pytest.raises(ValueError, match='poll_attempts must be positive'):
        manager.install(
            paths,
            uid=501,
            mode='test',
            runner=runner,
            poll_attempts=0,
        )

    assert runner.calls == []
    runner.assert_complete()


def test_install_rejects_unknown_mode_before_side_effects(
    tmp_path: Path,
) -> None:
    paths = _manager_paths(tmp_path)
    runner = RecordingRunner([])

    with pytest.raises(
        manager.LaunchAgentValidationError,
        match='Bucket run mode is unknown',
    ):
        manager.install(paths, uid=501, mode='staging', runner=runner)

    assert runner.calls == []
    assert not paths.lock_path.exists()
    assert not paths.log_dir.exists()
    assert not paths.plist_path.exists()
    runner.assert_complete()


def test_install_writes_private_plist_and_logs(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 113),
            (PLIST_LINT, 0),
            (BOOTSTRAP, 0),
        ]
    )

    manager.install(paths, uid=501, mode='test', runner=runner)

    assert plistlib.loads(
        paths.plist_path.read_bytes()
    ) == manager.build_plist(
        paths,
        'test',
    )
    assert paths.plist_path.stat().st_mode & 0o777 == 0o600
    assert paths.log_dir.stat().st_mode & 0o777 == 0o700
    assert runner.calls[-1][0] == (
        str(paths.launchctl_path),
        'bootstrap',
        'gui/501',
        str(paths.plist_path),
    )
    runner.assert_complete()


def test_install_reloads_existing_service(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    paths.plist_path.write_bytes(manager.render_plist(paths, 'test'))
    runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 0),
            (PLIST_LINT, 0),
            (BOOTOUT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 113),
            (BOOTSTRAP, 0),
        ]
    )

    manager.install(paths, uid=501, mode='test', runner=runner)

    commands = [call[0] for call in runner.calls]
    assert (
        str(paths.launchctl_path),
        'bootout',
        'gui/501/io.github.hawkxdev.aw-watcher-orca',
    ) in commands
    assert paths.plist_path.read_bytes() == manager.render_plist(paths, 'test')
    runner.assert_complete()


def test_install_waits_for_existing_service_removal(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    paths.plist_path.write_bytes(manager.render_plist(paths, 'test'))
    runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 0),
            (PLIST_LINT, 0),
            (BOOTOUT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 113),
            (BOOTSTRAP, 0),
        ]
    )
    sleeps: list[float] = []

    manager.install(
        paths,
        uid=501,
        mode='test',
        runner=runner,
        sleeper=sleeps.append,
        poll_attempts=2,
    )

    commands = [call[0][1] for call in runner.calls]
    assert commands[5:] == [
        'bootout',
        'print',
        'print',
        'print',
        'print',
        'bootstrap',
    ]
    assert sleeps == [manager.DEFAULT_POLL_INTERVAL_SECONDS]
    runner.assert_complete()


def test_install_preserves_plist_when_service_remains_loaded(
    tmp_path: Path,
) -> None:
    paths = _manager_paths(tmp_path)
    previous = manager.render_plist(paths, 'test')
    paths.plist_path.write_bytes(previous)
    runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 0),
            (PLIST_LINT, 0),
            (BOOTOUT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 0),
        ]
    )
    sleeps: list[float] = []

    with pytest.raises(
        manager.LaunchAgentCommandError,
        match='LaunchAgent remained loaded after bootout',
    ):
        manager.install(
            paths,
            uid=501,
            mode='test',
            runner=runner,
            sleeper=sleeps.append,
            poll_attempts=2,
        )

    assert paths.plist_path.read_bytes() == previous
    assert all(call[0][1] != 'bootstrap' for call in runner.calls)
    assert sleeps == [manager.DEFAULT_POLL_INTERVAL_SECONDS]
    runner.assert_complete()


def test_install_preserves_state_when_plutil_fails(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    previous = manager.render_plist(paths, 'test')
    paths.plist_path.write_bytes(previous)
    runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 113),
            (PLIST_LINT, 1),
        ]
    )

    with pytest.raises(
        manager.LaunchAgentCommandError,
        match='plutil validation failed',
    ):
        manager.install(paths, uid=501, mode='test', runner=runner)

    assert paths.plist_path.read_bytes() == previous
    assert not paths.log_dir.exists()
    assert all(call[0][1] != 'bootout' for call in runner.calls)
    runner.assert_complete()


def test_install_rejects_unmanaged_loaded_service(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 0),
        ]
    )

    with pytest.raises(
        manager.LaunchAgentValidationError,
        match='Loaded LaunchAgent has no managed plist',
    ):
        manager.install(paths, uid=501, mode='test', runner=runner)

    assert not paths.plist_path.exists()
    assert all(call[0][1] != 'bootout' for call in runner.calls)
    runner.assert_complete()


@pytest.mark.parametrize('previous_mode', ['test', 'production'])
def test_install_restores_previous_configuration_after_bootstrap_failure(
    tmp_path: Path,
    previous_mode: str,
) -> None:
    control = _manager_paths(tmp_path / 'control')
    control.plist_path.write_bytes(
        manager.render_plist(control, previous_mode)
    )
    control_runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 0),
            (PLIST_LINT, 0),
            (BOOTOUT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 113),
            (BOOTSTRAP, 0),
        ]
    )

    manager.install(control, uid=501, mode='test', runner=control_runner)

    assert control.plist_path.read_bytes() == manager.render_plist(
        control,
        'test',
    )
    control_runner.assert_complete()

    paths = _manager_paths(tmp_path / 'guarded')
    previous = manager.render_plist(paths, previous_mode)
    paths.plist_path.write_bytes(previous)
    runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 0),
            (PLIST_LINT, 0),
            (BOOTOUT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 113),
            (BOOTSTRAP, 1),
            (BOOTSTRAP, 0),
        ]
    )

    with pytest.raises(
        manager.LaunchAgentCommandError,
        match='launchctl bootstrap failed',
    ):
        manager.install(paths, uid=501, mode='test', runner=runner)

    assert paths.plist_path.read_bytes() == previous
    bootstrap_calls = [
        call for call in runner.calls if call[0][1] == 'bootstrap'
    ]
    assert len(bootstrap_calls) == 2
    assert bootstrap_calls[-1][0] == (
        str(paths.launchctl_path),
        'bootstrap',
        'gui/501',
        str(paths.plist_path),
    )
    runner.assert_complete()


def test_install_reports_failed_service_restoration(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    previous = manager.render_plist(paths, 'test')
    paths.plist_path.write_bytes(previous)
    runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 0),
            (PLIST_LINT, 0),
            (BOOTOUT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 113),
            (BOOTSTRAP, 1),
            (BOOTSTRAP, 1),
        ]
    )

    with pytest.raises(
        manager.LaunchAgentRollbackError,
        match='Previous LaunchAgent reload failed',
    ):
        manager.install(paths, uid=501, mode='test', runner=runner)

    assert paths.plist_path.read_bytes() == previous
    runner.assert_complete()


def test_install_restores_previous_plist_mode_after_rollback(
    tmp_path: Path,
) -> None:
    control = _manager_paths(tmp_path / 'control')
    control.plist_path.write_bytes(manager.render_plist(control, 'test'))
    control.plist_path.chmod(0o640)
    control_runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 0),
            (PLIST_LINT, 0),
            (BOOTOUT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 113),
            (BOOTSTRAP, 0),
        ]
    )

    manager.install(control, uid=501, mode='test', runner=control_runner)

    assert control.plist_path.read_bytes() == manager.render_plist(
        control,
        'test',
    )
    assert control.plist_path.stat().st_mode & 0o777 == 0o600
    control_runner.assert_complete()

    paths = _manager_paths(tmp_path / 'guarded')
    previous = manager.render_plist(paths, 'test')
    paths.plist_path.write_bytes(previous)
    paths.plist_path.chmod(0o640)
    runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 0),
            (PLIST_LINT, 0),
            (BOOTOUT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 113),
            (BOOTSTRAP, 1),
            (BOOTSTRAP, 0),
        ]
    )

    with pytest.raises(
        manager.LaunchAgentCommandError,
        match='launchctl bootstrap failed',
    ):
        manager.install(paths, uid=501, mode='test', runner=runner)

    assert paths.plist_path.read_bytes() == previous
    assert paths.plist_path.stat().st_mode & 0o777 == 0o640
    runner.assert_complete()


def test_restoration_wraps_unavailable_service_command(
    tmp_path: Path,
) -> None:
    paths = _manager_paths(tmp_path)
    sensitive_path = '/Users/private/bin/launchctl'
    runner = Mock(side_effect=OSError(sensitive_path))

    with pytest.raises(
        manager.LaunchAgentRollbackError,
        match='Previous LaunchAgent restoration failed',
    ) as error:
        manager._restore_previous_service(
            paths,
            uid=501,
            previous_bytes=b'previous plist',
            previous_mode=0o600,
            was_loaded=True,
            runner=runner,
        )

    assert sensitive_path not in str(error.value)


def test_install_removes_new_plist_after_bootstrap_failure(
    tmp_path: Path,
) -> None:
    paths = _manager_paths(tmp_path)
    runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 113),
            (PLIST_LINT, 0),
            (BOOTSTRAP, 1),
        ]
    )

    with pytest.raises(
        manager.LaunchAgentCommandError,
        match='launchctl bootstrap failed',
    ):
        manager.install(paths, uid=501, mode='test', runner=runner)

    assert not paths.plist_path.exists()
    runner.assert_complete()


def test_install_rejects_symlink_target(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    redirect = tmp_path / 'redirect.plist'
    redirect.write_bytes(b'protected')
    paths.plist_path.symlink_to(redirect)
    runner = RecordingRunner([])

    with pytest.raises(
        manager.LaunchAgentValidationError,
        match='LaunchAgent plist cannot be a symlink',
    ):
        manager.install(paths, uid=501, mode='test', runner=runner)

    assert redirect.read_bytes() == b'protected'
    assert runner.calls == []
    runner.assert_complete()


def test_install_rejects_symlink_target_ancestor(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    redirect = tmp_path / 'redirected-launch-agents'
    redirect.mkdir()
    paths.launch_agents_dir.rmdir()
    paths.launch_agents_dir.symlink_to(redirect, target_is_directory=True)
    runner = RecordingRunner([])

    with pytest.raises(
        manager.LaunchAgentValidationError,
        match='LaunchAgent plist cannot be a symlink',
    ):
        manager.install(paths, uid=501, mode='test', runner=runner)

    assert list(redirect.iterdir()) == []
    assert runner.calls == []
    runner.assert_complete()


# === Removal contract ===


def test_uninstall_rejects_invalid_poll_attempts(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    runner = RecordingRunner([])

    with pytest.raises(ValueError, match='poll_attempts must be positive'):
        manager.uninstall(paths, uid=501, runner=runner, poll_attempts=0)

    assert runner.calls == []
    runner.assert_complete()


def test_uninstall_boots_out_and_preserves_logs(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    paths.plist_path.write_bytes(manager.render_plist(paths, 'test'))
    paths.log_dir.mkdir(mode=0o700, parents=True)
    paths.watcher_log_path.write_text('evidence', encoding='utf-8')
    runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 0),
            (BOOTOUT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 113),
        ]
    )

    manager.uninstall(
        paths,
        uid=501,
        runner=runner,
        sleeper=lambda seconds: None,
        poll_attempts=2,
    )

    assert not paths.plist_path.exists()
    assert paths.watcher_log_path.read_text(encoding='utf-8') == 'evidence'
    assert any(call[0][1] == 'bootout' for call in runner.calls)
    runner.assert_complete()


def test_uninstall_removes_unloaded_plist(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    paths.plist_path.write_bytes(manager.render_plist(paths, 'test'))
    runner = RecordingRunner([(DOMAIN_STATUS, 0), (SERVICE_STATUS, 113)])

    manager.uninstall(paths, uid=501, runner=runner)

    assert not paths.plist_path.exists()
    assert all(call[0][1] != 'bootout' for call in runner.calls)
    runner.assert_complete()


def _configuration_payload(
    paths: Any,
    payload_kind: str,
) -> bytes:
    """Build one plist payload of the requested configuration kind."""
    if payload_kind == 'corrupted':
        return b'<xml bytes that are not a plist'
    if payload_kind == 'foreign':
        return _foreign_plist_bytes(paths)
    return plistlib.dumps(
        {'Label': 'io.github.hawkxdev.aw-watcher-orca'},
        fmt=plistlib.FMT_XML,
        sort_keys=True,
    )


@pytest.mark.parametrize(
    ('payload_kind', 'loaded'),
    [
        ('corrupted', False),
        ('foreign', False),
        ('missing-arguments', False),
        ('corrupted', True),
        ('foreign', True),
        ('missing-arguments', True),
    ],
)
def test_uninstall_removes_any_managed_configuration(
    tmp_path: Path,
    payload_kind: str,
    loaded: bool,
) -> None:
    paths = _manager_paths(tmp_path)
    paths.plist_path.write_bytes(_configuration_payload(paths, payload_kind))
    responses = (
        [
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 0),
            (BOOTOUT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 113),
        ]
        if loaded
        else [(DOMAIN_STATUS, 0), (SERVICE_STATUS, 113)]
    )
    runner = RecordingRunner(responses)

    manager.uninstall(
        paths,
        uid=501,
        runner=runner,
        sleeper=lambda seconds: None,
        poll_attempts=2,
    )

    assert not paths.plist_path.exists()
    assert any(call[0][1] == 'bootout' for call in runner.calls) is loaded
    runner.assert_complete()


def test_uninstall_preserves_plist_when_bootout_fails(
    tmp_path: Path,
) -> None:
    paths = _manager_paths(tmp_path)
    original = manager.render_plist(paths, 'test')
    paths.plist_path.write_bytes(original)
    runner = RecordingRunner(
        [(DOMAIN_STATUS, 0), (SERVICE_STATUS, 0), (BOOTOUT, 1)]
    )

    with pytest.raises(
        manager.LaunchAgentCommandError,
        match='launchctl bootout failed',
    ):
        manager.uninstall(paths, uid=501, runner=runner)

    assert paths.plist_path.read_bytes() == original
    runner.assert_complete()


def test_uninstall_preserves_plist_when_service_remains_loaded(
    tmp_path: Path,
) -> None:
    paths = _manager_paths(tmp_path)
    original = manager.render_plist(paths, 'test')
    paths.plist_path.write_bytes(original)
    runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 0),
            (BOOTOUT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 0),
        ]
    )

    with pytest.raises(
        manager.LaunchAgentCommandError,
        match='LaunchAgent remained loaded after bootout',
    ):
        manager.uninstall(
            paths,
            uid=501,
            runner=runner,
            sleeper=lambda seconds: None,
            poll_attempts=2,
        )

    assert paths.plist_path.read_bytes() == original
    runner.assert_complete()


# === Command interface ===


@pytest.mark.parametrize(
    ('command', 'extra', 'expected_mode'),
    [
        ('render', ['--mode', 'test'], 'test'),
        ('status', [], None),
        ('install', ['--mode', 'production'], 'production'),
        ('uninstall', [], None),
    ],
)
def test_parser_accepts_all_commands(
    command: str,
    extra: list[str],
    expected_mode: str | None,
) -> None:
    arguments = manager.build_argument_parser().parse_args([command, *extra])

    assert arguments.command == command
    assert getattr(arguments, 'mode', None) == expected_mode


def test_status_json_excludes_absolute_paths(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    runner = RecordingRunner([(DOMAIN_STATUS, 0), (SERVICE_STATUS, 113)])

    payload = manager.status_payload(
        manager.get_status(paths, uid=501, runner=runner)
    )

    encoded = json.dumps(payload)
    assert str(tmp_path) not in encoded
    assert payload == {
        'label': 'io.github.hawkxdev.aw-watcher-orca',
        'loaded': False,
        'plist_exists': False,
        'bucket_mode': None,
    }
    runner.assert_complete()


def test_status_json_reports_detected_mode(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    paths.plist_path.write_bytes(manager.render_plist(paths, 'production'))
    runner = RecordingRunner([(DOMAIN_STATUS, 0), (SERVICE_STATUS, 0)])

    payload = manager.status_payload(
        manager.get_status(paths, uid=501, runner=runner)
    )

    assert payload['bucket_mode'] == 'production'
    runner.assert_complete()


def test_install_rejects_unknown_installed_configuration(
    tmp_path: Path,
) -> None:
    control = _manager_paths(tmp_path / 'control')
    control.plist_path.write_bytes(manager.render_plist(control, 'test'))
    control_runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 113),
            (PLIST_LINT, 0),
            (BOOTSTRAP, 0),
        ]
    )

    manager.install(
        control,
        uid=501,
        mode='test',
        runner=control_runner,
    )
    control_runner.assert_complete()

    paths = _manager_paths(tmp_path / 'guarded')
    foreign = _foreign_plist_bytes(paths)
    paths.plist_path.write_bytes(foreign)
    runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 113),
        ]
    )

    with pytest.raises(
        manager.LaunchAgentValidationError,
        match='remove it with the uninstall command',
    ):
        manager.install(paths, uid=501, mode='test', runner=runner)

    assert paths.plist_path.read_bytes() == foreign
    assert all(
        call[0][1] not in {'bootout', 'bootstrap'} for call in runner.calls
    )
    runner.assert_complete()


def _legacy_plist_bytes(paths: Any) -> bytes:
    """Build the Stage 6C plist form: managed paths, no run mode."""
    payload = manager.build_plist(paths, 'test')
    arguments = payload['ProgramArguments']
    assert isinstance(arguments, list)
    index = arguments.index('--mode')
    payload['ProgramArguments'] = arguments[:index] + arguments[index + 2 :]
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)


def test_stage6c_plist_is_unknown_and_blocks_install(tmp_path: Path) -> None:
    control = _manager_paths(tmp_path / 'control')
    control.plist_path.write_bytes(manager.render_plist(control, 'test'))
    control_status_runner = RecordingRunner(
        [(DOMAIN_STATUS, 0), (SERVICE_STATUS, 113)]
    )

    control_status = manager.get_status(
        control,
        uid=501,
        runner=control_status_runner,
    )

    assert control_status.mode == 'test'
    control_status_runner.assert_complete()

    control_install_runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 113),
            (PLIST_LINT, 0),
            (BOOTSTRAP, 0),
        ]
    )

    manager.install(
        control, uid=501, mode='test', runner=control_install_runner
    )
    control_install_runner.assert_complete()

    paths = _manager_paths(tmp_path / 'guarded')
    legacy = _legacy_plist_bytes(paths)
    assert b'--mode' not in legacy
    assert str(paths.python_path) in legacy.decode('utf-8')
    paths.plist_path.write_bytes(legacy)
    status_runner = RecordingRunner(
        [(DOMAIN_STATUS, 0), (SERVICE_STATUS, 113)]
    )

    status = manager.get_status(paths, uid=501, runner=status_runner)

    assert status.plist_exists is True
    assert status.mode is None
    status_runner.assert_complete()

    runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 113),
        ]
    )

    with pytest.raises(
        manager.LaunchAgentValidationError,
        match='remove it with the uninstall command',
    ):
        manager.install(paths, uid=501, mode='test', runner=runner)

    assert paths.plist_path.read_bytes() == legacy
    assert not paths.lock_path.exists()
    assert not paths.log_dir.exists()
    assert all(
        call[0][1] not in {'bootout', 'bootstrap'} for call in runner.calls
    )
    runner.assert_complete()


# === Instance lock contract ===


def test_build_paths_derives_lock_path_from_home(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)

    assert paths.lock_path == (
        paths.home_dir
        / 'Library'
        / 'Application Support'
        / 'aw-watcher-orca'
        / 'watcher.lock'
    )


def test_install_rejects_occupied_lock_before_changes(
    tmp_path: Path,
) -> None:
    control = _manager_paths(tmp_path / 'control')
    control_runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 113),
            (PLIST_LINT, 0),
            (BOOTSTRAP, 0),
        ]
    )

    manager.install(control, uid=501, mode='test', runner=control_runner)
    control_runner.assert_complete()

    paths = _manager_paths(tmp_path / 'guarded')
    descriptor = _hold_flock(paths.lock_path)
    try:
        runner = RecordingRunner(
            [
                (DOMAIN_STATUS, 0),
                (PYTHON_IMPORT, 0),
                (DOMAIN_STATUS, 0),
                (SERVICE_STATUS, 113),
            ]
        )
        sleeps: list[float] = []

        with pytest.raises(
            manager.LaunchAgentValidationError,
            match='Watcher instance lock remained occupied',
        ):
            manager.install(
                paths,
                uid=501,
                mode='test',
                runner=runner,
                sleeper=sleeps.append,
                poll_attempts=2,
            )

        assert sleeps == [manager.DEFAULT_POLL_INTERVAL_SECONDS]
        assert not paths.plist_path.exists()
        assert not paths.log_dir.exists()
        assert all(
            call[0][1] not in {'bootout', 'bootstrap'} for call in runner.calls
        )
        runner.assert_complete()
    finally:
        _release_flock(descriptor)


def test_install_waits_full_budget_on_unloaded_lock(tmp_path: Path) -> None:
    control = _manager_paths(tmp_path / 'control')
    control_runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 113),
            (PLIST_LINT, 0),
            (BOOTSTRAP, 0),
        ]
    )

    manager.install(control, uid=501, mode='test', runner=control_runner)
    control_runner.assert_complete()

    paths = _manager_paths(tmp_path / 'guarded')
    descriptor = _hold_flock(paths.lock_path)
    try:
        runner = RecordingRunner(
            [
                (DOMAIN_STATUS, 0),
                (PYTHON_IMPORT, 0),
                (DOMAIN_STATUS, 0),
                (SERVICE_STATUS, 113),
            ]
        )
        sleeps: list[float] = []

        with pytest.raises(
            manager.LaunchAgentValidationError,
            match='Watcher instance lock remained occupied',
        ):
            manager.install(
                paths,
                uid=501,
                mode='test',
                runner=runner,
                sleeper=sleeps.append,
                poll_attempts=3,
            )

        assert sleeps == [manager.DEFAULT_POLL_INTERVAL_SECONDS] * 2
        assert not paths.plist_path.exists()
        assert not paths.log_dir.exists()
        assert all(
            call[0][1] not in {'bootout', 'bootstrap'} for call in runner.calls
        )
        runner.assert_complete()
    finally:
        _release_flock(descriptor)


def test_install_reports_lock_probe_failure_without_paths(
    tmp_path: Path,
) -> None:
    control = _manager_paths(tmp_path / 'control')
    control_runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 113),
            (PLIST_LINT, 0),
            (BOOTSTRAP, 0),
        ]
    )

    manager.install(control, uid=501, mode='test', runner=control_runner)
    control_runner.assert_complete()

    paths = _manager_paths(tmp_path / 'guarded')
    paths.lock_path.parent.parent.write_text('', encoding='utf-8')
    runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 113),
        ]
    )

    with pytest.raises(
        manager.LaunchAgentValidationError,
        match='Watcher instance lock path cannot be probed',
    ) as error:
        manager.install(paths, uid=501, mode='test', runner=runner)

    assert str(tmp_path) not in str(error.value)
    assert 'Not a directory' not in str(error.value)
    assert not paths.plist_path.exists()
    assert not paths.log_dir.exists()
    runner.assert_complete()


@pytest.mark.parametrize('symlink_target', ['file', 'directory'])
def test_install_rejects_unsafe_lock_path_before_changes(
    tmp_path: Path,
    symlink_target: str,
) -> None:
    control = _manager_paths(tmp_path / 'control')
    control_runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 113),
            (PLIST_LINT, 0),
            (BOOTSTRAP, 0),
        ]
    )

    manager.install(control, uid=501, mode='test', runner=control_runner)
    control_runner.assert_complete()

    paths = _manager_paths(tmp_path / 'guarded')
    if symlink_target == 'file':
        redirect = tmp_path / 'redirected-watcher.lock'
        redirect.write_bytes(b'')
        paths.lock_path.parent.mkdir(parents=True, exist_ok=True)
        paths.lock_path.symlink_to(redirect)
    else:
        redirect = tmp_path / 'redirected-lock-directory'
        redirect.mkdir()
        paths.lock_path.parent.parent.mkdir(parents=True, exist_ok=True)
        paths.lock_path.parent.symlink_to(
            redirect,
            target_is_directory=True,
        )
    runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 113),
        ]
    )

    with pytest.raises(
        manager.LaunchAgentValidationError,
        match='Watcher instance lock path cannot be trusted',
    ):
        manager.install(paths, uid=501, mode='test', runner=runner)

    assert not paths.plist_path.exists()
    assert not paths.log_dir.exists()
    assert all(
        call[0][1] not in {'bootout', 'bootstrap'} for call in runner.calls
    )
    runner.assert_complete()


def test_install_preserves_system_when_bootout_fails(tmp_path: Path) -> None:
    control = _manager_paths(tmp_path / 'control')
    control.plist_path.write_bytes(manager.render_plist(control, 'test'))
    control_runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 0),
            (PLIST_LINT, 0),
            (BOOTOUT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 113),
            (BOOTSTRAP, 0),
        ]
    )

    manager.install(control, uid=501, mode='test', runner=control_runner)
    control_runner.assert_complete()

    paths = _manager_paths(tmp_path / 'guarded')
    previous = manager.render_plist(paths, 'test')
    paths.plist_path.write_bytes(previous)
    runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 0),
            (PLIST_LINT, 0),
            (BOOTOUT, 1),
        ]
    )

    with pytest.raises(
        manager.LaunchAgentCommandError,
        match='launchctl bootout failed',
    ):
        manager.install(paths, uid=501, mode='test', runner=runner)

    assert paths.plist_path.read_bytes() == previous
    assert not paths.log_dir.exists()
    assert all(call[0][1] != 'bootstrap' for call in runner.calls)
    runner.assert_complete()


def test_install_waits_for_lock_release_after_bootout(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    paths.plist_path.write_bytes(manager.render_plist(paths, 'test'))
    descriptor = _hold_flock(paths.lock_path)
    runner = EffectRunner(
        RecordingRunner(
            [
                (DOMAIN_STATUS, 0),
                (PYTHON_IMPORT, 0),
                (DOMAIN_STATUS, 0),
                (SERVICE_STATUS, 0),
                (PLIST_LINT, 0),
                (BOOTOUT, 0),
                (DOMAIN_STATUS, 0),
                (SERVICE_STATUS, 113),
                (BOOTSTRAP, 0),
            ]
        ),
        BOOTOUT,
        lambda: _release_flock(descriptor),
    )

    try:
        manager.install(paths, uid=501, mode='test', runner=runner)
    finally:
        _release_flock(descriptor)

    assert paths.plist_path.read_bytes() == manager.render_plist(
        paths,
        'test',
    )
    runner.inner.assert_complete()


def test_install_retries_lock_check_within_budget(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    paths.plist_path.write_bytes(manager.render_plist(paths, 'test'))
    descriptor = _hold_flock(paths.lock_path)
    sleeps: list[float] = []
    released = [False]

    def release_on_first_sleep(seconds: float) -> None:
        """Release the held flock on the first polling sleep."""
        sleeps.append(seconds)
        if not released[0]:
            released[0] = True
            _release_flock(descriptor)

    runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 0),
            (PLIST_LINT, 0),
            (BOOTOUT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 113),
            (BOOTSTRAP, 0),
        ]
    )

    try:
        manager.install(
            paths,
            uid=501,
            mode='test',
            runner=runner,
            sleeper=release_on_first_sleep,
            poll_attempts=2,
        )
    finally:
        _release_flock(descriptor)

    assert paths.plist_path.read_bytes() == manager.render_plist(
        paths,
        'test',
    )
    assert sleeps == [manager.DEFAULT_POLL_INTERVAL_SECONDS]
    runner.assert_complete()


def test_install_rejects_lock_occupied_after_bootout(tmp_path: Path) -> None:
    control = _manager_paths(tmp_path / 'control')
    control.plist_path.write_bytes(manager.render_plist(control, 'test'))
    control_descriptor = _hold_flock(control.lock_path)
    control_runner = EffectRunner(
        RecordingRunner(
            [
                (DOMAIN_STATUS, 0),
                (PYTHON_IMPORT, 0),
                (DOMAIN_STATUS, 0),
                (SERVICE_STATUS, 0),
                (PLIST_LINT, 0),
                (BOOTOUT, 0),
                (DOMAIN_STATUS, 0),
                (SERVICE_STATUS, 113),
                (BOOTSTRAP, 0),
            ]
        ),
        BOOTOUT,
        lambda: _release_flock(control_descriptor),
    )

    try:
        manager.install(control, uid=501, mode='test', runner=control_runner)
    finally:
        _release_flock(control_descriptor)
    control_runner.inner.assert_complete()

    paths = _manager_paths(tmp_path / 'guarded')
    previous = manager.render_plist(paths, 'test')
    paths.plist_path.write_bytes(previous)
    descriptor = _hold_flock(paths.lock_path)
    try:
        runner = RecordingRunner(
            [
                (DOMAIN_STATUS, 0),
                (PYTHON_IMPORT, 0),
                (DOMAIN_STATUS, 0),
                (SERVICE_STATUS, 0),
                (PLIST_LINT, 0),
                (BOOTOUT, 0),
                (DOMAIN_STATUS, 0),
                (SERVICE_STATUS, 113),
                (BOOTSTRAP, 0),
            ]
        )
        sleeps: list[float] = []

        with pytest.raises(
            manager.LaunchAgentValidationError,
            match=(
                'Watcher instance lock remained occupied; '
                'the previous service was reloaded'
            ),
        ):
            manager.install(
                paths,
                uid=501,
                mode='test',
                runner=runner,
                sleeper=sleeps.append,
                poll_attempts=2,
            )

        assert paths.plist_path.read_bytes() == previous
        bootstrap_calls = [
            call for call in runner.calls if call[0][1] == 'bootstrap'
        ]
        assert len(bootstrap_calls) == 1
        assert bootstrap_calls[0][0] == (
            str(paths.launchctl_path),
            'bootstrap',
            'gui/501',
            str(paths.plist_path),
        )
        assert sleeps == [manager.DEFAULT_POLL_INTERVAL_SECONDS]
        runner.assert_complete()
    finally:
        _release_flock(descriptor)


def test_install_creates_private_lock_file(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 113),
            (PLIST_LINT, 0),
            (BOOTSTRAP, 0),
        ]
    )

    manager.install(paths, uid=501, mode='test', runner=runner)

    assert paths.lock_path.exists()
    assert paths.lock_path.stat().st_mode & 0o777 == 0o600
    assert paths.lock_path.parent.stat().st_mode & 0o777 == 0o700
    runner.assert_complete()


def test_install_releases_instance_lock_on_success(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 113),
            (PLIST_LINT, 0),
            (BOOTSTRAP, 0),
        ]
    )

    manager.install(paths, uid=501, mode='test', runner=runner)

    lock = acquire_instance_lock(lock_path=paths.lock_path)
    lock.release()


def test_install_releases_instance_lock_on_refusal_path(
    tmp_path: Path,
) -> None:
    paths = _manager_paths(tmp_path)
    paths.plist_path.write_bytes(manager.render_plist(paths, 'test'))
    runner = RecordingRunner(
        [
            (DOMAIN_STATUS, 0),
            (PYTHON_IMPORT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 0),
            (PLIST_LINT, 0),
            (BOOTOUT, 0),
            (DOMAIN_STATUS, 0),
            (SERVICE_STATUS, 113),
            (BOOTSTRAP, 1),
            (BOOTSTRAP, 0),
        ]
    )

    with pytest.raises(
        manager.LaunchAgentCommandError,
        match='launchctl bootstrap failed',
    ):
        manager.install(paths, uid=501, mode='test', runner=runner)

    lock = acquire_instance_lock(lock_path=paths.lock_path)
    lock.release()


def test_main_sanitizes_filesystem_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sensitive_path = '/Users/private/Library/LaunchAgents/private.plist'
    monkeypatch.setattr(
        manager,
        'build_paths',
        Mock(side_effect=PermissionError(sensitive_path)),
    )

    exit_code = manager.main(['status'])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert json.loads(captured.err) == {
        'ok': False,
        'error': 'LaunchAgentCommandError',
        'message': 'Local filesystem operation failed',
    }
    assert sensitive_path not in captured.err


@pytest.mark.parametrize(
    'message',
    ['plutil validation failed', 'launchctl bootstrap failed'],
)
def test_main_reports_sanitized_domain_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    message: str,
) -> None:
    paths = _manager_paths(tmp_path)
    monkeypatch.setattr(manager, 'build_paths', Mock(return_value=paths))
    monkeypatch.setattr(
        manager,
        'install',
        Mock(side_effect=manager.LaunchAgentCommandError(message)),
    )

    exit_code = manager.main(['install', '--mode', 'test'])
    payload = json.loads(capsys.readouterr().err)

    assert exit_code == 2
    assert payload == {
        'ok': False,
        'error': 'LaunchAgentCommandError',
        'message': message,
    }
    assert '/Users/private' not in json.dumps(payload)


# === Mode gate contract ===


def _isolate_main_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    """Point the manager entry at isolated temporary paths."""
    paths = _manager_paths(tmp_path)
    monkeypatch.setattr(manager, 'build_paths', Mock(return_value=paths))
    return paths


@pytest.mark.parametrize('command', ['render', 'install'])
def test_main_rejects_missing_mode_before_any_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    paths = _isolate_main_paths(tmp_path, monkeypatch)

    with pytest.raises(SystemExit) as raised:
        manager.main([command])

    assert raised.value.code == 2
    assert not paths.plist_path.exists()
    assert not paths.log_dir.exists()


@pytest.mark.parametrize(
    ('command', 'mode'),
    [('render', 'staging'), ('install', 'staging')],
)
def test_main_rejects_mode_outside_closed_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    mode: str,
) -> None:
    paths = _isolate_main_paths(tmp_path, monkeypatch)

    with pytest.raises(SystemExit) as raised:
        manager.main([command, '--mode', mode])

    assert raised.value.code == 2
    assert not paths.plist_path.exists()
    assert not paths.log_dir.exists()


# === Interpreter and renderer contract ===


def test_manager_runs_under_validating_interpreter(tmp_path: Path) -> None:
    manager_path = Path(manager.__file__).resolve()
    environment = os.environ.copy()
    environment['PYTHONPATH'] = str(manager_path.parents[1] / 'app')
    environment['HOME'] = str(tmp_path)

    result = subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, str(manager_path), 'render', '--mode', 'test'],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert 'ModuleNotFoundError' not in result.stderr
    expected = manager.build_plist(
        manager.build_paths(home_dir=tmp_path),
        'test',
    )
    assert plistlib.loads(result.stdout.encode('utf-8')) == expected
    assert not (tmp_path / 'Library' / 'LaunchAgents').exists()


@pytest.mark.parametrize('mode', ['test', 'production'])
def test_rendered_candidates_pass_real_plutil(
    tmp_path: Path,
    mode: str,
) -> None:
    paths = _manager_paths(tmp_path)
    candidate = tmp_path / f'candidate-{mode}.plist'
    candidate.write_bytes(manager.render_plist(paths, mode))

    result = subprocess.run(  # noqa: S603 - fixed system plutil path
        [str(manager.PLUTIL_PATH), '-lint', str(candidate)],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    candidate.unlink()
