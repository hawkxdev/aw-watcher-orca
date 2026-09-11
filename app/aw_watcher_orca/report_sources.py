"""Classify and pair ActivityWatch historical reporting sources."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from aw_watcher_orca.bucket_target import (
    PRODUCTION_BUCKET_TARGET,
    TEST_BUCKET_TARGET,
)
from aw_watcher_orca.report_models import (
    InventorySummary,
    ReportMalformedPayloadError,
    ReportSourceKind,
    SourceCatalogEntry,
    parse_event_timestamp,
)
from aw_watcher_orca.report_settings import (
    MAX_INVENTORY_BUCKETS,
    MAX_SUPPORTED_HOSTS,
    validate_inventory_buckets_limit,
    validate_supported_hosts_limit,
)

# === Constants and Prefixes ===

TEST_BUCKET_PREFIX: Final[str] = f'{TEST_BUCKET_TARGET.prefix}_'
TEST_BUCKET_CLIENT: Final[str] = TEST_BUCKET_TARGET.client
TEST_BUCKET_TYPE: Final[str] = TEST_BUCKET_TARGET.bucket_type

PRODUCTION_BUCKET_PREFIX: Final[str] = f'{PRODUCTION_BUCKET_TARGET.prefix}_'
PRODUCTION_BUCKET_CLIENT: Final[str] = PRODUCTION_BUCKET_TARGET.client
PRODUCTION_BUCKET_TYPE: Final[str] = PRODUCTION_BUCKET_TARGET.bucket_type

STANDARD_BUCKET_PREFIX: Final[str] = 'aw-watcher-window_'
STANDARD_BUCKET_CLIENT: Final[str] = 'aw-watcher-window'
STANDARD_BUCKET_TYPE: Final[str] = 'currentwindow'

AFK_BUCKET_PREFIX: Final[str] = 'aw-watcher-afk_'
AFK_BUCKET_CLIENT: Final[str] = 'aw-watcher-afk'
AFK_BUCKET_TYPE: Final[str] = 'afkstatus'


# === Catalog Data Structure ===


@dataclass(frozen=True, slots=True)
class SourceCatalog:
    """Hold all classified historical sources and inventory metrics."""

    entries: tuple[SourceCatalogEntry, ...]
    inventory: InventorySummary
    specialized_entries: tuple[SourceCatalogEntry, ...]
    standard_entries: tuple[SourceCatalogEntry, ...]
    afk_entries: tuple[SourceCatalogEntry, ...]
    has_incompleteness: bool
    incompleteness_reasons: tuple[str, ...]

    def get_entry(self, bucket_id: str) -> SourceCatalogEntry | None:
        """Find one catalog entry by bucket identifier."""
        for entry in self.entries:
            if entry.bucket_id == bucket_id:
                return entry
        return None

    def get_afk_bucket_for_host(self, host_suffix: str) -> str | None:
        """Find the AFK bucket ID paired with one host suffix."""
        for entry in self.afk_entries:
            if (
                entry.host_suffix == host_suffix
                and not entry.is_corrupted
                and entry.is_paired
            ):
                return entry.bucket_id
        return None


# === Bucket Classification and Catalog Building ===


def classify_bucket_id(
    bucket_id: str,
) -> tuple[ReportSourceKind | None, str | None]:
    """Classify bucket identifier into (kind, host_suffix).

    Test prefix is strictly checked before production prefix because
    'aw-watcher-orca-test_' starts with 'aw-watcher-orca' prefix family.
    """
    if bucket_id.startswith(TEST_BUCKET_PREFIX):
        suffix = bucket_id[len(TEST_BUCKET_PREFIX) :]
        if suffix:
            return (ReportSourceKind.TEST, suffix)
    elif bucket_id.startswith(PRODUCTION_BUCKET_PREFIX):
        suffix = bucket_id[len(PRODUCTION_BUCKET_PREFIX) :]
        if suffix:
            return (ReportSourceKind.PRODUCTION, suffix)
    elif bucket_id.startswith(STANDARD_BUCKET_PREFIX):
        suffix = bucket_id[len(STANDARD_BUCKET_PREFIX) :]
        if suffix:
            return (ReportSourceKind.STANDARD, suffix)
    elif bucket_id.startswith(AFK_BUCKET_PREFIX):
        suffix = bucket_id[len(AFK_BUCKET_PREFIX) :]
        if suffix:
            return (ReportSourceKind.AFK, suffix)

    return (None, None)


def build_source_catalog(
    buckets: Mapping[str, object],
    max_inventory_buckets: int = MAX_INVENTORY_BUCKETS,
    max_supported_hosts: int = MAX_SUPPORTED_HOSTS,
) -> SourceCatalog:
    """Classify and pair ActivityWatch buckets into an immutable catalog."""
    total_scanned = len(buckets)
    validate_inventory_buckets_limit(total_scanned, max_inventory_buckets)

    raw_supported: list[
        tuple[str, ReportSourceKind, str, Mapping[str, object]]
    ] = []
    ignored_count = 0
    supported_hosts: set[str] = set()

    # 1. Classify raw buckets
    for bucket_id, raw_metadata in buckets.items():
        kind, host_suffix = classify_bucket_id(bucket_id)
        if kind is None or host_suffix is None:
            ignored_count += 1
            continue
        if not isinstance(raw_metadata, Mapping):
            metadata_map: Mapping[str, object] = {}
        else:
            metadata_map = raw_metadata

        raw_supported.append((bucket_id, kind, host_suffix, metadata_map))
        supported_hosts.add(host_suffix)

    validate_supported_hosts_limit(len(supported_hosts), max_supported_hosts)

    # 2. Inspect metadata and classify valid AFK buckets
    valid_afk_by_host: dict[str, str] = {}
    afk_corruption_by_host: dict[str, str] = {}

    preliminary_entries: list[
        tuple[
            str,
            ReportSourceKind,
            str,
            str,
            str,
            str,
            bool,
            str | None,
            datetime | None,
        ]
    ] = []

    for bucket_id, kind, host_suffix, metadata in raw_supported:
        client = str(metadata.get('client', ''))
        bucket_type = str(metadata.get('type', ''))
        hostname = str(metadata.get('hostname', ''))

        expected_client: str
        expected_type: str

        if kind == ReportSourceKind.TEST:
            expected_client = TEST_BUCKET_CLIENT
            expected_type = TEST_BUCKET_TYPE
        elif kind == ReportSourceKind.PRODUCTION:
            expected_client = PRODUCTION_BUCKET_CLIENT
            expected_type = PRODUCTION_BUCKET_TYPE
        elif kind == ReportSourceKind.STANDARD:
            expected_client = STANDARD_BUCKET_CLIENT
            expected_type = STANDARD_BUCKET_TYPE
        else:
            expected_client = AFK_BUCKET_CLIENT
            expected_type = AFK_BUCKET_TYPE

        is_corrupted = False
        corruption_reason: str | None = None

        if hostname != host_suffix:
            is_corrupted = True
            corruption_reason = 'hostname_mismatch'
        elif client != expected_client:
            is_corrupted = True
            corruption_reason = 'client_mismatch'
        elif bucket_type != expected_type:
            is_corrupted = True
            corruption_reason = 'type_mismatch'

        raw_last_updated = metadata.get('last_updated')
        last_updated: datetime | None = None
        if raw_last_updated is not None:
            try:
                last_updated = parse_event_timestamp(raw_last_updated)
            except ReportMalformedPayloadError:
                last_updated = None

        if kind == ReportSourceKind.AFK:
            if not is_corrupted:
                valid_afk_by_host[host_suffix] = bucket_id
            elif corruption_reason:
                afk_corruption_by_host[host_suffix] = corruption_reason

        preliminary_entries.append(
            (
                bucket_id,
                kind,
                host_suffix,
                client,
                bucket_type,
                hostname,
                is_corrupted,
                corruption_reason,
                last_updated,
            )
        )

    # 3. Complete pairing for specialized and standard entries
    final_entries: list[SourceCatalogEntry] = []
    has_incompleteness = False
    incompleteness_reasons: list[str] = []
    paired_count = 0
    corrupted_count = 0

    for idx, (
        bucket_id,
        kind,
        host_suffix,
        client,
        bucket_type,
        hostname,
        is_corrupted,
        corruption_reason,
        last_updated,
    ) in enumerate(preliminary_entries):
        afk_bucket_id: str | None = None
        is_paired = False

        if kind in (ReportSourceKind.TEST, ReportSourceKind.PRODUCTION):
            if host_suffix in valid_afk_by_host:
                afk_bucket_id = valid_afk_by_host[host_suffix]
                is_paired = True
                paired_count += 1
            else:
                is_corrupted = True
                afk_reason = afk_corruption_by_host.get(
                    host_suffix, 'missing_afk_pair'
                )
                corruption_reason = (
                    f'{corruption_reason}; {afk_reason}'
                    if corruption_reason
                    else afk_reason
                )

        elif kind == ReportSourceKind.AFK:
            if not is_corrupted:
                is_paired = True
                afk_bucket_id = bucket_id

        elif (
            kind == ReportSourceKind.STANDARD
            and host_suffix in valid_afk_by_host
        ):
            afk_bucket_id = valid_afk_by_host[host_suffix]
            is_paired = True

        if is_corrupted:
            corrupted_count += 1
            has_incompleteness = True
            incompleteness_reasons.append(
                f'source_{idx}: {corruption_reason or "corrupted_metadata"}'
            )
        entry = SourceCatalogEntry(
            host_suffix=host_suffix,
            bucket_id=bucket_id,
            source_kind=str(kind),
            client=client,
            bucket_type=bucket_type,
            hostname=hostname,
            afk_bucket_id=afk_bucket_id,
            is_paired=is_paired,
            is_corrupted=is_corrupted,
            corruption_reason=corruption_reason,
            last_updated=last_updated,
        )
        final_entries.append(entry)

    inventory = InventorySummary(
        total_scanned=total_scanned,
        supported=len(raw_supported),
        paired=paired_count,
        ignored=ignored_count,
        corrupted=corrupted_count,
    )

    specialized_kinds = (
        ReportSourceKind.TEST,
        ReportSourceKind.PRODUCTION,
    )
    specialized_entries = tuple(
        e
        for e in final_entries
        if e.source_kind in specialized_kinds
        and not e.is_corrupted
        and e.is_paired
    )
    standard_entries = tuple(
        e
        for e in final_entries
        if e.source_kind == ReportSourceKind.STANDARD and not e.is_corrupted
    )
    afk_entries = tuple(
        e
        for e in final_entries
        if e.source_kind == ReportSourceKind.AFK and not e.is_corrupted
    )

    return SourceCatalog(
        entries=tuple(final_entries),
        inventory=inventory,
        specialized_entries=specialized_entries,
        standard_entries=standard_entries,
        afk_entries=afk_entries,
        has_incompleteness=has_incompleteness,
        incompleteness_reasons=tuple(incompleteness_reasons),
    )
