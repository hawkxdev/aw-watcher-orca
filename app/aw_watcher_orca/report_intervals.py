"""Interval algebra, AFK intersection, noise filtering, and calendar."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from aw_watcher_orca.report_models import (
    DataQualityCounter,
    NoiseFilterResult,
    PeriodInterval,
    QualityBreakdown,
    RawEventRecord,
    ReportSourceKind,
    SourceCatalogEntry,
    SpecializedIdentity,
    TimeInterval,
    validate_afk_event_data,
    validate_specialized_event_data,
)
from aw_watcher_orca.report_reader import FullSnapshotResult
from aw_watcher_orca.report_settings import (
    DEFAULT_TIMEZONE,
    NOISE_THRESHOLD_MICROSECONDS,
)

# === Epoch and Time Conversion Helpers ===

EPOCH_UTC: Final[datetime] = datetime(1970, 1, 1, 0, 0, 0, tzinfo=UTC)


def datetime_to_us(dt_val: datetime) -> int:
    """Convert timezone-aware datetime to exact UTC microseconds."""
    utc_dt = dt_val.astimezone(UTC)
    delta = utc_dt - EPOCH_UTC
    return (
        delta.days * 86400 + delta.seconds
    ) * 1_000_000 + delta.microseconds


def us_to_datetime(us: int) -> datetime:
    """Convert integer microseconds from epoch to UTC datetime."""
    return EPOCH_UTC + timedelta(microseconds=us)


# === Pure Interval Arithmetic ===


def span_us(intervals: Iterable[tuple[int, int]]) -> int:
    """Measure non-overlapping duration of intervals in microseconds."""
    return sum(end - start for start, end in union_intervals(intervals))


def union_intervals(
    intervals: Iterable[tuple[int, int]],
) -> tuple[tuple[int, int], ...]:
    """Union and merge overlapping and adjacent half-open intervals."""
    valid_pieces: list[tuple[int, int]] = []
    for start, end in intervals:
        if end > start:
            valid_pieces.append((start, end))

    if not valid_pieces:
        return ()

    valid_pieces.sort(key=lambda item: item[0])
    merged: list[tuple[int, int]] = []
    cur_start, cur_end = valid_pieces[0]

    for next_start, next_end in valid_pieces[1:]:
        if next_start <= cur_end:
            cur_end = max(cur_end, next_end)
        else:
            merged.append((cur_start, cur_end))
            cur_start, cur_end = next_start, next_end

    merged.append((cur_start, cur_end))
    return tuple(merged)


def intersect_intervals(
    left: Iterable[tuple[int, int]],
    right: Iterable[tuple[int, int]],
) -> tuple[tuple[int, int], ...]:
    """Compute pairwise intersection of two interval sets."""
    left_union = union_intervals(left)
    right_union = union_intervals(right)

    intersections: list[tuple[int, int]] = []
    for l_start, l_end in left_union:
        for r_start, r_end in right_union:
            overlap_start = max(l_start, r_start)
            overlap_end = min(l_end, r_end)
            if overlap_start < overlap_end:
                intersections.append((overlap_start, overlap_end))

    return union_intervals(intersections)


def intervals_overlap(
    left: Iterable[tuple[int, int]],
    right: Iterable[tuple[int, int]],
) -> bool:
    """Report whether any non-empty intersection exists."""
    return len(intersect_intervals(left, right)) > 0


def intervals_overlap_in_period(
    left: Iterable[tuple[int, int]],
    right: Iterable[tuple[int, int]],
    period_start_us: int,
    period_end_us: int,
) -> bool:
    """Report whether non-empty intersection exists within period."""
    period_interval = ((period_start_us, period_end_us),)
    left_clipped = intersect_intervals(left, period_interval)
    right_clipped = intersect_intervals(right, period_interval)
    return intervals_overlap(left_clipped, right_clipped)


# === AFK Processing (TIME-04, TLR-06) ===


@dataclass(frozen=True, slots=True)
class AfkIntervalResult:
    """Hold processed AFK intervals and contradiction status."""

    not_afk_intervals: tuple[tuple[int, int], ...]
    afk_intervals: tuple[tuple[int, int], ...]
    has_conflict: bool
    conflict_reason: str | None = None


def compute_afk_intervals(
    afk_events: Iterable[RawEventRecord],
    period_bounds: tuple[int, int] | None = None,
) -> AfkIntervalResult:
    """Merge AFK events and check contradictory overlap within period."""
    not_afk_raw: list[tuple[int, int]] = []
    afk_raw: list[tuple[int, int]] = []

    for event in afk_events:
        status = validate_afk_event_data(event.data)
        start_us = datetime_to_us(event.timestamp)
        end_us = start_us + event.duration_us
        if end_us <= start_us:
            continue
        if status == 'not-afk':
            not_afk_raw.append((start_us, end_us))
        else:
            afk_raw.append((start_us, end_us))

    not_afk_union = union_intervals(not_afk_raw)
    afk_union = union_intervals(afk_raw)

    has_conflict = False
    conflict_reason: str | None = None

    if period_bounds is not None:
        p_start, p_end = period_bounds
        if intervals_overlap_in_period(
            not_afk_union, afk_union, p_start, p_end
        ):
            has_conflict = True
            conflict_reason = 'contradictory_afk_and_not_afk_in_period'
    elif intervals_overlap(not_afk_union, afk_union):
        has_conflict = True
        conflict_reason = 'contradictory_afk_and_not_afk'

    return AfkIntervalResult(
        not_afk_intervals=not_afk_union,
        afk_intervals=afk_union,
        has_conflict=has_conflict,
        conflict_reason=conflict_reason,
    )


# === Noise Threshold and Clipping (TIME-09, TIME-10, TLR-09) ===


def apply_noise_threshold_and_clip(
    groups: Iterable[Iterable[tuple[int, int]]],
    threshold_us: int = NOISE_THRESHOLD_MICROSECONDS,
    period_bounds: tuple[int, int] | None = None,
) -> NoiseFilterResult:
    """Apply noise threshold to connected segments, then clip to period.

    Each group in `groups` represents connected intervals of ONE identity.
    """
    kept: list[TimeInterval] = []
    noise: list[TimeInterval] = []

    period_interval = (
        ((period_bounds[0], period_bounds[1]),)
        if period_bounds is not None
        else None
    )
    for group in groups:
        merged_group = union_intervals(group)
        for start, end in merged_group:
            segment_duration = end - start
            if segment_duration >= threshold_us:
                if period_interval is not None:
                    clipped = intersect_intervals(
                        ((start, end),), period_interval
                    )
                    for c_start, c_end in clipped:
                        kept.append(
                            TimeInterval(start_us=c_start, end_us=c_end)
                        )
                else:
                    kept.append(TimeInterval(start_us=start, end_us=end))
            else:
                noise.append(TimeInterval(start_us=start, end_us=end))

    kept_us = sum(i.duration_us for i in kept)
    noise_us = sum(i.duration_us for i in noise)

    return NoiseFilterResult(
        kept_intervals=tuple(kept),
        noise_intervals=tuple(noise),
        kept_duration_us=kept_us,
        noise_duration_us=noise_us,
    )


# === Calendar Midnights and Day Splitting (TIME-11, TLR-10) ===


def resolve_calendar_midnight_utc(d: date, zone_name: str) -> int:
    """Resolve local calendar midnight in UTC integer microseconds."""
    tz = ZoneInfo(zone_name)
    dt_local = datetime.combine(d, time.min, tzinfo=tz)
    return datetime_to_us(dt_local)


def calendar_day_length_us(d: date, zone_name: str) -> int:
    """Calculate exact duration of local calendar day in microseconds."""
    m_start = resolve_calendar_midnight_utc(d, zone_name)
    m_end = resolve_calendar_midnight_utc(d + timedelta(days=1), zone_name)
    return m_end - m_start


def compute_period_bounds_us(
    start_date: date,
    end_date: date,
    zone_name: str,
    observed_until: datetime | None = None,
) -> tuple[int, int]:
    """Compute UTC start and end bounds in microseconds for date range."""
    p_start_us = resolve_calendar_midnight_utc(start_date, zone_name)
    p_end_us = resolve_calendar_midnight_utc(
        end_date + timedelta(days=1), zone_name
    )
    if observed_until is not None:
        obs_us = datetime_to_us(observed_until)
        p_end_us = min(p_end_us, obs_us)
    return (p_start_us, p_end_us)


def split_interval_by_local_days(
    start_us: int,
    end_us: int,
    zone_name: str,
) -> dict[str, int]:
    """Split half-open interval across local calendar days."""
    if end_us <= start_us:
        return {}

    tz = ZoneInfo(zone_name)
    start_dt_local = us_to_datetime(start_us).astimezone(tz)
    end_dt_local = us_to_datetime(end_us).astimezone(tz)

    cur_date = start_dt_local.date()
    end_date = end_dt_local.date()
    splits: dict[str, int] = {}

    while cur_date <= end_date:
        m_start = resolve_calendar_midnight_utc(cur_date, zone_name)
        m_end = resolve_calendar_midnight_utc(
            cur_date + timedelta(days=1), zone_name
        )
        seg_start = max(start_us, m_start)
        seg_end = min(end_us, m_end)
        if seg_start < seg_end:
            day_str = cur_date.isoformat()
            splits[day_str] = seg_end - seg_start
        cur_date += timedelta(days=1)

    return splits


# === Strict Test vs Production Transition (TIME-01..03, TLR-04) ===


def validate_test_boundary_transition(
    test_intervals: Iterable[tuple[int, int]],
    boundary_us: int,
) -> bool:
    """Validate strict test boundary rule across all test intervals."""
    return all(end < boundary_us for _, end in test_intervals)


# === Project Filtering (UI-04, TLR-07) ===


def filter_matching_project_events(
    values: Iterable[str],
    pattern: str,
    exact: bool = False,
) -> list[str]:
    """Filter project strings case-insensitively using literal matching."""
    pat = pattern.casefold()
    matched: list[str] = []
    for val in values:
        v_fold = val.casefold()
        if exact:
            if v_fold == pat:
                matched.append(val)
        else:
            if pat in v_fold:
                matched.append(val)
    return matched


# === Statistics Results Dataclasses ===


@dataclass(frozen=True, slots=True)
class ProjectDayRow:
    """Hold daily project statistics aggregate."""

    day: str
    duration_us: int
    seconds: float
    contributing_event_keys: frozenset[tuple[str, int]]
    derived_segments_count: int


@dataclass(frozen=True, slots=True)
class ProjectWorktreeRow:
    """Hold worktree statistics and child daily rows."""

    repo: str
    worktree: str
    duration_us: int
    seconds: float
    daily_rows: tuple[ProjectDayRow, ...]


@dataclass(frozen=True, slots=True)
class SourceCalculationResult:
    """Hold calculation results and quality metrics for one source."""

    source_entry: SourceCatalogEntry
    is_conflict: bool
    conflict_reason: str | None
    project_duration_us: int | None
    quality: QualityBreakdown
    worktree_rows: tuple[ProjectWorktreeRow, ...]
    raw_contributing_keys: frozenset[tuple[str, int]]


@dataclass(frozen=True, slots=True)
class CalculationResult:
    """Hold complete calculation output and overall combined totals."""

    snapshot: FullSnapshotResult
    period: PeriodInterval
    combined_allowed: bool
    combined_prohibition_reason: str | None
    source_results: tuple[SourceCalculationResult, ...]
    total_contributing_keys: frozenset[tuple[str, int]]
    total_derived_segments: int
    counters: DataQualityCounter


# === Contributing Events Summary Helper ===


def compute_contributing_events_summary(
    day_rows: Iterable[ProjectDayRow],
) -> tuple[int, int]:
    """Compute (unique_period_contributing_events, total_derived_segments)."""
    unique_keys: set[tuple[str, int]] = set()
    total_segments = 0
    for row in day_rows:
        unique_keys.update(row.contributing_event_keys)
        total_segments += row.derived_segments_count
    return (len(unique_keys), total_segments)


# === Full Core Calculation Pipeline (TIME-01..16, OUT-01..08) ===


def compute_project_statistics_intervals(
    snapshot: FullSnapshotResult,
    start_date: date,
    end_date: date,
    zone_name: str = DEFAULT_TIMEZONE,
    project_filter: str | None = None,
    exact_project_match: bool = False,
    noise_threshold_us: int = NOISE_THRESHOLD_MICROSECONDS,
) -> CalculationResult:
    """Execute complete interval algebra pipeline over a verified snapshot."""
    # 1. Period bounds in UTC microseconds
    p_start_us, p_end_us = compute_period_bounds_us(
        start_date=start_date,
        end_date=end_date,
        zone_name=zone_name,
        observed_until=snapshot.observed_until,
    )
    period_obj = PeriodInterval(
        start_utc=us_to_datetime(p_start_us),
        end_utc=us_to_datetime(p_end_us),
        zone_name=zone_name,
    )

    # 2. Check strict test boundary transition across ALL test events
    combined_allowed = True
    combined_prohibition_reason: str | None = None

    if snapshot.production_boundary is not None:
        boundary_us = datetime_to_us(snapshot.production_boundary)
        all_test_intervals: list[tuple[int, int]] = []
        for entry in snapshot.catalog.specialized_entries:
            if entry.source_kind == ReportSourceKind.TEST:
                for ev in snapshot.events_by_bucket.get(entry.bucket_id, ()):
                    s_us = datetime_to_us(ev.timestamp)
                    e_us = s_us + ev.duration_us
                    all_test_intervals.append((s_us, e_us))

        if not validate_test_boundary_transition(
            all_test_intervals, boundary_us
        ):
            combined_allowed = False
            combined_prohibition_reason = 'test_after_production_boundary'
    elif snapshot.boundary_status == 'unknown_boundary':
        combined_allowed = False
        combined_prohibition_reason = 'unknown_production_boundary'
    elif snapshot.boundary_status == 'absent_unverified':
        # Finding 4: absent_unverified allows test-only total
        combined_allowed = True

    # Finding 2: Incomplete catalog blocks combined total
    if snapshot.catalog.has_incompleteness:
        combined_allowed = False
        combined_prohibition_reason = (
            combined_prohibition_reason or 'catalog_incomplete'
        )

    # 3. Process each specialized source
    source_results: list[SourceCalculationResult] = []
    host_active_intervals: dict[str, list[tuple[int, int]]] = defaultdict(list)
    all_period_contributing_keys: set[tuple[str, int]] = set()
    all_period_derived_segments = 0

    for entry in snapshot.catalog.specialized_entries:
        raw_events = snapshot.events_by_bucket.get(entry.bucket_id, ())
        afk_events = (
            snapshot.events_by_bucket.get(entry.afk_bucket_id, ())
            if entry.afk_bucket_id
            else ()
        )

        afk_res = compute_afk_intervals(
            afk_events, period_bounds=(p_start_us, p_end_us)
        )
        if afk_res.has_conflict:
            q_breakdown = QualityBreakdown(
                neutral_us=0,
                confirmed_afk_us=span_us(afk_res.afk_intervals),
                unknown_afk_us=0,
                noise_us=0,
                project_contributing_us=0,
                is_reliable=False,
                unreliable_reason=afk_res.conflict_reason,
            )
            res = SourceCalculationResult(
                source_entry=entry,
                is_conflict=True,
                conflict_reason=afk_res.conflict_reason,
                project_duration_us=None,
                quality=q_breakdown,
                worktree_rows=(),
                raw_contributing_keys=frozenset(),
            )
            source_results.append(res)
            continue

        neutral_intervals: list[tuple[int, int]] = []
        active_by_identity: dict[
            SpecializedIdentity, list[tuple[RawEventRecord, int, int]]
        ] = defaultdict(list)

        for ev in raw_events:
            is_active, repo, worktree, title, schema = (
                validate_specialized_event_data(ev.data)
            )
            s_us = datetime_to_us(ev.timestamp)
            e_us = s_us + ev.duration_us
            if is_active:
                if project_filter:
                    fields = [repo, worktree, title]
                    if not filter_matching_project_events(
                        fields, project_filter, exact_project_match
                    ):
                        continue
                ident = SpecializedIdentity.create(
                    host_suffix=entry.host_suffix,
                    bucket_id=entry.bucket_id,
                    source_kind=entry.source_kind,
                    repo=repo,
                    worktree=worktree,
                )
                active_by_identity[ident].append((ev, s_us, e_us))
            else:
                neutral_intervals.append((s_us, e_us))

        # Check internal conflict within this source
        source_has_internal_conflict = False
        conflict_reason = None

        all_identities = list(active_by_identity.keys())
        for i, id1 in enumerate(all_identities):
            ints1 = [(s, e) for _, s, e in active_by_identity[id1]]
            if intervals_overlap_in_period(
                ints1, neutral_intervals, p_start_us, p_end_us
            ):
                source_has_internal_conflict = True
                conflict_reason = 'active_and_neutral_overlap_in_period'
                break
            for id2 in all_identities[i + 1 :]:
                ints2 = [(s, e) for _, s, e in active_by_identity[id2]]
                if intervals_overlap_in_period(
                    ints1, ints2, p_start_us, p_end_us
                ):
                    source_has_internal_conflict = True
                    conflict_reason = (
                        'multiple_distinct_identities_overlap_in_period'
                    )
                    break
            if source_has_internal_conflict:
                break

        if source_has_internal_conflict:
            q_breakdown = QualityBreakdown(
                neutral_us=span_us(neutral_intervals),
                confirmed_afk_us=span_us(afk_res.afk_intervals),
                unknown_afk_us=0,
                noise_us=0,
                project_contributing_us=0,
                is_reliable=False,
                unreliable_reason=conflict_reason,
            )
            res = SourceCalculationResult(
                source_entry=entry,
                is_conflict=True,
                conflict_reason=conflict_reason,
                project_duration_us=None,
                quality=q_breakdown,
                worktree_rows=(),
                raw_contributing_keys=frozenset(),
            )
            source_results.append(res)
            continue

        worktree_rows_list: list[ProjectWorktreeRow] = []
        source_contributing_keys: set[tuple[str, int]] = set()
        source_total_kept_us = 0
        source_total_noise_us = 0

        # Finding 6: Calculate unknown_afk_us for active events
        all_active_raw = [
            (s, e) for evs in active_by_identity.values() for _, s, e in evs
        ]
        active_in_period = intersect_intervals(
            all_active_raw, ((p_start_us, p_end_us),)
        )
        all_afk_coverage = union_intervals(
            list(afk_res.not_afk_intervals) + list(afk_res.afk_intervals)
        )
        active_covered = intersect_intervals(
            active_in_period, all_afk_coverage
        )
        unknown_afk_us = max(
            0, span_us(active_in_period) - span_us(active_covered)
        )

        for ident, ev_records in active_by_identity.items():
            raw_pairs = [(s, e) for _, s, e in ev_records]
            afk_intersected = intersect_intervals(
                raw_pairs, afk_res.not_afk_intervals
            )

            noise_res = apply_noise_threshold_and_clip(
                (afk_intersected,),
                threshold_us=noise_threshold_us,
                period_bounds=(p_start_us, p_end_us),
            )
            source_total_noise_us += noise_res.noise_duration_us

            if not noise_res.kept_intervals:
                continue

            daily_us: dict[str, int] = defaultdict(int)
            daily_keys: dict[str, set[tuple[str, int]]] = defaultdict(set)
            daily_seg_counts: dict[str, int] = defaultdict(int)

            for seg in noise_res.kept_intervals:
                host_active_intervals[entry.host_suffix].append(
                    (seg.start_us, seg.end_us)
                )
                source_total_kept_us += seg.duration_us
                day_splits = split_interval_by_local_days(
                    seg.start_us, seg.end_us, zone_name
                )
                for day_str, d_us in day_splits.items():
                    daily_us[day_str] += d_us
                    daily_seg_counts[day_str] += 1
                    # Finding 14: check overlap with exact day bounds
                    d_date = date.fromisoformat(day_str)
                    m_start = resolve_calendar_midnight_utc(d_date, zone_name)
                    m_end = resolve_calendar_midnight_utc(
                        d_date + timedelta(days=1), zone_name
                    )
                    seg_day_start = max(seg.start_us, m_start)
                    seg_day_end = min(seg.end_us, m_end)
                    for raw_ev, s_ev, e_ev in ev_records:
                        if max(seg_day_start, s_ev) < min(seg_day_end, e_ev):
                            daily_keys[day_str].add(
                                (entry.bucket_id, raw_ev.event_id)
                            )
                            source_contributing_keys.add(
                                (entry.bucket_id, raw_ev.event_id)
                            )

            day_rows: list[ProjectDayRow] = []
            for d_str in sorted(daily_us.keys()):
                d_us = daily_us[d_str]
                day_rows.append(
                    ProjectDayRow(
                        day=d_str,
                        duration_us=d_us,
                        seconds=d_us / 1_000_000,
                        contributing_event_keys=frozenset(daily_keys[d_str]),
                        derived_segments_count=daily_seg_counts[d_str],
                    )
                )

            wt_duration = sum(d.duration_us for d in day_rows)
            worktree_rows_list.append(
                ProjectWorktreeRow(
                    repo=ident.repo,
                    worktree=ident.worktree,
                    duration_us=wt_duration,
                    seconds=wt_duration / 1_000_000,
                    daily_rows=tuple(day_rows),
                )
            )

        q_breakdown = QualityBreakdown(
            neutral_us=span_us(neutral_intervals),
            confirmed_afk_us=span_us(afk_res.afk_intervals),
            unknown_afk_us=unknown_afk_us,
            noise_us=source_total_noise_us,
            project_contributing_us=source_total_kept_us,
            is_reliable=(unknown_afk_us == 0),
            unreliable_reason=(
                'unknown_afk_coverage' if unknown_afk_us > 0 else None
            ),
        )

        all_period_contributing_keys.update(source_contributing_keys)
        all_period_derived_segments += sum(
            sum(d.derived_segments_count for d in wt.daily_rows)
            for wt in worktree_rows_list
        )

        source_results.append(
            SourceCalculationResult(
                source_entry=entry,
                is_conflict=False,
                conflict_reason=None,
                project_duration_us=source_total_kept_us,
                quality=q_breakdown,
                worktree_rows=tuple(worktree_rows_list),
                raw_contributing_keys=frozenset(source_contributing_keys),
            )
        )

    # If any source result has null sum, combined total is prohibited
    if any(s.project_duration_us is None for s in source_results):
        combined_allowed = False
        combined_prohibition_reason = (
            combined_prohibition_reason or 'incomplete_source_sum'
        )

    # 4. Check cross-host conflict across different host suffixes
    host_suffixes = list(host_active_intervals.keys())
    for i, h1 in enumerate(host_suffixes):
        ints1 = host_active_intervals[h1]
        for j, h2 in enumerate(host_suffixes[i + 1 :], start=i + 1):
            ints2 = host_active_intervals[h2]
            if intervals_overlap_in_period(ints1, ints2, p_start_us, p_end_us):
                combined_allowed = False
                combined_prohibition_reason = (
                    f'cross_host_active_overlap: host_{i} vs host_{j}'
                )
                break
        if not combined_allowed and combined_prohibition_reason:
            break

    # Compute overall DataQualityCounter (Finding 11)
    all_raw_keys = {
        (b, ev.event_id)
        for b, evs in snapshot.events_by_bucket.items()
        for ev in evs
    }
    period_events_keys = {
        (b, ev.event_id)
        for b, evs in snapshot.events_by_bucket.items()
        for ev in evs
        if (
            max(datetime_to_us(ev.timestamp), p_start_us)
            < min(datetime_to_us(ev.timestamp) + ev.duration_us, p_end_us)
            or (
                ev.duration_us == 0
                and p_start_us <= datetime_to_us(ev.timestamp) < p_end_us
            )
        )
    }

    matching_events_keys: set[tuple[str, int]] = set()
    for b, evs in snapshot.events_by_bucket.items():
        for ev in evs:
            if (b, ev.event_id) not in period_events_keys:
                continue
            if project_filter:
                repo = str(ev.data.get('repo', ''))
                worktree = str(ev.data.get('worktree', ''))
                title = str(ev.data.get('title', ''))
                if filter_matching_project_events(
                    [repo, worktree, title],
                    project_filter,
                    exact_project_match,
                ):
                    matching_events_keys.add((b, ev.event_id))
            else:
                matching_events_keys.add((b, ev.event_id))

    counters = DataQualityCounter(
        raw_events=len(all_raw_keys),
        period_events=len(period_events_keys),
        matching_events=len(matching_events_keys),
        contributing_events=len(all_period_contributing_keys),
        derived_segments=all_period_derived_segments,
    )

    return CalculationResult(
        snapshot=snapshot,
        period=period_obj,
        combined_allowed=combined_allowed,
        combined_prohibition_reason=combined_prohibition_reason,
        source_results=tuple(source_results),
        total_contributing_keys=frozenset(all_period_contributing_keys),
        total_derived_segments=all_period_derived_segments,
        counters=counters,
    )
