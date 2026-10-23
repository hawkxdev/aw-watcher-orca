"""Literal contract fixtures and expected values for reporting tests.

Per AC-02, this file carries literal expected values and must NOT import
any report_* modules. Reconciled against
project-statistics-contract-cases.json.
"""

from typing import Final

# === Pinned Case Expectations (Literal Constants) ===

EXPECTED_C01: Final[int] = 300_000_000
EXPECTED_C02: Final[int] = 300_000_000
EXPECTED_C03: Final[bool] = True
EXPECTED_C04: Final[int] = 420_000_000
EXPECTED_C06: Final[int] = 0
EXPECTED_C07: Final[bool] = True
EXPECTED_C08: Final[bool] = False
EXPECTED_C09: Final[bool] = False
EXPECTED_C11: Final[None] = None
EXPECTED_C12: Final[int] = 15_000_000
EXPECTED_C13: Final[bool] = True
EXPECTED_C14: Final[bool] = True
EXPECTED_C15: Final[int] = 1_200_000
EXPECTED_C16: Final[int] = 0
EXPECTED_C17: Final[dict[str, int]] = {
    '2026-03-29': 600_000,
    '2026-03-30': 600_000,
}
EXPECTED_C18_SPRING: Final[int] = 82_800_000_000
EXPECTED_C18_AUTUMN: Final[int] = 90_000_000_000
EXPECTED_C19: Final[int] = 600_000_000
EXPECTED_C20: Final[bool] = False
EXPECTED_C21: Final[bool] = False
EXPECTED_C23: Final[bool] = False
EXPECTED_C25: Final[int] = 600_000
EXPECTED_C27: Final[list[str]] = ['a+b']
EXPECTED_C28: Final[list[str]] = ['repo']
EXPECTED_CONTROL_COMPLETE: Final[bool] = True
EXPECTED_CONTROL_NO_OVERLAP: Final[bool] = False

# Mapping of all case IDs to expected values for automated reconciliation
PINNED_CASE_EXPECTATIONS: Final[dict[str, object]] = {
    'C01': EXPECTED_C01,
    'C02': EXPECTED_C02,
    'C03': EXPECTED_C03,
    'C04': EXPECTED_C04,
    'C06': EXPECTED_C06,
    'C07': EXPECTED_C07,
    'C08': EXPECTED_C08,
    'C09': EXPECTED_C09,
    'C11': EXPECTED_C11,
    'C12': EXPECTED_C12,
    'C13': EXPECTED_C13,
    'C14': EXPECTED_C14,
    'C15': EXPECTED_C15,
    'C16': EXPECTED_C16,
    'C17': EXPECTED_C17,
    'C18-spring': EXPECTED_C18_SPRING,
    'C18-autumn': EXPECTED_C18_AUTUMN,
    'C19': EXPECTED_C19,
    'C20': EXPECTED_C20,
    'C21': EXPECTED_C21,
    'C23': EXPECTED_C23,
    'C25': EXPECTED_C25,
    'C27': EXPECTED_C27,
    'C28': EXPECTED_C28,
    'control-complete': EXPECTED_CONTROL_COMPLETE,
    'control-no-overlap': EXPECTED_CONTROL_NO_OVERLAP,
}
