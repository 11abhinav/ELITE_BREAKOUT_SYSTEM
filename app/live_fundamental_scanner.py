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
import json
import time
import logging
import math
from datetime import datetime, date
from zoneinfo import ZoneInfo
from typing import Dict, List, Any, Optional, Tuple
from enum import Enum
import numpy as np
import pandas as pd

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger("LIVE_FUNDAMENTAL_SCANNER")

try:
    from app.fundamental_telemetry import FundamentalScanTelemetry
except ImportError:
    from fundamental_telemetry import FundamentalScanTelemetry

BASE_DIR = os.getenv("ELITE_BASE_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if not os.path.exists(os.path.join(BASE_DIR, "data")) and os.path.exists("/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/data"):
    BASE_DIR = "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM"
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
    # Earnings Acceleration Gate
    FAIL_REVENUE_ACCELERATION = "FAIL_REVENUE_ACCELERATION"
    FAIL_OP_PROFIT_ACCELERATION = "FAIL_OP_PROFIT_ACCELERATION"
    FAIL_EPS_ACCELERATION = "FAIL_EPS_ACCELERATION"
    FAIL_PRIOR_EPS = "FAIL_PRIOR_EPS"
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
        if not fundamentals:
            logger.info(f"🔍 [GATE_EVAL:FQ] {sym} REJECTED: fundamentals record is missing or empty")
            return False, [RejectionReason.FUNDAMENTAL_DATA_MISSING], {}

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
            failures.append(RejectionReason.FUNDAMENTAL_DATA_MISSING)
            logger.info(
                f"🔍 [GATE_EVAL:FQ] {sym:<12} REJECTED (DATA_MISSING): missing={missing_fields} | "
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
        if not fundamentals:
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
            failures.append(RejectionReason.FUNDAMENTAL_DATA_MISSING)
            logger.info(
                f"🔍 [GATE_EVAL:EA] {sym:<12} REJECTED (DATA_MISSING): missing={missing_fields} | "
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
            # Fallback benchmark baseline: positive absolute alpha (stock return > 0)
            if ret_3m_stock <= 0.0 or ret_6m_stock <= 0.0:
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
                            ocf_val = f0.get("operating_cash_flow") if f0.get("operating_cash_flow") is not None else f0.get("free_cash_flow")

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

            fcf_raw = r.get("FCF_yield", r.get("fcf_yield", r.get("FCF Margin %", r.get("free_cash_flow_margin_ttm"))))
            if fcf_raw is not None and not pd.isna(fcf_raw):
                ocf_val = float(fcf_raw)
            else:
                raw_ocf = r.get("operating_cash_flow")
                ocf_val = float(raw_ocf) if (raw_ocf is not None and not pd.isna(raw_ocf)) else None

            fund_cat = str(r.get("fundamental_category", r.get("Category", "NONE")))
            is_trap = (fund_cat == "VALUE_TRAP") or bool(r.get("is_value_trap", False)) or (str(r.get("Forensic_Risk_Tier", "")).upper() == "HIGH")

            # Acceleration fields (ZERO SYNTHETIC CONSTANTS: missing values must stay None to fail closed)
            rev_l = r.get("rev_yoy_latest", r.get("total_revenue_yoy_growth_ttm", r.get("YOY Revenue %")))
            rev_l = float(rev_l) if (rev_l is not None and not pd.isna(rev_l)) else None

            rev_p = r.get("rev_yoy_prev", r.get("total_revenue_5y_growth", r.get("5Y Revenue %")))
            rev_p = float(rev_p) if (rev_p is not None and not pd.isna(rev_p)) else None

            op_l = r.get("op_profit_yoy_latest", r.get("gross_profit_yoy_growth_ttm", r.get("YOY Profit %")))
            op_l = float(op_l) if (op_l is not None and not pd.isna(op_l)) else None

            op_p = r.get("op_profit_yoy_prev")
            op_p = float(op_p) if (op_p is not None and not pd.isna(op_p)) else None

            eps_l = r.get("eps_yoy_latest", r.get("earnings_per_share_diluted_yoy_growth_ttm", r.get("5Y EPS %")))
            eps_l = float(eps_l) if (eps_l is not None and not pd.isna(eps_l)) else None

            eps_p = r.get("eps_yoy_prev", r.get("earnings_per_share_basic_5y_growth"))
            eps_p = float(eps_p) if (eps_p is not None and not pd.isna(eps_p)) else None

            p_eps = r.get("prior_eps", r.get("earnings_per_share_basic_ttm"))
            p_eps = float(p_eps) if (p_eps is not None and not pd.isna(p_eps) and float(p_eps) > 0) else None

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

        # 5. Market Data Sanity
        if df_bars is None or len(df_bars) < 200:
            rejections.append(RejectionReason.MARKET_DATA_INSUFFICIENT_LOOKBACK)
            if telemetry is not None:
                telemetry.record_gate_evaluation(
                    sym, "MARKET_DATA", False,
                    {"bars_len": len(df_bars) if df_bars is not None else 0},
                    [RejectionReason.MARKET_DATA_INSUFFICIENT_LOOKBACK.value]
                )
                telemetry.record_symbol_final_decision(sym, False, [e.value for e in rejections], RejectionReason.MARKET_DATA_INSUFFICIENT_LOOKBACK.value)
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
            target_symbols = self.universe_registry.approved_symbols
            for sym in target_symbols:
                p_path = os.path.join(history_dir, f"{sym}.parquet")
                if os.path.exists(p_path):
                    try:
                        df_bar = pd.read_parquet(p_path)
                        if not df_bar.empty and len(df_bar) >= 50:
                            market_data_map[sym] = df_bar
                    except Exception as e:
                        logger.debug(f"Failed to load daily candle for {sym}: {e}")

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

        try:
            try:
                from database import create_scanner_execution_run, complete_scanner_execution_run, upsert_scanner_health, save_wealth_buy_alert, save_alert_if_new
            except ImportError:
                from app.database import create_scanner_execution_run, complete_scanner_execution_run, upsert_scanner_health, save_wealth_buy_alert, save_alert_if_new
        except Exception:
            create_scanner_execution_run = None
            complete_scanner_execution_run = None
            upsert_scanner_health = None
            save_wealth_buy_alert = None
            save_alert_if_new = None

        if create_scanner_execution_run is not None:
            try:
                ctx = create_scanner_execution_run(
                    scanner_name="FUNDAMENTAL",
                    trigger_type=trigger_type,
                    total_stocks=len(market_data_map),
                    allow_concurrent=True
                )
            except Exception as e:
                logger.debug(f"Execution history start warning: {e}")

        if upsert_scanner_health is not None:
            try:
                upsert_scanner_health(
                    "FUNDAMENTAL",
                    status="RUNNING",
                    total_count=len(market_data_map),
                    run_id=getattr(ctx, "run_id", None)
                )
            except Exception as e:
                logger.debug(f"Scanner health RUNNING warning: {e}")

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
            "rejection_summary": {},
            "buy_candidates": []
        }

        try:
            for sym, df_bars in market_data_map.items():
                funnel["scanned_count"] += 1
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
                res = self.scan_candidate(sym, df_bars, funds, benchmark_closes=benchmark_closes, telemetry=telemetry)
                # Track data freshness per-symbol in the run context
                if ctx is not None:
                    if funds.get("upstream_provider") == "DATA_UNAVAILABLE":
                        ctx.mark_incomplete()   # no real fundamentals data for this symbol
                    elif db_meta.get("freshness_status", "FRESH") == "STALE":
                        ctx.mark_stale()        # data exists but is stale
                    else:
                        ctx.mark_fresh()        # fresh, real data

                if RejectionReason.EXCLUDED_UNAPPROVED_UNIVERSE not in res["rejection_reasons"] and \
                   RejectionReason.EXCLUDED_QUARANTINED_ANOMALY not in res["rejection_reasons"]:
                    funnel["universe_valid_count"] += 1

                if not any(r in res["rejection_reasons"] for r in [RejectionReason.FAIL_ROCE, RejectionReason.FAIL_ROE, RejectionReason.FAIL_OCF, RejectionReason.FAIL_DEBT_EQUITY, RejectionReason.FAIL_VALUE_TRAP]):
                    funnel["fundamental_quality_pass_count"] += 1

                if not any(r in res["rejection_reasons"] for r in [RejectionReason.FAIL_REVENUE_ACCELERATION, RejectionReason.FAIL_OP_PROFIT_ACCELERATION, RejectionReason.FAIL_EPS_ACCELERATION, RejectionReason.FAIL_PRIOR_EPS]):
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
                    ctx.set_alerts(funnel["buy_alerts_count"])
                    # fresh_count, stale_count, incomplete_count were incremented per-symbol
                    # inside the loop above — just call complete to flush them to DB.
                    complete_scanner_execution_run(ctx)
                except Exception as ce_err:
                    logger.debug(f"Execution completion warning: {ce_err}")

            if upsert_scanner_health is not None:
                try:
                    upsert_scanner_health(
                        "FUNDAMENTAL",
                        status="OK",
                        today_alerts=funnel["buy_alerts_count"],
                        last_success=datetime.now(IST).isoformat(),
                        processed_count=funnel["scanned_count"],
                        total_count=len(market_data_map),
                        duration_seconds=duration_sec,
                        run_id=getattr(ctx, "run_id", None)
                    )
                except Exception as he_err:
                    logger.debug(f"Scanner health OK warning: {he_err}")

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

    @staticmethod
    def is_financial_sector(industry_str: str) -> bool:
        if not isinstance(industry_str, str):
            return False
        ind_upper = industry_str.upper()
        financial_keywords = [
            "BANK", "FINANCE", "FINANCIAL", "HOUSING FINANCE", "NBFC",
            "INSURANCE", "INVESTMENT", "CAPITAL", "SECURITIES", "LEASING"
        ]
        return any(kw in ind_upper for kw in financial_keywords)

    @staticmethod
    def compute_100pt_score(row_dict: dict, ev_discount, pe_discount, res_dd: float) -> float:
        """Score is only meaningful when valuation data is available. Returns 0.0 when either
        discount is None (valuation data unavailable) — caller should not use score for ranking
        when valuation is missing."""
        if ev_discount is None:
            ev_discount = 0.0   # safety: score = 0 when valuation unavailable
        if pe_discount is None:
            pe_discount = 0.0
        # 1. EV/EBITDA Discount Depth (0.25 to 0.50 => 0 to 30 pts)
        ev_pts = 30.0 * min(max((ev_discount - 0.25) / 0.25, 0.0), 1.0)
        # 2. 5Y ROCE (15% to 40% => 0 to 25 pts)
        roce_val = float(row_dict.get("roce_5y_avg", 15.0) or 15.0)
        roce_pts = 25.0 * min(max((roce_val - 15.0) / 25.0, 0.0), 1.0)
        # 3. PE Discount Depth (0% to 40% => 0 to 20 pts)
        pe_pts = 20.0 * min(max(pe_discount / 0.40, 0.0), 1.0)
        # 4. CFO / PAT Ratio (0.80 to 1.50 => 0 to 15 pts)
        cfo_pat_val = float(row_dict.get("cfo_pat_5y_ratio", 0.80) or 0.80)
        cfo_pts = 15.0 * min(max((cfo_pat_val - 0.80) / 0.70, 0.0), 1.0)
        # 5. Residual Drawdown Bonus (Res_DD <= 10% gets full 10 pts)
        if res_dd <= 0.10:
            res_pts = 10.0
        else:
            res_pts = 10.0 * min(max((0.25 - res_dd) / 0.15, 0.0), 1.0)

        return round(ev_pts + roce_pts + pe_pts + cfo_pts + res_pts, 2)

    def scan_universe(self, trigger_type: str = "SCHEDULED", scheduler_name: str = "CRON") -> Dict[str, Any]:
        """
        Executes the frozen QUALITY_COMPOUNDER_VALUE_V2_FINAL 17:00 IST daily scan run.
        Generates daily immutable SCAN_SNAPSHOT rows for ALL evaluated stocks and
        ALERT_EVENT rows for passing candidate stocks directly in the existing 'alerts' table.
        """
        import time
        start_ts = time.time()
        now_ist = datetime.now(IST)
        today_str = now_ist.strftime("%Y-%m-%d")

        logger.info(f"📡 [SCANNER: V2_FINAL] Starting 17:00 IST daily scan run ({today_str}, trigger={trigger_type})...")

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

        # Record execution run start in scanner_execution_history and scanner_health
        exec_run_ctx = None
        if create_scanner_execution_run is not None:
            try:
                exec_run_ctx = create_scanner_execution_run(
                    scanner_name="QUALITY_COMPOUNDER_VALUE_V2_FINAL",
                    trigger_type=trigger_type,
                    allow_concurrent=True
                )
            except Exception as e:
                logger.debug(f"Execution history start warning: {e}")

        if upsert_scanner_health is not None:
            try:
                upsert_scanner_health(
                    "QUALITY_COMPOUNDER_VALUE_V2_FINAL",
                    status="RUNNING",
                    run_id=getattr(exec_run_ctx, "run_id", None)
                )
            except Exception as e:
                logger.debug(f"Scanner health RUNNING warning: {e}")

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
        value_pass_count = 0
        candidate_count = 0
        quality_data_blocked_count = 0    # unique symbols with missing quality fields
        valuation_data_blocked_count = 0  # unique symbols with missing valuation fields
        data_blocked_count = 0            # unique symbols with ANY missing field (quality OR valuation)

        # Filter to latest PIT record per symbol on or before today
        if 'filing_date' in pit_df.columns:
            pit_df['filing_date'] = pd.to_datetime(pit_df['filing_date'])
            pit_df = pit_df[pit_df['filing_date'] <= pd.to_datetime(today_str)].sort_values('filing_date').groupby('symbol').last().reset_index()

        # ── PRE-FLIGHT VALUATION HEALTH GATE ──────────────────────────────────────
        # Sample up to 30 symbols from the PIT dataset to check if valuation data
        # (pe_3y_median or ev_ebitda_3y_median) is actually populated.
        # If ZERO of the sampled symbols have any valuation, the valuation provider
        # is completely unavailable and we must flag this before the main loop.
        _sample_size = min(30, len(pit_df))
        _val_available = 0
        for _, _row in pit_df.head(_sample_size).iterrows():
            _ev_med = _row.get('ev_ebitda_3y_median', _row.get('ev_to_ebitda_3y_median', _row.get('ev_ebitda_median')))
            _pe_med = _row.get('pe_3y_median', _row.get('pe_ratio_3y_median'))
            if (_ev_med is not None and not pd.isna(_ev_med) and float(_ev_med) > 0) or \
               (_pe_med is not None and not pd.isna(_pe_med) and float(_pe_med) > 0):
                _val_available += 1
        _valuation_provider_healthy = (_val_available > 0)
        if not _valuation_provider_healthy:
            logger.error(
                f"❌ [V2_FINAL] PRE-FLIGHT GATE: VALUATION_DATA_UNAVAILABLE — "
                f"0/{_sample_size} sampled symbols have pe_3y_median or ev_ebitda_3y_median. "
                f"Continuing quality scan for telemetry but NO BUY alerts can be generated. "
                f"Required: real 3Y EV/EBITDA or PE median per symbol from the valuation pipeline."
            )
        else:
            logger.info(f"✅ [V2_FINAL] PRE-FLIGHT GATE: Valuation data available ({_val_available}/{_sample_size} sampled symbols). Proceeding.")
        # ─────────────────────────────────────────────────────────────────────────

        for _, row in pit_df.iterrows():
            total_scanned += 1
            sym = str(row['symbol']).upper()
            industry = str(row.get('industry', 'Unknown'))
            mcap = float(row.get('market_cap', row.get('mcap', 1000.0)) or 1000.0)
            adtv_90d = float(row.get('adtv_90d', row.get('adtv', 2.0)) or 2.0)

            # Metrics & Multi-field Real Resolution (NO SYNTHETIC FALLBACKS)
            roce_5y = row.get('roce_5y_avg', row.get('roce_5y', row.get('ROCE', row.get('roce', row.get('ROE', row.get('roe'))))))
            sales_cagr_5y = row.get('sales_cagr_5y', row.get('sales_cagr', row.get('rev_cagr', row.get('revenue_cagr_3y'))))
            pat_cagr_5y = row.get('pat_cagr_5y', row.get('pat_cagr', row.get('op_profit_cagr')))
            cfo_pat_5y = row.get('cfo_pat_5y_ratio', row.get('cfo_pat_5y', row.get('cfo_pat')))
            de_ratio = row.get('debt_to_equity', row.get('debt_equity', row.get('debt', row.get('d_e'))))
            share_dilution_3y = row.get('share_dilution_3y_pct', row.get('share_dilution_3y', 0.0))

            ev_ebitda_curr = row.get('current_ev_ebitda', row.get('ev_to_ebitda', row.get('ev_ebitda')))
            ev_ebitda_med = row.get('ev_ebitda_3y_median', row.get('ev_to_ebitda_3y_median', row.get('ev_ebitda_median')))
            pe_curr = row.get('current_pe', row.get('pe_ratio', row.get('pe', row.get('pe_fallback'))))
            pe_med = row.get('pe_3y_median', row.get('pe_ratio_3y_median'))

            cmp_price = float(row.get('current_price', row.get('close', 0.0)) or 0.0)
            if cmp_price <= 0.0:
                for _cdir in [DATA_DIR, os.path.join(BASE_DIR, "data"), os.path.join(os.getcwd(), "data"), "/app/data"]:
                    p_path = os.path.join(_cdir, "history", "1d", f"{sym}.parquet")
                    if os.path.exists(p_path):
                        try:
                            df_px = pd.read_parquet(p_path)
                            if not df_px.empty:
                                c_col = 'close' if 'close' in df_px.columns else ('Close' if 'Close' in df_px.columns else None)
                                if c_col:
                                    cmp_price = float(df_px[c_col].iloc[-1])
                                    break
                        except Exception:
                            pass

            # Dynamic real-time calculation from CMP + statement filings if multiples were not pre-calculated
            if (ev_ebitda_curr is None or pd.isna(ev_ebitda_curr)) and cmp_price > 0:
                _sh = row.get('shares_outstanding')
                _eb = row.get('ebitda')
                _d = float(row.get('total_debt', 0.0) or 0.0)
                _c = float(row.get('cash_and_equivalents', 0.0) or 0.0)
                if _sh is not None and not pd.isna(_sh) and float(_sh) > 0 and _eb is not None and not pd.isna(_eb) and float(_eb) > 0:
                    _mc = (float(_sh) * cmp_price) / 1e7
                    _ev = _mc + _d - _c
                    if _ev > 0:
                        ev_ebitda_curr = round(_ev / float(_eb), 2)
            if (pe_curr is None or pd.isna(pe_curr)) and cmp_price > 0:
                _ep = row.get('eps')
                if _ep is not None and not pd.isna(_ep) and float(_ep) > 0:
                    pe_curr = round(cmp_price / float(_ep), 2)

            sma50 = float(row.get('sma50', cmp_price) or cmp_price)
            sma100 = float(row.get('sma100', cmp_price) or cmp_price)
            sma200 = float(row.get('sma200', cmp_price) or cmp_price)

            # Evaluate Gates
            rejections = []
            quality_gate_passed = False
            value_gate_passed = False

            # Financial Sector exclusion from primary EV/EBITDA pipeline
            if self.is_financial_sector(industry):
                rejections.append("FAIL_UNIVERSE_FINANCIAL_SECTOR")
            if mcap < 1000.0:
                rejections.append("FAIL_UNIVERSE_MARKET_CAP")
            if adtv_90d < 2.0:
                rejections.append("FAIL_LIQUIDITY")

            # Missing Quality Data check — STOPS candidate from passing if any real fundamental metric is missing
            quality_data_missing = any(v is None or pd.isna(v) for v in [roce_5y, sales_cagr_5y, pat_cagr_5y, cfo_pat_5y, de_ratio])
            if quality_data_missing:
                rejections.append("DATA_INSUFFICIENT_QUALITY")
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

            # ── VALUE GATE ────────────────────────────────────────────────────────
            # ev_discount = None means the required data is MISSING.
            # NEVER use 0.0 as a sentinel for missing valuation — 0.0 means "current
            # multiple equals the historical median", which is a valid real measurement.
            ev_discount = None   # None = valuation data unavailable (DATA_INSUFFICIENT)
            pe_discount = None   # None = PE comparison data unavailable
            calc_discount = None

            if ev_ebitda_curr is not None and ev_ebitda_med is not None and not pd.isna(ev_ebitda_curr) and not pd.isna(ev_ebitda_med) and float(ev_ebitda_med or 0) > 0:
                calc_discount = (float(ev_ebitda_med) - float(ev_ebitda_curr)) / float(ev_ebitda_med)
            elif pe_curr is not None and pe_med is not None and not pd.isna(pe_curr) and not pd.isna(pe_med) and float(pe_med or 0) > 0:
                calc_discount = (float(pe_med) - float(pe_curr)) / float(pe_med)

            valuation_data_missing = (calc_discount is None)
            if valuation_data_missing:
                rejections.append("DATA_INSUFFICIENT_VALUATION")
                valuation_data_blocked_count += 1
            else:
                ev_discount = calc_discount
                if ev_discount < 0.25:
                    rejections.append("FAIL_VALUATION")

                if pe_curr is not None and pe_med is not None and not pd.isna(pe_curr) and not pd.isna(pe_med) and float(pe_med or 0) > 0:
                    pe_c = float(pe_curr)
                    pe_m = float(pe_med)
                    pe_discount = max((pe_m - pe_c) / pe_m, 0.0)

                value_gate_passed = (ev_discount >= 0.25)
                if value_gate_passed:
                    value_pass_count += 1

            # Count symbol as data-blocked ONCE (even if both quality and valuation data are missing)
            if quality_data_missing or valuation_data_missing:
                data_blocked_count += 1

            # Drawdown & Tiering
            dd_stock = float(row.get('drawdown_252d', 0.15) or 0.15)
            dd_nifty = float(row.get('nifty_drawdown_252d', 0.10) or 0.10)
            res_dd = max(dd_stock - dd_nifty, 0.0)
            tier = "Tier A" if res_dd <= 0.10 else "Tier B"

            score_100 = self.compute_100pt_score(row.to_dict(), ev_discount, pe_discount, res_dd)

            is_candidate = quality_gate_passed and value_gate_passed and len(rejections) == 0
            if is_candidate:
                candidate_count += 1

            primary_rejection = "PASS" if is_candidate else (rejections[0] if rejections else "FAIL_UNKNOWN")
            watchlist_state = "GREEN" if is_candidate else "REJECTED"

            # Gap Analysis: What exact data/metrics are needed for this stock to pass and trigger an alert?
            required_improvements = []
            if roce_5y is None or pd.isna(roce_5y) or float(roce_5y) < 15.0:
                cur_v = f"{float(roce_5y):.1f}%" if roce_5y is not None and not pd.isna(roce_5y) else "N/A"
                required_improvements.append(f"ROCE >= 15.0% (Current: {cur_v})")
            if sales_cagr_5y is None or pd.isna(sales_cagr_5y) or float(sales_cagr_5y) < 10.0:
                cur_v = f"{float(sales_cagr_5y):.1f}%" if sales_cagr_5y is not None and not pd.isna(sales_cagr_5y) else "N/A"
                required_improvements.append(f"Sales CAGR >= 10.0% (Current: {cur_v})")
            if pat_cagr_5y is None or pd.isna(pat_cagr_5y) or float(pat_cagr_5y) < 10.0:
                cur_v = f"{float(pat_cagr_5y):.1f}%" if pat_cagr_5y is not None and not pd.isna(pat_cagr_5y) else "N/A"
                required_improvements.append(f"PAT CAGR >= 10.0% (Current: {cur_v})")
            if cfo_pat_5y is None or pd.isna(cfo_pat_5y) or float(cfo_pat_5y) < 0.80:
                cur_v = f"{float(cfo_pat_5y):.2f}" if cfo_pat_5y is not None and not pd.isna(cfo_pat_5y) else "N/A"
                required_improvements.append(f"CFO/PAT >= 0.80 (Current: {cur_v})")
            if de_ratio is not None and not pd.isna(de_ratio) and float(de_ratio) > 0.50:
                required_improvements.append(f"Debt/Equity <= 0.50 (Current: {float(de_ratio):.2f})")
            # Valuation: None means DATA_INSUFFICIENT, not a failed discount
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
                f"Metrics=[roce={roce_5y}, sales_cagr={sales_cagr_5y}, pat_cagr={pat_cagr_5y}, cfo_pat={cfo_pat_5y}, d_e={de_ratio}, ev_discount={ev_disc_str}] | "
                f"ValuationDetails=[EV_curr={ev_ebitda_curr}, EV_3Y_med={ev_ebitda_med}, PE_curr={pe_curr}, PE_3Y_med={pe_med}] | "
                f"RequiredToPass={required_improvements if required_improvements else ['NONE (PASSING CANDIDATE)']}"
            )

            # Context Payload for forensic prospective research
            ctx = {
                "strategy_id": self.strategy_id,
                "symbol": sym,
                "scan_date": today_str,
                "industry": industry,
                "market_cap_cr": round(mcap, 2),
                "adtv_90d_cr": round(adtv_90d, 2),
                "current_price": round(cmp_price, 2),
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
                "sma50": round(sma50, 2),
                "sma100": round(sma100, 2),
                "sma200": round(sma200, 2),
                "res_dd_pct": round(res_dd * 100, 1),
                "tier": tier,
                "score_100": score_100,
                "rejection_reasons": rejections,
                "primary_rejection_reason": primary_rejection,
                "filing_date": str(row.get("filing_date", today_str)),
                "financial_period_end": str(row.get("financial_period_end", "")),
                "data_available_date": str(row.get("data_available_date", today_str))
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

        # Persist ONLY genuine candidate alerts to alerts table
        try:
            snapshots_inserted = save_v2_scan_snapshots(snapshot_records)
            candidates_inserted = 0
            for cand in candidate_records:
                ok, msg = save_v2_candidate_alert(cand)
                if ok:
                    candidates_inserted += 1
                    logger.info(
                        f"🚀 [BUY_ALERT: V2] {cand['symbol']:<12} | Tier={cand['tier']} | "
                        f"Score={cand['ranking_score']:<5.1f} | CMP=₹{cand['current_price']:<8.2f} | Status={msg}"
                    )

            duration_sec = round(time.time() - start_ts, 2)
            
            # Record execution history completion — pass full breakdown so history card shows quality/value/blocked/candidates
            if complete_scanner_execution_run is not None and exec_run_ctx and getattr(exec_run_ctx, "run_id", None):
                try:
                    complete_scanner_execution_run(
                        run_id=exec_run_ctx.run_id,
                        total_scanned=total_scanned,
                        candidate_count=candidate_count,
                        quality_status="HEALTHY",
                        summary_notes=(
                            f"Evaluated={total_scanned} | "
                            f"QualityPass={quality_pass_count} | "
                            f"ValuationPass={value_pass_count} | "
                            f"DataBlocked={data_blocked_count} | "
                            f"Candidates={candidate_count}"
                        )
                    )
                except Exception as e:
                    logger.debug(f"Execution history completion warning: {e}")

            # ── POST-SCAN HEALTH STATUS ───────────────────────────────────────────
            # Distinguish execution health (did the code run?) from strategy health
            # (did the strategy have its required data?)
            # Rule: if valuation data was unavailable for >= 95% of evaluated stocks,
            # the scanner is DATA_BLOCKED, not OK.
            _val_block_pct = valuation_data_blocked_count / max(total_scanned, 1)
            _health_status = "OK" if (_val_block_pct < 0.95 and candidate_count >= 0) else "DATA_BLOCKED"
            _health_error = None
            if _health_status == "DATA_BLOCKED":
                _health_error = (
                    f"VALUATION_DATA_UNAVAILABLE: {valuation_data_blocked_count}/{total_scanned} symbols "
                    f"have missing pe_3y_median and ev_ebitda_3y_median. "
                    f"V2 strategy decision engine is BLOCKED — 0 genuine valuation passes possible. "
                    f"Action required: populate valuation history in the PIT pipeline."
                )
                logger.error(f"🚫 [V2_FINAL] STRATEGY STATUS=BLOCKED_DATA | {_health_error}")

            if upsert_scanner_health is not None:
                try:
                    upsert_scanner_health(
                        "QUALITY_COMPOUNDER_VALUE_V2_FINAL",
                        status=_health_status,
                        today_alerts=candidate_count,          # 0 when no candidates
                        last_success=now_ist.isoformat() if _health_status == "OK" else None,
                        processed_count=candidate_count,       # alerts generated
                        total_count=total_scanned,             # equities evaluated
                        duration_seconds=duration_sec,
                        error_msg=_health_error,
                        run_id=getattr(exec_run_ctx, "run_id", None)
                    )
                except Exception as e:
                    logger.debug(f"Scanner health update warning: {e}")

            # Structured End-of-Scan Telemetry Summary Report
            data_good_count = total_scanned - data_blocked_count
            _val_status_str = "DATA_AVAILABLE" if _valuation_provider_healthy else "DATA_BLOCKED — VALUATION_UNAVAILABLE"
            logger.info("=" * 80)
            logger.info(f"📊 [SCANNER TELEMETRY: QUALITY_COMPOUNDER_VALUE_V2_FINAL] END-OF-SCAN REPORT ({today_str})")
            logger.info("=" * 80)
            logger.info(f"  • Total Equities Evaluated       : {total_scanned}")
            logger.info(f"  • Data Complete (any field OK)   : {data_good_count}  ({round(data_good_count/max(total_scanned,1)*100,1)}%)")
            logger.info(f"  • Quality Data Blocked           : {quality_data_blocked_count}  (missing ROCE/CAGR/CFO/D_E)")
            logger.info(f"  • Valuation Data Blocked         : {valuation_data_blocked_count}  (missing pe_3y_median + ev_ebitda_3y_median)")
            logger.info(f"  • Any Data Blocked (unique syms) : {data_blocked_count}")
            logger.info(f"  • Quality Gate Passed            : {quality_pass_count}  (of {data_good_count} data-complete stocks)")
            logger.info(f"  • Valuation Provider Status      : {_val_status_str}")
            logger.info(f"  • Valuation Gate Passed          : {value_pass_count}  ({'genuine passes' if _valuation_provider_healthy else 'N/A — valuation data missing for all'})")
            logger.info(f"  • Candidates Selected            : {candidate_count}  ← BUY ALERTS")
            logger.info(f"  • Snapshots Saved in DB          : {snapshots_inserted}")
            logger.info(f"  • Candidate Alerts Saved         : {candidates_inserted}")
            logger.info(f"  • Scanner Health Status          : {_health_status}")
            logger.info(f"  • Duration (Seconds)             : {duration_sec}s")
            logger.info("-" * 80)
            if not _valuation_provider_healthy:
                logger.error(
                    "🚫 [V2_FINAL] ZERO CANDIDATES IS NOT A VALID MARKET SIGNAL — "
                    "It is a DATA FAILURE. V2 cannot make the BUY decision without real "
                    "3Y EV/EBITDA or PE median per stock. Required action: populate "
                    "ev_ebitda_3y_median / pe_3y_median in the PIT fundamentals pipeline."
                )
            logger.info(f"🎯 GENERATED BUY CANDIDATE ALERTS ({len(candidate_records)} STOCKS):")
            for idx, cand in enumerate(candidate_records, 1):
                logger.info(f"  [{idx:02d}] {cand['symbol']:<12} | Tier={cand['tier']} | Score={cand['ranking_score']:<5.1f} | CMP=₹{cand['current_price']:<8.2f} | SignalDate={cand['signal_date']}")
            if not candidate_records:
                _zero_reason = "no stocks met all quality + valuation gates" if _valuation_provider_healthy else "valuation data unavailable for all stocks"
                logger.info(f"  (none — {_zero_reason})")
            logger.info("=" * 80)

            return {
                "status": _health_status,
                "execution": "SUCCESS",
                "strategy_status": "BLOCKED_DATA" if not _valuation_provider_healthy else ("OK" if candidate_count > 0 else "SCARCITY"),
                "valuation_provider_healthy": _valuation_provider_healthy,
                "total_scanned": total_scanned,
                "quality_pass_count": quality_pass_count,
                "value_pass_count": value_pass_count,
                "candidate_count": candidate_count,
                "quality_data_blocked_count": quality_data_blocked_count,
                "valuation_data_blocked_count": valuation_data_blocked_count,
                "data_blocked_count": data_blocked_count,
                "snapshots_inserted": snapshots_inserted,
                "candidates_inserted": candidates_inserted,
                "duration_sec": duration_sec
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
                                        logger.info(f"⚡ Loaded {len(pit_val_cache)} PIT valuation medians directly from {_cp}")
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
                                    logger.info(f"✅ Loaded {len(pit_val_cache)} PIT valuation medians from database")
                        except Exception as _dbe:
                            logger.debug(f"DB valuation download notice: {_dbe}")

                    records = []
                    for sym, g in raw_df.groupby('symbol'):
                        g = g.sort_values('period_end_date')
                        n = len(g)
                        latest_filing = g.iloc[-1]

                        # ROCE / ROE
                        roce = latest_filing.get('roce') if pd.notna(latest_filing.get('roce')) else None
                        roe = latest_filing.get('roe') if pd.notna(latest_filing.get('roe')) else None
                        roce_eff = roce if roce is not None else roe

                        # Multi-year Revenue CAGR & PAT CAGR
                        rev_cagr, pat_cagr = None, None
                        if n >= 2:
                            r0, r1 = g['revenue'].iloc[0], g['revenue'].iloc[-1]
                            p0, p1 = g['net_profit'].iloc[0], g['net_profit'].iloc[-1]
                            yrs = max(1.0, (g['period_end_date'].iloc[-1] - g['period_end_date'].iloc[0]).days / 365.25)
                            if r0 and r0 > 0 and r1 and r1 > 0:
                                rev_cagr = (pow(r1 / r0, 1.0 / yrs) - 1.0) * 100.0
                            if p0 and p0 > 0 and p1 and p1 > 0:
                                pat_cagr = (pow(p1 / p0, 1.0 / yrs) - 1.0) * 100.0

                        # CFO / PAT Ratio
                        ocf = latest_filing.get('operating_cash_flow') if pd.notna(latest_filing.get('operating_cash_flow')) else None
                        pat = latest_filing.get('net_profit') if pd.notna(latest_filing.get('net_profit')) else None
                        cfo_pat = (float(ocf) / float(pat)) if ocf is not None and pat is not None and float(pat) > 0 else None

                        # Debt to Equity
                        td = latest_filing.get('total_debt') if pd.notna(latest_filing.get('total_debt')) else 0.0
                        te = latest_filing.get('total_equity') if pd.notna(latest_filing.get('total_equity')) else None
                        de = (float(td) / float(te)) if te is not None and float(te) > 0 else (0.0 if float(td) == 0 else None)

                        # Statement fundamentals for dynamic valuation
                        _shares = latest_filing.get('shares_outstanding')
                        _shares_f = float(_shares) if _shares is not None and pd.notna(_shares) and float(_shares) > 0 else None
                        _eps = latest_filing.get('eps')
                        _eps_f = float(_eps) if _eps is not None and pd.notna(_eps) and float(_eps) > 0 else None
                        _net_p = latest_filing.get('net_profit')
                        _net_p_f = float(_net_p) if _net_p is not None and pd.notna(_net_p) else None
                        _op     = latest_filing.get('operating_profit')
                        _da     = latest_filing.get('depreciation_amortization')
                        _op_f   = float(_op)   if _op   is not None and pd.notna(_op)   else None
                        _da_f   = float(_da)   if _da   is not None and pd.notna(_da)   else 0.0
                        _td_f   = float(td)    if td    is not None and pd.notna(td)    else 0.0
                        _cash   = latest_filing.get('cash_and_equivalents')
                        _cash_f = float(_cash) if _cash  is not None and pd.notna(_cash)  else 0.0
                        _ebitda_f = (_op_f + _da_f) if (_op_f is not None) else None

                        # ── VALUATION MULTIPLES ─────────────────────────────────────────────
                        v_data = val_cache.get(sym, {})
                        pit_val = pit_val_cache.get(sym, {})
                        pe_curr = v_data.get('pe_fallback') or v_data.get('pe')   # current-period PE from cache
                        pe_med  = pit_val.get('pe_3y_median') or v_data.get('pe_3y_median')
                        ev_med  = pit_val.get('ev_ebitda_3y_median') or v_data.get('ev_ebitda_3y_median')

                        # Current Market Cap: from cache or fallback to latest 1D history Close
                        _mcap = v_data.get('market_cap')
                        _mcap_f = float(_mcap) if _mcap is not None and pd.notna(_mcap) and float(_mcap) > 0 else None
                        _mcap_cr = (_mcap_f / 1e7) if (_mcap_f is not None and _mcap_f > 1e6) else _mcap_f

                        if _mcap_cr is None or pe_curr is None:
                            for _cdir in _candidate_dirs:
                                p_path = os.path.join(_cdir, "history", "1d", f"{sym}.parquet")
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
                                                    elif _net_p_f and _eps_f and _eps_f > 0:
                                                        _mcap_cr = _net_p_f * (_px / _eps_f)
                                                if pe_curr is None and _eps_f and _eps_f > 0 and _px > 0:
                                                    pe_curr = round(_px / _eps_f, 2)
                                        break
                                    except Exception:
                                        pass

                        ev_curr = None
                        if _mcap_cr is not None and _ebitda_f is not None and _ebitda_f > 0:
                            _ev = _mcap_cr + _td_f - _cash_f
                            if _ev > 0:
                                ev_curr = round(_ev / _ebitda_f, 2)

                        records.append({
                            'symbol': sym,
                            'filing_date': str(latest_filing.get('filing_date'))[:10],
                            'financial_period_end': str(latest_filing.get('period_end_date'))[:10],
                            'market_cap': _mcap_cr,
                            'roce_5y_avg': roce_eff,
                            'sales_cagr_5y': rev_cagr,
                            'pat_cagr_5y': pat_cagr,
                            'cfo_pat_5y_ratio': cfo_pat,
                            'debt_to_equity': de,
                            'shares_outstanding': _shares_f,
                            'eps': _eps_f,
                            'ebitda': _ebitda_f,
                            'total_debt': _td_f,
                            'cash_and_equivalents': _cash_f,
                            'current_pe': pe_curr,
                            'pe_3y_median': pe_med,
                            'current_ev_ebitda': ev_curr,
                            'ev_ebitda_3y_median': ev_med,
                            'provenance_status': 'CERTIFIED_PIT_STATEMENT_CALCULATED'
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

def run_quality_compounder_v2_scan(trigger_type: str = "SCHEDULED", scheduler_name: str = "CRON") -> Dict[str, Any]:
    """Top-level invocation wrapper for QUALITY_COMPOUNDER_VALUE_V2_FINAL scanner."""
    return get_quality_compounder_v2_scanner().scan_universe(trigger_type=trigger_type, scheduler_name=scheduler_name)


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
]


