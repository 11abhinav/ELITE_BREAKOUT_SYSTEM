"""
app/earnings_forensic/__init__.py
Earnings Forensic & Hidden Facts Scanner package.

Deterministic calculation engine + AI enrichment layer that consumes
financial results fetched by the Filing Scanner and produces a one-page
investor-grade earnings quality report.

Key design constraints:
  - Purely downstream from financial_filing_watcher.py (never blocks it)
  - All deterministic checks run before any AI call
  - Every finding is traceable to a source (PIT_QUARTERLY, PIT_ANNUAL, RAW_FACTS_JSONB)
  - EVIDENCE_INSUFFICIENT is explicit — never substituted with 0 or synthetic values
  - Not a buy/sell engine — analysis and alerting only
"""

from .worker import run_forensic_worker_scan_once

__all__ = ["run_forensic_worker_scan_once"]
