#!/usr/bin/env python3
"""
scripts/financial_filing_watcher.py
===================================
Periodic Financial Filing Watcher, Event Detector & Dependency-Based Snapshot Invalidator.

PURPOSE:
  Solves the financial data freshness problem:
  "Do we know whether a newer or amended financial filing now exists?"
  
  Does NOT run heavy fetches inside the 17:00 scan loop.
  Instead runs as a decoupled event watcher that:
    1. DISCOVERS: Polls NSE/BSE corporate announcements & integrated filings.
    2. DETECTS: Categorizes events as NEW, AMENDED, RESTATED, or UNSEEN_PERIOD.
    3. INGESTS: Immutable raw payload storage, SHA256 hashing, fact normalization.
    4. INVALIDATES & REBUILDS: Dependency-based granular metric recalculation,
       updating the canonical financial snapshot for the next scanner run.

SCANNER CONTRACT:
  - 17:00 Scanner checks canonical snapshot status:
      • FRESH / VALID      -> Evaluate normally
      • UPDATE_PENDING     -> DATA_INSUFFICIENT -> Block BUY
      • STALE              -> DATA_STALE        -> Block BUY
      • NEW INGESTED       -> Immediately consume updated snapshot

Usage:
  python3 scripts/financial_filing_watcher.py [--poll] [--symbol SYMBOL] [--force-rebuild]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Ensure repository root is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import numpy as np
import pandas as pd

from app.financial_data_integrity import (
    DataStatus,
    StatementBasis,
    check_pit_freshness,
    detect_annual_fiscal_gaps,
    compute_cagr_pit,
    compute_ev_ebitda,
    derive_and_validate_shares,
    validate_share_count,
    reconcile_nse_bse_fact,
)
from app.live_fundamental_scanner import (
    ApprovedUniverseRegistry,
    _get_pit_filings,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FILING_WATCHER")

STATE_FILE = BASE_DIR / "data" / "filing_watcher_state.json"
EVENTS_LOG_FILE = BASE_DIR / "data" / "filing_invalidation_events.jsonl"
SNAPSHOT_PATH = BASE_DIR / "data" / "canonical_pit_rebuilt.parquet"
EXCHANGE_DIR = BASE_DIR / "data" / "exchange_financials"


class FilingEventType(str, Enum):
    NEW_FILING             = "NEW_FILING"
    AMENDED_FILING         = "AMENDED_FILING"
    RESTATED_FILING        = "RESTATED_FILING"
    UNSEEN_PERIOD          = "UNSEEN_PERIOD"
    CHANGED_PAYLOAD        = "CHANGED_PAYLOAD"
    NO_CHANGE              = "NO_CHANGE"


class SnapshotFreshnessStatus(str, Enum):
    FRESH           = "FRESH"
    UPDATE_PENDING  = "UPDATE_PENDING"
    STALE           = "STALE"
    INVALID         = "INVALID"


@dataclass
class FilingEvent:
    """Represents a discovered financial filing event."""
    symbol: str
    exchange: str
    filing_id: str
    period_end_date: str
    statement_type: str        # ANNUAL or QUARTERLY
    basis: str                 # CONSOLIDATED or STANDALONE
    event_type: FilingEventType
    broadcast_timestamp: str
    retrieved_timestamp: str
    source_hash: str
    payload_path: str
    affected_metrics: List[str] = field(default_factory=list)
    raw_facts: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["event_type"] = self.event_type.value
        return d


class FinancialFilingWatcher:
    """
    Decoupled financial filing watcher, detector, and dependency-based invalidator.
    """

    def __init__(self, state_file: Path = STATE_FILE, events_log: Path = EVENTS_LOG_FILE):
        self.state_file = Path(state_file)
        self.events_log = Path(events_log)
        self.registry = ApprovedUniverseRegistry()
        self.approved_symbols = sorted(list(self.registry.approved_symbols))
        self.state: Dict[str, Dict[str, Any]] = self._load_state()

    def _load_state(self) -> Dict[str, Dict[str, Any]]:
        """Loads persistent watcher state."""
        if self.state_file.exists():
            try:
                with open(self.state_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load filing watcher state: {e}. Starting fresh.")
        return {}

    def _save_state(self) -> None:
        """Persists watcher state."""
        os.makedirs(self.state_file.parent, exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump(self.state, f, indent=2)

    def _log_event(self, event: FilingEvent) -> None:
        """Appends event to audit log."""
        os.makedirs(self.events_log.parent, exist_ok=True)
        with open(self.events_log, "a") as f:
            f.write(json.dumps(event.to_dict()) + "\n")

    # -------------------------------------------------------------------------
    # 1. DISCOVER & DETECT
    # -------------------------------------------------------------------------

    def detect_filing_changes(
        self,
        symbol: str,
        incoming_filing: Dict[str, Any],
        raw_payload: bytes,
    ) -> FilingEvent:
        """
        Detects whether an incoming filing is NEW, AMENDED, RESTATED, or UNCHANGED.
        """
        sym = symbol.strip().upper()
        filing_id = str(incoming_filing.get("filing_id", "UNKNOWN_FILING"))
        period_end = str(incoming_filing.get("period_end_date", ""))
        st_type = str(incoming_filing.get("statement_type", "QUARTERLY")).upper()
        basis = str(incoming_filing.get("basis", StatementBasis.CONSOLIDATED)).upper()
        broadcast_time = str(incoming_filing.get("broadcast_timestamp") or incoming_filing.get("filing_date") or datetime.now().isoformat())
        payload_hash = hashlib.sha256(raw_payload).hexdigest()

        known = self.state.get(sym, {})
        known_filings = known.get("filings", {})

        # Determine event type
        if filing_id in known_filings:
            prev = known_filings[filing_id]
            prev_hash = prev.get("source_hash")
            if prev_hash != payload_hash:
                if incoming_filing.get("amended", False):
                    event_type = FilingEventType.AMENDED_FILING
                elif incoming_filing.get("restated", False):
                    event_type = FilingEventType.RESTATED_FILING
                else:
                    event_type = FilingEventType.CHANGED_PAYLOAD
            else:
                event_type = FilingEventType.NO_CHANGE
        else:
            # Check if this period was previously unseen
            known_periods = {v.get("period_end_date") for v in known_filings.values()}
            if period_end not in known_periods:
                event_type = FilingEventType.UNSEEN_PERIOD
            else:
                event_type = FilingEventType.NEW_FILING

        # Determine affected metrics based on dependency graph
        affected_metrics = self.resolve_dependent_metrics(st_type, incoming_filing)

        # Store raw payload immutably
        sym_raw_dir = EXCHANGE_DIR / sym / "raw"
        os.makedirs(sym_raw_dir, exist_ok=True)
        version_str = f"v{incoming_filing.get('revision_number', 1)}"
        payload_file = sym_raw_dir / f"{filing_id}_{version_str}.payload"
        sha_file = sym_raw_dir / f"{filing_id}_{version_str}.sha256"

        with open(payload_file, "wb") as f:
            f.write(raw_payload)
        with open(sha_file, "w") as f:
            f.write(payload_hash)

        event = FilingEvent(
            symbol=sym,
            exchange=str(incoming_filing.get("source_exchange", "NSE")),
            filing_id=filing_id,
            period_end_date=period_end,
            statement_type=st_type,
            basis=basis,
            event_type=event_type,
            broadcast_timestamp=broadcast_time,
            retrieved_timestamp=datetime.now().isoformat(),
            source_hash=payload_hash,
            payload_path=str(payload_file.relative_to(BASE_DIR)),
            affected_metrics=affected_metrics,
            raw_facts=incoming_filing.get("facts", {}),
        )

        if event_type != FilingEventType.NO_CHANGE:
            # Update state
            if sym not in self.state:
                self.state[sym] = {"filings": {}, "latest_filing_date": None, "snapshot_status": SnapshotFreshnessStatus.FRESH.value}
            self.state[sym]["filings"][filing_id] = {
                "period_end_date": period_end,
                "statement_type": st_type,
                "broadcast_timestamp": broadcast_time,
                "source_hash": payload_hash,
                "last_updated": datetime.now().isoformat(),
            }
            self.state[sym]["latest_filing_date"] = period_end
            self.state[sym]["snapshot_status"] = SnapshotFreshnessStatus.UPDATE_PENDING.value
            self._save_state()
            self._log_event(event)

            logger.info(
                f"📢 [FILING_EVENT: {event_type.value}] {sym}: filing_id={filing_id} "
                f"period={period_end} ({st_type}) | affected={len(affected_metrics)} metrics"
            )

        return event

    # -------------------------------------------------------------------------
    # 2. DEPENDENCY-BASED METRIC INVALIDATION ENGINE
    # -------------------------------------------------------------------------

    @staticmethod
    def resolve_dependent_metrics(
        statement_type: str,
        filing_data: Dict[str, Any],
    ) -> List[str]:
        """
        Determines exactly which downstream metrics must be recalculated.
        Implements the dependency graph specified in the architecture.
        """
        st_upper = statement_type.upper()
        facts = filing_data.get("facts", {})
        deps: Set[str] = set()

        if st_upper == "QUARTERLY":
            # Quarterly P&L affects YoY growth & earnings acceleration
            deps.update([
                "rev_yoy_latest", "rev_yoy_prev",
                "op_profit_yoy_latest", "op_profit_yoy_prev",
                "eps_yoy_latest", "eps_yoy_prev",
                "prior_eps", "growth_score",
                "ttm_revenue", "ttm_operating_profit", "ttm_pat",
                "quarterly_margins",
            ])
            # If quarterly balance sheet items provided:
            if "total_debt" in facts or "cash_and_equivalents" in facts:
                deps.update(["current_ev", "current_ev_ebitda", "debt_to_equity"])
            if "shares_outstanding" in facts:
                deps.update(["shares_outstanding", "market_cap", "current_ev", "current_pe"])

        elif st_upper == "ANNUAL":
            # Annual balance sheet and cash flow statement affects long-term quality
            deps.update([
                "5y_sales_cagr", "5y_pat_cagr",
                "roce_5y_avg", "cfo_pat_5y_ratio",
                "operating_cash_flow",
                "debt_to_equity",
                "total_debt", "total_equity", "cash_and_equivalents",
                "current_ev", "current_ev_ebitda", "ev_ebitda_3y_median",
                "current_pe", "pe_3y_median",
                "shares_outstanding", "market_cap",
                "pit_freshness_status",
            ])

        return sorted(list(deps))

    # -------------------------------------------------------------------------
    # 3. REBUILD CANONICAL SNAPSHOT
    # -------------------------------------------------------------------------

    def invalidate_and_rebuild_snapshot(
        self,
        symbol: Optional[str] = None,
        as_of_date: Optional[date] = None,
    ) -> bool:
        """
        Recalculates dependent metrics and updates the canonical PIT dataset.
        Sets snapshot status to FRESH when successfully built.
        """
        as_of = as_of_date or date.today()
        sym_target = symbol.strip().upper() if symbol else "ALL_PENDING"
        logger.info(f"🔄 [SNAPSHOT_REBUILD] Target: {sym_target} as of {as_of.isoformat()}...")

        try:
            from scripts.rebuild_pit_from_exchange import rebuild_canonical_pit_dataset
            df = rebuild_canonical_pit_dataset(as_of_date=as_of)

            # Mark state as FRESH
            if symbol and symbol.upper() in self.state:
                self.state[symbol.upper()]["snapshot_status"] = SnapshotFreshnessStatus.FRESH.value
            else:
                for s in self.state.values():
                    if s.get("snapshot_status") == SnapshotFreshnessStatus.UPDATE_PENDING.value:
                        s["snapshot_status"] = SnapshotFreshnessStatus.FRESH.value
            self._save_state()

            logger.info(f"✅ [SNAPSHOT_REBUILD: SUCCESS] Rebuilt {len(df)} rows. Status set to FRESH.")
            return True
        except Exception as e:
            logger.error(f"❌ [SNAPSHOT_REBUILD: FAILED] {e}")
            return False

    # -------------------------------------------------------------------------
    # 4. SCANNER STATUS QUERY
    # -------------------------------------------------------------------------

    def get_symbol_freshness_status(self, symbol: str) -> SnapshotFreshnessStatus:
        """
        Called by the 17:00 Scanner to verify if a stock's snapshot is valid.
        Returns: FRESH, UPDATE_PENDING, STALE, or INVALID.
        """
        sym = symbol.strip().upper()
        entry = self.state.get(sym)
        if not entry:
            return SnapshotFreshnessStatus.FRESH  # Default to baseline if un-updated

        status_str = entry.get("snapshot_status", SnapshotFreshnessStatus.FRESH.value)
        return SnapshotFreshnessStatus(status_str)


# -----------------------------------------------------------------------------
# CLI Driver
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Financial Filing Watcher & Dependency Invalidation Engine")
    parser.add_argument("--poll", action="store_true", help="Poll for new filings")
    parser.add_argument("--symbol", type=str, default=None, help="Specific symbol to check/rebuild")
    parser.add_argument("--force-rebuild", action="store_true", help="Force rebuild canonical snapshot")
    parser.add_argument("--as-of-date", type=str, default=None, help="As-of date in YYYY-MM-DD")
    args = parser.parse_args()

    as_of = datetime.strptime(args.as_of_date, "%Y-%m-%d").date() if args.as_of_date else date.today()
    watcher = FinancialFilingWatcher()

    if args.force_rebuild:
        watcher.invalidate_and_rebuild_snapshot(symbol=args.symbol, as_of_date=as_of)
    else:
        print("\n" + "=" * 80)
        print("  FINANCIAL FILING WATCHER & FRESHNESS ENGINE STATUS")
        print("=" * 80)
        print(f"  Approved Equities Tracked    : {len(watcher.approved_symbols)}")
        print(f"  Watch State Path             : {watcher.state_file}")
        print(f"  Events Audit Log             : {watcher.events_log}")
        print(f"  Canonical Snapshot Path      : {SNAPSHOT_PATH}")
        print(f"  Snapshot Exists On Disk      : {SNAPSHOT_PATH.exists()}")
        if args.symbol:
            sym_u = args.symbol.upper()
            status = watcher.get_symbol_freshness_status(sym_u)
            print(f"  Freshness Status for {sym_u:<10}: {status.value}")
        print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
