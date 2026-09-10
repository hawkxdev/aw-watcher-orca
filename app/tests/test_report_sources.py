"""Test reporting source catalog classification, pairing, and validation."""

import pytest

from aw_watcher_orca.report_models import (
    ReportLimitError,
    ReportSourceKind,
)
from aw_watcher_orca.report_sources import (
    build_source_catalog,
    classify_bucket_id,
)


def test_classify_bucket_id_order_and_exact_prefixes() -> None:
    """Verify test prefix is checked before production prefix."""
    kind, suffix = classify_bucket_id('aw-watcher-orca-test_mac.local')
    assert kind == ReportSourceKind.TEST
    assert suffix == 'mac.local'

    kind, suffix = classify_bucket_id('aw-watcher-orca_mac.local')
    assert kind == ReportSourceKind.PRODUCTION
    assert suffix == 'mac.local'

    kind, suffix = classify_bucket_id('aw-watcher-window_mac.local')
    assert kind == ReportSourceKind.STANDARD
    assert suffix == 'mac.local'

    kind, suffix = classify_bucket_id('aw-watcher-afk_mac.local')
    assert kind == ReportSourceKind.AFK
    assert suffix == 'mac.local'

    # Unknown buckets
    kind, suffix = classify_bucket_id('aw-watcher-vscode_mac.local')
    assert kind is None
    assert suffix is None

    # Empty suffix
    kind, suffix = classify_bucket_id('aw-watcher-orca_')
    assert kind is None
    assert suffix is None


def test_build_source_catalog_happy_path() -> None:
    """Verify paired catalog with production, test, standard, and AFK."""
    buckets = {
        'aw-watcher-orca_host1': {
            'client': 'aw-watcher-orca',
            'type': 'currentwindow',
            'hostname': 'host1',
        },
        'aw-watcher-orca-test_host1': {
            'client': 'aw-watcher-orca-test',
            'type': 'currentwindow',
            'hostname': 'host1',
        },
        'aw-watcher-afk_host1': {
            'client': 'aw-watcher-afk',
            'type': 'afkstatus',
            'hostname': 'host1',
        },
        'aw-watcher-window_host1': {
            'client': 'aw-watcher-window',
            'type': 'currentwindow',
            'hostname': 'host1',
        },
        'unknown-bucket_host1': {
            'client': 'unknown',
            'type': 'unknown',
            'hostname': 'host1',
        },
    }
    catalog = build_source_catalog(buckets)
    assert catalog.has_incompleteness is False
    assert catalog.inventory.total_scanned == 5
    assert catalog.inventory.supported == 4
    assert catalog.inventory.paired == 2  # prod and test paired with AFK
    assert catalog.inventory.ignored == 1
    assert catalog.inventory.corrupted == 0

    prod_entry = catalog.get_entry('aw-watcher-orca_host1')
    assert prod_entry is not None
    assert prod_entry.is_paired is True
    assert prod_entry.afk_bucket_id == 'aw-watcher-afk_host1'
    assert prod_entry.is_corrupted is False

    test_entry = catalog.get_entry('aw-watcher-orca-test_host1')
    assert test_entry is not None
    assert test_entry.is_paired is True
    assert test_entry.afk_bucket_id == 'aw-watcher-afk_host1'


def test_build_source_catalog_missing_afk_creates_incompleteness() -> None:
    """Verify missing AFK bucket makes catalog incomplete."""
    buckets = {
        'aw-watcher-orca_host1': {
            'client': 'aw-watcher-orca',
            'type': 'currentwindow',
            'hostname': 'host1',
        },
    }
    catalog = build_source_catalog(buckets)
    assert catalog.has_incompleteness is True
    assert catalog.inventory.corrupted == 1
    prod_entry = catalog.get_entry('aw-watcher-orca_host1')
    assert prod_entry is not None
    assert prod_entry.is_paired is False
    assert prod_entry.is_corrupted is True
    assert 'missing_afk_pair' in (prod_entry.corruption_reason or '')


def test_build_source_catalog_corrupted_metadata_hostname_mismatch() -> None:
    """Verify hostname != suffix marks source corrupted (SRC-01)."""
    buckets = {
        'aw-watcher-orca_host1': {
            'client': 'aw-watcher-orca',
            'type': 'currentwindow',
            'hostname': 'other-host',  # mismatch with suffix host1
        },
        'aw-watcher-afk_host1': {
            'client': 'aw-watcher-afk',
            'type': 'afkstatus',
            'hostname': 'host1',
        },
    }
    catalog = build_source_catalog(buckets)
    assert catalog.has_incompleteness is True
    assert catalog.inventory.corrupted >= 1
    prod_entry = catalog.get_entry('aw-watcher-orca_host1')
    assert prod_entry is not None
    assert prod_entry.is_corrupted is True
    assert 'hostname_mismatch' in (prod_entry.corruption_reason or '')


def test_build_source_catalog_corrupted_metadata_client_or_type_mismatch() -> (
    None
):
    """Verify wrong client or type marks source corrupted."""
    buckets = {
        'aw-watcher-orca_host1': {
            'client': 'wrong-client',
            'type': 'currentwindow',
            'hostname': 'host1',
        },
        'aw-watcher-afk_host1': {
            'client': 'aw-watcher-afk',
            'type': 'wrong-type',
            'hostname': 'host1',
        },
    }
    catalog = build_source_catalog(buckets)
    assert catalog.has_incompleteness is True
    assert catalog.inventory.corrupted == 2


def test_build_source_catalog_missing_standard_leaves_specialized_valid() -> (
    None
):
    """Verify missing standard window leaves specialized valid (SRC-02)."""
    buckets = {
        'aw-watcher-orca_host1': {
            'client': 'aw-watcher-orca',
            'type': 'currentwindow',
            'hostname': 'host1',
        },
        'aw-watcher-afk_host1': {
            'client': 'aw-watcher-afk',
            'type': 'afkstatus',
            'hostname': 'host1',
        },
    }
    catalog = build_source_catalog(buckets)
    assert catalog.has_incompleteness is False
    assert len(catalog.specialized_entries) == 1
    assert len(catalog.standard_entries) == 0


def test_build_source_catalog_exceeds_inventory_buckets_limit() -> None:
    """Verify > 32 inventory buckets raises ReportLimitError (LIMIT-01)."""
    buckets = {
        f'unknown-bucket_{i}': {
            'client': 'c',
            'type': 't',
            'hostname': f'host{i}',
        }
        for i in range(33)
    }
    with pytest.raises(ReportLimitError) as exc_info:
        build_source_catalog(buckets)
    assert '32' in str(exc_info.value)


def test_build_source_catalog_exceeds_supported_hosts_limit() -> None:
    """Verify > 8 supported hosts raises ReportLimitError (LIMIT-01)."""
    buckets: dict[str, dict[str, object]] = {}
    for i in range(9):
        buckets[f'aw-watcher-orca_host{i}'] = {
            'client': 'aw-watcher-orca',
            'type': 'currentwindow',
            'hostname': f'host{i}',
        }
        buckets[f'aw-watcher-afk_host{i}'] = {
            'client': 'aw-watcher-afk',
            'type': 'afkstatus',
            'hostname': f'host{i}',
        }
    with pytest.raises(ReportLimitError) as exc_info:
        build_source_catalog(buckets)
    assert '8' in str(exc_info.value)
