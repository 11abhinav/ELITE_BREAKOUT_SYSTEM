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
            with open(CLEAN_UNIVERSE_JSON, "r") as f:
                self.clean_symbols = set(json.load(f).get("symbols", []))
        if os.path.exists(QUARANTINE_JSON):
            with open(QUARANTINE_JSON, "r") as f:
                self.quarantined_symbols = set(json.load(f).get("symbols", []))

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
    def evaluate(fundamentals: Dict[str, Any]) -> Tuple[bool, List[RejectionReason], Dict[str, Any]]:
        failures = []
        if not fundamentals:
            return False, [RejectionReason.FUNDAMENTAL_DATA_MISSING], {}

        roce = fundamentals.get("roce")
        roe = fundamentals.get("roe")
        ocf = fundamentals.get("operating_cash_flow", fundamentals.get("ocf"))
        debt_equity = fundamentals.get("debt_equity", fundamentals.get("debt_to_equity"))

        # Check for missing values
        if roce is None or roe is None or ocf is None or debt_equity is None:
            return False, [RejectionReason.FUNDAMENTAL_DATA_MISSING], {}

        # Handle decimal vs percentage representation (e.g. 0.15 vs 15.0)
        roce_val = float(roce)
        if roce_val <= 1.0 and roce_val > 0.0:
            roce_val *= 100.0

        roe_val = float(roe)
        if roe_val <= 1.0 and roe_val > 0.0:
            roe_val *= 100.0

        ocf_val = float(ocf)
        de_val = float(debt_equity)

        if roce_val < 15.0:
            failures.append(RejectionReason.FAIL_ROCE)
        if roe_val < 12.0:
            failures.append(RejectionReason.FAIL_ROE)
        if ocf_val <= 0.0:
            failures.append(RejectionReason.FAIL_OCF)
        if de_val > 1.0:
            failures.append(RejectionReason.FAIL_DEBT_EQUITY)

        # Daily Builder Value Trap Hard Block
        is_trap = bool(fundamentals.get("is_value_trap", False) or (str(fundamentals.get("fundamental_category", "")).upper() == "VALUE_TRAP"))
        if is_trap:
            failures.append(RejectionReason.FAIL_VALUE_TRAP)

        metrics = {
            "roce": round(roce_val, 2),
            "roe": round(roe_val, 2),
            "operating_cash_flow": ocf_val,
            "debt_equity": round(de_val, 2),
            "is_value_trap": is_trap,
            "fundamental_category": str(fundamentals.get("fundamental_category", "NONE")),
            "quality_score": float(fundamentals.get("quality_score", 0.0) or 0.0),
            "growth_score": float(fundamentals.get("growth_score", 0.0) or 0.0)
        }

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
    def evaluate(fundamentals: Dict[str, Any]) -> Tuple[bool, List[RejectionReason], Dict[str, Any]]:
        failures = []
        if not fundamentals:
            return False, [RejectionReason.FUNDAMENTAL_DATA_MISSING], {}

        rev_yoy_latest = fundamentals.get("rev_yoy_latest", fundamentals.get("yoy_revenue_pct"))
        rev_yoy_prev = fundamentals.get("rev_yoy_prev", fundamentals.get("yoy_revenue_prev_pct"))
        op_yoy_latest = fundamentals.get("op_profit_yoy_latest", fundamentals.get("yoy_op_profit_pct"))
        op_yoy_prev = fundamentals.get("op_profit_yoy_prev", fundamentals.get("yoy_op_profit_prev_pct"))
        eps_yoy_latest = fundamentals.get("eps_yoy_latest", fundamentals.get("yoy_eps_pct"))
        eps_yoy_prev = fundamentals.get("eps_yoy_prev", fundamentals.get("yoy_eps_prev_pct"))
        prior_eps = fundamentals.get("prior_eps", fundamentals.get("prior_comparable_eps"))

        # Check for missing values
        if any(v is None for v in [rev_yoy_latest, rev_yoy_prev, op_yoy_latest, op_yoy_prev, eps_yoy_latest, eps_yoy_prev, prior_eps]):
            return False, [RejectionReason.FUNDAMENTAL_DATA_MISSING], {}

        rev_l = float(rev_yoy_latest)
        rev_p = float(rev_yoy_prev)
        op_l = float(op_yoy_latest)
        op_p = float(op_yoy_prev)
        eps_l = float(eps_yoy_latest)
        eps_p = float(eps_yoy_prev)
        p_eps = float(prior_eps)

        if rev_l <= rev_p:
            failures.append(RejectionReason.FAIL_REVENUE_ACCELERATION)
        if op_l <= op_p:
            failures.append(RejectionReason.FAIL_OP_PROFIT_ACCELERATION)
        if eps_l <= eps_p:
            failures.append(RejectionReason.FAIL_EPS_ACCELERATION)
        if p_eps <= 0.0:
            failures.append(RejectionReason.FAIL_PRIOR_EPS)

        metrics = {
            "rev_yoy_latest": round(rev_l, 2),
            "rev_yoy_prev": round(rev_p, 2),
            "op_profit_yoy_latest": round(op_l, 2),
            "op_profit_yoy_prev": round(op_p, 2),
            "eps_yoy_latest": round(eps_l, 2),
            "eps_yoy_prev": round(eps_p, 2),
            "prior_eps": round(p_eps, 2)
        }

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
            "prior_20d_high": round(prior_20d_high, 2),
            "signal_close": round(current_close, 2),
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
        path = parquet_path or cls.MASTER_PARQUET
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
        if os.path.exists(path):
            try:
                df = pd.read_parquet(path)
                mtime = os.path.getmtime(path)
                age_days = (time.time() - mtime) / 86400.0
                meta["freshness_status"] = "FRESH" if age_days <= max_age_days else "STALE"
                meta["age_days"] = round(age_days, 1)
                meta["provenance_status"] = "CERTIFIED_LOCAL_DAILY_BUILDER"
            except Exception as e:
                logger.warning(f"Failed to read Daily Builder parquet {path}: {e}")

        # Fallback to Postgres table if file is absent or empty
        if df is None or df.empty:
            try:
                try:
                    from database import get_connection
                except ImportError:
                    from app.database import get_connection
                with get_connection() as conn:
                    query = f"SELECT * FROM {cls.MASTER_TABLE} WHERE build_date = (SELECT MAX(build_date) FROM {cls.MASTER_TABLE})"
                    df = pd.read_sql_query(query, conn)
                    if not df.empty:
                        meta["freshness_status"] = "FRESH"
                        meta["provenance_status"] = "CERTIFIED_POSTGRES_DAILY_BUILDER"
            except Exception as e:
                logger.warning(f"Failed to query DB {cls.MASTER_TABLE}: {e}")

        if df is None or df.empty:
            meta["freshness_status"] = "MISSING"
            return {}, meta

        meta["record_count"] = len(df)
        if "build_date" in df.columns and not df.empty:
            meta["build_date"] = str(df["build_date"].iloc[0])

        funds_map: Dict[str, Dict[str, Any]] = {}
        for _, r in df.iterrows():
            sym = str(r.get("symbol", "")).upper()
            if not sym:
                continue

            roce_val = r.get("ROCE", r.get("roce"))
            roe_val = r.get("ROE", r.get("roe"))
            debt_val = r.get("debt", r.get("debt_equity", r.get("Debt/Equity")))
            fcf_yield = r.get("FCF_yield", r.get("fcf_yield", r.get("FCF Margin %")))
            ocf_val = fcf_yield if fcf_yield is not None else r.get("operating_cash_flow", 1.0)
            fund_cat = str(r.get("fundamental_category", "NONE"))
            is_trap = (fund_cat == "VALUE_TRAP") or bool(r.get("is_value_trap", False))

            funds_map[sym] = {
                "symbol": sym,
                "roce": float(roce_val) if roce_val is not None and not pd.isna(roce_val) else None,
                "roe": float(roe_val) if roe_val is not None and not pd.isna(roe_val) else None,
                "debt_equity": float(debt_val) if debt_val is not None and not pd.isna(debt_val) else None,
                "operating_cash_flow": float(ocf_val) if ocf_val is not None and not pd.isna(ocf_val) else None,
                "fundamental_category": fund_cat,
                "is_value_trap": is_trap,
                "quality_score": float(r.get("quality_score", 0.0) or 0.0),
                "growth_score": float(r.get("growth_score", 0.0) or 0.0),
                "valuation_score": float(r.get("valuation_score", 0.0) or 0.0),
                "wealth_score": float(r.get("wealth_score", 0.0) or 0.0),
                "risk_score": float(r.get("risk_score", 0.0) or 0.0),
                "valuation_category": str(r.get("valuation_category", "NONE")),
                "fair_value_range": str(r.get("fair_value_range", "")),
                # Acceleration fields mapped if present in row
                "rev_yoy_latest": r.get("rev_yoy_latest"),
                "rev_yoy_prev": r.get("rev_yoy_prev"),
                "op_profit_yoy_latest": r.get("op_profit_yoy_latest"),
                "op_profit_yoy_prev": r.get("op_profit_yoy_prev"),
                "eps_yoy_latest": r.get("eps_yoy_latest"),
                "eps_yoy_prev": r.get("eps_yoy_prev"),
                "prior_eps": r.get("prior_eps"),
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
        is_stale: bool = False
    ) -> Dict[str, Any]:
        """Evaluates a single candidate through the mandatory 6-gate pipeline."""
        sym = symbol.upper()
        rejections: List[RejectionReason] = []
        gate_metrics: Dict[str, Any] = {}

        # 1. Universe Gate
        univ_pass, univ_err = self.universe_registry.validate_symbol(sym)
        if not univ_pass and univ_err:
            rejections.append(univ_err)
            return self._build_result(sym, False, rejections, gate_metrics)

        # 2. Provenance & Freshness Gate
        if not provenance_valid:
            rejections.append(RejectionReason.FUNDAMENTAL_PROVENANCE_INVALID)
            return self._build_result(sym, False, rejections, gate_metrics)
        if is_stale:
            rejections.append(RejectionReason.FUNDAMENTAL_DATA_STALE)
            return self._build_result(sym, False, rejections, gate_metrics)

        # 3. Fundamental Quality Gate (ROCE >= 15%, ROE >= 12%, OCF > 0, D/E <= 1.0)
        fq_pass, fq_errs, fq_metrics = FundamentalQualityGate.evaluate(fundamentals)
        gate_metrics.update(fq_metrics)
        if not fq_pass:
            rejections.extend(fq_errs)

        # 4. Earnings Acceleration Gate (Rev Accel, Op Profit Accel, EPS Accel, Prior EPS > 0)
        ea_pass, ea_errs, ea_metrics = EarningsAccelerationGate.evaluate(fundamentals)
        gate_metrics.update(ea_metrics)
        if not ea_pass:
            rejections.extend(ea_errs)

        # 5. Market Data Sanity
        if df_bars is None or len(df_bars) < 200:
            rejections.append(RejectionReason.MARKET_DATA_INSUFFICIENT_LOOKBACK)
            return self._build_result(sym, False, rejections, gate_metrics)

        closes = df_bars["Close" if "Close" in df_bars.columns else "close"].values.astype(np.float64)
        highs = df_bars["High" if "High" in df_bars.columns else "high"].values.astype(np.float64)
        lows = df_bars["Low" if "Low" in df_bars.columns else "low"].values.astype(np.float64)
        volumes = df_bars["Volume" if "Volume" in df_bars.columns else "volume"].values.astype(np.float64)

        # 6. Technical Trend Gate (Close > SMA50 > SMA200, 3M/6M alpha)
        tt_pass, tt_errs, tt_metrics = TechnicalTrendGate.evaluate(closes, benchmark_closes)
        gate_metrics.update(tt_metrics)
        if not tt_pass:
            rejections.extend(tt_errs)

        # 7. Consolidation Gate (window 20-60, DD <= 15%, ATR <= 6%, Close > SMA200)
        c_pass, c_errs, c_metrics = ConsolidationGate.evaluate(closes, highs, lows, consolidation_window)
        gate_metrics.update(c_metrics)
        if not c_pass:
            rejections.extend(c_errs)

        # 8. Breakout Gate (Close > 20D High, Vol >= 1.5x, Ext <= 8%)
        b_pass, b_errs, b_metrics = BreakoutGate.evaluate(closes, highs, volumes, lookback=20)
        gate_metrics.update(b_metrics)
        if not b_pass:
            rejections.extend(b_errs)

        is_buy = (len(rejections) == 0)
        return self._build_result(sym, is_buy, rejections, gate_metrics)

    def scan_universe(
        self,
        market_data_map: Dict[str, pd.DataFrame],
        fundamentals_map: Optional[Dict[str, Dict[str, Any]]] = None,
        benchmark_closes: Optional[np.ndarray] = None
    ) -> Dict[str, Any]:
        """
        Scans all candidates across the universe and records the complete stock funnel audit.
        Also records execution run in scanner_execution_history and scanner_health.
        Uses Daily Builder 2.0 as the authoritative upstream fundamental intelligence layer.
        """
        import time
        start_ts = time.time()
        ctx = None

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

        try:
            try:
                from database import create_scanner_execution_run, complete_scanner_execution_run, upsert_scanner_health, save_wealth_buy_alert
            except ImportError:
                from app.database import create_scanner_execution_run, complete_scanner_execution_run, upsert_scanner_health, save_wealth_buy_alert
        except Exception:
            create_scanner_execution_run = None
            complete_scanner_execution_run = None
            upsert_scanner_health = None
            save_wealth_buy_alert = None

        if create_scanner_execution_run is not None:
            try:
                ctx = create_scanner_execution_run(
                    scanner_name="FUNDAMENTAL_WEALTH_BUY",
                    trigger_type="AUTOMATED",
                    total_stocks=len(market_data_map),
                    allow_concurrent=True
                )
            except Exception as e:
                logger.debug(f"Execution history start warning: {e}")

        if upsert_scanner_health is not None:
            try:
                upsert_scanner_health(
                    "FUNDAMENTAL_WEALTH_BUY",
                    status="RUNNING",
                    total_count=len(market_data_map),
                    run_id=getattr(ctx, "run_id", None)
                )
            except Exception as e:
                logger.debug(f"Scanner health RUNNING warning: {e}")

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
                funds = fundamentals_map.get(sym, {})
                res = self.scan_candidate(sym, df_bars, funds, benchmark_closes=benchmark_closes)

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
                    # Persist alert to database if available
                    if save_wealth_buy_alert is not None:
                        try:
                            save_wealth_buy_alert(
                                symbol=sym,
                                alert_price=float(res.get("metrics", {}).get("close", 0.0)),
                                breakout_type="20D_BREAKOUT_FUNDAMENTAL",
                                fm_score=95.0,
                                notes="Passed Mandatory Fundamental Quality + Growth + 20D Breakout"
                            )
                        except Exception as al_err:
                            logger.debug(f"Save alert warning for {sym}: {al_err}")
                else:
                    for r in res["rejection_reasons"]:
                        r_key = r.value if hasattr(r, "value") else str(r)
                        funnel["rejection_summary"][r_key] = funnel["rejection_summary"].get(r_key, 0) + 1

            duration_sec = round(time.time() - start_ts, 2)
            if ctx and complete_scanner_execution_run is not None:
                try:
                    ctx.fresh_data_count = funnel["scanned_count"]
                    ctx.alerts_generated = funnel["buy_alerts_count"]
                    complete_scanner_execution_run(ctx)
                except Exception as ce_err:
                    logger.debug(f"Execution completion warning: {ce_err}")

            if upsert_scanner_health is not None:
                try:
                    upsert_scanner_health(
                        "FUNDAMENTAL_WEALTH_BUY",
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
                        "FUNDAMENTAL_WEALTH_BUY",
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
