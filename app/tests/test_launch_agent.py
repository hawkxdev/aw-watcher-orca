"""Test LaunchAgent lifecycle manager."""

import json
import plistlib
import subprocess
from pathlib import Path
from typing import Any

import pytest

import launch_agent as manager

# === Fakes ===


class RecordingRunner:
    """Record fake command execution."""

    def __init__(self, returncodes: list[int]) -> None:
        """Initialize fake command runner."""
        self.returncodes = iter(returncodes)
        self.calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    def __call__(
        self,
        arguments: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        """Return queued command result."""
        self.calls.append((tuple(arguments), kwargs))
        return subprocess.CompletedProcess(
            arguments,
            next(self.returncodes),
            stdout='',
            stderr='secret /Users/private branch prompt',
        )


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

    payload = manager.build_plist(paths)

    assert payload == {
        'KeepAlive': True,
        'Label': 'io.github.hawkxdev.aw-watcher-orca',
        'ProcessType': 'Background',
        'ProgramArguments': [
            str(paths.python_path),
            '-m',
            'aw_watcher_orca',
            '--log-file',
            str(paths.watcher_log_path),
        ],
        'StandardErrorPath': str(paths.launcher_log_path),
        'ThrottleInterval': 10,
        'Umask': '077',
        'WorkingDirectory': str(paths.app_dir),
    }


def test_render_plist_round_trips_exact_payload(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)

    rendered = manager.render_plist(paths)

    assert plistlib.loads(rendered) == manager.build_plist(paths)


def test_render_plist_excludes_production_configuration(
    tmp_path: Path,
) -> None:
    paths = _manager_paths(tmp_path)

    rendered = manager.render_plist(paths)

    assert b'aw-watcher-orca_' not in rendered
    assert b'EnvironmentVariables' not in rendered
    assert b'RunAtLoad' not in rendered


# === Status contract ===


def test_status_reports_unloaded_service(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    runner = RecordingRunner([0, 113])

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


def test_status_reports_loaded_service(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    paths.plist_path.write_bytes(manager.render_plist(paths))
    runner = RecordingRunner([0, 0])

    status = manager.get_status(paths, uid=501, runner=runner)

    assert status.loaded is True
    assert status.plist_exists is True


def test_status_rejects_unavailable_user_domain(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    runner = RecordingRunner([1])

    with pytest.raises(
        manager.LaunchAgentCommandError,
        match='launchctl user domain unavailable',
    ):
        manager.get_status(paths, uid=501, runner=runner)

    assert 'secret' not in str(runner.calls)


def test_status_rejects_unexpected_service_error(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    runner = RecordingRunner([0, 2])

    with pytest.raises(
        manager.LaunchAgentCommandError,
        match='launchctl service status failed',
    ):
        manager.get_status(paths, uid=501, runner=runner)


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


def test_preflight_hides_import_failure_output(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    runner = RecordingRunner([0, 1])

    with pytest.raises(
        manager.LaunchAgentCommandError,
        match='Python import check failed',
    ) as error:
        manager.validate_installation(paths, uid=501, runner=runner)

    assert 'secret' not in str(error.value)
    assert '/Users/private' not in str(error.value)


# === Installation contract ===


def test_install_writes_private_plist_and_logs(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    runner = RecordingRunner([0, 0, 0, 0, 113, 0])

    manager.install(paths, uid=501, runner=runner)

    assert plistlib.loads(
        paths.plist_path.read_bytes()
    ) == manager.build_plist(
        paths,
    )
    assert paths.plist_path.stat().st_mode & 0o777 == 0o600
    assert paths.log_dir.stat().st_mode & 0o777 == 0o700
    assert runner.calls[-1][0] == (
        str(paths.launchctl_path),
        'bootstrap',
        'gui/501',
        str(paths.plist_path),
    )


def test_install_reloads_existing_service(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    paths.plist_path.write_bytes(b'previous plist')
    runner = RecordingRunner([0, 0, 0, 0, 0, 0, 0])

    manager.install(paths, uid=501, runner=runner)

    commands = [call[0] for call in runner.calls]
    assert (
        str(paths.launchctl_path),
        'bootout',
        'gui/501/io.github.hawkxdev.aw-watcher-orca',
    ) in commands
    assert paths.plist_path.read_bytes() == manager.render_plist(paths)


def test_install_preserves_state_when_plutil_fails(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    previous = b'previous plist'
    paths.plist_path.write_bytes(previous)
    runner = RecordingRunner([0, 0, 1])

    with pytest.raises(
        manager.LaunchAgentCommandError,
        match='plutil validation failed',
    ):
        manager.install(paths, uid=501, runner=runner)

    assert paths.plist_path.read_bytes() == previous
    assert all(call[0][1] != 'bootout' for call in runner.calls)


def test_install_rejects_unmanaged_loaded_service(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    runner = RecordingRunner([0, 0, 0, 0, 0])

    with pytest.raises(
        manager.LaunchAgentValidationError,
        match='Loaded LaunchAgent has no managed plist',
    ):
        manager.install(paths, uid=501, runner=runner)

    assert not paths.plist_path.exists()
    assert all(call[0][1] != 'bootout' for call in runner.calls)


def test_install_restores_loaded_service_after_bootstrap_failure(
    tmp_path: Path,
) -> None:
    paths = _manager_paths(tmp_path)
    previous = b'previous plist'
    paths.plist_path.write_bytes(previous)
    runner = RecordingRunner([0, 0, 0, 0, 0, 0, 1, 0])

    with pytest.raises(
        manager.LaunchAgentCommandError,
        match='launchctl bootstrap failed',
    ):
        manager.install(paths, uid=501, runner=runner)

    assert paths.plist_path.read_bytes() == previous
    bootstrap_calls = [
        call for call in runner.calls if call[0][1] == 'bootstrap'
    ]
    assert len(bootstrap_calls) == 2


def test_install_reports_failed_service_restoration(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    previous = b'previous plist'
    paths.plist_path.write_bytes(previous)
    runner = RecordingRunner([0, 0, 0, 0, 0, 0, 1, 1])

    with pytest.raises(
        manager.LaunchAgentRollbackError,
        match='Previous LaunchAgent reload failed',
    ):
        manager.install(paths, uid=501, runner=runner)

    assert paths.plist_path.read_bytes() == previous


def test_install_removes_new_plist_after_bootstrap_failure(
    tmp_path: Path,
) -> None:
    paths = _manager_paths(tmp_path)
    runner = RecordingRunner([0, 0, 0, 0, 113, 1])

    with pytest.raises(
        manager.LaunchAgentCommandError,
        match='launchctl bootstrap failed',
    ):
        manager.install(paths, uid=501, runner=runner)

    assert not paths.plist_path.exists()


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
        manager.install(paths, uid=501, runner=runner)

    assert redirect.read_bytes() == b'protected'
    assert runner.calls == []


# === Removal contract ===


def test_uninstall_boots_out_and_preserves_logs(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    paths.plist_path.write_bytes(manager.render_plist(paths))
    paths.log_dir.mkdir(mode=0o700, parents=True)
    paths.watcher_log_path.write_text('evidence', encoding='utf-8')
    runner = RecordingRunner([0, 0, 0, 0, 113])

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


def test_uninstall_removes_unloaded_plist(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    paths.plist_path.write_bytes(manager.render_plist(paths))
    runner = RecordingRunner([0, 113])

    manager.uninstall(paths, uid=501, runner=runner)

    assert not paths.plist_path.exists()
    assert all(call[0][1] != 'bootout' for call in runner.calls)


def test_uninstall_preserves_plist_when_bootout_fails(
    tmp_path: Path,
) -> None:
    paths = _manager_paths(tmp_path)
    original = manager.render_plist(paths)
    paths.plist_path.write_bytes(original)
    runner = RecordingRunner([0, 0, 1])

    with pytest.raises(
        manager.LaunchAgentCommandError,
        match='launchctl bootout failed',
    ):
        manager.uninstall(paths, uid=501, runner=runner)

    assert paths.plist_path.read_bytes() == original


def test_uninstall_preserves_plist_when_service_remains_loaded(
    tmp_path: Path,
) -> None:
    paths = _manager_paths(tmp_path)
    original = manager.render_plist(paths)
    paths.plist_path.write_bytes(original)
    runner = RecordingRunner([0, 0, 0, 0, 0, 0, 0])

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


# === Command interface ===


@pytest.mark.parametrize(
    'command',
    ['render', 'status', 'install', 'uninstall'],
)
def test_parser_accepts_all_commands(command: str) -> None:
    arguments = manager.build_argument_parser().parse_args([command])

    assert arguments.command == command


def test_status_json_excludes_absolute_paths(tmp_path: Path) -> None:
    paths = _manager_paths(tmp_path)
    runner = RecordingRunner([0, 113])

    payload = manager.status_payload(
        manager.get_status(paths, uid=501, runner=runner)
    )

    encoded = json.dumps(payload)
    assert str(tmp_path) not in encoded
    assert payload == {
        'label': 'io.github.hawkxdev.aw-watcher-orca',
        'loaded': False,
        'plist_exists': False,
        'bucket_mode': 'test',
    }
