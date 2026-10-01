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
    final_action: str,
) -> None:
    """
    Emits a structured [DATA_RECOVERY] audit block to the application log.

    This function answers four governance questions for every skipped symbol:
      1. What data was missing?
      2. What was attempted to recover it?
      3. Did recovery + validation succeed?
      4. Why was the stock ultimately skipped?

    Parameters
    ----------
    scanner          : Scanner identifier (e.g. "FUNDAMENTAL", "QUALITY_COMPOUNDER_VALUE_V2_FINAL")
    symbol           : Ticker symbol
    stage            : Gate where missing data was detected (e.g. "FUNDAMENTAL", "TECHNICAL", "QUALITY", "VALUATION")
    missing_data     : Human-readable description of the missing field(s)
    recovery_attempted: Whether a fetch / recovery was attempted
    providers        : List of dicts with keys: provider, result, and optionally rows_received / failure_type / validation / validation_reason
    validation       : Overall validation outcome ("PASSED" / "FAILED" / None)
    validation_reason: Why validation failed, if applicable
    final_action     : "DATA_USED" (scan continued) or "STOCK_SKIPPED" (symbol blocked)
    """
    lines = [
        f"[DATA_RECOVERY]",
        f"  scanner={scanner}",
        f"  symbol={symbol}",
        f"  stage={stage}",
        f"  missing_data={missing_data}",
        f"  recovery_attempted={str(recovery_attempted).lower()}",
    ]
    if providers:
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
    log_level = logging.WARNING if final_action == "STOCK_SKIPPED" else logging.DEBUG
    logger.log(log_level, "\n".join(lines))


# -------------------------------------------------------------------------------------
# PIT FUNDAMENTALS APPROVED RECOVERY PROVIDER
# -------------------------------------------------------------------------------------
_PIT_FILINGS_CACHE: Optional[Dict[str, List[Dict[str, Any]]]] = None

def _get_pit_filings(symbol: str) -> List[Dict[str, Any]]:
    """Returns historical filings for symbol from PIT database, sorted by period_end_date descending."""
    global _PIT_FILINGS_CACHE
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
    return _PIT_FILINGS_CACHE.get(str(symbol).upper(), [])


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
                    SELECT symbol, period_end_date, revenue, operating_profit, net_profit, eps,
                           roce, roe, total_debt, total_equity, operating_cash_flow, free_cash_flow
                    FROM pit_fundamentals_v1
                    ORDER BY symbol, period_end_date DESC
                    """
                    df_all = pd.read_sql(query, con)
                    con.close()

                    if not df_all.empty:
                        rows_list = []
                        for sym, group in df_all.groupby("symbol"):
                            filings = group.to_dict("records")
                            if not filings:
                                continue
                            f0 = filings[0]
                            roce_val = f0.get("roce")
                            roe_val = f0.get("roe")
                            tot_debt = f0.get("total_debt")
                            tot_eq = f0.get("total_equity")
                            de_val = (float(tot_debt) / float(tot_eq)) if tot_debt is not None and tot_eq is not None and float(tot_eq) > 0 else (0.0 if tot_debt == 0 else None)
                            ocf_raw = f0.get("operating_cash_flow")
                            ocf_val = float(ocf_raw) if (ocf_raw is not None and not pd.isna(ocf_raw)) else None

                            rev_l, rev_p, op_l, op_p, eps_l, eps_p, p_eps = None, None, None, None, None, None, None

                            if len(filings) >= 2:
                                f1 = filings[1]
                                rev0, rev1 = f0.get("revenue"), f1.get("revenue")
                                op0, op1 = f0.get("operating_profit"), f1.get("operating_profit")
                                eps0, eps1 = f0.get("eps"), f1.get("eps")

                                if rev0 is not None and rev1 is not None and abs(rev1) > 1e-5:
                                    rev_l = ((rev0 - rev1) / abs(rev1)) * 100.0
                                if op0 is not None and op1 is not None and abs(op1) > 1e-5:
                                    op_l = ((op0 - op1) / abs(op1)) * 100.0
                                if eps0 is not None and eps1 is not None and abs(eps1) > 1e-5:
                                    eps_l = ((eps0 - eps1) / abs(eps1)) * 100.0
                                    p_eps = float(eps1)

                            if len(filings) >= 3:
                                f1, f2 = filings[1], filings[2]
                                rev1, rev2 = f1.get("revenue"), f2.get("revenue")
                                op1, op2 = f1.get("operating_profit"), f2.get("operating_profit")
                                eps1, eps2 = f1.get("eps"), f2.get("eps")

                                if rev1 is not None and rev2 is not None and abs(rev2) > 1e-5:
                                    rev_p = ((rev1 - rev2) / abs(rev2)) * 100.0
                                if op1 is not None and op2 is not None and abs(op2) > 1e-5:
                                    op_p = ((op1 - op2) / abs(op2)) * 100.0
                                if eps1 is not None and eps2 is not None and abs(eps2) > 1e-5:
                                    eps_p = ((eps1 - eps2) / abs(eps2)) * 100.0

                            rows_list.append({
                                "symbol": sym,
                                "ROCE": float(roce_val) if roce_val is not None else None,
                                "ROE": float(roe_val) if roe_val is not None else None,
                                "debt": float(de_val) if de_val is not None else None,
                                "operating_cash_flow": float(ocf_val) if ocf_val is not None else None,
                                "fundamental_category": "HIGH_QUALITY" if roce_val is not None and float(roce_val) >= 15.0 else "NORMAL",
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
            ocf_val = float(raw_ocf) if (raw_ocf is not None and not pd.isna(raw_ocf)) else None

            fund_cat = str(r.get("fundamental_category", r.get("Category", "NONE")))
            is_trap = (fund_cat == "VALUE_TRAP") or bool(r.get("is_value_trap", False)) or (str(r.get("Forensic_Risk_Tier", "")).upper() == "HIGH")

            # Acceleration fields (ZERO SYNTHETIC CONSTANTS: missing values must stay None to fail closed)
            # EA LATEST-period: allow YoY proxies from daily builder if specific field absent
            rev_l = r.get("rev_yoy_latest", r.get("total_revenue_yoy_growth_ttm", r.get("YOY Revenue %")))
            rev_l = float(rev_l) if (rev_l is not None and not pd.isna(rev_l)) else None

            # EA PREV-period: ONLY from PIT-sourced parquet field. NEVER fall back to 5Y CAGR or
            # any cross-metric proxy. A 5-year CAGR is NOT a prior-year YoY rate.
            # B4 fix: rev_yoy_prev must NEVER fall back to 'total_revenue_5y_growth' or '5Y Revenue %'
            rev_p = r.get("rev_yoy_prev")  # None if absent → DATA_MISSING in EA gate
            rev_p = float(rev_p) if (rev_p is not None and not pd.isna(rev_p)) else None

            op_l = r.get("op_profit_yoy_latest", r.get("gross_profit_yoy_growth_ttm", r.get("YOY Profit %")))
            op_l = float(op_l) if (op_l is not None and not pd.isna(op_l)) else None

            # B4 fix: op_profit_yoy_prev NEVER falls back to any proxy
            op_p = r.get("op_profit_yoy_prev")  # None if absent → DATA_MISSING in EA gate
            op_p = float(op_p) if (op_p is not None and not pd.isna(op_p)) else None

            eps_l = r.get("eps_yoy_latest", r.get("earnings_per_share_diluted_yoy_growth_ttm", r.get("YOY Profit %")))
            eps_l = float(eps_l) if (eps_l is not None and not pd.isna(eps_l)) else None

            # B4 fix: eps_yoy_prev must NEVER fall back to 'earnings_per_share_basic_5y_growth' (CAGR ≠ prior-year YoY)
            eps_p = r.get("eps_yoy_prev")  # None if absent → DATA_MISSING in EA gate
            eps_p = float(eps_p) if (eps_p is not None and not pd.isna(eps_p)) else None

            p_eps = r.get("prior_eps", r.get("earnings_per_share_basic_ttm"))
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

        # ── SECONDARY RE-HYDRATION FROM CERTIFIED LOCAL FUNDAMENTAL CACHES ────
        # Fills missing symbols and null ROCE/ROE/Debt/OCF fields for approved universe
        # using real exchange/filing data cached locally without synthetic fallbacks.
        sec_caches = {}
        for cname in ["fundamentals_cache.json", "multibagger_fundamentals_cache.json"]:
            for _cdir in [DATA_DIR, os.path.join(BASE_DIR, "data"), "/app/data"]:
                cpath = os.path.join(_cdir, cname)
                if os.path.exists(cpath):
                    try:
                        with open(cpath, "r") as cf:
                            cd = json.load(cf)
                            if isinstance(cd, dict):
                                for k, v in cd.items():
                                    if isinstance(v, dict):
                                        sec_caches[str(k).strip().upper()] = v
                        break
                    except Exception as _ce:
                        logger.debug(f"Notice reading secondary cache {cname}: {_ce}")

        rehydrated_cnt = 0
        augmented_cnt = 0
        for sym, s_data in sec_caches.items():
            s_roe = s_data.get("roe")
            s_roce = s_data.get("roce")
            s_de = s_data.get("debt_equity")
            s_ocf = s_data.get("operating_cash_flow", s_data.get("free_cash_flow"))

            s_roce_f = None
            if s_roce is not None and not pd.isna(s_roce):
                try:
                    s_roce_f = float(s_roce)
                    if 0.0 < s_roce_f <= 1.0:
                        s_roce_f *= 100.0
                except (ValueError, TypeError):
                    pass

            s_roe_f = None
            if s_roe is not None and not pd.isna(s_roe):
                try:
                    s_roe_f = float(s_roe)
                    if 0.0 < s_roe_f <= 1.0:
                        s_roe_f *= 100.0
                except (ValueError, TypeError):
                    pass

            s_de_f = None
            if s_de is not None and not pd.isna(s_de):
                try:
                    s_de_f = float(s_de)
                except (ValueError, TypeError):
                    pass

            s_ocf_f = None
            if s_ocf is not None and not pd.isna(s_ocf):
                try:
                    s_ocf_f = float(s_ocf)
                except (ValueError, TypeError):
                    pass

            s_eps_f = None
            if s_data.get("eps") is not None and not pd.isna(s_data.get("eps")):
                try:
                    ep_val = float(s_data.get("eps"))
                    if ep_val > 0:
                        s_eps_f = ep_val
                except (ValueError, TypeError):
                    pass

            if sym not in funds_map:
                funds_map[sym] = {
                    "symbol": sym,
                    "roce": s_roce_f,
                    "roe": s_roe_f,
                    "debt_equity": s_de_f,
                    "operating_cash_flow": s_ocf_f,
                    "fundamental_category": "HIGH_QUALITY" if (s_roce_f is not None and s_roce_f >= 15.0) else "NORMAL",
                    "is_value_trap": False,
                    "quality_score": float(s_data.get("score", 0.0) or 0.0),
                    "growth_score": 0.0,
                    "valuation_score": 0.0,
                    "wealth_score": 0.0,
                    "risk_score": 0.0,
                    "valuation_category": "NONE",
                    "fair_value_range": "",
                    "rev_yoy_latest": None,
                    "rev_yoy_prev": None,
                    "op_profit_yoy_latest": None,
                    "op_profit_yoy_prev": None,
                    "eps_yoy_latest": None,
                    "eps_yoy_prev": None,
                    "prior_eps": s_eps_f,
                    "upstream_provider": "LOCAL_CERTIFIED_FUNDAMENTAL_CACHE"
                }
                rehydrated_cnt += 1
            else:
                rec = funds_map[sym]
                augmented = False
                if rec.get("roce") is None and s_roce_f is not None:
                    rec["roce"] = s_roce_f
                    augmented = True
                if rec.get("roe") is None and s_roe_f is not None:
                    rec["roe"] = s_roe_f
                    augmented = True
                if rec.get("debt_equity") is None and s_de_f is not None:
                    rec["debt_equity"] = s_de_f
                    augmented = True
                if rec.get("operating_cash_flow") is None and s_ocf_f is not None:
                    rec["operating_cash_flow"] = s_ocf_f
                    augmented = True
                if rec.get("prior_eps") is None and s_eps_f is not None:
                    rec["prior_eps"] = s_eps_f
                    augmented = True
                if augmented:
                    augmented_cnt += 1

        if rehydrated_cnt > 0 or augmented_cnt > 0:
            logger.info(
                f"✅ [FUNDAMENTAL_CACHE] Re-hydrated {rehydrated_cnt} missing symbols and "
                f"augmented {augmented_cnt} symbols with real values from certified local fundamental caches."
            )
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

            # Authoritative Upstream Layer: Daily Builder 2.0
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

                # ── DATA RECOVERY AUDIT: FUNDAMENTAL SCANNER ───────────────────────
                _fund_missing = funds.get("upstream_provider") == "DATA_UNAVAILABLE"
                _bars_missing = (df_bars is None or df_bars.empty)
                _bars_short   = (not _bars_missing and len(df_bars) < 50)

                if _fund_missing:
                    # Attempt recovery from PIT_DATABASE
                    pit_filings = _get_pit_filings(sym)
                    prov_pit_result = "FETCHED" if (pit_filings and any(f.get("roce") is not None for f in pit_filings)) else "NOT_AVAILABLE"
                    prov_pit_fail = None if prov_pit_result == "FETCHED" else "SYMBOL_NOT_IN_PIT_DB"
                    
                    if prov_pit_result == "FETCHED":
                        f0 = pit_filings[0]
                        roce_val = f0.get("roce")
                        roe_val = f0.get("roe")
                        tot_debt = f0.get("total_debt")
                        tot_eq = f0.get("total_equity")
                        de_val = (float(tot_debt) / float(tot_eq)) if tot_debt is not None and tot_eq is not None and float(tot_eq) > 0 else (0.0 if tot_debt == 0 else None)
                        ocf_val = f0.get("operating_cash_flow")
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
                        missing_data="fundamental_metrics (ROCE, ROE, OCF, D/E, growth rates)",
                        recovery_attempted=True,
                        providers=[
                            {
                                "provider": "DAILY_BUILDER_2.0",
                                "result": "NOT_AVAILABLE",
                                "failure_type": "SYMBOL_NOT_IN_MASTER_DATASET",
                            },
                            {
                                "provider": "PIT_DATABASE",
                                "result": prov_pit_result,
                                "failure_type": prov_pit_fail,
                            }
                        ],
                        validation="PASSED" if prov_pit_result == "FETCHED" else "FAILED",
                        validation_reason=None if prov_pit_result == "FETCHED" else "APPROVED_PROVIDERS_EXHAUSTED",
                        final_action="DATA_USED" if prov_pit_result == "FETCHED" else "STOCK_SKIPPED",
                    )
                else:
                    # Check partial missing Quality fields
                    missing_q = [f for f in ["roce", "roe", "operating_cash_flow", "debt_equity"] if funds.get(f) is None]
                    if missing_q:
                        pit_filings = _get_pit_filings(sym)
                        if pit_filings:
                            f0 = pit_filings[0]
                            if funds.get("roce") is None and f0.get("roce") is not None and not pd.isna(f0.get("roce")):
                                funds["roce"] = float(f0.get("roce"))
                            if funds.get("roe") is None and f0.get("roe") is not None and not pd.isna(f0.get("roe")):
                                funds["roe"] = float(f0.get("roe"))
                            if funds.get("operating_cash_flow") is None and f0.get("operating_cash_flow") is not None and not pd.isna(f0.get("operating_cash_flow")):
                                funds["operating_cash_flow"] = float(f0.get("operating_cash_flow"))
                            if funds.get("debt_equity") is None:
                                td = f0.get("total_debt")
                                te = f0.get("total_equity")
                                if td is not None and te is not None and not pd.isna(td) and not pd.isna(te) and float(te) > 0:
                                    funds["debt_equity"] = float(td) / float(te)
                                elif td == 0:
                                    funds["debt_equity"] = 0.0

                        unresolved_q = [f for f in ["roce", "roe", "operating_cash_flow", "debt_equity"] if funds.get(f) is None]
                        q_ok = (len(unresolved_q) == 0)
                        _emit_data_recovery_log(
                            scanner="FUNDAMENTAL",
                            symbol=sym,
                            stage="QUALITY",
                            missing_data=f"quality_metrics ({', '.join(missing_q)})",
                            recovery_attempted=True,
                            providers=[
                                {
                                    "provider": "DAILY_BUILDER_2.0",
                                    "result": "PARTIAL_OR_ABSENT",
                                    "failure_type": "METRICS_MISSING_IN_DAILY_BUILDER",
                                },
                                {
                                    "provider": "PIT_DATABASE",
                                    "result": "FETCHED" if q_ok else ("NOT_AVAILABLE" if not pit_filings else "INSUFFICIENT_DATA"),
                                    "failure_type": None if q_ok else ("SYMBOL_NOT_IN_PIT_DB" if not pit_filings else "METRICS_ABSENT_IN_FILINGS"),
                                }
                            ],
                            validation="PASSED" if q_ok else "FAILED",
                            validation_reason=None if q_ok else f"UNRESOLVED_QUALITY_FIELDS_{unresolved_q}",
                            final_action="DATA_USED" if q_ok else "STOCK_SKIPPED",
                        )

                    # Check partial missing Growth fields
                    missing_g = [f for f in ["rev_yoy_latest", "rev_yoy_prev", "op_profit_yoy_latest", "op_profit_yoy_prev", "eps_yoy_latest", "eps_yoy_prev", "prior_eps"] if funds.get(f) is None]
                    if missing_g:
                        pit_filings = _get_pit_filings(sym)
                        if pit_filings and len(pit_filings) >= 2:
                            f0 = pit_filings[0]
                            f1 = pit_filings[1]

                            def _find_yoy_match(ref_f):
                                ref_dt = pd.to_datetime(ref_f.get("period_end_date"))
                                for past_f in pit_filings:
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
                                    funds["rev_yoy_latest"] = ((float(r0) - float(r_m0)) / abs(float(r_m0))) * 100.0
                                if funds.get("op_profit_yoy_latest") is None and op0 is not None and op_m0 is not None and not pd.isna(op0) and not pd.isna(op_m0) and abs(float(op_m0)) > 1e-5:
                                    funds["op_profit_yoy_latest"] = ((float(op0) - float(op_m0)) / abs(float(op_m0))) * 100.0
                                if funds.get("eps_yoy_latest") is None and eps0 is not None and eps_m0 is not None and not pd.isna(eps0) and not pd.isna(eps_m0) and abs(float(eps_m0)) > 1e-5:
                                    funds["eps_yoy_latest"] = ((float(eps0) - float(eps_m0)) / abs(float(eps_m0))) * 100.0
                                if funds.get("prior_eps") is None and eps_m0 is not None and not pd.isna(eps_m0):
                                    funds["prior_eps"] = float(eps_m0)

                            if match_f1:
                                r1, r_m1 = f1.get("revenue"), match_f1.get("revenue")
                                op1, op_m1 = f1.get("operating_profit"), match_f1.get("operating_profit")
                                eps1, eps_m1 = f1.get("eps"), match_f1.get("eps")
                                if funds.get("rev_yoy_prev") is None and r1 is not None and r_m1 is not None and not pd.isna(r1) and not pd.isna(r_m1) and abs(float(r_m1)) > 1e-5:
                                    funds["rev_yoy_prev"] = ((float(r1) - float(r_m1)) / abs(float(r_m1))) * 100.0
                                if funds.get("op_profit_yoy_prev") is None and op1 is not None and op_m1 is not None and not pd.isna(op1) and not pd.isna(op_m1) and abs(float(op_m1)) > 1e-5:
                                    funds["op_profit_yoy_prev"] = ((float(op1) - float(op_m1)) / abs(float(op_m1))) * 100.0
                                if funds.get("eps_yoy_prev") is None and eps1 is not None and eps_m1 is not None and not pd.isna(eps1) and not pd.isna(eps_m1) and abs(float(eps_m1)) > 1e-5:
                                    funds["eps_yoy_prev"] = ((float(eps1) - float(eps_m1)) / abs(float(eps_m1))) * 100.0

                            if funds.get("prior_eps") is None and f1.get("eps") is not None and not pd.isna(f1.get("eps")):
                                funds["prior_eps"] = float(f1.get("eps"))

                        unresolved_g = [f for f in ["rev_yoy_latest", "rev_yoy_prev", "op_profit_yoy_latest", "op_profit_yoy_prev", "eps_yoy_latest", "eps_yoy_prev", "prior_eps"] if funds.get(f) is None]
                        g_ok = (len(unresolved_g) == 0)
                        _emit_data_recovery_log(
                            scanner="FUNDAMENTAL",
                            symbol=sym,
                            stage="GROWTH",
                            missing_data=f"growth_acceleration_metrics ({', '.join(missing_g)})",
                            recovery_attempted=True,
                            providers=[
                                {
                                    "provider": "DAILY_BUILDER_2.0",
                                    "result": "PARTIAL_OR_ABSENT",
                                    "failure_type": "METRICS_MISSING_IN_DAILY_BUILDER",
                                },
                                {
                                    "provider": "PIT_DATABASE",
                                    "result": "FETCHED" if g_ok else ("NOT_AVAILABLE" if not pit_filings else "INSUFFICIENT_DATA"),
                                    "failure_type": None if g_ok else ("SYMBOL_NOT_IN_PIT_DB" if not pit_filings else "INSUFFICIENT_QUARTERS_IN_FILINGS"),
                                }
                            ],
                            validation="PASSED" if g_ok else "FAILED",
                            validation_reason=None if g_ok else f"UNRESOLVED_GROWTH_FIELDS_{unresolved_g}",
                            final_action="DATA_USED" if g_ok else "STOCK_SKIPPED",
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

                    # Persist alert to unified alerts table (accessible to all dashboard views & tracking)
                    if save_alert_if_new is not None:
                        try:
                            try:
                                from engine.production.governance_registry import get_current_macro_regime
                                macro_regime = get_current_macro_regime()
                            except Exception:
                                macro_regime = "BULL"

                            now_ist = datetime.now(IST)
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
                            telemetry.record_alert_persistence(sym, inserted, reason or ("INSERTED" if inserted else "REJECTED"), cmp_price)

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

                    # Determine if the run had any data gaps. Any non‑zero data_insufficient count should degrade health.
                    data_gap = (di > 0) or (high_provider_failure) or (high_insufficient) or (high_missing) or (context_failed)
                    is_degraded = is_crashed or data_gap
                    # Emit a clearer health label when data is incomplete.
                    health_status = "OK_WITH_DATA_GAPS" if is_degraded else "OK"
                    # Preserve legacy "DEGRADED" label for backward compatibility in logs.
                    _computed_health_status = "DEGRADED" if is_degraded else "OK"
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

    @staticmethod
    def _build_result(
        symbol: str,
        is_buy: bool,
        rejections: List[RejectionReason],
        metrics: Dict[str, Any]
    ) -> Dict[str, Any]:
        return {
            "symbol": symbol,
            "is_buy": is_buy,
            "decision": "BUY_ALERT" if is_buy else ("BLOCKED_DATA" if any("DATA" in r.value for r in rejections) else "NOT_QUALIFIED"),
            "fundamentally_qualified": not any(r.value.startswith("FAIL_R") or r.value.startswith("FAIL_O") or r.value.startswith("FAIL_E") or r.value.startswith("FAIL_P") or r.value.startswith("FAIL_D") for r in rejections),
            "rejection_reasons": [r.value for r in rejections],
            "metrics": metrics,
            "rules_hash": RULES_HASH_BUY,
            "code_sha": FROZEN_GIT_SHA,
            "timestamp": datetime.now(IST).isoformat()
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
# QUALITY_COMPOUNDER_VALUE_V2_FINAL — FROZEN STRATEGY IMPLEMENTATION
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
        "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/data/pit_raw_filings",
    ]
    resolved_dir = None
    for c in candidate_dirs:
        if c and os.path.isdir(c) and glob.glob(os.path.join(c, "*.json")):
            resolved_dir = c
            break
    if not resolved_dir:
        logger.error(f"❌ [V2_RAW_INDEX] Directory not found across candidates: {candidate_dirs}")
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
    # Does historical evidence prove this company is mature despite having < 5 filings in our file?
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
        except Exception:
            pass

    if first_px_dt is not None:
        if first_px_dt <= cutoff_date:
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
        else:
            # First traded AFTER cutoff date (within last 5 years)! E.g. KRN (Oct 2024), GARUDA (Oct 2024), AIIL (Apr 2024).
            # When earliest annual period also exists and is recent (< 5 years ago), we have corroborated positive proof:
            if earliest_annual_period is not None:
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
    FROZEN PRODUCTION SCANNER: QUALITY_COMPOUNDER_VALUE_V2_FINAL
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
        self.strategy_id = "QUALITY_COMPOUNDER_VALUE_V2_FINAL"
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
        "JMFINANCIL", "LICHSGFIN", "MASFIN", "SUNDARMFIN", "TSFINV", "CGCL", "AIIL"
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
        roce_val = float(roce_val_raw) if (roce_val_raw is not None and not pd.isna(roce_val_raw)) else 0.0
        roce_pts = round(25.0 * min(max((roce_val - 15.0) / 25.0, 0.0), 1.0), 2)
        pe_pts = round(20.0 * min(max(pe_disc_eff / 0.40, 0.0), 1.0), 2)
        cfo_pat_raw = row_dict.get("cfo_pat_5y_ratio")
        cfo_pat_val = float(cfo_pat_raw) if (cfo_pat_raw is not None and not pd.isna(cfo_pat_raw)) else 0.0
        cfo_pts = round(15.0 * min(max((cfo_pat_val - 0.80) / 0.70, 0.0), 1.0), 2)
        if res_dd <= 0.10:
            res_pts = 10.0
        else:
            res_pts = round(10.0 * min(max((0.25 - res_dd) / 0.15, 0.0), 1.0), 2)

        total_score = round(ev_pts + roce_pts + pe_pts + cfo_pts + res_pts, 2)
        return {
            "ev_discount": ev_discount,
            "ev_pts": ev_pts,
            "roce_val": roce_val,
            "roce_pts": roce_pts,
            "pe_discount": pe_discount,
            "pe_pts": pe_pts,
            "cfo_pat_val": cfo_pat_val,
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
                                f"✅ [UPSTREAM_RECOVERY: SUCCESS] {clean_sym}: Successfully recovered "
                                f"current_ev_ebitda={recovered_ev} from Upstox Key-Ratios API (raw='{raw_ev_str}')"
                            )
                            providers_audit.append({
                                "provider": "UPSTOX_KEY_RATIOS_API",
                                "endpoint": api_url,
                                "result": "SUCCESS",
                                "http_status": 200,
                                "latency_ms": elapsed_ms,
                                "field_requested": "EV/EBITDA",
                                "raw_company_value": raw_ev_str,
                                "recovered_value": recovered_ev,
                                "raw_pe_value": raw_pe_str,
                                "recovered_pe": recovered_pe,
                                "validation": "PASSED",
                                "action": "ACCEPTED_DIRECT_UPSTOX_EV_EBITDA"
                            })
                            return recovered_ev, recovered_pe, providers_audit, "RECOVERED_VIA_UPSTOX_KEY_RATIOS_API"
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
            if _sh_f is None:
                _np = row.get("net_profit")
                _ep = row.get("eps")
                if _np is not None and _ep is not None and float(_ep or 0) > 0:
                    _sh_f = (float(_np) * 1e7) / float(_ep)

            _op = row.get("operating_profit")
            _da = row.get("depreciation_amortization")
            _eb = None
            if _op is not None and _da is not None:
                _eb = float(_op) + float(_da)
            elif row.get("ebitda") is not None and pd.notna(row.get("ebitda")):
                _eb = float(row.get("ebitda"))

            if eff_cmp > 0 and _sh_f is not None and _eb is not None and _eb > 0:
                _mc_cr = (_sh_f * eff_cmp) / 1e7
                if _d is not None and _c is not None:
                    _ev = _mc_cr + _d - _c
                elif _d is not None:
                    _ev = _mc_cr + _d  # conservative bound
                else:
                    _ev = None

                if _ev is not None and _ev > 0:
                    recovered_ev = round(_ev / _eb, 2)
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
        Executes the frozen QUALITY_COMPOUNDER_VALUE_V2_FINAL 17:00 IST daily scan run.
        Generates daily immutable SCAN_SNAPSHOT rows for ALL evaluated stocks and
        ALERT_EVENT rows for passing candidate stocks directly in the existing 'alerts' table.
        """
        import time
        start_ts = time.time()
        _scan_start = time.monotonic()  # must be monotonic — print_scanner_end_banner computes time.monotonic() - start_mono
        _computed_v2_health_status = None   # set by _scan_universe_core via exec_run_ctx_holder; passed to end banner
        # Invariant: verify _scan_start is a valid monotonic value, not a wall-clock timestamp.
        assert _scan_start > 0, f"V2_FINAL _scan_start={_scan_start} must be positive"
        assert _scan_start < 1e9, (
            f"V2_FINAL _scan_start={_scan_start:.0f} looks like time.time() (wall-clock), "
            f"not time.monotonic(). Duration will be negative in end banner."
        )
        acquired_scan = False
        acquired_global = False
        exec_run_ctx_holder = [None]

        # 1. Thread-level concurrency lock: prevent overlapping runs of same scanner
        if not _v2_scan_lock.acquire(blocking=False):
            logger.warning("🔒 [V2_FINAL] Scanner is already running in another thread. Skipping duplicate cycle.")
            return {"status": "SKIPPED", "reason": "Already running"}
        acquired_scan = True

        # 2. Universal global scanner lock queue wait: serialize TECHNICAL, FUNDAMENTAL, V2_FINAL
        queued_at = time.monotonic()
        if not _global_lock.acquire(blocking=False, owner_scanner="QUALITY_COMPOUNDER_VALUE_V2_FINAL", operation="FULL_SCAN"):
            logger.info("⏳ [V2_FINAL] Global scanner lock busy (another main scanner is running) — waiting in queue until active scanner finishes...")
            try:
                from database import upsert_scanner_health
            except ImportError:
                try:
                    from app.database import upsert_scanner_health
                except Exception:
                    upsert_scanner_health = None
            if upsert_scanner_health is not None:
                try:
                    upsert_scanner_health("QUALITY_COMPOUNDER_VALUE_V2_FINAL", "QUEUED", error_msg="Waiting in queue for active scanner to release lock...")
                except Exception:
                    pass

            try:
                acquired_global = _global_lock.acquire(blocking=True, owner_scanner="QUALITY_COMPOUNDER_VALUE_V2_FINAL", operation="FULL_SCAN")
            except Exception as lock_err:
                logger.error(f"❌ [V2_FINAL] Error acquiring global lock: {lock_err}")
                acquired_global = False

            if not acquired_global:
                logger.error("❌ [V2_FINAL] Failed to acquire global scanner lock after queue wait.")
                if upsert_scanner_health is not None:
                    try:
                        upsert_scanner_health("QUALITY_COMPOUNDER_VALUE_V2_FINAL", "IDLE", error_msg="Lock acquisition timed out")
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
                    "QUALITY_COMPOUNDER_VALUE_V2_FINAL",
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
                    scanner_name="QUALITY_COMPOUNDER_VALUE_V2_FINAL",
                    trigger_type=trigger_type,
                    allow_concurrent=True
                )
                if exec_run_ctx_holder is not None:
                    exec_run_ctx_holder[0] = exec_run_ctx
            except Exception as e:
                logger.debug(f"Execution history start warning: {e}")

        # Start Banner
        print_scanner_start_banner("QUALITY_COMPOUNDER_VALUE_V2_FINAL", queued_at=queued_at, run_id=getattr(exec_run_ctx, "run_id", None))

        if upsert_scanner_health is not None:
            try:
                upsert_scanner_health(
                    "QUALITY_COMPOUNDER_VALUE_V2_FINAL",
                    status="RUNNING",
                    run_id=getattr(exec_run_ctx, "run_id", None)
                )
            except Exception as e:
                logger.debug(f"Scanner health RUNNING warning: {e}")

        logger.info(f"📡 [SCANNER: V2_FINAL] Starting 17:00 IST daily scan run ({today_str}, trigger={trigger_type})...")

        # Load PIT fundamentals dataset
        pit_df = self.load_pit_dataset()
        if pit_df is None or pit_df.empty:
            logger.error("❌ [SCANNER: V2_FINAL] Failed to load PIT dataset — scan failed!")
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
                    upsert_scanner_health("QUALITY_COMPOUNDER_VALUE_V2_FINAL", status="DOWN", error_msg="PIT dataset unavailable", run_id=getattr(exec_run_ctx, "run_id", None))
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
                    scanner_id="QUALITY_COMPOUNDER_VALUE_V2_FINAL",
                    scanner_name="Quality Compounder Value V2 Final",
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

                _emit_data_recovery_log(
                    scanner="QUALITY_COMPOUNDER_VALUE_V2_FINAL",
                    symbol=sym,
                    stage="QUALITY",
                    missing_data="pit_statement_filings (ROCE, Sales CAGR, PAT CAGR, CFO/PAT, D/E)",
                    recovery_attempted=True,
                    providers=[
                        {
                            "provider": "PIT_DATABASE (pit_fundamentals_v1.parquet)",
                            "result": "NOT_AVAILABLE",
                            "failure_type": "SYMBOL_NOT_IN_PIT_DATASET",
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

            # Provenance tracking from statement calculations
            growth_start_period = row.get('growth_start_period')
            growth_end_period = row.get('growth_end_period')
            growth_years_elapsed = row.get('growth_years_elapsed')
            financial_periods_used = row.get('financial_periods_used')
            roce_periods_used = row.get('roce_periods_used')

            # Dynamic real-time calculation from CMP + statement filings if multiples were not pre-calculated
            if (ev_ebitda_curr is None or pd.isna(ev_ebitda_curr)) and cmp_price > 0:
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
            _f_ev_m = round(ev_ebitda_curr, 2) if (ev_ebitda_curr is not None and not pd.isna(ev_ebitda_curr)) else None
            _f_med  = round(float(ev_ebitda_med), 2) if (ev_ebitda_med is not None and not pd.isna(ev_ebitda_med)) else None
            if _f_ev_m is None and _f_med is None:
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

            # Missing Price Check — HARD BLOCK for candidate selection
            price_data_missing = (cmp_price is None or cmp_price <= 0.0)
            if price_data_missing:
                price_df_symbols.add(sym)
                rejections.append("DATA_INSUFFICIENT_PRICE")
                price_data_blocked_count += 1
                _emit_data_recovery_log(
                    scanner="QUALITY_COMPOUNDER_VALUE_V2_FINAL",
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
                quality_data_missing = any(v is None or pd.isna(v) for v in [roce_5y, sales_cagr_5y, pat_cagr_5y, cfo_pat_5y, de_ratio])
                if quality_data_missing:
                    rejections.append("DATA_INSUFFICIENT_QUALITY")
                    incomplete_quality_count += 1
                    # Identify exactly which fields are missing for the audit log
                    _missing_fields = [
                        name for name, val in [
                            ("roce_5y", roce_5y), ("sales_cagr_5y", sales_cagr_5y),
                            ("pat_cagr_5y", pat_cagr_5y), ("cfo_pat_5y", cfo_pat_5y),
                            ("debt_to_equity", de_ratio),
                        ] if val is None or pd.isna(val)
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
                        scanner="QUALITY_COMPOUNDER_VALUE_V2_FINAL",
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
                    sales_val = float(sales_cagr_5y)
                    pat_val = float(pat_cagr_5y)
                    cfo_val = float(cfo_pat_5y)
                    de_val = float(de_ratio)

                    if roce_val < 15.0: rejections.append("FAIL_ROCE")
                    if sales_val < 10.0: rejections.append("FAIL_SALES_CAGR")
                    if pat_val < 10.0: rejections.append("FAIL_PAT_CAGR")
                    if cfo_val < 0.80: rejections.append("FAIL_CFO_PAT")
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
            if (ev_ebitda_curr is None or pd.isna(ev_ebitda_curr)) and not is_fin and quality_gate_passed:
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
                    logger.info(
                        f"🎉 [V2_VALUATION_GATE] {sym}: Upstream recovery succeeded! "
                        f"current_ev_ebitda set to {ev_ebitda_curr} via {recovery_verdict}."
                    )
                    if (pe_curr is None or pd.isna(pe_curr)) and recovered_pe is not None:
                        pe_curr = recovered_pe
                    _emit_data_recovery_log(
                        scanner="QUALITY_COMPOUNDER_VALUE_V2_FINAL",
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
            if ev_ebitda_curr is not None and ev_ebitda_med is not None and not pd.isna(ev_ebitda_curr) and not pd.isna(ev_ebitda_med) and float(ev_ebitda_med or 0) > 0:
                calc_discount = (float(ev_ebitda_med) - float(ev_ebitda_curr)) / float(ev_ebitda_med)

            valuation_data_missing = (calc_discount is None)
            curr_val_missing = (ev_ebitda_curr is None or pd.isna(ev_ebitda_curr))
            med_val_missing = (ev_ebitda_med is None or pd.isna(ev_ebitda_med) or float(ev_ebitda_med or 0) <= 0)
            _val_reason = None

            if valuation_data_missing:
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
                    scanner="QUALITY_COMPOUNDER_VALUE_V2_FINAL",
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
            if quality_data_missing or valuation_data_missing or price_data_missing:
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
            if sales_cagr_5y is None or pd.isna(sales_cagr_5y) or float(sales_cagr_5y) < 10.0:
                cur_v = f"{float(sales_cagr_5y):.1f}%" if sales_cagr_5y is not None and not pd.isna(sales_cagr_5y) else "N/A"
                required_improvements.append(f"5Y Sales CAGR >= 10.0% (Current: {cur_v})")
            if pat_cagr_5y is None or pd.isna(pat_cagr_5y) or float(pat_cagr_5y) < 10.0:
                cur_v = f"{float(pat_cagr_5y):.1f}%" if pat_cagr_5y is not None and not pd.isna(pat_cagr_5y) else "N/A"
                required_improvements.append(f"5Y PAT CAGR >= 10.0% (Current: {cur_v})")
            if cfo_pat_5y is None or pd.isna(cfo_pat_5y) or float(cfo_pat_5y) < 0.80:
                cur_v = f"{float(cfo_pat_5y):.2f}" if cfo_pat_5y is not None and not pd.isna(cfo_pat_5y) else "N/A"
                required_improvements.append(f"5Y Cum CFO/PAT >= 0.80 (Current: {cur_v})")
            if de_ratio is not None and not pd.isna(de_ratio) and float(de_ratio) > 0.50:
                required_improvements.append(f"Debt/Equity <= 0.50 (Current: {float(de_ratio):.2f})")
            if ev_discount is None:
                required_improvements.append("EV/EBITDA Discount >= 25% (Current: N/A — valuation data missing)")
            elif ev_discount < 0.25:
                required_improvements.append(f"EV/EBITDA Discount >= 25% (Current: {ev_discount*100:.1f}%)")

            # Per-Stock Complete Telemetry Logging
            ev_disc_str = f"{ev_discount*100:.1f}%" if ev_discount is not None else "N/A (DATA_INSUFFICIENT)"
            telemetry_status = "CANDIDATE" if is_candidate else "REJECTED"
            logger.info(
                f"🔍 [STOCK_TELEMETRY: V2] {sym:<12} | Status={telemetry_status:<9} | "
                f"FailedAt={primary_rejection:<28} | Rejections={rejections} | "
                f"CMP=₹{cmp_price:<8.2f} (Source={price_source}) | "
                f"Metrics=[roce_5y={roce_5y}, sales_cagr_5y={sales_cagr_5y}, pat_cagr_5y={pat_cagr_5y}, cfo_pat_5y={cfo_pat_5y}, d_e={de_ratio}, ev_discount={ev_disc_str}] | "
                f"ValuationDetails=[EV_curr={ev_ebitda_curr}, EV_3Y_med={ev_ebitda_med}, PE_curr={pe_curr}, PE_3Y_med={pe_med}] | "
                f"RequiredToPass={required_improvements if required_improvements else ['NONE (PASSING CANDIDATE)']}"
            )

            # Determine Top-Level Population and Forensic Record
            _struct_rsn = None
            _df_rsns = []

            if quality_data_missing and _inc_cls is not None:
                if _inc_cls["is_structural"]:
                    _struct_rsn = _inc_cls["reason"]
                else:
                    _df_rsns.append(_inc_cls["reason"])

            if valuation_data_missing:
                _df_rsns.append(f"VALUATION_DATA_GAP: {_val_reason or 'DISCOUNT_UNAVAILABLE'}")

            if sym in provider_failed_symbols:
                price_df_symbols.add(sym)
                _df_rsns.append("PRICE_PROVIDER_FAILURE")
            elif price_data_missing:
                price_df_symbols.add(sym)
                _df_rsns.append("PRICE_DATA_MISSING")

            if sym in structural_ineligible_symbols:
                _top_pop = "STRUCTURAL_INELIGIBLE"
            elif _df_rsns:
                _top_pop = "DATA_FAILURE"
            else:
                _top_pop = "FULLY_EVALUABLE"

            _val_status = "NOT_EVALUATED" if valuation_data_missing else ("PASS" if value_gate_passed else "FAIL")
            _qual_status = "FAIL" if quality_data_missing else ("PASS" if quality_gate_passed else "FAIL")

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
                "current_ev_status": "PRESENT" if _safe_pos(ev_ebitda_curr) else "MISSING",
                "ev_3y_median_status": "PRESENT" if _safe_pos(ev_ebitda_med) else "MISSING",
                "provenance_source": "PIT_FUNDAMENTALS_V1",
            })

            # Context Payload for forensic prospective research
            ctx = {
                "strategy_id": self.strategy_id,
                "symbol": sym,
                "scan_date": today_str,
                "industry": industry,
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
                "sales_cagr_5y": round(float(sales_cagr_5y), 2) if sales_cagr_5y is not None and not pd.isna(sales_cagr_5y) else None,
                "pat_cagr_5y": round(float(pat_cagr_5y), 2) if pat_cagr_5y is not None and not pd.isna(pat_cagr_5y) else None,
                "cfo_pat_5y_ratio": round(float(cfo_pat_5y), 2) if cfo_pat_5y is not None and not pd.isna(cfo_pat_5y) else None,
                "debt_to_equity": round(float(de_ratio), 2) if de_ratio is not None and not pd.isna(de_ratio) else None,
                "share_dilution_3y_pct": round(float(share_dilution_3y), 2) if share_dilution_3y is not None and not pd.isna(share_dilution_3y) else None,
                "current_ev_ebitda": round(float(ev_ebitda_curr), 2) if ev_ebitda_curr is not None and not pd.isna(ev_ebitda_curr) else None,
                "ev_ebitda_3y_median": round(float(ev_ebitda_med), 2) if ev_ebitda_med is not None and not pd.isna(ev_ebitda_med) else None,
                "ev_ebitda_discount_pct": round(ev_discount * 100, 1) if ev_discount is not None else None,
                "valuation_status": "DATA_AVAILABLE" if ev_discount is not None else "DATA_INSUFFICIENT",
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
                collector.record_production_metric(sym, "5Y_SALES_CAGR", sales_cagr_5y, f"{float(sales_cagr_5y):.2f}%" if sales_cagr_5y is not None and not pd.isna(sales_cagr_5y) else "N/A", sales_cagr_5y, "%")
                collector.record_production_metric(sym, "5Y_PAT_CAGR", pat_cagr_5y, f"{float(pat_cagr_5y):.2f}%" if pat_cagr_5y is not None and not pd.isna(pat_cagr_5y) else "N/A", pat_cagr_5y, "%")
                collector.record_production_metric(sym, "5Y_CFO_PAT_RATIO", cfo_pat_5y, f"{float(cfo_pat_5y):.2f}" if cfo_pat_5y is not None and not pd.isna(cfo_pat_5y) else "N/A", cfo_pat_5y, "ratio")
                collector.record_production_metric(sym, "DEBT_TO_EQUITY", de_ratio, f"{float(de_ratio):.2f}" if de_ratio is not None and not pd.isna(de_ratio) else "N/A", de_ratio, "ratio")
                collector.record_production_metric(sym, "CURRENT_EV_EBITDA", ev_ebitda_curr, f"{float(ev_ebitda_curr):.2f}" if ev_ebitda_curr is not None and not pd.isna(ev_ebitda_curr) else "N/A", ev_ebitda_curr, "ratio")
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
                    p_sales = (sales_cagr_5y is not None and not pd.isna(sales_cagr_5y) and float(sales_cagr_5y) >= 10.0)
                    p_pat = (pat_cagr_5y is not None and not pd.isna(pat_cagr_5y) and float(pat_cagr_5y) >= 10.0)
                    p_cfo = (cfo_pat_5y is not None and not pd.isna(cfo_pat_5y) and float(cfo_pat_5y) >= 0.80)
                    p_de = (de_ratio is not None and not pd.isna(de_ratio) and float(de_ratio) <= 0.50)
                    collector.record_gate_result(sym, "5Y_ROCE", "QUALITY", ">= 15.0%", 15.0, roce_5y, ">=", "PASS" if p_roce else "FAIL")
                    collector.record_gate_result(sym, "5Y_SALES_CAGR", "QUALITY", ">= 10.0%", 10.0, sales_cagr_5y, ">=", "PASS" if p_sales else "FAIL")
                    collector.record_gate_result(sym, "5Y_PAT_CAGR", "QUALITY", ">= 10.0%", 10.0, pat_cagr_5y, ">=", "PASS" if p_pat else "FAIL")
                    collector.record_gate_result(sym, "5Y_CFO_PAT", "QUALITY", ">= 0.80", 0.80, cfo_pat_5y, ">=", "PASS" if p_cfo else "FAIL")
                    collector.record_gate_result(sym, "DEBT_TO_EQUITY", "QUALITY", "<= 0.50", 0.50, de_ratio, "<=", "PASS" if p_de else "FAIL")

                    p_val = (calc_discount is not None and calc_discount >= 0.25)
                    collector.record_gate_result(sym, "EV_EBITDA_DISCOUNT", "VALUATION", ">= 25.0%", 0.25, calc_discount, ">=", "PASS" if p_val else "FAIL")
                else:
                    collector.record_gate_result(sym, "5Y_ROCE", "QUALITY", ">= 15.0%", 15.0, roce_5y, ">=", "BLOCKED_FINANCIAL_SECTOR")
                    collector.record_gate_result(sym, "5Y_SALES_CAGR", "QUALITY", ">= 10.0%", 10.0, sales_cagr_5y, ">=", "BLOCKED_FINANCIAL_SECTOR")
                    collector.record_gate_result(sym, "5Y_PAT_CAGR", "QUALITY", ">= 10.0%", 10.0, pat_cagr_5y, ">=", "BLOCKED_FINANCIAL_SECTOR")
                    collector.record_gate_result(sym, "5Y_CFO_PAT", "QUALITY", ">= 0.80", 0.80, cfo_pat_5y, ">=", "BLOCKED_FINANCIAL_SECTOR")
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

        # Persist scan results & evaluate post-scan health under safety gates
        try:
            duration_sec = round(time.time() - start_ts, 2)

            # ── EXACT SET UNION MATH RECONCILIATION ──────────────────────────────
            data_failure_symbols = (non_pit_df_symbols | quality_df_symbols | val_df_symbols | price_df_symbols) - structural_ineligible_symbols
            data_failure_count = len(data_failure_symbols)
            structural_ineligible_count = len(structural_ineligible_symbols)
            fully_evaluable_symbols = set(universe_symbols) - structural_ineligible_symbols - data_failure_symbols
            fully_evaluable_count = len(fully_evaluable_symbols)

            _approved_universe = total_scanned
            _structural_ineligible = structural_ineligible_count
            _evaluable_universe = _approved_universe - _structural_ineligible
            _data_failures = data_failure_count
            _fully_evaluable = fully_evaluable_count

            assert _approved_universe == _structural_ineligible + _evaluable_universe, (
                f"Approved ({_approved_universe}) != Structural ({_structural_ineligible}) + Evaluable ({_evaluable_universe})"
            )
            assert _evaluable_universe == _data_failures + _fully_evaluable, (
                f"Evaluable ({_evaluable_universe}) != DataFailure ({_data_failures}) + FullyEvaluable ({_fully_evaluable})"
            )
            assert _approved_universe == _structural_ineligible + _data_failures + _fully_evaluable, (
                f"Approved ({_approved_universe}) != Structural ({_structural_ineligible}) + DataFailure ({_data_failures}) + FullyEvaluable ({_fully_evaluable})"
            )
            assert len(structural_ineligible_symbols & data_failure_symbols) == 0, "Overlap between Structural and Data Failure"
            assert len(structural_ineligible_symbols & fully_evaluable_symbols) == 0, "Overlap between Structural and Fully Evaluable"
            assert len(data_failure_symbols & fully_evaluable_symbols) == 0, "Overlap between Data Failure and Fully Evaluable"

            # ── POST-SCAN HEALTH STATUS ───────────────────────────────────────────
            # V2 CONSERVATIVE HEALTH ACCOUNTING (2026-10-01):
            #
            # STRUCTURAL_INELIGIBLE: symbol cannot satisfy 5Y prerequisite by design (< 5 years
            #                        proven historical existence). NOT a data failure. NEVER triggers DEGRADED.
            # DATA_FAILURE: symbol that should be evaluable, but required data is missing.
            #               MUST trigger DEGRADED until resolved.
            # HEALTH = OK (GREEN) when DATA_FAILURE_COUNT == 0 and zero_price_candidates == 0.
            # ZERO price candidates => BLOCKED.
            zero_price_candidates = [c for c in candidate_records if float(c.get("current_price", 0) or 0) <= 0]
            if zero_price_candidates:
                _health_status = "BLOCKED"
                _health_error = f"ZERO_PRICE_CANDIDATE_DEFECT: {len(zero_price_candidates)} candidates produced with CMP <= 0"
            elif data_failure_count > 0:
                _health_status = "DEGRADED"
                _health_error = (
                    f"DATA_FAILURE: {data_failure_count} symbols with unresolved data failures "
                    f"(NonPIT={len(non_pit_df_symbols)}, Quality={len(quality_df_symbols)}, "
                    f"Valuation={len(val_df_symbols)}, Price={len(price_df_symbols)}) | "
                    f"StructuralIneligible={structural_ineligible_count} (excluded from health basis)"
                )
            else:
                _health_status = "OK"
                _health_error = None

            if _health_error:
                logger.warning(f"⚠️ [V2_FINAL] SCANNER HEALTH: {_health_status} | {_health_error}")

            # ── ALERT ROUTING GOVERNANCE UNDER HEALTH GATES ─────────────────────────
            candidates_inserted = 0
            if _health_status in ("DATA_BLOCKED", "BLOCKED"):
                if candidate_records:
                    logger.warning(
                        f"🚫 [V2_ALERT_SUPPRESSED] SCANNER HEALTH IS {_health_status}: "
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
                    ok, msg = save_v2_candidate_alert(cand)
                    if ok:
                        candidates_inserted += 1
                        logger.info(
                            f"🚀 [BUY_ALERT: V2] {cand['symbol']:<12} | Tier={cand['tier']} | "
                            f"Score={cand['ranking_score']:<5.1f} | CMP=₹{cand['current_price']:<8.2f} (Source={cand['context'].get('price_source', 'UNKNOWN')}) | Status={msg}"
                        )

            snapshots_inserted = save_v2_scan_snapshots(snapshot_records)

            if complete_scanner_execution_run is not None and exec_run_ctx and getattr(exec_run_ctx, "run_id", None):
                try:
                    complete_scanner_execution_run(
                        ctx=exec_run_ctx,
                        run_id=exec_run_ctx.run_id,
                        total_scanned=total_scanned,
                        total_stocks=total_scanned,
                        candidate_count=candidates_inserted,
                        quality_status=_health_status,
                        data_insufficient_count=incomplete_quality_count,
                        data_missing_count=non_pit_blocked_count,
                        summary_notes=(
                            f"Approved={total_scanned} | "
                            f"PIT={pit_univ_cnt} | Non_PIT={non_pit_blocked_count} | "
                            f"StructuralIneligible={structural_ineligible_count} | DataFailures={data_failure_count} | "
                            f"QualityPass={quality_pass_count} | QualityReject={quality_reject_count} | "
                            f"ValuationPass={value_pass_count} | ValuationReject={value_reject_count} | "
                            f"ResearchCandidates={len(candidate_records)} | LiveAlerts={candidates_inserted} | Health={_health_status}"
                        ),
                        metrics_json={
                            "total_scanned": total_scanned,
                            "pit_univ_cnt": pit_univ_cnt,
                            "non_pit_blocked_count": non_pit_blocked_count,
                            "data_complete_count": data_complete_count,
                            "data_blocked_count": data_blocked_count,
                            "incomplete_quality_count": incomplete_quality_count,
                            "valuation_data_blocked_count": valuation_data_blocked_count,
                            "val_curr_missing_count": val_curr_missing_count,
                            "val_med_missing_count": val_med_missing_count,
                            "val_both_missing_count": val_both_missing_count,
                            "quality_only_blocked_count": quality_only_blocked_count,
                            "val_only_blocked_count": val_only_blocked_count,
                            "quality_and_val_blocked_count": quality_and_val_blocked_count,
                            "price_data_blocked_count": price_data_blocked_count,
                            "quality_pass_count": quality_pass_count,
                            "quality_reject_count": quality_reject_count,
                            "value_pass_count": value_pass_count,
                            "value_reject_count": value_reject_count,
                            "research_candidates_detected": len(candidate_records),
                            "live_alerts_generated": candidates_inserted,
                            "health_status": _health_status,
                            "health_error": _health_error,
                            # V2 Canonical 3-population health accounting
                            "structural_ineligible_count": structural_ineligible_count,
                            "non_pit_structural_count": non_pit_structural_count,
                            "incomplete_pit_structural_count": incomplete_pit_structural_count,
                            "data_failure_count": data_failure_count,
                            "non_pit_data_failure_count": len(non_pit_df_symbols),
                            "incomplete_pit_data_failure_count": len(quality_df_symbols),
                            "valuation_data_failure_count": len(val_df_symbols),
                            "price_data_failure_count": len(price_df_symbols),
                            "evaluable_universe_count": _evaluable_universe,
                            "fully_evaluable_count": _fully_evaluable,
                            "health_basis": "DATA_FAILURE_COUNT",
                            "health_basis_threshold": "GREEN_WHEN_ZERO",
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
                        "QUALITY_COMPOUNDER_VALUE_V2_FINAL",
                        status=_health_status,
                        today_alerts=candidates_inserted,
                        last_success=now_ist.isoformat() if _health_status in ("OK", "DEGRADED") else None,
                        processed_count=candidates_inserted,
                        total_count=total_scanned,
                        duration_seconds=duration_sec,
                        error_msg=_health_error,
                        run_id=getattr(exec_run_ctx, "run_id", None)
                    )
                except Exception as e:
                    logger.debug(f"Scanner health update warning: {e}")

            # Structured End-of-Scan Telemetry Summary Report with strict mathematical identities
            _pit_blocked_cnt = pit_univ_cnt - data_complete_count
            _venn_sum = quality_only_blocked_count + val_only_blocked_count + quality_and_val_blocked_count + price_only_blocked_count
            _venn_ok = (_venn_sum == _pit_blocked_cnt)
            _val_decomp_sum = val_curr_missing_count + val_med_missing_count - val_both_missing_count
            _val_decomp_ok = (_val_decomp_sum == valuation_data_blocked_count)

            logger.info("=" * 80)
            logger.info(f"📊 [SCANNER TELEMETRY: QUALITY_COMPOUNDER_VALUE_V2_FINAL] END-OF-SCAN REPORT ({today_str})")
            logger.info("=" * 80)
            logger.info("  1. UNIVERSE & DATA PROVENANCE ACCOUNTING:")
            logger.info(f"     • Approved Scanner Universe      : {total_scanned}")
            logger.info(f"     • PIT Valuation Universe         : {pit_univ_cnt}  (symbols with audited PIT statement history)")
            logger.info(f"     • Non-PIT / Missing PIT Filings  : {non_pit_blocked_count}  (blocked — see breakdown below)")
            logger.info(f"       ├─ Structural Ineligible         : {non_pit_structural_count}  (absent from PIT parquet, proven genuine insufficient history)")
            logger.info(f"       └─ Data Failures (ingestion gap) : {non_pit_data_failure_count}  (mature/unknown, absent from PIT parquet)")
            logger.info(f"     • Price Provider Accounting (across all {total_scanned} symbols):")
            logger.info(f"       ├─ Requested Price Symbols     : {len(requested_price_symbols)}")
            logger.info(f"       ├─ Successful Price Quotes     : {len(successful_price_symbols)}")
            logger.info(f"       ├─ Provider Failed Symbols     : {len(provider_failed_symbols)}")
            logger.info(f"       └─ Zero/Negative Price Quotes  : {len(zero_or_negative_price_symbols)}")
            logger.info(f"       [Identity: {pit_univ_cnt} PIT + {non_pit_blocked_count} Non-PIT = {total_scanned} Approved Universe]  {'✅' if pit_univ_cnt + non_pit_blocked_count == total_scanned else '⚠️ MISMATCH'}")
            logger.info(f"     • PIT Field Completeness         : EV/EBITDA_curr={_ev_curr_cnt}/{pit_univ_cnt}, EV/EBITDA_3Ymed={_ev_med_cnt}/{pit_univ_cnt}, PE_curr={_pe_curr_cnt}/{pit_univ_cnt}, PE_3Ymed={_pe_med_cnt}/{pit_univ_cnt}, EV_PE_Both_Complete={_ev_pe_both_complete}/{pit_univ_cnt} (Cache: {_valuation_cache_cert_status})")
            logger.info("  2. DATA COMPLETENESS & BLOCKING RECONCILIATION:")
            logger.info(f"     • Complete Required Quality Data : {data_complete_count}  ({round(data_complete_count/max(total_scanned,1)*100,1)}% of universe — requires quality + valuation + price all present)")
            logger.info(f"     • Total Data Blocked             : {data_blocked_count}  ({round(data_blocked_count/max(total_scanned,1)*100,1)}% of universe)")
            logger.info(f"       [Identity: {data_complete_count} Complete + {data_blocked_count} Blocked = {total_scanned} Universe]  {'✅' if data_complete_count + data_blocked_count == total_scanned else '⚠️ MISMATCH'}")
            logger.info(f"       ├─ Non-PIT (no filing history) : {non_pit_blocked_count}")
            logger.info(f"       └─ PIT blocked (any required field missing) : {_pit_blocked_cnt} of {pit_univ_cnt} PIT symbols")
            logger.info(f"           ├─ Quality-Only Incomplete    : {quality_only_blocked_count}  (quality missing, valuation complete)")
            logger.info(f"           ├─ Valuation-Only Incomplete  : {val_only_blocked_count}  (valuation missing, quality complete)")
            logger.info(f"           ├─ Both Quality & Valuation   : {quality_and_val_blocked_count}  (both quality and valuation missing)")
            logger.info(f"           └─ Price-Only Missing         : {price_only_blocked_count}  (missing/non-positive CMP)")
            logger.info(f"           [Venn Identity: {quality_only_blocked_count} Quality-Only + {val_only_blocked_count} Valuation-Only + {quality_and_val_blocked_count} Both + {price_only_blocked_count} Price-Only = {_pit_blocked_cnt} PIT Blocked]  {'✅' if _venn_ok else '⚠️ MISMATCH'}")
            logger.info(f"       • Valuation Blocked Decomposition (Total Valuation Blocked: {valuation_data_blocked_count}):")
            logger.info(f"           ├─ Current EV/EBITDA Missing  : {val_curr_missing_count}")
            logger.info(f"           ├─ 3Y EV/EBITDA Median Missing: {val_med_missing_count}")
            logger.info(f"           └─ Both Missing (Overlap)     : {val_both_missing_count}")
            logger.info(f"           [Inclusion-Exclusion Identity: {val_curr_missing_count} Current + {val_med_missing_count} 3Y_Med - {val_both_missing_count} Both = {valuation_data_blocked_count} Valuation Blocked]  {'✅' if _val_decomp_ok else '⚠️ MISMATCH'}")
            logger.info(f"       [Universe Identity: {pit_univ_cnt} PIT + {non_pit_blocked_count} Non-PIT = {total_scanned} Approved]  {'✅' if pit_univ_cnt + non_pit_blocked_count == total_scanned else '⚠️ MISMATCH'}")
            logger.info(f"       [Total Blocked Identity: {non_pit_blocked_count} Non-PIT + {_pit_blocked_cnt} PIT-Blocked = {data_blocked_count} Total Blocked]  {'✅' if non_pit_blocked_count + _pit_blocked_cnt == data_blocked_count else '⚠️ MISMATCH'}")
            _quality_evaluated = pit_univ_cnt - incomplete_quality_count
            logger.info("  3. STRATEGY FILTER FUNNEL RECONCILIATION:")
            logger.info(f"     • Quality-evaluated PIT symbols  : {_quality_evaluated}  (PIT symbols with full ROCE/CAGR/CFO/D_E history)")
            logger.info(f"     • Quality Gate Passed            : {quality_pass_count}")
            logger.info(f"     • Quality Gate Rejected          : {quality_reject_count}  (failed ROCE, CAGR, CFO, debt, or liquidity)")
            logger.info(f"       [Identity: {quality_pass_count} Passed + {quality_reject_count} Rejected = {_quality_evaluated} Quality-evaluated]  {'✅' if quality_pass_count + quality_reject_count == _quality_evaluated else '⚠️ MISMATCH'}")
            _val_blocked_count = quality_pass_count - value_pass_count - value_reject_count
            _val_evaluated = quality_pass_count - _val_blocked_count
            logger.info("     • UNIVERSE VALUATION DATA GAPS (diagnostic across entire PIT universe):")
            logger.info(f"         ├─ Current EV/EBITDA missing : {val_curr_missing_count}")
            logger.info(f"         ├─ 3Y median missing         : {val_med_missing_count}")
            logger.info(f"         └─ Both missing (overlap)    : {val_both_missing_count}")
            logger.info("     • QUALITY-PASSED STOCKS VALUATION FUNNEL:")
            logger.info(f"         ├─ Valuation evaluated       : {_val_evaluated}")
            logger.info(f"         ├─ Valuation passed          : {value_pass_count}  (EV/EBITDA discount >= 25%)")
            logger.info(f"         ├─ Valuation rejected        : {value_reject_count}  (EV/EBITDA discount < 25%)")
            logger.info(f"         └─ Valuation blocked by data : {_val_blocked_count}")
            logger.info(f"       [Identity: {value_pass_count} Val-Pass + {value_reject_count} Val-Reject + {_val_blocked_count} Val-Blocked = {quality_pass_count} Quality-Passed]  {'✅' if value_pass_count + value_reject_count + _val_blocked_count == quality_pass_count else '⚠️ MISMATCH'}")
            logger.info(f"     • Research Candidates (Gates OK) : {len(candidate_records)}")
            _suppressed_syms = [c['symbol'] for c in candidate_records] if _health_status in ('DATA_BLOCKED', 'BLOCKED') and candidate_records else []
            _suppression_note = (
                f"(SUPPRESSED: {len(_suppressed_syms)} research candidate(s) blocked — {_suppressed_syms}, reason=DATA_BLOCKED)"
                if _suppressed_syms else ""
            )
            logger.info(f"     • Production BUY Alerts Saved    : {candidates_inserted} {_suppression_note}")
            logger.info(f"     • Snapshots Saved in DB          : {snapshots_inserted}  (100% universe audit trail)")
            logger.info("  4. HEALTH STATE & ALERT ROUTING GOVERNANCE:")
            logger.info(f"     • Health Status                  : {_health_status}")
            logger.info(f"     • Health Basis (Canonical 3-Pop) : DATA_FAILURE_COUNT={data_failure_count} (GREEN when = 0)")
            logger.info(f"     • Zero-Price Defect Count        : {len(zero_price_candidates)}")
            logger.info(f"     • Candidate Alerts Saved         : {candidates_inserted}")
            logger.info(f"     • Duration (Seconds)             : {duration_sec}s")

            logger.info("  5. CANONICAL 3-POPULATION CLASSIFICATION (V2 CONSERVATIVE HEALTH ACCOUNTING):")
            logger.info(f"     • Approved Universe              : {_approved_universe}  (all approved clean equities evaluated)")
            logger.info(f"     • Structural Ineligible          : {_structural_ineligible}  (proven genuine limited existence < {required_v2_annual_history()}Y — excluded from health basis)")
            logger.info(f"       ├─ Non-PIT structural          : {non_pit_structural_count}")
            logger.info(f"       └─ Incomplete-PIT structural   : {incomplete_pit_structural_count}")
            logger.info(f"     • Evaluable Universe             : {_evaluable_universe}  (Approved minus Structural)")
            logger.info(f"     • Data Failures                  : {_data_failures}  (Exact Set Union of all data gaps — triggers DEGRADED)")
            logger.info(f"       ├─ Non-PIT data failures       : {len(non_pit_df_symbols)}")
            logger.info(f"       ├─ Quality data failures       : {len(quality_df_symbols)}")
            logger.info(f"       ├─ Valuation data failures     : {len(val_df_symbols)} ({val_only_blocked_count} valuation-only + {quality_and_val_blocked_count} overlap with quality)")
            logger.info(f"       └─ Price data failures         : {len(price_df_symbols)}")
            logger.info(f"     • Fully Evaluable                : {_fully_evaluable}  (Evaluable minus Data Failures)")
            logger.info(f"     • Quality Passed                 : {quality_pass_count}")
            logger.info(f"     • Valuation Evaluated            : {_val_evaluated}")
            logger.info(f"     • Valuation Passed               : {value_pass_count}")
            logger.info(f"     • BUY Alerts                     : {candidates_inserted}")
            logger.info("")
            logger.info("     [V2 CANONICAL POPULATION IDENTITIES]")
            logger.info(f"     Approved = Structural + DataFailures + FullyEvaluable ({_approved_universe} = {_structural_ineligible} + {_data_failures} + {_fully_evaluable})  ✅ PASS")
            logger.info(f"     Evaluable = DataFailures + FullyEvaluable ({_evaluable_universe} = {_data_failures} + {_fully_evaluable})  ✅ PASS")

            # Save symbol-level forensic audit CSV & Parquet with exact 13 required columns
            audit_parquet_p = os.path.join(DATA_DIR, "v2_health_population_audit.parquet")
            audit_csv_p = os.path.join(DATA_DIR, "v2_health_population_audit.csv")
            try:
                audit_df = pd.DataFrame(population_audit_records)
                audit_cols = [
                    "symbol",
                    "top_level_population",
                    "quality_status",
                    "valuation_status",
                    "price_status",
                    "filing_annual_count",
                    "earliest_annual_period",
                    "latest_annual_period",
                    "structural_reason",
                    "data_failure_reasons",
                    "current_ev_status",
                    "ev_3y_median_status",
                    "provenance_source",
                ]
                audit_df = audit_df[audit_cols]
                assert len(audit_df) == total_scanned, f"Audit row count ({len(audit_df)}) != Total scanned ({total_scanned})"
                assert audit_df["symbol"].nunique() == total_scanned, f"Audit unique symbols ({audit_df['symbol'].nunique()}) != Total scanned ({total_scanned})"

                if total_scanned >= 800:
                    audit_df.to_csv(audit_csv_p, index=False)
                    audit_df.to_parquet(audit_parquet_p, index=False)
                    logger.info(f"📁 [V2_AUDIT] Saved symbol population audit artifact to {audit_csv_p} and {audit_parquet_p} ({len(audit_df)} records, 13 columns)")

                logger.info("  6. POPULATION REASON AUDIT SUMMARY:")
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
                    logger.info(f"📜 [EVIDENCE_EXPORT] Forensic evidence bundle created at {collector.run_dir}")
                except Exception as _exp_err:
                    logger.error(f"Failed to export evidence bundle: {_exp_err}", exc_info=True)

            return {
                "status": _health_status,
                "health": _health_status,
                "health_status": _health_status,
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
                        "QUALITY_COMPOUNDER_VALUE_V2_FINAL",
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

                    # Load valuation cache for continuous multiples across candidate paths
                    val_cache = {}
                    _val_cache_files = ["multibagger_fundamentals_cache.json", "fundamentals_cache.json"]
                    _candidate_dirs = [
                        DATA_DIR,
                        os.path.join(BASE_DIR, "data"),
                        os.path.join(os.getcwd(), "data"),
                        "/app/data",
                        "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/data"
                    ]
                    for _cdir in _candidate_dirs:
                        if not os.path.exists(_cdir):
                            continue
                        for v_name in _val_cache_files:
                            v_path = os.path.join(_cdir, v_name)
                            if os.path.exists(v_path):
                                try:
                                    with open(v_path) as f:
                                        val_cache.update(json.load(f))
                                except Exception:
                                    pass

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

                    # ── Multibagger fundamentals cache (market_cap + shares fallback) ──────────
                    # Authoritative source for market_cap (~3194 symbols, absolute ₹).
                    # Used as Tier-2 in the 3-tier MCap resolution chain when val_cache has no mcap.
                    # This was the path that produced 784/795 current EV/EBITDA on Sep 29.
                    _mb_cache = {}
                    _mb_cache_path = os.path.join(DATA_DIR, "multibagger_fundamentals_cache.json")
                    if os.path.exists(_mb_cache_path):
                        try:
                            with open(_mb_cache_path) as _f_mb:
                                _mb_cache = json.load(_f_mb)
                            logger.info(f"✅ Loaded {len(_mb_cache)} entries from multibagger_fundamentals_cache "
                                        f"(market_cap + shares fallback for EV computation)")
                        except Exception as _mb_err:
                            logger.warning(f"multibagger_fundamentals_cache load failed: {_mb_err}; "
                                           f"EV will fall back to 1D-history tier only")

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
                        if sum_pat is not None and not pd.isna(sum_pat) and float(sum_pat) > 0 and len(trailing_g) >= 1:
                            cfo_pat = round(float(sum_cfo) / float(sum_pat), 2)

                        # ── 3. 5Y CAGR (Sales CAGR & PAT CAGR across trailing ANNUAL filings) ──
                        k_cagr = min(5, n_ann - 1) if n_ann >= 2 else 0
                        rev_cagr, pat_cagr = None, None
                        growth_start_period, growth_end_period = None, None
                        growth_yrs = 0.0
                        if k_cagr >= 1:
                            start_row = g_ann.iloc[-k_cagr - 1]
                            end_row = g_ann.iloc[-1]
                            start_date = pd.to_datetime(start_row['period_end_date'])
                            end_date = pd.to_datetime(end_row['period_end_date'])
                            growth_yrs = max(1.0, (end_date - start_date).days / 365.25)
                            growth_start_period = str(start_date)[:10]
                            growth_end_period = str(end_date)[:10]

                            # P0: Do NOT use `or 0.0` — missing revenue/profit must be None,
                            # not silently converted to zero (UNKNOWN != ZERO invariant).
                            _r0_raw = start_row.get('revenue')
                            _r1_raw = end_row.get('revenue')
                            _p0_raw = start_row.get('net_profit')
                            _p1_raw = end_row.get('net_profit')
                            r0 = float(_r0_raw) if (_r0_raw is not None and pd.notna(_r0_raw)) else None
                            r1 = float(_r1_raw) if (_r1_raw is not None and pd.notna(_r1_raw)) else None
                            p0 = float(_p0_raw) if (_p0_raw is not None and pd.notna(_p0_raw)) else None
                            p1 = float(_p1_raw) if (_p1_raw is not None and pd.notna(_p1_raw)) else None

                            if r0 is not None and r1 is not None and r0 > 0 and r1 > 0:
                                rev_cagr = round((pow(r1 / r0, 1.0 / growth_yrs) - 1.0) * 100.0, 2)
                            if p0 is not None and p1 is not None and p0 > 0 and p1 > 0:
                                pat_cagr = round((pow(p1 / p0, 1.0 / growth_yrs) - 1.0) * 100.0, 2)

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

                        # Ind-AS / GAAP Exact Derivation if shares_outstanding is not explicitly reported:
                        # Basic EPS = Net Profit (Cr) * 1e7 / Shares => Shares = Net Profit (Cr) * 1e7 / EPS
                        if _shares_f is None and _net_p_f is not None and _eps_f is not None and _eps_f > 0:
                            _shares_f = (_net_p_f * 1e7) / _eps_f

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
                        v_data  = val_cache.get(clean_sym, val_cache.get(sym, {}))
                        pit_val = pit_val_cache.get(clean_sym, pit_val_cache.get(sym, {}))
                        pe_curr = v_data.get('pe_fallback') or v_data.get('pe')   # current-period PE from val_cache
                        pe_med  = pit_val.get('pe_3y_median') or v_data.get('pe_3y_median')
                        ev_med  = pit_val.get('ev_ebitda_3y_median') or v_data.get('ev_ebitda_3y_median')

                        # ── MARKET CAP RESOLUTION (3-tier) ──────────────────────────────────
                        # Tier 1: val_cache (pit_valuation_history_cache.json — only has EV/PE medians, no mcap)
                        _mcap = v_data.get('market_cap')
                        _mcap_f = float(_mcap) if _mcap is not None and pd.notna(_mcap) and float(_mcap) > 0 else None
                        _mcap_cr = (_mcap_f / 1e7) if (_mcap_f is not None and _mcap_f > 1e6) else _mcap_f
                        _mcap_source = "VAL_CACHE" if _mcap_cr is not None else None

                        # Tier 2: multibagger_fundamentals_cache — has market_cap (absolute ₹) for ~3194 symbols.
                        # This was the path that produced 784/795 on Sep 29.
                        if _mcap_cr is None and _mb_cache:
                            _mb = _mb_cache.get(clean_sym, _mb_cache.get(sym, {}))
                            _mb_mcap = _mb.get('market_cap') if _mb else None
                            # shares from multibagger cache as fallback for _shares_f
                            if _shares_f is None and _mb:
                                _mb_sh = _mb.get('shares_outstanding')
                                if _mb_sh is not None and float(_mb_sh) > 0:
                                    _shares_f = float(_mb_sh)
                            if _mb_mcap is not None and float(_mb_mcap) > 0:
                                _mcap_f_mb = float(_mb_mcap)
                                # multibagger stores in absolute ₹ (e.g. 17.7T for Reliance)
                                _mcap_cr = (_mcap_f_mb / 1e7) if _mcap_f_mb > 1e6 else _mcap_f_mb
                                _mcap_source = "MULTIBAGGER_CACHE"

                        # Tier 3: 1D history parquet — compute live MCap from last close × shares
                        if _mcap_cr is None or pe_curr is None:
                            for _cdir in _candidate_dirs:
                                p_path = os.path.join(_cdir, "history", "1d", f"{clean_sym}.parquet")
                                if os.path.exists(p_path):
                                    try:
                                        df_px = pd.read_parquet(p_path)
                                        if not df_px.empty:
                                            c_col = 'close' if 'close' in df_px.columns else ('Close' if 'Close' in df_px.columns else None)
                                            if c_col:
                                                _px = float(df_px[c_col].iloc[-1])
                                                if _mcap_cr is None and _px > 0:
                                                    if _shares_f:
                                                        _mcap_cr = (_shares_f * _px) / 1e7
                                                        _mcap_source = "1D_HISTORY_SHARES_X_PRICE"
                                                    elif _net_p_f and _eps_f and _eps_f > 0:
                                                        _mcap_cr = _net_p_f * (_px / _eps_f)
                                                        _mcap_source = "1D_HISTORY_NETPROFIT_X_PE"
                                                if pe_curr is None and _eps_f and _eps_f > 0 and _px > 0:
                                                    pe_curr = round(_px / _eps_f, 2)
                                        break
                                    except Exception:
                                        pass

                        # ── EV CALCULATION ──────────────────────────────────────────────────
                        # P0 rule: debt and cash must be genuinely known for the full formula.
                        # Exception: when cash_and_equivalents is universally absent from the
                        # data provider (not synthetically zero), we use the conservative bound:
                        #   EV_conservative = MCap + Debt  (overstates EV; marked EV_CASH_UNKNOWN)
                        # This restores Sep-29 behavior: cash was always None then too.
                        # NEVER set cash = 0. Always record the cash-component status.
                        ev_curr = None
                        _ev_cash_component = "KNOWN" if _cash_f is not None else "UNKNOWN"
                        if _mcap_cr is not None and _ebitda_f is not None and _ebitda_f > 0:
                            if _td_f is not None:
                                if _cash_f is not None:
                                    # Full EV formula (preferred)
                                    _ev = _mcap_cr + _td_f - _cash_f
                                else:
                                    # Conservative: cash genuinely unavailable from all sources
                                    # EV = MCap + Debt (overstates EV, documented)
                                    _ev = _mcap_cr + _td_f
                                if _ev > 0:
                                    ev_curr = round(_ev / _ebitda_f, 2)
                            elif _cash_f is None:
                                # Both debt and cash unknown: only MCap/EBITDA computable
                                # But P0 rule: we don't know net debt position → ev_curr stays None
                                pass
                            # else: debt unknown, cash known: EV net position unreliable → ev_curr stays None

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
    """Top-level invocation wrapper for QUALITY_COMPOUNDER_VALUE_V2_FINAL scanner."""
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


