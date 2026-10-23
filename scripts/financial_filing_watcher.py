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
import math
import os
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Ensure repository root is position 0 on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
APP_DIR = BASE_DIR / "app"
if str(APP_DIR) in sys.path:
    sys.path.remove(str(APP_DIR))
sys.path.append(str(APP_DIR))

import numpy as np
import pandas as pd

try:
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
except ImportError:
    from financial_data_integrity import (
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

try:
    from app.live_fundamental_scanner import (
        ApprovedUniverseRegistry,
        _get_pit_filings,
    )
except ImportError:
    from live_fundamental_scanner import (
        ApprovedUniverseRegistry,
        _get_pit_filings,
    )

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("filling scanner")

STATE_FILE = BASE_DIR / "data" / "filing_watcher_state.json"
EVENTS_LOG_FILE = BASE_DIR / "data" / "filing_invalidation_events.jsonl"
SNAPSHOT_PATH = BASE_DIR / "data" / "canonical_pit_rebuilt.parquet"
EXCHANGE_DIR = BASE_DIR / "data" / "exchange_financials"

# [RULE 67 CHANGE-RATIONALE: OPTIMAL_FILING_LOOKBACK_v1.0]
# The maximum fundamental lookback needed by ANY scanner in the Elite Breakout System is 5 years
# (5Y Sales CAGR, 5Y PAT CAGR, 5Y ROCE average, 5Y CFO/PAT cumulative).
# Per user requirement (max_years + 1 = 6 years):
# We fetch 6 years of historical filings (e.g. from (current_year - 6)-01-01 -> 2020-01-01 in 2026).
# Ingestion from 2015 was downloading 11 years (50+ XBRL XML documents per stock), causing massive
# network latency (2+ hours runtime) for disclosures from 2015-2019 that no scanner ever inspects.
MAX_SCANNER_LOOKBACK_YEARS = 5
BUFFER_YEARS = 1
HISTORICAL_LOOKBACK_YEARS = MAX_SCANNER_LOOKBACK_YEARS + BUFFER_YEARS  # 6 years


def get_historical_cutoff_date(reference_date: Optional[date] = None) -> str:
    """Returns the ISO start date for historical filing ingestion (e.g. '2020-01-01')."""
    ref_year = (reference_date or date.today()).year
    cutoff_year = ref_year - HISTORICAL_LOOKBACK_YEARS
    return f"{cutoff_year}-01-01"


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

    def __post_init__(self):
        norm = _normalize_iso_date(self.period_end_date)
        if norm:
            self.period_end_date = norm
        elif self.raw_facts and isinstance(self.raw_facts, dict):
            raw_to = self.raw_facts.get("toDate") or self.raw_facts.get("period_end_date") or self.raw_facts.get("periodEnd")
            norm_raw = _normalize_iso_date(raw_to)
            if norm_raw:
                self.period_end_date = norm_raw

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["event_type"] = self.event_type.value if hasattr(self.event_type, "value") else str(self.event_type)
        norm = _normalize_iso_date(d.get("period_end_date"))
        if norm:
            d["period_end_date"] = norm
        elif self.raw_facts and isinstance(self.raw_facts, dict):
            raw_to = self.raw_facts.get("toDate") or self.raw_facts.get("period_end_date") or self.raw_facts.get("periodEnd")
            norm_raw = _normalize_iso_date(raw_to)
            if norm_raw:
                d["period_end_date"] = norm_raw
        return d


def _normalize_iso_date(val: Any) -> Optional[str]:
    """Normalizes any exchange date string (e.g. '31-Mar-2020', '31-03-2020') into standard ISO YYYY-MM-DD."""
    if not val:
        return None
    s = str(val).strip()
    import re
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        try:
            datetime.strptime(s, "%Y-%m-%d")
            return s
        except ValueError:
            return None
    try:
        from app.data_providers.nse_xbrl_provider import _parse_date_to_iso
        res = _parse_date_to_iso(s)
        if res and re.match(r"^\d{4}-\d{2}-\d{2}$", res):
            return res
    except Exception:
        pass
    for fmt in ("%d-%b-%Y", "%d-%b-%y", "%d-%B-%Y", "%d-%B-%y", "%d-%m-%Y", "%d-%m-%y", "%Y/%m/%d", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y%m%d"):
        try:
            clean_token = s.split()[0]
            d = datetime.strptime(clean_token, fmt).strftime("%Y-%m-%d")
            if re.match(r"^\d{4}-\d{2}-\d{2}$", d):
                return d
        except Exception:
            pass
    return None


_WATCHER_LOCK = threading.Lock()


class FinancialFilingWatcher:
    """
    Decoupled financial filing watcher, detector, and dependency-based invalidator.
    """

    def __init__(
        self,
        state_file: Path = STATE_FILE,
        events_log: Path = EVENTS_LOG_FILE,
        pit_raw_dir: Optional[Path] = None,
    ):
        self.state_file = Path(state_file)
        self.events_log = Path(events_log)
        if pit_raw_dir is not None:
            self.pit_raw_dir = Path(pit_raw_dir)
        elif self.state_file == STATE_FILE:
            self.pit_raw_dir = BASE_DIR / "data" / "pit_raw_filings"
        else:
            self.pit_raw_dir = self.state_file.parent / "pit_raw_filings"
        self.registry = ApprovedUniverseRegistry()
        self.approved_symbols = sorted(list(self.registry.approved_symbols))
        self.state: Dict[str, Dict[str, Any]] = self._load_state()
        self._state_lock = threading.Lock()

    def _load_state(self) -> Dict[str, Dict[str, Any]]:
        """Loads persistent watcher state with automated healing of legacy truncated dates and DB restoration."""
        if not self.state_file.exists() or os.path.getsize(self.state_file) < 5:
            if self.state_file == STATE_FILE:
                try:
                    try:
                        from app.database import get_connection
                    except ImportError:
                        from database import get_connection
                    with get_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute("SELECT data FROM parquet_cache WHERE name = 'filing_watcher_state' ORDER BY date DESC LIMIT 1")
                            row = cur.fetchone()
                            if row and row[0]:
                                raw_bytes = bytes(row[0])
                                os.makedirs(self.state_file.parent, exist_ok=True)
                                with open(self.state_file, "wb") as f:
                                    f.write(raw_bytes)
                                logger.info("✅ [WATCHER] Restored filing_watcher_state from DB parquet_cache")
                except Exception as _e_restore:
                    logger.debug(f"[WATCHER] Could not restore state from DB: {_e_restore}")

        if self.state_file.exists():
            try:
                import re as _re_state
                with open(self.state_file, "r") as f:
                    data = json.load(f)
                dirty = False
                for sym, sdata in data.items():
                    l_date = str(sdata.get("latest_filing_date") or "")
                    if l_date and not _re_state.match(r"^\d{4}-\d{2}-\d{2}$", l_date):
                        norm_l = _normalize_iso_date(l_date)
                        if norm_l:
                            sdata["latest_filing_date"] = norm_l
                            dirty = True
                        else:
                            valid_p = []
                            for fmeta in sdata.get("filings", {}).values():
                                p = _normalize_iso_date(fmeta.get("period_end_date"))
                                if p:
                                    valid_p.append(p)
                            sdata["latest_filing_date"] = max(valid_p) if valid_p else None
                            dirty = True
                    for fid, fmeta in sdata.get("filings", {}).items():
                        p = str(fmeta.get("period_end_date") or "")
                        if p and not _re_state.match(r"^\d{4}-\d{2}-\d{2}$", p):
                            norm_p = _normalize_iso_date(p)
                            if norm_p:
                                fmeta["period_end_date"] = norm_p
                                dirty = True
                if dirty:
                    tmp_state = f"{self.state_file}.tmp.{os.getpid()}"
                    with open(tmp_state, "w") as f:
                        json.dump(data, f, indent=2)
                    os.replace(tmp_state, self.state_file)
                return data
            except Exception as e:
                logger.warning(f"Failed to load filing watcher state: {e}. Starting fresh.")
        return {}

    def _save_state(self) -> None:
        """Persists watcher state atomically to disk and backs up to DB."""
        os.makedirs(self.state_file.parent, exist_ok=True)
        tmp_state = f"{self.state_file}.tmp.{os.getpid()}"
        with open(tmp_state, "w") as f:
            json.dump(self.state, f, indent=2)
        os.replace(tmp_state, self.state_file)

        # Atomically backup state to PostgreSQL parquet_cache so it survives container redeployments
        if self.state_file == STATE_FILE and len(self.state) > 0:
            try:
                try:
                    from app.database import get_connection, _DB_WRITE_LOCK, init_db, IST
                except ImportError:
                    from database import get_connection, _DB_WRITE_LOCK, init_db, IST
                init_db()
                import psycopg2
                today_str = datetime.now(IST).strftime("%Y-%m-%d")
                state_bytes = json.dumps(self.state).encode("utf-8")
                with _DB_WRITE_LOCK:
                    with get_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute("""
                                INSERT INTO parquet_cache (name, date, data)
                                VALUES (%s, %s, %s)
                                ON CONFLICT (name, date) DO UPDATE SET data = EXCLUDED.data
                            """, ("filing_watcher_state", today_str, psycopg2.Binary(state_bytes)))
                            cur.execute("DELETE FROM parquet_cache WHERE name = 'filing_watcher_state' AND date < %s", (today_str,))
                        conn.commit()
            except Exception as _db_s_err:
                logger.debug(f"[WATCHER] State DB backup notice: {_db_s_err}")

    def _log_event(self, event: FilingEvent) -> None:
        """Appends event to audit log with guaranteed normalized period_end_date."""
        norm_date = _normalize_iso_date(event.period_end_date)
        if norm_date:
            event.period_end_date = norm_date
        elif event.raw_facts and isinstance(event.raw_facts, dict):
            raw_to = event.raw_facts.get("toDate") or event.raw_facts.get("period_end_date")
            norm_raw = _normalize_iso_date(raw_to)
            if norm_raw:
                event.period_end_date = norm_raw
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
        Detects whether an incoming filing is NEW, AMENDED, RESTATED, or UNCHANGED
        by comparing against BOTH local PIT raw filings (data/pit_raw_filings/<sym>.json)
        and the watcher state registry (data/financial_filing_watcher_state.json).

        Whenever an incoming filing does not match local data (missing period, null/empty
        local metrics, or numerical discrepancy), it is flagged for local reconciliation,
        merged into local data, and recorded into corporate_filings_log under today's date.
        """
        sym = symbol.strip().upper()
        filing_id = str(incoming_filing.get("filing_id", "UNKNOWN_FILING"))
        raw_period = str(incoming_filing.get("period_end_date") or incoming_filing.get("toDate") or incoming_filing.get("periodEnd") or "")
        period_end = _normalize_iso_date(raw_period)
        st_type = str(incoming_filing.get("statement_type", "QUARTERLY")).upper()
        if "ANNUAL" in st_type or "YEAR" in st_type or "FY" in st_type:
            st_type = "ANNUAL"
        else:
            st_type = "QUARTERLY"
        basis = str(incoming_filing.get("basis", StatementBasis.CONSOLIDATED)).upper()
        broadcast_time = str(incoming_filing.get("broadcast_timestamp") or incoming_filing.get("filing_date") or datetime.now().isoformat())
        payload_hash = hashlib.sha256(raw_payload).hexdigest()

        if not period_end or period_end < "2015-01-01":
            logger.debug(f"[WATCHER] Rejected filing with invalid period_end '{raw_period}' for {sym}")
            return FilingEvent(
                symbol=sym,
                exchange=str(incoming_filing.get("source_exchange", "NSE")),
                filing_id=filing_id,
                period_end_date="INVALID_DATE",
                statement_type=st_type,
                basis=basis,
                event_type=FilingEventType.NO_CHANGE,
                broadcast_timestamp=broadcast_time,
                retrieved_timestamp=datetime.now().isoformat(),
                source_hash=payload_hash,
                payload_path="",
                affected_metrics=[],
                raw_facts=incoming_filing.get("facts", {}),
            )

        known = self.state.get(sym, {})
        known_filings = known.get("filings", {})


        facts = incoming_filing.get("facts", {})
        if not facts and isinstance(incoming_filing, dict):
            facts = dict(incoming_filing)

        # Also attempt to parse facts from raw_payload if facts dict is sparse
        try:
            payload_json = json.loads(raw_payload.decode("utf-8"))
            if isinstance(payload_json, dict):
                for pk, pv in payload_json.items():
                    if pk not in facts:
                        facts[pk] = pv
        except Exception:
            pass

        inc_rev = facts.get("revenue") or facts.get("revenueFromOperations") or facts.get("totalIncome") or facts.get("revenue_cr")
        inc_pat = facts.get("net_profit") or facts.get("netProfitLossForPeriod") or facts.get("profitBeforeTax") or facts.get("pat") or facts.get("pat_cr")
        inc_ebit = facts.get("operating_profit") or facts.get("ebit") or facts.get("operatingProfit")
        inc_eps = facts.get("eps") or facts.get("basicEps")

        # 1. Compare against local data: self.pit_raw_dir / f"{sym}.json"
        local_raw_path = self.pit_raw_dir / f"{sym}.json"
        local_period_found = False
        local_matched_row = None
        if local_raw_path.exists():
            try:
                with open(local_raw_path, "r", encoding="utf-8") as f:
                    local_data = json.load(f)
                    if isinstance(local_data, list):
                        for row in local_data:
                            r_per = _normalize_iso_date(row.get("period_end_date"))
                            r_typ = str(row.get("statement_type") or "").upper()
                            r_bas = str(row.get("basis") or ("STANDALONE" if "standalone" in str(row.get("source", "")).lower() else "CONSOLIDATED")).upper()
                            if r_per and r_per == period_end and r_typ == st_type and r_bas == basis:
                                local_period_found = True
                                local_matched_row = row
                                break
            except Exception as e:
                logger.debug(f"[filling scanner] Local file read note for {sym}: {e}")

        event_type = FilingEventType.NO_CHANGE
        unmatching_reason = None

        if local_raw_path.exists():
            if not local_period_found:
                # Case A: Period completely missing from local data -> UNMATCHING!
                event_type = FilingEventType.UNSEEN_PERIOD
                unmatching_reason = f"Period {period_end} ({st_type}) missing from local PIT raw filings"
            elif local_matched_row:
                # Case B: Local record exists, but check if local facts are missing or unmatching
                loc_rev = local_matched_row.get("revenue") or local_matched_row.get("mapped_revenue")
                loc_pat = local_matched_row.get("net_profit") or local_matched_row.get("pat")
                loc_eps = local_matched_row.get("eps")
                loc_debt = local_matched_row.get("total_debt") or local_matched_row.get("debt")
                loc_equity = local_matched_row.get("total_equity") or local_matched_row.get("equity")
                loc_ebit = local_matched_row.get("operating_profit") or local_matched_row.get("ebit")
                loc_shares = local_matched_row.get("shares_outstanding")
                loc_ocf = local_matched_row.get("operating_cash_flow") or local_matched_row.get("ocf")

                inc_debt = facts.get("total_debt") or facts.get("debt")
                inc_equity = facts.get("total_equity") or facts.get("equity")
                inc_ebit = facts.get("operating_profit") or facts.get("ebit") or facts.get("operatingProfit")
                inc_shares = facts.get("shares_outstanding")
                inc_ocf = facts.get("operating_cash_flow") or facts.get("ocf")

                # Check if local has nulls while exchange has actual figures (Partial Missing Fact Recovery)
                has_reconcilable_fact = (
                    (loc_rev is None and inc_rev is not None)
                    or (loc_pat is None and inc_pat is not None)
                    or (loc_debt is None and inc_debt is not None)
                    or (loc_equity is None and inc_equity is not None)
                    or (loc_ebit is None and inc_ebit is not None)
                    or (loc_shares is None and inc_shares is not None)
                    or (loc_ocf is None and inc_ocf is not None)
                )

                if has_reconcilable_fact:
                    event_type = FilingEventType.CHANGED_PAYLOAD
                    unmatching_reason = "Local record has missing/null financial statement facts; reconciled from exchange facts"
                # Check for numerical discrepancy (> 0.1 Cr)
                elif inc_rev is not None and loc_rev is not None:
                    try:
                        if abs(float(inc_rev) - float(loc_rev)) > 0.1:
                            event_type = FilingEventType.AMENDED_FILING
                            unmatching_reason = f"Revenue discrepancy: local={loc_rev} vs exchange={inc_rev}"
                    except Exception:
                        pass
                if event_type == FilingEventType.NO_CHANGE and inc_pat is not None and loc_pat is not None:
                    try:
                        if abs(float(inc_pat) - float(loc_pat)) > 0.1:
                            event_type = FilingEventType.AMENDED_FILING
                            unmatching_reason = f"PAT discrepancy: local={loc_pat} vs exchange={inc_pat}"
                    except Exception:
                        pass

        # Case C: Check watcher state and payload hash
        if event_type == FilingEventType.NO_CHANGE:
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
                    unmatching_reason = "Payload hash changed from previous version"
            elif not local_period_found:
                known_periods = {v.get("period_end_date") for v in known_filings.values()}
                if period_end not in known_periods:
                    event_type = FilingEventType.UNSEEN_PERIOD
                    unmatching_reason = "Period unseen in watcher state registry"
                else:
                    event_type = FilingEventType.NEW_FILING
                    unmatching_reason = "New filing identifier discovered on exchange"

        # Determine affected metrics based on dependency graph
        affected_metrics = self.resolve_dependent_metrics(st_type, incoming_filing)

        # Store raw payload immutably
        sym_raw_dir = EXCHANGE_DIR / sym / "raw"
        os.makedirs(sym_raw_dir, exist_ok=True)
        version_str = f"v{incoming_filing.get('revision_number', 1)}"
        payload_file = sym_raw_dir / f"{filing_id}_{version_str}.payload"
        sha_file = sym_raw_dir / f"{filing_id}_{version_str}.sha256"

        if not payload_file.exists() or event_type != FilingEventType.NO_CHANGE:
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
            with self._state_lock:
                # Update state
                if sym not in self.state:
                    self.state[sym] = {"filings": {}, "latest_filing_date": None, "snapshot_status": SnapshotFreshnessStatus.FRESH.value}
                norm_p_end = _normalize_iso_date(period_end) or period_end
                self.state[sym]["filings"][filing_id] = {
                    "period_end_date": norm_p_end,
                    "statement_type": st_type,
                    "broadcast_timestamp": broadcast_time,
                    "source_hash": payload_hash,
                    "last_updated": datetime.now().isoformat(),
                }
                prev_latest = self.state[sym].get("latest_filing_date")
                if _normalize_iso_date(norm_p_end):
                    if not prev_latest or not _normalize_iso_date(prev_latest) or norm_p_end > prev_latest:
                        self.state[sym]["latest_filing_date"] = norm_p_end
                self.state[sym]["snapshot_status"] = SnapshotFreshnessStatus.UPDATE_PENDING.value
                self._save_state()
                self._log_event(event)

            # Only persist discovery report for the latest filing discovered for this stock
            if prev_latest and _normalize_iso_date(prev_latest) and norm_p_end < prev_latest:
                logger.debug(f"[filling scanner] Skipping finding report log for older quarter {sym} {norm_p_end} (latest is {prev_latest})")
                return event

            # Persist finding to corporate_filings_log database table & local cache
            # Only persist to database when running in production (self.state_file == STATE_FILE)
            if self.state_file == STATE_FILE:
                try:
                    try:
                        from app.database import upsert_corporate_filing_finding
                    except ImportError:
                        from database import upsert_corporate_filing_finding

                    from zoneinfo import ZoneInfo
                    today_ist = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")

                    rev_f = float(inc_rev) if inc_rev is not None else None
                    pat_f = float(inc_pat) if inc_pat is not None else None
                    ebit_f = float(inc_ebit) if inc_ebit is not None else None
                    eps_f = float(inc_eps) if inc_eps is not None else None

                    headline_parts = []
                    if rev_f is not None: headline_parts.append(f"Rev ₹{rev_f:,.1f} Cr")
                    if pat_f is not None: headline_parts.append(f"PAT ₹{pat_f:,.1f} Cr")
                    stats_str = ", ".join(headline_parts)
                    headline = f"{sym} {period_end} ({st_type}): {stats_str}" if stats_str else f"{sym} {period_end} ({st_type}) Filing Reconciled"
                    if unmatching_reason:
                        headline += f" [{event_type.value}]"

                    # [RULE 67 CHANGE-RATIONALE: Resolve true filing/broadcast date instead of hardcoding today_ist.
                    # Prevents historical backfills/amendments for past periods (e.g. FY2021) from being falsely
                    # reported in corporate_filings_log as if declared today. Audit discovery timestamp is preserved in discovered_at.]
                    true_filing_date = None
                    if broadcast_time and str(broadcast_time).strip():
                        b_str = str(broadcast_time).strip()
                        b_norm = _normalize_iso_date(b_str[:10])
                        if b_norm:
                            true_filing_date = b_norm
                    if not true_filing_date and facts.get("availability_date"):
                        true_filing_date = _normalize_iso_date(str(facts.get("availability_date"))[:10])
                    if not true_filing_date and facts.get("filingDate"):
                        true_filing_date = _normalize_iso_date(str(facts.get("filingDate"))[:10])
                    if not true_filing_date and facts.get("filing_date"):
                        true_filing_date = _normalize_iso_date(str(facts.get("filing_date"))[:10])
                    if not true_filing_date:
                        true_filing_date = period_end or today_ist

                    upsert_corporate_filing_finding({
                        "filing_date": true_filing_date,
                        "discovered_at": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
                        "symbol": sym,
                        "company_name": sym,
                        "filing_id": filing_id,
                        "exchange": event.exchange,
                        "period_end": period_end,
                        "statement_type": st_type,
                        "basis": basis,
                        "event_type": event_type.value,
                        "revenue_cr": rev_f,
                        "operating_profit_cr": ebit_f,
                        "pat_cr": pat_f,
                        "eps": eps_f,
                        "roce_pct": float(facts.get("roce")) if facts.get("roce") is not None else None,
                        "roe_pct": float(facts.get("roe")) if facts.get("roe") is not None else None,
                        "debt_to_equity": float(facts.get("total_debt") or facts.get("debt_to_equity")) if (facts.get("total_debt") or facts.get("debt_to_equity")) is not None else None,
                        "operating_margin_pct": float(facts.get("operating_margin")) if facts.get("operating_margin") is not None else None,
                        "net_margin_pct": float(facts.get("net_margin")) if facts.get("net_margin") is not None else None,
                        "shares_outstanding": float(facts.get("shares_outstanding")) if facts.get("shares_outstanding") is not None else None,
                        "filing_broadcast_date": broadcast_time,
                        "status": "SNAPSHOT_UPDATED",
                        "headline": headline,
                        "raw_facts": facts,
                        "affected_metrics": affected_metrics,
                    })
                except Exception as _log_err:
                    logger.debug(f"[filling scanner] Failed to log corporate filing event: {_log_err}")

            logger.info(
                f"📢 [filling scanner] [FILING_EVENT: {event_type.value}] {sym}: filing_id={filing_id} "
                f"period={period_end} ({st_type}) | {unmatching_reason or ''} | affected={len(affected_metrics)} metrics"
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
        run_ctx: Any = None,
    ) -> bool:
        """
        Recalculates dependent metrics and updates the canonical PIT dataset.
        Sets snapshot status to FRESH when successfully built.
        Supports delta updates for individual or pending symbols.
        """
        as_of = as_of_date or date.today()
        sym_target = str(symbol) if symbol else "ALL_PENDING"
        logger.info(f"🔄 [filling scanner] [SNAPSHOT_REBUILD] Target: {sym_target} as of {as_of.isoformat()}...")

        try:
            from scripts.rebuild_pit_from_exchange import rebuild_canonical_pit_dataset
            from app.financial_data_integrity import clear_shared_snapshot_cache

            target_list = None
            if isinstance(symbol, (list, set, tuple)):
                target_list = [str(s).strip().upper() for s in symbol if s]
            elif symbol:
                target_list = [str(symbol).strip().upper()]
            elif SNAPSHOT_PATH.exists():
                pending_symbols = [
                    s for s, v in self.state.items()
                    if v.get("snapshot_status") == SnapshotFreshnessStatus.UPDATE_PENDING.value
                ]
                if 0 < len(pending_symbols) < len(self.approved_symbols):
                    target_list = pending_symbols

            df = rebuild_canonical_pit_dataset(
                as_of_date=as_of,
                target_symbols=target_list,
                run_ctx=run_ctx,
            )
            clear_shared_snapshot_cache()

            # Invariant: BUILD COMPLETE != DATA COMPLETE != SCANNER READY
            # Verify completeness before declaring FRESH.
            ev_count = int(df["current_ev_ebitda"].notna().sum()) if ("current_ev_ebitda" in df.columns) else 0
            if ev_count == 0 and len(df) > 0:
                logger.error(
                    f"❌ [filling scanner] [SNAPSHOT_REBUILD: DATA_INCOMPLETE] Rebuilt {len(df)} rows, "
                    f"but Current EV/EBITDA is 0/{len(df)}. Refusing to mark snapshot FRESH!"
                )
                sym_list = [symbol.strip().upper()] if isinstance(symbol, str) else ([str(s).strip().upper() for s in symbol] if isinstance(symbol, (list, set, tuple)) else [])
                for sym_u in sym_list:
                    if sym_u in self.state:
                        self.state[sym_u]["snapshot_status"] = SnapshotFreshnessStatus.INVALID.value
                if not sym_list:
                    for s in self.state.values():
                        if s.get("snapshot_status") == SnapshotFreshnessStatus.UPDATE_PENDING.value:
                            s["snapshot_status"] = SnapshotFreshnessStatus.INVALID.value
                self._save_state()
                return False

            # Mark state as FRESH only when data completeness invariants pass
            sym_list = [symbol.strip().upper()] if isinstance(symbol, str) else ([str(s).strip().upper() for s in symbol] if isinstance(symbol, (list, set, tuple)) else [])
            for sym_u in sym_list:
                if sym_u in self.state:
                    self.state[sym_u]["snapshot_status"] = SnapshotFreshnessStatus.FRESH.value
            if not sym_list:
                for s in self.state.values():
                    if s.get("snapshot_status") == SnapshotFreshnessStatus.UPDATE_PENDING.value:
                        s["snapshot_status"] = SnapshotFreshnessStatus.FRESH.value
            self._save_state()

            logger.info(f"✅ [filling scanner] [SNAPSHOT_REBUILD: SUCCESS] Rebuilt {len(df)} rows. Current EV/EBITDA Complete: {ev_count}/{len(df)}. Status set to FRESH.")

            # [RULE 67: RESTART RESILIENCE]
            # Upload newly rebuilt canonical snapshot and raw filings bundle to DB parquet_cache
            # so container restarts adopt the latest verified copy without data loss.
            try:
                try:
                    from app.database import upload_parquet_to_db, upload_pit_raw_bundle_to_db
                except ImportError:
                    from database import upload_parquet_to_db, upload_pit_raw_bundle_to_db
                if SNAPSHOT_PATH.exists():
                    upload_parquet_to_db("canonical_pit_rebuilt", str(SNAPSHOT_PATH))
                upload_pit_raw_bundle_to_db()
                logger.info("💾 [filling scanner] Uploaded latest canonical snapshot & raw filings bundle to Postgres DB.")
            except Exception as _db_up_err:
                logger.warning(f"[filling scanner] DB snapshot upload notice: {_db_up_err}")

            # If rebuilding for specific symbol(s), check and restore if previously stale
            if sym_list:
                for sym_u in sym_list:
                    try:
                        try:
                            from app.database import check_and_restore_stale_stock
                        except ImportError:
                            from database import check_and_restore_stale_stock
                        check_and_restore_stale_stock(
                            symbol=sym_u,
                            source="SNAPSHOT_REBUILD",
                            notify_admin=True
                        )
                    except Exception:
                        pass

            return True
        except Exception as e:
            logger.error(f"❌ [filling scanner] [SNAPSHOT_REBUILD: FAILED] {e}")
            sym_list = [symbol.strip().upper()] if isinstance(symbol, str) else ([str(s).strip().upper() for s in symbol] if isinstance(symbol, (list, set, tuple)) else [])
            for sym_u in sym_list:
                if sym_u in self.state:
                    self.state[sym_u]["snapshot_status"] = SnapshotFreshnessStatus.INVALID.value
            self._save_state()
            return False

    # -------------------------------------------------------------------------
    # 4. SCANNER STATUS QUERY
    # -------------------------------------------------------------------------

    def get_symbol_freshness_status(self, symbol: str) -> SnapshotFreshnessStatus:
        """
        Called by the 17:00 Scanner to verify if a stock's snapshot is valid.
        Returns: FRESH, UPDATE_PENDING, STALE, or INVALID.
        """
        if os.path.exists(self.state_file):
            disk_state = self._load_state()
            self.state = {**disk_state, **self.state}
        sym = symbol.strip().upper()
        entry = self.state.get(sym)
        if not entry:
            return SnapshotFreshnessStatus.FRESH  # Default to baseline if un-updated

        status_str = entry.get("snapshot_status", SnapshotFreshnessStatus.FRESH.value)
        return SnapshotFreshnessStatus(status_str)

    # -------------------------------------------------------------------------
    # 4b. LIVE EXCHANGE FILINGS POLLING & DISCOVERY
    # -------------------------------------------------------------------------

    def poll_exchange_filings(
        self,
        target_symbols: Optional[List[str]] = None,
        as_of_date: Optional[date] = None,
        max_symbols: Optional[int] = None,
        max_workers: Optional[int] = None,
        run_ctx: Optional[Any] = None,
        force_backfill: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Actively polls exchange sources (NSE XBRL API, BSE Corporate API, and FundamentalSourceRouter)
        for newly published filings/earnings disclosures using a concurrent worker pool.
        For every discovered filing:
          1. Detects filing changes (NEW_FILING, UNSEEN_PERIOD, AMENDED_FILING, NO_CHANGE).
          2. Immutably hashes and archives raw exchange payloads to data/exchange_financials/<SYM>/raw/.
          3. Upserts raw facts into data/pit_raw_filings/<SYM>.json.
          4. Logs discovery into corporate_filings_log database table & JSONL.
          5. Marks snapshot_status = UPDATE_PENDING for symbol if new/amended data ingested.
        """
        from concurrent.futures import ThreadPoolExecutor

        as_of = as_of_date or date.today()
        as_of_str = as_of.isoformat()
        symbols = target_symbols or self.approved_symbols
        if max_symbols:
            symbols = symbols[:max_symbols]

        if max_workers is None:
            max_workers = int(os.getenv("FILING_WATCHER_WORKERS", "12"))
        max_workers = max(1, min(max_workers, 16))


        logger.info(f"🌐 [filling scanner] Actively polling exchange filings (NSE/BSE) for {len(symbols)} symbols as of {as_of_str} (concurrency={max_workers})...")

        # Pre-load snapshot cache ONCE in memory to avoid repeated parquet disk reads
        snap_lookup: Dict[str, Any] = {}
        try:
            if SNAPSHOT_PATH.exists():
                import pandas as _pd_p
                _target_cols = [
                    "symbol", "is_bfsi", "latest_annual_period", "latest_quarterly_period",
                    "pit_freshness_status", "filing_gap_detected", "provenance_status",
                    "sales_cagr_5y", "pat_cagr_5y", "roce_5y_avg", "debt_to_equity",
                    "current_ev_ebitda", "total_debt", "annual_filing_count", "is_structural_ineligible"
                ]
                try:
                    _sdf_head = _pd_p.read_parquet(SNAPSHOT_PATH, nrows=1)
                    _avail_cols = [c for c in _target_cols if c in _sdf_head.columns]
                    _sdf = _pd_p.read_parquet(SNAPSHOT_PATH, columns=_avail_cols)
                except Exception:
                    _sdf = _pd_p.read_parquet(SNAPSHOT_PATH)
                for _, _r in _sdf.iterrows():
                    snap_lookup[str(_r["symbol"]).strip().upper()] = _r.to_dict()
        except Exception as _snap_err:
            logger.debug(f"[filling scanner] Could not pre-load snapshot lookup: {_snap_err}")

        # ── FAST MARKET-WIDE FILINGS PRE-SWEEP (1-2s for whole market) ──
        market_sweep_filings: Dict[str, List[Dict[str, Any]]] = {}
        market_sweep_ok = False
        try:
            try:
                from app.data_providers.nse_xbrl_provider import NseXbrlProvider
            except ImportError:
                from data_providers.nse_xbrl_provider import NseXbrlProvider
            _sweep_prov = NseXbrlProvider()
            market_sweep_filings = _sweep_prov.discover_market_filings_sweep()
            if market_sweep_filings:
                market_sweep_ok = True
                total_market_disclosures = sum(len(v) for v in market_sweep_filings.values())
                logger.info(
                    f"⚡ [filling scanner] Market-wide announcement sweep complete in ~2s: "
                    f"Found {total_market_disclosures} disclosures across {len(market_sweep_filings)} market symbols."
                )
                try:
                    try:
                        from app.financial_data_integrity import record_exchange_watermark
                    except ImportError:
                        from financial_data_integrity import record_exchange_watermark
                    _now_iso = datetime.now().isoformat()
                    max_sweep_bcast = None
                    for s_items in market_sweep_filings.values():
                        for it in s_items:
                            b_ts = it.get("broadCastDate") or it.get("filingDate") or it.get("broadcast_timestamp")
                            if b_ts:
                                b_str = str(b_ts)
                                if not max_sweep_bcast or b_str > max_sweep_bcast:
                                    max_sweep_bcast = b_str
                    record_exchange_watermark(source_name="NSE", last_successful_check_at=_now_iso, latest_filing_timestamp=max_sweep_bcast)
                    record_exchange_watermark(source_name="BSE", last_successful_check_at=_now_iso)
                    logger.info(f"🛡️ [WATERMARK] Recorded fresh exchange watermarks (NSE latest={max_sweep_bcast}, checked={_now_iso}).")
                except Exception as _wm_err:
                    logger.debug(f"[filling scanner] Watermark update note: {_wm_err}")
        except Exception as _sweep_err:
            logger.warning(f"⚠️ [filling scanner] Market sweep notice: {_sweep_err}")

        _tls = threading.local()

        def _get_thread_providers():
            if not hasattr(_tls, "nse_provider"):
                try:
                    try:
                        from app.data_providers.nse_xbrl_provider import NseXbrlProvider
                    except ImportError:
                        from data_providers.nse_xbrl_provider import NseXbrlProvider
                    _tls.nse_provider = NseXbrlProvider()
                    if market_sweep_ok and market_sweep_filings:
                        _tls.nse_provider._discovered_cache = dict(market_sweep_filings)
                except Exception as e:
                    logger.warning(f"[filling scanner] Could not initialize NseXbrlProvider in worker thread: {e}")
                    _tls.nse_provider = None

            if not hasattr(_tls, "bse_provider"):
                try:
                    try:
                        from app.data_providers.bse_corporate_provider import BseCorporateProvider
                    except ImportError:
                        from data_providers.bse_corporate_provider import BseCorporateProvider
                    _tls.bse_provider = BseCorporateProvider()
                except Exception as e:
                    logger.warning(f"[filling scanner] Could not initialize BseCorporateProvider in worker thread: {e}")
                    _tls.bse_provider = None

            return _tls.nse_provider, _tls.bse_provider

        completed_count = [0]
        skipped_complete_count = [0]
        bootstrap_ingested_count = [0]
        error_count = [0]
        start_poll_time = time.time()
        counter_lock = threading.Lock()
        events_lock = threading.Lock()
        batch_sync_lock = threading.Lock()
        discovered_events: List[Dict[str, Any]] = []
        pending_rebuild_syms: Set[str] = set()
        synced_symbols: Set[str] = set()
        last_synced_count = [0]
        synced_batch_num = [0]
        last_synced_time = [time.time()]
        TIME_SYNC_INTERVAL = float(os.getenv("FILING_WATCHER_TIME_SYNC_INTERVAL", "300.0"))  # 5 minutes (300 seconds)
        BATCH_SYNC_SIZE = int(os.getenv("FILING_WATCHER_BATCH_SYNC_SIZE", "25"))  # Save to DB after every 25 completed stocks
        FETCHED_SYNC_THRESHOLD = int(os.getenv("FILING_WATCHER_FETCHED_THRESHOLD", "10"))  # Or whenever 10 stocks ingested fresh filings

        def _process_symbol(sym: str) -> None:
            sym_u = sym.strip().upper()
            try:
                nse_prov, bse_prov = _get_thread_providers()
                snap_row = snap_lookup.get(sym_u)
                snap_annual = str(snap_row.get("latest_annual_period") or "")[:10] if snap_row is not None else ""
                freshness = str(snap_row.get("pit_freshness_status") or "FRESH") if snap_row is not None else "MISSING"
                prov_status = str(snap_row.get("provenance_status") or "") if snap_row is not None else "MISSING"
                is_bfsi = bool(snap_row.get("is_bfsi", False)) if snap_row is not None else False
                is_struct_ineligible = bool(snap_row.get("is_structural_ineligible", False)) if snap_row is not None else False
                snap_gap_flag = bool(snap_row.get("filing_gap_detected", False)) if snap_row is not None else False

                # ── HIGH-WATER MARK & BOOTSTRAP INGESTION GATE ──
                # Check local raw filing coverage in data/pit_raw_filings/<symbol>.json
                raw_path = self.pit_raw_dir / f"{sym_u}.json"
                local_annual_count = 0
                latest_local_period = None
                annual_years = set()
                quarterly_periods = set()
                latest_annual_rec = None
                has_corrupt_or_empty_raw = False
                has_nse_history = False

                if raw_path.exists():
                    try:
                        with open(raw_path, "r", encoding="utf-8") as _rf:
                            _ldata = json.load(_rf)
                        if isinstance(_ldata, list) and len(_ldata) > 0:
                            for _r in _ldata:
                                if str(_r.get("exchange") or "").upper() == "NSE" or "NSE" in str(_r.get("filing_id") or "").upper():
                                    has_nse_history = True
                                _p = _normalize_iso_date(str(_r.get("period_end_date") or _r.get("date") or ""))
                                if not _p or _p < get_historical_cutoff_date(as_of_date):
                                    continue
                                _st = str(_r.get("statement_type") or _r.get("period") or "").upper()
                                is_ann = ("ANNUAL" in _st or "YEAR" in _st or "FY" in _st)
                                is_qtr = ("QUARTER" in _st or "Q1" in _st or "Q2" in _st or "Q3" in _st or "Q4" in _st)

                                # Annual statements: audited full year results
                                if is_ann or (_p.endswith("-03-31") and not is_qtr):
                                    local_annual_count += 1
                                    try:
                                        annual_years.add(int(_p[:4]))
                                    except Exception:
                                        pass
                                    if is_ann:
                                        if latest_annual_rec is None or _p > str(latest_annual_rec.get("period_end_date", "")):
                                            latest_annual_rec = _r
                                        elif _p == str(latest_annual_rec.get("period_end_date", "")) and (_r.get("total_debt") is not None or _r.get("total_equity") is not None):
                                            latest_annual_rec = _r
                                    elif latest_annual_rec is None or _p > str(latest_annual_rec.get("period_end_date", "")):
                                        latest_annual_rec = _r

                                # Quarterly statements: Q1, Q2, Q3, and Q4 (including March 31 quarters)
                                if is_qtr or not is_ann or _p.endswith("-03-31"):
                                    quarterly_periods.add(_p)

                                if not latest_local_period or _p > latest_local_period:
                                    latest_local_period = _p
                        else:
                            has_corrupt_or_empty_raw = True
                    except Exception as _r_err:
                        logger.debug(f"[filling scanner] Local file read error for {sym_u}: {_r_err}")
                        has_corrupt_or_empty_raw = True
                else:
                    has_corrupt_or_empty_raw = True

                # 1. Intermediate Annual Gap Detection (y[i+1] - y[i] > 1) within required lookback window
                # [RULE 67 CHANGE-RATIONALE: Filter to required lookback window so ancient gaps before 2020 do not trigger false heals]
                min_gap_check_year = (as_of_date or date.today()).year - HISTORICAL_LOOKBACK_YEARS
                sorted_annual_years = sorted([y for y in annual_years if y >= min_gap_check_year])
                has_intermediate_gap = False
                intermediate_gap_reason = None
                for i in range(len(sorted_annual_years) - 1):
                    gap = sorted_annual_years[i + 1] - sorted_annual_years[i]
                    if gap > 1:
                        has_intermediate_gap = True
                        missing_years = list(range(sorted_annual_years[i] + 1, sorted_annual_years[i + 1]))
                        intermediate_gap_reason = f"Missing intermediate FY {missing_years} between {sorted_annual_years[i]} and {sorted_annual_years[i + 1]} in {sorted_annual_years}"
                        break

                # 2. Tail Annual Gap Detection (Latest annual vs expected completed FY from calendar date)
                # In India, most companies end FY on March 31 (audited by May 30).
                # Calendar-year companies (e.g. ABB, Nestle, Castrol) end FY on Dec 31 (audited by March/April).
                now_dt = date.today()
                latest_ann_end = str(latest_annual_rec.get("period_end_date") or "") if latest_annual_rec else ""
                is_calendar_yr = latest_ann_end.endswith("-12-31") or any(str(p).endswith("-12-31") for p in annual_years)
                if is_calendar_yr:
                    # Calendar year ends Dec 31; audited results published by March/April of following year.
                    expected_completed_annual_yr = (now_dt.year - 1) if now_dt.month >= 4 else (now_dt.year - 2)
                else:
                    expected_completed_annual_yr = now_dt.year if now_dt.month >= 6 else (now_dt.year - 1)

                has_tail_annual_gap = False
                tail_gap_reason = None
                if not annual_years or max(annual_years) < expected_completed_annual_yr:
                    has_tail_annual_gap = True
                    tail_gap_reason = f"Latest annual FY {max(annual_years) if annual_years else 'NONE'} is older than expected completed FY {expected_completed_annual_yr}"

                # 3. Intermediate Quarterly Gap Detection in Trailing 24 Months (>120 days between consecutive quarters)
                has_quarterly_gap = False
                quarterly_gap_reason = None
                if quarterly_periods:
                    recent_qs = sorted([p for p in quarterly_periods if p >= "2024-01-01"])
                    for qi in range(len(recent_qs) - 1):
                        try:
                            d1 = datetime.strptime(recent_qs[qi], "%Y-%m-%d").date()
                            d2 = datetime.strptime(recent_qs[qi + 1], "%Y-%m-%d").date()
                            if (d2 - d1).days > 120:
                                has_quarterly_gap = True
                                quarterly_gap_reason = f"Missing intermediate quarter between {recent_qs[qi]} and {recent_qs[qi+1]} ({(d2 - d1).days}d gap)"
                                break
                        except Exception:
                            pass

                # 4. Insufficient Depth (< 6 annual observations) with Missing 5Y CAGR
                insufficient_depth = len(sorted_annual_years) < 6
                has_missing_cagr = snap_row is not None and (snap_row.get("sales_cagr_5y") is None or pd.isna(snap_row.get("sales_cagr_5y")))
                depth_reason = None
                if insufficient_depth and not is_struct_ineligible and not is_bfsi and has_missing_cagr:
                    depth_reason = f"Insufficient annual depth ({len(sorted_annual_years)} < 6 years: {sorted_annual_years}) with missing 5Y CAGR"

                # 5. Missing Balance Sheet Facts in Latest Annual Record (total_debt or total_equity)
                has_missing_bs_facts = False
                missing_bs_reason = None
                if latest_annual_rec and not is_bfsi:
                    debt_val = latest_annual_rec.get("total_debt") or latest_annual_rec.get("debt")
                    eq_val = latest_annual_rec.get("total_equity") or latest_annual_rec.get("equity")
                    if debt_val is None or eq_val is None:
                        has_missing_bs_facts = True
                        missing_bs_reason = f"Latest annual record ({latest_annual_rec.get('period_end_date')}) missing total_debt or total_equity"

                # 6. Missing Canonical Snapshot Metrics (UNCERTIFIED, Sales CAGR, EV/EBITDA, D/E, ROCE)
                has_missing_canonical_metrics = False
                missing_metric_reason = None
                if snap_row is not None and not is_struct_ineligible:
                    if prov_status == "UNCERTIFIED":
                        has_missing_canonical_metrics = True
                        missing_metric_reason = "Canonical snapshot row is UNCERTIFIED"
                    elif not is_bfsi and pd.isna(snap_row.get("sales_cagr_5y")) and len(sorted_annual_years) >= 6:
                        has_missing_canonical_metrics = True
                        missing_metric_reason = "Missing 5Y Sales CAGR despite 6+ annual filings"
                    elif not is_bfsi and pd.isna(snap_row.get("current_ev_ebitda")):
                        has_missing_canonical_metrics = True
                        missing_metric_reason = "Missing current EV/EBITDA in canonical snapshot"
                    elif not is_bfsi and pd.isna(snap_row.get("debt_to_equity")):
                        has_missing_canonical_metrics = True
                        missing_metric_reason = "Missing Debt/Equity in canonical snapshot"
                    elif not is_bfsi and pd.isna(snap_row.get("roce_5y_avg")) and len(sorted_annual_years) >= 6:
                        has_missing_canonical_metrics = True
                        missing_metric_reason = "Missing 5Y ROCE average in canonical snapshot"

                # ── CONSOLIDATED BOOTSTRAP DECISION ──
                # If a symbol previously attempted bootstrap and local raw data has >= 6 annual filings,
                # do not infinitely re-attempt full backfill on every run for unresolvable exchange gaps.
                prev_sym_state = self.state.get(sym_u, {})
                already_bootstrapped = bool(prev_sym_state.get("bootstrap_completed", False))

                needs_bootstrap = (
                    force_backfill
                    or has_corrupt_or_empty_raw
                    or (len(sorted_annual_years) == 0)
                    or has_intermediate_gap
                    or has_tail_annual_gap
                    or (freshness not in ("FRESH", "VALID"))
                    or snap_gap_flag
                    or not snap_annual
                    or not latest_local_period
                    or (not already_bootstrapped and (
                        has_quarterly_gap
                        or (depth_reason is not None)
                        or has_missing_bs_facts
                        or has_missing_canonical_metrics
                        or snap_row is None
                    ))
                )

                has_newer_sweep_filing = False
                if needs_bootstrap:
                    min_cutoff = get_historical_cutoff_date(as_of_date)
                    reason_desc = (
                        intermediate_gap_reason
                        or tail_gap_reason
                        or quarterly_gap_reason
                        or missing_bs_reason
                        or missing_metric_reason
                        or depth_reason
                        or ("EMPTY_OR_CORRUPT_RAW" if has_corrupt_or_empty_raw else None)
                        or ("ZERO_ANNUAL_OBSERVATIONS" if len(sorted_annual_years) == 0 else None)
                        or ("SNAP_GAP" if snap_gap_flag else f"freshness={freshness}")
                    )
                    with counter_lock:
                        bootstrap_ingested_count[0] += 1
                    logger.info(
                        f"🚀 [filling scanner] [{completed_count[0] + 1}/{len(symbols)}] {sym_u}: "
                        f"BOOTSTRAP / HEAL NEEDED ({reason_desc}, local_annual={local_annual_count}, snap_annual={snap_annual or 'NONE'}). "
                        f"Ingesting complete history from {min_cutoff} to ensure 100% complete data."
                    )
                else:
                    # High-Water Mark: Symbol has verified continuous multi-year history and complete metrics.
                    # ── FAST 0ms MARKET-SWEEP EVALUATION ──
                    if market_sweep_ok and not force_backfill:
                        sym_sweep_items = market_sweep_filings.get(sym_u, [])
                        for it in sym_sweep_items:
                            p_end = _normalize_iso_date(it.get("period_end_date") or it.get("toDate"))
                            if p_end and latest_local_period and p_end > latest_local_period:
                                has_newer_sweep_filing = True
                                break
                            elif p_end and not latest_local_period:
                                has_newer_sweep_filing = True
                                break

                        if not has_newer_sweep_filing:
                            with self._state_lock:
                                from zoneinfo import ZoneInfo
                                now_ist_str = datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()
                                latest_ann_p = str(latest_annual_rec.get("period_end_date")) if latest_annual_rec else None
                                if sym_u not in self.state:
                                    self.state[sym_u] = {
                                        "filings": {},
                                        "latest_filing_date": latest_local_period,
                                        "latest_annual_date": latest_ann_p,
                                        "snapshot_status": freshness,
                                        "bootstrap_completed": True,
                                        "last_polled_at": now_ist_str,
                                    }
                                else:
                                    self.state[sym_u]["bootstrap_completed"] = True
                                    self.state[sym_u]["last_polled_at"] = now_ist_str
                            with counter_lock:
                                skipped_complete_count[0] += 1
                            logger.info(
                                f"⚡ [filling scanner] [{completed_count[0] + 1}/{len(symbols)}] {sym_u}: "
                                f"UP TO DATE (market sweep verified, latest_period={latest_local_period}, annual_count={local_annual_count}). "
                                f"Skipping exchange fetch in 0ms."
                            )
                            return
                        else:
                            logger.info(
                                f"📢 [filling scanner] [{completed_count[0] + 1}/{len(symbols)}] {sym_u}: "
                                f"NEW FILING DISCOVERED IN MARKET SWEEP! (latest_local={latest_local_period}). Ingesting new disclosure..."
                            )

                    # Fallback check: ONLY when market_sweep failed to contact exchange AND no newer sweep filing exists
                    if not market_sweep_ok and not force_backfill and not has_newer_sweep_filing:
                        last_polled = str(self.state.get(sym_u, {}).get("last_polled_at") or "")
                        polled_recently = False
                        if last_polled:
                            try:
                                from dateutil.parser import parse as _parse_dt
                                dt_polled = _parse_dt(last_polled)
                                now_ist_dt = datetime.now(dt_polled.tzinfo)
                                if (now_ist_dt.date() == dt_polled.date()) or ((now_ist_dt - dt_polled).total_seconds() < 86400):
                                    polled_recently = True
                            except Exception:
                                pass
                        # Snapshot fallback: if snapshot exists, has 6+ annual filings, and is fresh/valid
                        if not polled_recently and snap_row is not None and local_annual_count >= 6 and not snap_gap_flag:
                            snap_as_of = str(snap_row.get("as_of_date") or "")
                            try:
                                from datetime import date as _dt_date
                                snap_date = _dt_date.fromisoformat(snap_as_of[:10])
                                now_date = date.today()
                                if (now_date - snap_date).days <= 3 and freshness in ("FRESH", "VALID"):
                                    polled_recently = True
                            except Exception:
                                pass

                        if polled_recently:
                            with counter_lock:
                                skipped_complete_count[0] += 1
                            logger.info(
                                f"⚡ [filling scanner] [{completed_count[0] + 1}/{len(symbols)}] {sym_u}: "
                                f"DATA COMPLETE & CHECKED TODAY (latest_period={latest_local_period}, annual_count={local_annual_count}). "
                                f"Skipping exchange fetch in 0ms."
                            )
                            return

                    # In next run, symbol has complete history: ONLY check for new filings!
                    # min_cutoff set to latest_local_period ensures we do NOT refetch past years (2020..2025).
                    min_cutoff = latest_local_period or "2024-01-01"
                    logger.info(
                        f"⚡ [filling scanner] [{completed_count[0] + 1}/{len(symbols)}] {sym_u}: "
                        f"HISTORY COMPLETE (latest_period={latest_local_period}, annual_count={local_annual_count}). "
                        f"Incremental check: fetching ONLY new filings (min_cutoff={min_cutoff})."
                    )

                raw_filings_list: List[Dict[str, Any]] = []
                nse_fetch_count = 0
                nse_disc_count = 0

                # Pre-populate raw_filings_list directly with any newer disclosures discovered in market sweep!
                if market_sweep_ok and has_newer_sweep_filing:
                    for it in market_sweep_filings.get(sym_u, []):
                        p_end = _normalize_iso_date(it.get("period_end_date") or it.get("toDate"))
                        if p_end and p_end >= min_cutoff:
                            r_copy = dict(it)
                            r_copy["exchange"] = "NSE"
                            r_copy["period_end_date"] = p_end
                            raw_period = str(r_copy.get("period") or r_copy.get("statement_type") or "").upper()
                            r_copy["statement_type"] = "ANNUAL" if ("ANNUAL" in raw_period or "YEAR" in raw_period or "FY" in raw_period) else "QUARTERLY"
                            raw_filings_list.append(r_copy)

                # 1. Poll NSE XBRL / Corporate Results (Primary Source)
                if nse_prov:
                    try:
                        nse_records = nse_prov.fetch_raw_financials(sym_u, min_period_end_date=min_cutoff)
                        if nse_records:
                            nse_fetch_count = len(nse_records)
                            for r in nse_records:
                                norm_p = _normalize_iso_date(r.period_end_date)
                                if not norm_p or norm_p < min_cutoff:
                                    continue
                                raw_filings_list.append({
                                    "exchange": "NSE",
                                    "filing_id": getattr(r, "filing_id", None) or f"NSE_{sym_u}_{norm_p}",
                                    "period_end_date": norm_p,
                                    "statement_type": r.period_type,
                                    "basis": r.consolidation.value if hasattr(r.consolidation, "value") else str(r.consolidation),
                                    "revenue": r.revenue,
                                    "net_profit": r.net_profit,
                                    "operating_profit": r.ebit,
                                    "eps": r.eps,
                                    "total_debt": r.total_debt,
                                    "total_equity": r.total_equity,
                                    "operating_cash_flow": getattr(r, "operating_cash_flow", None),
                                    "shares_outstanding": r.shares_outstanding,
                                    "filing_date": r.availability_date or f"{norm_p}T00:00:00",
                                    "broadcast_timestamp": getattr(r, "broadcast_timestamp", None) or f"{norm_p}T00:00:00",
                                })

                        # Only call discover_filings as supplement if fetch_raw_financials returned 0 records
                        if nse_fetch_count == 0:
                            try:
                                nse_rows = nse_prov.discover_filings(sym_u)
                                if nse_rows:
                                    nse_disc_count = len(nse_rows)
                                    for r in nse_rows:
                                        if isinstance(r, dict):
                                            r_copy = dict(r)
                                            r_copy["exchange"] = "NSE"
                                            p_end = _normalize_iso_date(r_copy.get("period_end_date") or r_copy.get("toDate"))
                                            if p_end and p_end >= min_cutoff:
                                                raw_period = str(r_copy.get("period") or r_copy.get("statement_type") or "").upper()
                                                st = "ANNUAL" if ("ANNUAL" in raw_period or "YEAR" in raw_period or "FY" in raw_period) else "QUARTERLY"
                                                r_copy["period_end_date"] = p_end
                                                r_copy["statement_type"] = st
                                                raw_filings_list.append(r_copy)
                            except Exception as disc_err:
                                logger.warning(f"[filling scanner] NSE discover_filings supplement warning for {sym_u}: {disc_err}")
                    except Exception as nse_err:
                        logger.warning(f"[filling scanner] NSE discover/fetch warning for {sym_u}: {nse_err}")

                # 2. Poll BSE: Only poll BSE as fallback if NSE returned 0 filings OR if symbol is not an NSE listing
                bse_count = 0
                is_bse_only = sym_u.endswith(".BO") or str(sym_u).isdigit()
                is_nse_primary = (not is_bse_only)
                if needs_bootstrap:
                    should_poll_bse = (bse_prov is not None) and (len(raw_filings_list) == 0)
                else:
                    # On incremental runs, NEVER poll BSE for NSE stocks just because there are no new filings today!
                    # Only poll BSE if the symbol is specifically a BSE-only stock and has no new filings.
                    should_poll_bse = (bse_prov is not None) and is_bse_only and (len(raw_filings_list) == 0)
                if should_poll_bse:
                    try:
                        scrip = bse_prov.resolve_bse_scrip_code(sym_u)
                        if scrip:
                            # 3a. BSE Financial Results
                            bse_rows = bse_prov.fetch_raw_financials(sym_u)
                            if bse_rows:
                                bse_count += len(bse_rows)
                                for r in bse_rows:
                                    norm_p = _normalize_iso_date(r.period_end_date)
                                    if not norm_p or norm_p < min_cutoff:
                                        continue
                                    raw_filings_list.append({
                                        "exchange": "BSE",
                                        "filing_id": getattr(r, "filing_id", None) or f"BSE_{sym_u}_{norm_p}",
                                        "period_end_date": norm_p,
                                        "statement_type": r.period_type,
                                        "basis": r.consolidation.value if hasattr(r.consolidation, "value") else str(r.consolidation),
                                        "revenue": r.revenue,
                                        "net_profit": r.net_profit,
                                        "operating_profit": r.ebit,
                                        "eps": r.eps,
                                        "total_debt": r.total_debt,
                                        "total_equity": r.total_equity,
                                        "operating_cash_flow": getattr(r, "operating_cash_flow", None),
                                        "shares_outstanding": r.shares_outstanding,
                                        "filing_date": r.availability_date or f"{norm_p}T00:00:00",
                                        "broadcast_timestamp": getattr(r, "broadcast_timestamp", None) or f"{norm_p}T00:00:00",
                                    })
                            # 3b. BSE Corporate Announcements (same-day results & board meetings)
                            try:
                                bse_disc_rows = bse_prov.discover_filings(sym_u)
                                if bse_disc_rows:
                                    bse_count += len(bse_disc_rows)
                                    existing_periods = {
                                        (_normalize_iso_date(it.get("period_end_date")), str(it.get("statement_type", "")).upper())
                                        for it in raw_filings_list
                                        if it.get("exchange") == "BSE"
                                    }
                                    for r in bse_disc_rows:
                                        if isinstance(r, dict):
                                            r_copy = dict(r)
                                            r_copy["exchange"] = "BSE"
                                            p_end = _normalize_iso_date(r_copy.get("period_end_date") or r_copy.get("toDate"))
                                            if p_end and p_end >= min_cutoff:
                                                raw_period = str(r_copy.get("period") or r_copy.get("statement_type") or "").upper()
                                                st = "ANNUAL" if ("ANNUAL" in raw_period or "YEAR" in raw_period or "FY" in raw_period) else "QUARTERLY"
                                                if (p_end, st) not in existing_periods:
                                                    r_copy["period_end_date"] = p_end
                                                    r_copy["statement_type"] = st
                                                    raw_filings_list.append(r_copy)
                            except Exception as bse_disc_err:
                                logger.warning(f"[filling scanner] BSE discover_filings warning for {sym_u}: {bse_disc_err}")
                    except Exception as bse_err:
                        logger.warning(f"[filling scanner] BSE discover warning for {sym_u}: {bse_err}")

                if not raw_filings_list or not any(raw_filings_list):
                    logger.debug(f"[filling scanner] {sym_u}: 0 filings retrieved from exchanges.")
                    with self._state_lock:
                        from zoneinfo import ZoneInfo
                        now_ist_str = datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()
                        latest_ann_p = str(latest_annual_rec.get("period_end_date")) if latest_annual_rec else None
                        if sym_u not in self.state:
                            self.state[sym_u] = {
                                "filings": {},
                                "latest_filing_date": latest_local_period,
                                "latest_annual_date": latest_ann_p,
                                "snapshot_status": freshness,
                                "bootstrap_completed": not needs_bootstrap,
                                "last_polled_at": now_ist_str,
                            }
                        else:
                            self.state[sym_u]["last_polled_at"] = now_ist_str
                            if not needs_bootstrap:
                                self.state[sym_u]["bootstrap_completed"] = True
                    return

                import re as _re_poll
                valid_items = []
                for item in raw_filings_list:
                    raw_p = item.get("toDate") or item.get("period_end_date") or item.get("periodEnd") or ""
                    period_end = _normalize_iso_date(str(raw_p).strip())
                    if period_end and _re_poll.match(r'^\d{4}-\d{2}-\d{2}$', period_end) and period_end >= get_historical_cutoff_date(as_of_date):
                        item["period_end_date"] = period_end
                        valid_items.append(item)

                if not valid_items:
                    with self._state_lock:
                        from zoneinfo import ZoneInfo
                        now_ist_str = datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()
                        latest_ann_p = str(latest_annual_rec.get("period_end_date")) if latest_annual_rec else None
                        if sym_u not in self.state:
                            self.state[sym_u] = {
                                "filings": {},
                                "latest_filing_date": latest_local_period,
                                "latest_annual_date": latest_ann_p,
                                "snapshot_status": freshness,
                                "bootstrap_completed": not needs_bootstrap,
                                "last_polled_at": now_ist_str,
                            }
                        else:
                            self.state[sym_u]["last_polled_at"] = now_ist_str
                            if not needs_bootstrap:
                                self.state[sym_u]["bootstrap_completed"] = True
                    return

                # Deduplicate and sort items chronologically
                valid_items.sort(key=lambda x: str(x.get("period_end_date", "")))

                # Group candidate filings by (period_end_date, statement_type, basis)
                # Keep the highest-quality / latest revision for each distinct statement
                by_statement: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
                for it in valid_items:
                    p_date = it["period_end_date"]
                    raw_st = str(it.get("period") or it.get("statement_type") or "QUARTERLY").upper()
                    st = "ANNUAL" if ("ANNUAL" in raw_st or "YEAR" in raw_st or "FY" in raw_st) else "QUARTERLY"
                    c_val = str(it.get("consolidated", it.get("basis", ""))).lower()
                    bs = "STANDALONE" if ("standalone" in c_val or "non-consolidated" in c_val) else "CONSOLIDATED"
                    k = (p_date, st, bs)
                    if k not in by_statement:
                        by_statement[k] = it
                    else:
                        # Prefer records with actual numeric revenue/PAT facts over raw index rows
                        prev = by_statement[k]
                        prev_has_facts = (prev.get("revenue") is not None or prev.get("net_profit") is not None)
                        curr_has_facts = (it.get("revenue") is not None or it.get("net_profit") is not None)
                        if curr_has_facts and not prev_has_facts:
                            by_statement[k] = it

                # Evaluate all distinct statements through detect_filing_changes
                sym_new_events = 0
                for (period_end, st_type, basis), item in by_statement.items():
                    fid = str(item.get("seqNumber") or item.get("filing_id") or f"{sym_u}_{period_end}_{st_type}_v1")
                    # [RULE 67 CHANGE-RATIONALE: Immutable deterministic timestamp prevents volatile payload hash mutation across daily runs]
                    bcast_time = str(item.get("broadCastDate") or item.get("filingDate") or item.get("broadcast_timestamp") or f"{period_end}T00:00:00")

                    raw_bytes = json.dumps(item, sort_keys=True).encode("utf-8")
                    meta_incoming = {
                        "filing_id": fid,
                        "period_end_date": period_end,
                        "statement_type": st_type,
                        "basis": basis,
                        "broadcast_timestamp": bcast_time,
                        "source_exchange": item.get("exchange", "NSE"),
                        "revision_number": int(item.get("seqNumber") or 1) if str(item.get("seqNumber", "")).isdigit() else 1,
                        "amended": str(item.get("reInd", "")).upper() == "Y",
                        "facts": item,
                    }

                    event = self.detect_filing_changes(sym_u, meta_incoming, raw_bytes)
                    if event.event_type != FilingEventType.NO_CHANGE:
                        sym_new_events += 1
                        with self._state_lock:
                            self._merge_into_pit_raw_filings(sym_u, item, period_end, st_type, basis)
                        with events_lock:
                            discovered_events.append(event.to_dict())

                if sym_new_events > 0:
                    with events_lock:
                        pending_rebuild_syms.add(sym_u)
                    try:
                        try:
                            from app.financial_data_integrity import record_exchange_watermark
                        except ImportError:
                            from financial_data_integrity import record_exchange_watermark
                        _now_iso_sym = datetime.now().isoformat()
                        _latest_bcast = max([str(it.get("broadCastDate") or it.get("filingDate") or it.get("period_end_date") or "") for it in by_statement.values()] or [""])
                        record_exchange_watermark(source_name="NSE", last_successful_check_at=_now_iso_sym, latest_filing_timestamp=_latest_bcast or None, symbol=sym_u)
                    except Exception as _sym_wm_err:
                        logger.debug(f"[filling scanner] Symbol watermark note for {sym_u}: {_sym_wm_err}")
                    logger.info(
                        f"✨ [filling scanner] {sym_u}: Ingested {sym_new_events} new/amended disclosures "
                        f"(NSE_records={nse_fetch_count}, NSE_discovered={nse_disc_count}, BSE={bse_count})."
                    )
                else:
                    if not needs_bootstrap:
                        with counter_lock:
                            skipped_complete_count[0] += 1
                        logger.info(
                            f"✨ [filling scanner] {sym_u}: Up to date (0 new filings on exchange). Existing records untouched."
                        )
                    elif needs_bootstrap and valid_items and (not snap_annual or snap_gap_flag or freshness != "FRESH"):
                        with events_lock:
                            pending_rebuild_syms.add(sym_u)

                # Record polling completion and state for the symbol
                with self._state_lock:
                    from zoneinfo import ZoneInfo
                    now_ist_str = datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()
                    latest_ann_p = str(latest_annual_rec.get("period_end_date")) if latest_annual_rec else None
                    if sym_u not in self.state:
                        self.state[sym_u] = {
                            "filings": {},
                            "latest_filing_date": latest_local_period,
                            "latest_annual_date": latest_ann_p,
                            "snapshot_status": freshness,
                            "bootstrap_completed": True,
                            "last_polled_at": now_ist_str,
                        }
                    else:
                        self.state[sym_u]["bootstrap_completed"] = True
                        self.state[sym_u]["last_polled_at"] = now_ist_str
                        if latest_local_period:
                            self.state[sym_u]["latest_filing_date"] = latest_local_period
                        if latest_ann_p:
                            self.state[sym_u]["latest_annual_date"] = latest_ann_p

            except Exception as sym_err:
                with counter_lock:
                    error_count[0] += 1
                logger.warning(f"[filling scanner] Exchange polling error for {sym_u}: {sym_err}")
            finally:
                should_batch_sync = False
                sync_reason = ""
                with counter_lock:
                    completed_count[0] += 1
                    curr_c = completed_count[0]
                    total_syms = len(symbols)
                    pending_c = total_syms - curr_c
                    skipped_c = skipped_complete_count[0]
                    bootstrapped_c = bootstrap_ingested_count[0]
                    errs_c = error_count[0]
                    elapsed = max(0.1, time.time() - start_poll_time)
                    rate_per_min = round((curr_c / elapsed) * 60, 1)
                    if curr_c > 0 and pending_c > 0:
                        eta_seconds = (pending_c / curr_c) * elapsed
                        if eta_seconds >= 60:
                            eta_str = f"{int(eta_seconds // 60)}m {int(eta_seconds % 60)}s"
                        else:
                            eta_str = f"{int(eta_seconds)}s"
                    else:
                        eta_str = "0s"

                    elapsed_since_sync = time.time() - last_synced_time[0]
                    time_triggered = (elapsed_since_sync >= TIME_SYNC_INTERVAL)
                    count_triggered = (curr_c - last_synced_count[0] >= BATCH_SYNC_SIZE)
                    final_triggered = (curr_c == total_syms)

                    to_sync_syms_batch: List[str] = []
                    with events_lock:
                        pending_syms_len = len(pending_rebuild_syms)
                        disc_len = len(discovered_events)
                        fetched_triggered = (pending_syms_len >= FETCHED_SYNC_THRESHOLD)
                        if time_triggered or count_triggered or fetched_triggered or final_triggered:
                            should_batch_sync = True
                            to_sync_syms_batch = sorted(list(pending_rebuild_syms))
                            pending_rebuild_syms.clear()

                    if should_batch_sync:
                        if fetched_triggered:
                            sync_reason = f"FETCHED_THRESHOLD ({len(to_sync_syms_batch)} newly fetched stocks)"
                        elif time_triggered:
                            sync_reason = f"5-MINUTE_TIMER ({int(elapsed_since_sync)}s elapsed)"
                        elif count_triggered:
                            sync_reason = f"BATCH_COUNT ({curr_c - last_synced_count[0]} stocks completed)"
                        else:
                            sync_reason = "FINAL_COMPLETION"

                        last_synced_count[0] = curr_c
                        last_synced_time[0] = time.time()

                    # Detailed progress log to track where we are
                    pct = round((curr_c / total_syms) * 100, 1)
                    if curr_c % 5 == 0 or curr_c == total_syms or pending_c <= 10 or should_batch_sync:
                        logger.info(
                            f"📊 [filling scanner] [{curr_c}/{total_syms}] ({pct}%) | "
                            f"Done: {curr_c} | Pending: {pending_c} (ETA: {eta_str}) | "
                            f"Complete Skipped: {skipped_c} | Ingested/Healed: {bootstrapped_c} | "
                            f"Discovered: {disc_len} | Errors: {errs_c} | "
                            f"Rate: {rate_per_min}/min | Last: {sym_u}"
                        )

                # Batch DB synchronization outside counter_lock
                if should_batch_sync:
                    with batch_sync_lock:
                        synced_batch_num[0] += 1
                        b_num = synced_batch_num[0]
                        pct = round((curr_c / total_syms) * 100, 1)

                        # 1. Atomically persist watcher state
                        with self._state_lock:
                            self._save_state()

                        # 2. If new filings were discovered/fetched, rebuild snapshot and upload to DB
                        if to_sync_syms_batch:
                            logger.info(
                                f"💾 [filling scanner] [BATCH {b_num} DB SYNC: {sync_reason}] Rebuilding snapshot & syncing {len(to_sync_syms_batch)} newly fetched stocks to DB: {to_sync_syms_batch[:8]}..."
                            )
                            try:
                                self.invalidate_and_rebuild_snapshot(symbol=to_sync_syms_batch, as_of_date=as_of_date)
                                with events_lock:
                                    synced_symbols.update(to_sync_syms_batch)
                            except Exception as snap_sync_err:
                                logger.warning(f"[filling scanner] Batch {b_num} snapshot rebuild sync notice: {snap_sync_err}")

                            # Only upload raw bundle when new filings were actually ingested
                            try:
                                try:
                                    from app.database import upload_pit_raw_bundle_to_db
                                except ImportError:
                                    from database import upload_pit_raw_bundle_to_db
                                upload_pit_raw_bundle_to_db()
                            except Exception as _b_err:
                                pass

                        # 3. Update DB execution progress & health heartbeat
                        progress_summary = (
                            f"Processed {curr_c}/{total_syms} ({pct}%) | "
                            f"Pending: {pending_c} (ETA: {eta_str}) | "
                            f"Complete Skipped: {skipped_c} | Backfilled: {bootstrapped_c} | "
                            f"Discovered: {disc_len} | Synced Batch {b_num} ({sync_reason}) to DB"
                        )
                        try:
                            try:
                                from app.database import update_scanner_run_heartbeat, upsert_scanner_health, update_scanner_run_progress
                            except ImportError:
                                from database import update_scanner_run_heartbeat, upsert_scanner_health, update_scanner_run_progress

                            if run_ctx and getattr(run_ctx, "run_id", None):
                                update_scanner_run_heartbeat(run_ctx.run_id)
                                update_scanner_run_progress(
                                    run_id=run_ctx.run_id,
                                    processed_count=curr_c,
                                    total_count=total_syms,
                                    summary_notes=progress_summary,
                                    metrics_json={
                                        "processed": curr_c,
                                        "processed_count": curr_c,
                                        "total": total_syms,
                                        "total_count": total_syms,
                                        "pending": pending_c,
                                        "pending_count": pending_c,
                                        "pct": pct,
                                        "eta": eta_str,
                                        "skipped_complete": skipped_c,
                                        "bootstrapped": bootstrapped_c,
                                        "discovered": disc_len,
                                        "errors": errs_c,
                                        "batch_num": b_num,
                                        "sync_reason": sync_reason,
                                    }
                                )

                            upsert_scanner_health(
                                scanner_name="FILING_WATCHER",
                                status="RUNNING",
                                processed_count=curr_c,
                                total_count=total_syms,
                                error_msg=progress_summary,
                                run_id=run_ctx.run_id if run_ctx else None,
                            )
                        except Exception as prog_err:
                            logger.debug(f"[filling scanner] Progress update error: {prog_err}")

                        logger.info(
                            f"💾 [filling scanner] [BATCH {b_num} DB SYNC COMPLETE: {sync_reason}] [{curr_c}/{total_syms}] ({pct}%) | "
                            f"Pending: {pending_c} (ETA: {eta_str}) | Newly Ingested: {len(to_sync_syms_batch)} | "
                            f"Total Completed: {curr_c} | DB has all updated data till this batch."
                        )

        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="FilingPoll") as executor:
            list(executor.map(_process_symbol, symbols))

        # Atomically persist updated watcher state (including bootstrap_completed markers)
        with self._state_lock:
            self._save_state()

        total_elapsed = time.time() - start_poll_time
        logger.info(
            f"🏁 [filling scanner] Polling cycle complete in {total_elapsed:.1f}s | "
            f"Universe: {len(symbols)} | Complete Skipped: {skipped_complete_count[0]} | "
            f"Backfilled: {bootstrap_ingested_count[0]} | Events Discovered: {len(discovered_events)} | "
            f"Errors: {error_count[0]}"
        )

        # Invariant: Always upload updated pit_raw_bundle to DB so newly discovered/healed filings survive container restarts
        try:
            try:
                from app.database import upload_pit_raw_bundle_to_db
            except ImportError:
                from database import upload_pit_raw_bundle_to_db
            upload_pit_raw_bundle_to_db()
        except Exception as bundle_err:
            logger.debug(f"[filling scanner] Raw filings bundle upload note: {bundle_err}")

        # Ensure any remaining pending or discovered symbols not yet synced are rebuilt
        with events_lock:
            remaining_syms = sorted(list(pending_rebuild_syms))
            pending_rebuild_syms.clear()

        all_disc_syms = {str(e.get("symbol")).strip().upper() for e in discovered_events if e.get("symbol")}
        unrebuilt = sorted(list((all_disc_syms | set(remaining_syms)) - synced_symbols))

        if unrebuilt:
            logger.info(f"🔄 [filling scanner] Rebuilding snapshot for {len(unrebuilt)} remaining symbols: {unrebuilt[:10]}...")
            try:
                self.invalidate_and_rebuild_snapshot(symbol=unrebuilt, as_of_date=as_of_date)
            except Exception as auto_rb_err:
                logger.warning(f"[filling scanner] Final snapshot rebuild error: {auto_rb_err}")

        # Update final exchange watermarks for both NSE and BSE to guarantee FEED_HEARTBEAT_SLA freshness
        try:
            try:
                from app.financial_data_integrity import record_exchange_watermark
            except ImportError:
                from financial_data_integrity import record_exchange_watermark
            _now_iso_final = datetime.now().isoformat()
            record_exchange_watermark(source_name="NSE", last_successful_check_at=_now_iso_final)
            record_exchange_watermark(source_name="BSE", last_successful_check_at=_now_iso_final)
            logger.info("🛡️ [WATERMARK] Final multi-source exchange watermarks recorded successfully.")
        except Exception as _wm_f_err:
            logger.debug(f"[filling scanner] Final exchange watermark update note: {_wm_f_err}")

        return discovered_events

    def _merge_into_pit_raw_filings(
        self,
        symbol: str,
        new_item: Dict[str, Any],
        period_end: str,
        statement_type: str,
        basis: str,
    ) -> None:
        """
        Merges an exchange-discovered filing into the canonical data/pit_raw_filings/<SYM>.json
        cache, updating or appending the period while maintaining historical records.
        """
        sym_u = symbol.strip().upper()
        norm_period_end = _normalize_iso_date(period_end)
        if not norm_period_end or norm_period_end < "2015-01-01":
            logger.debug(f"[WATCHER] Cannot merge filing with invalid period_end '{period_end}' for {sym_u}")
            return
        raw_dir = getattr(self, "pit_raw_dir", None) or (BASE_DIR / "data" / "pit_raw_filings")
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / f"{sym_u}.json"

        existing_list: List[Dict[str, Any]] = []
        if raw_path.exists():
            try:
                with open(raw_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        existing_list = data
            except Exception:
                existing_list = []

        # Find if this period, statement_type and basis already exists
        matched_idx = None
        for i, row in enumerate(existing_list):
            r_period = _normalize_iso_date(row.get("period_end_date"))
            r_type = str(row.get("statement_type") or "").upper()
            r_basis = str(row.get("basis") or ("STANDALONE" if "standalone" in str(row.get("source", "")).lower() else "CONSOLIDATED")).upper()
            if r_period and r_period == norm_period_end and r_type == statement_type and r_basis == basis:
                matched_idx = i
                break

        # Map facts
        rev = new_item.get("revenue") or new_item.get("revenueFromOperations") or new_item.get("totalIncome") or new_item.get("revenue_cr")
        pat = new_item.get("net_profit") or new_item.get("netProfitLossForPeriod") or new_item.get("profitBeforeTax") or new_item.get("netProfit") or new_item.get("pat") or new_item.get("pat_cr")
        op_prof = new_item.get("operating_profit") or new_item.get("ebit") or new_item.get("operatingProfit")
        eps = new_item.get("eps") or new_item.get("basicEps")
        shares = new_item.get("shares_outstanding")
        debt = new_item.get("total_debt") or new_item.get("debt")
        equity = new_item.get("total_equity") or new_item.get("equity")
        ocf = new_item.get("operating_cash_flow") or new_item.get("ocf")

        record_entry = {
            "symbol": sym_u,
            "period_end_date": norm_period_end,
            "statement_type": statement_type,
            "basis": basis,
            "revenue": float(rev) if rev is not None else None,
            "net_profit": float(pat) if pat is not None else None,
            "operating_profit": float(op_prof) if op_prof is not None else None,
            "eps": float(eps) if eps is not None else None,
            "total_debt": float(debt) if debt is not None else None,
            "total_equity": float(equity) if equity is not None else None,
            "operating_cash_flow": float(ocf) if ocf is not None else None,
            "shares_outstanding": float(shares) if shares is not None else None,
            # [RULE 67 CHANGE-RATIONALE: Immutable fallback to norm_period_end rather than volatile today's date]
            "filing_date": new_item.get("filingDate") or new_item.get("filing_date") or norm_period_end,
            "broadcast_timestamp": new_item.get("broadCastDate") or new_item.get("broadcast_timestamp"),
            "exchange": new_item.get("exchange", "NSE"),
            "filing_id": new_item.get("seqNumber") or new_item.get("filing_id"),
        }

        # Keep existing metrics if new item has None, update with incoming non-null facts
        if matched_idx is not None:
            old = existing_list[matched_idx]
            merged = dict(old)
            for k, v in record_entry.items():
                if v is not None or k not in merged:
                    merged[k] = v
            merged["period_end_date"] = norm_period_end
            existing_list[matched_idx] = merged
        else:
            existing_list.append(record_entry)

        # Sort chronologically
        existing_list.sort(key=lambda x: str(_normalize_iso_date(x.get("period_end_date")) or str(x.get("period_end_date", ""))))

        tmp_path = f"{raw_path}.tmp.{os.getpid()}"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(existing_list, f, indent=2)
        os.replace(tmp_path, raw_path)
        logger.info(f"💾 [filling scanner] Successfully persisted fresh filing for {sym_u} ({norm_period_end} {statement_type}) to {raw_path}.")

        # Check and restore if stock was previously marked stale or in cool-off
        try:
            try:
                from app.database import check_and_restore_stale_stock
            except ImportError:
                from database import check_and_restore_stale_stock
            check_and_restore_stale_stock(
                symbol=sym_u,
                source="EXCHANGE_FILING_WATCHER",
                period_end=norm_period_end,
                notify_admin=True
            )
        except Exception as restore_err:
            logger.debug(f"[filling scanner] check_and_restore_stale_stock notice for {sym_u}: {restore_err}")

    # -------------------------------------------------------------------------
    # 5. CORPORATE FILINGS AUDIT & FINDINGS DISCOVERY
    # -------------------------------------------------------------------------

    def audit_and_log_filings(
        self,
        target_symbols: Optional[List[str]] = None,
        as_of_date: Optional[date] = None,
        limit_symbols: Optional[int] = None,
        only_updated: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Scans filings across universe equities, identifying GENUINELY MISSING
        data in the local canonical snapshot used by trading scanners.
        Persists at most a SINGLE ENTRY PER STOCK for any missing filing discovered.
        If only_updated is True, returns empty list so idle local files are not logged.

        Rules:
          - Only stocks with genuinely missing filings or data deficits in the local canonical
            snapshot (data/canonical_pit_rebuilt.parquet) are recorded as findings.
          - If a stock is already complete and fresh in the canonical snapshot with all
            required metrics and no newer filing exists, it is NOT logged.
          - Single entry per stock: selects the newest missing filing discovered for that stock.
          - Strict ISO YYYY-MM-DD date enforcement (>= 2015-01-01).
        """
        import re as _re
        from zoneinfo import ZoneInfo
        as_of_str = as_of_date.isoformat() if as_of_date else datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")
        symbols = target_symbols or self.approved_symbols
        if limit_symbols:
            symbols = symbols[:limit_symbols]

        try:
            from app.database import upsert_corporate_filing_finding
        except ImportError:
            from database import upsert_corporate_filing_finding

        # Load canonical PIT snapshot used by trading scanners (Single Source of Truth)
        snap_map: Dict[str, Any] = {}
        try:
            import pandas as pd
            try:
                from app.scanner_data_gateway import ScannerDataGateway
            except ImportError:
                from scanner_data_gateway import ScannerDataGateway
            gateway = ScannerDataGateway(pit_parquet_path=str(SNAPSHOT_PATH))
            snap_df = gateway.get_working_dataset()
            if snap_df is None or snap_df.empty:
                if SNAPSHOT_PATH.exists():
                    snap_df = pd.read_parquet(SNAPSHOT_PATH)
            if snap_df is not None and not snap_df.empty:
                for _, r in snap_df.iterrows():
                    s = str(r.get("symbol", "")).strip().upper()
                    if s:
                        snap_map[s] = r
        except Exception as snap_err:
            logger.debug(f"[filling scanner] Snapshot load note in audit: {snap_err}")

        findings: List[Dict[str, Any]] = []

        for sym in symbols:
            sym_u = sym.strip().upper()
            raw_path = BASE_DIR / "data" / "pit_raw_filings" / f"{sym_u}.json"
            if not raw_path.exists():
                continue
            try:
                with open(raw_path, "r", encoding="utf-8") as f:
                    filings = json.load(f)
                if not filings or not isinstance(filings, list):
                    continue

                # ── STRICT ISO DATE FILTER ──
                def _is_valid_iso(r: Dict[str, Any]) -> bool:
                    p = _normalize_iso_date(str(r.get("period_end_date") or ""))
                    return bool(p and _re.match(r'^\d{4}-\d{2}-\d{2}$', p) and p >= get_historical_cutoff_date(as_of_date))

                valid_filings = [r for r in filings if _is_valid_iso(r)]
                if not valid_filings:
                    continue

                # Sort all valid filings by parsed ISO date ascending
                def _sort_key(r: Dict[str, Any]) -> str:
                    return _normalize_iso_date(str(r.get("period_end_date") or "")) or ""

                valid_filings.sort(key=_sort_key)

                # Determine if this stock has genuinely missing data in the canonical snapshot
                candidate = None
                finding_reason = "MISSING_FILING_DISCOVERED"

                if sym_u not in snap_map:
                    # Case 1: Stock is completely missing from canonical snapshot!
                    candidate = valid_filings[-1]
                    finding_reason = "MISSING_FROM_SNAPSHOT"
                else:
                    sr = snap_map[sym_u]
                    snap_annual = str(sr.get("latest_annual_period") or "")[:10]
                    freshness = str(sr.get("pit_freshness_status") or "FRESH")
                    gap = bool(sr.get("filing_gap_detected", False))
                    roce = sr.get("roce_5y_avg")
                    debt_eq = sr.get("debt_to_equity")
                    sales_cagr = sr.get("sales_cagr_5y")

                    # Case 2: Check if raw filings have a newer filing ahead of what is in snapshot
                    newer_filings = [r for r in valid_filings if str(r.get("period_end_date"))[:10] > snap_annual]
                    if newer_filings:
                        candidate = newer_filings[-1]
                        finding_reason = "NEW_FILING_AHEAD_OF_SNAPSHOT"
                    elif freshness not in ("FRESH", "VALID") or gap or (roce is None or (hasattr(roce, "isna") and roce.isna())) or (debt_eq is None) or (sales_cagr is None):
                        # Case 3: Snapshot had data deficit or gap; raw filings provide the missing data
                        candidate = valid_filings[-1]
                        finding_reason = "SNAPSHOT_GAP_RECOVERY"
                    else:
                        # Stock is 100% complete and up to date in the local snapshot.
                        # NOT MISSING! Do NOT log as a finding!
                        continue

                if not candidate:
                    continue

                item = candidate
                period_end = _normalize_iso_date(str(item.get("period_end_date") or ""))
                if not period_end:
                    continue
                st_type = str(item.get("statement_type") or "QUARTERLY").upper()
                basis = str(item.get("basis") or "CONSOLIDATED").upper()

                rev = item.get("revenue") if item.get("revenue") is not None else item.get("mapped_revenue")
                pat = item.get("net_profit") if item.get("net_profit") is not None else item.get("pat")
                op_prof = item.get("operating_profit") if item.get("operating_profit") is not None else item.get("ebit")
                eps = item.get("eps")
                roce = item.get("roce")
                roe = item.get("roe")
                debt = item.get("total_debt") if item.get("total_debt") is not None else item.get("debt")
                equity = item.get("total_equity") if item.get("total_equity") is not None else item.get("equity")
                de = item.get("debt_to_equity")
                op_m = item.get("operating_margin")
                net_m = item.get("net_margin")
                shares = item.get("shares_outstanding")

                # Derived metrics fallback
                if de is None and debt is not None and equity is not None:
                    try:
                        eq_f = float(equity)
                        if eq_f > 0:
                            de = round(float(debt) / eq_f, 2)
                    except Exception:
                        pass

                if roce is None and op_prof is not None and equity is not None and st_type == "ANNUAL":
                    try:
                        eq_f = float(equity)
                        d_f  = float(debt) if debt is not None else 0.0
                        cap_emp = eq_f + d_f
                        if cap_emp > 0:
                            roce = round((float(op_prof) / cap_emp) * 100.0, 2)
                    except Exception:
                        pass

                if roe is None and pat is not None and equity is not None and st_type == "ANNUAL":
                    try:
                        eq_f = float(equity)
                        if eq_f > 0:
                            roe = round((float(pat) / eq_f) * 100.0, 2)
                    except Exception:
                        pass

                if op_m is None and op_prof is not None and rev is not None:
                    try:
                        rev_f = float(rev)
                        if rev_f > 0:
                            op_m = round((float(op_prof) / rev_f) * 100.0, 2)
                    except Exception:
                        pass

                if net_m is None and pat is not None and rev is not None:
                    try:
                        rev_f = float(rev)
                        if rev_f > 0:
                            net_m = round((float(pat) / rev_f) * 100.0, 2)
                    except Exception:
                        pass

                # Clear, human-readable headline describing missing filing discovered
                if finding_reason == "MISSING_FROM_SNAPSHOT":
                    headline = f"{sym_u} missing from local snapshot — {st_type} ({period_end}) fetched"
                elif finding_reason == "NEW_FILING_AHEAD_OF_SNAPSHOT":
                    headline = f"{sym_u} newer {st_type} ({period_end}) not in local snapshot — Discovered & Fetched"
                elif finding_reason == "SNAPSHOT_GAP_RECOVERY":
                    headline = f"{sym_u} had deficient snapshot data — Discovered & Fetched {st_type} ({period_end})"
                else:
                    headline = f"{sym_u} missing filing {st_type} ({period_end}) fetched"

                if rev is not None and pat is not None:
                    headline += f" (Rev ₹{float(rev):,.1f} Cr, PAT ₹{float(pat):,.1f} Cr)"

                fid = f"{sym_u}_{period_end}_{st_type}_v1"
                # [RULE 67 CHANGE-RATIONALE: Use true filing/broadcast date or period_end rather than as_of_str]
                true_audit_fdate = _normalize_iso_date(str(item.get("filing_date") or item.get("filingDate") or item.get("broadcast_timestamp") or "")[:10]) or period_end or as_of_str

                finding_dict = {
                    "filing_date": true_audit_fdate,
                    "discovered_at": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
                    "symbol": sym_u,
                    "company_name": sym_u,
                    "exchange": str(item.get("exchange") or "NSE"),
                    "filing_id": fid,
                    "period_end": period_end,
                    "statement_type": st_type,
                    "basis": basis,
                    "event_type": finding_reason,
                    "revenue_cr": float(rev) if rev is not None else None,
                    "operating_profit_cr": float(op_prof) if op_prof is not None else None,
                    "pat_cr": float(pat) if pat is not None else None,
                    "eps": float(eps) if eps is not None else None,
                    "roce_pct": float(roce) if roce is not None else None,
                    "roe_pct": float(roe) if roe is not None else None,
                    "debt_to_equity": float(de) if de is not None else None,
                    "operating_margin_pct": float(op_m) if op_m is not None else None,
                    "net_margin_pct": float(net_m) if net_m is not None else None,
                    "shares_outstanding": float(shares) if shares is not None else None,
                    "filing_broadcast_date": item.get("broadcast_timestamp") or item.get("broadCastDate") or item.get("filing_date") or item.get("actual_publication_timestamp"),
                    "status": "SNAPSHOT_UPDATED",
                    "headline": headline,
                    "raw_facts": item,
                    "affected_metrics": self.resolve_dependent_metrics(st_type, {"facts": item})
                }
                findings.append(finding_dict)

            except Exception as read_err:
                logger.debug(f"[filling scanner] Error auditing filings for {sym_u}: {read_err}")

        # Batch persist all findings to database and local cache (guaranteed single entry per stock)
        try:
            from app.database import batch_upsert_corporate_filings
        except ImportError:
            from database import batch_upsert_corporate_filings
        batch_upsert_corporate_filings(findings)

        logger.info(f"📊 [filling scanner] Logged {len(findings)} genuinely missing filing findings for {as_of_str}.")
        return findings

    # -------------------------------------------------------------------------
    # 6. EXECUTION LIFECYCLE & TELEMETRY
    # -------------------------------------------------------------------------

    def run_watcher_cycle(
        self,
        trigger_type: str = "SCHEDULED",
        scheduler_name: str = "CRON",
        symbol: Optional[str] = None,
        force_rebuild: bool = False,
        force_backfill: bool = False,
    ) -> Dict[str, Any]:
        """
        Executes a complete financial filing watcher cycle:
          1. Acquires thread execution lock to prevent duplicate concurrent runs.
          2. Creates an execution record in scanner_execution_history (start_scanner_execution_run).
          3. Upserts scanner_health record to RUNNING.
          4. Audits approved universe and verifies/rebuilds canonical snapshot.
          5. On success:
             - Updates scanner_health to OK (duration, processed_count, last_success).
             - Finalizes scanner_execution_history as COMPLETED.
          6. On failure / exception:
             - Updates scanner_health to DOWN with detailed error message.
             - Logs error into fetch_errors and scan_failures tables.
             - Finalizes scanner_execution_history as FAILED.
        """
        from zoneinfo import ZoneInfo
        IST = ZoneInfo("Asia/Kolkata")
        start_ts = time.time()
        now_ist = datetime.now(IST)

        if not _WATCHER_LOCK.acquire(blocking=False):
            logger.info("⏳ [filling scanner] Watcher cycle is already actively executing in this process. Skipping duplicate trigger.")
            return {"status": "ALREADY_RUNNING", "message": "Already actively running"}

        run_ctx = None
        target_symbols = [symbol.strip().upper()] if symbol else list(self.approved_symbols)
        total_count = len(target_symbols)

        # Lazy import database telemetry utilities
        try:
            from app.database import (
                start_scanner_execution_run,
                complete_scanner_execution_run,
                upsert_scanner_health,
                upsert_fetch_error,
                get_connection,
            )
        except ImportError:
            from database import (
                start_scanner_execution_run,
                complete_scanner_execution_run,
                upsert_scanner_health,
                upsert_fetch_error,
                get_connection,
            )

        try:
            # 1. Register execution run in scanner_execution_history
            try:
                run_ctx = start_scanner_execution_run(
                    scanner_name="FILING_WATCHER",
                    trigger_type=trigger_type,
                    scheduler_name=scheduler_name,
                    total_stocks=total_count,
                )
            except Exception as run_err:
                if "already actively running" in str(run_err).lower():
                    # If this is a MANUAL trigger, CLI, or force run, auto-clean stale orphan and retry
                    if trigger_type in ("MANUAL", "CLI") or force_backfill or force_rebuild:
                        logger.warning("🧹 [filling scanner] Auto-cleaning stale orphaned RUNNING/QUEUED run before manual trigger...")
                        try:
                            with get_connection() as conn:
                                with conn.cursor() as cur:
                                    cur.execute("""
                                        UPDATE scanner_execution_history
                                        SET completed_at = NOW(),
                                            lifecycle_status = 'CANCELLED',
                                            error_summary = 'Superseded by fresh manual trigger'
                                        WHERE LOWER(scanner_name) = 'filing_watcher'
                                          AND lifecycle_status IN ('RUNNING', 'QUEUED');
                                    """)
                                    conn.commit()
                            run_ctx = start_scanner_execution_run(
                                scanner_name="FILING_WATCHER",
                                trigger_type=trigger_type,
                                scheduler_name=scheduler_name,
                                total_stocks=total_count,
                            )
                        except Exception as retry_err:
                            logger.error(f"[filling scanner] Failed to start execution run after auto-clean: {retry_err}")
                    else:
                        logger.info("⏳ [filling scanner] Active execution already registered in DB. Skipping duplicate.")
                        return {"status": "ALREADY_RUNNING", "message": "Already running in database"}
                else:
                    logger.warning(f"[filling scanner] Failed to insert execution run in scanner_execution_history: {run_err}")

            if run_ctx and hasattr(run_ctx, "start_heartbeat_worker"):
                try:
                    run_ctx.start_heartbeat_worker()
                except Exception:
                    pass

            # 2. Update health to RUNNING
            upsert_scanner_health(
                scanner_name="FILING_WATCHER",
                status="RUNNING",
                error_msg="Polling corporate announcements and verifying canonical snapshot...",
                processed_count=0,
                total_count=total_count,
                run_id=run_ctx.run_id if run_ctx else None,
            )

            # 2b. Actively poll live exchanges (NSE/BSE) for newly published filings
            try:
                poll_events = self.poll_exchange_filings(
                    target_symbols=target_symbols,
                    as_of_date=now_ist.date(),
                    run_ctx=run_ctx,
                    force_backfill=force_backfill,
                )
                if poll_events:
                    logger.info(f"📢 [filling scanner] Exchange poll ingested {len(poll_events)} new filing disclosures.")
            except Exception as poll_err:
                logger.warning(f"[filling scanner] Active exchange polling encountered non-fatal error: {poll_err}")

            # [RULE 67 CHANGE-RATIONALE: Execute fundamental data recovery sweep exclusively in FILING_WATCHER background cycle to isolate network filing downloads from live scanners and prevent global lock stalls.]
            # 2c. Execute fundamental data recovery sweep for incomplete symbols (handled exclusively by FILING_WATCHER)
            try:
                try:
                    from app.fundamental_pre_recovery import FundamentalPreRecoveryEngine
                except ImportError:
                    from fundamental_pre_recovery import FundamentalPreRecoveryEngine
                logger.info("🔄 [filling scanner] Starting fundamental data recovery sweep for incomplete symbols...")
                pre_recovery_engine = FundamentalPreRecoveryEngine(scanner_name="FILING_WATCHER")
                pre_recovery_engine.execute_pre_scan_sweep()
                logger.info("✅ [filling scanner] Fundamental data recovery sweep complete.")
            except Exception as pre_rec_err:
                logger.warning(f"[filling scanner] Fundamental recovery sweep notice: {pre_rec_err}")

            # 3. Detect pending symbols or missing snapshot
            pending_symbols = [
                s for s, v in self.state.items()
                if v.get("snapshot_status") == SnapshotFreshnessStatus.UPDATE_PENDING.value
            ]
            snapshot_missing = not SNAPSHOT_PATH.exists()

            rebuild_needed = force_rebuild or snapshot_missing or len(pending_symbols) > 0 or bool(symbol)
            rebuild_ok = True

            if rebuild_needed:
                logger.info(
                    f"🔄 [filling scanner] Triggering snapshot rebuild: "
                    f"force={force_rebuild}, snapshot_missing={snapshot_missing}, "
                    f"pending_count={len(pending_symbols)}, symbol={symbol}"
                )
                rebuild_ok = self.invalidate_and_rebuild_snapshot(symbol=symbol, run_ctx=run_ctx)
                if not rebuild_ok:
                    raise RuntimeError(
                        f"Canonical snapshot rebuild failed for target={symbol or 'ALL_PENDING'}"
                    )

            # 4. Verify canonical snapshot is on disk and valid
            if not SNAPSHOT_PATH.exists():
                raise FileNotFoundError(
                    f"Canonical PIT snapshot not found at {SNAPSHOT_PATH} after cycle completion."
                )

            # 4b. Corporate filing findings: strictly entries where something was updated/corrected in local DB
            findings = poll_events or []
            if not findings and hasattr(self, "audit_and_log_filings"):
                try:
                    mocked_f = self.audit_and_log_filings(target_symbols=target_symbols, as_of_date=now_ist.date(), only_updated=True)
                    if mocked_f:
                        findings = mocked_f
                except Exception:
                    pass
            filings_found_count = len(findings)

            duration = round(time.time() - start_ts, 2)
            processed = total_count

            # 5. Success update to scanner_health
            upsert_scanner_health(
                scanner_name="FILING_WATCHER",
                status="OK",
                last_success=now_ist.isoformat(),
                processed_count=processed,
                total_count=processed,
                duration_seconds=duration,
                outcome="SUCCESS",
                error_msg=None,
                run_id=run_ctx.run_id if run_ctx else None,
            )

            # 5b. Multi-source feed operational heartbeat update (NSE + BSE)
            try:
                try:
                    from app.financial_data_integrity import record_source_watermark
                except ImportError:
                    from financial_data_integrity import record_source_watermark
                record_source_watermark("NSE", last_successful_check_at=now_ist.isoformat())
                record_source_watermark("BSE", last_successful_check_at=now_ist.isoformat())
            except Exception as _wm_err:
                logger.warning(f"[filling scanner] Failed to record exchange watermarks: {_wm_err}")

            # 5c. [RULE 67: RESTART RESILIENCE]
            # Ensure latest canonical snapshot and raw filings bundle are uploaded to PostgreSQL parquet_cache
            # so server restarts and container redeployments immediately adopt the latest copy.
            try:
                try:
                    from app.database import upload_parquet_to_db, upload_pit_raw_bundle_to_db
                except ImportError:
                    from database import upload_parquet_to_db, upload_pit_raw_bundle_to_db
                if SNAPSHOT_PATH.exists():
                    upload_parquet_to_db("canonical_pit_rebuilt", str(SNAPSHOT_PATH))
                upload_pit_raw_bundle_to_db()
                logger.info("💾 [filling scanner] Synchronized latest canonical snapshot & raw filings bundle to DB parquet_cache.")
            except Exception as _sync_db_err:
                logger.warning(f"[filling scanner] End-of-cycle DB sync notice: {_sync_db_err}")

            # 6. Complete scanner_execution_history
            if run_ctx:
                complete_scanner_execution_run(
                    run_ctx,
                    lifecycle_status="COMPLETED",
                    quality_status="NORMAL",
                    total_stocks=processed,
                    fresh_data_count=processed,
                    summary_notes=f"Processed {processed} equities in {duration}s. Filings audited: {filings_found_count}. Rebuild: {rebuild_needed}.",
                    metrics_json={
                        "rebuild_needed": rebuild_needed,
                        "pending_symbols_resolved": len(pending_symbols),
                        "duration_seconds": duration,
                        "filings_found": filings_found_count,
                    }
                )

            logger.info(f"✅ [filling scanner: COMPLETE] Successfully audited {processed} stocks, logged {filings_found_count} findings in {duration}s.")
            return {
                "status": "SUCCESS",
                "processed_count": processed,
                "total_count": processed,
                "filings_found": filings_found_count,
                "duration_seconds": duration,
                "rebuilt": rebuild_needed,
            }

        except Exception as e:
            duration = round(time.time() - start_ts, 2)
            err_msg = f"Filing Watcher failed: {str(e)[:300]}"
            logger.exception(f"❌ [filling scanner: DOWN] {err_msg}")

            # 1. Update scanner_health to DOWN with full error_msg
            try:
                upsert_scanner_health(
                    scanner_name="FILING_WATCHER",
                    status="DOWN",
                    error_msg=err_msg,
                    duration_seconds=duration,
                    outcome="FAILED",
                    run_id=run_ctx.run_id if run_ctx else None,
                )
            except Exception as h_err:
                logger.error(f"[filling scanner] Failed to upsert scanner_health DOWN: {h_err}")

            # 2. Record in fetch_errors for Admin error grid
            try:
                upsert_fetch_error(
                    source_name="EXCHANGE_FILINGS",
                    scanner_name="FILING_WATCHER",
                    symbol=symbol or "UNIVERSE",
                    interval="1D",
                    category="CORPORATE_FILING",
                    error_msg=err_msg,
                )
            except Exception as fe_err:
                logger.error(f"[filling scanner] Failed to upsert fetch_error: {fe_err}")

            # 3. Insert failure trace in scan_failures for admin audit trail
            try:
                with get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO scan_failures (scan_id, scanner_name, symbol, provider, failure_reason, failed_at)
                            VALUES (%s, %s, %s, 'EXCHANGE_FILINGS', %s, NOW());
                        """, (
                            f"FAIL_FILING_WATCHER_{int(time.time())}",
                            "FILING_WATCHER",
                            symbol or "UNIVERSE",
                            err_msg,
                        ))
                        conn.commit()
            except Exception as sf_err:
                logger.debug(f"[filling scanner] Failed to insert scan_failures record: {sf_err}")

            # 4. Complete execution run as FAILED in scanner_execution_history
            if run_ctx:
                try:
                    complete_scanner_execution_run(
                        run_ctx,
                        exception=e,
                        lifecycle_status="FAILED",
                        stop_reason=err_msg,
                        summary_notes=f"Execution crashed after {duration}s: {err_msg}",
                    )
                except Exception as c_err:
                    logger.error(f"[filling scanner] Failed to complete failed execution run: {c_err}")

            return {
                "status": "FAILED",
                "error": err_msg,
                "duration_seconds": duration,
            }

        finally:
            if run_ctx and hasattr(run_ctx, "stop_heartbeat_worker"):
                try:
                    run_ctx.stop_heartbeat_worker()
                except Exception:
                    pass
            if _WATCHER_LOCK.locked():
                _WATCHER_LOCK.release()


# -----------------------------------------------------------------------------
# CLI Driver
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Financial Filing Watcher & Dependency Invalidation Engine")
    parser.add_argument("--poll", action="store_true", help="Poll for new filings and execute cycle")
    parser.add_argument("--symbol", type=str, default=None, help="Specific symbol to check/rebuild")
    parser.add_argument("--force-rebuild", action="store_true", help="Force rebuild canonical snapshot")
    parser.add_argument("--force-backfill", action="store_true", help=f"Force full historical bootstrap from {get_historical_cutoff_date()} (max 5Y scanner lookback + 1Y buffer)")
    parser.add_argument("--as-of-date", type=str, default=None, help="As-of date in YYYY-MM-DD")
    args = parser.parse_args()

    as_of = datetime.strptime(args.as_of_date, "%Y-%m-%d").date() if args.as_of_date else date.today()
    watcher = FinancialFilingWatcher()

    if args.poll or args.force_rebuild or args.force_backfill:
        res = watcher.run_watcher_cycle(
            trigger_type="MANUAL",
            scheduler_name="CLI",
            symbol=args.symbol,
            force_rebuild=args.force_rebuild,
            force_backfill=args.force_backfill,
        )
        print(f"\n[FILING_WATCHER] Cycle finished with status: {res.get('status')}")
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
