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

    def __init__(self, state_file: Path = STATE_FILE, events_log: Path = EVENTS_LOG_FILE):
        self.state_file = Path(state_file)
        self.events_log = Path(events_log)
        self.registry = ApprovedUniverseRegistry()
        self.approved_symbols = sorted(list(self.registry.approved_symbols))
        self.state: Dict[str, Dict[str, Any]] = self._load_state()

    def _load_state(self) -> Dict[str, Dict[str, Any]]:
        """Loads persistent watcher state with automated healing of legacy truncated dates."""
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
        """Persists watcher state atomically."""
        os.makedirs(self.state_file.parent, exist_ok=True)
        tmp_state = f"{self.state_file}.tmp.{os.getpid()}"
        with open(tmp_state, "w") as f:
            json.dump(self.state, f, indent=2)
        os.replace(tmp_state, self.state_file)

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
            facts = incoming_filing
        inc_rev = facts.get("revenue") or facts.get("revenueFromOperations") or facts.get("totalIncome") or facts.get("revenue_cr")
        inc_pat = facts.get("net_profit") or facts.get("netProfitLossForPeriod") or facts.get("profitBeforeTax") or facts.get("pat") or facts.get("pat_cr")
        inc_ebit = facts.get("operating_profit") or facts.get("ebit") or facts.get("operatingProfit")
        inc_eps = facts.get("eps") or facts.get("basicEps")

        # 1. Compare against local data: data/pit_raw_filings/<sym>.json
        local_raw_path = BASE_DIR / "data" / "pit_raw_filings" / f"{sym}.json"
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
                            if r_per and r_per == period_end and r_typ == st_type:
                                local_period_found = True
                                local_matched_row = row
                                break
            except Exception as e:
                logger.debug(f"[filling scanner] Local file read note for {sym}: {e}")

        event_type = FilingEventType.NO_CHANGE
        unmatching_reason = None

        if not local_period_found:
            # Case A: Period completely missing from local data -> UNMATCHING!
            event_type = FilingEventType.UNSEEN_PERIOD
            unmatching_reason = f"Period {period_end} ({st_type}) missing from local PIT raw filings"
        elif local_matched_row:
            # Case B: Local record exists, but check if local facts are missing or unmatching
            loc_rev = local_matched_row.get("revenue") or local_matched_row.get("mapped_revenue")
            loc_pat = local_matched_row.get("net_profit") or local_matched_row.get("pat")
            loc_eps = local_matched_row.get("eps")

            # Check if local has nulls while exchange has actual figures
            if (loc_rev is None and inc_rev is not None) or (loc_pat is None and inc_pat is not None):
                event_type = FilingEventType.CHANGED_PAYLOAD
                unmatching_reason = "Local record has missing/null metrics; reconciled from exchange facts"
            # Check for numerical discrepancy (> 0.1 Cr)
            elif inc_rev is not None and loc_rev is not None:
                try:
                    if abs(float(inc_rev) - float(loc_rev)) > 0.1:
                        event_type = FilingEventType.AMENDED_FILING
                        unmatching_reason = f"Revenue discrepancy: local={loc_rev} vs exchange={inc_rev}"
                except Exception:
                    pass
            elif inc_pat is not None and loc_pat is not None:
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
            else:
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

                upsert_corporate_filing_finding({
                    "filing_date": today_ist,  # Explicitly log under today's date so admin view immediately shows it!
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
        sym_target = symbol.strip().upper() if symbol else "ALL_PENDING"
        logger.info(f"🔄 [filling scanner] [SNAPSHOT_REBUILD] Target: {sym_target} as of {as_of.isoformat()}...")

        try:
            from scripts.rebuild_pit_from_exchange import rebuild_canonical_pit_dataset
            from app.financial_data_integrity import clear_shared_snapshot_cache

            target_list = None
            if symbol:
                target_list = [symbol.strip().upper()]
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
                if symbol and symbol.upper() in self.state:
                    self.state[symbol.upper()]["snapshot_status"] = SnapshotFreshnessStatus.INVALID.value
                else:
                    for s in self.state.values():
                        if s.get("snapshot_status") == SnapshotFreshnessStatus.UPDATE_PENDING.value:
                            s["snapshot_status"] = SnapshotFreshnessStatus.INVALID.value
                self._save_state()
                return False

            # Mark state as FRESH only when data completeness invariants pass
            if symbol and symbol.upper() in self.state:
                self.state[symbol.upper()]["snapshot_status"] = SnapshotFreshnessStatus.FRESH.value
            else:
                for s in self.state.values():
                    if s.get("snapshot_status") == SnapshotFreshnessStatus.UPDATE_PENDING.value:
                        s["snapshot_status"] = SnapshotFreshnessStatus.FRESH.value
            self._save_state()

            logger.info(f"✅ [filling scanner] [SNAPSHOT_REBUILD: SUCCESS] Rebuilt {len(df)} rows. Current EV/EBITDA Complete: {ev_count}/{len(df)}. Status set to FRESH.")

            # If rebuilding for specific symbol, check and restore if previously stale
            if symbol:
                try:
                    try:
                        from app.database import check_and_restore_stale_stock
                    except ImportError:
                        from database import check_and_restore_stale_stock
                    check_and_restore_stale_stock(
                        symbol=symbol.strip().upper(),
                        source="SNAPSHOT_REBUILD",
                        notify_admin=True
                    )
                except Exception:
                    pass

            return True
        except Exception as e:
            logger.error(f"❌ [filling scanner] [SNAPSHOT_REBUILD: FAILED] {e}")
            if symbol and symbol.upper() in self.state:
                self.state[symbol.upper()]["snapshot_status"] = SnapshotFreshnessStatus.INVALID.value
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
            self.state = self._load_state()
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
    ) -> List[Dict[str, Any]]:
        """
        Actively polls exchange sources (NSE XBRL API, BSE Corporate API, and FundamentalSourceRouter)
        for newly published filings/earnings disclosures.
        For every discovered filing:
          1. Detects filing changes (NEW_FILING, UNSEEN_PERIOD, AMENDED_FILING, NO_CHANGE).
          2. Immutably hashes and archives raw exchange payloads to data/exchange_financials/<SYM>/raw/.
          3. Upserts raw facts into data/pit_raw_filings/<SYM>.json.
          4. Logs discovery into corporate_filings_log database table & JSONL.
          5. Marks snapshot_status = UPDATE_PENDING for symbol if new/amended data ingested.
        """
        as_of = as_of_date or date.today()
        as_of_str = as_of.isoformat()
        symbols = target_symbols or self.approved_symbols
        if max_symbols:
            symbols = symbols[:max_symbols]

        logger.info(f"🌐 [filling scanner] Actively polling exchange filings (NSE/BSE) for {len(symbols)} symbols as of {as_of_str}...")

        discovered_events: List[Dict[str, Any]] = []

        try:
            try:
                from app.data_providers.nse_xbrl_provider import NseXbrlProvider
            except ImportError:
                from data_providers.nse_xbrl_provider import NseXbrlProvider
            nse_provider = NseXbrlProvider()
        except Exception as e:
            logger.warning(f"[filling scanner] Could not initialize NseXbrlProvider: {e}")
            nse_provider = None

        try:
            try:
                from app.data_providers.bse_corporate_provider import BseCorporateProvider
            except ImportError:
                from data_providers.bse_corporate_provider import BseCorporateProvider
            bse_provider = BseCorporateProvider()
        except Exception as e:
            logger.warning(f"[filling scanner] Could not initialize BseCorporateProvider: {e}")
            bse_provider = None

        try:
            from app.database import upsert_corporate_filing_finding
        except ImportError:
            from database import upsert_corporate_filing_finding

        for sym in symbols:
            sym_u = sym.strip().upper()
            try:
                raw_filings_list: List[Dict[str, Any]] = []

                # 1. Poll NSE XBRL / Corporate Results
                if nse_provider:
                    try:
                        # Attempt to fetch rich parsed XBRL financials first
                        nse_records = nse_provider.fetch_raw_financials(sym_u)
                        if nse_records:
                            for r in nse_records:
                                norm_p = _normalize_iso_date(r.period_end_date)
                                if not norm_p or norm_p < "2015-01-01":
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
                                    "filing_date": r.availability_date or as_of_str,
                                    "broadcast_timestamp": getattr(r, "broadcast_timestamp", as_of_str),
                                })
                        else:
                            # Fallback to discover_filings metadata
                            nse_rows = nse_provider.discover_filings(sym_u)
                            if nse_rows:
                                for r in nse_rows:
                                    if isinstance(r, dict):
                                        r_copy = dict(r)
                                        r_copy["exchange"] = "NSE"
                                        # Normalize period_end_date if present
                                        p_end = _normalize_iso_date(r_copy.get("period_end_date") or r_copy.get("toDate"))
                                        if p_end and p_end >= "2015-01-01":
                                            r_copy["period_end_date"] = p_end
                                            raw_filings_list.append(r_copy)
                    except Exception as nse_err:
                        logger.debug(f"[filling scanner] NSE discover/fetch failed for {sym_u}: {nse_err}")

                # 2. Poll BSE Corporate Results if NSE had nothing or symbol is BSE-listed
                if bse_provider and (not raw_filings_list or len(raw_filings_list) == 0):
                    try:
                        scrip = bse_provider.resolve_bse_scrip_code(sym_u)
                        if scrip:
                            bse_rows = bse_provider.fetch_raw_financials(sym_u)
                            for r in bse_rows:
                                norm_p = _normalize_iso_date(r.period_end_date)
                                if not norm_p or norm_p < "2015-01-01":
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
                                    "filing_date": r.availability_date or as_of_str,
                                    "broadcast_timestamp": getattr(r, "broadcast_timestamp", as_of_str),
                                })
                    except Exception as bse_err:
                        logger.debug(f"[filling scanner] BSE discover failed for {sym_u}: {bse_err}")

                if not raw_filings_list:
                    continue

                # Filter to valid ISO dates >= 2015-01-01
                import re as _re_poll
                valid_items = []
                for item in raw_filings_list:
                    raw_p = item.get("toDate") or item.get("period_end_date") or item.get("periodEnd") or ""
                    period_end = _normalize_iso_date(str(raw_p).strip())
                    if period_end and _re_poll.match(r'^\d{4}-\d{2}-\d{2}$', period_end) and period_end >= "2015-01-01":
                        item["period_end_date"] = period_end
                        valid_items.append(item)

                if not valid_items:
                    continue

                valid_items.sort(key=lambda x: str(x.get("period_end_date", "")))

                # Check against canonical snapshot to avoid processing already-known historical periods
                snap_row = None
                try:
                    if SNAPSHOT_PATH.exists():
                        import pandas as _pd_p
                        _sdf = _pd_p.read_parquet(SNAPSHOT_PATH, columns=["symbol", "latest_annual_period", "pit_freshness_status"])
                        _match = _sdf[_sdf["symbol"].str.upper() == sym_u]
                        if not _match.empty:
                            snap_row = _match.iloc[0]
                except Exception:
                    pass

                snap_annual = str(snap_row.get("latest_annual_period") or "")[:10] if snap_row is not None else ""
                freshness = str(snap_row.get("pit_freshness_status") or "FRESH") if snap_row is not None else "MISSING"

                if snap_row is not None and snap_annual and freshness in ("FRESH", "VALID"):
                    candidate_items = [it for it in valid_items if str(it.get("period_end_date"))[:10] > snap_annual]
                else:
                    candidate_items = valid_items

                if not candidate_items:
                    continue

                # Rank candidates: latest period_end_date, ANNUAL > QUARTERLY, CONSOLIDATED > STANDALONE
                def _candidate_rank(x):
                    p_date = str(x.get("period_end_date", ""))
                    st = str(x.get("period") or x.get("statement_type") or "").upper()
                    is_ann = 1 if ("ANNUAL" in st or "YEAR" in st or "FY" in st) else 0
                    c_val = str(x.get("consolidated", "")).lower()
                    is_cons = 1 if ("standalone" not in c_val and "non-consolidated" not in c_val) else 0
                    return (p_date, is_ann, is_cons)

                candidate_items.sort(key=_candidate_rank)

                # Take ONLY the single latest genuinely missing filing for this stock
                item = candidate_items[-1]
                period_end = item["period_end_date"]

                st_type = str(item.get("period") or item.get("statement_type") or "QUARTERLY").upper()
                if "ANNUAL" in st_type or "YEAR" in st_type or "FY" in st_type:
                    st_type = "ANNUAL"
                else:
                    st_type = "QUARTERLY"

                basis = "STANDALONE" if ("standalone" in str(item.get("consolidated", "")).lower() or "non-consolidated" in str(item.get("consolidated", "")).lower()) else "CONSOLIDATED"
                fid = str(item.get("seqNumber") or item.get("filing_id") or f"{sym_u}_{period_end}_{st_type}_v1")
                bcast_time = str(item.get("broadCastDate") or item.get("filingDate") or item.get("broadcast_timestamp") or as_of_str)

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
                    self._merge_into_pit_raw_filings(sym_u, item, period_end, st_type, basis)
                    discovered_events.append(event.to_dict())

            except Exception as sym_err:
                logger.debug(f"[filling scanner] Exchange polling error for {sym_u}: {sym_err}")

        logger.info(f"✅ [filling scanner] Exchange polling complete. Discovered {len(discovered_events)} new/amended filing events.")
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
        raw_dir = BASE_DIR / "data" / "pit_raw_filings"
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

        # Find if this period and statement_type already exists
        matched_idx = None
        for i, row in enumerate(existing_list):
            r_period = _normalize_iso_date(row.get("period_end_date"))
            r_type = str(row.get("statement_type") or "").upper()
            if r_period and r_period == norm_period_end and r_type == statement_type:
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
            "filing_date": new_item.get("filingDate") or new_item.get("filing_date") or date.today().isoformat(),
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
        limit_symbols: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Scans filings across universe equities, identifying GENUINELY MISSING
        data in the local canonical snapshot used by trading scanners.
        Persists at most a SINGLE ENTRY PER STOCK for any missing filing discovered.

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

        # Load canonical PIT snapshot used by trading scanners
        snap_map: Dict[str, Any] = {}
        try:
            import pandas as pd
            if SNAPSHOT_PATH.exists():
                snap_df = pd.read_parquet(SNAPSHOT_PATH)
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
                    return bool(p and _re.match(r'^\d{4}-\d{2}-\d{2}$', p) and p >= "2015-01-01")

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

                finding_dict = {
                    "filing_date": as_of_str,
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
                    "filing_broadcast_date": item.get("filing_date") or item.get("actual_publication_timestamp"),
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
                    logger.info("⏳ [filling scanner] Active execution already registered in DB. Skipping duplicate.")
                    return {"status": "ALREADY_RUNNING", "message": "Already running in database"}
                logger.warning(f"[filling scanner] Failed to insert execution run in scanner_execution_history: {run_err}")

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
                poll_events = self.poll_exchange_filings(target_symbols=target_symbols, as_of_date=now_ist.date())
                if poll_events:
                    logger.info(f"📢 [filling scanner] Exchange poll ingested {len(poll_events)} new filing disclosures.")
            except Exception as poll_err:
                logger.warning(f"[filling scanner] Active exchange polling encountered non-fatal error: {poll_err}")

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

            # 4b. Audit and record discovered corporate filing findings into database table
            findings = self.audit_and_log_filings(target_symbols=target_symbols, as_of_date=now_ist.date())
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
    parser.add_argument("--as-of-date", type=str, default=None, help="As-of date in YYYY-MM-DD")
    args = parser.parse_args()

    as_of = datetime.strptime(args.as_of_date, "%Y-%m-%d").date() if args.as_of_date else date.today()
    watcher = FinancialFilingWatcher()

    if args.poll or args.force_rebuild:
        res = watcher.run_watcher_cycle(
            trigger_type="MANUAL",
            scheduler_name="CLI",
            symbol=args.symbol,
            force_rebuild=args.force_rebuild,
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
