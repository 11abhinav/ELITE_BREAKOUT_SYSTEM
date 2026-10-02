"""
app/financial_filing_watcher.py
===============================
Proxy module re-exporting the canonical Financial Filing Watcher and Freshness Engine
from scripts/financial_filing_watcher.py.
"""

from scripts.financial_filing_watcher import (
    FinancialFilingWatcher,
    FilingEventType,
    SnapshotFreshnessStatus,
    FilingEvent,
    _WATCHER_LOCK,
    STATE_FILE,
    EVENTS_LOG_FILE,
    SNAPSHOT_PATH,
    EXCHANGE_DIR,
)

__all__ = [
    "FinancialFilingWatcher",
    "FilingEventType",
    "SnapshotFreshnessStatus",
    "FilingEvent",
    "_WATCHER_LOCK",
    "STATE_FILE",
    "EVENTS_LOG_FILE",
    "SNAPSHOT_PATH",
    "EXCHANGE_DIR",
]
