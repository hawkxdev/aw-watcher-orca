"""Tests for ReportService: composition root, lifecycle, and comparison."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from aw_watcher_orca.report_models import (
    DataQualityCounter,
    RawEventRecord,
    ReportApiError,
    ReportBusyError,
    ReportIncompleteError,
    ReportLimitError,
)
from aw_watcher_orca.report_reader import FullSnapshotResult
from aw_watcher_orca.report_service import (
    ReportService,
    ReportServiceError,
    ServiceComparisonResult,
    ServiceReportResult,
)
from aw_watcher_orca.report_settings import (
    DEFAULT_TIMEZONE,
)
from aw_watcher_orca.report_sources import SourceCatalog, build_source_catalog
from report_fixtures import (
    EXPECTED_C04,
)


def _make_sample_catalog() -> SourceCatalog:
    """Create sample valid catalog with test, production, standard, afk."""
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
        'aw-watcher-window_h1': {
            'client': 'aw-watcher-window',
            'type': 'currentwindow',
            'hostname': 'h1',
        },
    }
    return build_source_catalog(buckets)


def _make_sample_snapshot(
    catalog: SourceCatalog | None = None,
    observed_until: datetime | None = None,
) -> FullSnapshotResult:
    """Create sample verified snapshot for service tests."""
    cat = catalog or _make_sample_catalog()
    obs = observed_until or datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
    t0 = obs - timedelta(hours=2)

    events_orca = (
        RawEventRecord(
            event_id=1,
            timestamp=t0 + timedelta(minutes=10),
            duration_us=300_000_000,
            data={
                'app': 'Orca',
                'repo': 'my-repo',
                'worktree': 'main',
                'title': 'my-repo / main',
            },
        ),
    )
    events_afk = (
        RawEventRecord(
            event_id=1,
            timestamp=t0,
            duration_us=7200_000_000,
            data={'status': 'not-afk'},
        ),
    )
    events_by_bucket = {
        'aw-watcher-orca_h1': events_orca,
        'aw-watcher-afk_h1': events_afk,
    }

    return FullSnapshotResult(
        catalog=cat,
        observed_until=obs,
        read_started_at=t0,
        read_finished_at=t0 + timedelta(seconds=1),
        events_by_bucket=events_by_bucket,
        production_boundary=t0 + timedelta(minutes=10),
        boundary_status='known',
        total_response_bytes=2048,
    )


# === Service Tests ===


def test_report_service_happy_path() -> None:
    """Verify end-to-end report calculation, report_id, and OUT-01 fields."""
    snapshot = _make_sample_snapshot()
    service = ReportService(base_url='http://127.0.0.1:5600')

    # Mock catalog and reader call
    service.get_catalog = MagicMock(return_value=snapshot.catalog)  # type: ignore[method-assign]
    service._fetch_snapshot = MagicMock(return_value=snapshot)  # type: ignore[method-assign]
    result = service.calculate_report(
        start_date=date(2026, 9, 8),
        end_date=date(2026, 9, 8),
        zone_name=DEFAULT_TIMEZONE,
    )

    assert isinstance(result, ServiceReportResult)
    assert len(result.report_id) >= 16
    assert result.observed_until == snapshot.observed_until
    assert result.combined_allowed is True
    assert result.inventory.paired == snapshot.catalog.inventory.paired
    assert len(result.project_rows) == 1
    assert result.project_rows[0].repo == 'my-repo'
    assert result.project_rows[0].worktree == 'main'
    assert result.project_rows[0].duration_us == 300_000_000
    assert len(result.daily_rows) == 1
    assert result.daily_rows[0].day == '2026-09-08'
    assert result.daily_rows[0].duration_us == 300_000_000
    assert result.counters.contributing_events == 1
    assert result.counters.derived_segments == 1


def test_report_service_c04_universe_unknown_afk() -> None:
    """C04 in service layer: period universe 600s - active 180s = 420s."""
    t0 = datetime(2026, 9, 8, 0, 0, 0, tzinfo=UTC)
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
    events_orca = (
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
    )
    snapshot = FullSnapshotResult(
        catalog=catalog,
        observed_until=t0 + timedelta(seconds=600),
        read_started_at=t0,
        read_finished_at=t0,
        events_by_bucket={
            'aw-watcher-orca_h1': events_orca,
            'aw-watcher-afk_h1': (),
        },
        production_boundary=t0 + timedelta(seconds=120),
        boundary_status='known',
        total_response_bytes=1000,
    )

    service = ReportService()
    service.get_catalog = MagicMock(return_value=snapshot.catalog)  # type: ignore[method-assign]
    service._fetch_snapshot = MagicMock(return_value=snapshot)  # type: ignore[method-assign]
    result = service.calculate_report(
        start_date=date(2026, 9, 8),
        end_date=date(2026, 9, 8),
        zone_name='UTC',
    )
    src_summary = result.source_summaries[0]
    # Service layer computes unknown_afk_us over period universe (Variant A)
    assert src_summary.quality.unknown_afk_us == EXPECTED_C04


def test_report_service_c04_universe_mixed_coverage_unknown_afk() -> None:
    """Verify C04 mixed coverage: 180s active, 100s not-afk -> 420s unknown."""
    t0 = datetime(2026, 9, 8, 0, 0, 0, tzinfo=UTC)
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
    # Active 180s [120s..300s]
    events_orca = (
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
    )
    # not-afk 100s [120s..220s]
    events_afk = (
        RawEventRecord(
            event_id=1,
            timestamp=t0 + timedelta(seconds=120),
            duration_us=100_000_000,
            data={'status': 'not-afk'},
        ),
    )
    snapshot = FullSnapshotResult(
        catalog=catalog,
        observed_until=t0 + timedelta(seconds=600),
        read_started_at=t0,
        read_finished_at=t0,
        events_by_bucket={
            'aw-watcher-orca_h1': events_orca,
            'aw-watcher-afk_h1': events_afk,
        },
        production_boundary=t0 + timedelta(seconds=120),
        boundary_status='known',
        total_response_bytes=1000,
    )

    service = ReportService()
    service.get_catalog = MagicMock(return_value=snapshot.catalog)  # type: ignore[method-assign]
    service._fetch_snapshot = MagicMock(return_value=snapshot)  # type: ignore[method-assign]
    result = service.calculate_report(
        start_date=date(2026, 9, 8),
        end_date=date(2026, 9, 8),
        zone_name='UTC',
    )
    src_summary = result.source_summaries[0]
    assert src_summary.project_duration_us == 100_000_000
    assert src_summary.quality.unknown_afk_us == EXPECTED_C04  # 420_000_000


def test_report_service_raw_vs_segments_counters() -> None:
    """Verify OUT-02/03: raw != derived segments != contributing."""
    snapshot = _make_sample_snapshot()
    service = ReportService()
    service.get_catalog = MagicMock(return_value=snapshot.catalog)  # type: ignore[method-assign]
    service._fetch_snapshot = MagicMock(return_value=snapshot)  # type: ignore[method-assign]
    result = service.calculate_report(
        start_date=date(2026, 9, 8),
        end_date=date(2026, 9, 8),
        zone_name=DEFAULT_TIMEZONE,
    )
    counters = result.counters
    assert isinstance(counters, DataQualityCounter)
    assert counters.raw_events >= counters.contributing_events
    assert counters.contributing_events > 0


def test_report_service_calendar_days_limit_exceeded() -> None:
    """Verify requesting more than MAX_CALENDAR_DAYS raises error."""
    service = ReportService()
    with pytest.raises(ReportLimitError):
        service.calculate_report(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 2, 15),  # 46 days > 31
            zone_name=DEFAULT_TIMEZONE,
        )


def test_report_service_concurrent_calculation_raises_busy() -> None:
    """Verify concurrent calculation attempt raises ReportBusyError."""
    service = ReportService()
    service._is_calculating = True
    try:
        with pytest.raises(ReportBusyError):
            service.calculate_report(
                start_date=date(2026, 9, 8),
                end_date=date(2026, 9, 8),
            )
    finally:
        service._is_calculating = False


def test_report_service_snapshot_ttl_expiration() -> None:
    """Verify standard comparison on snapshot older than 15 min fails."""
    snapshot = _make_sample_snapshot()
    service = ReportService()
    service.get_catalog = MagicMock(return_value=snapshot.catalog)  # type: ignore[method-assign]
    service._fetch_snapshot = MagicMock(return_value=snapshot)  # type: ignore[method-assign]
    res = service.calculate_report(
        start_date=date(2026, 9, 8),
        end_date=date(2026, 9, 8),
    )
    report_id = res.report_id

    # Advance cached_at timestamp by 16 minutes (> 15 min TTL)
    service._cached_at = datetime.now(UTC) - timedelta(minutes=16)

    with pytest.raises(ReportServiceError) as exc_info:
        service.calculate_comparison(report_id=report_id)
    assert exc_info.value.reason_code == 'expired_report_id'


def test_report_service_unknown_report_id_rejected() -> None:
    """Verify unknown report_id is rejected without recalculation."""
    service = ReportService()
    with pytest.raises(ReportServiceError) as exc_info:
        service.calculate_comparison(report_id='unknown-id-12345')
    assert exc_info.value.reason_code == 'unknown_report_id'


def test_report_service_standard_comparison_happy_path() -> None:
    """Verify standard comparison reads and aggregates window events."""
    snapshot = _make_sample_snapshot()
    service = ReportService()
    service.get_catalog = MagicMock(return_value=snapshot.catalog)  # type: ignore[method-assign]
    service._fetch_snapshot = MagicMock(return_value=snapshot)  # type: ignore[method-assign]
    std_events = (
        RawEventRecord(
            event_id=10,
            timestamp=snapshot.observed_until - timedelta(minutes=30),
            duration_us=120_000_000,
            data={'app': 'Orca', 'title': 'workspace window'},
        ),
    )
    service._fetch_standard_events = MagicMock(return_value=std_events)  # type: ignore[method-assign]

    rep_res = service.calculate_report(
        start_date=date(2026, 9, 8),
        end_date=date(2026, 9, 8),
    )
    comp_res = service.calculate_comparison(report_id=rep_res.report_id)

    assert isinstance(comp_res, ServiceComparisonResult)
    assert comp_res.report_id == rep_res.report_id
    assert comp_res.standard_duration_us == 120_000_000
    assert comp_res.read_started_at is not None
    assert comp_res.read_finished_at is not None
    assert len(comp_res.host_rows) == 1
    assert comp_res.host_rows[0].host_suffix == 'h1'


def test_report_service_comparison_failure_preserves_project() -> None:
    """Verify comparison failure does NOT erase project result."""
    snapshot = _make_sample_snapshot()
    service = ReportService()
    service.get_catalog = MagicMock(return_value=snapshot.catalog)  # type: ignore[method-assign]
    service._fetch_snapshot = MagicMock(return_value=snapshot)  # type: ignore[method-assign]
    service._fetch_standard_events = MagicMock(  # type: ignore[method-assign]
        side_effect=ReportApiError('ActivityWatch unavailable for query')
    )

    rep_res = service.calculate_report(
        start_date=date(2026, 9, 8),
        end_date=date(2026, 9, 8),
    )
    report_id = rep_res.report_id

    # Attempt comparison which fails
    with pytest.raises(ReportApiError):
        service.calculate_comparison(report_id=report_id)

    # Verify cached project result and snapshot are still valid
    cached = service.get_cached_report(report_id=report_id)
    assert cached is not None
    assert cached.report_id == report_id


def test_report_service_snapshot_replaced_only_on_success() -> None:
    """Verify failed calculation does NOT overwrite previous snapshot."""
    snapshot1 = _make_sample_snapshot()
    service = ReportService()
    service.get_catalog = MagicMock(return_value=snapshot1.catalog)  # type: ignore[method-assign]
    service._fetch_snapshot = MagicMock(return_value=snapshot1)  # type: ignore[method-assign]
    rep_res1 = service.calculate_report(
        start_date=date(2026, 9, 8),
        end_date=date(2026, 9, 8),
    )
    first_report_id = rep_res1.report_id

    # Second calculation attempt fails
    service._fetch_snapshot = MagicMock(  # type: ignore[method-assign]
        side_effect=ReportIncompleteError('Bucket disappeared')
    )

    with pytest.raises(ReportIncompleteError):
        service.calculate_report(
            start_date=date(2026, 9, 8),
            end_date=date(2026, 9, 8),
        )

    # First report snapshot is still preserved
    cached = service.get_cached_report(report_id=first_report_id)
    assert cached is not None
    assert cached.report_id == first_report_id


def test_report_service_earliest_production_boundary_memory() -> None:
    """Verify service remembers minimal production boundary."""
    t0 = datetime(2026, 9, 7, 10, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC)

    service = ReportService()
    assert service.earliest_production_boundary is None

    # First snapshot observed boundary at t0
    service.record_production_boundary(t0)
    assert service.earliest_production_boundary == t0

    # Second snapshot observed later boundary t1 -> memory stays at earliest t0
    service.record_production_boundary(t1)
    assert service.earliest_production_boundary == t0


def test_report_service_snapshot_bytes_limit_exceeded() -> None:
    """Verify snapshot bytes > max_snapshot_bytes raises ReportLimitError."""
    snapshot = _make_sample_snapshot()
    # Override total_response_bytes > 32 MiB
    oversized_snapshot = FullSnapshotResult(
        catalog=snapshot.catalog,
        observed_until=snapshot.observed_until,
        read_started_at=snapshot.read_started_at,
        read_finished_at=snapshot.read_finished_at,
        events_by_bucket=snapshot.events_by_bucket,
        production_boundary=snapshot.production_boundary,
        boundary_status=snapshot.boundary_status,
        total_response_bytes=33 * 1024 * 1024,
    )
    service = ReportService()
    service.get_catalog = MagicMock(return_value=snapshot.catalog)  # type: ignore[method-assign]
    service._fetch_snapshot = MagicMock(return_value=oversized_snapshot)  # type: ignore[method-assign]

    with pytest.raises(ReportLimitError):
        service.calculate_report(
            start_date=date(2026, 9, 8),
            end_date=date(2026, 9, 8),
        )


def test_report_service_comparison_standard_source_absent() -> None:
    """Verify comparison when no standard sources exist returns absent."""
    # Catalog without standard sources
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
    snapshot = _make_sample_snapshot(catalog=catalog)
    service = ReportService()
    service.get_catalog = MagicMock(return_value=catalog)  # type: ignore[method-assign]
    service._fetch_snapshot = MagicMock(return_value=snapshot)  # type: ignore[method-assign]

    rep = service.calculate_report(
        start_date=date(2026, 9, 8), end_date=date(2026, 9, 8)
    )
    comp = service.calculate_comparison(report_id=rep.report_id)

    assert comp.is_available is False
    assert comp.unavailable_reason == 'standard_source_absent'
    assert comp.standard_duration_us is None


def test_report_service_comparison_cross_host_conflict() -> None:
    """Verify TIME-16: overlapping standard events on different hosts."""
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
        'aw-watcher-window_h1': {
            'client': 'aw-watcher-window',
            'type': 'currentwindow',
            'hostname': 'h1',
        },
        'aw-watcher-window_h2': {
            'client': 'aw-watcher-window',
            'type': 'currentwindow',
            'hostname': 'h2',
        },
    }
    catalog = build_source_catalog(buckets)
    snapshot = _make_sample_snapshot(catalog=catalog)
    service = ReportService()
    service.get_catalog = MagicMock(return_value=catalog)  # type: ignore[method-assign]
    service._fetch_snapshot = MagicMock(return_value=snapshot)  # type: ignore[method-assign]

    # Mock standard events for h1 and h2 with overlapping timestamps
    t_obs = snapshot.observed_until
    ev_h1 = (
        RawEventRecord(
            event_id=1,
            timestamp=t_obs - timedelta(minutes=30),
            duration_us=600_000_000,
            data={'app': 'Orca', 'title': 'w1'},
        ),
    )
    ev_h2 = (
        RawEventRecord(
            event_id=2,
            timestamp=t_obs - timedelta(minutes=25),
            duration_us=600_000_000,
            data={'app': 'Orca', 'title': 'w2'},
        ),
    )

    def mock_fetch_std(
        bucket_id: str, observed_until: datetime | None
    ) -> tuple[RawEventRecord, ...]:
        if 'h1' in bucket_id:
            return ev_h1
        return ev_h2

    service._fetch_standard_events = MagicMock(side_effect=mock_fetch_std)  # type: ignore[method-assign]

    rep = service.calculate_report(
        start_date=date(2026, 9, 8), end_date=date(2026, 9, 8)
    )
    comp = service.calculate_comparison(report_id=rep.report_id)

    assert comp.is_available is True
    assert comp.has_cross_host_conflict is True
    assert comp.standard_duration_us is None
    assert len(comp.host_rows) == 2


def test_report_service_freshness_fresh_snapshot() -> None:
    """Verify fresh snapshot (< 30s) produces stale=False (OUT-07)."""
    obs = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
    event_end = obs - timedelta(seconds=10)
    event_start = event_end - timedelta(seconds=5)

    catalog = _make_sample_catalog()
    events = {
        'aw-watcher-orca_h1': (
            RawEventRecord(
                event_id=1,
                timestamp=event_start,
                duration_us=5_000_000,
                data={
                    'app': 'Orca',
                    'repo': 'my-repo',
                    'worktree': 'main',
                    'title': 'my-repo / main',
                },
            ),
        ),
        'aw-watcher-afk_h1': (
            RawEventRecord(
                event_id=1,
                timestamp=obs - timedelta(hours=1),
                duration_us=3590_000_000,
                data={'status': 'not-afk'},
            ),
        ),
    }
    snapshot = FullSnapshotResult(
        catalog=catalog,
        observed_until=obs,
        read_started_at=obs - timedelta(seconds=2),
        read_finished_at=obs - timedelta(seconds=1),
        events_by_bucket=events,
        production_boundary=event_start,
        boundary_status='known',
        total_response_bytes=1024,
    )
    service = ReportService()
    service.get_catalog = MagicMock(return_value=catalog)  # type: ignore[method-assign]
    service._fetch_snapshot = MagicMock(return_value=snapshot)  # type: ignore[method-assign]

    result = service.calculate_report(
        start_date=date(2026, 9, 8), end_date=date(2026, 9, 8)
    )
    assert result.freshness.stale is False
    assert result.freshness.max_event_end == event_end
    assert result.freshness.observed_until == obs


def test_report_service_freshness_stale_snapshot() -> None:
    """Verify snapshot older than 30s produces stale=True (OUT-07)."""
    obs = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
    event_end = obs - timedelta(seconds=31)
    event_start = event_end - timedelta(seconds=5)

    catalog = _make_sample_catalog()
    events = {
        'aw-watcher-orca_h1': (
            RawEventRecord(
                event_id=1,
                timestamp=event_start,
                duration_us=5_000_000,
                data={
                    'app': 'Orca',
                    'repo': 'my-repo',
                    'worktree': 'main',
                    'title': 'my-repo / main',
                },
            ),
        ),
        'aw-watcher-afk_h1': (
            RawEventRecord(
                event_id=1,
                timestamp=obs - timedelta(hours=1),
                duration_us=3500_000_000,
                data={'status': 'not-afk'},
            ),
        ),
    }
    snapshot = FullSnapshotResult(
        catalog=catalog,
        observed_until=obs,
        read_started_at=obs - timedelta(seconds=2),
        read_finished_at=obs - timedelta(seconds=1),
        events_by_bucket=events,
        production_boundary=event_start,
        boundary_status='known',
        total_response_bytes=1024,
    )
    service = ReportService()
    service.get_catalog = MagicMock(return_value=catalog)  # type: ignore[method-assign]
    service._fetch_snapshot = MagicMock(return_value=snapshot)  # type: ignore[method-assign]

    result = service.calculate_report(
        start_date=date(2026, 9, 8), end_date=date(2026, 9, 8)
    )
    assert result.freshness.stale is True
    assert result.freshness.max_event_end == event_end


def test_report_service_freshness_empty_snapshot() -> None:
    """Verify empty snapshot produces max_event_end=None and stale=True."""
    obs = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
    catalog = _make_sample_catalog()
    events: dict[str, tuple[RawEventRecord, ...]] = {
        'aw-watcher-orca_h1': (),
        'aw-watcher-afk_h1': (),
    }
    snapshot = FullSnapshotResult(
        catalog=catalog,
        observed_until=obs,
        read_started_at=obs - timedelta(seconds=2),
        read_finished_at=obs - timedelta(seconds=1),
        events_by_bucket=events,
        production_boundary=None,
        boundary_status='unknown',
        total_response_bytes=256,
    )
    service = ReportService()
    service.get_catalog = MagicMock(return_value=catalog)  # type: ignore[method-assign]
    service._fetch_snapshot = MagicMock(return_value=snapshot)  # type: ignore[method-assign]

    result = service.calculate_report(
        start_date=date(2026, 9, 8), end_date=date(2026, 9, 8)
    )
    assert result.freshness.max_event_end is None
    assert result.freshness.stale is True


def test_report_service_freshness_boundary_exact_threshold() -> None:
    """Verify event with end exactly at threshold (30.0s) is not stale."""
    obs = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
    # Event end is exactly 30.0 seconds before obs
    event_end = obs - timedelta(seconds=30)
    # Event has positive duration (5.0s), start is 35.0s before obs
    event_start = event_end - timedelta(seconds=5)

    catalog = _make_sample_catalog()
    events = {
        'aw-watcher-orca_h1': (
            RawEventRecord(
                event_id=1,
                timestamp=event_start,
                duration_us=5_000_000,
                data={
                    'app': 'Orca',
                    'repo': 'my-repo',
                    'worktree': 'main',
                    'title': 'my-repo / main',
                },
            ),
        ),
        'aw-watcher-afk_h1': (
            RawEventRecord(
                event_id=1,
                timestamp=obs - timedelta(hours=1),
                duration_us=3570_000_000,
                data={'status': 'not-afk'},
            ),
        ),
    }
    snapshot = FullSnapshotResult(
        catalog=catalog,
        observed_until=obs,
        read_started_at=obs - timedelta(seconds=2),
        read_finished_at=obs - timedelta(seconds=1),
        events_by_bucket=events,
        production_boundary=event_start,
        boundary_status='known',
        total_response_bytes=1024,
    )
    service = ReportService()
    service.get_catalog = MagicMock(return_value=catalog)  # type: ignore[method-assign]
    service._fetch_snapshot = MagicMock(return_value=snapshot)  # type: ignore[method-assign]

    result = service.calculate_report(
        start_date=date(2026, 9, 8), end_date=date(2026, 9, 8)
    )
    # Exact 30.0s age is NOT > 30.0s, so stale must be False
    assert result.freshness.stale is False
    assert result.freshness.max_event_end == event_end


def test_report_service_freshness_last_updated_preserved() -> None:
    """Verify last_updated from snapshot is preserved in freshness block."""
    obs = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
    last_up = datetime(2026, 9, 8, 11, 55, 0, tzinfo=UTC)
    read_fin = obs - timedelta(seconds=1)

    catalog = _make_sample_catalog()
    snapshot = FullSnapshotResult(
        catalog=catalog,
        observed_until=obs,
        read_started_at=obs - timedelta(seconds=2),
        read_finished_at=read_fin,
        events_by_bucket={'aw-watcher-orca_h1': (), 'aw-watcher-afk_h1': ()},
        production_boundary=None,
        boundary_status='unknown',
        total_response_bytes=256,
        last_updated=last_up,
    )
    service = ReportService()
    service.get_catalog = MagicMock(return_value=catalog)  # type: ignore[method-assign]
    service._fetch_snapshot = MagicMock(return_value=snapshot)  # type: ignore[method-assign]

    result = service.calculate_report(
        start_date=date(2026, 9, 8), end_date=date(2026, 9, 8)
    )
    assert result.freshness.last_updated == last_up
    assert result.freshness.last_updated != read_fin
