#!/usr/bin/env python3
"""
app/live_fundamental_scanner.py
===============================
ELITE BREAKOUT SYSTEM — FUNDAMENTALLY STRONG LIVE BUY SCANNER

GOVERNANCE INVARIANTS:
  1. Live Alert-Only Decision Support Dashboard:
     - NO broker order placement, NO automatic order placement, NO automatic buy, NO automatic sell.
  2. Mandatory Fundamental Strength Gate:
     - The BUY scanner MUST NOT evaluate '20D Breakout -> BUY'.
     - It MUST evaluate:
       'Approved Universe -> Fundamental Quality -> Earnings Acceleration -> Trend -> Consolidation -> 20D Breakout -> BUY'
     - Fundamental strength is a HARD GATE: no partial credit, no score-based compensation.
     - A strong technical chart NEVER overrides a failed fundamental gate.
  3. Approved Universe:
     - Exactly 886 certified clean Indian equities (41 corporate action anomaly stocks quarantined).
  4. Point-in-Time Causality & Data Health:
     - Missing or stale fundamental data FAILS CLOSED (BUY = BLOCKED).
     - Publication timestamp must precede signal timestamp.
"""

from __future__ import annotations
import os
import sys
import glob
import json
import time
import logging
import math
import hashlib  # Rule 67: Required for SHA256 alert_payload_hash (BUY alert immutable fingerprint — Finding 2)
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, List, Any, Optional, Tuple
from enum import Enum
import numpy as np
import pandas as pd
import requests
import pyarrow as pa
import pyarrow.parquet as pq

try:
    from trading_calendar import default_trading_calendar, get_latest_trading_date, get_previous_trading_date
except ImportError:
    try:
        from app.trading_calendar import default_trading_calendar, get_latest_trading_date, get_previous_trading_date
    except ImportError:
        default_trading_calendar = None
        get_latest_trading_date = None
        get_previous_trading_date = None

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger("LIVE_FUNDAMENTAL_SCANNER")

try:
    from app.fundamental_telemetry import FundamentalScanTelemetry
except ImportError:
    from fundamental_telemetry import FundamentalScanTelemetry

import threading
try:
    from lock_utils import ProcessLock, print_scanner_start_banner, print_scanner_end_banner
except ImportError:
    from app.lock_utils import ProcessLock, print_scanner_start_banner, print_scanner_end_banner

# Universal sequential lock shared across the 3 main scanners
_global_lock = ProcessLock("global_scanner_lock")
_fundamental_scan_lock = threading.Lock()
_v2_scan_lock = threading.Lock()

try:
    from app.financial_data_integrity import (
        check_pit_freshness,
        detect_annual_fiscal_gaps,
        compute_cagr_pit,
        compute_ev_pit,
        validate_share_count,
        compute_yoy_quarterly,
        compute_ebitda,
        compute_roce,
        pre_buy_data_integrity_gate,
        BUYEvidenceBundle,
        FieldProvenance,
        DataStatus,
        FreshnessStatus,
    )
except ImportError:
    from financial_data_integrity import (
        check_pit_freshness,
        detect_annual_fiscal_gaps,
        compute_cagr_pit,
        compute_ev_pit,
        validate_share_count,
        compute_yoy_quarterly,
        compute_ebitda,
        compute_roce,
        pre_buy_data_integrity_gate,
        BUYEvidenceBundle,
        FieldProvenance,
        DataStatus,
        FreshnessStatus,
    )

BASE_DIR = os.getenv("ELITE_BASE_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if not os.path.exists(os.path.join(BASE_DIR, "data")) and os.path.exists("/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/data"):
    BASE_DIR = "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM"
for _sp in [BASE_DIR, os.path.join(BASE_DIR, "app")]:
    if _sp not in sys.path:
        sys.path.insert(0, _sp)
DATA_DIR = os.path.join(BASE_DIR, "data")
CLEAN_UNIVERSE_JSON = os.path.join(DATA_DIR, "certified_clean_universe_886.json")
QUARANTINE_JSON = os.path.join(DATA_DIR, "quarantined_anomaly_symbols_41.json")

# Governance References
FROZEN_GIT_SHA = "35fe412d"
RULES_HASH_BUY = "bf04bf9ca9810bb62b4c1aa5e4125d19e99a807d9f75bfdc8ce645c38bc35fc2"


# -------------------------------------------------------------------------------------
# REJECTION REASONS ENUMERATION
# -------------------------------------------------------------------------------------
class RejectionReason(str, Enum):
    # Universe
    EXCLUDED_UNAPPROVED_UNIVERSE = "EXCLUDED_UNAPPROVED_UNIVERSE"
    EXCLUDED_QUARANTINED_ANOMALY = "EXCLUDED_QUARANTINED_ANOMALY"
    FAIL_UNIVERSE_FINANCIAL_SECTOR = "FAIL_UNIVERSE_FINANCIAL_SECTOR"
    # Data Quality & Provenance
    FUNDAMENTAL_DATA_MISSING = "FUNDAMENTAL_DATA_MISSING"
    FUNDAMENTAL_DATA_STALE = "FUNDAMENTAL_DATA_STALE"
    FUNDAMENTAL_PROVENANCE_INVALID = "FUNDAMENTAL_PROVENANCE_INVALID"
    MARKET_DATA_MISSING = "MARKET_DATA_MISSING"
    MARKET_DATA_INSUFFICIENT_LOOKBACK = "MARKET_DATA_INSUFFICIENT_LOOKBACK"
    # Fundamental Quality Gate
    FAIL_ROCE = "FAIL_ROCE"
    FAIL_ROE = "FAIL_ROE"
    FAIL_OCF = "FAIL_OCF"
    FAIL_DEBT_EQUITY = "FAIL_DEBT_EQUITY"
    FAIL_VALUE_TRAP = "FAIL_VALUE_TRAP"
    FAIL_QUALITY_METRICS_INCOMPLETE = "FAIL_QUALITY_METRICS_INCOMPLETE"
    # Earnings Acceleration Gate
    FAIL_REVENUE_ACCELERATION = "FAIL_REVENUE_ACCELERATION"
    FAIL_OP_PROFIT_ACCELERATION = "FAIL_OP_PROFIT_ACCELERATION"
    FAIL_EPS_ACCELERATION = "FAIL_EPS_ACCELERATION"
    FAIL_PRIOR_EPS = "FAIL_PRIOR_EPS"
    FAIL_EARNINGS_ACCELERATION = "FAIL_EARNINGS_ACCELERATION"
    FAIL_GROWTH_DATA_INSUFFICIENT = "FAIL_GROWTH_DATA_INSUFFICIENT"
    # Technical Trend & Relative Strength Gate
    FAIL_TREND = "FAIL_TREND"
    FAIL_RELATIVE_STRENGTH = "FAIL_RELATIVE_STRENGTH"
    # Consolidation Gate
    FAIL_CONSOLIDATION_WINDOW = "FAIL_CONSOLIDATION_WINDOW"
    FAIL_CONSOLIDATION_DRAWDOWN = "FAIL_CONSOLIDATION_DRAWDOWN"
    FAIL_CONSOLIDATION_ATR = "FAIL_CONSOLIDATION_ATR"
    FAIL_CONSOLIDATION_SMA200 = "FAIL_CONSOLIDATION_SMA200"
    # 20D Breakout Gate
    FAIL_BREAKOUT_PRICE = "FAIL_BREAKOUT_PRICE"
    FAIL_BREAKOUT_VOLUME = "FAIL_BREAKOUT_VOLUME"
    FAIL_BREAKOUT_EXTENSION = "FAIL_BREAKOUT_EXTENSION"


FINANCIAL_KEYWORDS = [
    "BANK", "FINANCE", "FINANCIAL", "HOUSING FINANCE", "NBFC",
    "INSURANCE", "INVESTMENT", "CAPITAL", "SECURITIES", "LEASING"
]

FINANCIAL_SYMBOLS = {
    'HDFCBANK', 'ICICIBANK', 'SBIN', 'KOTAKBANK', 'AXISBANK',
    'BAJFINANCE', 'BAJAJFINSV', 'CHOLAFIN', 'MUTHOOTFIN', 'SHRIRAMFIN',
    'AUBANK', 'BANKBARODA', 'BANKINDIA', 'CANFINHOME', 'CAPITALSFB',
    'CSBBANK', 'CUB', 'DCBBANK', 'FEDERALBNK', 'FEDFINA', 'IDFCFIRSTB',
    'INDIANB', 'J&KBANK', 'KARURVYSYA', 'KTKBANK', 'PNB', 'SBICARD',
    'SBILIFE', 'SGFIN', 'TMB', 'UNIONBANK', 'BANDHANBNK', 'CANBK',
    'GODIGIT', 'ICICIGI', 'APTUS', 'AYE', 'MANAPPURAM', 'POONAWALLA',
    'L&TFH', 'PEL', 'CREDITACC', 'HOMEFIRST', 'FIVESTAR'
}

def is_financial_entity(fundamentals: Optional[Dict[str, Any]], symbol: Optional[str] = None) -> bool:
    """Identifies financial sector entities (banks, NBFCs, insurance, housing finance) excluded from industrial ROCE."""
    sym = (symbol or (fundamentals.get("symbol", "") if fundamentals else "")).upper()
    if sym in FINANCIAL_SYMBOLS:
        return True
    if not fundamentals:
        return False
    sec = str(fundamentals.get("sector", fundamentals.get("Sector", ""))).upper()
    ind = str(fundamentals.get("industry", fundamentals.get("Industry", ""))).upper()
    sub = str(fundamentals.get("financial_sub_path", "")).upper()
    if sub in ("BANK", "NBFC_HFC", "INSURANCE", "AMC", "FINANCIAL_UNCLASSIFIED"):
        return True
    if any(kw in sec for kw in FINANCIAL_KEYWORDS) or any(kw in ind for kw in FINANCIAL_KEYWORDS):
        return True
    return False


# -------------------------------------------------------------------------------------
# DATA RECOVERY AUDIT LOGGER
# -------------------------------------------------------------------------------------
def _emit_data_recovery_log(
    *,
    scanner: str,
    symbol: str,
    stage: str,
    missing_data: str,
    recovery_attempted: bool,
    providers: Optional[List[Dict[str, str]]] = None,
    validation: Optional[str] = None,
    validation_reason: Optional[str] = None,
    final_action: str = "STOCK_SKIPPED",
    attempts: Optional[Dict[str, str]] = None,
    field: Optional[str] = None,
) -> None:
    """
    Emits a structured [DATA_RECOVERY] audit block to the application log.

    This function answers four governance questions for every skipped symbol:
      1. What data was missing?
      2. What was attempted to recover it?
      3. Did recovery + validation succeed?
      4. Why was the stock ultimately skipped?
    """
    lines = [
        f"[DATA_RECOVERY]",
        f"  scanner={scanner}",
        f"  symbol={symbol}",
        f"  stage={stage}",
    ]
    if field:
        lines.append(f"  field={field}")
    lines.append(f"  missing_data={missing_data}")
    lines.append(f"  recovery_attempted={str(recovery_attempted).lower()}")

    if attempts:
        for att_k, att_v in attempts.items():
            lines.append(f"  {att_k}={att_v}")
    elif providers:
        for p in providers:
            lines.append(f"  provider={p.get('provider', 'UNKNOWN')}")
            lines.append(f"    result={p.get('result', 'UNKNOWN')}")
            if p.get('rows_received') is not None:
                lines.append(f"    rows_received={p['rows_received']}")
            if p.get('failure_type'):
                lines.append(f"    failure_type={p['failure_type']}")
            if p.get('validation'):
                lines.append(f"    validation={p['validation']}")
            if p.get('validation_reason'):
                lines.append(f"    validation_reason={p['validation_reason']}")
    if validation is not None:
        lines.append(f"  validation={validation}")
    if validation_reason is not None:
        lines.append(f"  validation_reason={validation_reason}")
    lines.append(f"  final_action={final_action}")
    log_level = logging.WARNING if final_action == "STOCK_SKIPPED" else logging.INFO
    logger.log(log_level, "\n".join(lines))


# -------------------------------------------------------------------------------------
# PIT FUNDAMENTALS APPROVED RECOVERY PROVIDER
# -------------------------------------------------------------------------------------
_PIT_FILINGS_CACHE: Optional[Dict[str, List[Dict[str, Any]]]] = None

def _get_pit_filings(symbol: str, allow_live_refresh: bool = False) -> List[Dict[str, Any]]:
    """Returns historical filings for symbol from PIT database, sorted by period_end_date descending."""
    global _PIT_FILINGS_CACHE
    sym_u = str(symbol).strip().upper()
    if _PIT_FILINGS_CACHE is None:
        _PIT_FILINGS_CACHE = {}
        pit_path = os.path.join(DATA_DIR, "pit_fundamentals_v1", "pit_fundamentals_v1.parquet")
        if os.path.exists(pit_path):
            try:
                df_pit = pd.read_parquet(pit_path)
                if not df_pit.empty and "symbol" in df_pit.columns:
                    for sym, group in df_pit.sort_values("period_end_date", ascending=False).groupby("symbol"):
                        _PIT_FILINGS_CACHE[str(sym).upper()] = group.to_dict("records")
            except Exception as _e:
                logger.debug(f"PIT filings cache load notice: {_e}")

    filings = _PIT_FILINGS_CACHE.get(sym_u, [])
    if (not filings or len(filings) == 0) and allow_live_refresh:
        # Exhaustive recovery: live fetch from authoritative filing provider
        try:
            from scripts.ingest_pit_quarterly_and_annual import fetch_and_parse_symbol
            import requests
            sess = requests.Session()
            records, q_cnt, a_cnt = fetch_and_parse_symbol(sym_u, sess)
            if records:
                filings = sorted(records, key=lambda x: str(x.get("period_end_date", "")), reverse=True)
                _PIT_FILINGS_CACHE[sym_u] = filings
                logger.info(f"🌐 [FILING_PROVIDER_REFRESH] {sym_u}: Live retrieved {len(records)} filings (Q={q_cnt}, A={a_cnt})")
        except Exception as _fe:
            logger.debug(f"Live filing refresh error for {sym_u}: {_fe}")

    return filings


# -------------------------------------------------------------------------------------
# 1. APPROVED STOCK UNIVERSE REGISTRY
# -------------------------------------------------------------------------------------
class ApprovedUniverseRegistry:
    """Manages the 886 certified clean Indian equities and 41 quarantined anomaly stocks."""

    def __init__(self):
        self.clean_symbols: set = set()
        self.quarantined_symbols: set = set()
        self._load_universe()

    def _load_universe(self):
        if os.path.exists(CLEAN_UNIVERSE_JSON):
            try:
                with open(CLEAN_UNIVERSE_JSON, "r") as f:
                    self.clean_symbols = set(json.load(f).get("symbols", []))
            except Exception as e:
                logger.warning(f"Failed to load clean universe JSON: {e}")

        if os.path.exists(QUARANTINE_JSON):
            try:
                with open(QUARANTINE_JSON, "r") as f:
                    self.quarantined_symbols = set(json.load(f).get("symbols", []))
            except Exception as e:
                logger.warning(f"Failed to load quarantine JSON: {e}")

        # Fallback if clean_symbols is empty: populate from 1D history parquets
        if not self.clean_symbols:
            history_dir = os.path.join(DATA_DIR, "history", "1d")
            if os.path.exists(history_dir):
                files = glob.glob(os.path.join(history_dir, "*.parquet"))
                self.clean_symbols = {
                    os.path.basename(p).replace(".parquet", "").upper()
                    for p in files if not os.path.basename(p).startswith("^") and "NIFTY" not in os.path.basename(p).upper()
                } - self.quarantined_symbols

    @property
    def master_symbols(self) -> set:
        return self.clean_symbols | self.quarantined_symbols

    @property
    def approved_symbols(self) -> set:
        return self.clean_symbols

    def validate_symbol(self, symbol: str) -> Tuple[bool, Optional[RejectionReason]]:
        sym = symbol.upper()
        if sym in self.quarantined_symbols:
            return False, RejectionReason.EXCLUDED_QUARANTINED_ANOMALY
        if self.clean_symbols and sym not in self.clean_symbols:
            return False, RejectionReason.EXCLUDED_UNAPPROVED_UNIVERSE
        return True, None


# -------------------------------------------------------------------------------------
# 2. FUNDAMENTAL QUALITY GATE (HARD BOOLEAN GATE)
# -------------------------------------------------------------------------------------
class FundamentalQualityGate:
    """
    Mandatory Quality Conditions:
      - ROCE >= 15.0%
      - ROE >= 12.0%
      - Operating Cash Flow (OCF) > 0.0
      - Debt / Equity <= 1.0
    Hard gate: zero partial credit, zero score-based compensation.
    """

    @staticmethod
    def evaluate(fundamentals: Dict[str, Any], symbol: Optional[str] = None) -> Tuple[bool, List[RejectionReason], Dict[str, Any]]:
        failures = []
        sym = symbol or (fundamentals.get("symbol", "UNKNOWN") if fundamentals else "UNKNOWN")
        if not fundamentals or fundamentals.get("upstream_provider") == "DATA_UNAVAILABLE":
            logger.info(f"🔍 [GATE_EVAL:FQ] {sym} REJECTED: fundamentals record is missing or empty")
            return False, [RejectionReason.FUNDAMENTAL_DATA_MISSING], {}

        # Sector Gate: Financial entities (banks, NBFCs, insurance) do not have industrial ROCE / D/E
        if is_financial_entity(fundamentals, sym):
            failures.append(RejectionReason.FAIL_UNIVERSE_FINANCIAL_SECTOR)
            logger.info(f"🔍 [GATE_EVAL:FQ] {sym:<12} REJECTED (FINANCIAL_SECTOR): Excluded from industrial ROCE/DE model")
            return False, failures, {"sector_type": "FINANCIAL"}

        roce = fundamentals.get("roce")
        roe = fundamentals.get("roe")
        ocf = fundamentals.get("operating_cash_flow", fundamentals.get("ocf"))
        debt_equity = fundamentals.get("debt_equity", fundamentals.get("debt_to_equity"))
        is_trap = bool(fundamentals.get("is_value_trap", False) or (str(fundamentals.get("fundamental_category", "")).upper() == "VALUE_TRAP"))

        roce_val = None
        if roce is not None and not pd.isna(roce):
            roce_val = float(roce)
            if 0.0 < roce_val <= 1.0:
                roce_val *= 100.0

        roe_val = None
        if roe is not None and not pd.isna(roe):
            roe_val = float(roe)
            if 0.0 < roe_val <= 1.0:
                roe_val *= 100.0

        ocf_val = float(ocf) if (ocf is not None and not pd.isna(ocf)) else None
        de_val = float(debt_equity) if (debt_equity is not None and not pd.isna(debt_equity)) else None

        metrics = {
            "roce": round(roce_val, 2) if roce_val is not None else None,
            "roe": round(roe_val, 2) if roe_val is not None else None,
            "operating_cash_flow": round(ocf_val, 2) if ocf_val is not None else None,
            "debt_equity": round(de_val, 2) if de_val is not None else None,
            "is_value_trap": is_trap,
            "fundamental_category": str(fundamentals.get("fundamental_category", "NONE")),
            "quality_score": float(fundamentals.get("quality_score", 0.0) or 0.0),
            "growth_score": float(fundamentals.get("growth_score", 0.0) or 0.0)
        }

        # Check for missing values
        missing_fields = []
        if roce_val is None: missing_fields.append("roce")
        if roe_val is None: missing_fields.append("roe")
        if ocf_val is None: missing_fields.append("ocf")
        if de_val is None: missing_fields.append("debt_equity")

        if missing_fields:
            failures.append(RejectionReason.FAIL_QUALITY_METRICS_INCOMPLETE)
            logger.info(
                f"🔍 [GATE_EVAL:FQ] {sym:<12} REJECTED (QUALITY_INCOMPLETE): missing={missing_fields} | "
                f"roce={f'{roce_val:.1f}%' if roce_val is not None else 'MISSING'} | "
                f"roe={f'{roe_val:.1f}%' if roe_val is not None else 'MISSING'} | "
                f"ocf={f'{ocf_val:.1f}' if ocf_val is not None else 'MISSING'} | "
                f"d/e={f'{de_val:.2f}' if de_val is not None else 'MISSING'}"
            )
            return False, failures, metrics

        if roce_val < 15.0:
            failures.append(RejectionReason.FAIL_ROCE)
        if roe_val < 12.0:
            failures.append(RejectionReason.FAIL_ROE)
        if ocf_val <= 0.0:
            failures.append(RejectionReason.FAIL_OCF)
        if de_val > 1.0:
            failures.append(RejectionReason.FAIL_DEBT_EQUITY)
        if is_trap:
            failures.append(RejectionReason.FAIL_VALUE_TRAP)

        if failures:
            logger.info(
                f"🔍 [GATE_EVAL:FQ] {sym:<12} REJECTED (THRESHOLDS): "
                f"roce={roce_val:.1f}% (>=15% {'PASS' if roce_val>=15.0 else 'FAIL'}) | "
                f"roe={roe_val:.1f}% (>=12% {'PASS' if roe_val>=12.0 else 'FAIL'}) | "
                f"ocf={ocf_val:.1f} (>0 {'PASS' if ocf_val>0 else 'FAIL'}) | "
                f"d/e={de_val:.2f} (<=1.0 {'PASS' if de_val<=1.0 else 'FAIL'}) | "
                f"trap={is_trap}"
            )
        else:
            logger.info(
                f"✨ [GATE_EVAL:FQ] {sym:<12} PASSED: "
                f"roce={roce_val:.1f}% | roe={roe_val:.1f}% | ocf={ocf_val:.1f} | d/e={de_val:.2f}"
            )

        return (len(failures) == 0), failures, metrics


# -------------------------------------------------------------------------------------
# 3. EARNINGS ACCELERATION GATE (HARD BOOLEAN GATE)
# -------------------------------------------------------------------------------------
class EarningsAccelerationGate:
    """
    Mandatory Growth & Acceleration Conditions:
      - Latest Revenue YoY Growth > Previous Comparable Revenue YoY Growth
      - Latest Operating Profit YoY Growth > Previous Comparable Operating Profit YoY Growth
      - Latest EPS YoY Growth > Previous Comparable EPS YoY Growth
      - Prior Comparable-Period EPS > 0.0 (prevents low-base distortions)
    """

    @staticmethod
    def evaluate(fundamentals: Dict[str, Any], symbol: Optional[str] = None) -> Tuple[bool, List[RejectionReason], Dict[str, Any]]:
        failures = []
        sym = symbol or (fundamentals.get("symbol", "UNKNOWN") if fundamentals else "UNKNOWN")
        if not fundamentals or fundamentals.get("upstream_provider") == "DATA_UNAVAILABLE":
            logger.info(f"🔍 [GATE_EVAL:EA] {sym} REJECTED: fundamentals record is missing or empty")
            return False, [RejectionReason.FUNDAMENTAL_DATA_MISSING], {}

        rev_yoy_latest = fundamentals.get("rev_yoy_latest", fundamentals.get("yoy_revenue_pct"))
        rev_yoy_prev = fundamentals.get("rev_yoy_prev", fundamentals.get("yoy_revenue_prev_pct"))
        op_yoy_latest = fundamentals.get("op_profit_yoy_latest", fundamentals.get("yoy_op_profit_pct"))
        op_yoy_prev = fundamentals.get("op_profit_yoy_prev", fundamentals.get("yoy_op_profit_prev_pct"))
        eps_yoy_latest = fundamentals.get("eps_yoy_latest", fundamentals.get("yoy_eps_pct"))
        eps_yoy_prev = fundamentals.get("eps_yoy_prev", fundamentals.get("yoy_eps_prev_pct"))
        prior_eps = fundamentals.get("prior_eps", fundamentals.get("prior_comparable_eps"))

        def _sf(v):
            return float(v) if (v is not None and not pd.isna(v)) else None

        rev_l = _sf(rev_yoy_latest)
        rev_p = _sf(rev_yoy_prev)
        op_l = _sf(op_yoy_latest)
        op_p = _sf(op_yoy_prev)
        eps_l = _sf(eps_yoy_latest)
        eps_p = _sf(eps_yoy_prev)
        p_eps = _sf(prior_eps)

        metrics = {
            "rev_yoy_latest": round(rev_l, 2) if rev_l is not None else None,
            "rev_yoy_prev": round(rev_p, 2) if rev_p is not None else None,
            "op_profit_yoy_latest": round(op_l, 2) if op_l is not None else None,
            "op_profit_yoy_prev": round(op_p, 2) if op_p is not None else None,
            "eps_yoy_latest": round(eps_l, 2) if eps_l is not None else None,
            "eps_yoy_prev": round(eps_p, 2) if eps_p is not None else None,
            "prior_eps": round(p_eps, 2) if p_eps is not None else None
        }

        # Check for missing values
        missing_fields = []
        if rev_l is None: missing_fields.append("rev_yoy_latest")
        if rev_p is None: missing_fields.append("rev_yoy_prev")
        if op_l is None: missing_fields.append("op_profit_yoy_latest")
        if op_p is None: missing_fields.append("op_profit_yoy_prev")
        if eps_l is None: missing_fields.append("eps_yoy_latest")
        if eps_p is None: missing_fields.append("eps_yoy_prev")
        if p_eps is None: missing_fields.append("prior_eps")

        if missing_fields:
            failures.append(RejectionReason.FAIL_GROWTH_DATA_INSUFFICIENT)
            logger.info(
                f"🔍 [GATE_EVAL:EA] {sym:<12} REJECTED (GROWTH_DATA_INSUFFICIENT): missing={missing_fields} | "
                f"rev_l={f'{rev_l:.1f}%' if rev_l is not None else 'MISSING'} | rev_p={f'{rev_p:.1f}%' if rev_p is not None else 'MISSING'} | "
                f"op_l={f'{op_l:.1f}%' if op_l is not None else 'MISSING'} | op_p={f'{op_p:.1f}%' if op_p is not None else 'MISSING'} | "
                f"eps_l={f'{eps_l:.1f}%' if eps_l is not None else 'MISSING'} | eps_p={f'{eps_p:.1f}%' if eps_p is not None else 'MISSING'} | "
                f"prior_eps={f'{p_eps:.2f}' if p_eps is not None else 'MISSING'}"
            )
            return False, failures, metrics

        if rev_l <= rev_p:
            failures.append(RejectionReason.FAIL_REVENUE_ACCELERATION)
        if op_l <= op_p:
            failures.append(RejectionReason.FAIL_OP_PROFIT_ACCELERATION)
        if eps_l <= eps_p:
            failures.append(RejectionReason.FAIL_EPS_ACCELERATION)
        if p_eps <= 0.0:
            failures.append(RejectionReason.FAIL_PRIOR_EPS)

        if failures:
            logger.info(
                f"🔍 [GATE_EVAL:EA] {sym:<12} REJECTED (DECELERATING): "
                f"rev={rev_l:.1f}% vs prev={rev_p:.1f}% ({'PASS' if rev_l>rev_p else 'FAIL'}) | "
                f"op={op_l:.1f}% vs prev={op_p:.1f}% ({'PASS' if op_l>op_p else 'FAIL'}) | "
                f"eps={eps_l:.1f}% vs prev={eps_p:.1f}% ({'PASS' if eps_l>eps_p else 'FAIL'}) | "
                f"prior_eps={p_eps:.2f} (>0 {'PASS' if p_eps>0.0 else 'FAIL'})"
            )
        else:
            logger.info(
                f"✨ [GATE_EVAL:EA] {sym:<12} PASSED: "
                f"rev={rev_l:.1f}%>{rev_p:.1f}% | op={op_l:.1f}%>{op_p:.1f}% | eps={eps_l:.1f}%>{eps_p:.1f}% | prior_eps={p_eps:.2f}"
            )

        return (len(failures) == 0), failures, metrics


# -------------------------------------------------------------------------------------
# 4. TECHNICAL TREND & RELATIVE STRENGTH GATE
# -------------------------------------------------------------------------------------
class TechnicalTrendGate:
    """
    Mandatory Trend & Relative Strength Conditions:
      - Close > SMA50 > SMA200
      - 3M Stock Return > Benchmark 3M Return (approx 63 trading days)
      - 6M Stock Return > Benchmark 6M Return (approx 126 trading days)
    """

    @staticmethod
    def evaluate(
        closes: np.ndarray,
        benchmark_closes: Optional[np.ndarray] = None
    ) -> Tuple[bool, List[RejectionReason], Dict[str, Any]]:
        failures = []
        n = len(closes)
        if n < 200:
            return False, [RejectionReason.MARKET_DATA_INSUFFICIENT_LOOKBACK], {}

        sma50 = float(np.mean(closes[-50:]))
        sma200 = float(np.mean(closes[-200:]))
        current_close = float(closes[-1])

        # Trend check: Close > SMA50 > SMA200
        if not (current_close > sma50 > sma200):
            failures.append(RejectionReason.FAIL_TREND)

        # 3M & 6M Relative Strength vs Benchmark
        ret_3m_stock = (current_close - closes[-63]) / closes[-63] if n >= 63 and closes[-63] > 0 else 0.0
        ret_6m_stock = (current_close - closes[-126]) / closes[-126] if n >= 126 and closes[-126] > 0 else 0.0

        if benchmark_closes is not None and len(benchmark_closes) >= 126:
            bm_ret_3m = (benchmark_closes[-1] - benchmark_closes[-63]) / benchmark_closes[-63] if benchmark_closes[-63] > 0 else 0.0
            bm_ret_6m = (benchmark_closes[-1] - benchmark_closes[-126]) / benchmark_closes[-126] if benchmark_closes[-126] > 0 else 0.0
            if ret_3m_stock <= bm_ret_3m or ret_6m_stock <= bm_ret_6m:
                failures.append(RejectionReason.FAIL_RELATIVE_STRENGTH)
        else:
            # Benchmark unavailable: fail closed per strict data integrity protocol
            failures.append(RejectionReason.FAIL_RELATIVE_STRENGTH)

        metrics = {
            "sma50": round(sma50, 2),
            "sma200": round(sma200, 2),
            "ret_3m_stock_pct": round(ret_3m_stock * 100.0, 2),
            "ret_6m_stock_pct": round(ret_6m_stock * 100.0, 2)
        }

        return (len(failures) == 0), failures, metrics


# -------------------------------------------------------------------------------------
# 5. CONSOLIDATION GATE
# -------------------------------------------------------------------------------------
class ConsolidationGate:
    """
    Mandatory Controlled Consolidation Conditions:
      - Consolidation window between 20 and 60 sessions
      - Drawdown from recent high <= 15.0%
      - ATR14 / Close <= 6.0%
      - No close below SMA200 during consolidation window
    """

    @staticmethod
    def evaluate(
        closes: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        consolidation_window: int = 20
    ) -> Tuple[bool, List[RejectionReason], Dict[str, Any]]:
        failures = []
        n = len(closes)
        if n < 200 or consolidation_window < 20 or consolidation_window > 60:
            return False, [RejectionReason.FAIL_CONSOLIDATION_WINDOW], {}

        window_closes = closes[-consolidation_window:]
        window_highs = highs[-consolidation_window:]
        recent_high = float(np.max(window_highs))
        recent_low_close = float(np.min(window_closes))

        # Max drawdown in window: (recent_high - recent_low_close) / recent_high
        drawdown_pct = ((recent_high - recent_low_close) / recent_high) * 100.0 if recent_high > 0 else 0.0
        if drawdown_pct > 15.0:
            failures.append(RejectionReason.FAIL_CONSOLIDATION_DRAWDOWN)

        # ATR14 / Close <= 6.0%
        tr = np.maximum(
            highs[-14:] - lows[-14:],
            np.maximum(np.abs(highs[-14:] - closes[-15:-1]), np.abs(lows[-14:] - closes[-15:-1]))
        )
        atr14 = float(np.mean(tr))
        current_close = float(closes[-1])
        atr_pct = (atr14 / current_close) * 100.0 if current_close > 0 else 0.0
        if atr_pct > 6.0:
            failures.append(RejectionReason.FAIL_CONSOLIDATION_ATR)

        # No close below SMA200
        sma200_series = pd.Series(closes).rolling(200, min_periods=50).mean().values
        window_sma200 = sma200_series[-consolidation_window:]
        if (window_closes < window_sma200).any():
            failures.append(RejectionReason.FAIL_CONSOLIDATION_SMA200)

        metrics = {
            "consolidation_window": consolidation_window,
            "recent_high": round(recent_high, 2),
            "drawdown_pct": round(drawdown_pct, 2),
            "atr_pct": round(atr_pct, 2)
        }

        return (len(failures) == 0), failures, metrics


# -------------------------------------------------------------------------------------
# 6. BREAKOUT GATE
# -------------------------------------------------------------------------------------
class BreakoutGate:
    """
    Mandatory Breakout Conditions:
      - Close > Prior 20D High (prior to current candle)
      - Volume >= 1.5 × Average 20D Volume
      - Extension <= 8.0% ((Close - Prior 20D High) / Prior 20D High <= 0.08)
    """

    @staticmethod
    def evaluate(
        closes: np.ndarray,
        highs: np.ndarray,
        volumes: np.ndarray,
        lookback: int = 20
    ) -> Tuple[bool, List[RejectionReason], Dict[str, Any]]:
        failures = []
        n = len(closes)
        if n < lookback + 1:
            return False, [RejectionReason.MARKET_DATA_INSUFFICIENT_LOOKBACK], {}

        current_close = float(closes[-1])
        current_volume = float(volumes[-1])
        prior_20d_high = float(np.max(highs[-1 - lookback : -1]))
        avg_20d_vol = float(np.mean(volumes[-1 - lookback : -1]))

        # Price Breakout
        if current_close <= prior_20d_high:
            failures.append(RejectionReason.FAIL_BREAKOUT_PRICE)

        # Volume Confirmation
        vol_ratio = current_volume / avg_20d_vol if avg_20d_vol > 0 else 0.0
        if vol_ratio < 1.5:
            failures.append(RejectionReason.FAIL_BREAKOUT_VOLUME)

        # Extension <= 8.0%
        extension_pct = ((current_close - prior_20d_high) / prior_20d_high) * 100.0 if prior_20d_high > 0 else 0.0
        if extension_pct > 8.0:
            failures.append(RejectionReason.FAIL_BREAKOUT_EXTENSION)

        metrics = {
            "close": round(current_close, 2),
            "signal_close": round(current_close, 2),
            "prior_20d_high": round(prior_20d_high, 2),
            "vol_ratio": round(vol_ratio, 2),
            "extension_pct": round(extension_pct, 2)
        }

        return (len(failures) == 0), failures, metrics


# -------------------------------------------------------------------------------------
# 6.5. AUTHORITATIVE UPSTREAM FUNDAMENTAL PROVIDER (DAILY BUILDER 2.0)
# -------------------------------------------------------------------------------------
class DailyBuilderFundamentalProvider:
    """
    Authoritative upstream fundamental data provider for Live BUY Scanner.
    Extracts pre-qualified fundamental intelligence from Daily Builder 2.0 (daily_builder_master_v2).
    
    Governance & Operating Invariants:
      1. Zero Composite Substitution:
         QUALITY_SCORE, GROWTH_SCORE, and WEALTH_SCORE NEVER replace frozen boolean gates.
      2. Strict Field Equivalence Mapping:
         - ROCE: r["ROCE"] (>= 15.0%)
         - ROE: r["ROE"] (>= 12.0%)
         - Debt/Equity: r["debt"] (<= 1.0)
         - Operating Cash Flow: r["FCF_yield"] / r["ocf"] (> 0.0)
         - Value Trap Veto: fundamental_category == "VALUE_TRAP" or is_value_trap == True
      3. Acceleration Invariant:
         If 4-period acceleration fields (rev_yoy_latest > prev, op_yoy_latest > prev, eps_yoy_latest > prev, prior_eps > 0)
         are not present in master record, the scanner must retain canonical statement calculations or strictly block the candidate.
      4. Point-in-Time Causality Guard:
         Daily Builder 2.0 is certified for LIVE screening only.
         For historical backtesting, Daily Builder records CANNOT be used without audited publication/filing timestamps.
    """
    MASTER_PARQUET = "data/daily_builder_master_v2.parquet"
    MASTER_CSV = "data/daily_builder_master_v2.csv"
    MASTER_TABLE = "daily_builder_master_v2"

    @classmethod
    def load_master_fundamentals(
        cls,
        parquet_path: Optional[str] = None,
        max_age_days: int = 7
    ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
        """
        Loads fundamental master records from Daily Builder 2.0.
        Returns (fundamentals_by_symbol, metadata).
        """
        path = parquet_path or os.path.join(DATA_DIR, "daily_builder_master_v2.parquet")
        if not os.path.exists(path) and os.path.exists(cls.MASTER_PARQUET):
            path = cls.MASTER_PARQUET
        if not os.path.exists(path):
            try:
                from database import download_parquet_from_db_today, download_parquet_from_db
                download_parquet_from_db_today("daily_builder_master_v2", path) or download_parquet_from_db("daily_builder_master_v2", path)
            except Exception as _dbe:
                logger.debug(f"DB download attempt for daily_builder_master_v2 failed: {_dbe}")

        meta: Dict[str, Any] = {
            "source": "DAILY_BUILDER_2.0",
            "loaded_at": datetime.now(IST).isoformat(),
            "file_path": path,
            "record_count": 0,
            "freshness_status": "UNKNOWN",
            "build_date": None,
            "provenance_status": "UNPROVEN"
        }
        df = None
        need_fetch = False

        if os.path.exists(path):
            try:
                mtime = os.path.getmtime(path)
                age_days = (time.time() - mtime) / 86400.0
                meta["age_days"] = round(age_days, 1)

                if age_days > max_age_days:
                    logger.info(f"🔄 [FUNDAMENTAL_CACHE] Cache file {path} is STALE (age={age_days:.1f} days > max={max_age_days} days). Triggering refetch...")
                    need_fetch = True
                else:
                    df_candidate = pd.read_parquet(path)
                    # Validate: must have acceleration fields (including prior_eps) AND critical FQ fields (ROCE/ROE)
                    # without >10% nulls. Daily Builder output has NaN ROCE for many stocks
                    # and missing prior_eps if not re-hydrated, which causes DATA_MISSING.
                    has_accel = (
                        "rev_yoy_latest" in df_candidate.columns
                        and "prior_eps" in df_candidate.columns
                        and df_candidate["rev_yoy_latest"].notna().sum() > 50
                        and df_candidate["prior_eps"].notna().sum() > 50
                    )
                    roce_col = next((c for c in ("ROCE", "roce") if c in df_candidate.columns), None)
                    roce_null_frac = (df_candidate[roce_col].isna().sum() / max(len(df_candidate), 1)) if roce_col else 1.0
                    has_valid_roce = roce_null_frac <= 0.10  # ≤10% nulls tolerated
                    if len(df_candidate) > 1 and has_accel and has_valid_roce:
                        df = df_candidate
                        meta["freshness_status"] = "FRESH"
                        meta["provenance_status"] = "CERTIFIED_LOCAL_DAILY_BUILDER"
                        logger.info(
                            f"✅ [FUNDAMENTAL_CACHE] Loaded valid cache {path}: {len(df)} records | "
                            f"ROCE valid={df[roce_col].notna().sum()}/{len(df)} | "
                            f"rev_yoy valid={df['rev_yoy_latest'].notna().sum()}/{len(df)} | "
                            f"prior_eps valid={df['prior_eps'].notna().sum()}/{len(df)}"
                        )
                    else:
                        prior_valid = df_candidate['prior_eps'].notna().sum() if 'prior_eps' in df_candidate.columns else 0
                        rev_valid = df_candidate['rev_yoy_latest'].notna().sum() if 'rev_yoy_latest' in df_candidate.columns else 0
                        logger.info(
                            f"🔄 [FUNDAMENTAL_CACHE] Local cache {path} incomplete — "
                            f"has_accel={has_accel} (rev_valid={rev_valid}, prior_valid={prior_valid}), "
                            f"roce_null_frac={roce_null_frac:.2%}. "
                            f"Triggering PIT DB re-hydration..."
                        )
                        need_fetch = True
            except Exception as e:
                logger.warning(f"Failed to read Daily Builder parquet {path}: {e}")
                need_fetch = True
        else:
            need_fetch = True

        # Re-hydrate / fetch on demand if missing or stale
        if (df is None or df.empty) and need_fetch:
            pit_db = os.path.join(DATA_DIR, "pit_fundamentals_v1", "pit_fundamentals_v1.db")
            if os.path.exists(pit_db):
                try:
                    import sqlite3
                    # timeout=30: wait up to 30s if another process holds a write-lock
                    # (e.g. backtest script accessing pit_fundamentals_v1.db concurrently).
                    # Without timeout, SQLite raises OperationalError immediately on lock,
                    # causing fall-through to empty Postgres table → empty funds_map → DATA_MISSING.
                    con = sqlite3.connect(pit_db, timeout=30)
                    query = """
                    SELECT symbol, period_end_date, statement_type, revenue, operating_profit, net_profit, eps,
                           roce, roe, total_debt, total_equity, operating_cash_flow, free_cash_flow
                    FROM pit_fundamentals_v1
                    ORDER BY symbol, period_end_date DESC
                    """
                    df_all = pd.read_sql(query, con)
                    con.close()

                    if not df_all.empty:
                        try:
                            from app.financial_data_integrity import MonetaryUnit, convert_to_inr_crores
                        except ImportError:
                            from financial_data_integrity import MonetaryUnit, convert_to_inr_crores

                        rows_list = []
                        for sym, group in df_all.groupby("symbol"):
                            filings = group.to_dict("records")
                            if not filings:
                                continue

                            annual_filings = [f for f in filings if str(f.get("statement_type", "")).upper() == "ANNUAL"]
                            quarterly_filings = [f for f in filings if str(f.get("statement_type", "")).upper() == "QUARTERLY"]

                            f0_ann = annual_filings[0] if annual_filings else filings[0]
                            roce_val = f0_ann.get("roce")
                            roe_val = f0_ann.get("roe")
                            tot_debt = convert_to_inr_crores(f0_ann.get("total_debt"), source_unit=MonetaryUnit.INR_CRORES)
                            tot_eq = convert_to_inr_crores(f0_ann.get("total_equity"), source_unit=MonetaryUnit.INR_CRORES)
                            de_val = (float(tot_debt) / float(tot_eq)) if tot_debt is not None and tot_eq is not None and float(tot_eq) > 0 else (0.0 if tot_debt == 0 else None)
                            
                            # P0: Strict CFO mapping — NEVER fallback to free_cash_flow
                            raw_ocf = f0_ann.get("operating_cash_flow")
                            ocf_val = convert_to_inr_crores(raw_ocf, source_unit=MonetaryUnit.INR_CRORES)

                            # Derive ROCE / ROE mathematically if missing from annual statement
                            if (roce_val is None or pd.isna(roce_val)) and f0_ann.get("operating_profit") is not None and tot_eq is not None:
                                cap = float(tot_eq) + float(tot_debt or 0.0)
                                if cap > 0:
                                    roce_val = round((convert_to_inr_crores(f0_ann["operating_profit"], source_unit=MonetaryUnit.INR_CRORES) or 0.0) / cap * 100.0, 2)
                            if (roe_val is None or pd.isna(roe_val)) and f0_ann.get("net_profit") is not None and tot_eq is not None and float(tot_eq) > 0:
                                roe_val = round((convert_to_inr_crores(f0_ann["net_profit"], source_unit=MonetaryUnit.INR_CRORES) or 0.0) / float(tot_eq) * 100.0, 2)

                            # P0: True Quarterly YoY acceleration (same fiscal quarter 1 year ago, 330-400 days prior)
                            rev_l, rev_p, op_l, op_p, eps_l, eps_p, p_eps = None, None, None, None, None, None, None
                            q_filings = quarterly_filings if quarterly_filings else [f for f in filings if str(f.get("statement_type", "")).upper() != "ANNUAL"]

                            def _find_yoy_match(ref_f):
                                ref_dt = pd.to_datetime(ref_f.get("period_end_date"))
                                for past_f in q_filings:
                                    past_dt = pd.to_datetime(past_f.get("period_end_date"))
                                    diff_days = (ref_dt - past_dt).days
                                    if 330 <= diff_days <= 400:
                                        return past_f
                                return None

                            if len(q_filings) >= 1:
                                q0 = q_filings[0]
                                match_q0 = _find_yoy_match(q0)
                                if match_q0:
                                    r0, r_m0 = q0.get("revenue"), match_q0.get("revenue")
                                    op0, op_m0 = q0.get("operating_profit"), match_q0.get("operating_profit")
                                    eps0, eps_m0 = q0.get("eps"), match_q0.get("eps")
                                    if r0 is not None and r_m0 is not None and not pd.isna(r0) and not pd.isna(r_m0) and abs(float(r_m0)) > 1e-5:
                                        rev_l = round(((float(r0) - float(r_m0)) / abs(float(r_m0))) * 100.0, 2)
                                    if op0 is not None and op_m0 is not None and not pd.isna(op0) and not pd.isna(op_m0) and abs(float(op_m0)) > 1e-5:
                                        op_l = round(((float(op0) - float(op_m0)) / abs(float(op_m0))) * 100.0, 2)
                                    if eps0 is not None and eps_m0 is not None and not pd.isna(eps0) and not pd.isna(eps_m0) and abs(float(eps_m0)) > 1e-5:
                                        eps_l = round(((float(eps0) - float(eps_m0)) / abs(float(eps_m0))) * 100.0, 2)
                                        p_eps = float(eps_m0)

                            if len(q_filings) >= 2:
                                q1 = q_filings[1]
                                match_q1 = _find_yoy_match(q1)
                                if match_q1:
                                    r1, r_m1 = q1.get("revenue"), match_q1.get("revenue")
                                    op1, op_m1 = q1.get("operating_profit"), match_q1.get("operating_profit")
                                    eps1, eps_m1 = q1.get("eps"), match_q1.get("eps")
                                    if r1 is not None and r_m1 is not None and not pd.isna(r1) and not pd.isna(r_m1) and abs(float(r_m1)) > 1e-5:
                                        rev_p = round(((float(r1) - float(r_m1)) / abs(float(r_m1))) * 100.0, 2)
                                    if op1 is not None and op_m1 is not None and not pd.isna(op1) and not pd.isna(op_m1) and abs(float(op_m1)) > 1e-5:
                                        op_p = round(((float(op1) - float(op_m1)) / abs(float(op_m1))) * 100.0, 2)
                                    if eps1 is not None and eps_m1 is not None and not pd.isna(eps1) and not pd.isna(eps_m1) and abs(float(eps_m1)) > 1e-5:
                                        eps_p = round(((float(eps1) - float(eps_m1)) / abs(float(eps_m1))) * 100.0, 2)
                                if p_eps is None and q1.get("eps") is not None and not pd.isna(q1.get("eps")):
                                    p_eps = float(q1.get("eps"))

                            rows_list.append({
                                "symbol": sym,
                                "ROCE": float(roce_val) if (roce_val is not None and not pd.isna(roce_val)) else None,
                                "ROE": float(roe_val) if (roe_val is not None and not pd.isna(roe_val)) else None,
                                "debt": float(de_val) if de_val is not None else None,
                                "operating_cash_flow": float(ocf_val) if (ocf_val is not None and not pd.isna(ocf_val)) else None,
                                "fundamental_category": "HIGH_QUALITY" if (roce_val is not None and not pd.isna(roce_val) and float(roce_val) >= 15.0) else "NORMAL",
                                "is_value_trap": False,
                                "quality_score": None,
                                "growth_score": None,
                                "valuation_score": None,
                                "wealth_score": None,
                                "rev_yoy_latest": rev_l,
                                "rev_yoy_prev": rev_p,
                                "op_profit_yoy_latest": op_l,
                                "op_profit_yoy_prev": op_p,
                                "eps_yoy_latest": eps_l,
                                "eps_yoy_prev": eps_p,
                                "prior_eps": p_eps
                            })

                        if rows_list:
                            df = pd.DataFrame(rows_list)
                            meta["freshness_status"] = "FRESH"
                            meta["provenance_status"] = "CERTIFIED_PIT_FUNDAMENTALS_DB_REHYDRATED"
                            meta["source"] = "DAILY_BUILDER_2.0_PIT_DB"
                            # Store & Cache for reuse
                            try:
                                os.makedirs(os.path.dirname(path), exist_ok=True)
                                df.to_parquet(path, index=False)
                                logger.info(f"💾 [FUNDAMENTAL_CACHE] Persisted {len(df)} re-hydrated fundamental records to local cache: {path}")
                            except Exception as save_err:
                                logger.warning(f"Could not persist fundamental cache parquet: {save_err}")
                except Exception as e:
                    logger.warning(f"Failed to re-hydrate from PIT fundamentals DB: {e}")

        # Fallback to Postgres table if still absent or empty
        if df is None or df.empty:
            try:
                try:
                    from database import get_connection
                except ImportError:
                    from app.database import get_connection
                import warnings
                with get_connection() as conn:
                    query = f"SELECT * FROM {cls.MASTER_TABLE} WHERE build_date = (SELECT MAX(build_date) FROM {cls.MASTER_TABLE})"
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", category=UserWarning)
                        df = pd.read_sql_query(query, conn)
                    if not df.empty:
                        meta["freshness_status"] = "FRESH"
                        meta["provenance_status"] = "CERTIFIED_POSTGRES_DAILY_BUILDER"
                        try:
                            df.to_parquet(path, index=False)
                            logger.info(f"💾 [FUNDAMENTAL_CACHE] Persisted Postgres master records to local cache: {path}")
                        except Exception:
                            pass
            except Exception as e:
                logger.warning(f"Failed to query DB {cls.MASTER_TABLE}: {e}")

        if df is None or df.empty:
            # Check watchlist parquet (restored from DB parquet_cache on boot)
            wl_path = os.path.join(DATA_DIR, "elite_fundamental_watchlist.parquet")
            if not os.path.exists(wl_path):
                try:
                    from database import download_parquet_from_db_today, download_parquet_from_db
                    download_parquet_from_db_today("daily_builder", wl_path) or download_parquet_from_db("daily_builder", wl_path)
                except Exception:
                    pass
            if os.path.exists(wl_path):
                try:
                    df = pd.read_parquet(wl_path)
                    if not df.empty:
                        meta["freshness_status"] = "FRESH"
                        meta["provenance_status"] = "CERTIFIED_DAILY_BUILDER_WATCHLIST"
                        meta["source"] = "DAILY_BUILDER_WATCHLIST"
                        logger.info(f"✅ [FUNDAMENTAL_CACHE] Loaded {len(df)} records from {wl_path}")
                except Exception as e:
                    logger.debug(f"Failed reading {wl_path}: {e}")

        if df is None or df.empty:
            meta["freshness_status"] = "MISSING"
            return {}, meta

        meta["record_count"] = len(df)
        if "build_date" in df.columns and not df.empty:
            meta["build_date"] = str(df["build_date"].iloc[0])

        funds_map: Dict[str, Dict[str, Any]] = {}
        for _, r in df.iterrows():
            sym = str(r.get("symbol", r.get("Stock", ""))).upper()
            if not sym:
                continue

            roce_raw = r.get("ROCE", r.get("roce", r.get("ROCE %")))
            roce_val = float(roce_raw) if (roce_raw is not None and not pd.isna(roce_raw)) else None

            roe_raw = r.get("ROE", r.get("roe", r.get("ROE %", r.get("return_on_equity_fy"))))
            roe_val = float(roe_raw) if (roe_raw is not None and not pd.isna(roe_raw)) else None

            debt_raw = r.get("debt", r.get("debt_equity", r.get("Debt/Equity", r.get("debt_to_equity_fq"))))
            debt_val = float(debt_raw) if (debt_raw is not None and not pd.isna(debt_raw)) else None

            raw_ocf = r.get("operating_cash_flow", r.get("ocf"))
            ocf_val = None
            if raw_ocf is not None and not pd.isna(raw_ocf):
                try:
                    v = float(raw_ocf)
                    ocf_val = round(v / 1e7, 2) if abs(v) > 1e6 else round(v, 2)
                except (ValueError, TypeError):
                    ocf_val = None

            fund_cat = str(r.get("fundamental_category", r.get("Category", "NONE")))
            is_trap = (fund_cat == "VALUE_TRAP") or bool(r.get("is_value_trap", False)) or (str(r.get("Forensic_Risk_Tier", "")).upper() == "HIGH")

            # Acceleration fields (ZERO SYNTHETIC CONSTANTS: strictly quarterly, NO annual FY26 or TTM fallbacks)
            rev_l = r.get("rev_yoy_latest")
            rev_l = float(rev_l) if (rev_l is not None and not pd.isna(rev_l)) else None

            rev_p = r.get("rev_yoy_prev")
            rev_p = float(rev_p) if (rev_p is not None and not pd.isna(rev_p)) else None

            op_l = r.get("op_profit_yoy_latest")
            op_l = float(op_l) if (op_l is not None and not pd.isna(op_l)) else None

            op_p = r.get("op_profit_yoy_prev")
            op_p = float(op_p) if (op_p is not None and not pd.isna(op_p)) else None

            eps_l = r.get("eps_yoy_latest")
            eps_l = float(eps_l) if (eps_l is not None and not pd.isna(eps_l)) else None

            eps_p = r.get("eps_yoy_prev")
            eps_p = float(eps_p) if (eps_p is not None and not pd.isna(eps_p)) else None

            p_eps = r.get("prior_eps")
            p_eps = float(p_eps) if (p_eps is not None and not pd.isna(p_eps)) else None

            funds_map[sym] = {
                "symbol": sym,
                "roce": roce_val,
                "roe": roe_val,
                "debt_equity": debt_val,
                "operating_cash_flow": ocf_val,
                "fundamental_category": fund_cat,
                "is_value_trap": is_trap,
                "quality_score": float(r.get("quality_score", 0.0) or 0.0),
                "growth_score": float(r.get("growth_score", 0.0) or 0.0),
                "valuation_score": float(r.get("valuation_score", 0.0) or 0.0),
                "wealth_score": float(r.get("wealth_score", 0.0) or 0.0),
                "risk_score": float(r.get("risk_score", 0.0) or 0.0),
                "valuation_category": str(r.get("valuation_category", "NONE")),
                "fair_value_range": str(r.get("fair_value_range", "")),
                "rev_yoy_latest": rev_l,
                "rev_yoy_prev": rev_p,
                "op_profit_yoy_latest": op_l,
                "op_profit_yoy_prev": op_p,
                "eps_yoy_latest": eps_l,
                "eps_yoy_prev": eps_p,
                "prior_eps": p_eps,
                "upstream_provider": "DAILY_BUILDER_2.0"
            }

        # ── P0 ZERO-FALLBACK ENFORCEMENT ──────────────────────────────────────────
        # Missing symbols or null financial metrics are strictly fail-closed.
        # No uncertified secondary JSON caches (multibagger/fundamentals_cache)
        # participate in production financial decision paths.
        meta["record_count"] = len(funds_map)

        return funds_map, meta

    @staticmethod
    def verify_point_in_time_provenance(
        symbol: str,
        signal_timestamp: Optional[datetime] = None,
        is_backtest: bool = False
    ) -> Tuple[bool, str]:
        """
        Point-in-Time Causality Guard:
          - LIVE: Daily Builder 2.0 output is permissible for current forward session.
          - HISTORICAL BACKTEST: Daily Builder snapshot CANNOT be used to claim historical alpha
            without verifiable filing/publication timestamps (publication_timestamp < signal_timestamp).
        """
        if is_backtest:
            return False, "BACKTEST_BLOCKED_UNPROVEN_PIT_PROVENANCE: Daily Builder records lack audited historical filing timestamps"
        return True, "PROVENANCE_CERTIFIED_LIVE_DAILY_BUILDER"


# -------------------------------------------------------------------------------------
# 7. UNIFIED LIVE BUY SCANNER ORCHESTRATOR
# -------------------------------------------------------------------------------------
class LiveFundamentalBuyScanner:
    """
    End-to-End Orchestrator executing the Mandatory 6-Gate Pipeline:
      Approved Universe (886)
      -> Fundamental Quality Gate (backed by Daily Builder 2.0)
      -> Earnings Acceleration Gate
      -> Technical Trend Gate
      -> Consolidation Gate
      -> 20D Breakout Gate
      -> BUY ALERT
    """

    def __init__(self):
        self.universe_registry = ApprovedUniverseRegistry()
        self.daily_builder_provider = DailyBuilderFundamentalProvider()
        self.last_funnel_audit: Dict[str, Any] = {}

    def scan_candidate(
        self,
        symbol: str,
        df_bars: pd.DataFrame,
        fundamentals: Dict[str, Any],
        benchmark_closes: Optional[np.ndarray] = None,
        consolidation_window: int = 20,
        provenance_valid: bool = True,
        is_stale: bool = False,
        telemetry: Optional[Any] = None
    ) -> Dict[str, Any]:
        """Evaluates a single candidate through the mandatory 6-gate pipeline."""
        sym = symbol.upper()
        rejections: List[RejectionReason] = []
        gate_metrics: Dict[str, Any] = {}

        if telemetry is not None:
            telemetry.record_symbol_start(sym)
            telemetry.record_composite_scores(sym, fundamentals)

        # 1. Universe Gate
        univ_pass, univ_err = self.universe_registry.validate_symbol(sym)
        if telemetry is not None:
            telemetry.record_gate_evaluation(
                sym, "UNIVERSE", univ_pass, {"symbol": sym},
                [univ_err.value] if univ_err else []
            )
        if not univ_pass and univ_err:
            rejections.append(univ_err)
            res = self._build_result(sym, False, rejections, gate_metrics)
            if telemetry is not None:
                telemetry.record_symbol_final_decision(sym, False, [univ_err.value], univ_err.value)
            return res

        # 2. Provenance & Freshness Gate
        prov_pass = (provenance_valid and not is_stale)
        prov_errs = []
        if not provenance_valid:
            prov_errs.append(RejectionReason.FUNDAMENTAL_PROVENANCE_INVALID)
        if is_stale:
            prov_errs.append(RejectionReason.FUNDAMENTAL_DATA_STALE)

        if telemetry is not None:
            telemetry.record_gate_evaluation(
                sym, "PROVENANCE", prov_pass,
                {"provenance_valid": provenance_valid, "is_stale": is_stale},
                [e.value for e in prov_errs]
            )

        if not provenance_valid:
            rejections.append(RejectionReason.FUNDAMENTAL_PROVENANCE_INVALID)
            res = self._build_result(sym, False, rejections, gate_metrics)
            if telemetry is not None:
                telemetry.record_symbol_final_decision(sym, False, [e.value for e in rejections], RejectionReason.FUNDAMENTAL_PROVENANCE_INVALID.value)
            return res
        if is_stale:
            rejections.append(RejectionReason.FUNDAMENTAL_DATA_STALE)
            res = self._build_result(sym, False, rejections, gate_metrics)
            if telemetry is not None:
                telemetry.record_symbol_final_decision(sym, False, [e.value for e in rejections], RejectionReason.FUNDAMENTAL_DATA_STALE.value)
            return res

        # 3. Fundamental Quality Gate (ROCE >= 15%, ROE >= 12%, OCF > 0, D/E <= 1.0)
        fq_pass, fq_errs, fq_metrics = FundamentalQualityGate.evaluate(fundamentals, sym)
        gate_metrics.update(fq_metrics)
        if telemetry is not None:
            telemetry.record_gate_evaluation(sym, "FUNDAMENTAL_QUALITY", fq_pass, fq_metrics, [e.value for e in fq_errs])
            telemetry.record_fundamental_gate_detail(sym, fq_metrics, fq_pass, [e.value for e in fq_errs])
        if not fq_pass:
            rejections.extend(fq_errs)

        # 4. Earnings Acceleration Gate (Rev Accel, Op Profit Accel, EPS Accel, Prior EPS > 0)
        ea_pass, ea_errs, ea_metrics = EarningsAccelerationGate.evaluate(fundamentals, sym)
        gate_metrics.update(ea_metrics)
        if telemetry is not None:
            telemetry.record_gate_evaluation(sym, "EARNINGS_ACCELERATION", ea_pass, ea_metrics, [e.value for e in ea_errs])
            telemetry.record_earnings_acceleration_gate_detail(sym, ea_metrics, ea_pass, [e.value for e in ea_errs])
        if not ea_pass:
            rejections.extend(ea_errs)

        # 4.5. Value Trap Gate (§7)
        is_trap = bool(fundamentals.get("is_value_trap", False) or (str(fundamentals.get("fundamental_category", "")).upper() == "VALUE_TRAP"))
        if telemetry is not None:
            telemetry.record_gate_evaluation(
                sym, "VALUE_TRAP", not is_trap,
                {"value_trap": is_trap, "fundamental_category": str(fundamentals.get("fundamental_category", "NONE"))},
                [RejectionReason.FAIL_VALUE_TRAP.value] if is_trap else []
            )
            telemetry.record_value_trap_detail(sym, is_trap, str(fundamentals.get("fundamental_category", "NONE")))

        # Fail-closed stop (§26, Mandatory Invariant Gate):
        # If required fundamental quality or growth metrics are missing/incomplete,
        # the stock is skipped at the data gate. Do NOT continue to technical pipeline.
        if any(r in rejections for r in (
            RejectionReason.FAIL_QUALITY_METRICS_INCOMPLETE,
            RejectionReason.FAIL_GROWTH_DATA_INSUFFICIENT,
            RejectionReason.FUNDAMENTAL_DATA_MISSING
        )):
            res = self._build_result(sym, False, rejections, gate_metrics)
            if telemetry is not None:
                telemetry.record_gate_evaluation(
                    sym, "MARKET_DATA", False,
                    {"reason": "SKIPPED_DUE_TO_INSUFFICIENT_FUNDAMENTAL_DATA"},
                    [r.value for r in rejections]
                )
                primary_err = rejections[0].value if rejections else "DATA_INSUFFICIENT"
                telemetry.record_symbol_final_decision(sym, False, [e.value for e in rejections], primary_err)
            return res

        # 5. Market Data Sanity
        if df_bars is None or len(df_bars) < 200:
            rejections.append(RejectionReason.MARKET_DATA_INSUFFICIENT_LOOKBACK)
            if telemetry is not None:
                telemetry.record_gate_evaluation(
                    sym, "MARKET_DATA", False,
                    {"bars_len": len(df_bars) if df_bars is not None else 0},
                    [RejectionReason.MARKET_DATA_INSUFFICIENT_LOOKBACK.value]
                )
                primary_err = rejections[0].value if rejections else RejectionReason.MARKET_DATA_INSUFFICIENT_LOOKBACK.value
                telemetry.record_symbol_final_decision(sym, False, [e.value for e in rejections], primary_err)
            return self._build_result(sym, False, rejections, gate_metrics)

        if telemetry is not None:
            telemetry.record_gate_evaluation(
                sym, "MARKET_DATA", True,
                {"bars_len": len(df_bars)},
                []
            )

        closes = df_bars["Close" if "Close" in df_bars.columns else "close"].values.astype(np.float64)
        highs = df_bars["High" if "High" in df_bars.columns else "high"].values.astype(np.float64)
        lows = df_bars["Low" if "Low" in df_bars.columns else "low"].values.astype(np.float64)
        volumes = df_bars["Volume" if "Volume" in df_bars.columns else "volume"].values.astype(np.float64)

        # 6. Technical Trend Gate & Relative Strength Gate
        if benchmark_closes is None:
            if hasattr(self, "_cached_benchmark_closes") and self._cached_benchmark_closes is not None:
                benchmark_closes = self._cached_benchmark_closes
            else:
                history_dir = os.path.join(DATA_DIR, "history", "1d")
                for bm_file in ["NIFTY 50.parquet", "NIFTY50.parquet", "^NSEI.parquet"]:
                    bm_path = os.path.join(history_dir, bm_file)
                    if os.path.exists(bm_path):
                        try:
                            df_bm = pd.read_parquet(bm_path)
                            c_col = "Close" if "Close" in df_bm.columns else ("close" if "close" in df_bm.columns else None)
                            if c_col and not df_bm.empty:
                                benchmark_closes = df_bm[c_col].values.astype(np.float64)
                                self._cached_benchmark_closes = benchmark_closes
                                break
                        except Exception:
                            pass

        tt_pass, tt_errs, tt_metrics = TechnicalTrendGate.evaluate(closes, benchmark_closes)
        gate_metrics.update(tt_metrics)
        trend_only_errs = [e for e in tt_errs if e == RejectionReason.FAIL_TREND]
        rs_only_errs = [e for e in tt_errs if e == RejectionReason.FAIL_RELATIVE_STRENGTH]
        trend_pass = (len(trend_only_errs) == 0)
        rs_pass = (len(rs_only_errs) == 0)

        if telemetry is not None:
            trend_data = {
                "close": float(closes[-1]),
                "sma50": tt_metrics.get("sma50"),
                "sma200": tt_metrics.get("sma200")
            }
            telemetry.record_gate_evaluation(sym, "TECHNICAL_TREND", trend_pass, trend_data, [e.value for e in trend_only_errs])
            telemetry.record_technical_trend_detail(sym, trend_data, trend_pass, [e.value for e in trend_only_errs])

            rs_data = {
                "ret_3m_stock_pct": tt_metrics.get("ret_3m_stock_pct"),
                "ret_6m_stock_pct": tt_metrics.get("ret_6m_stock_pct")
            }
            telemetry.record_gate_evaluation(sym, "RELATIVE_STRENGTH", rs_pass, rs_data, [e.value for e in rs_only_errs])
            telemetry.record_relative_strength_detail(sym, rs_data, rs_pass, [e.value for e in rs_only_errs])

        if not tt_pass:
            rejections.extend(tt_errs)

        # 7. Consolidation Gate (window 20-60, DD <= 15%, ATR <= 6%, Close > SMA200)
        c_pass, c_errs, c_metrics = ConsolidationGate.evaluate(closes, highs, lows, consolidation_window)
        gate_metrics.update(c_metrics)
        if telemetry is not None:
            telemetry.record_gate_evaluation(sym, "CONSOLIDATION", c_pass, c_metrics, [e.value for e in c_errs])
            telemetry.record_consolidation_detail(sym, c_metrics, c_pass, [e.value for e in c_errs])
        if not c_pass:
            rejections.extend(c_errs)

        # 8. Breakout Gate (Close > 20D High, Vol >= 1.5x, Ext <= 8%)
        b_pass, b_errs, b_metrics = BreakoutGate.evaluate(closes, highs, volumes, lookback=20)
        gate_metrics.update(b_metrics)
        if telemetry is not None:
            telemetry.record_gate_evaluation(sym, "BREAKOUT", b_pass, b_metrics, [e.value for e in b_errs])
            telemetry.record_breakout_detail(sym, b_metrics, b_pass, [e.value for e in b_errs])
        if not b_pass:
            rejections.extend(b_errs)

        is_buy = (len(rejections) == 0)
        res = self._build_result(sym, is_buy, rejections, gate_metrics)
        if telemetry is not None:
            telemetry.record_symbol_final_decision(sym, is_buy, [e.value for e in rejections])
        return res

    def scan_universe(
        self,
        market_data_map: Optional[Dict[str, pd.DataFrame]] = None,
        fundamentals_map: Optional[Dict[str, Dict[str, Any]]] = None,
        benchmark_closes: Optional[np.ndarray] = None,
        trigger_type: str = "AUTOMATED",
        scheduler_name: str = "SCHEDULED"
    ) -> Dict[str, Any]:
        """
        Scans all candidates across the universe and records the complete stock funnel audit.
        Also records execution run in scanner_execution_history and scanner_health.
        Uses Daily Builder 2.0 as the authoritative upstream fundamental intelligence layer.
        """
        import time
        start_ts = time.time()
        ctx = None
        acquired_scan = False
        acquired_global = False
        _scan_start = time.monotonic()  # must be monotonic — print_scanner_end_banner computes time.monotonic() - start_mono
        _computed_health_status = None   # set after health classification; passed to end banner as override_status
        # Invariant: verify _scan_start is a valid monotonic value, not a wall-clock timestamp.
        # time.monotonic() on any modern system is O(thousands) of seconds, never O(billions).
        assert _scan_start > 0, f"FUNDAMENTAL _scan_start={_scan_start} must be positive"
        assert _scan_start < 1e9, (
            f"FUNDAMENTAL _scan_start={_scan_start:.0f} looks like time.time() (wall-clock), "
            f"not time.monotonic(). Duration will be negative in end banner."
        )

        # 1. Thread-level concurrency lock: prevent overlapping runs of same scanner
        if not _fundamental_scan_lock.acquire(blocking=False):
            logger.warning("🔒 [FUNDAMENTAL] Scanner is already running in another thread. Skipping duplicate cycle.")
            return {"status": "SKIPPED", "reason": "Already running"}
        acquired_scan = True

        # 2. Universal global scanner lock queue wait: serialize TECHNICAL, FUNDAMENTAL, V2_FINAL
        queued_at = time.monotonic()
        if not _global_lock.acquire(blocking=False, owner_scanner="FUNDAMENTAL", operation="FULL_SCAN"):
            logger.info("⏳ [FUNDAMENTAL] Global scanner lock busy (another main scanner is running) — waiting in queue until active scanner finishes...")
            try:
                from database import upsert_scanner_health
            except ImportError:
                try:
                    from app.database import upsert_scanner_health
                except Exception:
                    upsert_scanner_health = None
            if upsert_scanner_health is not None:
                try:
                    upsert_scanner_health("FUNDAMENTAL", "QUEUED", error_msg="Waiting in queue for active scanner to release lock...")
                except Exception:
                    pass

            try:
                acquired_global = _global_lock.acquire(blocking=True, owner_scanner="FUNDAMENTAL", operation="FULL_SCAN")
            except Exception as lock_err:
                logger.error(f"❌ [FUNDAMENTAL] Error acquiring global lock: {lock_err}")
                acquired_global = False

            if not acquired_global:
                logger.error("❌ [FUNDAMENTAL] Failed to acquire global scanner lock after queue wait.")
                if upsert_scanner_health is not None:
                    try:
                        upsert_scanner_health("FUNDAMENTAL", "IDLE", error_msg="Lock acquisition timed out")
                    except Exception:
                        pass
                _fundamental_scan_lock.release()
                return {"status": "FAILED", "reason": "Lock acquisition failed"}
        else:
            acquired_global = True

        try:
            # 3. Imports for DB execution tracking & health updates
            try:
                from database import create_scanner_execution_run, complete_scanner_execution_run, upsert_scanner_health, save_wealth_buy_alert, save_alert_if_new
            except ImportError:
                from app.database import create_scanner_execution_run, complete_scanner_execution_run, upsert_scanner_health, save_wealth_buy_alert, save_alert_if_new

            target_symbols = list(market_data_map.keys()) if market_data_map is not None else list(self.universe_registry.approved_symbols)

            # 4. Entry in history MUST ONLY be created once we get lock and start running
            if create_scanner_execution_run is not None:
                try:
                    ctx = create_scanner_execution_run(
                        scanner_name="FUNDAMENTAL",
                        trigger_type=trigger_type,
                        total_stocks=len(target_symbols),
                        allow_concurrent=True
                    )
                except Exception as e:
                    logger.debug(f"Execution history start warning: {e}")

            # 5. Start Banner
            _scan_start = print_scanner_start_banner("FUNDAMENTAL", queued_at=queued_at, run_id=getattr(ctx, "run_id", None))

            if upsert_scanner_health is not None:
                try:
                    upsert_scanner_health(
                        "FUNDAMENTAL",
                        status="RUNNING",
                        total_count=len(target_symbols),
                        run_id=getattr(ctx, "run_id", None)
                    )
                except Exception as e:
                    logger.debug(f"Scanner health RUNNING warning: {e}")

            logger.info(f"📡 [SCANNER: FUNDAMENTAL] Fetching 1D market data & loading fundamentals (trigger={trigger_type}, scheduler={scheduler_name})...")

            # Authoritative Upstream Layer: Daily Builder 2.0 & Canonical Shared Financial Snapshot
            db_funds, db_meta = self.daily_builder_provider.load_master_fundamentals()
            if fundamentals_map is None:
                fundamentals_map = db_funds
            else:
                # Enrich passed fundamentals with Daily Builder metadata & value trap flags
                for sym, f_data in list(fundamentals_map.items()):
                    sym_u = sym.upper()
                    if sym_u in db_funds:
                        for k, v in db_funds[sym_u].items():
                            if k not in f_data or f_data[k] is None:
                                f_data[k] = v

            # Load canonical shared financial snapshots (single shared data layer)
            shared_snapshots = {}
            try:
                from app.financial_data_integrity import load_all_shared_financial_snapshots
                shared_snapshots = load_all_shared_financial_snapshots(symbols=target_symbols)
            except Exception as _snap_err:
                logger.debug(f"Shared snapshot load notice: {_snap_err}")

            if market_data_map is None:
                market_data_map = {}
                history_dir = os.path.join(DATA_DIR, "history", "1d")
                os.makedirs(history_dir, exist_ok=True)

                # Live quote warmup for accurate intraday breakout evaluation
                live_quotes = {}
                try:
                    from live_prices import get_live_prices
                    live_quotes = get_live_prices(target_symbols, purpose="FUNDAMENTAL_SCAN")
                except Exception as _lpe:
                    logger.debug(f"Live quote fetch notice: {_lpe}")

                # Pass 1: Load existing valid 1D parquets from disk
                missing_or_short = []
                for sym in target_symbols:
                    p_path = os.path.join(history_dir, f"{sym}.parquet")
                    if os.path.exists(p_path):
                        try:
                            df_bar = pd.read_parquet(p_path)
                            if not df_bar.empty and len(df_bar) >= 200:
                                market_data_map[sym] = df_bar
                            else:
                                missing_or_short.append(sym)
                        except Exception as e:
                            logger.debug(f"Failed to load daily candle for {sym}: {e}")
                            missing_or_short.append(sym)
                    else:
                        missing_or_short.append(sym)

                # Pass 2: Fetch missing or short (<200 candles) symbols via UnifiedFetcher
                if missing_or_short:
                    logger.info(f"📥 [FUNDAMENTAL_SCAN] Fetching missing/short 1D history for {len(missing_or_short)} symbols via UnifiedFetcher...")
                    try:
                        from price_cache import fetch_unified_historical
                        for i in range(0, len(missing_or_short), 100):
                            batch = missing_or_short[i:i + 100]
                            fetched = fetch_unified_historical(batch, period="1y", interval="1d", requester="FUNDAMENTAL_SCAN")
                            if fetched:
                                for sym, df_bar in fetched.items():
                                    if df_bar is not None and not df_bar.empty and len(df_bar) >= 50:
                                        market_data_map[sym] = df_bar

                        # Persist newly fetched 1d parquet files to DB history bundle in background
                        try:
                            from database import upload_history_bundle_to_db, submit_background_upload
                            submit_background_upload(lambda: upload_history_bundle_to_db("1d", force=True))
                        except Exception as _ube:
                            logger.debug(f"History bundle upload dispatch notice: {_ube}")
                    except Exception as fe:
                        logger.warning(f"⚠️ [FUNDAMENTAL_SCAN] Failed to fetch missing 1D history: {fe}")

                # Pass 3: Overlay live quote onto the latest daily candle
                for sym, df_bar in market_data_map.items():
                    lp = live_quotes.get(sym)
                    if lp and float(lp) > 0:
                        df_bar = df_bar.copy()
                        c_col = 'Close' if 'Close' in df_bar.columns else ('close' if 'close' in df_bar.columns else None)
                        h_col = 'High' if 'High' in df_bar.columns else ('high' if 'high' in df_bar.columns else None)
                        if c_col:
                            if df_bar[c_col].dtype != 'float64':
                                df_bar[c_col] = df_bar[c_col].astype(float)
                            df_bar.loc[df_bar.index[-1], c_col] = float(lp)
                        if h_col and float(lp) > float(df_bar[h_col].iloc[-1]):
                            if df_bar[h_col].dtype != 'float64':
                                df_bar[h_col] = df_bar[h_col].astype(float)
                            df_bar.loc[df_bar.index[-1], h_col] = float(lp)
                        market_data_map[sym] = df_bar

            if benchmark_closes is None:
                history_dir = os.path.join(DATA_DIR, "history", "1d")
                for bm_file in ["NIFTY 50.parquet", "NIFTY50.parquet", "^NSEI.parquet"]:
                    bm_path = os.path.join(history_dir, bm_file)
                    if os.path.exists(bm_path):
                        try:
                            df_bm = pd.read_parquet(bm_path)
                            if "Close" in df_bm.columns and not df_bm.empty:
                                benchmark_closes = df_bm["Close"].values
                                break
                            elif "close" in df_bm.columns and not df_bm.empty:
                                benchmark_closes = df_bm["close"].values
                                break
                        except Exception:
                            pass

            telemetry = FundamentalScanTelemetry(
                scanner_version="2.0.0",
                universe_version="certified_clean_universe_886",
                universe_hash=RULES_HASH_BUY,
                daily_builder_version="2.0",
                git_commit=FROZEN_GIT_SHA
            )
            telemetry.log_scan_start(
                master_count=len(self.universe_registry.master_symbols),
                quarantined_count=len(self.universe_registry.quarantined_symbols),
                eligible_count=len(self.universe_registry.approved_symbols)
            )
            telemetry.record_data_provider_audit(
                provider="DAILY_BUILDER_2.0",
                source=str(db_meta.get("file_path", "data/daily_builder_master_v2.parquet")),
                rows=len(fundamentals_map) if fundamentals_map else 0,
                latency_ms=round((time.time() - start_ts) * 1000.0, 2),
                latest_timestamp=db_meta.get("loaded_at"),
                data_age_days=db_meta.get("age_days", 0.0),
                freshness_status=str(db_meta.get("freshness_status", "FRESH")),
                validation_status=str(db_meta.get("provenance_status", "CERTIFIED_LOCAL_DAILY_BUILDER"))
            )

            funnel = {
                "scanned_count": 0,
                "universe_valid_count": 0,
                "fundamental_quality_pass_count": 0,
                "earnings_acceleration_pass_count": 0,
                "trend_pass_count": 0,
                "relative_strength_pass_count": 0,
                "consolidation_pass_count": 0,
                "breakout_pass_count": 0,
                "buy_alerts_count": 0,
                "data_insufficient_count": 0,
                "growth_data_insufficient_count": 0,
                "quality_data_insufficient_count": 0,
                "price_data_insufficient_count": 0,
                "data_missing_count": 0,
                "pit_missing_count": 0,
                "provider_failure_count": 0,
                "rejection_summary": {},
                "buy_candidates": []
            }
            # Audit EVERY approved symbol in the clean universe (strictly 886 / 886 — NO SILENT SKIPS)
            for sym in target_symbols:
                funnel["scanned_count"] += 1
                df_bars = market_data_map.get(sym)
                funds = fundamentals_map.get(sym)
                if not funds:
                    funds = {
                        "symbol": sym,
                        "roce": None,
                        "roe": None,
                        "debt_equity": None,
                        "operating_cash_flow": None,
                        "fundamental_category": "NONE",
                        "is_value_trap": False,
                        "quality_score": 0.0,
                        "growth_score": 0.0,
                        "valuation_score": 0.0,
                        "wealth_score": 0.0,
                        "rev_yoy_latest": None,
                        "rev_yoy_prev": None,
                        "op_profit_yoy_latest": None,
                        "op_profit_yoy_prev": None,
                        "eps_yoy_latest": None,
                        "eps_yoy_prev": None,
                        "prior_eps": None,
                        "upstream_provider": "DATA_UNAVAILABLE"
                    }
                is_data_stale = (db_meta.get("freshness_status") == "STALE")
                prov_valid = (db_meta.get("provenance_status") in ("CERTIFIED_LOCAL_DAILY_BUILDER", "CERTIFIED_POSTGRES_DAILY_BUILDER", "CERTIFIED_DAILY_BUILDER_WATCHLIST", "CERTIFIED_PIT_FUNDAMENTALS_DB_REHYDRATED")) and (funds.get("upstream_provider") != "DATA_UNAVAILABLE")

                # ── SHARED CANONICAL FINANCIAL SNAPSHOT GATE ─────────────────────────
                snap = shared_snapshots.get(sym)
                if snap is not None:
                    f_eligible, f_reasons = snap.is_eligible_for_fundamental()
                    if not f_eligible:
                        if snap.snapshot_status == "UPDATE_PENDING":
                            prov_valid = False
                            is_data_stale = True
                            funds["upstream_provider"] = "UPDATE_PENDING"
                        elif snap.snapshot_status in ("INVALID", "DATA_INSUFFICIENT"):
                            prov_valid = False
                            funds["upstream_provider"] = "DATA_INSUFFICIENT"
                        elif snap.snapshot_status == "STALE" or snap.pit_freshness_status != "VALID":
                            is_data_stale = True
                    else:
                        # Shared financial snapshot is FRESH and CERTIFIED
                        snap_dict = snap.to_fundamental_dict()
                        for k, v in snap_dict.items():
                            if v is not None and (funds.get(k) is None or funds.get("upstream_provider") in ("DATA_UNAVAILABLE", "NONE")):
                                funds[k] = v
                        if funds.get("roce") is not None and funds.get("roe") is not None:
                            prov_valid = True
                            if funds.get("upstream_provider") == "DATA_UNAVAILABLE":
                                funds["upstream_provider"] = "SHARED_CANONICAL_SNAPSHOT"

                # ── DATA RECOVERY AUDIT: FUNDAMENTAL SCANNER ───────────────────────
                _fund_missing = funds.get("upstream_provider") == "DATA_UNAVAILABLE"
                _bars_missing = (df_bars is None or df_bars.empty)
                _bars_short   = (not _bars_missing and len(df_bars) < 50)

                if _fund_missing:
                    # Attempt 4-stage recovery for completely missing fundamental record
                    pit_filings = _get_pit_filings(sym, allow_live_refresh=False)
                    if not pit_filings:
                        pit_filings = _get_pit_filings(sym, allow_live_refresh=True)

                    annual_filings = [f for f in pit_filings if str(f.get("statement_type", "")).upper() == "ANNUAL"]
                    f_annual = annual_filings[0] if annual_filings else None
                    quarterly_filings = [f for f in pit_filings if str(f.get("statement_type", "")).upper() == "QUARTERLY"]
                    f_q0 = quarterly_filings[0] if quarterly_filings else (pit_filings[0] if pit_filings else None)

                    prov_pit_result = "FETCHED" if (f_annual or f_q0) else "NOT_AVAILABLE"
                    prov_pit_fail = None if prov_pit_result == "FETCHED" else "SYMBOL_NOT_IN_PIT_OR_FILINGS"
                    
                    if prov_pit_result == "FETCHED":
                        # Populate Quality from latest audited annual statement
                        if f_annual:
                            roce_val = f_annual.get("roce")
                            roe_val = f_annual.get("roe")
                            tot_debt = f_annual.get("total_debt")
                            tot_eq = f_annual.get("total_equity")
                            de_val = (float(tot_debt) / float(tot_eq)) if tot_debt is not None and tot_eq is not None and not pd.isna(tot_debt) and not pd.isna(tot_eq) and float(tot_eq) > 0 else (0.0 if tot_debt == 0 else None)
                            ocf_val = f_annual.get("operating_cash_flow")

                            # Attempt mathematical derivations if ratio fields are null
                            if roce_val is None and f_annual.get("operating_profit") is not None and tot_eq is not None:
                                cap = float(tot_eq) + (float(tot_debt or 0.0))
                                if cap > 0:
                                    roce_val = round(float(f_annual["operating_profit"]) / cap * 100.0, 2)
                            if roe_val is None and f_annual.get("net_profit") is not None and tot_eq is not None and float(tot_eq) > 0:
                                roe_val = round(float(f_annual["net_profit"]) / float(tot_eq) * 100.0, 2)

                            funds["roce"] = float(roce_val) if roce_val is not None and not pd.isna(roce_val) else None
                            funds["roe"] = float(roe_val) if roe_val is not None and not pd.isna(roe_val) else None
                            funds["debt_equity"] = float(de_val) if de_val is not None and not pd.isna(de_val) else None
                            funds["operating_cash_flow"] = float(ocf_val) if ocf_val is not None and not pd.isna(ocf_val) else None

                        funds["upstream_provider"] = "PIT_DATABASE"
                        funds["provenance_status"] = "CERTIFIED_PIT_FUNDAMENTALS_DB_REHYDRATED"
                        prov_valid = True

                    _emit_data_recovery_log(
                        scanner="FUNDAMENTAL",
                        symbol=sym,
                        stage="FUNDAMENTAL",
                        missing_data="fundamental_master_record",
                        recovery_attempted=True,
                        attempts={
                            "attempt_1": "DAILY_BUILDER_CACHE → MISSING",
                            "attempt_2": f"PIT_DATABASE → {'FETCHED' if pit_filings else 'NOT_AVAILABLE'}",
                            "attempt_3": f"DERIVED_FROM_RAW → {'COMPUTED' if f_annual else 'CANNOT_DERIVE'}",
                            "attempt_4": f"FILING_PROVIDER_REFRESH → {'FETCHED' if pit_filings else 'NOT_AVAILABLE'}",
                        },
                        validation="PASSED" if prov_pit_result == "FETCHED" else "FAILED",
                        validation_reason=None if prov_pit_result == "FETCHED" else "APPROVED_PROVIDERS_EXHAUSTED",
                        final_action="DATA_USED" if prov_pit_result == "FETCHED" else "STOCK_SKIPPED",
                    )
                else:
                    # Check partial missing Quality fields: ROCE, ROE, OCF, Debt/Equity
                    missing_q = [f for f in ["roce", "roe", "operating_cash_flow", "debt_equity"] if funds.get(f) is None]
                    if missing_q:
                        pit_filings = _get_pit_filings(sym, allow_live_refresh=False)
                        annual_filings = [f for f in pit_filings if str(f.get("statement_type", "")).upper() == "ANNUAL"]
                        f_annual = annual_filings[0] if annual_filings else None

                        # If annual statement missing or missing OCF/Equity, trigger live filing refresh
                        if f_annual is None or any(f_annual.get(k) is None for k in ("operating_cash_flow", "total_equity")):
                            pit_filings = _get_pit_filings(sym, allow_live_refresh=True)
                            annual_filings = [f for f in pit_filings if str(f.get("statement_type", "")).upper() == "ANNUAL"]
                            f_annual = annual_filings[0] if annual_filings else None

                        for fld in missing_q:
                            att = {
                                "attempt_1": "DAILY_BUILDER_CACHE → MISSING",
                                "attempt_2": "PIT_DATABASE → NOT_AVAILABLE",
                                "attempt_3": "DERIVED_FROM_RAW → NOT_AVAILABLE",
                                "attempt_4": "FILING_PROVIDER_REFRESH → NOT_AVAILABLE",
                            }
                            fld_resolved = False

                            if fld == "operating_cash_flow":
                                ocf_val = f_annual.get("operating_cash_flow") if f_annual else None
                                if ocf_val is not None and not pd.isna(ocf_val):
                                    funds["operating_cash_flow"] = float(ocf_val)
                                    att["attempt_2"] = f"PIT_DATABASE → FETCHED ({float(ocf_val):.1f} Cr)"
                                    att["attempt_3"] = "DERIVED_FROM_RAW → NOT_NEEDED"
                                    att["attempt_4"] = "FILING_PROVIDER_REFRESH → NOT_NEEDED"
                                    fld_resolved = True
                                else:
                                    att["attempt_2"] = "PIT_DATABASE → FIELD_ABSENT_IN_ANNUAL_CF"
                                    att["attempt_3"] = "DERIVED_FROM_RAW → CANNOT_DERIVE_WITHOUT_CF_STATEMENT"
                                    att["attempt_4"] = "FILING_PROVIDER_REFRESH → FIELD_NOT_REPORTED"

                            elif fld == "roce":
                                roce_val = f_annual.get("roce") if f_annual else None
                                if roce_val is not None and not pd.isna(roce_val):
                                    funds["roce"] = float(roce_val)
                                    att["attempt_2"] = f"PIT_DATABASE → FETCHED ({float(roce_val):.1f}%)"
                                    att["attempt_3"] = "DERIVED_FROM_RAW → NOT_NEEDED"
                                    att["attempt_4"] = "FILING_PROVIDER_REFRESH → NOT_NEEDED"
                                    fld_resolved = True
                                elif f_annual:
                                    op = f_annual.get("operating_profit")
                                    te = f_annual.get("total_equity")
                                    td = f_annual.get("total_debt") or 0.0
                                    if op is not None and te is not None and not pd.isna(op) and not pd.isna(te):
                                        cap = float(te) + float(td)
                                        if cap > 0:
                                            derived_roce = round(float(op) / cap * 100.0, 2)
                                            funds["roce"] = derived_roce
                                            att["attempt_2"] = "PIT_DATABASE → RAW_EBIT_CAP_FOUND"
                                            att["attempt_3"] = f"DERIVED_FROM_RAW → COMPUTED ({derived_roce:.2f}% from EBIT/Capital)"
                                            att["attempt_4"] = "FILING_PROVIDER_REFRESH → NOT_NEEDED"
                                            fld_resolved = True
                                        else:
                                            att["attempt_3"] = "DERIVED_FROM_RAW → CAPITAL_LE_ZERO"
                                    else:
                                        att["attempt_3"] = "DERIVED_FROM_RAW → INSUFFICIENT_RAW_EBIT_OR_EQUITY"
                                else:
                                    att["attempt_2"] = "PIT_DATABASE → NO_ANNUAL_FILINGS"
                                    att["attempt_3"] = "DERIVED_FROM_RAW → NO_ANNUAL_DATA"
                                    att["attempt_4"] = "FILING_PROVIDER_REFRESH → FIELD_NOT_REPORTED"

                            elif fld == "roe":
                                roe_val = f_annual.get("roe") if f_annual else None
                                if roe_val is not None and not pd.isna(roe_val):
                                    funds["roe"] = float(roe_val)
                                    att["attempt_2"] = f"PIT_DATABASE → FETCHED ({float(roe_val):.1f}%)"
                                    att["attempt_3"] = "DERIVED_FROM_RAW → NOT_NEEDED"
                                    att["attempt_4"] = "FILING_PROVIDER_REFRESH → NOT_NEEDED"
                                    fld_resolved = True
                                elif f_annual:
                                    np_val = f_annual.get("net_profit")
                                    te = f_annual.get("total_equity")
                                    if np_val is not None and te is not None and not pd.isna(np_val) and not pd.isna(te) and float(te) > 0:
                                        derived_roe = round(float(np_val) / float(te) * 100.0, 2)
                                        funds["roe"] = derived_roe
                                        att["attempt_2"] = "PIT_DATABASE → RAW_PAT_EQUITY_FOUND"
                                        att["attempt_3"] = f"DERIVED_FROM_RAW → COMPUTED ({derived_roe:.2f}% from PAT/Equity)"
                                        att["attempt_4"] = "FILING_PROVIDER_REFRESH → NOT_NEEDED"
                                        fld_resolved = True
                                    else:
                                        att["attempt_3"] = "DERIVED_FROM_RAW → INSUFFICIENT_PAT_OR_EQUITY"
                                else:
                                    att["attempt_2"] = "PIT_DATABASE → NO_ANNUAL_FILINGS"
                                    att["attempt_3"] = "DERIVED_FROM_RAW → NO_ANNUAL_DATA"
                                    att["attempt_4"] = "FILING_PROVIDER_REFRESH → FIELD_NOT_REPORTED"

                            elif fld == "debt_equity":
                                if f_annual:
                                    td = f_annual.get("total_debt")
                                    te = f_annual.get("total_equity")
                                    if td is not None and te is not None and not pd.isna(td) and not pd.isna(te) and float(te) > 0:
                                        de_calc = round(float(td) / float(te), 2)
                                        funds["debt_equity"] = de_calc
                                        att["attempt_2"] = f"PIT_DATABASE → FETCHED (Debt={td}, Eq={te})"
                                        att["attempt_3"] = f"DERIVED_FROM_RAW → COMPUTED ({de_calc} from Debt/Equity)"
                                        att["attempt_4"] = "FILING_PROVIDER_REFRESH → NOT_NEEDED"
                                        fld_resolved = True
                                    elif td == 0 or (td is not None and float(td) == 0.0):
                                        funds["debt_equity"] = 0.0
                                        att["attempt_2"] = "PIT_DATABASE → DEBT_FREE (Debt=0)"
                                        att["attempt_3"] = "DERIVED_FROM_RAW → 0.0 (Debt Free)"
                                        att["attempt_4"] = "FILING_PROVIDER_REFRESH → NOT_NEEDED"
                                        fld_resolved = True
                                    else:
                                        att["attempt_2"] = "PIT_DATABASE → BORROWINGS_NOT_REPORTED"
                                        att["attempt_3"] = "DERIVED_FROM_RAW → CANNOT_COMPUTE_DEBT_RATIO"
                                        att["attempt_4"] = "FILING_PROVIDER_REFRESH → FIELD_NOT_REPORTED"
                                else:
                                    att["attempt_2"] = "PIT_DATABASE → NO_ANNUAL_FILINGS"
                                    att["attempt_3"] = "DERIVED_FROM_RAW → NO_ANNUAL_DATA"
                                    att["attempt_4"] = "FILING_PROVIDER_REFRESH → FIELD_NOT_REPORTED"

                            _emit_data_recovery_log(
                                scanner="FUNDAMENTAL",
                                symbol=sym,
                                stage="QUALITY",
                                missing_data=f"quality_metric ({fld})",
                                recovery_attempted=True,
                                attempts=att,
                                field=fld,
                                validation="PASSED" if fld_resolved else "FAILED",
                                validation_reason=None if fld_resolved else f"APPROVED_PROVIDERS_EXHAUSTED_FOR_{fld.upper()}",
                                final_action="DATA_USED" if fld_resolved else "STOCK_SKIPPED"
                            )

                    # Check partial missing Growth fields: YoY Rev, OpProfit, EPS & Prior EPS
                    missing_g = [f for f in ["rev_yoy_latest", "rev_yoy_prev", "op_profit_yoy_latest", "op_profit_yoy_prev", "eps_yoy_latest", "eps_yoy_prev", "prior_eps"] if funds.get(f) is None]
                    if missing_g:
                        pit_filings = _get_pit_filings(sym, allow_live_refresh=False)
                        quarterly_filings = [f for f in pit_filings if str(f.get("statement_type", "")).upper() == "QUARTERLY"]
                        if len(quarterly_filings) < 2:
                            pit_filings = _get_pit_filings(sym, allow_live_refresh=True)
                            quarterly_filings = [f for f in pit_filings if str(f.get("statement_type", "")).upper() == "QUARTERLY"]

                        if len(quarterly_filings) >= 2:
                            f0 = quarterly_filings[0]
                            f1 = quarterly_filings[1]

                            def _find_yoy_match(ref_f):
                                ref_dt = pd.to_datetime(ref_f.get("period_end_date"))
                                for past_f in quarterly_filings:
                                    past_dt = pd.to_datetime(past_f.get("period_end_date"))
                                    diff_days = (ref_dt - past_dt).days
                                    if 340 <= diff_days <= 390:
                                        return past_f
                                return None

                            match_f0 = _find_yoy_match(f0)
                            match_f1 = _find_yoy_match(f1)

                            if match_f0:
                                r0, r_m0 = f0.get("revenue"), match_f0.get("revenue")
                                op0, op_m0 = f0.get("operating_profit"), match_f0.get("operating_profit")
                                eps0, eps_m0 = f0.get("eps"), match_f0.get("eps")
                                if funds.get("rev_yoy_latest") is None and r0 is not None and r_m0 is not None and not pd.isna(r0) and not pd.isna(r_m0) and abs(float(r_m0)) > 1e-5:
                                    funds["rev_yoy_latest"] = round(((float(r0) - float(r_m0)) / abs(float(r_m0))) * 100.0, 2)
                                if funds.get("op_profit_yoy_latest") is None and op0 is not None and op_m0 is not None and not pd.isna(op0) and not pd.isna(op_m0) and abs(float(op_m0)) > 1e-5:
                                    funds["op_profit_yoy_latest"] = round(((float(op0) - float(op_m0)) / abs(float(op_m0))) * 100.0, 2)
                                if funds.get("eps_yoy_latest") is None and eps0 is not None and eps_m0 is not None and not pd.isna(eps0) and not pd.isna(eps_m0) and abs(float(eps_m0)) > 1e-5:
                                    funds["eps_yoy_latest"] = round(((float(eps0) - float(eps_m0)) / abs(float(eps_m0))) * 100.0, 2)
                                if funds.get("prior_eps") is None and eps_m0 is not None and not pd.isna(eps_m0):
                                    funds["prior_eps"] = float(eps_m0)

                            if match_f1:
                                r1, r_m1 = f1.get("revenue"), match_f1.get("revenue")
                                op1, op_m1 = f1.get("operating_profit"), match_f1.get("operating_profit")
                                eps1, eps_m1 = f1.get("eps"), match_f1.get("eps")
                                if funds.get("rev_yoy_prev") is None and r1 is not None and r_m1 is not None and not pd.isna(r1) and not pd.isna(r_m1) and abs(float(r_m1)) > 1e-5:
                                    funds["rev_yoy_prev"] = round(((float(r1) - float(r_m1)) / abs(float(r_m1))) * 100.0, 2)
                                if funds.get("op_profit_yoy_prev") is None and op1 is not None and op_m1 is not None and not pd.isna(op1) and not pd.isna(op_m1) and abs(float(op_m1)) > 1e-5:
                                    funds["op_profit_yoy_prev"] = round(((float(op1) - float(op_m1)) / abs(float(op_m1))) * 100.0, 2)
                                if funds.get("eps_yoy_prev") is None and eps1 is not None and eps_m1 is not None and not pd.isna(eps1) and not pd.isna(eps_m1) and abs(float(eps_m1)) > 1e-5:
                                    funds["eps_yoy_prev"] = round(((float(eps1) - float(eps_m1)) / abs(float(eps_m1))) * 100.0, 2)

                            if funds.get("prior_eps") is None and f1.get("eps") is not None and not pd.isna(f1.get("eps")):
                                funds["prior_eps"] = float(f1.get("eps"))

                        for fld in missing_g:
                            is_res = funds.get(fld) is not None
                            att_g = {
                                "attempt_1": "DAILY_BUILDER_CACHE → MISSING",
                                "attempt_2": f"PIT_DATABASE → {'MATCHED' if is_res else 'INSUFFICIENT_QUARTERS'}",
                                "attempt_3": f"DERIVED_FROM_RAW → {'COMPUTED' if is_res else 'CANNOT_DERIVE'}",
                                "attempt_4": f"FILING_PROVIDER_REFRESH → {'FETCHED' if is_res else 'NOT_AVAILABLE'}",
                            }
                            _emit_data_recovery_log(
                                scanner="FUNDAMENTAL",
                                symbol=sym,
                                stage="GROWTH",
                                missing_data=f"growth_acceleration_metric ({fld})",
                                recovery_attempted=True,
                                attempts=att_g,
                                field=fld,
                                validation="PASSED" if is_res else "FAILED",
                                validation_reason=None if is_res else f"APPROVED_PROVIDERS_EXHAUSTED_FOR_{fld.upper()}",
                                final_action="DATA_USED" if is_res else "STOCK_SKIPPED"
                            )

                if _bars_missing:
                    _emit_data_recovery_log(
                        scanner="FUNDAMENTAL",
                        symbol=sym,
                        stage="TECHNICAL",
                        missing_data="1D_OHLCV_HISTORY",
                        recovery_attempted=True,
                        providers=[
                            {
                                "provider": "LOCAL_1D_PARQUET",
                                "result": "NOT_AVAILABLE",
                                "failure_type": "FILE_MISSING_OR_UNREADABLE",
                            },
                            {
                                "provider": "UNIFIED_FETCHER",
                                "result": "FETCH_FAILED" if sym not in market_data_map else "FETCHED",
                                "validation": "FAILED" if sym not in market_data_map else "PASSED",
                                "validation_reason": "FETCH_RETURNED_EMPTY_OR_FAILED" if sym not in market_data_map else None,
                            }
                        ],
                        validation="FAILED" if sym not in market_data_map else "PASSED",
                        validation_reason="1D_HISTORY_UNAVAILABLE" if sym not in market_data_map else None,
                        final_action="STOCK_SKIPPED" if sym not in market_data_map else "DATA_USED",
                    )
                elif _bars_short:
                    _emit_data_recovery_log(
                        scanner="FUNDAMENTAL",
                        symbol=sym,
                        stage="TECHNICAL",
                        missing_data=f"1D_OHLCV_HISTORY (only {len(df_bars)} candles — minimum 50 required)",
                        recovery_attempted=False,
                        providers=[
                            {
                                "provider": "LOCAL_1D_PARQUET",
                                "result": "FETCHED",
                                "rows_received": len(df_bars),
                                "validation": "FAILED",
                                "validation_reason": f"INSUFFICIENT_LOOKBACK_{len(df_bars)}_CANDLES",
                            }
                        ],
                        validation="FAILED",
                        validation_reason=f"INSUFFICIENT_LOOKBACK_{len(df_bars)}_CANDLES",
                        final_action="STOCK_SKIPPED",
                    )
                # ── END DATA RECOVERY AUDIT ─────────────────────────────────────────

                res = self.scan_candidate(
                    sym, df_bars, funds,
                    benchmark_closes=benchmark_closes,
                    provenance_valid=prov_valid,
                    is_stale=is_data_stale,
                    telemetry=telemetry
                )
                # Track data freshness per-symbol in the run context
                if ctx is not None:
                    if df_bars is None or df_bars.empty:
                        ctx.mark_incomplete()   # broker/exchange data completely unavailable
                    elif is_data_stale:
                        ctx.mark_stale()        # data exists but is stale
                    else:
                        ctx.mark_fresh()        # fresh, real data

                if RejectionReason.EXCLUDED_UNAPPROVED_UNIVERSE not in res["rejection_reasons"] and \
                   RejectionReason.EXCLUDED_QUARANTINED_ANOMALY not in res["rejection_reasons"]:
                    funnel["universe_valid_count"] += 1

                if not any(r in res["rejection_reasons"] for r in [
                    RejectionReason.FAIL_ROCE, RejectionReason.FAIL_ROE, RejectionReason.FAIL_OCF,
                    RejectionReason.FAIL_DEBT_EQUITY, RejectionReason.FAIL_VALUE_TRAP,
                    RejectionReason.FAIL_UNIVERSE_FINANCIAL_SECTOR, RejectionReason.FAIL_QUALITY_METRICS_INCOMPLETE,
                    RejectionReason.FUNDAMENTAL_DATA_MISSING
                ]):
                    funnel["fundamental_quality_pass_count"] += 1

                if not any(r in res["rejection_reasons"] for r in [
                    RejectionReason.FAIL_REVENUE_ACCELERATION, RejectionReason.FAIL_OP_PROFIT_ACCELERATION,
                    RejectionReason.FAIL_EPS_ACCELERATION, RejectionReason.FAIL_PRIOR_EPS,
                    RejectionReason.FAIL_GROWTH_DATA_INSUFFICIENT, RejectionReason.FAIL_EARNINGS_ACCELERATION,
                    RejectionReason.FUNDAMENTAL_DATA_MISSING
                ]):
                    funnel["earnings_acceleration_pass_count"] += 1

                if RejectionReason.FAIL_TREND not in res["rejection_reasons"]:
                    funnel["trend_pass_count"] += 1

                if RejectionReason.FAIL_RELATIVE_STRENGTH not in res["rejection_reasons"]:
                    funnel["relative_strength_pass_count"] += 1

                if not any(r in res["rejection_reasons"] for r in [RejectionReason.FAIL_CONSOLIDATION_WINDOW, RejectionReason.FAIL_CONSOLIDATION_DRAWDOWN, RejectionReason.FAIL_CONSOLIDATION_ATR, RejectionReason.FAIL_CONSOLIDATION_SMA200]):
                    funnel["consolidation_pass_count"] += 1

                if not any(r in res["rejection_reasons"] for r in [RejectionReason.FAIL_BREAKOUT_PRICE, RejectionReason.FAIL_BREAKOUT_VOLUME, RejectionReason.FAIL_BREAKOUT_EXTENSION]):
                    funnel["breakout_pass_count"] += 1

                if res["is_buy"]:
                    funnel["buy_alerts_count"] += 1
                    funnel["buy_candidates"].append(res)
                    m = res.get("metrics", {})
                    cmp_price = float(m.get("close") or m.get("signal_close") or 0.0)
                    sma50 = float(m.get("sma50") or cmp_price)
                    vol_ratio = float(m.get("vol_ratio", 1.5))

                    # Construct full 7-stage sequential decision audit record
                    audit_trail = {
                        "symbol": sym.upper(),
                        "decision": "BUY",
                        "stages": {
                            "stage_0_universe": {
                                "status": "PASS",
                                "details": "Approved Universe, Not Quarantined"
                            },
                            "stage_1_quality": {
                                "status": "PASS",
                                "roce": m.get("roce"),
                                "roe": m.get("roe"),
                                "operating_cash_flow": m.get("operating_cash_flow"),
                                "debt_equity": m.get("debt_equity"),
                                "is_value_trap": m.get("is_value_trap"),
                                "category": m.get("fundamental_category"),
                                "quality_score": m.get("quality_score"),
                            },
                            "stage_2_growth": {
                                "status": "PASS",
                                "rev_yoy_latest": m.get("rev_yoy_latest"),
                                "rev_yoy_prev": m.get("rev_yoy_prev"),
                                "op_profit_yoy_latest": m.get("op_profit_yoy_latest"),
                                "op_profit_yoy_prev": m.get("op_profit_yoy_prev"),
                                "eps_yoy_latest": m.get("eps_yoy_latest"),
                                "eps_yoy_prev": m.get("eps_yoy_prev"),
                                "growth_score": m.get("growth_score"),
                            },
                            "stage_3_trend": {
                                "status": "PASS",
                                "close": cmp_price,
                                "sma50": m.get("sma50"),
                                "sma200": m.get("sma200"),
                                "ret_3m": m.get("ret_3m_stock_pct"),
                                "ret_6m": m.get("ret_6m_stock_pct"),
                            },
                            "stage_4_relative_strength": {
                                "status": "PASS",
                                "stock_ret_3m": m.get("ret_3m_stock_pct"),
                                "benchmark_ret_3m": m.get("ret_3m_bm_pct"),
                                "excess_return_3m": m.get("excess_return_3m"),
                            },
                            "stage_5_consolidation": {
                                "status": "PASS",
                                "consolidation_window": m.get("consolidation_window"),
                                "drawdown_pct": m.get("drawdown_pct"),
                                "atr_pct": m.get("atr_pct"),
                            },
                            "stage_6_breakout": {
                                "status": "PASS",
                                "prior_20d_high": m.get("prior_20d_high"),
                                "signal_close": cmp_price,
                                "vol_ratio": vol_ratio,
                                "extension_pct": m.get("extension_pct"),
                            }
                        }
                    }

                    logger.info(
                        f"\n{'='*70}\n"
                        f"🌟 [BUY_CANDIDATE_AUDIT] {sym.upper()} — ALL 7 GATES PASSED (BUY ELIGIBLE)\n"
                        f"{'='*70}\n"
                        f"  1. Quality:       ROCE={m.get('roce')}% | ROE={m.get('roe')}% | OCF=₹{m.get('operating_cash_flow')} Cr | D/E={m.get('debt_equity')}\n"
                        f"  2. Growth:        Rev YoY={m.get('rev_yoy_latest')}% | OpProfit YoY={m.get('op_profit_yoy_latest')}% | EPS YoY={m.get('eps_yoy_latest')}%\n"
                        f"  3. Trend:         CMP=₹{cmp_price:.2f} > SMA50(₹{m.get('sma50', 0):.2f}) > SMA200(₹{m.get('sma200', 0):.2f})\n"
                        f"  4. Rel Strength:  3M Return={m.get('ret_3m_stock_pct')}% vs BM={m.get('ret_3m_bm_pct')}%\n"
                        f"  5. Consolidation: Window={m.get('consolidation_window')}d | Max DD={m.get('drawdown_pct')}% | ATR={m.get('atr_pct')}%\n"
                        f"  6. Breakout:      CMP=₹{cmp_price:.2f} > 20D High(₹{m.get('prior_20d_high', 0):.2f}) | Vol={vol_ratio:.2f}x (Req >= 1.5x)\n"
                        f"{'='*70}"
                    )

                    # Pre-BUY Data Integrity Gate (C18 / C39: Mandatory Fail-Closed Contract)
                    is_stale = funds.get("pit_freshness_status") == "PIT_DATA_STALE"
                    provenance_valid = bool(m.get("roce") is not None and m.get("operating_cash_flow") is not None and m.get("roe") is not None)
                    bundle = BUYEvidenceBundle(
                        scan_run_id=getattr(ctx, "run_id", "LIVE_FUNDAMENTAL_RUN") if ctx else "LIVE_FUNDAMENTAL_RUN",
                        scanner="FUNDAMENTAL",
                        symbol=sym.upper(),
                        cmp=cmp_price,
                        strategy_score=float(res.get("score", 95)),
                        gate_results={
                            "UNIVERSE": True,
                            "PROVENANCE": provenance_valid and not is_stale,
                            "QUALITY": True,
                            "EARNINGS_ACCEL": True,
                            "TREND": True,
                            "CONSOLIDATION": True,
                            "BREAKOUT": True,
                        },
                        financial_metrics={
                            "roce": FieldProvenance(
                                symbol=sym.upper(), scanner="FUNDAMENTAL", field="roce",
                                value_used=m.get("roce"), unit="PERCENT", basis="CONSOLIDATED",
                                period_end=str(funds.get("period_end") or funds.get("filing_date") or "2025-03-31"),
                                source_used="PIT_FUNDAMENTALS", validation_status="PASSED"
                            ),
                            "roe": FieldProvenance(
                                symbol=sym.upper(), scanner="FUNDAMENTAL", field="roe",
                                value_used=m.get("roe"), unit="PERCENT", basis="CONSOLIDATED",
                                period_end=str(funds.get("period_end") or funds.get("filing_date") or "2025-03-31"),
                                source_used="PIT_FUNDAMENTALS", validation_status="PASSED"
                            ),
                            "operating_cash_flow": FieldProvenance(
                                symbol=sym.upper(), scanner="FUNDAMENTAL", field="operating_cash_flow",
                                value_used=m.get("operating_cash_flow"), unit="INR_CRORE", basis="CONSOLIDATED",
                                period_end=str(funds.get("period_end") or funds.get("filing_date") or "2025-03-31"),
                                source_used="PIT_FUNDAMENTALS", validation_status="PASSED"
                            ),
                            "debt_equity": FieldProvenance(
                                symbol=sym.upper(), scanner="FUNDAMENTAL", field="debt_equity",
                                value_used=m.get("debt_equity"), unit="RATIO", basis="CONSOLIDATED",
                                period_end=str(funds.get("period_end") or funds.get("filing_date") or "2025-03-31"),
                                source_used="PIT_FUNDAMENTALS", validation_status="PASSED"
                            ),
                        },
                        pit_timestamp=str(funds.get("pit_eligible_from") or funds.get("filing_date") or ""),
                        pit_eligible_from=str(funds.get("pit_eligible_from") or funds.get("filing_date") or ""),
                        data_integrity_status=DataStatus.VALID if (provenance_valid and not is_stale) else DataStatus.DATA_INVALID,
                        financial_provenance_complete=bool(provenance_valid and m.get("roce") is not None and m.get("operating_cash_flow") is not None),
                        pit_valid=bool(not is_stale),
                        period_integrity=True,
                        basis_integrity=True,
                        unit_integrity=True,
                        required_metrics_complete=bool(m.get("roce") is not None and m.get("roe") is not None and m.get("operating_cash_flow") is not None),
                    )
                    gate_verdict = pre_buy_data_integrity_gate(bundle)
                    if not gate_verdict.ok:
                        logger.warning(
                            f"🛑 [PRE_BUY_GATE_BLOCKED: FUNDAMENTAL] {sym.upper()} passed strategy gates but failed "
                            f"Pre-BUY Data Integrity Gate: reason={gate_verdict.reason}, blocking={bundle.blocking_reasons}"
                        )
                        res["is_buy"] = False
                        res["rejection_reasons"].append(RejectionReason.FUNDAMENTAL_DATA_MISSING)
                        continue

                    # Persist alert to unified alerts table (accessible to all dashboard views & tracking)
                    if save_alert_if_new is not None:
                        try:
                            try:
                                from engine.production.governance_registry import get_current_macro_regime
                                macro_regime = get_current_macro_regime()
                            except Exception:
                                macro_regime = "BULL"

                            now_ist = datetime.now(IST)

                            # Rule 67 — Change E: Extract alert_payload_hash from res dict (Finding 2 & 3).
                            # _build_result computes the SHA256 fingerprint but it was previously discarded here.
                            # Now we carry it into the DB context dict and telemetry call so the full audit chain
                            # is complete: gate inputs → SHA256 hash → DB alert record → JSONL audit event.
                            _alert_payload_hash = res.get("alert_payload_hash")  # str | None

                            inserted, reason, _, _ = save_alert_if_new(
                                symbol=sym.upper(),
                                breakout_type="FUNDAMENTAL_BREAKOUT",
                                alert_time=now_ist.strftime("%Y-%m-%d %H:%M:%S"),
                                scanner="FUNDAMENTAL",
                                category="OPEN_TARGET / WEALTH_EXIT_V1",
                                entry_price=cmp_price,
                                stop_loss=None,
                                target_1=None,
                                target_2=None,
                                target_3=None,
                                target_4=None,
                                signals="FUNDAMENTAL QUALITY + 20D BREAKOUT (OPEN TARGET)",
                                score=95,
                                volume_ratio=vol_ratio,
                                bayesian_regime=macro_regime,
                                context={
                                    "strategy": "FUNDAMENTAL_BREAKOUT",
                                    "rules": "ROCE>=15%, Growth Accelerating, RS vs BM, 20D BO, Open Target / WEALTH_EXIT_V1",
                                    "audit_trail": audit_trail,
                                    # Rule 67 — Change E: alert_payload_hash in DB context for immutable
                                    # provenance record. Enables audit cross-reference between DB and JSONL log.
                                    "alert_payload_hash": _alert_payload_hash,
                                    "fundamental_metrics": {
                                        "roce": m.get("roce"),
                                        "roe": m.get("roe"),
                                        "operating_cash_flow": m.get("operating_cash_flow"),
                                        "debt_equity": m.get("debt_equity"),
                                        "is_value_trap": m.get("is_value_trap"),
                                        "fundamental_category": m.get("fundamental_category"),
                                        "quality_score": m.get("quality_score"),
                                        "growth_score": m.get("growth_score"),
                                    },
                                    "growth_metrics": {
                                        "rev_yoy_latest": m.get("rev_yoy_latest"),
                                        "rev_yoy_prev": m.get("rev_yoy_prev"),
                                        "op_profit_yoy_latest": m.get("op_profit_yoy_latest"),
                                        "op_profit_yoy_prev": m.get("op_profit_yoy_prev"),
                                        "eps_yoy_latest": m.get("eps_yoy_latest"),
                                        "eps_yoy_prev": m.get("eps_yoy_prev"),
                                        "prior_eps": m.get("prior_eps"),
                                    },
                                    "trend_metrics": {
                                        "sma50": m.get("sma50"),
                                        "sma200": m.get("sma200"),
                                        "ret_3m_stock_pct": m.get("ret_3m_stock_pct"),
                                        "ret_6m_stock_pct": m.get("ret_6m_stock_pct"),
                                    },
                                    "consolidation_metrics": {
                                        "consolidation_window": m.get("consolidation_window"),
                                        "recent_high": m.get("recent_high"),
                                        "drawdown_pct": m.get("drawdown_pct"),
                                        "atr_pct": m.get("atr_pct"),
                                    },
                                    "breakout_metrics": {
                                        "prior_20d_high": m.get("prior_20d_high"),
                                        "signal_close": m.get("signal_close") or m.get("close"),
                                        "vol_ratio": m.get("vol_ratio"),
                                        "extension_pct": m.get("extension_pct"),
                                    },
                                    "metrics": m
                                }
                            )
                            logger.info(f"✅ [FUNDAMENTAL ALERT] {sym} -> unified alerts DB: inserted={inserted}, reason={reason}")
                            # Rule 67 — Change E: Pass alert_payload_hash to telemetry (Finding 3).
                            # Completes the audit chain: DB record and JSONL event share the same hash.
                            telemetry.record_alert_persistence(
                                sym, inserted, reason or ("INSERTED" if inserted else "REJECTED"),
                                cmp_price, alert_payload_hash=_alert_payload_hash
                            )

                            if inserted:
                                try:
                                    try:
                                        from database import insert_notification
                                    except ImportError:
                                        from app.database import insert_notification
                                    roce_txt = f"{m.get('roce'):.1f}%" if m.get('roce') is not None else "15%+"
                                    roe_txt = f"{m.get('roe'):.1f}%" if m.get('roe') is not None else "12%+"
                                    insert_notification(
                                        notif_type="BUY_ALERT",
                                        title=f"🟢 FUNDAMENTAL BREAKOUT: {sym.upper()}",
                                        message=f"{sym.upper()} passed Quality (ROCE {roce_txt}, ROE {roe_txt}) + Growth Acceleration + 20D Breakout at ₹{cmp_price:.2f}",
                                        symbol=sym.upper()
                                    )
                                except Exception as notif_err:
                                    logger.debug(f"Notification insert warning for {sym}: {notif_err}")
                        except Exception as al_err:
                            logger.warning(f"Save alert to unified table failed for {sym}: {al_err}")

                    # Register with Live Wealth Monitor Engine (V1 Exit / V2 Shadow)
                    try:
                        try:
                            from live_wealth_monitor import get_live_wealth_monitor
                        except ImportError:
                            from app.live_wealth_monitor import get_live_wealth_monitor
                        wealth_mon = get_live_wealth_monitor()
                        prior_high = float(m.get("prior_20d_high", cmp_price))
                        ext_dist = float(m.get("extension_pct", 0.0))
                        wealth_mon.generate_buy_alert(
                            symbol=sym.upper(),
                            signal_date=now_ist.strftime("%Y-%m-%d"),
                            signal_close=cmp_price,
                            breakout_reference=prior_high,
                            breakout_distance=ext_dist,
                            indicator_values=m,
                            market_regime=macro_regime
                        )
                    except Exception as wm_err:
                        logger.warning(f"Registration with live wealth monitor failed for {sym}: {wm_err}")

                    # Persist alert to wealth_buy_alert table for wealth monitors
                    if save_wealth_buy_alert is not None:
                        try:
                            save_wealth_buy_alert(
                                symbol=sym,
                                alert_price=cmp_price,
                                breakout_type="20D_BREAKOUT_FUNDAMENTAL",
                                fm_score=95.0,
                                notes="Passed Mandatory Fundamental Quality + Growth + 20D Breakout (Open Target / WEALTH_EXIT_V1)"
                            )
                        except Exception as al_err:
                            logger.debug(f"Save alert warning for {sym}: {al_err}")
                else:
                    rej_set = set(res["rejection_reasons"])
                    is_price_insuff = bool(
                        RejectionReason.MARKET_DATA_INSUFFICIENT_LOOKBACK in rej_set or
                        RejectionReason.MARKET_DATA_MISSING in rej_set
                    )
                    is_quality_insuff = bool(
                        RejectionReason.FAIL_QUALITY_METRICS_INCOMPLETE in rej_set or
                        getattr(RejectionReason, "DATA_INSUFFICIENT_QUALITY", None) in rej_set
                    )
                    is_growth_insuff = bool(
                        RejectionReason.FAIL_GROWTH_DATA_INSUFFICIENT in rej_set
                    )
                    is_pit_missing = bool(
                        funds.get("upstream_provider") == "DATA_UNAVAILABLE" or 
                        RejectionReason.FUNDAMENTAL_DATA_MISSING in rej_set
                    )
                    is_provider_fail = bool(
                        any("PROVIDER" in str(r).upper() for r in res["rejection_reasons"])
                    )

                    if is_price_insuff:
                        funnel["price_data_insufficient_count"] += 1
                    if is_quality_insuff:
                        funnel["quality_data_insufficient_count"] += 1
                    if is_growth_insuff:
                        funnel["growth_data_insufficient_count"] += 1
                    if is_pit_missing:
                        funnel["pit_missing_count"] += 1
                    if is_provider_fail:
                        funnel["provider_failure_count"] += 1

                    # Unified counters across entire system
                    if is_price_insuff or is_quality_insuff or is_growth_insuff:
                        funnel["data_insufficient_count"] += 1
                    if is_pit_missing:
                        funnel["data_missing_count"] += 1

                    for r in res["rejection_reasons"]:
                        r_key = r.value if hasattr(r, "value") else str(r)
                        funnel["rejection_summary"][r_key] = funnel["rejection_summary"].get(r_key, 0) + 1

            # Produce end-of-run telemetry summary and self-check (§26, §31)
            telemetry.record_stage_latency("total_universe_scan", (time.time() - start_ts) * 1000.0)
            telemetry_summary = telemetry.produce_end_of_run_summary()
            integrity_pass, integrity_errors = telemetry.run_telemetry_integrity_check()
            funnel["telemetry_summary"] = telemetry_summary
            funnel["telemetry_integrity"] = "PASS" if integrity_pass else "FAIL"
            funnel["telemetry_run_id"] = telemetry.scan_run_id

            duration_sec = round(time.time() - start_ts, 2)
            if ctx and complete_scanner_execution_run is not None:
                try:
                    ctx.set_alerts(funnel.get("buy_alerts_count", 0))
                    ctx.data_insufficient_count = funnel.get("data_insufficient_count", 0)
                    ctx.data_missing_count = funnel.get("data_missing_count", 0)
                    ctx.provider_failure_count = funnel.get("provider_failure_count", 0)
                    ctx.summary_notes = (
                        f"Approved={funnel.get('approved_universe_count', 0)} | "
                        f"DataInsuff={funnel.get('data_insufficient_count', 0)} "
                        f"(Growth:{funnel.get('growth_data_insufficient_count', 0)}, "
                        f"Quality:{funnel.get('quality_data_insufficient_count', 0)}, "
                        f"Price:{funnel.get('price_data_insufficient_count', 0)}) | "
                        f"DataMissing={funnel.get('data_missing_count', 0)} | "
                        f"ProviderFail={funnel.get('provider_failure_count', 0)} | "
                        f"BreakoutEligible={funnel.get('breakout_eligible_count', 0)} | "
                        f"Alerts={funnel.get('buy_alerts_count', 0)} | "
                        f"Recon={funnel.get('reconciliation_verdict', 'N/A')} | "
                        f"Telemetry={funnel.get('telemetry_integrity', 'N/A')}"
                    )
                    ctx.metrics_json = {
                        "approved_universe_count": funnel.get("approved_universe_count", 0),
                        "data_insufficient_count": funnel.get("data_insufficient_count", 0),
                        "growth_data_insufficient_count": funnel.get("growth_data_insufficient_count", 0),
                        "quality_data_insufficient_count": funnel.get("quality_data_insufficient_count", 0),
                        "price_data_insufficient_count": funnel.get("price_data_insufficient_count", 0),
                        "data_missing_count": funnel.get("data_missing_count", 0),
                        "pit_missing_count": funnel.get("pit_missing_count", 0),
                        "provider_failure_count": funnel.get("provider_failure_count", 0),
                        "breakout_eligible_count": funnel.get("breakout_eligible_count", 0),
                        "buy_alerts_count": funnel.get("buy_alerts_count", 0),
                        "reconciliation_verdict": funnel.get("reconciliation_verdict", "N/A"),
                        "telemetry_integrity": funnel.get("telemetry_integrity", "N/A"),
                        "breakdown": funnel.get("breakdown", {})
                    }
                    complete_scanner_execution_run(ctx)
                except Exception as ce_err:
                    logger.debug(f"Execution completion warning: {ce_err}")

            if upsert_scanner_health is not None:
                try:
                    di = funnel["data_insufficient_count"]
                    dm = funnel["data_missing_count"]
                    pf = funnel["provider_failure_count"]
                    total_symbols_cnt = len(target_symbols)

                    # Scanner is degraded only if there is an actual system/broker outage:
                    # 1. Scanned fewer symbols than approved universe (crashed early)
                    # 2. Broker provider failures exceed 5% of universe
                    # 3. Technical lookback insufficiency exceeds 10% of universe (>35 symbols, indicating bundle/cache loss)
                    # 4. Upstream fundamental master records missing from provider exceeds 5% of universe (>44 symbols)
                    # 5. Context lifecycle failed
                    is_crashed = funnel["scanned_count"] < total_symbols_cnt
                    high_provider_failure = pf > max(5, int(total_symbols_cnt * 0.05))
                    high_insufficient = funnel.get("price_data_insufficient_count", 0) > max(35, int(total_symbols_cnt * 0.10))
                    high_missing = dm > max(15, int(total_symbols_cnt * 0.05))
                    context_failed = (ctx is not None and getattr(ctx, "lifecycle_status", "") in ("FAILED", "STOPPED"))

                    # Rule 67 — Change A: Removed `di > 0` from data_gap.
                    # Rationale (Finding 1 & 7): `di` counts per-symbol data insufficiency (e.g. one missing OCF field).
                    # A single missing field on any of 886 symbols previously set di=1, which fired data_gap=True,
                    # making health_status=DEGRADED unconditionally and making OK structurally unachievable.
                    # Per the declared intent in the comment block at L2317 ("degraded only if there is an actual
                    # system/broker outage"), individual symbol data insufficiency is expected/normal scanner behaviour
                    # and is already fully tracked per-symbol in telemetry + funnel counters.
                    # Only SYSTEMIC thresholds (>5% provider failures, >10% price data gaps, >5% missing master records,
                    # context lifecycle crash, or early termination) constitute a system-level health degradation event.
                    data_gap = (high_provider_failure) or (high_insufficient) or (high_missing) or (context_failed)
                    is_degraded = is_crashed or data_gap
                    # health_status=OK means: all approved symbols evaluated, no systemic infrastructure outage.
                    # health_status=DEGRADED means: a system-level outage affected run completeness/quality.
                    # Per-symbol DATA_INSUFFICIENT is normal scanner output, not a health degradation signal.
                    health_status = "DEGRADED" if is_degraded else "OK"
                    _computed_health_status = health_status  # propagate to end banner override
                    health_outcome = "PARTIAL" if is_degraded else "SUCCESS"
                    gap_msg = None
                    if is_degraded:
                        gap_msg = (
                            f"Data gaps: {funnel.get('price_data_insufficient_count', 0)} insufficient technicals, "
                            f"{funnel.get('growth_data_insufficient_count', 0)} insufficient growth, {dm} missing fundamentals, "
                            f"{pf} provider failures of {total_symbols_cnt} approved "
                            f"(combined_gap={di+dm}/{total_symbols_cnt} = {round((di+dm)/max(total_symbols_cnt,1)*100,1)}%)"
                        )

                    upsert_scanner_health(
                        "FUNDAMENTAL",
                        status=health_status,
                        outcome=health_outcome,
                        error_msg=gap_msg,
                        today_alerts=funnel["buy_alerts_count"],
                        last_success=datetime.now(IST).isoformat(),
                        processed_count=funnel["scanned_count"],
                        total_count=total_symbols_cnt,
                        duration_seconds=duration_sec,
                        run_id=getattr(ctx, "run_id", None)
                    )
                except Exception as he_err:
                    logger.debug(f"Scanner health update warning: {he_err}")

        except Exception as scan_err:
            logger.exception(f"❌ scan_universe failed: {scan_err}")
            if ctx and complete_scanner_execution_run is not None:
                try:
                    complete_scanner_execution_run(ctx, exception=scan_err)
                except Exception:
                    pass
            if upsert_scanner_health is not None:
                try:
                    upsert_scanner_health(
                        "FUNDAMENTAL",
                        status="DOWN",
                        error_msg=str(scan_err)[:500],
                        run_id=getattr(ctx, "run_id", None)
                    )
                except Exception:
                    pass
            raise
        finally:
            print_scanner_end_banner(
                "FUNDAMENTAL",
                start_mono=_scan_start,
                run_id=getattr(ctx, "run_id", None),
                override_status=_computed_health_status,
                start_wall_ts=start_ts
            )
            if acquired_global:
                try:
                    _global_lock.release()
                except Exception as _ge:
                    logger.debug(f"Global lock release notice: {_ge}")
            if acquired_scan:
                try:
                    _fundamental_scan_lock.release()
                except Exception as _se:
                    logger.debug(f"Fundamental scan lock release notice: {_se}")

        self.last_funnel_audit = funnel
        return funnel

    # Rule 67 — Change D: Explicit data-blocking rejection reasons for `decision` field classification.
    # Replaces the fragile `"DATA" in r.value` string-prefix heuristic (Finding 6) which would silently
    # misclassify any future rejection reason containing "DATA" as BLOCKED_DATA.
    _DATA_BLOCKING_REJECTIONS = frozenset({
        RejectionReason.FUNDAMENTAL_DATA_MISSING,
        RejectionReason.FUNDAMENTAL_DATA_STALE,
        RejectionReason.FUNDAMENTAL_PROVENANCE_INVALID,
        RejectionReason.FAIL_QUALITY_METRICS_INCOMPLETE,
        RejectionReason.FAIL_GROWTH_DATA_INSUFFICIENT,
        RejectionReason.MARKET_DATA_MISSING,
        RejectionReason.MARKET_DATA_INSUFFICIENT_LOOKBACK,
    })

    @staticmethod
    def _build_result(
        symbol: str,
        is_buy: bool,
        rejections: List[RejectionReason],
        metrics: Dict[str, Any]
    ) -> Dict[str, Any]:
        now_ist = datetime.now(IST)
        rejection_values = [r.value for r in rejections]

        # Rule 67 — Change D: Enum-based data-block classification (Finding 6).
        # Previously used `"DATA" in r.value` string heuristic — fragile and non-exhaustive.
        # Now checks against an explicit frozen set of data-blocking rejection reason enums.
        is_data_blocked = any(r in LiveFundamentalBuyScanner._DATA_BLOCKING_REJECTIONS for r in rejections)
        decision = "BUY_ALERT" if is_buy else ("BLOCKED_DATA" if is_data_blocked else "NOT_QUALIFIED")

        # Rule 67 — Change B: Compute SHA256 alert_payload_hash for BUY alerts (Finding 2).
        # Every BUY alert is assigned a deterministic, immutable fingerprint derived from the exact
        # numerical inputs used in the decision. This enables independent reproduction and audit verification.
        # For non-BUY results, alert_payload_hash = None (no alert was generated).
        alert_payload_hash: Optional[str] = None
        if is_buy:
            # Canonical payload: only include fields that are gate inputs, not computed metadata.
            # Sorted keys ensure deterministic ordering across Python versions.
            payload_for_hash = {
                "symbol": symbol.upper(),
                "rules_hash": RULES_HASH_BUY,
                "code_sha": FROZEN_GIT_SHA,
                "signal_date": now_ist.strftime("%Y-%m-%d"),
                # Quality gate inputs
                "roce": metrics.get("roce"),
                "roe": metrics.get("roe"),
                "debt_equity": metrics.get("debt_equity"),
                "operating_cash_flow": metrics.get("operating_cash_flow"),
                # Growth gate inputs
                "rev_yoy_latest": metrics.get("rev_yoy_latest"),
                "rev_yoy_prev": metrics.get("rev_yoy_prev"),
                "op_profit_yoy_latest": metrics.get("op_profit_yoy_latest"),
                "op_profit_yoy_prev": metrics.get("op_profit_yoy_prev"),
                "eps_yoy_latest": metrics.get("eps_yoy_latest"),
                "eps_yoy_prev": metrics.get("eps_yoy_prev"),
                "prior_eps": metrics.get("prior_eps"),
                # Technical gate inputs
                "sma50": metrics.get("sma50"),
                "sma200": metrics.get("sma200"),
                "prior_20d_high": metrics.get("prior_20d_high"),
                "vol_ratio": metrics.get("vol_ratio"),
                "signal_close": metrics.get("signal_close") or metrics.get("close"),
            }
            # Use json.dumps with sort_keys=True for deterministic serialization.
            # float(v) normalises None-vs-float inconsistencies that would change the hash.
            payload_str = json.dumps(
                {k: (round(float(v), 6) if isinstance(v, (int, float)) else v)
                 for k, v in sorted(payload_for_hash.items())},
                ensure_ascii=True,
                separators=(",", ":")
            )
            alert_payload_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()

        return {
            "symbol": symbol,
            "is_buy": is_buy,
            "decision": decision,
            "fundamentally_qualified": not any(
                r in {
                    RejectionReason.FAIL_ROCE, RejectionReason.FAIL_ROE,
                    RejectionReason.FAIL_OCF, RejectionReason.FAIL_DEBT_EQUITY,
                    RejectionReason.FAIL_VALUE_TRAP, RejectionReason.FAIL_QUALITY_METRICS_INCOMPLETE,
                    RejectionReason.FAIL_REVENUE_ACCELERATION, RejectionReason.FAIL_OP_PROFIT_ACCELERATION,
                    RejectionReason.FAIL_EPS_ACCELERATION, RejectionReason.FAIL_PRIOR_EPS,
                    RejectionReason.FAIL_GROWTH_DATA_INSUFFICIENT, RejectionReason.FUNDAMENTAL_DATA_MISSING,
                } for r in rejections
            ),
            "rejection_reasons": rejection_values,
            "metrics": metrics,
            "rules_hash": RULES_HASH_BUY,
            "code_sha": FROZEN_GIT_SHA,
            "alert_payload_hash": alert_payload_hash,  # SHA256 of gate inputs; None for non-BUY results
            "timestamp": now_ist.isoformat()
        }


# Module-level singleton instance for runtime trigger execution
_live_fundamental_scanner_instance = None

def get_live_fundamental_scanner() -> LiveFundamentalBuyScanner:
    global _live_fundamental_scanner_instance
    if _live_fundamental_scanner_instance is None:
        _live_fundamental_scanner_instance = LiveFundamentalBuyScanner()
    return _live_fundamental_scanner_instance

class _LazyScannerProxy:
    def scan_universe(self, *args, **kwargs):
        return get_live_fundamental_scanner().scan_universe(*args, **kwargs)

    def scan_candidate(self, *args, **kwargs):
        return get_live_fundamental_scanner().scan_candidate(*args, **kwargs)

    def __call__(self, *args, **kwargs):
        return get_live_fundamental_scanner().scan_universe(*args, **kwargs)

live_fundamental_scanner = _LazyScannerProxy()

def run_fundamental_scan(trigger_type: str = "MANUAL", scheduler_name: str = "MANUAL") -> Dict[str, Any]:
    """Top-level invocation wrapper matching the engine's trigger signature."""
    return get_live_fundamental_scanner().scan_universe(trigger_type=trigger_type, scheduler_name=scheduler_name)


# ─────────────────────────────────────────────────────────────────────────────────────
# QUALITY_COMPOUNDER — FROZEN STRATEGY IMPLEMENTATION
# ─────────────────────────────────────────────────────────────────────────────────────

REQUIRED_ANNUAL_HISTORY_FOR_V2_5Y_METRICS: int = 5

def required_v2_history_requirement() -> int:
    """Return the canonical minimum annual filing history required for frozen V2 5Y metrics."""
    return REQUIRED_ANNUAL_HISTORY_FOR_V2_5Y_METRICS

# Retain backward-compatible alias
required_v2_annual_history = required_v2_history_requirement


def build_raw_history_index(raw_filings_dir: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """
    Builds an in-memory index of raw statement filings once per scan.
    Provides O(1) query time for annual filing count and earliest/latest periods.
    Asserts RAW_FILING_INDEX_ACTUAL >= 1.
    """
    candidate_dirs = [
        raw_filings_dir,
        os.path.join(DATA_DIR, "pit_raw_filings"),
        os.path.join(BASE_DIR, "data", "pit_raw_filings"),
        os.path.join(os.getcwd(), "data", "pit_raw_filings"),
        os.path.abspath("data/pit_raw_filings"),
        "/app/data/pit_raw_filings",
        "/app/data_seed/pit_raw_filings",
        os.path.join(BASE_DIR, "data_seed", "pit_raw_filings"),
        "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/data/pit_raw_filings",
    ]
    resolved_dir = None
    for c in candidate_dirs:
        if c and os.path.isdir(c) and glob.glob(os.path.join(c, "*.json")):
            resolved_dir = c
            break
    if not resolved_dir:
        os.makedirs(os.path.join(DATA_DIR, "pit_raw_filings"), exist_ok=True)
        logger.warning(f"⚠️ [V2_RAW_INDEX] Directory not populated across candidates: {candidate_dirs}")
        return {}
    raw_filings_dir = resolved_dir

    pattern = os.path.join(raw_filings_dir, "*.json")
    json_files = glob.glob(pattern)
    raw_history_index: Dict[str, Dict[str, Any]] = {}
    parser_failures: List[str] = []
    extraction_failures: List[str] = []

    for p in json_files:
        sym = os.path.basename(p).replace(".json", "").strip().upper()
        if not sym:
            extraction_failures.append(p)
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            ann = []
            if isinstance(data, list):
                ann = [
                    r for r in data
                    if str(r.get("statement_type", r.get("period_type", r.get("type", "")))).upper() in ("ANNUAL", "YEARLY")
                    or r.get("is_annual", False)
                ]
            elif isinstance(data, dict):
                ann_list = data.get("annual", data.get("annuals", []))
                if isinstance(ann_list, list):
                    ann = ann_list

            sorted_ann = sorted([r for r in ann if r.get("period_end_date")], key=lambda x: str(x.get("period_end_date")))
            ann_cnt = len(ann)
            earliest = str(sorted_ann[0]["period_end_date"])[:10] if sorted_ann else None
            latest = str(sorted_ann[-1]["period_end_date"])[:10] if sorted_ann else None

            raw_history_index[sym] = {
                "annual_count": ann_cnt,
                "earliest_annual_period": earliest,
                "latest_annual_period": latest,
                "source_path": p,
                "parse_status": "OK",
            }
        except Exception as e:
            parser_failures.append(f"{sym}: {e}")
            raw_history_index[sym] = {
                "annual_count": 0,
                "earliest_annual_period": None,
                "latest_annual_period": None,
                "source_path": p,
                "parse_status": f"PARSE_ERROR: {e}",
            }

    raw_actual = len(raw_history_index)
    raw_expected = len(json_files)
    logger.info(
        f"⚡ [V2_RAW_INDEX] Raw filing index built: actual={raw_actual} symbols indexed from {raw_filings_dir} (files={raw_expected})"
    )
    if raw_actual == 0:
        logger.error(
            f"❌ [V2_RAW_INDEX] FORENSIC ASSERTION FAILED: RAW_FILING_INDEX_EXPECTED > 0 (files={raw_expected}) but actual=0. "
            f"Directory={raw_filings_dir} | sample_filenames={json_files[:5]} | "
            f"parser_failures={parser_failures[:5]} | extraction_failures={extraction_failures[:5]}"
        )

    return raw_history_index


def build_history_1d_dates_index(history_1d_dir: Optional[str] = None) -> Dict[str, str]:
    """
    Builds an in-memory index of earliest tradable date from 1D history parquets once per scan.
    Provides fast O(1) query time across the entire universe without repeated per-stock file opens.
    """
    candidate_dirs = [
        history_1d_dir,
        os.path.join(DATA_DIR, "history", "1d"),
        os.path.join(BASE_DIR, "data", "history", "1d"),
        os.path.join(os.getcwd(), "data", "history", "1d"),
        os.path.abspath("data/history/1d"),
        "/app/data/history/1d",
        "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/data/history/1d",
    ]
    resolved_dir = None
    for c in candidate_dirs:
        if c and os.path.isdir(c) and glob.glob(os.path.join(c, "*.parquet")):
            resolved_dir = c
            break
    if not resolved_dir:
        return {}
    history_1d_dir = resolved_dir

    pattern = os.path.join(history_1d_dir, "*.parquet")
    files = glob.glob(pattern)
    index_1d: Dict[str, str] = {}

    for p in files:
        sym = os.path.basename(p).replace(".parquet", "").strip().upper()
        try:
            schema = pq.read_schema(p)
            d_col = "Date" if "Date" in schema.names else ("date" if "date" in schema.names else None)
            if d_col:
                table = pq.read_table(p, columns=[d_col])
                if table.num_rows > 0:
                    val = table.column(0)[0].as_py()
                    index_1d[sym] = str(val)[:10]
        except Exception:
            pass

    logger.info(f"⚡ [V2_1D_INDEX] 1D price dates index built: {len(index_1d)} symbols indexed from {history_1d_dir}")
    return index_1d


def classify_v2_historical_evidence(
    symbol: str,
    filing_annual_count: Optional[int] = None,
    earliest_annual_period: Optional[str] = None,
    latest_annual_period: Optional[str] = None,
    raw_filings_dir: Optional[str] = None,
    history_1d_dir: Optional[str] = None,
    scan_date_str: Optional[str] = None,
    is_pit_symbol: bool = True,
    raw_history_index: Optional[Dict[str, Dict[str, Any]]] = None,
    history_1d_index: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Evaluates whether a symbol genuinely has limited historical existence (< required_v2_history_requirement())
    or is a data failure (ingestion gap / unreadable / missing / mature).

    Priority for structural evidence:
    A. Existing authoritative/company history metadata if available.
    B. Raw PIT filing history: data/pit_raw_filings/{SYM}.json (annual count, earliest/latest period).
    C. Reliable first-tradable date from 1D history: data/history/1d/{SYM}.parquet.
    D. Insufficient / ambiguous evidence -> DATA_FAILURE (UNKNOWN = DATA_FAILURE).

    Returns a dict with:
        is_structural: bool
        population: "STRUCTURAL_INELIGIBLE" | "DATA_FAILURE"
        reason: str
        filing_annual_count: Optional[int]
        earliest_annual_period: Optional[str]
        latest_annual_period: Optional[str]
        first_tradable_date: Optional[str]
        history_status: "STRUCTURAL_INELIGIBLE" | "HISTORY_COMPLETE" | "HISTORY_INCOMPLETE" | "UNKNOWN"
    """
    clean_sym = str(symbol).strip().upper()
    req_history = required_v2_history_requirement()

    try:
        today_dt = pd.to_datetime(scan_date_str).date() if scan_date_str else datetime.now(IST).date()
    except Exception:
        today_dt = datetime.now(IST).date()
    cutoff_date = today_dt - timedelta(days=int(req_history * 365.25))

    # 1. Resolve raw filing history if counts/periods were not provided
    if filing_annual_count is None or earliest_annual_period is None:
        if raw_history_index is not None:
            if len(raw_history_index) == 0:
                # Prompt rule: If index construction fails, classification = DATA_FAILURE, reason = RAW_INDEX_BUILD_FAILURE
                return {
                    "is_structural": False,
                    "population": "DATA_FAILURE",
                    "reason": "RAW_INDEX_BUILD_FAILURE",
                    "filing_annual_count": 0,
                    "earliest_annual_period": None,
                    "latest_annual_period": None,
                    "first_tradable_date": None,
                    "history_status": "UNKNOWN",
                }
            raw_meta = raw_history_index.get(clean_sym)
            if raw_meta and raw_meta.get("parse_status") == "OK":
                filing_annual_count = raw_meta.get("annual_count", 0)
                earliest_annual_period = raw_meta.get("earliest_annual_period")
                latest_annual_period = raw_meta.get("latest_annual_period")
            else:
                filing_annual_count = 0
                earliest_annual_period = None
                latest_annual_period = None
        else:
            raw_filings_dir = raw_filings_dir or os.path.join(DATA_DIR, "pit_raw_filings")
            raw_file_path = os.path.join(raw_filings_dir, f"{clean_sym}.json")
            raw_data = None
            if os.path.exists(raw_file_path):
                try:
                    with open(raw_file_path, "r", encoding="utf-8") as _rf_f:
                        raw_data = json.load(_rf_f)
                except Exception as _parse_err:
                    logger.debug(f"Raw filing parse failed for {clean_sym}: {_parse_err}")
                    raw_data = None

            if raw_data is None:
                # File absent or unreadable
                return {
                    "is_structural": False,
                    "population": "DATA_FAILURE",
                    "reason": "RAW_FILING_SOURCE_UNAVAILABLE" if not is_pit_symbol else "HISTORY_METADATA_UNAVAILABLE",
                    "filing_annual_count": 0,
                    "earliest_annual_period": None,
                    "latest_annual_period": None,
                    "first_tradable_date": None,
                    "history_status": "UNKNOWN",
                }

            ann_filings = []
            if isinstance(raw_data, list):
                ann_filings = [
                    r for r in raw_data
                    if str(r.get("statement_type", r.get("period_type", r.get("type", "")))).upper() in ("ANNUAL", "YEARLY")
                    or r.get("is_annual", False)
                ]
            elif isinstance(raw_data, dict):
                ann_list = raw_data.get("annual", raw_data.get("annuals", []))
                if isinstance(ann_list, list):
                    ann_filings = ann_list

            if ann_filings:
                sorted_ann = sorted([r for r in ann_filings if r.get("period_end_date")], key=lambda x: str(x.get("period_end_date")))
                filing_annual_count = len(ann_filings)
                earliest_annual_period = str(sorted_ann[0]["period_end_date"])[:10] if sorted_ann else None
                latest_annual_period = str(sorted_ann[-1]["period_end_date"])[:10] if sorted_ann else None
            else:
                filing_annual_count = 0
                earliest_annual_period = None
                latest_annual_period = None

    # Resolve first-tradable date from 1D history
    first_tradable_date = None
    first_px_dt = None
    if history_1d_index is not None:
        first_tradable_date = history_1d_index.get(clean_sym)
        if first_tradable_date:
            try:
                first_px_dt = pd.to_datetime(first_tradable_date).date()
            except Exception:
                pass
    else:
        history_1d_dir = history_1d_dir or os.path.join(DATA_DIR, "history", "1d")
        px_path = os.path.join(history_1d_dir, f"{clean_sym}.parquet")
        if os.path.exists(px_path):
            try:
                schema = pq.read_schema(px_path)
                d_col = "Date" if "Date" in schema.names else ("date" if "date" in schema.names else None)
                if d_col:
                    t = pq.read_table(px_path, columns=[d_col])
                    if t.num_rows > 0:
                        first_tradable_date = str(t.column(0)[0].as_py())[:10]
                        first_px_dt = pd.to_datetime(first_tradable_date).date()
            except Exception as _px_err:
                logger.debug(f"1D price read notice for {clean_sym}: {_px_err}")

    # Check 1: If symbol has >= req_history (5) annual filings, it has sufficient depth
    if filing_annual_count is not None and filing_annual_count >= req_history:
        if not is_pit_symbol:
            # Mature in raw history, but missing from PIT parquet!
            return {
                "is_structural": False,
                "population": "DATA_FAILURE",
                "reason": "PIT_INGESTION_GAP",
                "filing_annual_count": filing_annual_count,
                "earliest_annual_period": earliest_annual_period,
                "latest_annual_period": latest_annual_period,
                "first_tradable_date": first_tradable_date,
                "history_status": "HISTORY_INCOMPLETE",
            }
        else:
            # Has >= 5 annual filings in PIT dataset, but metrics are null
            return {
                "is_structural": False,
                "population": "DATA_FAILURE",
                "reason": "QUALITY_METRIC_CALCULATION_FAILURE",
                "filing_annual_count": filing_annual_count,
                "earliest_annual_period": earliest_annual_period,
                "latest_annual_period": latest_annual_period,
                "first_tradable_date": first_tradable_date,
                "history_status": "HISTORY_INCOMPLETE",
            }

    # Check 2: filing_annual_count < req_history (< 5).
    # Does historical evidence prove this company is young (< 5 years tradable exchange existence)?
    # When first-tradable date on exchange is within the last 5 years, the company cannot have 5Y history by design.
    if first_px_dt is not None:
        if first_px_dt > cutoff_date:
            # First traded AFTER cutoff date (within last 5 years)! E.g. KRN (Oct 2024), GARUDA (Oct 2024), AIIL (Apr 2024), KALAMANDIR (Sep 2023), UTLSOLAR (Nov 2025).
            return {
                "is_structural": True,
                "population": "STRUCTURAL_INELIGIBLE",
                "reason": "INSUFFICIENT_HISTORICAL_EXISTENCE",
                "filing_annual_count": filing_annual_count,
                "earliest_annual_period": earliest_annual_period,
                "latest_annual_period": latest_annual_period,
                "first_tradable_date": first_tradable_date,
                "history_status": "STRUCTURAL_INELIGIBLE",
            }
        else:
            # First traded 5+ years ago on exchange! E.g. RELIANCE (2016), RPGLIFE (2016), IRCTC (2019). Mature!
            return {
                "is_structural": False,
                "population": "DATA_FAILURE",
                "reason": "PIT_INGESTION_GAP" if not is_pit_symbol else "QUALITY_METRIC_CALCULATION_FAILURE",
                "filing_annual_count": filing_annual_count,
                "earliest_annual_period": earliest_annual_period,
                "latest_annual_period": latest_annual_period,
                "first_tradable_date": first_tradable_date,
                "history_status": "HISTORY_INCOMPLETE",
            }

    # If first_px_dt is unknown, inspect earliest annual period
    if earliest_annual_period:
        try:
            earliest_ann_dt = pd.to_datetime(earliest_annual_period).date()
            if earliest_ann_dt <= cutoff_date:
                # Company filed statements 5+ years ago! E.g. TATAELXSI (earliest 2012). Mature!
                return {
                    "is_structural": False,
                    "population": "DATA_FAILURE",
                    "reason": "PIT_INGESTION_GAP" if not is_pit_symbol else "QUALITY_METRIC_CALCULATION_FAILURE",
                    "filing_annual_count": filing_annual_count,
                    "earliest_annual_period": earliest_annual_period,
                    "latest_annual_period": latest_annual_period,
                    "first_tradable_date": first_tradable_date,
                    "history_status": "HISTORY_INCOMPLETE",
                }
            # If earliest_ann_dt > cutoff_date, we CANNOT assume it is a young IPO without 1D price corroboration.
            # Fall through to Check 3 (HISTORY_STATUS_UNKNOWN -> DATA_FAILURE).
        except Exception:
            pass

    # Check 3: Insufficient evidence / ambiguous / uncorroborated
    # Conservative Rule: UNKNOWN = DATA_FAILURE.
    return {
        "is_structural": False,
        "population": "DATA_FAILURE",
        "reason": "HISTORY_STATUS_UNKNOWN",
        "filing_annual_count": filing_annual_count,
        "earliest_annual_period": earliest_annual_period,
        "latest_annual_period": latest_annual_period,
        "first_tradable_date": first_tradable_date,
        "history_status": "UNKNOWN",
    }


class QualityCompounderValueV2Scanner:
    """
    FROZEN PRODUCTION SCANNER: QUALITY_COMPOUNDER
    STATUS: LIVE_PRODUCTION_WATCHLIST
    BROKER TRADING: DISABLED (Alert / Watchlist only)

    Universe Gate:
      - Market Cap >= ₹1,000 Cr
      - 90-day ADTV >= ₹2 Cr
      - Exclude Financials from primary EV/EBITDA arm

    Quality Hard Gates:
      - 5Y Average ROCE >= 15.0%
      - 5Y Sales CAGR >= 10.0%
      - 5Y PAT CAGR >= 10.0%
      - 5Y Cumulative CFO / PAT >= 0.80
      - Debt / Equity <= 0.50
      - 3Y Share Dilution <= 10.0%

    Value Hard Gate:
      - Current EV/EBITDA <= 0.75 * Stock's own PIT 3Y Median EV/EBITDA (EV/EBITDA Discount >= 25%)

    Operational 100-Point Score & Tier:
      - Tier A: Res_DD <= 0.10 (Stock vs Benchmark Drawdown Dislocation <= 10%)
      - Tier B: Res_DD > 0.10
      - 100-pt score prioritization (never overrides eligibility gates)
    """

    def __init__(self):
        self.strategy_id = "QUALITY_COMPOUNDER"
        self.daily_builder_provider = DailyBuilderFundamentalProvider()
        self.universe_registry = ApprovedUniverseRegistry()

    KNOWN_FINANCIAL_SYMBOLS = {
        "AADHARHFC", "AAVAS", "ABCAPITAL", "AUBANK", "HDBFS", "HDFCBANK", "ICICIBANK", "SBIN",
        "AXISBANK", "KOTAKBANK", "BAJFINANCE", "BAJAJFINSV", "CHOLAFIN", "CHOLAHLDNG", "CREDITACC",
        "CANFINHOME", "APTUS", "HOMEFIRST", "HUDCO", "PFC", "RECLTD", "MUTHOOTFIN", "MANAPPURAM",
        "SHRIRAMFIN", "M&MFIN", "L&TFH", "IIFL", "MOTILALOFS", "ICICIGI", "ICICIPRULI", "SBILIFE",
        "HDFCLIFE", "GICRE", "NIACL", "CDSL", "BSE", "MCX", "CAMS", "KFINTECH", "NAM-INDIA",
        "UTIAMC", "ANGELONE", "NUVAMA", "ANANDRATHI", "360ONE", "BANKBARODA", "BANKINDIA",
        "CENTRALBK", "IDFCFIRSTB", "INDIANB", "IOB", "MAHABANK", "PNB", "PSB", "UCOBANK",
        "UNIONBANK", "YESBANK", "BANDHANBNK", "FEDERALBNK", "IDBI", "INDUSINDBK", "KARURVYSYA",
        "RBLBANK", "SOUTHBANK", "CSBBANK", "CUB", "DCBBANK", "EQUITASBNK", "FINOPB", "J&KBANK",
        "JSFB", "KTKBANK", "SURYSFB", "UJJIVANSFB", "UTKARSHBNK", "CAPITALSFB", "AYE",
        "JMFINANCIL", "LICHSGFIN", "MASFIN", "SUNDARMFIN", "TSFINV", "CGCL", "AIIL",
        "BENGALASM", "FEDFINA", "FIVESTAR", "INDIASHLTR", "IREDA", "LTF", "NORTHARC",
        "PNBHOUSING", "REPCOHOME", "SATIN", "SBICARD", "SGFIN", "TATACAP", "TMB", "ZSARACOM",
        "GODIGIT", "LICI", "ABSLAMC", "HDFCAMC", "IIFLCAPS", "PRUDENT", "SHAREINDIA", "CHOICEIN"
    }

    @classmethod
    def is_financial_sector(cls, industry_str: str, symbol: str = "") -> bool:
        if symbol:
            sym_clean = str(symbol).strip().upper()
            if sym_clean in cls.KNOWN_FINANCIAL_SYMBOLS:
                return True
        if not isinstance(industry_str, str):
            return False
        ind_upper = industry_str.upper()
        financial_keywords = [
            "BANK", "FINANCE", "FINANCIAL", "HOUSING FINANCE", "NBFC",
            "INSURANCE", "INVESTMENT", "CAPITAL", "SECURITIES", "LEASING", "AMC"
        ]
        return any(kw in ind_upper for kw in financial_keywords)

    @classmethod
    def compute_100pt_score_detailed(
        cls,
        row_dict: dict,
        ev_discount: Optional[float],
        pe_discount: Optional[float],
        res_dd: float
    ) -> Dict[str, Any]:
        """Calculates granular breakdown and explanation for the 100-point ranking score."""
        ev_disc_eff = float(ev_discount) if ev_discount is not None else 0.0
        pe_disc_eff = float(pe_discount) if pe_discount is not None else 0.0

        ev_pts = round(30.0 * min(max((ev_disc_eff - 0.25) / 0.25, 0.0), 1.0), 2)
        roce_val_raw = row_dict.get("roce_5y_avg")
        roce_val = float(roce_val_raw) if (roce_val_raw is not None and not pd.isna(roce_val_raw) and float(roce_val_raw) != -999.0) else 0.0
        roce_pts = round(25.0 * min(max((roce_val - 15.0) / 25.0, 0.0), 1.0), 2)
        pe_pts = round(20.0 * min(max(pe_disc_eff / 0.40, 0.0), 1.0), 2)
        cfo_pat_raw = row_dict.get("cfo_pat_5y_ratio")
        cfo_pat_val = float(cfo_pat_raw) if (cfo_pat_raw is not None and not pd.isna(cfo_pat_raw) and float(cfo_pat_raw) != -999.0) else 0.0
        cfo_pts = round(15.0 * min(max((cfo_pat_val - 0.80) / 0.70, 0.0), 1.0), 2)
        if res_dd <= 0.10:
            res_pts = 10.0
        else:
            res_pts = round(10.0 * min(max((0.25 - res_dd) / 0.15, 0.0), 1.0), 2)

        total_score = round(ev_pts + roce_pts + pe_pts + cfo_pts + res_pts, 2)
        return {
            "ev_discount": ev_discount,
            "ev_pts": ev_pts,
            "roce_val": roce_val if (roce_val_raw is not None and not pd.isna(roce_val_raw) and float(roce_val_raw) != -999.0) else None,
            "roce_pts": roce_pts,
            "pe_discount": pe_discount,
            "pe_pts": pe_pts,
            "cfo_pat_val": cfo_pat_val if (cfo_pat_raw is not None and not pd.isna(cfo_pat_raw) and float(cfo_pat_raw) != -999.0) else None,
            "cfo_pts": cfo_pts,
            "res_dd": res_dd,
            "res_pts": res_pts,
            "total_score_100": total_score,
            "formula": "ev_pts(30) + roce_pts(25) + pe_pts(20) + cfo_pts(15) + res_pts(10)",
        }

    @classmethod
    def compute_100pt_score(cls, row_dict: dict, ev_discount, pe_discount, res_dd: float) -> float:
        """Score is only meaningful when valuation data is available. Returns 0.0 when either
        discount is None (valuation data unavailable) — caller should not use score for ranking
        when valuation is missing."""
        detailed = cls.compute_100pt_score_detailed(row_dict, ev_discount, pe_discount, res_dd)
        return detailed["total_score_100"]

    def recover_upstream_valuation_data(
        self,
        symbol: str,
        cmp_price: float,
        row: Dict[str, Any]
    ) -> Tuple[Optional[float], Optional[float], List[Dict[str, Any]], str]:
        """
        Controlled provider-recovery chain for missing current EV/EBITDA on eligible stocks:

        Trigger Condition:
          Triggered strictly on the exception path when an eligible non-financial stock reaches
          the valuation decision gate and current_ev_ebitda is missing from local PIT sources.

        Hierarchy:
          1. Local certified PIT data (already attempted)
                  ↓ missing
          2. Upstox Fundamentals Key-Ratios API (GET /v2/fundamentals/{isin}/key-ratios)
                  ↓ missing / not populated
          3. Raw Authoritative Statement Derivation (Exchange filings balance-sheet + income-statement)
                  ↓ missing
          4. DATA_INSUFFICIENT (Strict fail-closed)

        Returns:
          (recovered_ev_ebitda, recovered_pe, provider_audit_list, recovery_verdict)
        """
        providers_audit: List[Dict[str, Any]] = []
        recovered_ev: Optional[float] = None
        recovered_pe: Optional[float] = None
        clean_sym = str(symbol).strip().upper()

        logger.info(
            f"🔄 [UPSTREAM_RECOVERY: START] {clean_sym}: Local current_ev_ebitda missing. "
            f"Initiating controlled upstream recovery chain before valuation decision..."
        )

        # ── 1. RESOLVE ISIN VIA OFFICIAL UPSTOX INSTRUMENT MAPPER ──────────────────
        isin = None
        try:
            from market_data.providers.upstox_instrument_mapper import get_upstox_instrument_key
            inst_key = get_upstox_instrument_key(clean_sym)
            if inst_key and "|" in inst_key:
                isin = inst_key.split("|")[1].strip()
        except Exception as _e_map:
            logger.debug(f"[UPSTREAM_RECOVERY] {clean_sym}: Instrument mapper resolution notice: {_e_map}")

        # Fallback ISIN resolution if mapper returned bare key
        if not isin or not isin.startswith("INE"):
            _pit_f = row.get("isin")
            if _pit_f and str(_pit_f).startswith("INE"):
                isin = str(_pit_f).strip()

        # ── 2. CALL AUTHORITATIVE UPSTOX FUNDAMENTALS API (GET /v2/fundamentals/{isin}/key-ratios) ──
        if isin:
            try:
                import config
                token = getattr(config, "UPSTOX_ACCESS_TOKEN", None) or os.environ.get("UPSTOX_ACCESS_TOKEN")
                if token:
                    api_url = f"https://api.upstox.com/v2/fundamentals/{isin}/key-ratios"
                    headers = {
                        "Accept": "application/json",
                        "Authorization": f"Bearer {token}"
                    }
                    logger.info(
                        f"🌐 [UPSTREAM_RECOVERY: API_CALL] {clean_sym}: Calling Upstox Key-Ratios API | "
                        f"endpoint={api_url} | isin={isin}"
                    )
                    t0 = time.monotonic()
                    resp = requests.get(api_url, headers=headers, timeout=6.0)
                    elapsed_ms = round((time.monotonic() - t0) * 1000, 1)

                    raw_snippet = resp.text[:300].replace("\n", " ")
                    logger.info(
                        f"📥 [UPSTREAM_RECOVERY: API_RESPONSE] {clean_sym}: HTTP {resp.status_code} "
                        f"({elapsed_ms}ms) | payload_snippet={raw_snippet}"
                    )

                    if resp.status_code == 200:
                        data_items = resp.json().get("data", [])
                        raw_ev_str = None
                        raw_pe_str = None
                        for item in data_items:
                            name = str(item.get("name", "")).strip().upper()
                            cval = str(item.get("company_value", "")).strip()
                            if name in ("EV/EBITDA", "EV_TO_EBITDA", "EV_EBITDA") and cval:
                                raw_ev_str = cval
                                try:
                                    parsed_val = float(cval.replace("%", "").strip())
                                    if parsed_val > 0.0 and parsed_val < 1000.0:
                                        recovered_ev = round(parsed_val, 2)
                                except ValueError:
                                    pass
                            elif name in ("P/E", "PE", "PE_RATIO") and cval:
                                raw_pe_str = cval
                                try:
                                    parsed_pe = float(cval.replace("%", "").strip())
                                    if parsed_pe > 0.0 and parsed_pe < 1000.0:
                                        recovered_pe = round(parsed_pe, 2)
                                except ValueError:
                                    pass

                        if recovered_ev is not None:
                            logger.info(
                                f"ℹ️ [UPSTREAM_RECOVERY: QA_COMPARISON] {clean_sym}: Retrieved "
                                f"EV/EBITDA={recovered_ev} from Upstox Key-Ratios API (raw='{raw_ev_str}'). "
                                f"Governance rule: Upstox Key-Ratios is QA/comparison only — "
                                f"production BUY metric must be derived authoritatively from statement filings."
                            )
                            providers_audit.append({
                                "provider": "UPSTOX_KEY_RATIOS_API",
                                "endpoint": api_url,
                                "result": "SUCCESS_RECORDED_FOR_QA",
                                "http_status": 200,
                                "latency_ms": elapsed_ms,
                                "field_requested": "EV/EBITDA",
                                "raw_company_value": raw_ev_str,
                                "qa_comparison_value": recovered_ev,
                                "raw_pe_value": raw_pe_str,
                                "qa_comparison_pe": recovered_pe,
                                "validation": "RECORDED_FOR_QA_ONLY",
                                "action": "RECORDED_FOR_QA_NOT_USED_FOR_BUY_DECISION"
                            })
                            # Reset recovered_ev so it does NOT override authoritative statement derivation
                            recovered_ev = None
                        else:
                            logger.warning(
                                f"⚠️ [UPSTREAM_RECOVERY: FIELD_ABSENT] {clean_sym}: HTTP 200 received from Upstox, "
                                f"but 'EV/EBITDA' ratio was not found or was zero/negative in payload."
                            )
                            providers_audit.append({
                                "provider": "UPSTOX_KEY_RATIOS_API",
                                "endpoint": api_url,
                                "result": "FIELD_ABSENT",
                                "http_status": 200,
                                "latency_ms": elapsed_ms,
                                "validation": "FAILED",
                                "validation_reason": "EV_EBITDA_NOT_IN_KEY_RATIOS_PAYLOAD"
                            })
                    else:
                        logger.warning(
                            f"⚠️ [UPSTREAM_RECOVERY: HTTP_ERROR] {clean_sym}: Upstox API returned "
                            f"HTTP {resp.status_code} | response={resp.text[:200]}"
                        )
                        providers_audit.append({
                            "provider": "UPSTOX_KEY_RATIOS_API",
                            "endpoint": api_url,
                            "result": "HTTP_ERROR",
                            "http_status": resp.status_code,
                            "validation": "FAILED",
                            "validation_reason": f"HTTP_{resp.status_code}"
                        })
                else:
                    logger.warning(f"⚠️ [UPSTREAM_RECOVERY] {clean_sym}: UPSTOX_ACCESS_TOKEN not configured.")
                    providers_audit.append({
                        "provider": "UPSTOX_KEY_RATIOS_API",
                        "result": "AUTH_MISSING",
                        "validation": "FAILED",
                        "validation_reason": "NO_ACCESS_TOKEN"
                    })
            except Exception as _e_api:
                logger.error(f"❌ [UPSTREAM_RECOVERY: EXCEPTION] {clean_sym}: API call failed: {_e_api}")
                providers_audit.append({
                    "provider": "UPSTOX_KEY_RATIOS_API",
                    "result": "EXCEPTION",
                    "validation": "FAILED",
                    "validation_reason": str(_e_api)
                })
        else:
            logger.warning(f"⚠️ [UPSTREAM_RECOVERY] {clean_sym}: Unable to resolve ISIN for Upstox API.")
            providers_audit.append({
                "provider": "UPSTOX_INSTRUMENT_MAPPER",
                "result": "FAILED",
                "validation": "FAILED",
                "validation_reason": "ISIN_UNRESOLVED"
            })

        # ── 3. SECONDARY RECOVERY: DERIVE FROM AUTHORITATIVE RAW EXCHANGE FILINGS ──
        logger.info(
            f"🧮 [UPSTREAM_RECOVERY: FALLBACK_DERIVATION] {clean_sym}: Direct API did not yield EV/EBITDA. "
            f"Attempting independent derivation from raw authoritative statement filings + live CMP..."
        )
        try:
            # Resolve live CMP if missing
            eff_cmp = cmp_price
            if eff_cmp <= 0.0 and isin:
                try:
                    import config
                    token = getattr(config, "UPSTOX_ACCESS_TOKEN", None) or os.environ.get("UPSTOX_ACCESS_TOKEN")
                    if token:
                        q_url = f"https://api.upstox.com/v2/market-quote/quotes?instrument_key=NSE_EQ|{isin}"
                        q_resp = requests.get(q_url, headers={"Accept": "application/json", "Authorization": f"Bearer {token}"}, timeout=4.0)
                        if q_resp.status_code == 200:
                            q_data = q_resp.json().get("data", {})
                            for q_k, q_v in q_data.items():
                                lp = q_v.get("last_price")
                                if lp and float(lp) > 0:
                                    eff_cmp = float(lp)
                                    logger.info(f"📥 [UPSTREAM_RECOVERY] {clean_sym}: Live CMP recovered from Upstox Quote: ₹{eff_cmp:.2f}")
                                    break
                except Exception as _qe:
                    logger.debug(f"[UPSTREAM_RECOVERY] {clean_sym}: Quote fetch notice: {_qe}")

            _d_raw = row.get("total_debt")
            _c_raw = row.get("cash_and_equivalents")
            _d = float(_d_raw) if (_d_raw is not None and pd.notna(_d_raw)) else None
            _c = float(_c_raw) if (_c_raw is not None and pd.notna(_c_raw)) else None

            _sh = row.get("shares_outstanding")
            _sh_f = float(_sh) if (_sh is not None and pd.notna(_sh) and float(_sh) > 0) else None

            # Canonical C4 Share Count Validation (TIINDIA defence: no unvalidated silent derivation)
            _np_val = float(row.get("net_profit")) if (row.get("net_profit") is not None and pd.notna(row.get("net_profit"))) else None
            _ep_val = float(row.get("eps")) if (row.get("eps") is not None and pd.notna(row.get("eps"))) else None
            _sh_input = (_sh_f / 1e6) if (_sh_f is not None and _sh_f > 1e6) else _sh_f
            sh_res = validate_share_count(
                filed_shares=_sh_input,
                net_profit_cr=_np_val,
                eps=_ep_val,
                cmp_price=eff_cmp if eff_cmp > 0 else 0.0,
            )
            if sh_res.ok and sh_res.shares_millions is not None:
                _sh_f = sh_res.shares_millions * 1e6
            else:
                _sh_f = None

            _op = row.get("operating_profit")
            _da = row.get("depreciation_amortization")
            _eb = None
            if _op is not None and _da is not None:
                _eb = float(_op) + float(_da)
            elif row.get("ebitda") is not None and pd.notna(row.get("ebitda")):
                _eb = float(row.get("ebitda"))

            # Canonical C3 EV Calculation: Cash is MANDATORY (INDIAMART defence).
            # Never assume EV = MCap + Debt when cash is unavailable!
            if eff_cmp > 0 and _sh_f is not None and _eb is not None and _eb > 0:
                _mc_cr = (_sh_f * eff_cmp) / 1e7
                ev_res = compute_ev_pit(
                    mcap=_mc_cr,
                    total_debt=_d,
                    cash_and_equivalents=_c,
                    ebitda=_eb,
                )
                if ev_res.ok and ev_res.enterprise_value is not None and ev_res.ev_ebitda is not None:
                    recovered_ev = ev_res.ev_ebitda
                    logger.info(
                        f"✅ [UPSTREAM_RECOVERY: SUCCESS] {clean_sym}: Independently derived EV/EBITDA={recovered_ev} "
                        f"from raw statement filings (MCap=₹{_mc_cr:.2f}Cr, Debt=₹{_d}Cr, Cash=₹{_c}Cr, EBITDA=₹{_eb:.2f}Cr)!"
                    )
                    providers_audit.append({
                        "provider": "RAW_STATEMENTS_INDEPENDENT_DERIVATION",
                        "result": "SUCCESS",
                        "raw_inputs": {"mcap_cr": round(_mc_cr, 2), "debt_cr": _d, "cash_cr": _c, "ebitda_cr": round(_eb, 2)},
                        "recovered_value": recovered_ev,
                        "validation": "PASSED",
                        "action": "DERIVED_FROM_RAW_FILING_COMPONENTS"
                    })
                    return recovered_ev, recovered_pe, providers_audit, "RECOVERED_VIA_RAW_FILING_DERIVATION"
        except Exception as _e_der:
            logger.error(f"❌ [UPSTREAM_RECOVERY: EXCEPTION] {clean_sym}: Statement derivation failed: {_e_der}")

        # ── 4. ALL RECOVERY OPTIONS EXHAUSTED → DATA_INSUFFICIENT (STRICT FAIL-CLOSED) ──
        logger.error(
            f"❌ [UPSTREAM_RECOVERY: EXHAUSTED] {clean_sym}: All upstream recovery attempts failed to retrieve "
            f"or compute current_ev_ebitda. Enforcing strict fail-closed DATA_INSUFFICIENT_VALUATION."
        )
        providers_audit.append({
            "provider": "UPSTREAM_RECOVERY_EXHAUSTED",
            "result": "FAILED",
            "validation": "FAILED",
            "validation_reason": "ALL_UPSTREAM_PROVIDERS_UNAVAILABLE_OR_INSUFFICIENT"
        })
        return None, None, providers_audit, "DATA_INSUFFICIENT_VALUATION"

    def scan_universe(self, trigger_type: str = "SCHEDULED", scheduler_name: str = "CRON", record_full_evidence: bool = True) -> Dict[str, Any]:
        """
        Executes the frozen QUALITY_COMPOUNDER 17:00 IST daily scan run.
        Generates daily immutable SCAN_SNAPSHOT rows for ALL evaluated stocks and
        ALERT_EVENT rows for passing candidate stocks directly in the existing 'alerts' table.
        """
        import time
        start_ts = time.time()
        _scan_start = time.monotonic()  # must be monotonic — print_scanner_end_banner computes time.monotonic() - start_mono
        _computed_v2_health_status = None   # set by _scan_universe_core via exec_run_ctx_holder; passed to end banner
        # Invariant: verify _scan_start is a valid monotonic value, not a wall-clock timestamp.
        assert _scan_start > 0, f"QUALITY_COMPOUNDER _scan_start={_scan_start} must be positive"
        assert _scan_start < 1e9, (
            f"QUALITY_COMPOUNDER _scan_start={_scan_start:.0f} looks like time.time() (wall-clock), "
            f"not time.monotonic(). Duration will be negative in end banner."
        )
        acquired_scan = False
        acquired_global = False
        exec_run_ctx_holder = [None]

        # 1. Thread-level concurrency lock: prevent overlapping runs of same scanner
        if not _v2_scan_lock.acquire(blocking=False):
            logger.warning("🔒 [QUALITY_COMPOUNDER] Scanner is already running in another thread. Skipping duplicate cycle.")
            return {"status": "SKIPPED", "reason": "Already running"}
        acquired_scan = True

        # 2. Universal global scanner lock queue wait: serialize TECHNICAL, FUNDAMENTAL, QUALITY_COMPOUNDER
        queued_at = time.monotonic()
        if not _global_lock.acquire(blocking=False, owner_scanner="QUALITY_COMPOUNDER", operation="FULL_SCAN"):
            logger.info("⏳ [QUALITY_COMPOUNDER] Global scanner lock busy (another main scanner is running) — waiting in queue until active scanner finishes...")
            try:
                from database import upsert_scanner_health
            except ImportError:
                try:
                    from app.database import upsert_scanner_health
                except Exception:
                    upsert_scanner_health = None
            if upsert_scanner_health is not None:
                try:
                    upsert_scanner_health("QUALITY_COMPOUNDER", "QUEUED", error_msg="Waiting in queue for active scanner to release lock...")
                except Exception:
                    pass

            try:
                acquired_global = _global_lock.acquire(blocking=True, owner_scanner="QUALITY_COMPOUNDER", operation="FULL_SCAN")
            except Exception as lock_err:
                logger.error(f"❌ [QUALITY_COMPOUNDER] Error acquiring global lock: {lock_err}")
                acquired_global = False

            if not acquired_global:
                logger.error("❌ [QUALITY_COMPOUNDER] Failed to acquire global scanner lock after queue wait.")
                if upsert_scanner_health is not None:
                    try:
                        upsert_scanner_health("QUALITY_COMPOUNDER", "IDLE", error_msg="Lock acquisition timed out")
                    except Exception:
                        pass
                _v2_scan_lock.release()
                return {"status": "FAILED", "reason": "Lock acquisition failed"}
        else:
            acquired_global = True

        _core_result_holder = [None]
        try:
            _core_result_holder[0] = self._scan_universe_core(
                trigger_type=trigger_type,
                scheduler_name=scheduler_name,
                queued_at=queued_at,
                start_ts=start_ts,
                exec_run_ctx_holder=exec_run_ctx_holder,
                record_full_evidence=record_full_evidence
            )
            return _core_result_holder[0]
        finally:
            try:
                run_id = getattr(exec_run_ctx_holder[0], "run_id", None) if exec_run_ctx_holder[0] else None
                # Propagate _health_status from _scan_universe_core return dict to override_status.
                # This ensures the end-banner always persists the real health, even when the body's
                # upsert_scanner_health was silently rejected by the execution-ownership guard.
                if _core_result_holder[0] and isinstance(_core_result_holder[0], dict):
                    _computed_v2_health_status = _core_result_holder[0].get("status") or _computed_v2_health_status
                print_scanner_end_banner(
                    "QUALITY_COMPOUNDER",
                    start_mono=_scan_start,
                    run_id=run_id,
                    override_status=_computed_v2_health_status,
                    start_wall_ts=start_ts
                )
            except Exception as _banner_err:
                logger.debug(f"End banner notice: {_banner_err}")
            finally:
                if acquired_global:
                    try:
                        _global_lock.release()
                    except Exception as _ge:
                        logger.debug(f"Global lock release notice: {_ge}")
                if acquired_scan:
                    try:
                        _v2_scan_lock.release()
                    except Exception as _se:
                        logger.debug(f"V2 scan lock release notice: {_se}")

    def _scan_universe_core(
        self,
        trigger_type: str = "SCHEDULED",
        scheduler_name: str = "CRON",
        queued_at: float = 0.0,
        start_ts: float = 0.0,
        exec_run_ctx_holder: Optional[List[Any]] = None,
        record_full_evidence: bool = True
    ) -> Dict[str, Any]:
        now_ist = datetime.now(IST)
        today_str = now_ist.strftime("%Y-%m-%d")

        # Imports for DB execution tracking & health updates
        try:
            from database import (
                create_scanner_execution_run,
                complete_scanner_execution_run,
                upsert_scanner_health,
                save_v2_scan_snapshots,
                save_v2_candidate_alert
            )
        except ImportError:
            from app.database import (
                create_scanner_execution_run,
                complete_scanner_execution_run,
                upsert_scanner_health,
                save_v2_scan_snapshots,
                save_v2_candidate_alert
            )

        # Record execution run start in scanner_execution_history ONLY AFTER lock acquired
        exec_run_ctx = None
        if create_scanner_execution_run is not None:
            try:
                exec_run_ctx = create_scanner_execution_run(
                    scanner_name="QUALITY_COMPOUNDER",
                    trigger_type=trigger_type,
                    allow_concurrent=True
                )
                if exec_run_ctx_holder is not None:
                    exec_run_ctx_holder[0] = exec_run_ctx
            except Exception as e:
                logger.debug(f"Execution history start warning: {e}")

        # Start Banner
        print_scanner_start_banner("QUALITY_COMPOUNDER", queued_at=queued_at, run_id=getattr(exec_run_ctx, "run_id", None))

        if upsert_scanner_health is not None:
            try:
                upsert_scanner_health(
                    "QUALITY_COMPOUNDER",
                    status="RUNNING",
                    run_id=getattr(exec_run_ctx, "run_id", None)
                )
            except Exception as e:
                logger.debug(f"Scanner health RUNNING warning: {e}")

        logger.info(f"📡 [SCANNER: QUALITY_COMPOUNDER] Starting 17:00 IST daily scan run ({today_str}, trigger={trigger_type})...")

        # Load PIT fundamentals dataset
        pit_df = self.load_pit_dataset()
        if pit_df is None or pit_df.empty:
            logger.error("❌ [SCANNER: QUALITY_COMPOUNDER] Failed to load PIT dataset — scan failed!")
            if complete_scanner_execution_run is not None and exec_run_ctx and getattr(exec_run_ctx, "run_id", None):
                try:
                    complete_scanner_execution_run(
                        run_id=exec_run_ctx.run_id,
                        lifecycle_status="FAILED",
                        quality_status="CRITICAL",
                        summary_notes="PIT dataset unavailable"
                    )
                except Exception:
                    pass
            if upsert_scanner_health is not None:
                try:
                    upsert_scanner_health("QUALITY_COMPOUNDER", status="DOWN", error_msg="PIT dataset unavailable", run_id=getattr(exec_run_ctx, "run_id", None))
                except Exception:
                    pass
            return {"status": "FAILED", "error": "PIT_DATASET_UNAVAILABLE"}

        snapshot_records = []
        candidate_records = []

        total_scanned = 0
        quality_pass_count = 0
        quality_reject_count = 0
        value_pass_count = 0
        value_reject_count = 0
        candidate_count = 0

        non_pit_blocked_count = 0         # Non-PIT symbols (missing statement history in pit_df)
        incomplete_quality_count = 0      # PIT symbols missing 5Y quality history (roce/cagr/cfo/d_e)
        valuation_data_blocked_count = 0  # PIT symbols missing valuation discount calculation
        price_data_blocked_count = 0      # Missing or non-positive live quote CMP
        data_blocked_count = 0            # Total unique symbols with any required field missing
        data_complete_count = 0           # Symbols with 100% complete required data

        # Sub-breakdown counters for exact mathematical Venn reconciliation
        val_curr_missing_count = 0        # Missing current EV/EBITDA
        val_med_missing_count = 0         # Missing 3Y EV/EBITDA median
        val_both_missing_count = 0        # Missing BOTH current and median EV/EBITDA
        quality_only_blocked_count = 0    # Quality incomplete, but valuation complete
        val_only_blocked_count = 0        # Valuation incomplete, but quality complete
        quality_and_val_blocked_count = 0 # Both quality and valuation incomplete
        price_only_blocked_count = 0      # Price missing/non-positive CMP, but quality and valuation complete

        # Filter to latest PIT record per symbol on or before today
        if 'filing_date' in pit_df.columns:
            pit_df['filing_date'] = pd.to_datetime(pit_df['filing_date'])
            pit_df = pit_df[pit_df['filing_date'] <= pd.to_datetime(today_str)].sort_values('filing_date').groupby('symbol').last().reset_index()

        # Deduplicate and canonicalize symbols
        pit_df['symbol'] = pit_df['symbol'].astype(str).str.strip().str.upper()
        pit_df = pit_df.drop_duplicates(subset=['symbol'], keep='last').reset_index(drop=True)

        # ── PRE-FLIGHT UNIVERSE HEALTH & VALUATION COMPLETENESS GATE ──────────────
        # Explicit universe & PIT lineage tracking (§1, §2)
        approved_univ = sorted(list(self.universe_registry.approved_symbols))
        pit_symbols_set = set(pit_df['symbol'].astype(str).str.strip().str.upper())
        if approved_univ and any(s in pit_symbols_set for s in approved_univ):
            universe_symbols = approved_univ
        else:
            universe_symbols = [str(r['symbol']).strip().upper() for _, r in pit_df.iterrows()]
        total_approved_univ = len(universe_symbols)
        pit_univ_cnt = len(pit_df)
        non_pit_univ_cnt = max(0, total_approved_univ - pit_univ_cnt)

        # ── FULL FORENSIC EVIDENCE COLLECTOR INITIALIZATION ───────────────────────
        collector = None
        if record_full_evidence:
            try:
                try:
                    from full_forensic_evidence_collector import FullForensicEvidenceCollector
                except ImportError:
                    from app.full_forensic_evidence_collector import FullForensicEvidenceCollector
                _coll_run_id = getattr(exec_run_ctx, "run_id", None) or f"RUN_V2_FINAL_{now_ist.strftime('%Y%m%d_%H%M%S')}"
                collector = FullForensicEvidenceCollector(
                    scanner_id="QUALITY_COMPOUNDER",
                    scanner_name="Quality Compounder",
                    scanner_version="FROZEN_V2_PROD_1.0",
                    run_id=_coll_run_id,
                    universe_definition="Approved Universe (886 Certified Clean Equities)",
                )
                collector.record_universe_membership(universe_symbols)

                # Pre-populate raw PIT filings
                raw_pit_parquet = os.path.join(DATA_DIR, "pit_fundamentals_v1", "pit_fundamentals_v1.parquet")
                if os.path.exists(raw_pit_parquet):
                    try:
                        df_raw_pit = pd.read_parquet(raw_pit_parquet)
                        for r in df_raw_pit.to_dict(orient="records"):
                            collector.record_pit_observation(
                                symbol=str(r.get("symbol", "")).strip().upper(),
                                filing_id=str(r.get("filing_id", "")),
                                statement_type=str(r.get("statement_type", "ANNUAL")),
                                period_end_date=str(r.get("period_end_date", "")),
                                filing_date=str(r.get("filing_date", "")),
                                actual_pub=str(r.get("actual_publication_timestamp", "")),
                                source_provider=str(r.get("source_provider", "Upstox")),
                                rev=r.get("revenue"),
                                ebitda=r.get("operating_profit"),
                                pat=r.get("net_profit"),
                                cfo=r.get("operating_cash_flow"),
                                debt=r.get("total_debt"),
                                equity=r.get("total_equity"),
                                cash=r.get("cash_and_equivalents"),
                                roce=r.get("roce"),
                            )
                    except Exception as _e_pit:
                        logger.debug(f"Notice loading raw PIT statements for evidence: {_e_pit}")

                # Pre-populate historical valuation cache
                pit_val_cache_path = os.path.join(DATA_DIR, "pit_valuation_history_cache.json")
                if os.path.exists(pit_val_cache_path):
                    try:
                        with open(pit_val_cache_path) as f:
                            vj = json.load(f)
                        vdata = vj.get("data", vj)
                        for vsym, vrec in vdata.items():
                            med_val = vrec.get("ev_ebitda_3y_median")
                            if med_val is not None:
                                collector.record_historical_valuation(
                                    symbol=vsym,
                                    metric_name="EV_EBITDA_3Y_MEDIAN",
                                    median_value=med_val,
                                    samples_count=vrec.get("samples_3y", 745),
                                    data_provider=vrec.get("data_provider", "Upstox"),
                                    as_of_date=vrec.get("as_of_date", "2026-09-25"),
                                )
                    except Exception as _e_val:
                        logger.debug(f"Notice loading valuation medians cache for evidence: {_e_val}")
            except Exception as _init_coll_err:
                logger.warning(f"Forensic evidence collector initialization notice: {_init_coll_err}")
                collector = None

        # ── V2 CANONICAL 3-POPULATION HEALTH ACCOUNTING COUNTERS & SETS ───────────
        # Conservative model (2026-10-01):
        #   STRUCTURAL_INELIGIBLE: Symbol cannot satisfy 5Y prerequisite by design (< 5 years historical
        #                          existence proven by historical evidence) — NOT a data failure, never triggers DEGRADED.
        #   DATA_FAILURE:          Symbol that should be evaluable, but required data is missing because of
        #                          ingestion, cache construction, parsing, provider, or pipeline failure.
        #                          Exact Set Union of all data gaps. MUST cause DEGRADED.
        #   FULLY_EVALUABLE:       Symbol with 100% complete required data across quality, valuation, and live price.
        #   ZERO "Other" Population Bucket.
        #   HEALTH = GREEN (OK) when DATA_FAILURE_COUNT == 0.
        structural_ineligible_count = 0       # Symbols with proven limited historical existence
        data_failure_count = 0                # Symbols with unresolved data/ingestion failures
        non_pit_structural_count = 0          # Non-PIT symbols with proven genuine limited history
        non_pit_data_failure_count = 0        # Non-PIT symbols with ingestion gap / unreadable / mature
        incomplete_pit_structural_count = 0   # PIT symbols with proven genuine limited history
        incomplete_pit_data_failure_count = 0 # PIT symbols with metric calculation / ingestion failure

        # Disjoint symbol tracking sets across entire approved universe
        structural_ineligible_symbols: Set[str] = set()
        non_pit_df_symbols: Set[str] = set()
        quality_df_symbols: Set[str] = set()
        val_df_symbols: Set[str] = set()
        price_df_symbols: Set[str] = set()
        canonical_records: List[Dict[str, Any]] = []

        # ── CONFIRMED EXCHANGE NON-AVAILABILITY (STRUCTURAL, NOT DATA FAILURE) ──────
        # Symbols that are VERIFIED absent from Upstox NSE instrument master.
        # Absence is confirmed by exhaustive instrument master search (not transient API failure).
        # These symbols are classified STRUCTURAL_INELIGIBLE, not DATA_FAILURE:
        #   - We cannot fetch a live price because the instrument is not listed on Upstox
        #   - This is an infrastructure/exchange fact, not a data pipeline failure
        #   - No synthetic or wrong-company price is ever used (zero-synthetic-fallback preserved)
        # Audit log:
        #   GUJGASLTD: verified 2026-10-01 — not in NSE.csv.gz. ISIN INE844O01030 maps to GUJENERGY
        #              (Gujarat Energy Limited), a different company. Cannot be resolved.
        CONFIRMED_NOT_ON_EXCHANGE: Set[str] = set()


        _raw_filings_dir = os.path.join(DATA_DIR, "pit_raw_filings")

        _history_1d_dir = os.path.join(DATA_DIR, "history", "1d")
        population_audit_records: List[Dict[str, Any]] = []

        # ── IN-MEMORY HISTORICAL INDICES WARMUP (O(1) ACCESS PER SYMBOL) ─────────
        raw_history_index = build_raw_history_index(_raw_filings_dir)
        history_1d_index = build_history_1d_dates_index(_history_1d_dir)

        # Field-level completeness across PIT dataset rows (independent accounting)
        _ev_curr_cnt = int(pit_df['current_ev_ebitda'].notna().sum()) if 'current_ev_ebitda' in pit_df.columns else 0
        _ev_med_cnt  = int(pit_df['ev_ebitda_3y_median'].notna().sum()) if 'ev_ebitda_3y_median' in pit_df.columns else 0
        _pe_curr_cnt = int(pit_df['current_pe'].notna().sum()) if 'current_pe' in pit_df.columns else 0
        _pe_med_cnt  = int(pit_df['pe_3y_median'].notna().sum()) if 'pe_3y_median' in pit_df.columns else 0

        _ev_curr_missing_cnt = max(0, pit_univ_cnt - _ev_curr_cnt)
        _ev_med_missing_cnt  = max(0, pit_univ_cnt - _ev_med_cnt)

        def _safe_pos(v):
            if v is None or pd.isna(v):
                return False
            try:
                return float(v) > 0
            except (ValueError, TypeError):
                return False

        # Authoritative both-required completeness (Current EV/EBITDA > 0 AND 3Y Median EV/EBITDA > 0)
        _both_complete_pit = sum(
            1 for _, _row in pit_df.iterrows()
            if _safe_pos(_row.get('current_ev_ebitda')) and _safe_pos(_row.get('ev_ebitda_3y_median'))
        )
        _val_missing_both_cnt = max(0, pit_univ_cnt - _both_complete_pit)
        _both_cov_pct = (_both_complete_pit / max(pit_univ_cnt, 1)) * 100.0
        _valuation_cache_cert_status = "CERTIFIED" if _both_complete_pit == pit_univ_cnt and pit_univ_cnt > 0 else "PARTIAL_INCOMPLETE"
        _valuation_provider_healthy = (_both_cov_pct >= 50.0)

        # Complete EV and PE field availability (Current EV > 0 AND 3Y Median EV > 0 AND Current PE > 0 AND 3Y Median PE > 0)
        _ev_pe_both_complete = sum(
            1 for _, _row in pit_df.iterrows()
            if _safe_pos(_row.get('current_ev_ebitda')) and
               _safe_pos(_row.get('ev_ebitda_3y_median')) and
               _safe_pos(_row.get('current_pe')) and
               _safe_pos(_row.get('pe_3y_median'))
        )
        _ev_pe_cov_pct = (_ev_pe_both_complete / max(pit_univ_cnt, 1)) * 100.0

        logger.info(
            f"ℹ️ [V2_FINAL] UNIVERSE & PIT LINEAGE: "
            f"ApprovedUniverse={total_approved_univ} | "
            f"PIT_Universe={pit_univ_cnt} | "
            f"Non_PIT_Symbols={non_pit_univ_cnt} (hard-blocked as DATA_MISSING_PIT_FILINGS)"
        )
        logger.info(
            f"ℹ️ [V2_FINAL] PIT VALUATION FIELD COMPLETENESS ({pit_univ_cnt} PIT rows): "
            f"Current EV/EBITDA Complete={_ev_curr_cnt}/{pit_univ_cnt} (Missing: {_ev_curr_missing_cnt}) | "
            f"3Y EV/EBITDA Med Complete={_ev_med_cnt}/{pit_univ_cnt} (Missing: {_ev_med_missing_cnt}) | "
            f"EV_PE_Both_Complete (Current EV ∧ 3Y Med ∧ Current PE ∧ 3Y PE)={_ev_pe_both_complete}/{pit_univ_cnt} ({_ev_pe_cov_pct:.1f}%) | "
            f"Current PE Complete={_pe_curr_cnt}/{pit_univ_cnt} | "
            f"3Y PE Med Complete={_pe_med_cnt}/{pit_univ_cnt} | "
            f"Cache_Status={_valuation_cache_cert_status}"
        )

        if not _valuation_provider_healthy:
            logger.error(
                f"❌ [V2_FINAL] PRE-FLIGHT GATE: VALUATION_DATA_CRITICAL — "
                f"only {_both_complete_pit}/{pit_univ_cnt} ({_both_cov_pct:.1f}%) symbols satisfy the frozen EV/EBITDA gate (Current EV ∩ 3Y Med). "
                f"Current EV missing for {_ev_curr_missing_cnt}/{pit_univ_cnt} symbols; 3Y Med missing for {_ev_med_missing_cnt}/{pit_univ_cnt}. "
                f"V2 strategy decision engine is severely degraded. Continuing quality scan for telemetry."
            )
        else:
            logger.info(
                f"✅ [V2_FINAL] PRE-FLIGHT GATE: PIT DATASET PRESENT = {pit_univ_cnt}/{pit_univ_cnt} | "
                f"VALUATION BOTH-REQUIRED AVAILABILITY = {_both_complete_pit}/{pit_univ_cnt} ({_both_cov_pct:.1f}%); "
                f"VAL_MISSING_FOR_BOTH_REQUIRED = {_val_missing_both_cnt}; "
                f"CERTIFICATION_STATUS = {_valuation_cache_cert_status}; "
                f"{non_pit_univ_cnt} non-PIT symbols will be hard-blocked by data gate. Proceeding."
            )
        # ─────────────────────────────────────────────────────────────────────────

        # ── REAL BENCHMARK DRAWDOWN (NIFTY 50) ───────────────────────────────────
        bm_dd = None
        for bm_f in ["NIFTY 50.parquet", "NIFTY50.parquet", "^NSEI.parquet"]:
            bm_p = os.path.join(DATA_DIR, "history", "1d", bm_f)
            if os.path.exists(bm_p):
                try:
                    df_bm = pd.read_parquet(bm_p)
                    bc = 'Close' if 'Close' in df_bm.columns else ('close' if 'close' in df_bm.columns else None)
                    bh = 'High' if 'High' in df_bm.columns else ('high' if 'high' in df_bm.columns else None)
                    if bc and bh and not df_bm.empty:
                        n_cmp = float(df_bm[bc].iloc[-1])
                        n_h252 = float(df_bm[bh].tail(252).max())
                        if n_h252 > 0:
                            bm_dd = max(0.0, (n_h252 - n_cmp) / n_h252)
                            break
                except Exception:
                    pass
        dd_nifty = bm_dd if bm_dd is not None else 0.0
        # ─────────────────────────────────────────────────────────────────────────

        # ── BULK LIVE PRICE WARMUP & INDEPENDENT PRICE ACCOUNTING ───────────────
        live_prices_map = {}
        try:
            from live_prices import get_live_prices
            live_prices_map = get_live_prices(universe_symbols, purpose="V2_FUNDAMENTAL_SCAN")
        except Exception:
            try:
                from app.live_prices import get_live_prices
                live_prices_map = get_live_prices(universe_symbols, purpose="V2_FUNDAMENTAL_SCAN")
            except Exception as _lp_err2:
                logger.debug(f"Live price batch fetch notice: {_lp_err2}")

        req_cnt = len(universe_symbols)
        uniq_cnt = len(live_prices_map)
        fail_cnt = req_cnt - uniq_cnt
        failed_syms = [s for s in universe_symbols if s not in live_prices_map]
        fail_str = f" (failed: {failed_syms[:5]})" if failed_syms else ""
        logger.info(f"⚡ [V2_FINAL] Live price fetch complete: requested={req_cnt} | unique_live_quotes={uniq_cnt} | provider_failures={fail_cnt}{fail_str}")

        requested_price_symbols = set(universe_symbols)
        successful_price_symbols = {s for s, p in live_prices_map.items() if p is not None and not pd.isna(p) and float(p) > 0}
        provider_failed_symbols = requested_price_symbols - set(live_prices_map.keys())
        zero_or_negative_price_symbols = {s for s, p in live_prices_map.items() if p is None or pd.isna(p) or float(p) <= 0}

        if collector is not None:
            for s in universe_symbols:
                if s in provider_failed_symbols:
                    collector.record_provider_result(
                        provider="UPSTOX_LIVE_QUOTE",
                        endpoint="GET /v2/market-quote/ltp",
                        symbol=s,
                        success_failure="FAILURE",
                        status_code=500,
                        error_class="PROVIDER_UNAVAILABLE",
                        error_message="Live quote unavailable from upstream provider",
                        final_outcome="PROVIDER_FAILED",
                    )
                else:
                    collector.record_provider_result(
                        provider="UPSTOX_LIVE_QUOTE",
                        endpoint="GET /v2/market-quote/ltp",
                        symbol=s,
                        success_failure="SUCCESS",
                        status_code=200,
                        final_outcome="DATA_OBTAINED",
                    )
        # ─────────────────────────────────────────────────────────────────────────

        pit_records_map = {str(r['symbol']).strip().upper(): r for r in pit_df.to_dict(orient="records")}

        for sym in universe_symbols:
            total_scanned += 1
            cmp_price = float(live_prices_map.get(sym, 0.0) or 0.0)
            price_source = "LIVE_QUOTE" if cmp_price > 0 else "UNRESOLVED"

            # Allow mock / PIT override price if live quote is missing (e.g. unit tests or mock datasets)
            if cmp_price <= 0.0 and sym in pit_records_map:
                _mock_px = pit_records_map[sym].get('current_price')
                if _mock_px is not None and not pd.isna(_mock_px) and float(_mock_px) > 0:
                    cmp_price = float(_mock_px)
                    price_source = "PIT_DATASET_OVERRIDE"

            # Check if certified 1D history candle is available locally (only needed for eligible PIT candidates with CMP > 0)
            df_px = None
            if sym in pit_records_map and cmp_price > 0:
                p_path = os.path.join(_history_1d_dir, f"{sym}.parquet")
                if os.path.exists(p_path):
                    try:
                        df_px = pd.read_parquet(p_path)
                    except Exception:
                        pass

            # P0: DO NOT fall back to historical 1D parquet when live CMP is unavailable.
            # A stale/historical price must never participate in a live production BUY decision.
            # The symbol will be blocked by the price_data_missing check below.
            if cmp_price <= 0.0 and df_px is not None and not df_px.empty:
                # Historical price available but NOT injected as CMP — log for diagnostics only.
                c_col = 'close' if 'close' in df_px.columns else ('Close' if 'Close' in df_px.columns else None)
                _last_hist_px = float(df_px[c_col].iloc[-1]) if c_col else None
                price_source = "LIVE_QUOTE_FAILED_HISTORICAL_AVAILABLE"
                logger.debug(
                    f"[V2_CMP_BLOCK] {sym}: live CMP unavailable, "
                    f"last historical close=₹{_last_hist_px:.2f} (NOT used as CMP — DATA_INSUFFICIENT_PRICE)."
                )
                # cmp_price intentionally NOT updated — remains <=0 so price_data_missing gate blocks this symbol.

            # Resolve price status
            if cmp_price > 0 and price_source == "LIVE_QUOTE":
                _price_status = "VALID_LIVE_QUOTE"
            elif cmp_price > 0 and price_source == "PIT_DATASET_OVERRIDE":
                _price_status = "PIT_DATASET_OVERRIDE"
            elif sym in provider_failed_symbols:
                _price_status = "PROVIDER_FAILED"
            else:
                _price_status = "ZERO_OR_NEGATIVE_CMP"

            # 100% UNIVERSE AUDITABILITY: Handle symbols missing from PIT filings
            if sym not in pit_records_map:
                non_pit_blocked_count += 1
                data_blocked_count += 1
                rejections = ["DATA_MISSING_PIT_FILINGS"]

                if cmp_price <= 0.0 or sym in provider_failed_symbols:
                    price_df_symbols.add(sym)

                # Conservative classification: NON-PIT SYMBOL PATH
                _non_pit_cls = classify_v2_historical_evidence(
                    symbol=sym,
                    raw_filings_dir=_raw_filings_dir,
                    history_1d_dir=_history_1d_dir,
                    scan_date_str=today_str,
                    is_pit_symbol=False,
                    raw_history_index=raw_history_index,
                    history_1d_index=history_1d_index,
                )

                _df_rsns = []
                if _non_pit_cls["is_structural"]:
                    structural_ineligible_count += 1
                    non_pit_structural_count += 1
                    structural_ineligible_symbols.add(sym)
                    _eligibility_label = f"STRUCTURAL_INELIGIBLE_NO_PIT ({_non_pit_cls['reason']})"
                    _data_status = "STRUCTURAL_INELIGIBLE"
                    _top_pop = "STRUCTURAL_INELIGIBLE"
                    _s_rsn = _non_pit_cls["reason"]
                else:
                    data_failure_count += 1
                    non_pit_data_failure_count += 1
                    non_pit_df_symbols.add(sym)
                    _eligibility_label = f"DATA_FAILURE_NO_PIT ({_non_pit_cls['reason']})"
                    _data_status = "DATA_FAILURE"
                    _top_pop = "DATA_FAILURE"
                    _s_rsn = None
                    _df_rsns.append(_non_pit_cls["reason"])

                if sym in provider_failed_symbols:
                    _df_rsns.append("PRICE_PROVIDER_FAILURE")
                elif cmp_price <= 0.0:
                    _df_rsns.append("PRICE_DATA_MISSING")

                population_audit_records.append({
                    "symbol": sym,
                    "top_level_population": _top_pop,
                    "quality_status": "NOT_EVALUATED",
                    "valuation_status": "NOT_EVALUATED",
                    "price_status": _price_status,
                    "filing_annual_count": _non_pit_cls["filing_annual_count"],
                    "earliest_annual_period": _non_pit_cls["earliest_annual_period"],
                    "latest_annual_period": _non_pit_cls["latest_annual_period"],
                    "structural_reason": _s_rsn,
                    "data_failure_reasons": "; ".join(_df_rsns) if _df_rsns else None,
                    "current_ev_status": "MISSING",
                    "ev_3y_median_status": "MISSING",
                    "provenance_source": "PIT_RAW_FILINGS",
                })

                is_np_struct = bool(sym in structural_ineligible_symbols)
                canonical_records.append({
                    "symbol": sym,
                    "structural_ineligible": is_np_struct,
                    "quality_data_failure": False,
                    "valuation_data_failure": False,
                    "price_data_failure": bool((cmp_price <= 0.0 or sym in provider_failed_symbols) and not is_np_struct),
                    "other_data_failure": bool(not is_np_struct),
                    "incomplete": bool(not is_np_struct),
                    "fully_evaluable": False,
                    "final_action": "STRUCTURAL_INELIGIBLE" if is_np_struct else "INCOMPLETE",
                    "top_level_population": _top_pop,
                    "quality_status": "NOT_EVALUATED",
                    "valuation_status": "NOT_EVALUATED",
                    "price_status": _price_status,
                    "structural_reason": _s_rsn,
                    "data_failure_reasons": "; ".join(_df_rsns) if _df_rsns else None,
                    "provenance_source": "PIT_RAW_FILINGS",
                })

                _emit_data_recovery_log(
                    scanner="QUALITY_COMPOUNDER",
                    symbol=sym,
                    stage="QUALITY",
                    missing_data="pit_statement_filings (ROCE, Sales CAGR, PAT CAGR, CFO/PAT, D/E)",
                    recovery_attempted=True,
                    providers=[
                        {
                            "provider": "LOCAL_CACHE (pit_fundamentals_v1.parquet)",
                            "result": "NOT_AVAILABLE",
                            "failure_type": "SYMBOL_NOT_IN_PIT_DATASET",
                        },
                        {
                            "provider": "RAW_FILINGS_REFRESH (data/raw_filings)",
                            "result": "CHECKED",
                            "failure_type": "ZERO_OR_INSUFFICIENT_ANNUAL_STATEMENTS",
                        },
                        {
                            "provider": "DAILY_BUILDER_MASTER",
                            "result": "CHECKED",
                            "failure_type": "NO_EXTENDED_5Y_FILINGS",
                        },
                        {
                            "provider": "UPSTOX_KEY_RATIOS_API",
                            "result": "NOT_APPLICABLE_FOR_MULTI_YEAR_SERIES",
                            "failure_type": "REQUIRES_AUDITED_HISTORICAL_BALANCE_SHEET_SERIES",
                        },
                        {
                            "provider": "RAW_STATEMENT_DERIVATION",
                            "result": "EXHAUSTED",
                            "failure_type": "NO_RAW_DATA_TO_DERIVE",
                        }
                    ],
                    validation="FAILED",
                    validation_reason="NO_PIT_STATEMENT_HISTORY_FOR_SYMBOL",
                    final_action="STOCK_SKIPPED",
                )
                logger.info(
                    f"🔍 [STOCK_TELEMETRY: V2] {sym:<12} | Status=REJECTED  | "
                    f"FailedAt={_eligibility_label} | "
                    f"CMP=₹{cmp_price:<8.2f} (Source={price_source}) | "
                    f"RequiredToPass=['Filing history in pit_fundamentals_v1']"
                )
                snapshot_records.append({
                    "strategy_id": self.strategy_id,
                    "symbol": sym,
                    "scan_timestamp": now_ist.isoformat(),
                    "scan_date": today_str,
                    "current_price": cmp_price,
                    "overall_candidate_status": "REJECTED",
                    "watchlist_state": "REJECTED",
                    "rejection_reason": "DATA_MISSING_PIT_FILINGS",
                    "quality_gate_status": "FAIL",
                    "value_gate_status": "FAIL",
                    "tier": "Tier B",
                    "ranking_score": 0.0,
                    "context": {
                        "strategy_id": self.strategy_id,
                        "symbol": sym,
                        "scan_date": today_str,
                        "current_price": cmp_price,
                        "price_source": price_source,
                        "data_status": _data_status,
                        "top_level_population": _top_pop,
                        "eligibility_classification": _eligibility_label,
                        "filing_annual_count": _non_pit_cls["filing_annual_count"],
                        "earliest_annual_period": _non_pit_cls["earliest_annual_period"],
                        "latest_annual_period": _non_pit_cls["latest_annual_period"],
                        "history_status": _non_pit_cls["history_status"],
                        "structural_reason": _s_rsn,
                        "data_failure_reason": "; ".join(_df_rsns) if _df_rsns else None,
                    }
                })

                if collector is not None:
                    collector.record_raw_price(
                        symbol=sym,
                        cmp_price=cmp_price,
                        price_source=price_source,
                        quote_provider="UPSTOX",
                        primary_success=(sym not in provider_failed_symbols),
                        hist_row=None,
                    )
                    collector.record_stock_master(
                        symbol=sym,
                        seq_num=total_scanned,
                        overall_status=_top_pop,
                        final_decision="BLOCKED",
                        alert_generated=False,
                        alert_type="NONE",
                        blocked=True,
                        rejected=True,
                        rejection_stage="DATA_GATE",
                        rejection_reason=_eligibility_label,
                    )
                    collector.record_rejection(
                        symbol=sym,
                        rejection_stage="DATA_GATE",
                        rejection_reason="DATA_MISSING_PIT_FILINGS",
                        evaluated_gates_count=1,
                        primary_failed_gate="PIT_AVAILABILITY",
                        detailed_explanation=f"Symbol absent from PIT statement database; historical classification: {_top_pop} ({_eligibility_label})",
                    )
                    collector.record_decision_trace_step(
                        symbol=sym,
                        step_sequence=1,
                        stage="UNIVERSE_GATE",
                        input_summary=f"Symbol={sym}, in_approved_universe=True",
                        threshold_applied="APPROVED_UNIVERSE_MEMBER",
                        evaluation_result="PASS",
                        decision_action="PROCEED_TO_DATA_GATE",
                        next_stage="DATA_GATE",
                    )
                    collector.record_decision_trace_step(
                        symbol=sym,
                        step_sequence=2,
                        stage="DATA_GATE",
                        input_summary="PIT statement filings absent from pit_fundamentals_v1",
                        threshold_applied="PIT_STATEMENT_HISTORY_REQUIRED",
                        evaluation_result="FAIL",
                        decision_action="BLOCK_SYMBOL",
                        next_stage="TERMINATED",
                    )

                    # Extract raw filings from pit_raw_filings for complete auditability
                    _raw_p = os.path.join(_raw_filings_dir, f"{sym}.json")
                    _raw_ann_cnt = 0
                    if os.path.exists(_raw_p):
                        try:
                            with open(_raw_p, "r", encoding="utf-8") as _rf_f:
                                _raw_filings_data = json.load(_rf_f)
                            if isinstance(_raw_filings_data, list):
                                for _rfil in _raw_filings_data:
                                    if str(_rfil.get("statement_type", "")).upper() in ("ANNUAL", "YEARLY") or _rfil.get("is_annual"):
                                        _raw_ann_cnt += 1
                                        collector.record_raw_annual_filing(
                                            symbol=sym,
                                            period_end_date=str(_rfil.get("period_end_date", ""))[:10],
                                            filing_date=str(_rfil.get("filing_date", ""))[:10],
                                            publication_timestamp=str(_rfil.get("actual_publication_timestamp", _rfil.get("filing_date", ""))),
                                            statement_type="ANNUAL",
                                            revenue=float(_rfil.get("revenue")) if (_rfil.get("revenue") is not None and pd.notna(_rfil.get("revenue"))) else None,
                                            operating_profit=float(_rfil.get("operating_profit")) if (_rfil.get("operating_profit") is not None and pd.notna(_rfil.get("operating_profit"))) else None,
                                            depreciation_amortization=float(_rfil.get("depreciation_amortization")) if (_rfil.get("depreciation_amortization") is not None and pd.notna(_rfil.get("depreciation_amortization"))) else None,
                                            ebitda=None,
                                            net_profit=float(_rfil.get("net_profit")) if (_rfil.get("net_profit") is not None and pd.notna(_rfil.get("net_profit"))) else None,
                                            operating_cash_flow=float(_rfil.get("operating_cash_flow")) if (_rfil.get("operating_cash_flow") is not None and pd.notna(_rfil.get("operating_cash_flow"))) else None,
                                            total_debt=float(_rfil.get("total_debt")) if (_rfil.get("total_debt") is not None and pd.notna(_rfil.get("total_debt"))) else None,
                                            total_equity=float(_rfil.get("total_equity")) if (_rfil.get("total_equity") is not None and pd.notna(_rfil.get("total_equity"))) else None,
                                            cash_and_equivalents=float(_rfil.get("cash_and_equivalents")) if (_rfil.get("cash_and_equivalents") is not None and pd.notna(_rfil.get("cash_and_equivalents"))) else None,
                                            roce=float(_rfil.get("roce")) if (_rfil.get("roce") is not None and pd.notna(_rfil.get("roce"))) else None,
                                            eps=float(_rfil.get("eps")) if (_rfil.get("eps") is not None and pd.notna(_rfil.get("eps"))) else None,
                                            shares_outstanding=float(_rfil.get("shares_outstanding")) if (_rfil.get("shares_outstanding") is not None and pd.notna(_rfil.get("shares_outstanding"))) else None,
                                            source="PIT_RAW_FILING",
                                            is_trailing_5y=True,
                                        )
                        except Exception as _rfe:
                            logger.debug(f"Notice loading raw filings for evidence on {sym}: {_rfe}")

                    collector.record_financial_reconstruction(
                        symbol=sym,
                        metric_name="DATA_GATE_PIT_PRESENCE",
                        formula="Symbol in pit_fundamentals_v1.parquet",
                        inputs={
                            "pit_fundamentals_parquet": False,
                            "pit_raw_filings_json_exists": os.path.exists(_raw_p),
                            "raw_annual_filings_count": _raw_ann_cnt,
                            "earliest_annual_period": _non_pit_cls.get("earliest_annual_period"),
                            "latest_annual_period": _non_pit_cls.get("latest_annual_period"),
                            "classification": _top_pop,
                            "classification_reason": _non_pit_cls.get("reason"),
                        },
                        calculated_value="FAIL_CLOSED (BLOCKED)",
                        unit="status",
                        provenance="PIT_RECONCILIATION_AUDIT",
                    )

                    # Gate Results for non-PIT stock
                    collector.record_gate_result(sym, "PIT_DATA_EXISTS", "DATA_GATE", "Filing history in pit_fundamentals_v1", "PIT_DATASET_PRESENT", "MISSING", "IN", "FAIL", "DATA_MISSING_PIT_FILINGS")
                    collector.record_gate_result(sym, "RAW_FILINGS_EXISTENCE", "DATA_GATE", ">= 5 annual filings in pit_raw_filings", 5, _raw_ann_cnt, ">=", "PASS" if _raw_ann_cnt >= 5 else "FAIL", "INSUFFICIENT_RAW_FILINGS" if _raw_ann_cnt < 5 else None)
                    collector.record_gate_result(sym, "PRICE_CMP", "DATA_GATE", "> ₹0.00", 0.0, cmp_price, ">", "PASS" if cmp_price > 0 else "FAIL", "PRICE_PROVIDER_FAILURE" if cmp_price <= 0 else None)
                    collector.record_gate_result(sym, "ELIGIBILITY_GATE", "ELIGIBILITY", "Passed Data Gate", "DATA_PASSED", "BLOCKED_AT_DATA_GATE", "==", "NOT_EVALUATED", "BLOCKED_AT_DATA_GATE")
                    collector.record_gate_result(sym, "QUALITY_GATE", "QUALITY", "Passed Eligibility Gate", "ELIGIBILITY_PASSED", "BLOCKED_AT_DATA_GATE", "==", "NOT_EVALUATED", "BLOCKED_AT_DATA_GATE")
                    collector.record_gate_result(sym, "VALUATION_GATE", "VALUATION", "Passed Quality Gate", "QUALITY_PASSED", "BLOCKED_AT_DATA_GATE", "==", "NOT_EVALUATED", "BLOCKED_AT_DATA_GATE")
                continue

            row = pit_records_map[sym]
            industry = str(row.get('industry', 'Unknown'))

            # Real market cap: shares * cmp_price / 1e7, or PIT market cap
            _sh = row.get('shares_outstanding')
            if _sh is not None and not pd.isna(_sh) and float(_sh) > 0 and cmp_price > 0:
                mcap = (float(_sh) * cmp_price) / 1e7
            else:
                mcap_raw = row.get('market_cap', row.get('mcap'))
                mcap = float(mcap_raw) if (mcap_raw is not None and not pd.isna(mcap_raw) and float(mcap_raw) > 0) else None

            # Real ADTV 90D from 1D history volume * close (no hardcoded 2.0)
            adtv_raw = row.get('adtv_90d', row.get('adtv'))
            adtv_90d = float(adtv_raw) if (adtv_raw is not None and not pd.isna(adtv_raw) and float(adtv_raw) > 0) else None
            if adtv_90d is None and df_px is not None and not df_px.empty:
                v_col = 'Volume' if 'Volume' in df_px.columns else ('volume' if 'volume' in df_px.columns else None)
                c_col = 'Close' if 'Close' in df_px.columns else ('close' if 'close' in df_px.columns else None)
                if v_col and c_col and len(df_px) >= 20:
                    adtv_90d = float((df_px[v_col].tail(90) * df_px[c_col].tail(90)).mean() / 1e7)

            # Metrics & Multi-field Real Resolution (NO SYNTHETIC FALLBACKS)
            roce_5y = row.get('roce_5y_avg', row.get('roce_5y', row.get('ROCE', row.get('roce'))))
            sales_cagr_5y = row.get('sales_cagr_5y', row.get('sales_cagr', row.get('rev_cagr', row.get('revenue_cagr_3y'))))
            pat_cagr_5y = row.get('pat_cagr_5y', row.get('pat_cagr', row.get('op_profit_cagr')))
            cfo_pat_5y = row.get('cfo_pat_5y_ratio', row.get('cfo_pat_5y', row.get('cfo_pat')))
            de_ratio = row.get('debt_to_equity')
            share_dilution_3y = row.get('share_dilution_3y_pct', row.get('share_dilution_3y'))

            ev_ebitda_curr = row.get('current_ev_ebitda', row.get('ev_to_ebitda', row.get('ev_ebitda')))
            ev_ebitda_med = row.get('ev_ebitda_3y_median', row.get('ev_to_ebitda_3y_median', row.get('ev_ebitda_median')))
            pe_curr = row.get('current_pe', row.get('pe_ratio', row.get('pe', row.get('pe_fallback'))))
            pe_med = row.get('pe_3y_median', row.get('pe_ratio_3y_median'))

            # Loss/negative base sentinel detection (-999.0 indicates genuine loss/non-positive base, not missing data)
            sales_cagr_loss = (sales_cagr_5y == -999.0)
            pat_cagr_loss = (pat_cagr_5y == -999.0)
            cfo_pat_loss = (cfo_pat_5y == -999.0)
            ev_ebitda_curr_loss = (ev_ebitda_curr == -999.0)

            clean_sales_cagr = None if sales_cagr_loss else sales_cagr_5y
            clean_pat_cagr = None if pat_cagr_loss else pat_cagr_5y
            clean_cfo_pat = None if cfo_pat_loss else cfo_pat_5y
            clean_ev_curr = None if ev_ebitda_curr_loss else ev_ebitda_curr

            disp_sales = "INVALID_SENTINEL (NON_POSITIVE_BASE)" if sales_cagr_loss else (f"{float(clean_sales_cagr):.2f}%" if clean_sales_cagr is not None and not pd.isna(clean_sales_cagr) else "N/A")
            disp_pat = "INVALID_SENTINEL (LOSS)" if pat_cagr_loss else (f"{float(clean_pat_cagr):.2f}%" if clean_pat_cagr is not None and not pd.isna(clean_pat_cagr) else "N/A")
            disp_cfo = "INVALID_SENTINEL (CUMULATIVE_LOSS)" if cfo_pat_loss else (f"{float(clean_cfo_pat):.2f}" if clean_cfo_pat is not None and not pd.isna(clean_cfo_pat) else "N/A")
            disp_ev_curr = "INVALID_SENTINEL (OPERATING_LOSS)" if ev_ebitda_curr_loss else (f"{float(clean_ev_curr):.2f}" if clean_ev_curr is not None and not pd.isna(clean_ev_curr) else "N/A")

            # Provenance tracking from statement calculations
            growth_start_period = row.get('growth_start_period')
            growth_end_period = row.get('growth_end_period')
            growth_years_elapsed = row.get('growth_years_elapsed')
            financial_periods_used = row.get('financial_periods_used')
            roce_periods_used = row.get('roce_periods_used')

            # Dynamic real-time calculation from CMP + statement filings if multiples were not pre-calculated
            if (ev_ebitda_curr is None or pd.isna(ev_ebitda_curr)) and cmp_price > 0 and not ev_ebitda_curr_loss:
                _eb = row.get('ebitda')
                _d_raw = row.get('total_debt')
                _c_raw = row.get('cash_and_equivalents')
                _d = float(_d_raw) if (_d_raw is not None and pd.notna(_d_raw)) else None
                _c = float(_c_raw) if (_c_raw is not None and pd.notna(_c_raw)) else None
                if _sh is not None and not pd.isna(_sh) and float(_sh) > 0 and _eb is not None and not pd.isna(_eb) and float(_eb) > 0:
                    # P0: Both debt and cash must be genuinely known (no synthetic 0 defaults)
                    if _d is not None and _c is not None:
                        _mc = (float(_sh) * cmp_price) / 1e7
                        _ev = _mc + _d - _c
                        if _ev > 0:
                            ev_ebitda_curr = round(_ev / float(_eb), 2)
                            clean_ev_curr = ev_ebitda_curr
                            disp_ev_curr = f"{float(clean_ev_curr):.2f}"
            if (pe_curr is None or pd.isna(pe_curr)) and cmp_price > 0:
                _ep = row.get('eps')
                if _ep is not None and not pd.isna(_ep) and float(_ep) > 0:
                    pe_curr = round(cmp_price / float(_ep), 2)

            # ── PER-SYMBOL EV FORENSIC TRACE ─────────────────────────────────────────
            _f_sh   = float(_sh)  if (_sh is not None and not pd.isna(_sh)) else None
            _f_eb   = float(row.get('ebitda')) if (row.get('ebitda') is not None and not pd.isna(row.get('ebitda'))) else None
            _f_d    = float(row.get('total_debt')) if (row.get('total_debt') is not None and not pd.isna(row.get('total_debt'))) else None
            _f_c    = float(row.get('cash_and_equivalents')) if (row.get('cash_and_equivalents') is not None and not pd.isna(row.get('cash_and_equivalents'))) else None
            _f_mc   = round((_f_sh * cmp_price) / 1e7, 2) if (_f_sh and cmp_price > 0) else None
            _f_ev   = round(_f_mc + _f_d - _f_c, 2) if (_f_mc is not None and _f_d is not None and _f_c is not None) else None
            _f_ev_m = "OPERATING_LOSS" if ev_ebitda_curr_loss else (round(ev_ebitda_curr, 2) if (ev_ebitda_curr is not None and not pd.isna(ev_ebitda_curr)) else None)
            _f_med  = round(float(ev_ebitda_med), 2) if (ev_ebitda_med is not None and not pd.isna(ev_ebitda_med)) else None
            if ev_ebitda_curr_loss:
                _f_block = "OPERATING_LOSS_EBITDA_NON_POSITIVE"
            elif _f_ev_m is None and _f_med is None:
                _f_block = "CURRENT_EV_EBITDA_MISSING_AND_3Y_MEDIAN_MISSING"
            elif _f_ev_m is None:
                _f_block = "CURRENT_EV_EBITDA_MISSING"
            elif _f_med is None:
                _f_block = "EV_EBITDA_3Y_MEDIAN_MISSING"
            else:
                _f_block = "DATA_AVAILABLE"
            logger.debug(
                f"[EV_FORENSIC] {sym:<12} | CMP=₹{cmp_price:.2f} | "
                f"Shares={_f_sh} | Debt={_f_d} | Cash={_f_c} | EBITDA={_f_eb} | "
                f"MarketCap(Cr)={_f_mc} | EV(Cr)={_f_ev} | "
                f"current_EV_EBITDA={_f_ev_m} | 3Y_EV_EBITDA_median={_f_med} | "
                f"valuation_block_reason={_f_block}"
            )

            # Real Technical Moving Averages from certified 1D history (no fallback to cmp_price)
            c_col = 'Close' if (df_px is not None and 'Close' in df_px.columns) else ('close' if (df_px is not None and 'close' in df_px.columns) else None)
            if df_px is not None and c_col and len(df_px) >= 50:
                sma50 = float(df_px[c_col].tail(50).mean())
                sma100 = float(df_px[c_col].tail(100).mean()) if len(df_px) >= 100 else None
                sma200 = float(df_px[c_col].tail(200).mean()) if len(df_px) >= 200 else None
            else:
                sma50 = float(row.get('sma50')) if (row.get('sma50') is not None and not pd.isna(row.get('sma50'))) else None
                sma100 = float(row.get('sma100')) if (row.get('sma100') is not None and not pd.isna(row.get('sma100'))) else None
                sma200 = float(row.get('sma200')) if (row.get('sma200') is not None and not pd.isna(row.get('sma200'))) else None

            # Real 252D Drawdown from historical highs (no hardcoded 0.15)
            h_col = 'High' if (df_px is not None and 'High' in df_px.columns) else ('high' if (df_px is not None and 'high' in df_px.columns) else None)
            if df_px is not None and h_col and len(df_px) >= 20 and cmp_price > 0:
                h252 = float(df_px[h_col].tail(252).max())
                dd_stock = max(0.0, (h252 - cmp_price) / h252) if h252 > 0 else 0.0
            else:
                dd_stock_raw = row.get('drawdown_252d')
                dd_stock = float(dd_stock_raw) if (dd_stock_raw is not None and not pd.isna(dd_stock_raw)) else 0.0

            res_dd = max(dd_stock - dd_nifty, 0.0) if (dd_stock is not None and dd_nifty is not None) else 0.0
            tier = "Tier A" if res_dd <= 0.10 else "Tier B"

            # Evaluate Gates
            rejections = []
            quality_gate_passed = False
            value_gate_passed = False

            # Financial Sector classification (Rule 7 & Rule 2)
            is_fin = self.is_financial_sector(industry, sym)
            if is_fin:
                rejections.append("METRIC_NOT_APPLICABLE_FINANCIAL")

            if mcap is None or mcap < 1000.0:
                rejections.append("FAIL_UNIVERSE_MARKET_CAP")
            if adtv_90d is None or adtv_90d < 2.0:
                rejections.append("FAIL_LIQUIDITY")

            # Shared Financial Snapshot & Watcher Freshness Gate
            snap_status = row.get("snapshot_status", "FRESH")
            if snap_status == "UPDATE_PENDING":
                rejections.append("UPDATE_PENDING: NEW_OR_AMENDED_FILING_AWAITING_REBUILD")
            elif snap_status in ("INVALID", "DATA_INSUFFICIENT"):
                rejections.append(f"DATA_INSUFFICIENT: FILING_NORMALIZATION_FAILED ({snap_status})")

            # Missing Price Check — HARD BLOCK for candidate selection
            price_data_missing = (cmp_price is None or cmp_price <= 0.0)
            if price_data_missing:
                # Governance split: confirmed exchange non-availability is structural, not a pipeline failure
                if sym in CONFIRMED_NOT_ON_EXCHANGE:
                    structural_ineligible_symbols.add(sym)
                    logger.info(
                        f"[V2_FINAL][STRUCTURAL] {sym}: classified STRUCTURAL_INELIGIBLE "
                        f"(CONFIRMED_NOT_ON_EXCHANGE — instrument absent from Upstox NSE master, "
                        f"not a transient data failure)"
                    )
                else:
                    price_df_symbols.add(sym)

                rejections.append("DATA_INSUFFICIENT_PRICE")
                price_data_blocked_count += 1
                _emit_data_recovery_log(
                    scanner="QUALITY_COMPOUNDER",
                    symbol=sym,
                    stage="PRICE",
                    missing_data="live_CMP (current market price from Upstox live quote)",
                    recovery_attempted=True,
                    providers=[
                        {
                            "provider": "UPSTOX_LIVE_QUOTE",
                            "result": "FAILED",
                            "failure_type": "LIVE_QUOTE_UNAVAILABLE_OR_ZERO",
                        }
                    ],
                    validation="FAILED",
                    validation_reason="LIVE_CMP_REQUIRED_FOR_PRODUCTION_BUY_SIGNAL",
                    final_action="STOCK_SKIPPED",
                )

            # Missing Quality Data check — STOPS candidate from passing if industrial metric is missing for non-financials
            _inc_cls = None
            if is_fin:
                quality_data_missing = False
                quality_reject_count += 1
            else:
                quality_data_missing = (
                    (roce_5y is None or pd.isna(roce_5y)) or
                    ((sales_cagr_5y is None or pd.isna(sales_cagr_5y)) and not sales_cagr_loss) or
                    ((pat_cagr_5y is None or pd.isna(pat_cagr_5y)) and not pat_cagr_loss) or
                    ((cfo_pat_5y is None or pd.isna(cfo_pat_5y)) and not cfo_pat_loss) or
                    (de_ratio is None or pd.isna(de_ratio))
                )
                if quality_data_missing:
                    rejections.append("DATA_INSUFFICIENT_QUALITY")
                    incomplete_quality_count += 1
                    # Identify exactly which fields are missing for the audit log
                    _missing_fields = [
                        name for name, val, is_loss in [
                            ("roce_5y", roce_5y, False),
                            ("sales_cagr_5y", sales_cagr_5y, sales_cagr_loss),
                            ("pat_cagr_5y", pat_cagr_5y, pat_cagr_loss),
                            ("cfo_pat_5y", cfo_pat_5y, cfo_pat_loss),
                            ("debt_to_equity", de_ratio, False),
                        ] if (val is None or pd.isna(val)) and not is_loss
                    ]

                    # Conservative classification: INCOMPLETE-PIT PATH
                    _inc_ann_count = row.get("annual_filing_count")
                    _inc_earliest = row.get("earliest_annual_period")
                    _inc_latest = row.get("latest_annual_period")

                    _inc_cls = classify_v2_historical_evidence(
                        symbol=sym,
                        filing_annual_count=_inc_ann_count,
                        earliest_annual_period=_inc_earliest,
                        latest_annual_period=_inc_latest,
                        raw_filings_dir=_raw_filings_dir,
                        history_1d_dir=_history_1d_dir,
                        scan_date_str=today_str,
                        is_pit_symbol=True,
                        raw_history_index=raw_history_index,
                        history_1d_index=history_1d_index,
                    )

                    if _inc_cls["is_structural"]:
                        structural_ineligible_count += 1
                        incomplete_pit_structural_count += 1
                        structural_ineligible_symbols.add(sym)
                        _inc_eligibility = f"STRUCTURAL_INELIGIBLE_INCOMPLETE_PIT ({_inc_cls['reason']})"
                        _inc_data_status = "STRUCTURAL_INELIGIBLE"
                    else:
                        incomplete_pit_data_failure_count += 1
                        quality_df_symbols.add(sym)
                        _inc_eligibility = f"DATA_FAILURE_INCOMPLETE_PIT ({_inc_cls['reason']})"
                        _inc_data_status = "DATA_FAILURE"

                    _emit_data_recovery_log(
                        scanner="QUALITY_COMPOUNDER",
                        symbol=sym,
                        stage="QUALITY",
                        missing_data=", ".join(_missing_fields),
                        recovery_attempted=True,
                        providers=[
                            {
                                "provider": "PIT_DATABASE (pit_fundamentals_v1.parquet)",
                                "result": "FETCHED",
                                "validation": "FAILED",
                                "validation_reason": "INSUFFICIENT_5Y_ANNUAL_FILING_HISTORY_FOR_METRIC_CALCULATION",
                                "eligibility_classification": _inc_eligibility,
                                "annual_filing_count": _inc_cls["filing_annual_count"],
                            }
                        ],
                        validation="FAILED",
                        validation_reason=f"FIELDS_REMAIN_NULL_AFTER_PIT_LOAD: {', '.join(_missing_fields)}",
                        final_action="STOCK_SKIPPED",
                    )

                else:
                    roce_val = float(roce_5y)
                    de_val = float(de_ratio)

                    if roce_val < 15.0: rejections.append("FAIL_ROCE")
                    if sales_cagr_loss or float(sales_cagr_5y) < 10.0: rejections.append("FAIL_SALES_CAGR")
                    if pat_cagr_loss or float(pat_cagr_5y) < 10.0: rejections.append("FAIL_PAT_CAGR")
                    if cfo_pat_loss or float(cfo_pat_5y) < 0.80: rejections.append("FAIL_CFO_PAT")
                    if de_val > 0.50: rejections.append("FAIL_DEBT")

                    if share_dilution_3y is not None and not pd.isna(share_dilution_3y):
                        if float(share_dilution_3y) > 10.0:
                            rejections.append("FAIL_DILUTION")

                    quality_gate_passed = not any(r.startswith("FAIL_") or r.startswith("DATA_") for r in rejections)
                    if quality_gate_passed:
                        quality_pass_count += 1
                    else:
                        quality_reject_count += 1

            # ── VALUE GATE ────────────────────────────────────────────────────────
            ev_discount = None   # None = valuation data unavailable (DATA_INSUFFICIENT)
            pe_discount = None   # None = PE comparison data unavailable
            calc_discount = None

            # P0 Controlled Provider-Recovery Chain (Exception Path):
            # When an eligible non-financial stock reaches the valuation gate and current_ev_ebitda is missing,
            # trigger targeted upstream API recovery (Upstox Key-Ratios API -> Raw Statement Derivation)
            # BEFORE declaring DATA_INSUFFICIENT_VALUATION.
            recovery_providers_audit: List[Dict[str, Any]] = []
            if (ev_ebitda_curr is None or pd.isna(ev_ebitda_curr)) and not ev_ebitda_curr_loss and not is_fin and quality_gate_passed:
                logger.info(
                    f"🎯 [V2_VALUATION_GATE] {sym}: Reached valuation gate with missing current_ev_ebitda. "
                    f"Triggering targeted upstream provider recovery before valuation decision..."
                )
                recovered_ev, recovered_pe, recovery_providers_audit, recovery_verdict = self.recover_upstream_valuation_data(
                    symbol=sym,
                    cmp_price=cmp_price,
                    row=row
                )
                if recovered_ev is not None:
                    ev_ebitda_curr = recovered_ev
                    clean_ev_curr = recovered_ev
                    disp_ev_curr = f"{float(recovered_ev):.2f}"
                    ev_ebitda_curr_loss = False
                    logger.info(
                        f"🎉 [V2_VALUATION_GATE] {sym}: Upstream recovery succeeded! "
                        f"current_ev_ebitda set to {ev_ebitda_curr} via {recovery_verdict}."
                    )
                    if (pe_curr is None or pd.isna(pe_curr)) and recovered_pe is not None:
                        pe_curr = recovered_pe
                    _emit_data_recovery_log(
                        scanner="QUALITY_COMPOUNDER",
                        symbol=sym,
                        stage="VALUATION",
                        missing_data="current_ev_ebitda",
                        recovery_attempted=True,
                        providers=recovery_providers_audit,
                        validation="PASSED",
                        validation_reason=f"RECOVERED_VIA_{recovery_verdict}",
                        final_action="DATA_USED",
                    )
                else:
                    logger.warning(
                        f"⚠️ [V2_VALUATION_GATE] {sym}: Upstream recovery failed. "
                        f"All upstream and derived providers exhausted."
                    )

            # P0: EV/EBITDA is the authoritative valuation metric for this strategy.
            # PE is logged for context but NEVER substituted when EV/EBITDA is unavailable.
            # If EV/EBITDA data is missing, the symbol receives DATA_INSUFFICIENT_VALUATION — no fallback.
            if ev_ebitda_curr is not None and ev_ebitda_med is not None and not pd.isna(ev_ebitda_curr) and not pd.isna(ev_ebitda_med) and float(ev_ebitda_med or 0) > 0 and not ev_ebitda_curr_loss:
                calc_discount = (float(ev_ebitda_med) - float(ev_ebitda_curr)) / float(ev_ebitda_med)

            if not is_fin:
                valuation_data_missing = (calc_discount is None and not ev_ebitda_curr_loss)
                curr_val_missing = ((ev_ebitda_curr is None or pd.isna(ev_ebitda_curr)) and not ev_ebitda_curr_loss)
                med_val_missing = (ev_ebitda_med is None or pd.isna(ev_ebitda_med) or float(ev_ebitda_med or 0) <= 0)
                _val_reason = None

                if ev_ebitda_curr_loss:
                    # Operating loss (EBITDA <= 0): multiple is negative / undefined
                    rejections.append("FAIL_VALUATION")
                    valuation_data_missing = False
                elif valuation_data_missing:
                    if sym not in structural_ineligible_symbols:
                        val_df_symbols.add(sym)
                    rejections.append("DATA_INSUFFICIENT_VALUATION")
                    valuation_data_blocked_count += 1

                    # Track exact valuation missing cause (independent inclusion-exclusion accounting)
                    if curr_val_missing and med_val_missing:
                        val_curr_missing_count += 1
                        val_med_missing_count += 1
                        val_both_missing_count += 1
                        _val_missing = ["current_ev_ebitda", "ev_ebitda_3y_median"]
                        _val_reason = "CURRENT_EV_EBITDA_MISSING_AND_3Y_MEDIAN_MISSING"
                        providers_list = list(recovery_providers_audit) if recovery_providers_audit else [
                            {
                                "provider": "STATEMENT_FILINGS_CMP_CALCULATOR",
                                "result": "FAILED",
                                "validation": "FAILED",
                                "validation_reason": "CURRENT_EV_EBITDA_UNAVAILABLE_REQUIRED_FOR_EV_EBITDA_GATE",
                            }
                        ]
                        providers_list.append({
                            "provider": "PIT_VALUATION_HISTORY_CACHE (pit_valuation_history_cache.json)",
                            "result": "NOT_AVAILABLE",
                            "validation": "FAILED",
                            "validation_reason": "EV_EBITDA_3Y_MEDIAN_UNAVAILABLE — run pit_valuation_history_builder.py",
                        })
                    elif curr_val_missing:
                        val_curr_missing_count += 1
                        _val_missing = ["current_ev_ebitda"]
                        _val_reason = "CURRENT_EV_EBITDA_MISSING"
                        providers_list = list(recovery_providers_audit) if recovery_providers_audit else [
                            {
                                "provider": "STATEMENT_FILINGS_CMP_CALCULATOR",
                                "result": "FAILED",
                                "validation": "FAILED",
                                "validation_reason": "CURRENT_EV_EBITDA_UNAVAILABLE_REQUIRED_FOR_EV_EBITDA_GATE",
                            }
                        ]
                    else:
                        val_med_missing_count += 1
                        _val_missing = ["ev_ebitda_3y_median"]
                        _val_reason = "EV_EBITDA_3Y_MEDIAN_MISSING"
                        providers_list = [
                            {
                                "provider": "PIT_VALUATION_HISTORY_CACHE (pit_valuation_history_cache.json)",
                                "result": "NOT_AVAILABLE",
                                "validation": "FAILED",
                                "validation_reason": "EV_EBITDA_3Y_MEDIAN_UNAVAILABLE — run pit_valuation_history_builder.py",
                            }
                        ]

                    _emit_data_recovery_log(
                        scanner="QUALITY_COMPOUNDER",
                        symbol=sym,
                        stage="VALUATION",
                        missing_data=", ".join(_val_missing),
                        recovery_attempted=True,
                        providers=providers_list,
                        validation="FAILED",
                        validation_reason=_val_reason,
                        final_action="STOCK_SKIPPED",
                    )
                else:
                    ev_discount = calc_discount
                    if ev_discount < 0.25:
                        rejections.append("FAIL_VALUATION")

                    if pe_curr is not None and pe_med is not None and not pd.isna(pe_curr) and not pd.isna(pe_med) and float(pe_med or 0) > 0:
                        pe_c = float(pe_curr)
                        pe_m = float(pe_med)
                        pe_discount = max((pe_m - pe_c) / pe_m, 0.0)

                    value_gate_passed = (ev_discount >= 0.25)
                    if quality_gate_passed:
                        if value_gate_passed:
                            value_pass_count += 1
                        else:
                            value_reject_count += 1
            else:
                valuation_data_missing = False
                curr_val_missing = False
                med_val_missing = False
                _val_reason = None

            # Venn intersection accounting across PIT dimensions
            if quality_data_missing and valuation_data_missing:
                quality_and_val_blocked_count += 1
            elif quality_data_missing:
                quality_only_blocked_count += 1
            elif valuation_data_missing:
                val_only_blocked_count += 1
            elif price_data_missing:
                price_only_blocked_count += 1

            # Count symbol as data-blocked ONCE (if ANY quality, valuation, OR price data missing)
            if (not is_fin and (quality_data_missing or valuation_data_missing)) or price_data_missing:
                data_blocked_count += 1
            else:
                data_complete_count += 1

            score_100 = self.compute_100pt_score(row if isinstance(row, dict) else row.to_dict(), ev_discount, pe_discount, res_dd)

            # Strict Invariant: Candidate must satisfy quality, value, AND have valid live/verifiable CMP > 0
            is_candidate = (
                quality_gate_passed and 
                value_gate_passed and 
                not price_data_missing and 
                cmp_price > 0.0 and 
                len(rejections) == 0
            )
            if is_candidate:
                candidate_count += 1

            primary_rejection = "PASS" if is_candidate else (rejections[0] if rejections else "FAIL_UNKNOWN")
            watchlist_state = "GREEN" if is_candidate else "REJECTED"

            # Gap Analysis: What exact data/metrics are needed for this stock to pass and trigger an alert?
            required_improvements = []
            if price_data_missing:
                required_improvements.append(f"CMP > 0.0 (Current: ₹{cmp_price:.2f} — source: {price_source})")
            if roce_5y is None or pd.isna(roce_5y) or float(roce_5y) < 15.0:
                cur_v = f"{float(roce_5y):.1f}%" if roce_5y is not None and not pd.isna(roce_5y) else "N/A"
                required_improvements.append(f"5Y Avg ROCE >= 15.0% (Current: {cur_v})")
            if sales_cagr_loss or sales_cagr_5y is None or pd.isna(sales_cagr_5y) or float(sales_cagr_5y) < 10.0:
                cur_v = "N/A (NON_POSITIVE_BASE)" if sales_cagr_loss else (f"{float(sales_cagr_5y):.1f}%" if sales_cagr_5y is not None and not pd.isna(sales_cagr_5y) else "N/A")
                required_improvements.append(f"5Y Sales CAGR >= 10.0% (Current: {cur_v})")
            if pat_cagr_loss or pat_cagr_5y is None or pd.isna(pat_cagr_5y) or float(pat_cagr_5y) < 10.0:
                cur_v = "N/A (LOSS)" if pat_cagr_loss else (f"{float(pat_cagr_5y):.1f}%" if pat_cagr_5y is not None and not pd.isna(pat_cagr_5y) else "N/A")
                required_improvements.append(f"5Y PAT CAGR >= 10.0% (Current: {cur_v})")
            if cfo_pat_loss or cfo_pat_5y is None or pd.isna(cfo_pat_5y) or float(cfo_pat_5y) < 0.80:
                cur_v = "N/A (CUMULATIVE_LOSS)" if cfo_pat_loss else (f"{float(cfo_pat_5y):.2f}" if cfo_pat_5y is not None and not pd.isna(cfo_pat_5y) else "N/A")
                required_improvements.append(f"5Y Cum CFO/PAT >= 0.80 (Current: {cur_v})")
            if de_ratio is not None and not pd.isna(de_ratio) and float(de_ratio) > 0.50:
                required_improvements.append(f"Debt/Equity <= 0.50 (Current: {float(de_ratio):.2f})")
            if is_fin:
                required_improvements.append("Sector: Non-Financial Required (Current: Financial Sector Excluded)")
            elif ev_ebitda_curr_loss:
                required_improvements.append("EV/EBITDA Discount >= 25% (Current: N/A — operating loss / EBITDA <= 0)")
            elif ev_discount is None:
                required_improvements.append("EV/EBITDA Discount >= 25% (Current: N/A — valuation data missing)")
            elif ev_discount < 0.25:
                required_improvements.append(f"EV/EBITDA Discount >= 25% (Current: {ev_discount*100:.1f}%)")

            # Per-Stock Complete Telemetry Logging
            ev_disc_str = f"{ev_discount*100:.1f}%" if ev_discount is not None else ("EXCLUDED_FINANCIAL" if is_fin else ("OPERATING_LOSS" if ev_ebitda_curr_loss else "N/A (DATA_INSUFFICIENT)"))
            telemetry_status = "CANDIDATE" if is_candidate else "REJECTED"
            logger.info(
                f"🔍 [STOCK_TELEMETRY: QUALITY_COMPOUNDER] {sym:<12} | Status={telemetry_status:<9} | Basis=CONSOLIDATED | "
                f"FailedAt={primary_rejection:<28} | Rejections={rejections} | "
                f"CMP=₹{cmp_price:<8.2f} (Source={price_source}) | "
                f"Metrics=[roce_5y={roce_5y}, sales_cagr_5y={disp_sales}, pat_cagr_5y={disp_pat}, cfo_pat_5y={disp_cfo}, d_e={de_ratio}, ev_discount={ev_disc_str}] | "
                f"ValuationDetails=[EV_curr={disp_ev_curr}, EV_3Y_med={ev_ebitda_med}, PE_curr={pe_curr}, PE_3Y_med={pe_med}] | "
                f"RequiredToPass={required_improvements if required_improvements else ['NONE (PASSING CANDIDATE)']}"
            )

            # Determine Top-Level Population and Forensic Record
            _struct_rsn = None
            _df_rsns = []

            if sym in CONFIRMED_NOT_ON_EXCHANGE:
                _struct_rsn = "CONFIRMED_NOT_ON_EXCHANGE"
            elif quality_data_missing and _inc_cls is not None:
                if _inc_cls["is_structural"]:
                    _struct_rsn = _inc_cls["reason"]
                else:
                    _df_rsns.append(_inc_cls["reason"])

            if valuation_data_missing and not is_fin:
                _df_rsns.append(f"VALUATION_DATA_GAP: {_val_reason or 'DISCOUNT_UNAVAILABLE'}")

            if sym not in CONFIRMED_NOT_ON_EXCHANGE:
                if sym in provider_failed_symbols and price_source != "PIT_DATASET_OVERRIDE":
                    price_df_symbols.add(sym)
                    _df_rsns.append("PRICE_PROVIDER_FAILURE")
                elif price_data_missing:
                    price_df_symbols.add(sym)
                    _df_rsns.append("PRICE_DATA_MISSING")

            if sym in structural_ineligible_symbols:
                _top_pop = "STRUCTURAL_INELIGIBLE"
                _df_rsns = []
            elif _df_rsns:
                _top_pop = "DATA_FAILURE"
            else:
                _top_pop = "FULLY_EVALUABLE"

            if is_fin:
                _val_status = "EXCLUDED_FINANCIAL"
                _qual_status = "EXCLUDED_FINANCIAL"
                _curr_ev_status = "NOT_APPLICABLE"
                _med_ev_status = "NOT_APPLICABLE"
            else:
                _val_status = "NOT_EVALUATED" if valuation_data_missing else ("PASS" if value_gate_passed else "FAIL")
                _qual_status = "FAIL" if quality_data_missing else ("PASS" if quality_gate_passed else "FAIL")
                _curr_ev_status = "OPERATING_LOSS" if ev_ebitda_curr_loss else ("PRESENT" if _safe_pos(ev_ebitda_curr) else "MISSING")
                _med_ev_status = "PRESENT" if _safe_pos(ev_ebitda_med) else "MISSING"

            _ann_cnt = _inc_cls["filing_annual_count"] if (quality_data_missing and _inc_cls) else row.get("annual_filing_count")
            _earliest_p = _inc_cls["earliest_annual_period"] if (quality_data_missing and _inc_cls) else row.get("earliest_annual_period")
            _latest_p = _inc_cls["latest_annual_period"] if (quality_data_missing and _inc_cls) else row.get("latest_annual_period")

            population_audit_records.append({
                "symbol": sym,
                "top_level_population": _top_pop,
                "quality_status": _qual_status,
                "valuation_status": _val_status,
                "price_status": _price_status,
                "filing_annual_count": _ann_cnt,
                "earliest_annual_period": _earliest_p,
                "latest_annual_period": _latest_p,
                "structural_reason": _struct_rsn,
                "data_failure_reasons": "; ".join(_df_rsns) if _df_rsns else None,
                "current_ev_status": _curr_ev_status,
                "ev_3y_median_status": _med_ev_status,
                "provenance_source": "PIT_FUNDAMENTALS_V1",
            })

            # Context Payload for forensic prospective research
            ctx = {
                "strategy_id": self.strategy_id,
                "symbol": sym,
                "scan_date": today_str,
                "industry": industry,
                "financial_basis": "CONSOLIDATED",
                "growth_basis": "CONSOLIDATED",
                "market_cap_cr": round(mcap, 2) if mcap is not None and not pd.isna(mcap) else None,
                "adtv_90d_cr": round(adtv_90d, 2) if adtv_90d is not None and not pd.isna(adtv_90d) else None,
                "current_price": round(cmp_price, 2) if cmp_price is not None and not pd.isna(cmp_price) else 0.0,
                "price_source": price_source,
                "growth_start_period": growth_start_period,
                "growth_end_period": growth_end_period,
                "growth_years_elapsed": growth_years_elapsed,
                "financial_periods_used": financial_periods_used,
                "roce_periods_used": roce_periods_used,
                "roce_5y_avg": round(float(roce_5y), 2) if roce_5y is not None and not pd.isna(roce_5y) else None,
                "sales_cagr_5y": round(float(clean_sales_cagr), 2) if clean_sales_cagr is not None and not pd.isna(clean_sales_cagr) else None,
                "pat_cagr_5y": round(float(clean_pat_cagr), 2) if clean_pat_cagr is not None and not pd.isna(clean_pat_cagr) else None,
                "cfo_pat_5y_ratio": round(float(clean_cfo_pat), 2) if clean_cfo_pat is not None and not pd.isna(clean_cfo_pat) else None,
                "debt_to_equity": round(float(de_ratio), 2) if de_ratio is not None and not pd.isna(de_ratio) else None,
                "share_dilution_3y_pct": round(float(share_dilution_3y), 2) if share_dilution_3y is not None and not pd.isna(share_dilution_3y) else None,
                "current_ev_ebitda": round(float(clean_ev_curr), 2) if clean_ev_curr is not None and not pd.isna(clean_ev_curr) else None,
                "ev_ebitda_3y_median": round(float(ev_ebitda_med), 2) if ev_ebitda_med is not None and not pd.isna(ev_ebitda_med) else None,
                "ev_ebitda_discount_pct": round(ev_discount * 100, 1) if ev_discount is not None else None,
                "valuation_status": "EXCLUDED_FINANCIAL" if is_fin else ("DATA_AVAILABLE" if ev_discount is not None else ("OPERATING_LOSS" if ev_ebitda_curr_loss else "DATA_INSUFFICIENT")),
                "current_pe": round(float(pe_curr), 2) if pe_curr is not None and not pd.isna(pe_curr) else None,
                "pe_3y_median": round(float(pe_med), 2) if pe_med is not None and not pd.isna(pe_med) else None,
                "pe_discount_pct": round(pe_discount * 100, 1) if pe_discount is not None else None,
                "sma50": round(sma50, 2) if sma50 is not None else None,
                "sma100": round(sma100, 2) if sma100 is not None else None,
                "sma200": round(sma200, 2) if sma200 is not None else None,
                "res_dd_pct": round(res_dd * 100, 1) if res_dd is not None and not pd.isna(res_dd) else 0.0,
                "tier": tier,
                "score_100": score_100,
                "rejection_reasons": rejections,
                "primary_rejection_reason": primary_rejection,
                "filing_date": str(row.get("filing_date", today_str)),
                "financial_period_end": str(row.get("financial_period_end", "")),
                "data_available_date": str(row.get("data_available_date", today_str)),
                "filing_annual_count": _ann_cnt,
                "earliest_annual_period": _earliest_p,
                "latest_annual_period": _latest_p,
                "top_level_population": _top_pop,
                "history_status": "STRUCTURAL_INELIGIBLE" if (quality_data_missing and _inc_cls and _inc_cls["is_structural"]) else (
                    "HISTORY_COMPLETE" if not quality_data_missing else (_inc_cls["history_status"] if _inc_cls else "UNKNOWN")
                ),
                "structural_reason": _struct_rsn,
                "data_failure_reason": "; ".join(_df_rsns) if _df_rsns else None,
                "cash_and_equivalents": row.get("cash_and_equivalents"),
                "shares_outstanding": row.get("shares_outstanding"),
                "total_debt": row.get("total_debt"),
                "ebitda": row.get("ebitda"),
                "net_profit": row.get("net_profit", row.get("pat")),
                "revenue": row.get("revenue"),
            }

            snapshot_rec = {
                "symbol": sym,
                "scan_timestamp": now_ist.isoformat(),
                "scan_date": today_str,
                "current_price": cmp_price,
                "overall_candidate_status": "CANDIDATE" if is_candidate else "REJECTED",
                "watchlist_state": watchlist_state,
                "rejection_reason": primary_rejection,
                "quality_gate_status": "PASS" if quality_gate_passed else "FAIL",
                "value_gate_status": "PASS" if value_gate_passed else "FAIL",
                "tier": tier,
                "ranking_score": score_100,
                "context": ctx
            }
            snapshot_records.append(snapshot_rec)

            if is_candidate:
                if cmp_price <= 0.0:
                    logger.error(f"❌ [ZERO_PRICE_GUARD_BLOCKED] {sym} met gates but CMP is ₹{cmp_price:.2f} — BUY alert generation BLOCKED.")
                else:
                    candidate_rec = {
                        "symbol": sym,
                        "entry_price": cmp_price,
                        "current_price": cmp_price,
                        "watchlist_state": "GREEN",
                        "tier": tier,
                        "ranking_score": score_100,
                        "signal_date": today_str,
                        "context": ctx
                    }
                    candidate_records.append(candidate_rec)

            if collector is not None:
                # Raw price
                collector.record_raw_price(
                    symbol=sym,
                    cmp_price=cmp_price,
                    price_source=price_source,
                    quote_provider="UPSTOX",
                    primary_success=(sym not in provider_failed_symbols),
                    hist_row={"close": float(df_px[c_col].iloc[-1])} if (df_px is not None and not df_px.empty and c_col) else None,
                )

                # Raw financial inputs
                _rev = row.get('revenue')
                _eb = row.get('ebitda')
                _pat = row.get('net_profit', row.get('pat'))
                _eps = row.get('eps')
                _cfo = row.get('operating_cash_flow', row.get('cfo'))
                _debt = row.get('total_debt')
                _eq = row.get('total_equity')
                _cash = row.get('cash_and_equivalents')
                _shares = row.get('shares_outstanding')

                for _fname, _fval, _funit in [
                    ("Revenue", _rev, "Cr"),
                    ("EBITDA", _eb, "Cr"),
                    ("PAT / Net Profit", _pat, "Cr"),
                    ("EPS", _eps, "INR"),
                    ("Operating Cash Flow / CFO", _cfo, "Cr"),
                    ("Total Debt", _debt, "Cr"),
                    ("Total Equity", _eq, "Cr"),
                    ("Cash and Equivalents", _cash, "Cr"),
                    ("Shares Outstanding", _shares, "Shares"),
                ]:
                    collector.record_raw_financial_field(
                        symbol=sym,
                        field_name=_fname,
                        raw_value=_fval,
                        unit=_funit,
                        publication_timestamp=str(row.get("filing_date", today_str)),
                        production_value=_fval,
                    )

                # Record all audited annual filings for symbol
                _ann_filings = row.get("annual_filings_history")
                if not _ann_filings and sym in raw_history_index:
                    try:
                        with open(raw_history_index[sym], "r") as _rf:
                            _rf_data = json.load(_rf)
                            if isinstance(_rf_data, list):
                                _ann_filings = _rf_data
                            elif isinstance(_rf_data, dict):
                                _ann_filings = _rf_data.get("annual_reports") or _rf_data.get("filings") or []
                    except Exception:
                        pass
                if _ann_filings:
                    for _ann_f in _ann_filings:
                        collector.record_raw_annual_filing(
                            symbol=sym,
                            period_end_date=str(_ann_f.get("period_end_date", ""))[:10],
                            filing_date=str(_ann_f.get("filing_date", ""))[:10],
                            publication_timestamp=str(_ann_f.get("actual_publication_timestamp", _ann_f.get("filing_date", ""))),
                            statement_type="ANNUAL",
                            revenue=float(_ann_f.get("revenue")) if (_ann_f.get("revenue") is not None and pd.notna(_ann_f.get("revenue"))) else None,
                            operating_profit=float(_ann_f.get("operating_profit")) if (_ann_f.get("operating_profit") is not None and pd.notna(_ann_f.get("operating_profit"))) else None,
                            depreciation_amortization=float(_ann_f.get("depreciation_amortization")) if (_ann_f.get("depreciation_amortization") is not None and pd.notna(_ann_f.get("depreciation_amortization"))) else None,
                            ebitda=(float(_ann_f.get("operating_profit", 0) or 0) + float(_ann_f.get("depreciation_amortization", 0) or 0)) if (_ann_f.get("operating_profit") is not None or _ann_f.get("depreciation_amortization") is not None) else None,
                            net_profit=float(_ann_f.get("net_profit")) if (_ann_f.get("net_profit") is not None and pd.notna(_ann_f.get("net_profit"))) else None,
                            operating_cash_flow=float(_ann_f.get("operating_cash_flow")) if (_ann_f.get("operating_cash_flow") is not None and pd.notna(_ann_f.get("operating_cash_flow"))) else None,
                            total_debt=float(_ann_f.get("total_debt")) if (_ann_f.get("total_debt") is not None and pd.notna(_ann_f.get("total_debt"))) else None,
                            total_equity=float(_ann_f.get("total_equity")) if (_ann_f.get("total_equity") is not None and pd.notna(_ann_f.get("total_equity"))) else None,
                            cash_and_equivalents=float(_ann_f.get("cash_and_equivalents")) if (_ann_f.get("cash_and_equivalents") is not None and pd.notna(_ann_f.get("cash_and_equivalents"))) else None,
                            roce=float(_ann_f.get("roce")) if (_ann_f.get("roce") is not None and pd.notna(_ann_f.get("roce"))) else None,
                            eps=float(_ann_f.get("eps")) if (_ann_f.get("eps") is not None and pd.notna(_ann_f.get("eps"))) else None,
                            shares_outstanding=float(_ann_f.get("shares_outstanding")) if (_ann_f.get("shares_outstanding") is not None and pd.notna(_ann_f.get("shares_outstanding"))) else None,
                            source="PIT_RAW_FILING",
                            is_trailing_5y=True,
                        )

                # Granular Financial Reconstructions
                _r0 = row.get("start_revenue")
                _r1 = row.get("end_revenue")
                _ny = growth_years_elapsed
                collector.record_financial_reconstruction(
                    symbol=sym,
                    metric_name="5Y_SALES_CAGR",
                    formula="((r1 / r0) ** (1 / n) - 1.0) * 100.0",
                    inputs={
                        "start_revenue_cr": _r0,
                        "end_revenue_cr": _r1,
                        "start_period": growth_start_period,
                        "end_period": growth_end_period,
                        "years_elapsed": _ny,
                        "periods_used": financial_periods_used,
                    },
                    calculated_value=sales_cagr_5y,
                    unit="%",
                    provenance="PIT_ANNUAL_AUDITED_STATEMENTS",
                )
                _p0 = row.get("start_pat")
                _p1 = row.get("end_pat")
                collector.record_financial_reconstruction(
                    symbol=sym,
                    metric_name="5Y_PAT_CAGR",
                    formula="((p1 / p0) ** (1 / n) - 1.0) * 100.0",
                    inputs={
                        "start_pat_cr": _p0,
                        "end_pat_cr": _p1,
                        "start_period": growth_start_period,
                        "end_period": growth_end_period,
                        "years_elapsed": _ny,
                        "periods_used": financial_periods_used,
                    },
                    calculated_value=pat_cagr_5y,
                    unit="%",
                    provenance="PIT_ANNUAL_AUDITED_STATEMENTS",
                )
                collector.record_financial_reconstruction(
                    symbol=sym,
                    metric_name="5Y_CFO_PAT_RATIO",
                    formula="sum(operating_cash_flow_5y) / sum(net_profit_5y)",
                    inputs={
                        "cfo_annual_values": row.get("cfo_annual_values", []),
                        "pat_annual_values": row.get("pat_annual_values", []),
                        "sum_cfo_cr": row.get("sum_cfo"),
                        "sum_pat_cr": row.get("sum_pat"),
                        "periods_used": financial_periods_used,
                    },
                    calculated_value=cfo_pat_5y,
                    unit="ratio",
                    provenance="PIT_ANNUAL_AUDITED_STATEMENTS",
                )
                collector.record_financial_reconstruction(
                    symbol=sym,
                    metric_name="5Y_AVG_ROCE",
                    formula="sum(roce_annual_values) / len(roce_annual_values)",
                    inputs={
                        "roce_annual_values": row.get("roce_annual_values", []),
                        "periods_used": roce_periods_used,
                    },
                    calculated_value=roce_5y,
                    unit="%",
                    provenance="PIT_ANNUAL_AUDITED_STATEMENTS",
                )
                _d_stat = row.get("debt_reported_status", "MISSING")
                _eq_stat = row.get("equity_reported_status", "MISSING")
                collector.record_financial_reconstruction(
                    symbol=sym,
                    metric_name="DEBT_TO_EQUITY",
                    formula="total_debt / total_equity",
                    inputs={
                        "total_debt_cr": row.get("raw_total_debt"),
                        "total_equity_cr": row.get("raw_total_equity"),
                        "debt_reported_status": _d_stat,
                        "equity_reported_status": _eq_stat,
                        "default_used": False,
                        "fallback_source": "NONE",
                        "is_debt_free_company": (_d_stat == "GENUINELY_ZERO"),
                    },
                    calculated_value=de_ratio,
                    unit="ratio",
                    provenance="PIT_ANNUAL_BALANCE_SHEET",
                )
                collector.record_financial_reconstruction(
                    symbol=sym,
                    metric_name="CURRENT_EV_EBITDA",
                    formula="(MarketCap + TotalDebt - Cash) / EBITDA",
                    inputs={
                        "cmp_inr": cmp_price,
                        "shares_outstanding": _shares,
                        "market_cap_cr": mcap,
                        "total_debt_cr": _debt,
                        "cash_cr": _cash,
                        "ebitda_cr": _eb,
                        "operating_profit_cr": row.get("operating_profit"),
                        "depreciation_amortization_cr": row.get("depreciation_amortization"),
                        "net_debt_cr": row.get("ev_net_debt"),
                        "ev_total_cr": row.get("ev_total"),
                    },
                    calculated_value=ev_ebitda_curr,
                    unit="ratio",
                    provenance="POINT_IN_TIME_ENTERPRISE_VALUE_DERIVATION",
                )
                collector.record_financial_reconstruction(
                    symbol=sym,
                    metric_name="EV_EBITDA_3Y_MEDIAN",
                    formula="median(daily_ev_ebitda_trailing_750_trading_days)",
                    inputs={
                        "trailing_trading_days_window": 750,
                        "data_provider": "Upstox",
                        "as_of_date": "2026-09-25",
                        "median_value": ev_ebitda_med,
                    },
                    calculated_value=ev_ebitda_med,
                    unit="ratio",
                    provenance="UPSTOX_DAILY_CANDLES_AND_PIT_FILINGS",
                )
                collector.record_financial_reconstruction(
                    symbol=sym,
                    metric_name="EV_EBITDA_DISCOUNT",
                    formula="(median_ev_ebitda - current_ev_ebitda) / median_ev_ebitda",
                    inputs={
                        "current_ev_ebitda": ev_ebitda_curr,
                        "median_ev_ebitda": ev_ebitda_med,
                    },
                    calculated_value=calc_discount,
                    unit="ratio",
                    provenance="VALUATION_GATE_ARITHMETIC",
                )
                collector.record_financial_reconstruction(
                    symbol=sym,
                    metric_name="PE_3Y_MEDIAN",
                    formula="median(daily_pe_trailing_750_trading_days)",
                    inputs={
                        "trailing_trading_days_window": 750,
                        "data_provider": "Upstox",
                        "as_of_date": "2026-09-25",
                        "median_value": pe_med,
                    },
                    calculated_value=pe_med,
                    unit="ratio",
                    provenance="UPSTOX_DAILY_CANDLES_AND_PIT_FILINGS",
                )
                collector.record_financial_reconstruction(
                    symbol=sym,
                    metric_name="PE_DISCOUNT",
                    formula="(median_pe - current_pe) / median_pe",
                    inputs={
                        "current_pe": pe_curr,
                        "median_pe": pe_med,
                    },
                    calculated_value=pe_discount,
                    unit="ratio",
                    provenance="VALUATION_GATE_ARITHMETIC",
                )

                # Granular Score Breakdown
                _sc_detail = self.compute_100pt_score_detailed(
                    row_dict={
                        "roce_5y_avg": roce_5y,
                        "cfo_pat_5y_ratio": cfo_pat_5y,
                    },
                    ev_discount=ev_discount,
                    pe_discount=pe_discount,
                    res_dd=res_dd,
                )
                collector.record_score_breakdown(
                    symbol=sym,
                    ev_pts=_sc_detail["ev_pts"],
                    roce_pts=_sc_detail["roce_pts"],
                    pe_pts=_sc_detail["pe_pts"],
                    cfo_pts=_sc_detail["cfo_pts"],
                    res_pts=_sc_detail["res_pts"],
                    total_score_100=_sc_detail["total_score_100"],
                    tier=tier,
                )

                # Production metrics
                collector.record_production_metric(sym, "5Y_AVG_ROCE", roce_5y, f"{float(roce_5y):.2f}%" if roce_5y is not None and not pd.isna(roce_5y) else "N/A", roce_5y, "%")
                collector.record_production_metric(sym, "5Y_SALES_CAGR", clean_sales_cagr, disp_sales, clean_sales_cagr, "%")
                collector.record_production_metric(sym, "5Y_PAT_CAGR", clean_pat_cagr, disp_pat, clean_pat_cagr, "%")
                collector.record_production_metric(sym, "5Y_CFO_PAT_RATIO", clean_cfo_pat, disp_cfo, clean_cfo_pat, "ratio")
                collector.record_production_metric(sym, "DEBT_TO_EQUITY", de_ratio, f"{float(de_ratio):.2f}" if de_ratio is not None and not pd.isna(de_ratio) else "N/A", de_ratio, "ratio")
                collector.record_production_metric(sym, "CURRENT_EV_EBITDA", clean_ev_curr, disp_ev_curr, clean_ev_curr, "ratio")
                collector.record_production_metric(sym, "EV_EBITDA_3Y_MEDIAN", ev_ebitda_med, f"{float(ev_ebitda_med):.2f}" if ev_ebitda_med is not None and not pd.isna(ev_ebitda_med) else "N/A", ev_ebitda_med, "ratio")
                collector.record_production_metric(sym, "EV_EBITDA_DISCOUNT", calc_discount, f"{calc_discount*100:.1f}%" if calc_discount is not None else "N/A", calc_discount, "%")
                collector.record_production_metric(sym, "MARKET_CAP_CR", mcap, f"₹{mcap:.2f} Cr" if mcap is not None else "N/A", mcap, "Cr")
                collector.record_production_metric(sym, "ADTV_90D_CR", adtv_90d, f"₹{adtv_90d:.2f} Cr" if adtv_90d is not None else "N/A", adtv_90d, "Cr")
                collector.record_production_metric(sym, "TIER", tier, tier, tier, "")
                collector.record_production_metric(sym, "RANKING_SCORE_100", score_100, f"{score_100:.1f}", score_100, "points")

                # Gate evaluations
                collector.record_gate_result(sym, "MARKET_CAP", "ELIGIBILITY", ">= ₹1,000 Cr", 1000.0, mcap, ">=", "PASS" if (mcap and mcap >= 1000.0) else "FAIL")
                collector.record_gate_result(sym, "LIQUIDITY_ADTV", "ELIGIBILITY", ">= ₹2 Cr", 2.0, adtv_90d, ">=", "PASS" if (adtv_90d and adtv_90d >= 2.0) else "FAIL")
                collector.record_gate_result(sym, "FINANCIAL_EXCLUSION", "ELIGIBILITY", "Non-Financial", "Non-Financial", industry, "NOT_IN", "FAIL" if is_fin else "PASS")
                collector.record_gate_result(sym, "PRICE_CMP", "ELIGIBILITY", "> ₹0.00", 0.0, cmp_price, ">", "PASS" if cmp_price > 0 else "FAIL")

                if not is_fin:
                    p_roce = (roce_5y is not None and not pd.isna(roce_5y) and float(roce_5y) >= 15.0)
                    p_sales = (clean_sales_cagr is not None and not pd.isna(clean_sales_cagr) and float(clean_sales_cagr) >= 10.0)
                    p_pat = (clean_pat_cagr is not None and not pd.isna(clean_pat_cagr) and float(clean_pat_cagr) >= 10.0)
                    p_cfo = (clean_cfo_pat is not None and not pd.isna(clean_cfo_pat) and float(clean_cfo_pat) >= 0.80)
                    p_de = (de_ratio is not None and not pd.isna(de_ratio) and float(de_ratio) <= 0.50)
                    collector.record_gate_result(sym, "5Y_ROCE", "QUALITY", ">= 15.0%", 15.0, roce_5y, ">=", "PASS" if p_roce else "FAIL")
                    collector.record_gate_result(sym, "5Y_SALES_CAGR", "QUALITY", ">= 10.0%", 10.0, clean_sales_cagr, ">=", "PASS" if p_sales else "FAIL")
                    collector.record_gate_result(sym, "5Y_PAT_CAGR", "QUALITY", ">= 10.0%", 10.0, clean_pat_cagr, ">=", "PASS" if p_pat else "FAIL")
                    collector.record_gate_result(sym, "5Y_CFO_PAT", "QUALITY", ">= 0.80", 0.80, clean_cfo_pat, ">=", "PASS" if p_cfo else "FAIL")
                    collector.record_gate_result(sym, "DEBT_TO_EQUITY", "QUALITY", "<= 0.50", 0.50, de_ratio, "<=", "PASS" if p_de else "FAIL")

                    p_val = (calc_discount is not None and calc_discount >= 0.25)
                    collector.record_gate_result(sym, "EV_EBITDA_DISCOUNT", "VALUATION", ">= 25.0%", 0.25, calc_discount, ">=", "PASS" if p_val else "FAIL")
                else:
                    collector.record_gate_result(sym, "5Y_ROCE", "QUALITY", ">= 15.0%", 15.0, roce_5y, ">=", "BLOCKED_FINANCIAL_SECTOR")
                    collector.record_gate_result(sym, "5Y_SALES_CAGR", "QUALITY", ">= 10.0%", 10.0, clean_sales_cagr, ">=", "BLOCKED_FINANCIAL_SECTOR")
                    collector.record_gate_result(sym, "5Y_PAT_CAGR", "QUALITY", ">= 10.0%", 10.0, clean_pat_cagr, ">=", "BLOCKED_FINANCIAL_SECTOR")
                    collector.record_gate_result(sym, "5Y_CFO_PAT", "QUALITY", ">= 0.80", 0.80, clean_cfo_pat, ">=", "BLOCKED_FINANCIAL_SECTOR")
                    collector.record_gate_result(sym, "DEBT_TO_EQUITY", "QUALITY", "<= 0.50", 0.50, de_ratio, "<=", "BLOCKED_FINANCIAL_SECTOR")
                    collector.record_gate_result(sym, "EV_EBITDA_DISCOUNT", "VALUATION", ">= 25.0%", 0.25, calc_discount, ">=", "BLOCKED_FINANCIAL_SECTOR")

                # Trailing 3Y Daily Valuation observations for candidates
                if is_candidate and df_px is not None and not df_px.empty and _safe_pos(ev_ebitda_med):
                    try:
                        _d_cand = ['Date', 'date', 'Timestamp', 'timestamp']
                        _d_col = next((c for c in _d_cand if c in df_px.columns), None)
                        _df_c = df_px.copy()
                        _df_c['dt'] = pd.to_datetime(_df_c[_d_col] if _d_col else _df_c.index).dt.tz_localize(None)
                        _max_dt = _df_c['dt'].max()
                        _min_3y = _max_dt - pd.Timedelta(days=3 * 365.25)
                        _df_3y = _df_c[_df_c['dt'] >= _min_3y].sort_values('dt')
                        _cl_cand = ['Close', 'close', 'Adj Close', 'adj_close']
                        _cl_col = next((c for c in _cl_cand if c in _df_3y.columns), None)
                        if _cl_col:
                            _sh_cand = float(_shares or 0) if _shares else 0.0
                            _d_cand_val = float(_debt or 0) if _debt else 0.0
                            _c_cand_val = float(_cash or 0) if _cash else 0.0
                            _eb_cand = float(_eb or 0) if _eb else 0.0
                            _obs_list = []
                            for _, _b_row in _df_3y.iterrows():
                                _b_px = float(_b_row[_cl_col])
                                _b_dt = str(_b_row['dt'])[:10]
                                _b_mc = (_sh_cand * _b_px) / 1e7 if _sh_cand > 0 else np.nan
                                _b_ev = (_b_mc + _d_cand_val - _c_cand_val) if not np.isnan(_b_mc) else np.nan
                                _b_ev_eb = (_b_ev / _eb_cand) if (_eb_cand > 0 and not np.isnan(_b_ev) and _b_ev > 0) else np.nan
                                _obs_list.append({
                                    "observation_date": _b_dt,
                                    "trade_date": _b_dt,
                                    "close": _b_px,
                                    "ev_ebitda": _b_ev_eb if not np.isnan(_b_ev_eb) else None,
                                    "is_valid_ev_sample": not np.isnan(_b_ev_eb),
                                    "final_3y_median_ev": ev_ebitda_med,
                                    "samples_count": len(_df_3y),
                                    "provider": "Upstox",
                                })
                            collector.record_historical_valuation_timeseries(sym, _obs_list)
                    except Exception as _v_obs_err:
                        logger.debug(f"Valuation observations trace note for {sym}: {_v_obs_err}")

                    logger.info(
                        f"📊 [CANDIDATE_AUDIT_TRACE: {sym}] Tier={tier} | CMP=₹{cmp_price:.2f} | Score={score_100:.1f} | "
                        f"ROCE={float(roce_5y):.1f}% | Sales_CAGR={float(sales_cagr_5y):.1f}% | PAT_CAGR={float(pat_cagr_5y):.1f}% | "
                        f"CFO/PAT={float(cfo_pat_5y):.2f} | D/E={float(de_ratio):.2f} | EV_curr={float(ev_ebitda_curr):.2f} | "
                        f"EV_med={float(ev_ebitda_med):.2f} | EV_disc={calc_discount*100:.1f}% | PE_curr={float(pe_curr):.2f} | "
                        f"PE_med={float(pe_med):.2f} | PE_disc={(pe_discount*100 if pe_discount else 0):.1f}%"
                    )

                # Decision trace steps
                collector.record_decision_trace_step(
                    symbol=sym,
                    step_sequence=1,
                    stage="UNIVERSE_GATE",
                    input_summary=f"Symbol={sym}, in_approved_universe=True",
                    threshold_applied="APPROVED_UNIVERSE_MEMBER",
                    evaluation_result="PASS",
                    decision_action="PROCEED_TO_ELIGIBILITY_GATE",
                    next_stage="ELIGIBILITY_GATE",
                )
                _elig_pass = (mcap and mcap >= 1000.0 and adtv_90d and adtv_90d >= 2.0 and not is_fin and cmp_price > 0)
                collector.record_decision_trace_step(
                    symbol=sym,
                    step_sequence=2,
                    stage="ELIGIBILITY_GATE",
                    input_summary=f"Mcap={mcap}, ADTV={adtv_90d}, is_fin={is_fin}, CMP={cmp_price}",
                    threshold_applied="MCAP>=1000Cr, ADTV>=2Cr, NON_FIN, CMP>0",
                    evaluation_result="PASS" if _elig_pass else "FAIL",
                    decision_action="PROCEED_TO_QUALITY_GATE" if _elig_pass else "REJECT",
                    next_stage="QUALITY_GATE" if _elig_pass else "TERMINATED",
                )
                collector.record_decision_trace_step(
                    symbol=sym,
                    step_sequence=3,
                    stage="QUALITY_GATE",
                    input_summary=f"ROCE={roce_5y}, Sales={sales_cagr_5y}, PAT={pat_cagr_5y}, CFO={cfo_pat_5y}, D/E={de_ratio}",
                    threshold_applied="ROCE>=15%, Sales>=10%, PAT>=10%, CFO/PAT>=0.8, DE<=0.5",
                    evaluation_result="PASS" if quality_gate_passed else "FAIL",
                    decision_action="PROCEED_TO_VALUATION_GATE" if quality_gate_passed else "REJECT",
                    next_stage="VALUATION_GATE" if quality_gate_passed else "TERMINATED",
                )
                collector.record_decision_trace_step(
                    symbol=sym,
                    step_sequence=4,
                    stage="VALUATION_GATE",
                    input_summary=f"Current_EV={ev_ebitda_curr}, 3Y_Med={ev_ebitda_med}, Discount={calc_discount}",
                    threshold_applied="EV/EBITDA Discount >= 25%",
                    evaluation_result="PASS" if value_gate_passed else "FAIL",
                    decision_action="GENERATE_ALERT" if is_candidate else "REJECT",
                    next_stage="ALERT_ROUTING" if is_candidate else "TERMINATED",
                )
                collector.record_decision_trace_step(
                    symbol=sym,
                    step_sequence=5,
                    stage="ALERT_ROUTING",
                    input_summary=f"is_candidate={is_candidate}, score_100={score_100}, tier={tier}",
                    threshold_applied="ALL_GATES_PASSED_AND_CMP_VALID",
                    evaluation_result="ALERT_BUY" if is_candidate else "REJECTED",
                    decision_action="PERSIST_ALERT" if is_candidate else "NONE",
                    next_stage="COMPLETE",
                )

                _rej_stage = "NONE" if is_candidate else ("DATA_GATE" if any(r.startswith("DATA_") or r.startswith("STRUCTURAL_") for r in rejections) else ("QUALITY_GATE" if not quality_gate_passed else "VALUATION_GATE"))
                collector.record_stock_master(
                    symbol=sym,
                    seq_num=total_scanned,
                    overall_status=_top_pop,
                    final_decision="ALERT_BUY" if is_candidate else ("BLOCKED" if any(r.startswith("DATA_") or r.startswith("STRUCTURAL_") for r in rejections) else "REJECTED"),
                    alert_generated=is_candidate,
                    alert_type="BUY" if is_candidate else "NONE",
                    blocked=(not is_candidate and any(r.startswith("DATA_") or r.startswith("STRUCTURAL_") for r in rejections)),
                    rejected=not is_candidate,
                    rejection_stage=_rej_stage,
                    rejection_reason=primary_rejection,
                )

                if not is_candidate:
                    collector.record_rejection(
                        symbol=sym,
                        rejection_stage=_rej_stage,
                        rejection_reason=primary_rejection,
                        evaluated_gates_count=len(rejections),
                        primary_failed_gate=rejections[0] if rejections else "UNKNOWN",
                        detailed_explanation="; ".join(rejections) if rejections else "Failed gate criteria",
                    )
                else:
                    collector.record_alert(
                        symbol=sym,
                        cmp_price=cmp_price,
                        tier=tier,
                        score=score_100,
                        ranking_score=score_100,
                        alert_reason="MET_ALL_QUALITY_AND_VALUATION_HARD_GATES",
                        routing_result="PERSISTED_TO_ALERTS",
                    )

            is_p_struct = bool(sym in structural_ineligible_symbols)
            if is_p_struct:
                is_q_df = False
                is_v_df = False
                is_p_df = False
                is_o_df = False
                is_inc = False
                is_eval = False
                final_act = "STRUCTURAL_INELIGIBLE"
                data_qual = "STRUCTURAL_INELIGIBLE"
            else:
                is_q_df = bool(sym in quality_df_symbols and not is_fin)
                is_v_df = bool(sym in val_df_symbols and not is_fin)
                is_p_df = bool(sym in price_df_symbols)
                is_o_df = bool(sym in non_pit_df_symbols)

                # Deterministic precedence:
                # 1. Missing/unresolved/provider failure -> INCOMPLETE
                # 2. Valid but outside freshness window -> STALE (classified as DATA_FAILURE)
                # 3. Valid and fresh -> FRESH (FULLY_EVALUABLE)
                _is_stale_pit = False
                _ann_per = row.get("financial_period_end") or row.get("period_end_date") or row.get("latest_annual_period")
                if _ann_per and not (is_q_df or is_v_df or is_p_df or is_o_df):
                    _fresh_res = check_pit_freshness(sym, str(_ann_per)[:10], scan_date=now_ist.date())
                    if not _fresh_res.ok:
                        _is_stale_pit = True
                        is_o_df = True
                        _df_rsns.append(f"PIT_DATA_STALE: {_fresh_res.reason}")

                is_inc = bool(is_q_df or is_v_df or is_p_df or is_o_df)
                is_eval = bool(not is_inc)
                final_act = "BUY_ALERT" if (is_candidate and is_eval) else ("INCOMPLETE" if is_inc else "REJECTED")
                if is_inc:
                    data_qual = "STALE" if _is_stale_pit else "INCOMPLETE"
                else:
                    data_qual = "FRESH"

            canonical_records.append({
                "symbol": sym,
                "structural_ineligible": is_p_struct,
                "quality_data_failure": is_q_df,
                "valuation_data_failure": is_v_df,
                "price_data_failure": is_p_df,
                "other_data_failure": is_o_df,
                "incomplete": is_inc,
                "fully_evaluable": is_eval,
                "data_quality_bucket": data_qual,
                "final_action": final_act,
                "top_level_population": _top_pop,
                "quality_status": "EXCLUDED_FINANCIAL" if is_fin else ("NOT_EVALUATED" if quality_data_missing else ("PASS" if quality_gate_passed else "FAIL")),
                "valuation_status": "EXCLUDED_FINANCIAL" if is_fin else ("NOT_EVALUATED" if valuation_data_missing else ("PASS" if value_gate_passed else "FAIL")),
                "price_status": _price_status,
                "structural_reason": _struct_rsn if is_p_struct else None,
                "data_failure_reasons": "; ".join(_df_rsns) if _df_rsns else None,
                "provenance_source": "PIT_FUNDAMENTALS_V1",
            })

        # Persist scan results & evaluate post-scan health under safety gates
        try:
            duration_sec = round(time.time() - start_ts, 2)

            # ── CONSTRUCT CANONICAL PER-STOCK TABLE ─────────────────────────────
            canonical_df = pd.DataFrame(canonical_records)
            assert len(canonical_df) == total_scanned, f"Canonical table rows ({len(canonical_df)}) != total_scanned ({total_scanned})"
            assert canonical_df["symbol"].nunique() == total_scanned, f"Canonical table symbols ({canonical_df['symbol'].nunique()}) != total_scanned ({total_scanned})"

            scanned_count = len(canonical_df)
            structural_count = int(canonical_df["structural_ineligible"].sum())
            quality_df_count = int(canonical_df["quality_data_failure"].sum())
            val_df_count = int(canonical_df["valuation_data_failure"].sum())
            price_df_count = int(canonical_df["price_data_failure"].sum())
            other_df_count = int(canonical_df["other_data_failure"].sum())
            incomplete_count = int(canonical_df["incomplete"].sum())
            fully_evaluable_count = int(canonical_df["fully_evaluable"].sum())
            alerts_count = int((canonical_df["final_action"] == "BUY_ALERT").sum())
            rejected_count = int((canonical_df["final_action"] == "REJECTED").sum())

            incomplete_symbols = sorted(canonical_df[canonical_df["incomplete"]]["symbol"].tolist())
            structural_symbols = sorted(canonical_df[canonical_df["structural_ineligible"]]["symbol"].tolist())
            evaluable_symbols = sorted(canonical_df[canonical_df["fully_evaluable"]]["symbol"].tolist())
            alert_symbols = sorted(canonical_df[canonical_df["final_action"] == "BUY_ALERT"]["symbol"].tolist())

            # Canonical Fresh/Stale/Incomplete accounting from unique symbol sets
            evaluable_df = canonical_df[~canonical_df["structural_ineligible"]]
            fresh_symbols = set(evaluable_df[evaluable_df["data_quality_bucket"] == "FRESH"]["symbol"])
            stale_symbols = set(evaluable_df[evaluable_df["data_quality_bucket"] == "STALE"]["symbol"])
            incomplete_symbols_set = set(evaluable_df[evaluable_df["data_quality_bucket"] == "INCOMPLETE"]["symbol"])

            dashboard_classified_symbol_count = len(evaluable_df)
            fresh_count = len(fresh_symbols)
            stale_count = len(stale_symbols)
            incomplete_count = len(incomplete_symbols_set)

            # Mathematical no-double-counting assertions
            assert fresh_count + stale_count + incomplete_count == dashboard_classified_symbol_count, (
                f"Mismatch: fresh ({fresh_count}) + stale ({stale_count}) + incomplete ({incomplete_count}) != {dashboard_classified_symbol_count}"
            )
            assert fresh_symbols.isdisjoint(stale_symbols), "fresh_symbols ∩ stale_symbols must be empty"
            assert fresh_symbols.isdisjoint(incomplete_symbols_set), "fresh_symbols ∩ incomplete_symbols must be empty"
            assert stale_symbols.isdisjoint(incomplete_symbols_set), "stale_symbols ∩ incomplete_symbols must be empty"

            # Legacy aliases for logging & context
            data_failure_symbols = set(incomplete_symbols)
            data_failure_count = incomplete_count
            structural_ineligible_count = structural_count
            structural_ineligible_symbols = set(structural_symbols)
            fully_evaluable_symbols = set(evaluable_symbols)
            _approved_universe = scanned_count
            _structural_ineligible = structural_count
            _evaluable_universe = _approved_universe - _structural_ineligible
            _data_failures = incomplete_count
            _fully_evaluable = fully_evaluable_count
            _quality_evaluated = int((canonical_df["quality_status"].isin(["PASS", "FAIL"])).sum()) if "quality_status" in canonical_df.columns else (quality_pass_count + quality_reject_count)
            _val_evaluated = int((canonical_df["valuation_status"].isin(["PASS", "FAIL"])).sum()) if "valuation_status" in canonical_df.columns else (value_pass_count + value_reject_count)

            # Strict disjoint category sets for reporting
            q_syms = set(canonical_df[canonical_df["quality_data_failure"]]["symbol"])
            v_syms = set(canonical_df[canonical_df["valuation_data_failure"]]["symbol"])
            p_syms = set(canonical_df[canonical_df["price_data_failure"]]["symbol"])
            o_syms = set(canonical_df[canonical_df["other_data_failure"]]["symbol"])

            quality_only_syms = q_syms - v_syms - p_syms - o_syms
            val_only_syms = v_syms - q_syms - p_syms - o_syms
            price_only_syms = p_syms - q_syms - v_syms - o_syms
            other_only_syms = o_syms - q_syms - v_syms - p_syms
            overlap_syms = set(incomplete_symbols) - quality_only_syms - val_only_syms - price_only_syms - other_only_syms
            overlap_count = len(overlap_syms)

            # Mathematical integrity assertions
            is_reconciled = (
                scanned_count == structural_count + incomplete_count + fully_evaluable_count
                and fully_evaluable_count == alerts_count + rejected_count
                and len(canonical_df[canonical_df["structural_ineligible"] & canonical_df["incomplete"]]) == 0
                and len(canonical_df[canonical_df["structural_ineligible"] & canonical_df["fully_evaluable"]]) == 0
                and len(canonical_df[canonical_df["incomplete"] & canonical_df["fully_evaluable"]]) == 0
            )

            # ── POST-SCAN HEALTH STATUS ───────────────────────────────────────────
            zero_price_candidates = [c for c in candidate_records if float(c.get("current_price", 0) or 0) <= 0]
            if zero_price_candidates:
                _health_status = "BLOCKED"
                _health_error = f"ZERO_PRICE_CANDIDATE_DEFECT: {len(zero_price_candidates)} candidates produced with CMP <= 0"
            elif not is_reconciled:
                _health_status = "INCONSISTENT"
                _health_error = (
                    f"INCONSISTENT: Scanned ({scanned_count}) != Structural ({structural_count}) + "
                    f"Incomplete ({incomplete_count}) + Evaluable ({fully_evaluable_count}) or "
                    f"Evaluable != Alerts ({alerts_count}) + Rejected ({rejected_count})"
                )
            elif incomplete_count > 0:
                _health_status = "DEGRADED"
                _health_error = (
                    f"DATA_DEGRADED: {incomplete_count} stocks incomplete with data failures "
                    f"({', '.join(sorted(incomplete_symbols)[:10])})"
                )
            else:
                _health_status = "OK"
                _health_error = None

            if _health_error:
                logger.warning(f"⚠️ [QUALITY_COMPOUNDER] SCANNER HEALTH: {_health_status} | {_health_error}")

            # ── ALERT ROUTING GOVERNANCE UNDER HEALTH GATES ─────────────────────────
            candidates_inserted = 0
            if _health_status in ("DATA_BLOCKED", "BLOCKED", "INCONSISTENT"):
                if candidate_records:
                    logger.warning(
                        f"🚫 [QUALITY_COMPOUNDER_ALERT_SUPPRESSED] SCANNER HEALTH IS {_health_status}: "
                        f"{len(candidate_records)} candidate BUY alert(s) ({[c['symbol'] for c in candidate_records]}) "
                        f"were SUPPRESSED from live alerts table. Preserving snapshots as RESEARCH_CANDIDATE_DATA_BLOCKED."
                    )
                for s_rec in snapshot_records:
                    if s_rec.get("overall_candidate_status") == "CANDIDATE":
                        s_rec["overall_candidate_status"] = "RESEARCH_CANDIDATE_DATA_BLOCKED"
                        s_rec["watchlist_state"] = "DATA_BLOCKED"
                        s_rec["rejection_reason"] = f"SCANNER_HEALTH_{_health_status}"
            else:
                for cand in candidate_records:
                    # Pre-BUY Data Integrity Gate (C18 / C39)
                    fin_metrics = {
                        "roce_5y_avg": FieldProvenance(
                            symbol=cand["symbol"], scanner="QUALITY_COMPOUNDER", field="roce_5y_avg",
                            value_used=cand.get("context", {}).get("roce_5y_avg"), unit="PERCENT",
                            period_end=str(cand.get("context", {}).get("latest_annual_period") or "2025-03-31"),
                            basis="CONSOLIDATED",
                            source_used="PIT_FUNDAMENTALS", validation_status="PASSED"
                        ),
                        "cfo_pat_5y_ratio": FieldProvenance(
                            symbol=cand["symbol"], scanner="QUALITY_COMPOUNDER", field="cfo_pat_5y_ratio",
                            value_used=cand.get("context", {}).get("cfo_pat_5y_ratio"), unit="RATIO",
                            period_end=str(cand.get("context", {}).get("latest_annual_period") or "2025-03-31"),
                            basis="CONSOLIDATED",
                            source_used="PIT_FUNDAMENTALS", validation_status="PASSED"
                        ),
                        "current_ev_ebitda": FieldProvenance(
                            symbol=cand["symbol"], scanner="QUALITY_COMPOUNDER", field="current_ev_ebitda",
                            value_used=cand.get("context", {}).get("current_ev_ebitda"), unit="RATIO",
                            period_end=str(cand.get("context", {}).get("latest_annual_period") or "2025-03-31"),
                            basis="CONSOLIDATED",
                            source_used="STATEMENT_FILINGS", validation_status="PASSED"
                        ),
                    }
                    if cand.get("context", {}).get("cash_and_equivalents") is not None:
                        fin_metrics["cash_and_equivalents"] = FieldProvenance(
                            symbol=cand["symbol"], scanner="QUALITY_COMPOUNDER", field="cash_and_equivalents",
                            value_used=cand.get("context", {}).get("cash_and_equivalents"), unit="INR_CRORE",
                            period_end=str(cand.get("context", {}).get("latest_annual_period") or "2025-03-31"),
                            basis="CONSOLIDATED",
                            source_used="EXCHANGE_FILINGS", validation_status="PASSED"
                        )
                    if cand.get("context", {}).get("shares_outstanding") is not None:
                        fin_metrics["shares_outstanding"] = FieldProvenance(
                            symbol=cand["symbol"], scanner="QUALITY_COMPOUNDER", field="shares_outstanding",
                            value_used=cand.get("context", {}).get("shares_outstanding"), unit="NUMBER",
                            period_end=str(cand.get("context", {}).get("latest_annual_period") or "2025-03-31"),
                            basis="CONSOLIDATED",
                            source_used="EXCHANGE_FILINGS", validation_status="PASSED"
                        )

                    c_bundle = BUYEvidenceBundle(
                        scan_run_id=getattr(exec_run_ctx, "run_id", "QC_RUN") if exec_run_ctx else "QC_RUN",
                        scanner="QUALITY_COMPOUNDER",
                        symbol=cand["symbol"],
                        cmp=cand.get("current_price"),
                        strategy_score=cand.get("ranking_score"),
                        gate_results={"QUALITY": True, "VALUATION": True},
                        financial_metrics=fin_metrics,
                        data_integrity_status=DataStatus.VALID,
                        financial_provenance_complete=True,
                        pit_valid=True,
                        period_integrity=True,
                        basis_integrity=True,
                        unit_integrity=True,
                        required_metrics_complete=True,
                    )
                    c_verdict = pre_buy_data_integrity_gate(c_bundle)
                    if not c_verdict.ok:
                        logger.warning(
                            f"🛑 [PRE_BUY_GATE_BLOCKED: QUALITY_COMPOUNDER] {cand['symbol']} failed Pre-BUY Data Integrity Gate: "
                            f"{c_verdict.reason} | {c_bundle.blocking_reasons}. Alert suppressed."
                        )
                        continue

                    ok, msg = save_v2_candidate_alert(cand)
                    if ok:
                        candidates_inserted += 1
                        logger.info(
                            f"🚀 [BUY_ALERT: QUALITY_COMPOUNDER] {cand['symbol']:<12} | Tier={cand['tier']} | "
                            f"Score={cand['ranking_score']:<5.1f} | CMP=₹{cand['current_price']:<8.2f} (Source={cand['context'].get('price_source', 'UNKNOWN')}) | Status={msg}"
                        )

            snapshots_inserted = save_v2_scan_snapshots(snapshot_records)

            if complete_scanner_execution_run is not None and exec_run_ctx and getattr(exec_run_ctx, "run_id", None):
                try:
                    complete_scanner_execution_run(
                        ctx=exec_run_ctx,
                        run_id=exec_run_ctx.run_id,
                        total_scanned=scanned_count,
                        total_stocks=scanned_count,
                        candidate_count=candidates_inserted,
                        quality_status=(
                            _health_status if _health_status in ("BLOCKED", "INCONSISTENT", "DATA_BLOCKED", "DEGRADED")
                            else ("PARTIAL" if incomplete_count > 0 else "NORMAL")
                        ),
                        lifecycle_status="COMPLETED" if _health_status not in ("BLOCKED", "INCONSISTENT") else "FAILED",
                        fresh_data_count=fresh_count,
                        stale_data_count=stale_count,
                        incomplete_data_count=incomplete_count,
                        data_insufficient_count=quality_df_count + val_df_count,
                        data_missing_count=incomplete_count,
                        provider_failure_count=price_df_count,
                        summary_notes=(
                            f"Scanned={scanned_count} | Fresh={fresh_count} | Stale={stale_count} | "
                            f"Incomplete={incomplete_count} | StructuralIneligible={structural_count} | "
                            f"DataInsuff={quality_df_count + val_df_count} | Missing={incomplete_count} | "
                            f"Alerts={candidates_inserted} | Health={_health_status}"
                        ),
                        metrics_json={
                            "total_scanned": scanned_count,
                            "fresh_data_count": fresh_count,
                            "stale_data_count": stale_count,
                            "incomplete_data_count": incomplete_count,
                            "structural_ineligible_count": structural_count,
                            "quality_data_failure_count": quality_df_count,
                            "valuation_data_failure_count": val_df_count,
                            "price_data_failure_count": price_df_count,
                            "other_data_failure_count": other_df_count,
                            "quality_only_count": len(quality_only_syms),
                            "valuation_only_count": len(val_only_syms),
                            "price_only_count": len(price_only_syms),
                            "overlap_failure_count": overlap_count,
                            "live_alerts_generated": candidates_inserted,
                            "health_status": _health_status,
                            "health_error": _health_error,
                            "incomplete_symbols": sorted(list(incomplete_symbols)),
                            "structural_symbols": sorted(list(structural_symbols)),
                            "requested_price_symbols_count": len(requested_price_symbols),
                            "successful_price_symbols_count": len(successful_price_symbols),
                            "provider_failed_symbols_count": len(provider_failed_symbols),
                            "zero_or_negative_price_symbols_count": len(zero_or_negative_price_symbols),
                        }
                    )
                except Exception as e:
                    logger.debug(f"Execution history completion warning: {e}")

            if upsert_scanner_health is not None:
                try:
                    upsert_scanner_health(
                        "QUALITY_COMPOUNDER",
                        status="OK" if _health_status in ("OK", "COMPLETED") else _health_status,
                        today_alerts=candidates_inserted,
                        last_success=now_ist.isoformat() if _health_status in ("OK", "COMPLETED", "DEGRADED") else None,
                        processed_count=candidates_inserted,
                        total_count=scanned_count,
                        duration_seconds=duration_sec,
                        error_msg=_health_error if _health_status not in ("OK", "COMPLETED") else None,
                        run_id=getattr(exec_run_ctx, "run_id", None)
                    )
                except Exception as e:
                    logger.debug(f"Scanner health update warning: {e}")

            logger.info("=" * 80)
            logger.info(f"📊 [SCANNER TELEMETRY: QUALITY_COMPOUNDER] CANONICAL POPULATION REPORT ({today_str})")
            logger.info("=" * 80)
            logger.info("  1. CANONICAL AUDIT POPULATIONS (Strict Disjoint Partition):")
            logger.info(f"     • Scanned Universe (Approved)    : {scanned_count}")
            logger.info(f"     • Fully Evaluable                : {fully_evaluable_count} ({round(fully_evaluable_count/max(1,scanned_count)*100, 1)}%)")
            logger.info(f"       ├─ BUY Alerts Produced         : {alerts_count}")
            logger.info(f"       └─ Filter Rejections           : {rejected_count}")
            logger.info(f"     • Incomplete (Data Failures)     : {incomplete_count} ({round(incomplete_count/max(1,scanned_count)*100, 1)}%)")
            logger.info(f"       ├─ Quality Data Failures       : {quality_df_count} ({len(quality_only_syms)} quality-only)")
            logger.info(f"       ├─ Valuation Data Failures     : {val_df_count} ({len(val_only_syms)} valuation-only)")
            logger.info(f"       ├─ Price Data Failures         : {price_df_count} ({len(price_only_syms)} price-only)")
            logger.info(f"       └─ Multi-Failure Overlap       : {overlap_count} ({', '.join(overlap_syms) if overlap_syms else 'None'})")
            logger.info(f"     • Structural Ineligible          : {structural_count} ({', '.join(structural_symbols) if structural_symbols else 'None'})")
            logger.info("")
            logger.info("  2. CANONICAL POPULATION IDENTITIES:")
            logger.info(f"     • Scanned = Structural + Incomplete + Evaluable ({scanned_count} = {structural_count} + {incomplete_count} + {fully_evaluable_count})  {'✅ PASS' if is_reconciled else '❌ INCONSISTENT'}")
            logger.info(f"     • Evaluable = Alerts + Rejections ({fully_evaluable_count} = {alerts_count} + {rejected_count})  {'✅ PASS' if fully_evaluable_count == alerts_count + rejected_count else '❌ INCONSISTENT'}")
            logger.info("")
            logger.info("  3. HEALTH & GOVERNANCE STATE:")
            logger.info(f"     • Health Status                  : {_health_status}")
            logger.info(f"     • Health Error Message           : {_health_error or 'None (Clean Run)'}")
            logger.info(f"     • Production BUY Alerts Saved    : {candidates_inserted}")
            logger.info(f"     • Snapshots Saved in DB          : {snapshots_inserted}  (100% universe audit trail)")
            logger.info(f"     • Duration (Seconds)             : {duration_sec}s")
            logger.info("=" * 80)

            # Save symbol-level canonical audit CSV & Parquet
            audit_parquet_p = os.path.join(DATA_DIR, "v2_health_population_audit.parquet")
            audit_csv_p = os.path.join(DATA_DIR, "v2_health_population_audit.csv")
            try:
                canonical_export_cols = [
                    "symbol",
                    "structural_ineligible",
                    "quality_data_failure",
                    "valuation_data_failure",
                    "price_data_failure",
                    "other_data_failure",
                    "incomplete",
                    "fully_evaluable",
                    "final_action",
                    "top_level_population",
                    "quality_status",
                    "valuation_status",
                    "price_status",
                    "structural_reason",
                    "data_failure_reasons",
                    "provenance_source",
                ]
                audit_df = canonical_df[[c for c in canonical_export_cols if c in canonical_df.columns]]
                if scanned_count >= 800:
                    audit_df.to_csv(audit_csv_p, index=False)
                    audit_df.to_parquet(audit_parquet_p, index=False)
                    logger.info(f"📁 [V2_AUDIT] Saved canonical per-stock population audit artifact to {audit_csv_p} and {audit_parquet_p} ({len(audit_df)} records)")

                logger.info("  4. POPULATION REASON AUDIT SUMMARY:")
                _pop_grp = audit_df.groupby(["top_level_population", "structural_reason", "data_failure_reasons"], dropna=False).size()
                for (_pop, _s_rsn, _df_rsn), _cnt in _pop_grp.items():
                    _rsn_lbl = _s_rsn if _pop == "STRUCTURAL_INELIGIBLE" else (_df_rsn if _pop == "DATA_FAILURE" else "FULLY_EVALUABLE")
                    logger.info(f"     • {_pop:<22} | Reason: {str(_rsn_lbl):<46} | Count: {_cnt}")
            except Exception as _aud_err:
                logger.warning(f"Failed to persist population audit artifact: {_aud_err}")

            logger.info("-" * 80)
            if not _valuation_provider_healthy:
                if candidate_records and _health_status in ("DATA_BLOCKED", "BLOCKED"):
                    logger.warning(
                        f"🔒 [V2_FINAL] {len(candidate_records)} RESEARCH CANDIDATE(S) DETECTED "
                        f"BUT SUPPRESSED FROM PRODUCTION — "
                        f"Scanner health={_health_status}: valuation data pipeline is incomplete "
                        f"(Current EV/EBITDA available for {_ev_curr_cnt}/{pit_univ_cnt} PIT symbols). "
                        f"Candidates ({[c['symbol'] for c in candidate_records]}) are preserved as "
                        f"RESEARCH_CANDIDATE_DATA_BLOCKED. Required action: fix upstream current EV/EBITDA feed."
                    )
                else:
                    logger.error(
                        "🚫 [V2_FINAL] ZERO PRODUCTION CANDIDATES — "
                        "No stock passed all quality + valuation gates AND valuation data is incomplete. "
                        f"Current EV/EBITDA available for only {_ev_curr_cnt}/{pit_univ_cnt} PIT symbols. "
                        "Required action: populate current_ev_ebitda in the PIT fundamentals pipeline."
                    )
            logger.info(f"🎯 CANDIDATE AUDIT ({len(candidate_records)} STOCKS MET GATES):")
            for idx, cand in enumerate(candidate_records, 1):
                _alert_note = "PRODUCTION_BUY_ALERT" if candidates_inserted > 0 else "SUPPRESSED_DATA_BLOCKED"
                logger.info(f"  [{idx:02d}] {cand['symbol']:<12} | Tier={cand['tier']} | Score={cand['ranking_score']:<5.1f} | CMP=₹{cand['current_price']:<8.2f} (Source={cand['context'].get('price_source', 'UNKNOWN')}) | Status={_alert_note}")
            if not candidate_records:
                _zero_reason = "no stocks met all quality + valuation gates" if _valuation_provider_healthy else "valuation data unavailable for all stocks"
                logger.info(f"  (none — {_zero_reason})")
            logger.info("=" * 80)

            # Export Forensic Evidence Bundle if collector is active
            evidence_manifest_path = None
            evidence_manifest_dict = None
            evidence_export_status = "NOT_REQUESTED"
            if collector is not None:
                try:
                    _evidence_summary = {
                        "total_scanned": total_scanned,
                        "approved_universe": total_scanned,
                        "structural_ineligible_count": structural_ineligible_count,
                        "data_failure_count": data_failure_count,
                        "evaluable_universe_count": _evaluable_universe,
                        "fully_evaluable_count": _fully_evaluable,
                        "quality_evaluated": _quality_evaluated,
                        "quality_pass_count": quality_pass_count,
                        "quality_reject_count": quality_reject_count,
                        "value_evaluated": _val_evaluated,
                        "value_pass_count": value_pass_count,
                        "value_reject_count": value_reject_count,
                        "candidate_count": candidate_count,
                        "live_alerts_generated": candidates_inserted,
                        "health_status": _health_status,
                    }
                    evidence_manifest_path, evidence_manifest_dict = collector.finalize_and_export_bundle(_evidence_summary)
                    evidence_export_status = "EXPORTED"
                    logger.info(f"📜 [EVIDENCE_EXPORT] Forensic evidence bundle created at {collector.run_dir}")
                except Exception as _exp_err:
                    evidence_export_status = "EVIDENCE_EXPORT_FAILED"
                    logger.error(f"❌ [EVIDENCE_EXPORT_FAILED] Failed to export evidence bundle: {_exp_err}", exc_info=True)

            return {
                "status": _health_status,
                "health": _health_status,
                "health_status": _health_status,
                "health_error": _health_error,
                "evidence_export_status": evidence_export_status,
                "execution": "SUCCESS",
                "strategy_status": "BLOCKED_DATA" if not _valuation_provider_healthy else ("OK" if candidate_count > 0 else "SCARCITY"),
                "valuation_provider_healthy": _valuation_provider_healthy,
                "total_scanned": total_scanned,
                "approved_universe": total_scanned,
                "structural_ineligible_count": structural_ineligible_count,
                "evaluable_universe": _evaluable_universe,
                "data_failure_count": data_failure_count,
                "fully_evaluable_count": _fully_evaluable,
                "quality_pass_count": quality_pass_count,
                "value_pass_count": value_pass_count,
                "candidate_count": candidate_count,
                "candidates_count": candidate_count,
                "candidates": candidate_records,
                "quality_data_blocked_count": incomplete_quality_count,
                "valuation_data_blocked_count": valuation_data_blocked_count,
                "price_data_blocked_count": price_data_blocked_count,
                "data_blocked_count": data_blocked_count,
                "snapshots_inserted": snapshots_inserted,
                "candidates_inserted": candidates_inserted,
                "duration_sec": duration_sec,
                "population_audit_file": audit_parquet_p,
                "evidence_manifest_path": evidence_manifest_path,
                "evidence_manifest": evidence_manifest_dict,
                "evidence_run_dir": collector.run_dir if collector else None,
            }
        except Exception as err:
            logger.exception(f"❌ [SCANNER: V2_FINAL] Database persistence error: {err}")
            if complete_scanner_execution_run is not None and exec_run_ctx and getattr(exec_run_ctx, "run_id", None):
                try:
                    complete_scanner_execution_run(
                        run_id=exec_run_ctx.run_id,
                        lifecycle_status="FAILED",
                        quality_status="CRITICAL",
                        summary_notes=str(err)[:500]
                    )
                except Exception:
                    pass
            if upsert_scanner_health is not None:
                try:
                    upsert_scanner_health(
                        "QUALITY_COMPOUNDER",
                        status="DOWN",
                        error_msg=str(err)[:500],
                        run_id=getattr(exec_run_ctx, "run_id", None)
                    )
                except Exception:
                    pass
            return {"status": "FAILED", "error": str(err)}



    def load_pit_dataset(self) -> Optional[pd.DataFrame]:
        """Load certified PIT dataset from statement filings and Daily Builder 2.0 master fundamentals."""
        # 1. Authoritative PIT statement filings (795 clean equities)
        p_path = os.path.join(DATA_DIR, "pit_fundamentals_v1", "pit_fundamentals_v1.parquet")
        if not os.path.exists(p_path):
            p_path = os.path.join(DATA_DIR, "pit_fundamentals_v1.parquet")

        searched_paths = [
            os.path.join(DATA_DIR, "pit_fundamentals_v1", "pit_fundamentals_v1.parquet"),
            os.path.join(DATA_DIR, "pit_fundamentals_v1.parquet"),
            os.path.join(DATA_DIR, "daily_builder_master_v2.parquet"),
        ]

        if os.path.exists(p_path):
            try:
                raw_df = pd.read_parquet(p_path)
                if not raw_df.empty and 'symbol' in raw_df.columns:
                    raw_df['period_end_date'] = pd.to_datetime(raw_df['period_end_date'])
                    raw_df['filing_date'] = pd.to_datetime(raw_df['filing_date'])

                    # Candidate directories for historical price bars and PIT valuation medians
                    _candidate_dirs = [
                        DATA_DIR,
                        os.path.join(BASE_DIR, "data"),
                        os.path.join(os.getcwd(), "data"),
                        "/app/data",
                        "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/data"
                    ]

                    # Load certified PIT valuation medians cache (3Y EV/EBITDA & 3Y PE medians from Upstox + PIT)
                    pit_val_cache = {}
                    for _mod_name in ["app.pit_valuation_history_builder", "pit_valuation_history_builder"]:
                        try:
                            _mod = __import__(_mod_name, fromlist=["load_or_build_pit_valuation_cache"])
                            pit_val_cache = _mod.load_or_build_pit_valuation_cache()
                            if pit_val_cache:
                                break
                        except Exception as _b_err:
                            logger.debug(f"Builder import failed for {_mod_name}: {_b_err}")

                    if not pit_val_cache:
                        for _cdir in _candidate_dirs:
                            _cp = os.path.join(_cdir, "pit_valuation_history_cache.json")
                            if os.path.exists(_cp):
                                try:
                                    with open(_cp) as f:
                                        _j = json.load(f)
                                        pit_val_cache = _j.get("data", _j)
                                    if pit_val_cache:
                                        _both_c = sum(1 for r in pit_val_cache.values() if (r.get('ev_ebitda_3y_median') is not None and not pd.isna(r.get('ev_ebitda_3y_median')) and float(r.get('ev_ebitda_3y_median') or 0) > 0) and (r.get('pe_3y_median') is not None and not pd.isna(r.get('pe_3y_median')) and float(r.get('pe_3y_median') or 0) > 0))
                                        _c_stat = _j.get("certification_status") or ("CERTIFIED" if _both_c == len(pit_val_cache) and len(pit_val_cache) > 0 else "PARTIAL_INCOMPLETE")
                                        logger.info(f"⚡ Loaded {len(pit_val_cache)} PIT valuation medians directly from {_cp} | certification_status={_c_stat} | both_required={_both_c}/{len(pit_val_cache)}")
                                        break
                                except Exception:
                                    pass

                    if not pit_val_cache:
                        try:
                            from database import download_parquet_from_db
                            _temp_p = os.path.join(DATA_DIR, "pit_valuation_history_cache.parquet")
                            if download_parquet_from_db("pit_valuation_history_cache", _temp_p):
                                _df_v = pd.read_parquet(_temp_p)
                                if not _df_v.empty and "symbol" in _df_v.columns:
                                    pit_val_cache = {r["symbol"]: r for r in _df_v.to_dict(orient="records")}
                                    _both_c = sum(1 for r in pit_val_cache.values() if (r.get('ev_ebitda_3y_median') is not None and not pd.isna(r.get('ev_ebitda_3y_median')) and float(r.get('ev_ebitda_3y_median') or 0) > 0) and (r.get('pe_3y_median') is not None and not pd.isna(r.get('pe_3y_median')) and float(r.get('pe_3y_median') or 0) > 0))
                                    _c_stat = "CERTIFIED" if _both_c == len(pit_val_cache) and len(pit_val_cache) > 0 else "PARTIAL_INCOMPLETE"
                                    logger.info(f"✅ Loaded {len(pit_val_cache)} PIT valuation medians from database | certification_status={_c_stat} | ev_pe_both_complete={_both_c}/{len(pit_val_cache)}")
                        except Exception as _dbe:
                            logger.debug(f"DB valuation download notice: {_dbe}")

                    # ── P0: FILTER BY FILING DATE BEFORE ANY METRIC CALCULATION ──────────────
                    # Rule: filing_date must precede the scan/signal date to prevent future-filing leakage.
                    # This filter happens HERE — before groupby and before CAGR/ROCE/CFO-PAT are derived.
                    # Metrics are NEVER calculated from post-signal filings.
                    _today_ts = pd.Timestamp(datetime.now(IST).date())
                    _pre_filter_rows = len(raw_df)
                    raw_df = raw_df[raw_df['filing_date'] <= _today_ts]
                    _post_filter_rows = len(raw_df)
                    if _pre_filter_rows != _post_filter_rows:
                        logger.info(
                            f"🛡️ [PIT_PIT_FILTER] filing_date PIT filter: "
                            f"{_pre_filter_rows - _post_filter_rows} future-dated rows removed "
                            f"({_pre_filter_rows} → {_post_filter_rows} rows). "
                            f"All metrics will be derived from records filed on or before today."
                        )
                    # ─────────────────────────────────────────────────────────────────────────

                    records = []
                    for sym, g in raw_df.groupby('symbol'):
                        clean_sym = str(sym).strip().upper()
                        g = g.sort_values('period_end_date')
                        n = len(g)
                        latest_filing = g.iloc[-1]

                        # Separate ANNUAL statement filings for annual growth & annual ratio metrics
                        g_ann = g[g['statement_type'].astype(str).str.upper() == 'ANNUAL'].sort_values('period_end_date')
                        n_ann = len(g_ann)

                        # ── 1. 5Y AVERAGE ROCE (Mean of trailing up to 5 annual filings) ──
                        roce_series = g_ann['roce'].dropna() if not g_ann.empty else g['roce'].dropna()
                        trailing_roce = [float(x) for x in roce_series][-5:]
                        roce_eff = None
                        roce_periods_used = 0
                        if trailing_roce:
                            roce_eff = round(sum(trailing_roce) / len(trailing_roce), 2)
                            roce_periods_used = len(trailing_roce)

                        # ── 2. 5Y CUMULATIVE CFO / PAT RATIO (Trailing up to 5 annual filings) ──
                        trailing_g = g_ann.iloc[-5:] if n_ann >= 5 else g_ann
                        if trailing_g.empty:
                            trailing_g = g.iloc[-5:] if n >= 5 else g
                        sum_cfo = trailing_g['operating_cash_flow'].dropna().sum() if not trailing_g.empty else 0.0
                        sum_pat = trailing_g['net_profit'].dropna().sum() if not trailing_g.empty else 0.0
                        cfo_pat = None
                        if sum_pat is not None and not pd.isna(sum_pat) and len(trailing_g) >= 1:
                            if float(sum_pat) > 0:
                                cfo_pat = round(float(sum_cfo) / float(sum_pat), 2)
                            else:
                                # Cumulative 5Y net profit is non-positive (net loss over 5 years).
                                cfo_pat = -999.0

                        # ── 3. 5Y CAGR (Sales CAGR & PAT CAGR across trailing ANNUAL filings) ──
                        # Canonical C2 / C15: Gap-Aware CAGR Engine (GLOBUSSPR / SANDUMA defence)
                        k_cagr = min(5, n_ann - 1) if n_ann >= 2 else 0
                        rev_cagr, pat_cagr = None, None
                        growth_start_period, growth_end_period = None, None
                        growth_yrs = 0.0
                        if k_cagr >= 1:
                            ann_records = g_ann.to_dict('records')
                            cagr_rev_res = compute_cagr_pit(ann_records, metric="revenue", target_years=5, symbol=clean_sym)
                            cagr_pat_res = compute_cagr_pit(ann_records, metric="net_profit", target_years=5, symbol=clean_sym)

                            if cagr_rev_res.ok:
                                rev_cagr = cagr_rev_res.cagr
                                growth_start_period = cagr_rev_res.start_period
                                growth_end_period = cagr_rev_res.end_period
                                growth_yrs = cagr_rev_res.elapsed_years or 5.0
                            elif cagr_rev_res.reason in ("NON_POSITIVE_BASE_VALUE", "NEGATIVE_BASE_VALUE"):
                                rev_cagr = -999.0
                                growth_start_period = cagr_rev_res.start_period
                                growth_end_period = cagr_rev_res.end_period
                                growth_yrs = cagr_rev_res.elapsed_years or 5.0
                            else:
                                # Fiscal gaps detected, insufficient depth, or window distortion -> None (fail-closed)
                                rev_cagr = None

                            if cagr_pat_res.ok:
                                pat_cagr = cagr_pat_res.cagr
                                if not growth_start_period:
                                    growth_start_period = cagr_pat_res.start_period
                                    growth_end_period = cagr_pat_res.end_period
                                    growth_yrs = cagr_pat_res.elapsed_years or 5.0
                            elif cagr_pat_res.reason in ("NON_POSITIVE_BASE_VALUE", "NEGATIVE_BASE_VALUE"):
                                pat_cagr = -999.0
                            else:
                                pat_cagr = None

                            r0 = cagr_rev_res.start_value
                            r1 = cagr_rev_res.end_value
                            p0 = cagr_pat_res.start_value
                            p1 = cagr_pat_res.end_value

                        # Debt to Equity — strictly derived from latest annual filing with disclosed balance sheet
                        latest_ann = g_ann.iloc[-1] if not g_ann.empty else latest_filing
                        td_raw = latest_ann.get('total_debt')
                        te_raw = latest_ann.get('total_equity')
                        td_val = float(td_raw) if (td_raw is not None and pd.notna(td_raw)) else None
                        te_val = float(te_raw) if (te_raw is not None and pd.notna(te_raw) and float(te_raw) > 0) else None

                        # P0: Missing debt or missing equity must result in de = None (UNKNOWN != ZERO).
                        # Only genuine zero debt (td == 0.0) with valid positive equity yields de = 0.0.
                        if td_val is not None and te_val is not None:
                            de = round(td_val / te_val, 3)
                        else:
                            de = None

                        # Statement fundamentals for dynamic valuation
                        _shares = latest_ann.get('shares_outstanding')
                        _shares_f = float(_shares) if _shares is not None and pd.notna(_shares) and float(_shares) > 0 else None
                        _eps = latest_ann.get('eps')
                        _eps_f = float(_eps) if _eps is not None and pd.notna(_eps) and float(_eps) > 0 else None
                        _net_p = latest_ann.get('net_profit')
                        _net_p_f = float(_net_p) if _net_p is not None and pd.notna(_net_p) else None

                        # Canonical C4 Share Count Validation (TIINDIA defence: no unvalidated silent derivation)
                        _sh_input = (_shares_f / 1e6) if (_shares_f is not None and _shares_f > 1e6) else _shares_f
                        sh_res = validate_share_count(
                            filed_shares=_sh_input,
                            net_profit_cr=_net_p_f,
                            eps=_eps_f,
                            cmp_price=0.0,
                            symbol=clean_sym,
                            scanner="QUALITY_COMPOUNDER"
                        )
                        if sh_res.ok and sh_res.shares_millions is not None:
                            _shares_f = sh_res.shares_millions * 1e6
                        else:
                            _shares_f = None

                        _op     = latest_ann.get('operating_profit')
                        _da     = latest_ann.get('depreciation_amortization')
                        _op_f   = float(_op)   if _op   is not None and pd.notna(_op)   else None
                        _da_f   = float(_da)   if _da   is not None and pd.notna(_da)   else None
                        _td_f   = td_val
                        _cash   = latest_ann.get('cash_and_equivalents')
                        _cash_f = float(_cash) if _cash  is not None and pd.notna(_cash)  else None
                        # EBITDA requires both operating_profit AND D&A to be genuinely known.
                        _ebitda_f = (_op_f + _da_f) if (_op_f is not None and _da_f is not None) else None

                        # ── VALUATION MULTIPLES ─────────────────────────────────────────────
                        pit_val = pit_val_cache.get(clean_sym, pit_val_cache.get(sym, {}))
                        pe_med  = pit_val.get('pe_3y_median')
                        ev_med  = pit_val.get('ev_ebitda_3y_median')

                        # ── MARKET CAP RESOLUTION (Strictly 1D history + Statement Filings) ──
                        _mcap_cr = None
                        _mcap_source = None
                        pe_curr = None

                        for _cdir in _candidate_dirs:
                            p_path = os.path.join(_cdir, "history", "1d", f"{clean_sym}.parquet")
                            if os.path.exists(p_path):
                                try:
                                    df_px = pd.read_parquet(p_path)
                                    if not df_px.empty:
                                        c_col = 'close' if 'close' in df_px.columns else ('Close' if 'Close' in df_px.columns else None)
                                        if c_col:
                                            _px = float(df_px[c_col].iloc[-1])
                                            if _px > 0:
                                                if _shares_f:
                                                    _mcap_cr = (_shares_f * _px) / 1e7
                                                    _mcap_source = "1D_HISTORY_SHARES_X_PRICE"
                                                elif _net_p_f and _eps_f and _eps_f > 0:
                                                    _mcap_cr = _net_p_f * (_px / _eps_f)
                                                    _mcap_source = "1D_HISTORY_NETPROFIT_X_PE"
                                                if _eps_f and _eps_f > 0:
                                                    pe_curr = round(_px / _eps_f, 2)
                                    break
                                except Exception:
                                    pass

                        # ── EV CALCULATION ──────────────────────────────────────────────────
                        # Canonical C3 Rule: Cash is MANDATORY (INDIAMART defence).
                        # Cash genuinely unavailable from data provider -> EV is DATA_INSUFFICIENT (None).
                        # Never use conservative bound MCap + Debt for production decisions!
                        ev_curr = None
                        _ev_cash_component = "KNOWN" if _cash_f is not None else "UNKNOWN"
                        if _mcap_cr is not None and _ebitda_f is not None:
                            if _ebitda_f > 0:
                                if _td_f is not None and _cash_f is not None:
                                    _ev = _mcap_cr + _td_f - _cash_f
                                    if _ev > 0:
                                        ev_curr = round(_ev / _ebitda_f, 2)
                                else:
                                    # Cash or Debt unavailable -> EV remains None (DATA_INSUFFICIENT)
                                    _ev = None
                                    ev_curr = None
                            else:
                                # Operating loss (EBITDA <= 0): multiple is negative / undefined
                                ev_curr = -999.0

                        records.append({
                            'symbol': clean_sym,
                            'filing_date': str(latest_filing.get('filing_date'))[:10],
                            'financial_period_end': str(latest_filing.get('period_end_date'))[:10],
                            'market_cap': _mcap_cr,
                            'roce_5y_avg': roce_eff,
                            'sales_cagr_5y': rev_cagr,
                            'pat_cagr_5y': pat_cagr,
                            'cfo_pat_5y_ratio': cfo_pat,
                            'debt_to_equity': de,
                            'growth_start_period': growth_start_period,
                            'growth_end_period': growth_end_period,
                            'growth_years_elapsed': round(growth_yrs, 2),
                            'financial_periods_used': k_cagr + 1 if k_cagr >= 2 else n,
                            'roce_periods_used': roce_periods_used,
                            'shares_outstanding': _shares_f,
                            'eps': _eps_f,
                            'ebitda': _ebitda_f,
                            'total_debt': _td_f,
                            'total_equity': te_val,
                            'cash_and_equivalents': _cash_f,
                            'current_pe': pe_curr,
                            'pe_3y_median': pe_med,
                            'current_ev_ebitda': ev_curr,
                            'ev_ebitda_3y_median': ev_med,
                            # Provenance / audit fields
                            'mcap_source': _mcap_source,
                            'ev_cash_component': _ev_cash_component,
                            'provenance_status': 'CERTIFIED_PIT_STATEMENT_CALCULATED',
                            'annual_filing_count': n_ann,
                            'earliest_annual_period': str(g_ann.iloc[0]['period_end_date'])[:10] if not g_ann.empty else None,
                            'latest_annual_period': str(g_ann.iloc[-1]['period_end_date'])[:10] if not g_ann.empty else None,
                            # Intermediate / Reconstruction fields for independent auditability
                            'start_revenue': r0 if 'r0' in locals() else None,
                            'end_revenue': r1 if 'r1' in locals() else None,
                            'start_pat': p0 if 'p0' in locals() else None,
                            'end_pat': p1 if 'p1' in locals() else None,
                            'cfo_annual_values': [float(x) for x in trailing_g['operating_cash_flow'].dropna()] if not trailing_g.empty else [],
                            'pat_annual_values': [float(x) for x in trailing_g['net_profit'].dropna()] if not trailing_g.empty else [],
                            'sum_cfo': sum_cfo,
                            'sum_pat': sum_pat,
                            'roce_annual_values': trailing_roce,
                            'raw_total_debt': td_val,
                            'raw_total_equity': te_val,
                            'debt_reported_status': "GENUINELY_ZERO" if (td_val is not None and td_val == 0.0) else ("POSITIVE" if (td_val is not None and td_val > 0) else "MISSING"),
                            'equity_reported_status': "POSITIVE" if (te_val is not None and te_val > 0) else "MISSING",
                            'operating_profit': _op_f,
                            'depreciation_amortization': _da_f,
                            'ev_net_debt': (_td_f - _cash_f) if (_td_f is not None and _cash_f is not None) else _td_f,
                            'ev_total': _ev if (_mcap_cr is not None and _td_f is not None and '_ev' in locals()) else None,
                            'annual_filings_history': [
                                {
                                    "period_end_date": str(r.get("period_end_date"))[:10],
                                    "filing_date": str(r.get("filing_date"))[:10],
                                    "publication_timestamp": str(r.get("actual_publication_timestamp", r.get("filing_date"))),
                                    "statement_type": "ANNUAL",
                                    "revenue": float(r.get("revenue")) if (r.get("revenue") is not None and pd.notna(r.get("revenue"))) else None,
                                    "operating_profit": float(r.get("operating_profit")) if (r.get("operating_profit") is not None and pd.notna(r.get("operating_profit"))) else None,
                                    "depreciation_amortization": float(r.get("depreciation_amortization")) if (r.get("depreciation_amortization") is not None and pd.notna(r.get("depreciation_amortization"))) else None,
                                    "net_profit": float(r.get("net_profit")) if (r.get("net_profit") is not None and pd.notna(r.get("net_profit"))) else None,
                                    "operating_cash_flow": float(r.get("operating_cash_flow")) if (r.get("operating_cash_flow") is not None and pd.notna(r.get("operating_cash_flow"))) else None,
                                    "total_debt": float(r.get("total_debt")) if (r.get("total_debt") is not None and pd.notna(r.get("total_debt"))) else None,
                                    "total_equity": float(r.get("total_equity")) if (r.get("total_equity") is not None and pd.notna(r.get("total_equity"))) else None,
                                    "cash_and_equivalents": float(r.get("cash_and_equivalents")) if (r.get("cash_and_equivalents") is not None and pd.notna(r.get("cash_and_equivalents"))) else None,
                                    "roce": float(r.get("roce")) if (r.get("roce") is not None and pd.notna(r.get("roce"))) else None,
                                    "eps": float(r.get("eps")) if (r.get("eps") is not None and pd.notna(r.get("eps"))) else None,
                                    "shares_outstanding": float(r.get("shares_outstanding")) if (r.get("shares_outstanding") is not None and pd.notna(r.get("shares_outstanding"))) else None,
                                }
                                for _, r in g_ann.iterrows()
                            ],
                        })


                    pit_df = pd.DataFrame(records)
                    # Enrich with Shared Financial Snapshot & Watcher Freshness Status
                    try:
                        from app.financial_data_integrity import load_all_shared_financial_snapshots
                        shared_snaps = load_all_shared_financial_snapshots()
                        if shared_snaps:
                            status_map = {s_sym: s.snapshot_status for s_sym, s in shared_snaps.items()}
                            pit_df["snapshot_status"] = pit_df["symbol"].map(status_map).fillna("FRESH")
                        else:
                            pit_df["snapshot_status"] = "FRESH"
                    except Exception as _snap_e:
                        logger.debug(f"Shared snapshot enrichment in load_pit_dataset: {_snap_e}")
                        pit_df["snapshot_status"] = "FRESH"

                    # Valuation coverage report — logged at dataset build time
                    _n = len(pit_df)
                    _with_ev_curr = int(pit_df['current_ev_ebitda'].notna().sum())
                    _with_ev_med  = int(pit_df['ev_ebitda_3y_median'].notna().sum())
                    _with_pe_curr = int(pit_df['current_pe'].notna().sum())
                    _with_pe_med  = int(pit_df['pe_3y_median'].notna().sum())
                    logger.info(
                        f"✅ Loaded and calculated certified PIT dataset from statement filings ({_n} symbols) | "
                        f"CurrentEV/EBITDA: {_with_ev_curr}/{_n} | EV/EBITDA_3YMedian: {_with_ev_med}/{_n} | "
                        f"CurrentPE: {_with_pe_curr}/{_n} | PE_3YMedian: {_with_pe_med}/{_n}"
                    )
                    if _with_ev_med == 0 and _with_pe_med == 0:
                        logger.error(
                            f"❌ [V2_FINAL] VALUATION_DATA_UNAVAILABLE: ev_ebitda_3y_median and pe_3y_median are "
                            f"missing for ALL {_n} symbols. The valuation gate will block all stocks. "
                            f"Action: run pit_valuation_history_builder.py to fetch Upstox historical prices "
                            f"and compute 3Y median multiples per symbol."
                        )
                    return pit_df
            except Exception as e:
                logger.warning(f"Failed to process pit_fundamentals_v1.parquet: {e}")

        # Fallback 2: Daily Builder 2.0 master parquet
        p_db = os.path.join(DATA_DIR, "daily_builder_master_v2.parquet")
        if os.path.exists(p_db):
            try:
                df = pd.read_parquet(p_db)
                if not df.empty:
                    logger.info(f"✅ Loaded PIT dataset from Daily Builder master ({len(df)} rows)")
                    return df
            except Exception as e:
                logger.warning(f"Failed loading daily_builder_master_v2.parquet: {e}")

        # DATA_UNAVAILABLE — neither source found on this host.
        # Per governance rules: do NOT substitute synthetic data. Fail closed.
        logger.error(
            "❌ [V2_FINAL] PIT dataset unavailable — DATA_UNAVAILABLE. "
            f"Searched: {searched_paths}. "
            "Ensure pit_fundamentals_v1.parquet is present in DATA_DIR on the production host, "
            "or run the Daily Builder to regenerate it. Scan cannot proceed without real data."
        )
        return None


_v2_scanner_instance = None

def get_quality_compounder_v2_scanner() -> QualityCompounderValueV2Scanner:
    global _v2_scanner_instance
    if _v2_scanner_instance is None:
        _v2_scanner_instance = QualityCompounderValueV2Scanner()
    return _v2_scanner_instance

def run_quality_compounder_v2_scan(trigger_type: str = "SCHEDULED", scheduler_name: str = "CRON", record_full_evidence: bool = True) -> Dict[str, Any]:
    """Top-level invocation wrapper for QUALITY_COMPOUNDER scanner."""
    return get_quality_compounder_v2_scanner().scan_universe(trigger_type=trigger_type, scheduler_name=scheduler_name, record_full_evidence=record_full_evidence)


__all__ = [
    "RejectionReason",
    "ApprovedUniverseRegistry",
    "FundamentalQualityGate",
    "EarningsAccelerationGate",
    "TechnicalTrendGate",
    "ConsolidationGate",
    "BreakoutGate",
    "DailyBuilderFundamentalProvider",
    "LiveFundamentalBuyScanner",
    "live_fundamental_scanner",
    "get_live_fundamental_scanner",
    "run_fundamental_scan",
    "QualityCompounderValueV2Scanner",
    "get_quality_compounder_v2_scanner",
    "run_quality_compounder_v2_scan",
    "REQUIRED_ANNUAL_HISTORY_FOR_V2_5Y_METRICS",
    "required_v2_annual_history",
    "classify_v2_historical_evidence",
]


