"""Measure persisted Orca state."""

import argparse
import hashlib
import json
import math
import re
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import TextIO

# === Constants ===


SUPPORTED_SCHEMA_VERSION = 1
DEFAULT_INTERVAL_MS = 100
DEFAULT_DURATION_SECONDS = 15.0
FINGERPRINT_LENGTH = 12
MARKER_PATTERN = re.compile(r'^[A-Za-z0-9._-]{1,64}$')


# === Errors ===


class OrcaProbeError(Exception):
    """Represent safe probe failures."""


class StateDiscoveryError(OrcaProbeError):
    """Represent state discovery failures."""


class OrcaStateError(OrcaProbeError):
    """Represent invalid Orca state."""


class UnsupportedSchemaError(OrcaStateError):
    """Represent unsupported state schemas."""


# === Models ===


@dataclass(frozen=True, slots=True)
class SafeIdentity:
    """Hold sanitized worktree identity."""

    fingerprint: str
    name: str

    def to_public_dict(self) -> dict[str, str]:
        """Build a public identity."""
        return {
            'fingerprint': self.fingerprint,
            'name': self.name,
        }


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    """Hold one persisted snapshot."""

    schema_version: int
    source_mtime_ns: int
    active_worktree: SafeIdentity
    active_workspace: SafeIdentity

    @property
    def signature(self) -> tuple[object, ...]:
        """Build an observation signature."""
        return (
            self.active_worktree,
            self.active_workspace,
        )

    def to_public_dict(self, elapsed_ms: int) -> dict[str, object]:
        """Build a public snapshot."""
        return {
            'kind': 'snapshot',
            'elapsed_ms': elapsed_ms,
            'schema_version': self.schema_version,
            'source_mtime_ns': self.source_mtime_ns,
            'active_worktree': self.active_worktree.to_public_dict(),
            'active_workspace': self.active_workspace.to_public_dict(),
        }


# === State reading ===


def discover_state_file(home_dir: Path | None = None) -> Path:
    """Discover one state file."""
    resolved_home = home_dir or Path.home()
    profiles_dir = (
        resolved_home / 'Library' / 'Application Support' / 'orca' / 'profiles'
    )
    candidates = sorted(profiles_dir.glob('*/orca-data.json'))
    if not candidates:
        raise StateDiscoveryError('No Orca state file found')
    if len(candidates) > 1:
        raise StateDiscoveryError(
            f'Multiple Orca state files found: {len(candidates)}'
        )
    return candidates[0]


def sanitize_identity(raw_identity: str) -> SafeIdentity:
    """Sanitize one Orca identity."""
    normalized = raw_identity.removeprefix('worktree:')
    _, separator, worktree_path = normalized.partition('::')
    worktree_name = PurePath(worktree_path).name
    if not separator or not worktree_name:
        raise OrcaStateError('Invalid Orca worktree identity')
    fingerprint = hashlib.sha256(normalized.encode('utf-8')).hexdigest()
    return SafeIdentity(
        fingerprint=fingerprint[:FINGERPRINT_LENGTH],
        name=worktree_name,
    )


def _require_session_string(
    workspace_session: dict[str, object],
    field_name: str,
) -> str:
    """Require one session string."""
    value = workspace_session.get(field_name)
    if not isinstance(value, str) or not value:
        raise OrcaStateError(f'Missing workspaceSession.{field_name}')
    return value


def _state_metadata(state_file: Path) -> tuple[int, int]:
    """Read stable file metadata."""
    state_stat = state_file.stat()
    return state_stat.st_mtime_ns, state_stat.st_size


def load_snapshot(state_file: Path) -> WorkspaceSnapshot:
    """Load one Orca snapshot."""
    # Stable read
    try:
        initial_metadata = _state_metadata(state_file)
        raw_state = state_file.read_text(encoding='utf-8')
        final_metadata = _state_metadata(state_file)
    except UnicodeDecodeError as exc:
        raise OrcaStateError('Unable to decode Orca state') from exc
    except OSError as exc:
        raise OrcaStateError('Unable to read Orca state file') from exc
    if initial_metadata != final_metadata:
        raise OrcaStateError('Orca state changed during read')
    # Payload and schema
    try:
        payload = json.loads(raw_state)
    except json.JSONDecodeError as exc:
        raise OrcaStateError('Invalid Orca state JSON') from exc
    if not isinstance(payload, dict):
        raise OrcaStateError('Orca state root must be an object')
    schema_version = payload.get('schemaVersion')
    if (
        type(schema_version) is not int
        or schema_version != SUPPORTED_SCHEMA_VERSION
    ):
        raise UnsupportedSchemaError(
            f'Unsupported Orca schema version: {schema_version}'
        )
    # Session fields
    workspace_session = payload.get('workspaceSession')
    if not isinstance(workspace_session, dict):
        raise OrcaStateError('Missing workspaceSession object')
    active_worktree_id = _require_session_string(
        workspace_session,
        'activeWorktreeId',
    )
    active_workspace_key = _require_session_string(
        workspace_session,
        'activeWorkspaceKey',
    )
    return WorkspaceSnapshot(
        schema_version=SUPPORTED_SCHEMA_VERSION,
        source_mtime_ns=final_metadata[0],
        active_worktree=sanitize_identity(active_worktree_id),
        active_workspace=sanitize_identity(active_workspace_key),
    )


# === Probe execution ===


def _emit_json(payload: dict[str, object], stream: TextIO) -> None:
    """Emit one JSON record."""
    print(
        json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
        file=stream,
        flush=True,
    )


def run_probe(
    state_file: Path,
    interval_ms: int,
    duration_seconds: float,
    marker: str | None,
    output_stream: TextIO,
) -> int:
    """Run a bounded probe."""
    # 1. Argument guards
    if interval_ms <= 0:
        raise ValueError('interval_ms must be positive')
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError('duration_seconds must be finite and positive')
    # 2. Counters and deadline
    start_ns = time.monotonic_ns()
    deadline_ns = start_ns + int(duration_seconds * 1_000_000_000)
    interval_seconds = interval_ms / 1_000
    samples = 0
    observations = 0
    read_errors = 0
    last_signature: tuple[object, ...] | None = None
    last_error: tuple[str, str] | None = None
    # 3. Start marker
    if marker is not None:
        _emit_json(
            {
                'kind': 'marker',
                'elapsed_ms': 0,
                'label': marker,
            },
            output_stream,
        )
    # 4. Polling loop
    while True:
        now_ns = time.monotonic_ns()
        elapsed_ms = (now_ns - start_ns) // 1_000_000
        samples += 1
        # Snapshot read
        try:
            snapshot = load_snapshot(state_file)
        except OrcaProbeError as exc:
            read_errors += 1
            error_signature = (type(exc).__name__, str(exc))
            if error_signature != last_error:
                _emit_json(
                    {
                        'kind': 'read_error',
                        'elapsed_ms': elapsed_ms,
                        'error': type(exc).__name__,
                        'message': str(exc),
                    },
                    output_stream,
                )
            last_error = error_signature
        else:
            # New observation
            last_error = None
            if snapshot.signature != last_signature:
                _emit_json(
                    snapshot.to_public_dict(elapsed_ms),
                    output_stream,
                )
                observations += 1
                last_signature = snapshot.signature
        # Deadline and pause
        if now_ns >= deadline_ns:
            break
        remaining_seconds = (deadline_ns - now_ns) / 1_000_000_000
        time.sleep(min(interval_seconds, remaining_seconds))
    # 5. Summary
    total_elapsed_ms = (time.monotonic_ns() - start_ns) // 1_000_000
    _emit_json(
        {
            'kind': 'summary',
            'elapsed_ms': total_elapsed_ms,
            'samples': samples,
            'observations': observations,
            'read_errors': read_errors,
        },
        output_stream,
    )
    return 0 if observations else 2


# === Command line ===


def _positive_int(raw_value: str) -> int:
    """Parse one positive integer."""
    value = int(raw_value)
    if value <= 0:
        raise argparse.ArgumentTypeError('value must be positive')
    return value


def _positive_float(raw_value: str) -> float:
    """Parse one positive float."""
    value = float(raw_value)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError('value must be finite and positive')
    return value


def _marker_label(raw_value: str) -> str:
    """Parse one safe marker."""
    if not MARKER_PATTERN.fullmatch(raw_value):
        raise argparse.ArgumentTypeError(
            'marker must use 1-64 safe characters'
        )
    return raw_value


def build_parser() -> argparse.ArgumentParser:
    """Build the command parser."""
    parser = argparse.ArgumentParser(
        description='Measure persisted Orca worktree state changes.',
    )
    parser.add_argument('--state-file', type=Path)
    parser.add_argument('--once', action='store_true')
    parser.add_argument(
        '--interval-ms',
        type=_positive_int,
    )
    parser.add_argument(
        '--duration-seconds',
        type=_positive_float,
    )
    parser.add_argument('--marker', type=_marker_label)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the probe command."""
    # Arguments
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.once and (
        args.interval_ms is not None
        or args.duration_seconds is not None
        or args.marker is not None
    ):
        parser.error('--once cannot be combined with probe options')
    # Dispatch
    try:
        state_file = args.state_file or discover_state_file()
        if args.once:
            snapshot = load_snapshot(state_file)
            _emit_json(snapshot.to_public_dict(0), sys.stdout)
            return 0
        return run_probe(
            state_file=state_file,
            interval_ms=args.interval_ms or DEFAULT_INTERVAL_MS,
            duration_seconds=(
                args.duration_seconds or DEFAULT_DURATION_SECONDS
            ),
            marker=args.marker,
            output_stream=sys.stdout,
        )
    # Fatal errors
    except OrcaProbeError as exc:
        _emit_json(
            {
                'kind': 'fatal_error',
                'error': type(exc).__name__,
                'message': str(exc),
            },
            sys.stderr,
        )
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
