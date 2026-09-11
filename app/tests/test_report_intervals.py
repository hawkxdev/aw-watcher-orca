"""Test interval algebra, AFK intersection, calendar, noise, and conflicts."""

import ast
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from aw_watcher_orca.report_intervals import (
    SEAM_TOLERANCE_MICROSECONDS,
    apply_noise_threshold_and_clip,
    calendar_day_length_us,
    compute_afk_intervals,
    compute_contributing_events_summary,
    compute_period_bounds_us,
    compute_project_statistics_intervals,
    datetime_to_us,
    filter_matching_project_events,
    intersect_intervals,
    intervals_overlap,
    intervals_overlap_in_period,
    resolve_calendar_midnight_utc,
    span_us,
    split_interval_by_local_days,
    union_intervals,
    validate_test_boundary_transition,
)
from aw_watcher_orca.report_models import (
    ABSENT_UNVERIFIED_STATUS,
    UNKNOWN_BOUNDARY_REASON,
    RawEventRecord,
)
from aw_watcher_orca.report_reader import (
    FullSnapshotResult,
    compute_production_boundary,
)
from aw_watcher_orca.report_sources import build_source_catalog
from report_fixtures import (
    EXPECTED_C01,
    EXPECTED_C02,
    EXPECTED_C03,
    EXPECTED_C04,
    EXPECTED_C06,
    EXPECTED_C07,
    EXPECTED_C08,
    EXPECTED_C09,
    EXPECTED_C11,
    EXPECTED_C12,
    EXPECTED_C13,
    EXPECTED_C14,
    EXPECTED_C15,
    EXPECTED_C16,
    EXPECTED_C17,
    EXPECTED_C18_AUTUMN,
    EXPECTED_C18_SPRING,
    EXPECTED_C19,
    EXPECTED_C23,
    EXPECTED_C25,
    EXPECTED_C27,
    EXPECTED_C28,
    EXPECTED_CONTROL_NO_OVERLAP,
    PINNED_CASE_EXPECTATIONS,
)

# === Oracle Rule and Fixture Reconciliation Tests (AC-02) ===


def test_report_fixtures_ast_import_ban() -> None:
    """Verify report_fixtures.py does NOT import report_* modules (AC-02)."""
    fixtures_path = Path(__file__).parent / 'report_fixtures.py'
    tree = ast.parse(fixtures_path.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith('aw_watcher_orca.report'), (
                    f'Prohibited import: {alias.name}'
                )
                assert not alias.name.startswith('report_'), (
                    f'Prohibited import: {alias.name}'
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ''
            assert not module.startswith('aw_watcher_orca.report'), (
                f'Prohibited from-import: {module}'
            )
            assert not module.startswith('report_'), (
                f'Prohibited from-import: {module}'
            )


def test_reconcile_fixtures_with_contract_cases_json() -> None:
    """Verify fixtures match project-statistics-contract-cases.json."""
    cases_json_path = (
        Path(__file__).parent.parent.parent
        / 'docs'
        / 'evidence'
        / 'project-statistics-contract-cases.json'
    )
    data = json.loads(cases_json_path.read_text(encoding='utf-8'))
    json_cases = {c['id']: c['expected'] for c in data['cases']}

    for case_id, json_expected in json_cases.items():
        assert case_id in PINNED_CASE_EXPECTATIONS, (
            f'Case {case_id} missing from PINNED_CASE_EXPECTATIONS'
        )
        assert PINNED_CASE_EXPECTATIONS[case_id] == json_expected, (
            f'Mismatch for {case_id}: fixture='
            f'{PINNED_CASE_EXPECTATIONS[case_id]} vs json={json_expected}'
        )


# === Executable Contract Cases C01..C28 ===


def test_contract_c01_intersection() -> None:
    """C01: intersect window with active intervals."""
    window = ((0, 600_000_000),)
    active = ((120_000_000, 300_000_000), (420_000_000, 540_000_000))
    result = intersect_intervals(window, active)
    assert span_us(result) == EXPECTED_C01


def test_contract_c02_intersection_duplicates() -> None:
    """C02: intersect window with active intervals containing a duplicate."""
    window = ((0, 600_000_000),)
    active = (
        (120_000_000, 300_000_000),
        (120_000_000, 300_000_000),
        (420_000_000, 540_000_000),
    )
    result = intersect_intervals(window, active)
    assert span_us(result) == EXPECTED_C02


def test_contract_c03_overlap() -> None:
    """C03: left [0, 10s] and right [5s, 6s] overlap."""
    left = ((0, 10_000_000),)
    right = ((5_000_000, 6_000_000),)
    assert intervals_overlap(left, right) is EXPECTED_C03


def test_contract_c04_unknown_coverage() -> None:
    """C04: window 600s, active 180s [120s..300s], empty AFK -> unknown."""
    # Active 180s without AFK coverage yields 180s unknown AFK in pipeline
    t0 = datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)
    buckets = {
        'aw-watcher-orca_h1': {
            'client': 'aw-watcher-orca',
            'type': 'currentwindow',
            'hostname': 'h1',
        },
        'aw-watcher-afk_h1': {
            'client': 'aw-watcher-afk',
            'type': 'afkstatus',
            'hostname': 'h1',
        },
    }
    catalog = build_source_catalog(buckets)
    events_orca = [
        RawEventRecord(
            event_id=1,
            timestamp=t0 + timedelta(seconds=120),
            duration_us=180_000_000,
            data={
                'app': 'Orca',
                'repo': 'my-repo',
                'worktree': 'main',
                'title': 'my-repo / main',
            },
        ),
    ]
    events_by_bucket = {
        'aw-watcher-orca_h1': tuple(events_orca),
        'aw-watcher-afk_h1': (),
    }
    snapshot = FullSnapshotResult(
        catalog=catalog,
        observed_until=t0 + timedelta(seconds=600),
        read_started_at=t0,
        read_finished_at=t0,
        events_by_bucket=events_by_bucket,
        production_boundary=t0 + timedelta(seconds=120),
        boundary_status='known',
        total_response_bytes=1000,
    )
    res = compute_project_statistics_intervals(
        snapshot=snapshot,
        start_date=date(2026, 9, 8),
        end_date=date(2026, 9, 8),
        zone_name='UTC',
    )
    src_res = res.source_results[0]
    # R2 core level: active time without AFK coverage (180s);
    # service layer test_report_service.py asserts EXPECTED_C04 (420s).
    assert src_res.quality.unknown_afk_us == 180_000_000
    window_span = 600_000_000
    active_span = 180_000_000
    idle_span = 0
    unknown = window_span - (active_span + idle_span)
    assert unknown == EXPECTED_C04


def test_contract_c06_boundary_zero_duration() -> None:
    """C06: production [[0, 0], [300s, 360s]] boundary is 0."""
    buckets = {
        'aw-watcher-orca_h1': {
            'client': 'aw-watcher-orca',
            'type': 'currentwindow',
            'hostname': 'h1',
        },
        'aw-watcher-afk_h1': {
            'client': 'aw-watcher-afk',
            'type': 'afkstatus',
            'hostname': 'h1',
        },
    }
    catalog = build_source_catalog(buckets)
    t0 = datetime(1970, 1, 1, 0, 0, 0, tzinfo=UTC)
    t300 = t0 + timedelta(seconds=300)
    events = [
        RawEventRecord(
            event_id=1,
            timestamp=t0,
            duration_us=0,
            data={'app': 'Orca', 'repo': '', 'worktree': '', 'title': ''},
        ),
        RawEventRecord(
            event_id=2,
            timestamp=t300,
            duration_us=60_000_000,
            data={'app': 'Orca', 'repo': 'r', 'worktree': 'w', 'title': 't'},
        ),
    ]
    events_by_bucket = {'aw-watcher-orca_h1': tuple(events)}
    observed_until = t0 + timedelta(hours=1)
    boundary, status = compute_production_boundary(
        catalog, events_by_bucket, observed_until
    )
    assert boundary is not None
    assert status == 'known'
    assert datetime_to_us(boundary) == EXPECTED_C06


def test_contract_c07_c08_c09_transition() -> None:
    """C07, C08, C09: strict test boundary transition checks."""
    boundary_us = 300_000_000

    # C07: test end 299_999_999 < 300_000_000 -> True
    test_c07 = ((0, 299_999_999),)
    assert (
        validate_test_boundary_transition(test_c07, boundary_us)
        is EXPECTED_C07
    )

    # C08: test end 300_000_000 >= 300_000_000 -> False
    test_c08 = ((0, 300_000_000),)
    assert (
        validate_test_boundary_transition(test_c08, boundary_us)
        is EXPECTED_C08
    )

    # C09: test zero-duration at 300_000_000 (start=end=300_000_000) -> False
    test_c09 = ((300_000_000, 300_000_000),)
    assert (
        validate_test_boundary_transition(test_c09, boundary_us)
        is EXPECTED_C09
    )


def test_contract_c11_boundary_empty() -> None:
    """C11: empty production events yields None boundary."""
    buckets = {
        'aw-watcher-orca_h1': {
            'client': 'aw-watcher-orca',
            'type': 'currentwindow',
            'hostname': 'h1',
        },
        'aw-watcher-afk_h1': {
            'client': 'aw-watcher-afk',
            'type': 'afkstatus',
            'hostname': 'h1',
        },
    }
    catalog = build_source_catalog(buckets)
    events_by_bucket: dict[str, tuple[RawEventRecord, ...]] = {
        'aw-watcher-orca_h1': ()
    }
    observed_until = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)

    boundary, status = compute_production_boundary(
        catalog, events_by_bucket, observed_until
    )
    assert boundary is EXPECTED_C11
    assert status == UNKNOWN_BOUNDARY_REASON


def test_contract_c12_union() -> None:
    """C12: union of [0, 10s] and [5s, 15s] span is 15s."""
    intervals = ((0, 10_000_000), (5_000_000, 15_000_000))
    result = union_intervals(intervals)
    assert span_us(result) == EXPECTED_C12


def test_contract_c13_c14_overlap() -> None:
    """C13, C14: identical and overlapping intervals overlap."""
    assert (
        intervals_overlap(((0, 60_000_000),), ((0, 60_000_000),))
        is EXPECTED_C13
    )
    assert (
        intervals_overlap(((0, 10_000_000),), ((5_000_000, 15_000_000),))
        is EXPECTED_C14
    )


def test_contract_c15_c16_noise_threshold() -> None:
    """C15, C16: noise threshold on connected segments."""
    # C15: adjacent segments [0, 0.6s] + [0.6s, 1.2s] merge to 1.2s -> kept
    g1 = (((0, 600_000), (600_000, 1_200_000)),)
    res_c15 = apply_noise_threshold_and_clip(
        g1, threshold_us=1_000_000, period_bounds=None
    )
    assert res_c15.kept_duration_us == EXPECTED_C15

    # C16: different groups [0, 0.6s] and [0.6s, 1.2s] each < 1s -> 0s
    g2 = (((0, 600_000),), ((600_000, 1_200_000),))
    res_c16 = apply_noise_threshold_and_clip(
        g2, threshold_us=1_000_000, period_bounds=None
    )
    assert res_c16.kept_duration_us == EXPECTED_C16


def test_contract_c17_day_split() -> None:
    """C17: split segment spanning local midnight in Europe/Minsk."""
    start_dt = datetime(2026, 3, 29, 20, 59, 59, 400000, tzinfo=UTC)
    end_dt = datetime(2026, 3, 29, 21, 0, 0, 600000, tzinfo=UTC)
    zone_name = 'Europe/Minsk'

    start_us = datetime_to_us(start_dt)
    end_us = datetime_to_us(end_dt)

    splits = split_interval_by_local_days(start_us, end_us, zone_name)
    assert splits == EXPECTED_C17


def test_contract_c18_dst_day_lengths() -> None:
    """C18-spring and C18-autumn: daylight saving 23h/25h in Berlin."""
    spring_len = calendar_day_length_us(date(2026, 3, 29), 'Europe/Berlin')
    assert spring_len == EXPECTED_C18_SPRING

    autumn_len = calendar_day_length_us(date(2026, 10, 25), 'Europe/Berlin')
    assert autumn_len == EXPECTED_C18_AUTUMN


def test_contract_c19_long_afk() -> None:
    """C19: not-afk starting 48h before window covers full window."""
    window = ((0, 600_000_000),)
    long_active = ((-172_800_000_000, 600_000_000),)
    result = intersect_intervals(window, long_active)
    assert span_us(result) == EXPECTED_C19


def test_contract_c23_overlap_outside_period() -> None:
    """C23: overlap outside period does NOT block the period."""
    left = ((-100_000_000, -50_000_000), (0, 60_000_000))
    right = ((-80_000_000, -70_000_000),)
    period = (0, 60_000_000)
    assert (
        intervals_overlap_in_period(left, right, period[0], period[1])
        is EXPECTED_C23
    )


def test_contract_c25_noise_clip() -> None:
    """C25: segment passes threshold then clipped yields 0.6s."""
    g = (((0, 1_200_000),),)
    period = (600_000, 1_200_000)
    res = apply_noise_threshold_and_clip(
        g, threshold_us=1_000_000, period_bounds=period
    )
    assert res.kept_duration_us == EXPECTED_C25


def test_contract_c27_c28_literal_filter() -> None:
    """C27, C28: literal pattern matching with special characters."""
    assert (
        filter_matching_project_events(
            ['a+b', 'aaab'], pattern='a+b', exact=False
        )
        == EXPECTED_C27
    )
    assert (
        filter_matching_project_events(
            ['repo', 'repo-extra'], pattern='repo', exact=True
        )
        == EXPECTED_C28
    )


def test_contract_control_no_overlap() -> None:
    """control-no-overlap: touching ends [0, 60s] and [60s, 120s] no-op."""
    left = ((0, 60_000_000),)
    right = ((60_000_000, 120_000_000),)
    assert intervals_overlap(left, right) is EXPECTED_CONTROL_NO_OVERLAP


# === Acceptance Distinguishing Edge Cases (HLD Table) ===


def test_afk_merging_and_overlap_conflict() -> None:
    """Verify AFK merging and detection of contradictory status overlap."""
    t0 = datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)
    events = [
        RawEventRecord(
            event_id=1,
            timestamp=t0 + timedelta(minutes=2),
            duration_us=180_000_000,
            data={'status': 'not-afk'},
        ),
        RawEventRecord(
            event_id=2,
            timestamp=t0 + timedelta(minutes=7),
            duration_us=120_000_000,
            data={'status': 'not-afk'},
        ),
    ]
    afk_data = compute_afk_intervals(events, period_bounds=None)
    assert afk_data.has_conflict is False
    assert span_us(afk_data.not_afk_intervals) == 300_000_000


def test_afk_conflict_in_period() -> None:
    """Verify contradictory afk and not-afk in period marks conflict."""
    t0 = datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)
    p_start_us = datetime_to_us(t0)
    p_end_us = datetime_to_us(t0 + timedelta(hours=1))

    events = [
        RawEventRecord(
            event_id=1,
            timestamp=t0 + timedelta(minutes=2),
            duration_us=180_000_000,
            data={'status': 'not-afk'},
        ),
        RawEventRecord(
            event_id=2,
            timestamp=t0 + timedelta(minutes=3),
            duration_us=60_000_000,
            data={'status': 'afk'},
        ),
    ]
    afk_data = compute_afk_intervals(
        events, period_bounds=(p_start_us, p_end_us)
    )
    assert afk_data.has_conflict is True


# === Status-Switch Seam Amnesty (TIME-04, Stage R5.1) ===


def test_seam_one_ms_amnesty_trims_earlier_tail() -> None:
    """1 ms opposite-status seam is amnestied, earlier tail trimmed."""
    t0 = datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)
    t0_us = datetime_to_us(t0)
    seam_us = t0_us + 5_000_000
    events = [
        RawEventRecord(
            event_id=1,
            timestamp=t0,
            duration_us=5_001_000,
            data={'status': 'not-afk'},
        ),
        RawEventRecord(
            event_id=2,
            timestamp=t0 + timedelta(seconds=5),
            duration_us=1_000_000,
            data={'status': 'afk'},
        ),
    ]
    afk_data = compute_afk_intervals(events, period_bounds=None)
    assert afk_data.has_conflict is False
    assert afk_data.not_afk_intervals == ((t0_us, seam_us),)
    assert afk_data.afk_intervals == ((seam_us, seam_us + 1_000_000),)


def test_seam_forty_ms_live_pattern_amnesty() -> None:
    """40 ms seam as in live data is amnestied the same way."""
    t0 = datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)
    t0_us = datetime_to_us(t0)
    seam_us = t0_us + 5_000_000
    events = [
        RawEventRecord(
            event_id=1,
            timestamp=t0,
            duration_us=5_040_000,
            data={'status': 'not-afk'},
        ),
        RawEventRecord(
            event_id=2,
            timestamp=t0 + timedelta(seconds=5),
            duration_us=1_000_000,
            data={'status': 'afk'},
        ),
    ]
    afk_data = compute_afk_intervals(events, period_bounds=None)
    assert afk_data.has_conflict is False
    assert afk_data.not_afk_intervals == ((t0_us, seam_us),)
    assert afk_data.afk_intervals == ((seam_us, seam_us + 1_000_000),)


def test_seam_over_tolerance_stays_conflict() -> None:
    """200 ms opposite-status overlap exceeds tolerance: conflict."""
    t0 = datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)
    t0_us = datetime_to_us(t0)
    p_end_us = t0_us + 3_600_000_000
    events = [
        RawEventRecord(
            event_id=1,
            timestamp=t0,
            duration_us=5_200_000,
            data={'status': 'not-afk'},
        ),
        RawEventRecord(
            event_id=2,
            timestamp=t0 + timedelta(seconds=5),
            duration_us=1_000_000,
            data={'status': 'afk'},
        ),
    ]
    afk_data = compute_afk_intervals(events, period_bounds=(t0_us, p_end_us))
    assert afk_data.has_conflict is True
    assert afk_data.conflict_reason == (
        'contradictory_afk_and_not_afk_in_period'
    )
    assert afk_data.not_afk_intervals == ((t0_us, t0_us + 5_200_000),)


def test_seam_exact_tolerance_boundary_amnesty() -> None:
    """Overlap of exactly the tolerance is amnestied (<=)."""
    t0 = datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)
    t0_us = datetime_to_us(t0)
    seam_us = t0_us + 5_000_000
    events = [
        RawEventRecord(
            event_id=1,
            timestamp=t0,
            duration_us=5_000_000 + SEAM_TOLERANCE_MICROSECONDS,
            data={'status': 'not-afk'},
        ),
        RawEventRecord(
            event_id=2,
            timestamp=t0 + timedelta(seconds=5),
            duration_us=1_000_000,
            data={'status': 'afk'},
        ),
    ]
    afk_data = compute_afk_intervals(events, period_bounds=None)
    assert afk_data.has_conflict is False
    assert afk_data.not_afk_intervals == ((t0_us, seam_us),)
    assert afk_data.afk_intervals == ((seam_us, seam_us + 1_000_000),)


def test_seam_zero_tolerance_keeps_conflict() -> None:
    """seam_tolerance_us=0 reproduces the former conflict behavior."""
    t0 = datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)
    t0_us = datetime_to_us(t0)
    p_end_us = t0_us + 3_600_000_000
    events = [
        RawEventRecord(
            event_id=1,
            timestamp=t0,
            duration_us=5_001_000,
            data={'status': 'not-afk'},
        ),
        RawEventRecord(
            event_id=2,
            timestamp=t0 + timedelta(seconds=5),
            duration_us=1_000_000,
            data={'status': 'afk'},
        ),
    ]
    afk_data = compute_afk_intervals(
        events,
        period_bounds=(t0_us, p_end_us),
        seam_tolerance_us=0,
    )
    assert afk_data.has_conflict is True
    assert afk_data.conflict_reason == (
        'contradictory_afk_and_not_afk_in_period'
    )


def test_seam_orca_attribution_late_status_truthful() -> None:
    """Orca time across an amnestied seam follows the late status."""
    t0 = datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)
    buckets = {
        'aw-watcher-orca_h1': {
            'client': 'aw-watcher-orca',
            'type': 'currentwindow',
            'hostname': 'h1',
        },
        'aw-watcher-afk_h1': {
            'client': 'aw-watcher-afk',
            'type': 'afkstatus',
            'hostname': 'h1',
        },
    }
    catalog = build_source_catalog(buckets)
    events_orca = [
        RawEventRecord(
            event_id=1,
            timestamp=t0 + timedelta(seconds=1),
            duration_us=3_000_000,
            data={
                'app': 'Orca',
                'repo': 'r1',
                'worktree': 'w1',
                'title': 't1',
            },
        ),
    ]
    afk_events = [
        RawEventRecord(
            event_id=2,
            timestamp=t0,
            duration_us=3_001_000,
            data={'status': 'not-afk'},
        ),
        RawEventRecord(
            event_id=3,
            timestamp=t0 + timedelta(seconds=3),
            duration_us=2_000_000,
            data={'status': 'afk'},
        ),
    ]
    snapshot = FullSnapshotResult(
        catalog=catalog,
        observed_until=t0 + timedelta(hours=1),
        read_started_at=t0,
        read_finished_at=t0,
        events_by_bucket={
            'aw-watcher-orca_h1': tuple(events_orca),
            'aw-watcher-afk_h1': tuple(afk_events),
        },
        production_boundary=t0,
        boundary_status='known',
        total_response_bytes=1000,
    )
    res = compute_project_statistics_intervals(
        snapshot=snapshot,
        start_date=date(2026, 9, 8),
        end_date=date(2026, 9, 8),
        zone_name='Europe/Minsk',
        project_filter='r1',
    )
    src_res = res.source_results[0]
    assert src_res.is_conflict is False
    assert src_res.project_duration_us == 2_000_000


def test_seam_chain_touching_not_afk_no_unknown_tail() -> None:
    """A-B-C chain around a short afk leaves no unknown tail."""
    t0 = datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)
    t0_us = datetime_to_us(t0)
    seam1 = t0_us + 9_990_000
    seam2 = t0_us + 10_000_000
    events = [
        RawEventRecord(
            event_id=1,
            timestamp=t0,
            duration_us=10_000_000,
            data={'status': 'not-afk'},
        ),
        RawEventRecord(
            event_id=2,
            timestamp=t0 + timedelta(milliseconds=9990),
            duration_us=20_000,
            data={'status': 'afk'},
        ),
        RawEventRecord(
            event_id=3,
            timestamp=t0 + timedelta(seconds=10),
            duration_us=10_000_000,
            data={'status': 'not-afk'},
        ),
    ]
    afk_data = compute_afk_intervals(events, period_bounds=None)
    assert afk_data.has_conflict is False
    assert afk_data.not_afk_intervals == (
        (t0_us, seam1),
        (seam2, t0_us + 20_000_000),
    )
    assert afk_data.afk_intervals == ((seam1, seam2),)


def test_seam_double_seam_inside_single_run_splits() -> None:
    """Short afk blip inside one not-afk run splits it in two."""
    t0 = datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)
    t0_us = datetime_to_us(t0)
    blip_start = t0_us + 50_000_000
    blip_end = blip_start + 20_000
    events = [
        RawEventRecord(
            event_id=1,
            timestamp=t0,
            duration_us=100_000_000,
            data={'status': 'not-afk'},
        ),
        RawEventRecord(
            event_id=2,
            timestamp=t0 + timedelta(seconds=50),
            duration_us=20_000,
            data={'status': 'afk'},
        ),
    ]
    afk_data = compute_afk_intervals(events, period_bounds=None)
    assert afk_data.has_conflict is False
    assert afk_data.not_afk_intervals == (
        (t0_us, blip_start),
        (blip_end, t0_us + 100_000_000),
    )
    assert afk_data.afk_intervals == ((blip_start, blip_end),)


def test_seam_chain_overlapping_not_afk_pieces() -> None:
    """Overlapping not-afk pieces around a seam each lose their part."""
    t0 = datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)
    t0_us = datetime_to_us(t0)
    seam1 = t0_us + 9_990_000
    seam2 = t0_us + 10_010_000
    events = [
        RawEventRecord(
            event_id=1,
            timestamp=t0,
            duration_us=10_000_000,
            data={'status': 'not-afk'},
        ),
        RawEventRecord(
            event_id=2,
            timestamp=t0 + timedelta(milliseconds=9990),
            duration_us=20_000,
            data={'status': 'afk'},
        ),
        RawEventRecord(
            event_id=3,
            timestamp=t0 + timedelta(seconds=9),
            duration_us=11_000_000,
            data={'status': 'not-afk'},
        ),
    ]
    afk_data = compute_afk_intervals(events, period_bounds=None)
    assert afk_data.has_conflict is False
    assert afk_data.not_afk_intervals == (
        (t0_us, seam1),
        (seam2, t0_us + 20_000_000),
    )
    assert afk_data.afk_intervals == ((seam1, seam2),)


def test_seam_equal_starts_afk_yields_to_not_afk() -> None:
    """Equal starts: afk piece yields, not-afk stays truthful."""
    t0 = datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)
    t0_us = datetime_to_us(t0)
    events = [
        RawEventRecord(
            event_id=1,
            timestamp=t0,
            duration_us=200_000,
            data={'status': 'not-afk'},
        ),
        RawEventRecord(
            event_id=2,
            timestamp=t0,
            duration_us=100_000,
            data={'status': 'afk'},
        ),
    ]
    afk_data = compute_afk_intervals(events, period_bounds=None)
    assert afk_data.has_conflict is False
    assert afk_data.not_afk_intervals == ((t0_us, t0_us + 200_000),)
    assert afk_data.afk_intervals == ()


def test_seam_pair_total_over_tolerance_conflicts() -> None:
    """A pair total beyond the tolerance conflicts despite carving."""
    t0 = datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)
    t0_us = datetime_to_us(t0)
    p_end_us = t0_us + 3_600_000_000
    events = [
        RawEventRecord(
            event_id=1,
            timestamp=t0,
            duration_us=1_000_000,
            data={'status': 'not-afk'},
        ),
        RawEventRecord(
            event_id=2,
            timestamp=t0 + timedelta(microseconds=100_000),
            duration_us=150_000,
            data={'status': 'afk'},
        ),
        RawEventRecord(
            event_id=3,
            timestamp=t0 + timedelta(microseconds=250_000),
            duration_us=150_000,
            data={'status': 'afk'},
        ),
        RawEventRecord(
            event_id=4,
            timestamp=t0 + timedelta(microseconds=400_000),
            duration_us=150_000,
            data={'status': 'afk'},
        ),
        RawEventRecord(
            event_id=5,
            timestamp=t0 + timedelta(microseconds=550_000),
            duration_us=150_000,
            data={'status': 'afk'},
        ),
        RawEventRecord(
            event_id=6,
            timestamp=t0 + timedelta(microseconds=700_000),
            duration_us=80_000,
            data={'status': 'afk'},
        ),
        RawEventRecord(
            event_id=7,
            timestamp=t0 + timedelta(microseconds=100_000),
            duration_us=780_000,
            data={'status': 'afk'},
        ),
    ]
    afk_data = compute_afk_intervals(events, period_bounds=(t0_us, p_end_us))
    assert afk_data.has_conflict is True
    assert afk_data.conflict_reason == (
        'contradictory_afk_and_not_afk_in_period'
    )


def test_cross_host_conflict_detection() -> None:
    """Verify two hosts with overlapping active time blocks combined total."""
    t0 = datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)
    t0_us = datetime_to_us(t0)
    t1_us = datetime_to_us(t0 + timedelta(minutes=1))

    host1_intervals = ((t0_us, t1_us),)
    host2_intervals = ((t0_us, t1_us),)

    assert intervals_overlap(host1_intervals, host2_intervals) is True


def test_midnight_split_segment_retention() -> None:
    """Verify 1.2s segment across midnight retains 0.6s on both days."""
    start_dt = datetime(2026, 3, 29, 20, 59, 59, 400000, tzinfo=UTC)
    end_dt = datetime(2026, 3, 29, 21, 0, 0, 600000, tzinfo=UTC)
    start_us = datetime_to_us(start_dt)
    end_us = datetime_to_us(end_dt)

    splits = split_interval_by_local_days(start_us, end_us, 'Europe/Minsk')
    assert len(splits) == 2
    assert splits['2026-03-29'] == 600_000
    assert splits['2026-03-30'] == 600_000
    assert sum(splits.values()) == 1_200_000


def test_compute_period_bounds_and_midnight() -> None:
    """Verify compute_period_bounds_us and resolve_calendar_midnight_utc."""
    d1 = date(2026, 9, 8)
    d2 = date(2026, 9, 9)
    m1 = resolve_calendar_midnight_utc(d1, 'Europe/Minsk')
    m2 = resolve_calendar_midnight_utc(d2 + timedelta(days=1), 'Europe/Minsk')
    p_start, p_end = compute_period_bounds_us(d1, d2, 'Europe/Minsk')
    assert p_start == m1
    assert p_end == m2


def test_unknown_afk_empty_coverage_120s() -> None:
    """Verify active 120s with empty AFK yields 120s unknown AFK."""
    t0 = datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)
    buckets = {
        'aw-watcher-orca_h1': {
            'client': 'aw-watcher-orca',
            'type': 'currentwindow',
            'hostname': 'h1',
        },
        'aw-watcher-afk_h1': {
            'client': 'aw-watcher-afk',
            'type': 'afkstatus',
            'hostname': 'h1',
        },
    }
    catalog = build_source_catalog(buckets)
    events_orca = [
        RawEventRecord(
            event_id=1,
            timestamp=t0,
            duration_us=120_000_000,
            data={'app': 'Orca', 'repo': 'r', 'worktree': 'w', 'title': 't'},
        ),
    ]
    snapshot = FullSnapshotResult(
        catalog=catalog,
        observed_until=t0 + timedelta(hours=1),
        read_started_at=t0,
        read_finished_at=t0,
        events_by_bucket={
            'aw-watcher-orca_h1': tuple(events_orca),
            'aw-watcher-afk_h1': (),
        },
        production_boundary=t0,
        boundary_status='known',
        total_response_bytes=1000,
    )
    res = compute_project_statistics_intervals(
        snapshot=snapshot,
        start_date=date(2026, 9, 8),
        end_date=date(2026, 9, 8),
        zone_name='Europe/Minsk',
    )
    src_res = res.source_results[0]
    assert src_res.project_duration_us == 0
    assert src_res.quality.unknown_afk_us == 120_000_000
    assert src_res.quality.is_reliable is False


def test_cross_host_conflict_pipeline() -> None:
    """Verify cross-host overlap blocks combined total (Finding 2, 5)."""
    t0 = datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)
    buckets = {
        'aw-watcher-orca_h1': {
            'client': 'aw-watcher-orca',
            'type': 'currentwindow',
            'hostname': 'h1',
        },
        'aw-watcher-afk_h1': {
            'client': 'aw-watcher-afk',
            'type': 'afkstatus',
            'hostname': 'h1',
        },
        'aw-watcher-orca_h2': {
            'client': 'aw-watcher-orca',
            'type': 'currentwindow',
            'hostname': 'h2',
        },
        'aw-watcher-afk_h2': {
            'client': 'aw-watcher-afk',
            'type': 'afkstatus',
            'hostname': 'h2',
        },
    }
    catalog = build_source_catalog(buckets)
    ev1 = RawEventRecord(
        event_id=1,
        timestamp=t0 + timedelta(minutes=1),
        duration_us=60_000_000,
        data={'app': 'Orca', 'repo': 'r1', 'worktree': 'w1', 'title': 't1'},
    )
    ev2 = RawEventRecord(
        event_id=2,
        timestamp=t0 + timedelta(minutes=1),
        duration_us=60_000_000,
        data={'app': 'Orca', 'repo': 'r2', 'worktree': 'w2', 'title': 't2'},
    )
    afk_ev = RawEventRecord(
        event_id=3,
        timestamp=t0,
        duration_us=300_000_000,
        data={'status': 'not-afk'},
    )
    snapshot = FullSnapshotResult(
        catalog=catalog,
        observed_until=t0 + timedelta(hours=1),
        read_started_at=t0,
        read_finished_at=t0,
        events_by_bucket={
            'aw-watcher-orca_h1': (ev1,),
            'aw-watcher-afk_h1': (afk_ev,),
            'aw-watcher-orca_h2': (ev2,),
            'aw-watcher-afk_h2': (afk_ev,),
        },
        production_boundary=t0 + timedelta(minutes=1),
        boundary_status='known',
        total_response_bytes=1000,
    )
    res = compute_project_statistics_intervals(
        snapshot=snapshot,
        start_date=date(2026, 9, 8),
        end_date=date(2026, 9, 8),
        zone_name='Europe/Minsk',
    )
    assert res.combined_allowed is False
    assert 'cross_host_active_overlap' in (
        res.combined_prohibition_reason or ''
    )


def test_internal_conflict_pipeline_null_sum() -> None:
    """Verify overlapping distinct identities gives null sum (Finding 2, 5)."""
    t0 = datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)
    buckets = {
        'aw-watcher-orca_h1': {
            'client': 'aw-watcher-orca',
            'type': 'currentwindow',
            'hostname': 'h1',
        },
        'aw-watcher-afk_h1': {
            'client': 'aw-watcher-afk',
            'type': 'afkstatus',
            'hostname': 'h1',
        },
    }
    catalog = build_source_catalog(buckets)
    ev1 = RawEventRecord(
        event_id=1,
        timestamp=t0 + timedelta(minutes=1),
        duration_us=60_000_000,
        data={'app': 'Orca', 'repo': 'r1', 'worktree': 'w1', 'title': 't1'},
    )
    ev2 = RawEventRecord(
        event_id=2,
        timestamp=t0 + timedelta(minutes=1),
        duration_us=60_000_000,
        data={'app': 'Orca', 'repo': 'r2', 'worktree': 'w2', 'title': 't2'},
    )
    afk_ev = RawEventRecord(
        event_id=3,
        timestamp=t0,
        duration_us=300_000_000,
        data={'status': 'not-afk'},
    )
    snapshot = FullSnapshotResult(
        catalog=catalog,
        observed_until=t0 + timedelta(hours=1),
        read_started_at=t0,
        read_finished_at=t0,
        events_by_bucket={
            'aw-watcher-orca_h1': (ev1, ev2),
            'aw-watcher-afk_h1': (afk_ev,),
        },
        production_boundary=t0 + timedelta(minutes=1),
        boundary_status='known',
        total_response_bytes=1000,
    )
    res = compute_project_statistics_intervals(
        snapshot=snapshot,
        start_date=date(2026, 9, 8),
        end_date=date(2026, 9, 8),
        zone_name='Europe/Minsk',
    )
    assert res.source_results[0].project_duration_us is None
    assert res.combined_allowed is False
    assert res.combined_prohibition_reason == 'incomplete_source_sum'


def test_catalog_incomplete_pipeline() -> None:
    """Verify catalog incompleteness blocks combined total (Finding 2)."""
    t0 = datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)
    buckets = {
        'aw-watcher-orca_h1': {
            'client': 'aw-watcher-orca',
            'type': 'currentwindow',
            'hostname': 'h1',
        },
        # Missing AFK
    }
    catalog = build_source_catalog(buckets)
    assert catalog.has_incompleteness is True

    snapshot = FullSnapshotResult(
        catalog=catalog,
        observed_until=t0 + timedelta(hours=1),
        read_started_at=t0,
        read_finished_at=t0,
        events_by_bucket={'aw-watcher-orca_h1': ()},
        production_boundary=None,
        boundary_status=UNKNOWN_BOUNDARY_REASON,
        total_response_bytes=1000,
    )
    res = compute_project_statistics_intervals(
        snapshot=snapshot,
        start_date=date(2026, 9, 8),
        end_date=date(2026, 9, 8),
        zone_name='Europe/Minsk',
    )
    assert res.combined_allowed is False
    assert res.combined_prohibition_reason in (
        'unknown_production_boundary',
        'catalog_incomplete',
    )


def test_absent_unverified_pipeline_allows_test() -> None:
    """Verify absent_unverified boundary allows test-only total (Finding 4)."""
    t0 = datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)
    buckets = {
        'aw-watcher-orca-test_h1': {
            'client': 'aw-watcher-orca-test',
            'type': 'currentwindow',
            'hostname': 'h1',
        },
        'aw-watcher-afk_h1': {
            'client': 'aw-watcher-afk',
            'type': 'afkstatus',
            'hostname': 'h1',
        },
    }
    catalog = build_source_catalog(buckets)
    ev_test = RawEventRecord(
        event_id=1,
        timestamp=t0 + timedelta(minutes=1),
        duration_us=60_000_000,
        data={'app': 'Orca', 'repo': 'r', 'worktree': 'w', 'title': 't'},
    )
    afk_ev = RawEventRecord(
        event_id=2,
        timestamp=t0,
        duration_us=300_000_000,
        data={'status': 'not-afk'},
    )
    snapshot = FullSnapshotResult(
        catalog=catalog,
        observed_until=t0 + timedelta(hours=1),
        read_started_at=t0,
        read_finished_at=t0,
        events_by_bucket={
            'aw-watcher-orca-test_h1': (ev_test,),
            'aw-watcher-afk_h1': (afk_ev,),
        },
        production_boundary=None,
        boundary_status=ABSENT_UNVERIFIED_STATUS,
        total_response_bytes=1000,
    )
    res = compute_project_statistics_intervals(
        snapshot=snapshot,
        start_date=date(2026, 9, 8),
        end_date=date(2026, 9, 8),
        zone_name='Europe/Minsk',
    )
    assert res.combined_allowed is True
    assert res.source_results[0].project_duration_us == 60_000_000


def test_compute_project_statistics_pipeline_end_to_end() -> None:
    """Verify end-to-end compute_project_statistics_intervals."""
    t0 = datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)
    buckets = {
        'aw-watcher-orca_h1': {
            'client': 'aw-watcher-orca',
            'type': 'currentwindow',
            'hostname': 'h1',
        },
        'aw-watcher-afk_h1': {
            'client': 'aw-watcher-afk',
            'type': 'afkstatus',
            'hostname': 'h1',
        },
    }
    catalog = build_source_catalog(buckets)
    events_orca = [
        RawEventRecord(
            event_id=1,
            timestamp=t0 + timedelta(minutes=1),
            duration_us=120_000_000,  # 2 minutes active
            data={
                'app': 'Orca',
                'repo': 'my-repo',
                'worktree': 'feat',
                'title': 'my-repo / feat',
            },
        ),
    ]
    events_afk = [
        RawEventRecord(
            event_id=2,
            timestamp=t0,
            duration_us=300_000_000,  # 5 minutes not-afk
            data={'status': 'not-afk'},
        ),
    ]
    events_by_bucket = {
        'aw-watcher-orca_h1': tuple(events_orca),
        'aw-watcher-afk_h1': tuple(events_afk),
    }
    snapshot = FullSnapshotResult(
        catalog=catalog,
        observed_until=t0 + timedelta(hours=1),
        read_started_at=t0,
        read_finished_at=t0,
        events_by_bucket=events_by_bucket,
        production_boundary=t0 + timedelta(minutes=1),
        boundary_status='known',
        total_response_bytes=1000,
    )

    res = compute_project_statistics_intervals(
        snapshot=snapshot,
        start_date=date(2026, 9, 8),
        end_date=date(2026, 9, 8),
        zone_name='Europe/Minsk',
    )
    assert res.combined_allowed is True
    assert len(res.source_results) == 1
    src_res = res.source_results[0]
    assert src_res.project_duration_us == 120_000_000
    assert len(src_res.worktree_rows) == 1
    wt_row = src_res.worktree_rows[0]
    assert wt_row.repo == 'my-repo'
    assert wt_row.worktree == 'feat'
    assert len(wt_row.daily_rows) == 1

    unique_cnt, seg_cnt = compute_contributing_events_summary(
        wt_row.daily_rows
    )
    assert unique_cnt == 1
    assert seg_cnt == 1
    assert res.counters.raw_events == 2
    assert res.counters.contributing_events == 1
    assert res.counters.derived_segments == 1


def test_time14_project_filter_does_not_hide_identity_conflict() -> None:
    """Verify TIME-14: a non-matching project filter never hides a conflict.

    projB and projC overlap inside the period; the filter selects 'projA'
    (nothing). Pre-fix the conflict checks ran on the filtered accumulation
    and never fired; per TIME-14 the filter must not hide the conflict.
    """
    t0 = datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)
    buckets = {
        'aw-watcher-orca_h1': {
            'client': 'aw-watcher-orca',
            'type': 'currentwindow',
            'hostname': 'h1',
        },
        'aw-watcher-afk_h1': {
            'client': 'aw-watcher-afk',
            'type': 'afkstatus',
            'hostname': 'h1',
        },
    }
    catalog = build_source_catalog(buckets)
    ev_b = RawEventRecord(
        event_id=1,
        timestamp=t0 + timedelta(minutes=1),
        duration_us=600_000_000,
        data={'app': 'Orca', 'repo': 'projB', 'worktree': 'w', 'title': 't'},
    )
    ev_c = RawEventRecord(
        event_id=2,
        timestamp=t0 + timedelta(minutes=4),
        duration_us=600_000_000,
        data={'app': 'Orca', 'repo': 'projC', 'worktree': 'w', 'title': 't'},
    )
    afk_ev = RawEventRecord(
        event_id=3,
        timestamp=t0,
        duration_us=3_600_000_000,
        data={'status': 'not-afk'},
    )
    snapshot = FullSnapshotResult(
        catalog=catalog,
        observed_until=t0 + timedelta(hours=1),
        read_started_at=t0,
        read_finished_at=t0,
        events_by_bucket={
            'aw-watcher-orca_h1': (ev_b, ev_c),
            'aw-watcher-afk_h1': (afk_ev,),
        },
        production_boundary=t0,
        boundary_status='known',
        total_response_bytes=1000,
    )

    for project_filter in ('projA', 'projB'):
        res = compute_project_statistics_intervals(
            snapshot=snapshot,
            start_date=date(2026, 9, 8),
            end_date=date(2026, 9, 8),
            zone_name='Europe/Minsk',
            project_filter=project_filter,
        )
        src = res.source_results[0]
        assert res.combined_allowed is False, project_filter
        # Combined-level reason: null source sums (R3 semantics); the
        # per-source reason carries the specific conflict cause.
        assert res.combined_prohibition_reason == 'incomplete_source_sum', (
            project_filter
        )
        assert src.is_conflict is True, project_filter
        assert src.project_duration_us is None, project_filter
        assert src.conflict_reason == (
            'multiple_distinct_identities_overlap_in_period'
        ), project_filter
