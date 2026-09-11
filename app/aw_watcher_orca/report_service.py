"""Service layer for project reporting, snapshot lifecycle, and comparison."""

import secrets
import threading
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from urllib.request import OpenerDirector

from aw_watcher_orca.errors import OrcaCoreError
from aw_watcher_orca.report_intervals import (
    ProjectWorktreeRow,
    compute_project_statistics_intervals,
    datetime_to_us,
    intersect_intervals,
    intervals_overlap_in_period,
    span_us,
)
from aw_watcher_orca.report_models import (
    CALCULATION_BUSY_REASON,
    DataQualityCounter,
    InventorySummary,
    PeriodInterval,
    QualityBreakdown,
    RawEventRecord,
    ReportBusyError,
    ReportFreshness,
    ReportLimitError,
    ReportMalformedPayloadError,
)
from aw_watcher_orca.report_reader import (
    FullSnapshotResult,
    _http_get_json,
    read_full_reporting_snapshot,
    read_standard_comparison_events,
)
from aw_watcher_orca.report_settings import (
    DEFAULT_ACTIVITYWATCH_BASE_URL,
    DEFAULT_TIMEZONE,
    ReportSettings,
    validate_calendar_days_limit,
)
from aw_watcher_orca.report_sources import (
    SourceCatalog,
    build_source_catalog,
)

# === Error Taxonomy ===


class ReportServiceError(OrcaCoreError):
    """Exception raised for service-layer lifecycle and validation errors."""

    def __init__(self, reason_code: str, message: str) -> None:
        """Initialize ReportServiceError with reason code and message."""
        super().__init__(message)
        self.reason_code = reason_code


# === Service Data Structures (OUT-01..08) ===


@dataclass(frozen=True, slots=True)
class SourceReportSummary:
    """Hold summary and quality metrics for one reporting source."""

    alias: str
    bucket_id: str
    host_suffix: str
    source_kind: str
    project_duration_us: int | None
    project_seconds: float | None
    is_conflict: bool
    conflict_reason: str | None
    quality: QualityBreakdown


@dataclass(frozen=True, slots=True)
class ProjectDayAggregateRow:
    """Hold daily project active duration across all worktrees."""

    day: str
    duration_us: int
    seconds: float
    contributing_events: int
    derived_segments: int


@dataclass(frozen=True, slots=True)
class ServiceReportResult:
    """Hold complete calculated report response payload (OUT-01)."""

    report_id: str
    observed_until: datetime
    read_started_at: datetime
    read_finished_at: datetime
    period: PeriodInterval
    zone_name: str
    inventory: InventorySummary
    production_boundary: datetime | None
    boundary_status: str
    source_summaries: tuple[SourceReportSummary, ...]
    project_rows: tuple[ProjectWorktreeRow, ...]
    daily_rows: tuple[ProjectDayAggregateRow, ...]
    counters: DataQualityCounter
    combined_allowed: bool
    combined_prohibition_reason: str | None
    freshness: ReportFreshness


@dataclass(frozen=True, slots=True)
class StandardComparisonHostRow:
    """Hold standard window aggregate metrics for one host."""

    host_suffix: str
    bucket_id: str
    duration_us: int
    seconds: float
    event_count: int


@dataclass(frozen=True, slots=True)
class ServiceComparisonResult:
    """Hold standard window comparison calculation result (OUT-05, TIME-16)."""

    report_id: str
    observed_until: datetime
    read_started_at: datetime
    read_finished_at: datetime
    standard_duration_us: int | None
    standard_seconds: float | None
    host_rows: tuple[StandardComparisonHostRow, ...]
    has_cross_host_conflict: bool
    conflict_reason: str | None
    is_available: bool
    unavailable_reason: str | None


# === Aggregation Helpers ===


def aggregate_project_daily_rows(
    project_rows: tuple[ProjectWorktreeRow, ...],
) -> tuple[ProjectDayAggregateRow, ...]:
    """Aggregate daily metrics across all project worktrees (OUT-03).

    For each day, contributing_events counts unique event keys of that day.
    """
    daily_durations: dict[str, int] = defaultdict(int)
    daily_keys: dict[str, set[tuple[str, int]]] = defaultdict(set)
    daily_segments: dict[str, int] = defaultdict(int)

    for wt_row in project_rows:
        for d_row in wt_row.daily_rows:
            day_str = d_row.day
            daily_durations[day_str] += d_row.duration_us
            daily_keys[day_str].update(d_row.contributing_event_keys)
            daily_segments[day_str] += d_row.derived_segments_count

    aggregated: list[ProjectDayAggregateRow] = []
    for d_str in sorted(daily_durations.keys()):
        d_us = daily_durations[d_str]
        aggregated.append(
            ProjectDayAggregateRow(
                day=d_str,
                duration_us=d_us,
                seconds=d_us / 1_000_000,
                contributing_events=len(daily_keys[d_str]),
                derived_segments=daily_segments[d_str],
            )
        )

    return tuple(aggregated)


# === Report Service (OUT-05, LIMIT-05) ===


class ReportService:
    """Manage reporting snapshot cache, report calculation, and comparison."""

    def __init__(
        self,
        base_url: str = DEFAULT_ACTIVITYWATCH_BASE_URL,
        settings: ReportSettings | None = None,
        opener: OpenerDirector | None = None,
    ) -> None:
        """Initialize ReportService with base URL and optional settings."""
        self.base_url = base_url
        self.settings = settings or ReportSettings()
        self.opener = opener

        self._lock = threading.Lock()
        self._is_calculating = False

        self._cached_snapshot: FullSnapshotResult | None = None
        self._cached_report_id: str | None = None
        self._cached_report_result: ServiceReportResult | None = None
        self._cached_at: datetime | None = None
        self._earliest_production_boundary: datetime | None = None

    @property
    def earliest_production_boundary(self) -> datetime | None:
        """Return the earliest production boundary observed in process life."""
        return self._earliest_production_boundary

    def record_production_boundary(self, boundary: datetime | None) -> None:
        """Record and remember minimal production boundary (SRC-08)."""
        if boundary is None:
            return
        if (
            self._earliest_production_boundary is None
            or boundary < self._earliest_production_boundary
        ):
            self._earliest_production_boundary = boundary

    def _fetch_snapshot(
        self,
        catalog: SourceCatalog,
        observed_until: datetime | None,
    ) -> FullSnapshotResult:
        """Fetch verified snapshot from ActivityWatch."""
        return read_full_reporting_snapshot(
            catalog=catalog,
            base_url=self.base_url,
            observed_until=observed_until,
            settings=self.settings,
            opener=self.opener,
        )

    def _fetch_standard_events(
        self,
        bucket_id: str,
        observed_until: datetime | None,
    ) -> tuple[RawEventRecord, ...]:
        """Fetch standard window events for comparison from ActivityWatch."""
        return read_standard_comparison_events(
            bucket_id=bucket_id,
            base_url=self.base_url,
            observed_until=observed_until,
            settings=self.settings,
            opener=self.opener,
        )

    def get_catalog(self) -> SourceCatalog:
        """Fetch and classify historical ActivityWatch buckets catalog."""
        buckets_url = f'{self.base_url}/api/0/buckets/'
        buckets_data, _ = _http_get_json(
            url=buckets_url,
            timeout=self.settings.default_read_timeout_seconds,
            max_bytes=self.settings.max_response_bytes,
            opener=self.opener,
        )
        if not isinstance(buckets_data, Mapping):
            raise ReportMalformedPayloadError(
                'ActivityWatch buckets response must be mapping'
            )
        return build_source_catalog(
            buckets=buckets_data,
            max_inventory_buckets=self.settings.max_inventory_buckets,
            max_supported_hosts=self.settings.max_supported_hosts,
        )

    def calculate_report(
        self,
        start_date: date,
        end_date: date,
        zone_name: str = DEFAULT_TIMEZONE,
        project_filter: str | None = None,
        exact_project_match: bool = False,
        observed_until: datetime | None = None,
    ) -> ServiceReportResult:
        """Calculate project reporting statistics over verified snapshot."""
        # 1. Concurrency guard (LIMIT-05)
        with self._lock:
            if self._is_calculating:
                raise ReportBusyError(CALCULATION_BUSY_REASON)
            self._is_calculating = True

        try:
            # 2. Validate calendar days limit (LIMIT-01)
            days_count = (end_date - start_date).days + 1
            validate_calendar_days_limit(
                days_count, self.settings.max_calendar_days
            )

            # 3. Fetch catalog and snapshot
            catalog = self.get_catalog()
            snapshot = self._fetch_snapshot(
                catalog=catalog,
                observed_until=observed_until,
            )

            # 4. Memory budget verification (LIMIT-05)
            if (
                snapshot.total_response_bytes
                > self.settings.max_snapshot_bytes
            ):
                raise ReportLimitError(
                    f'Snapshot bytes ({snapshot.total_response_bytes}) '
                    f'exceeds limit ({self.settings.max_snapshot_bytes})'
                )

            # 5. Record minimal production boundary (SRC-08)
            self.record_production_boundary(snapshot.production_boundary)

            # 6. Execute interval calculation
            calc_result = compute_project_statistics_intervals(
                snapshot=snapshot,
                start_date=start_date,
                end_date=end_date,
                zone_name=zone_name,
                project_filter=project_filter,
                exact_project_match=exact_project_match,
                noise_threshold_us=self.settings.noise_threshold_us,
            )

            # 7. Build source summaries with stable aliases and quality
            p_start_us = datetime_to_us(calc_result.period.start_utc)
            p_end_us = datetime_to_us(calc_result.period.end_utc)
            period_universe_us = max(0, p_end_us - p_start_us)

            source_summaries: list[SourceReportSummary] = []
            for idx, s_res in enumerate(calc_result.source_results):
                entry = s_res.source_entry
                alias = f'source_{idx}'
                p_us = s_res.project_duration_us
                p_sec = (p_us / 1_000_000) if p_us is not None else None

                # Recompute unknown_afk_us based on period universe (Variant A)
                if s_res.is_conflict:
                    service_quality = s_res.quality
                else:
                    covered_us = (
                        s_res.quality.neutral_us
                        + s_res.quality.confirmed_afk_us
                        + s_res.quality.project_contributing_us
                        + s_res.quality.noise_us
                        + s_res.quality.unknown_afk_us
                    )
                    service_unknown_us = max(
                        0, period_universe_us - covered_us
                    )
                    service_quality = QualityBreakdown(
                        neutral_us=s_res.quality.neutral_us,
                        confirmed_afk_us=s_res.quality.confirmed_afk_us,
                        unknown_afk_us=service_unknown_us,
                        noise_us=s_res.quality.noise_us,
                        project_contributing_us=(
                            s_res.quality.project_contributing_us
                        ),
                        is_reliable=(service_unknown_us == 0),
                        unreliable_reason=(
                            'unknown_afk_coverage'
                            if service_unknown_us > 0
                            else None
                        ),
                    )

                source_summaries.append(
                    SourceReportSummary(
                        alias=alias,
                        bucket_id=entry.bucket_id,
                        host_suffix=entry.host_suffix,
                        source_kind=entry.source_kind,
                        project_duration_us=p_us,
                        project_seconds=p_sec,
                        is_conflict=s_res.is_conflict,
                        conflict_reason=s_res.conflict_reason,
                        quality=service_quality,
                    )
                )

            # 8. Collect all worktree rows across sources
            all_wt_rows: list[ProjectWorktreeRow] = []
            for s_res in calc_result.source_results:
                all_wt_rows.extend(s_res.worktree_rows)

            daily_aggregate = aggregate_project_daily_rows(tuple(all_wt_rows))

            # 9. Compute data freshness (OUT-07)
            all_event_ends: list[datetime] = []
            for b_events in snapshot.events_by_bucket.values():
                for ev in b_events:
                    all_event_ends.append(
                        ev.timestamp + timedelta(microseconds=ev.duration_us)
                    )
            max_event_end = max(all_event_ends) if all_event_ends else None

            if max_event_end is None:
                is_stale = True
            else:
                age_seconds = (
                    snapshot.observed_until - max_event_end
                ).total_seconds()
                is_stale = (
                    age_seconds > self.settings.freshness_threshold_seconds
                )

            freshness = ReportFreshness(
                max_event_end=max_event_end,
                last_updated=snapshot.last_updated,
                observed_until=snapshot.observed_until,
                stale=is_stale,
            )

            # 10. Generate report identifier
            report_id = secrets.token_hex(16)

            report_result = ServiceReportResult(
                report_id=report_id,
                observed_until=snapshot.observed_until,
                read_started_at=snapshot.read_started_at,
                read_finished_at=snapshot.read_finished_at,
                period=calc_result.period,
                zone_name=zone_name,
                inventory=catalog.inventory,
                production_boundary=snapshot.production_boundary,
                boundary_status=snapshot.boundary_status,
                source_summaries=tuple(source_summaries),
                project_rows=tuple(all_wt_rows),
                daily_rows=daily_aggregate,
                counters=calc_result.counters,
                combined_allowed=calc_result.combined_allowed,
                combined_prohibition_reason=(
                    calc_result.combined_prohibition_reason
                ),
                freshness=freshness,
            )

            # 10. Update cached state ONLY after full success (OUT-05)
            self._cached_snapshot = snapshot
            self._cached_report_id = report_id
            self._cached_report_result = report_result
            self._cached_at = datetime.now(UTC)

            return report_result

        finally:
            with self._lock:
                self._is_calculating = False

    def calculate_comparison(self, report_id: str) -> ServiceComparisonResult:
        """Calculate standard window comparison using cached snapshot."""
        with self._lock:
            if self._is_calculating:
                raise ReportBusyError(CALCULATION_BUSY_REASON)
            self._is_calculating = True

        try:
            # 1. Validate report_id exists and matches cached snapshot
            if (
                self._cached_report_id is None
                or self._cached_report_id != report_id
                or self._cached_snapshot is None
                or self._cached_report_result is None
                or self._cached_at is None
            ):
                raise ReportServiceError(
                    reason_code='unknown_report_id',
                    message='Report ID not found or unknown',
                )

            # 2. Check snapshot TTL (OUT-05: 15 minutes)
            elapsed_seconds = (
                datetime.now(UTC) - self._cached_at
            ).total_seconds()
            if elapsed_seconds > self.settings.snapshot_ttl_seconds:
                raise ReportServiceError(
                    reason_code='expired_report_id',
                    message='Report snapshot has expired (15-minute TTL)',
                )

            snapshot = self._cached_snapshot
            report_res = self._cached_report_result
            p_start_us = datetime_to_us(report_res.period.start_utc)
            p_end_us = datetime_to_us(report_res.period.end_utc)

            standard_entries = snapshot.catalog.standard_entries
            if not standard_entries:
                return ServiceComparisonResult(
                    report_id=report_id,
                    observed_until=snapshot.observed_until,
                    read_started_at=datetime.now(UTC),
                    read_finished_at=datetime.now(UTC),
                    standard_duration_us=None,
                    standard_seconds=None,
                    host_rows=(),
                    has_cross_host_conflict=False,
                    conflict_reason=None,
                    is_available=False,
                    unavailable_reason='standard_source_absent',
                )

            read_started_at = datetime.now(UTC)
            host_rows: list[StandardComparisonHostRow] = []
            host_intervals: dict[str, list[tuple[int, int]]] = defaultdict(
                list
            )
            for entry in standard_entries:
                events = self._fetch_standard_events(
                    bucket_id=entry.bucket_id,
                    observed_until=snapshot.observed_until,
                )
                raw_intervals = [
                    (
                        datetime_to_us(ev.timestamp),
                        datetime_to_us(ev.timestamp) + ev.duration_us,
                    )
                    for ev in events
                ]
                clipped = intersect_intervals(
                    raw_intervals, ((p_start_us, p_end_us),)
                )
                host_duration_us = span_us(clipped)
                host_intervals[entry.host_suffix].extend(clipped)

                host_rows.append(
                    StandardComparisonHostRow(
                        host_suffix=entry.host_suffix,
                        bucket_id=entry.bucket_id,
                        duration_us=host_duration_us,
                        seconds=host_duration_us / 1_000_000,
                        event_count=len(events),
                    )
                )

            read_finished_at = datetime.now(UTC)

            # 3. Check cross-host standard overlap conflict (TIME-16)
            has_cross_host_conflict = False
            conflict_reason: str | None = None
            host_suffixes = list(host_intervals.keys())

            for i, h1 in enumerate(host_suffixes):
                ints1 = host_intervals[h1]
                for _j, h2 in enumerate(host_suffixes[i + 1 :], start=i + 1):
                    ints2 = host_intervals[h2]
                    if intervals_overlap_in_period(
                        ints1, ints2, p_start_us, p_end_us
                    ):
                        has_cross_host_conflict = True
                        conflict_reason = (
                            f'standard_cross_host_overlap: {h1} vs {h2}'
                        )
                        break
                if has_cross_host_conflict:
                    break

            if has_cross_host_conflict:
                total_duration_us: int | None = None
                total_seconds: float | None = None
            else:
                total_duration_us = sum(r.duration_us for r in host_rows)
                total_seconds = total_duration_us / 1_000_000

            return ServiceComparisonResult(
                report_id=report_id,
                observed_until=snapshot.observed_until,
                read_started_at=read_started_at,
                read_finished_at=read_finished_at,
                standard_duration_us=total_duration_us,
                standard_seconds=total_seconds,
                host_rows=tuple(host_rows),
                has_cross_host_conflict=has_cross_host_conflict,
                conflict_reason=conflict_reason,
                is_available=True,
                unavailable_reason=None,
            )

        finally:
            with self._lock:
                self._is_calculating = False

    def get_cached_report(self, report_id: str) -> ServiceReportResult | None:
        """Retrieve cached report result by report_id if matching."""
        if (
            self._cached_report_id == report_id
            and self._cached_report_result is not None
        ):
            return self._cached_report_result
        return None
