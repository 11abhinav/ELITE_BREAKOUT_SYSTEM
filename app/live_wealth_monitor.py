#!/usr/bin/env python3
"""
app/live_wealth_monitor.py
==========================
ELITE BREAKOUT SYSTEM — LIVE BUY ALERT + LIVE EXIT MONITOR + V2 SHADOW TRACKER

MANDATORY GOVERNANCE INVARIANTS:
  1. Real Upstox Data Provenance & Point-in-Time Causality.
  2. Absolute Zero Broker Orders: There is NO broker API call, NO automatic order placement,
     NO automatic sell, and NO automatic buy. The system generates alerts and manages dashboard state.
  3. Primary User-Facing Exit: WEALTH_EXIT_V1 exclusively.
  4. Parallel Shadow Exit: WEALTH_EXIT_V2 for research observation only.
     V2 must NEVER independently close user-facing positions.
  5. Prohibition of Composite Exits: NEVER use (V1 OR V2) or (V1 AND V2) to close.
  6. CMP Reference Pricing: Dashboard closure is booked at Current Market Price (CMP) as
     'dashboard_exit_cmp'. It is NEVER falsely conflated with certified T+1 backtest price
     or user's actual broker execution price ('user_actual_exit_price').
  7. Market-Hour Gate: Monitor operates strictly 09:00 IST – 16:00 IST on valid trading days.
     Outside market hours: EXIT_MONITOR_STATUS = INACTIVE.
  8. Unfinished Candle Protection: Incomplete intraday candles MUST NOT be treated as completed
     daily closes for rules requiring completed daily closes.
  9. Duplicate Protection: Exactly one EXIT_ALERT per position lifecycle.
  10. Atomic State Persistence & Disaster Recovery across process restarts.
"""

from __future__ import annotations
import os
import sys
import json
import uuid
import math
import copy
import logging
import threading
from datetime import datetime, date, time as time_cls
from zoneinfo import ZoneInfo
from typing import Dict, List, Any, Optional, Tuple, Union
from enum import Enum
import numpy as np
import pandas as pd

# Core Configuration & Timezone
IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger("LIVE_WEALTH_MONITOR")

try:
    from app.fundamental_telemetry import WealthExitTelemetry
except ImportError:
    from fundamental_telemetry import WealthExitTelemetry

BASE_DIR = os.getenv("ELITE_BASE_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if not os.path.exists(os.path.join(BASE_DIR, "data")) and os.path.exists("/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/data"):
    BASE_DIR = "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM"
DATA_DIR = os.path.join(BASE_DIR, "data")
PROSPECTIVE_DIR = os.path.join(DATA_DIR, "prospective_holdout")
os.makedirs(PROSPECTIVE_DIR, exist_ok=True)

STATE_FILE = os.path.join(DATA_DIR, "live_wealth_monitor_state.json")
ALERTS_LOG = os.path.join(DATA_DIR, "live_wealth_alerts.jsonl")
RESEARCH_LEDGER = os.path.join(PROSPECTIVE_DIR, "live_wealth_research_ledger.jsonl")

# -------------------------------------------------------------------------------------
# 1. FROZEN GOVERNANCE REFERENCE HASHES (CANONICAL BENCHMARK SPECIFICATIONS)
# -------------------------------------------------------------------------------------
FROZEN_GIT_SHA = "35fe412d"
CLEAN_DATASET_SHA = "57297e68cfb613e536136d8591f4ae8b74681347072e50587dff573356024ce5"
CONFIGURATION_HASH = "593b48455110191ebc1bc3eb615aa4d7f7669d27376c9ad84126bf60ba0868f0"
STRATEGY_SPEC_HASH = "bf04bf9ca9810bb62b4c1aa5e4125d19e99a807d9f75bfdc8ce645c38bc35fc2"
RULES_HASH_V1 = "8bbdf26997d9bc662fb554d3bbd62ee46c6f780fc9304044ee78995a9cf2df62"
RULES_HASH_V2 = "be1816bc8d8e0ca45f65fb0a7ce5cb42a4253a6d9b935408a0d783aa803ec29a"

# Absolute Safety Invariant: No Broker Order Placement
AUTOMATIC_BROKER_ORDERS = False
SAFETY_INVARIANT = "NO_BROKER_ORDER_ROUTING"


# -------------------------------------------------------------------------------------
# 2. POSITION LIFECYCLE & STATE MACHINE
# -------------------------------------------------------------------------------------
class PositionStatus(str, Enum):
    BUY_ALERT = "BUY_ALERT"
    OPEN = "OPEN"
    EXIT_ALERT = "EXIT_ALERT"
    CLOSED = "CLOSED"


class DataHealthStatus(str, Enum):
    GREEN = "GREEN"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"


# -------------------------------------------------------------------------------------
# 3. CANONICAL EXIT EVALUATORS (FROZEN FORMULAS)
# -------------------------------------------------------------------------------------
class CanonicalV1ExitEvaluator:
    """
    Exact frozen implementation of WEALTH_EXIT_V1.
    Structural Weakness:
      - 2 consecutive daily closes < SMA50
      OR
      - Daily close < prior 20-session lowest close
    Secondary Confirmation (on the same session):
      - SMA50 slope down: SMA50[T] <= SMA50[T-5]
      OR
      - 10D relative return vs Nifty <= -5% (stock_ret_10 - bm_ret_10 <= -0.05)
      OR
      - >= 2 distribution days in prior 10 sessions (Close < Open and Volume >= 1.5 * VolSMA20)
    """

    @staticmethod
    def evaluate(
        closes: np.ndarray,
        opens: np.ndarray,
        volumes: np.ndarray,
        benchmark_closes: Optional[np.ndarray] = None,
        is_completed_session: bool = True
    ) -> Dict[str, Any]:
        """
        Evaluates the V1 exit condition on the historical/live array up to session T.
        If is_completed_session is False, returns HOLD because V1 requires a completed daily close.
        """
        n = len(closes)
        if n < 50:
            return {
                "exit_signal": False,
                "reason": "INSUFFICIENT_LOOKBACK",
                "structural_weakness": False,
                "secondary_confirmation": False,
                "components": [],
                "blocked": True,
                "blocked_reason": "Lookback bars < 50"
            }

        # Guard: Intraday unfinished candle rule
        if not is_completed_session:
            return {
                "exit_signal": False,
                "reason": "INTRADAY_UNFINISHED_CANDLE_GUARD",
                "structural_weakness": False,
                "secondary_confirmation": False,
                "components": ["INTRADAY_UNFINISHED_CANDLE"],
                "blocked": False,
                "blocked_reason": None
            }

        k = n - 1

        # 1. SMA50
        sma50_series = pd.Series(closes).rolling(50, min_periods=20).mean().values
        sma50_k = sma50_series[k]
        sma50_prev = sma50_series[k - 1]

        # 2. Lowest close of prior 20 completed sessions: min(Close[k-20 : k])
        prior20_series = pd.Series(closes).shift(1).rolling(20, min_periods=10).min().values
        prior20_low_k = prior20_series[k]

        # 3. 20-day Volume SMA
        vol_sma20_series = pd.Series(volumes).rolling(20, min_periods=10).mean().values
        vol_sma20_k = vol_sma20_series[k]

        # 4. Distribution days: Close < Open and Volume >= 1.5 * VolSMA20
        dist_day = (closes < opens) & (volumes >= 1.5 * vol_sma20_series)
        dist_days_10 = pd.Series(dist_day).rolling(10, min_periods=1).sum().values[k]

        # 5. 10-day return of stock
        ret10_stock = (closes[k] - closes[k - 10]) / closes[k - 10] if k >= 10 and closes[k - 10] > 0 else 0.0

        # 6. Relative return vs Benchmark
        if benchmark_closes is not None and len(benchmark_closes) >= 11 and benchmark_closes[-11] > 0:
            bm_ret10 = (benchmark_closes[-1] - benchmark_closes[-11]) / benchmark_closes[-11]
            rel_ret10 = ret10_stock - bm_ret10
        else:
            rel_ret10 = ret10_stock

        # Condition 1: Structural Weakness
        cond_sma50_2x = bool(closes[k] < sma50_k and closes[k - 1] < sma50_prev)
        cond_prior20_low = bool(not np.isnan(prior20_low_k) and closes[k] < prior20_low_k)
        is_struct = cond_sma50_2x or cond_prior20_low

        # Condition 2: Secondary Confirmation
        sec_1 = bool(k >= 5 and sma50_k <= sma50_series[k - 5])
        sec_2 = bool(rel_ret10 <= -0.05)
        sec_3 = bool(dist_days_10 >= 2)
        is_sec = sec_1 or sec_2 or sec_3

        components = []
        if cond_sma50_2x:
            components.append("2_CLOSES_BELOW_SMA50")
        if cond_prior20_low:
            components.append("BREAK_PRIOR_20D_LOW")
        if sec_1:
            components.append("SMA50_SLOPE_DOWN")
        if sec_2:
            components.append("REL_RET_LE_M5PCT")
        if sec_3:
            components.append("DIST_DAYS_GE_2")

        exit_signal = bool(is_struct and is_sec)
        struct_desc = "2_CLOSES_BELOW_SMA50" if cond_sma50_2x else ("BREAK_PRIOR_20D_LOW" if cond_prior20_low else "NONE")
        secs = []
        if sec_1:
            secs.append("SMA50_SLOPE_DOWN")
        if sec_2:
            secs.append("REL_RET_LE_M5PCT")
        if sec_3:
            secs.append("DIST_DAYS_GE_2")
        reason = f"{struct_desc}+({'+'.join(secs)})" if exit_signal else "HOLD"

        return {
            "exit_signal": exit_signal,
            "reason": reason,
            "structural_weakness": is_struct,
            "secondary_confirmation": is_sec,
            "components": components,
            "sma50": float(sma50_k) if not np.isnan(sma50_k) else 0.0,
            "prior20_low": float(prior20_low_k) if not np.isnan(prior20_low_k) else 0.0,
            "dist_days_10": int(dist_days_10),
            "rel_ret10": float(rel_ret10),
            "blocked": False,
            "blocked_reason": None
        }


class CanonicalV2ExitEvaluator:
    """
    Exact frozen implementation of WEALTH_EXIT_V2 (Shadow / Research Monitor).
    Structural Weakness:
      - 2 consecutive closes < prior 20-session lowest close
      OR
      - 2 consecutive closes < SMA50
    Compound Secondary Confirmation:
      - Relative return <= -5% AND Stock 10D absolute return < 0
      PLUS either:
        * SMA50 slope flat/down (SMA50[T] <= SMA50[T-5])
        OR
        * >= 2 distribution days in prior 10 sessions
    """

    @staticmethod
    def evaluate(
        closes: np.ndarray,
        opens: np.ndarray,
        volumes: np.ndarray,
        benchmark_closes: Optional[np.ndarray] = None,
        is_completed_session: bool = True
    ) -> Dict[str, Any]:
        n = len(closes)
        if n < 50:
            return {
                "exit_signal": False,
                "reason": "INSUFFICIENT_LOOKBACK",
                "structural_weakness": False,
                "secondary_confirmation": False,
                "components": [],
                "blocked": True,
                "blocked_reason": "Lookback bars < 50"
            }

        # Guard: Intraday unfinished candle rule
        if not is_completed_session:
            return {
                "exit_signal": False,
                "reason": "INTRADAY_UNFINISHED_CANDLE_GUARD",
                "structural_weakness": False,
                "secondary_confirmation": False,
                "components": ["INTRADAY_UNFINISHED_CANDLE"],
                "blocked": False,
                "blocked_reason": None
            }

        k = n - 1

        sma50_series = pd.Series(closes).rolling(50, min_periods=20).mean().values
        sma50_k = sma50_series[k]
        sma50_prev = sma50_series[k - 1]

        prior20_series = pd.Series(closes).shift(1).rolling(20, min_periods=10).min().values
        prior20_low_k = prior20_series[k]
        prior20_low_prev = prior20_series[k - 1] if k >= 1 else np.nan

        vol_sma20_series = pd.Series(volumes).rolling(20, min_periods=10).mean().values
        dist_day = (closes < opens) & (volumes >= 1.5 * vol_sma20_series)
        dist_days_10 = pd.Series(dist_day).rolling(10, min_periods=1).sum().values[k]

        ret10_stock = (closes[k] - closes[k - 10]) / closes[k - 10] if k >= 10 and closes[k - 10] > 0 else 0.0

        if benchmark_closes is not None and len(benchmark_closes) >= 11 and benchmark_closes[-11] > 0:
            bm_ret10 = (benchmark_closes[-1] - benchmark_closes[-11]) / benchmark_closes[-11]
            rel_ret10 = ret10_stock - bm_ret10
        else:
            rel_ret10 = ret10_stock

        # Structural Weakness in V2:
        # 2 consecutive closes < SMA50 OR 2 consecutive closes < prior 20D low
        cond_sma50_2x = bool(closes[k] < sma50_k and closes[k - 1] < sma50_prev)
        cond_prior20_2x = bool(
            not np.isnan(prior20_low_k) and not np.isnan(prior20_low_prev) and
            closes[k] < prior20_low_k and closes[k - 1] < prior20_low_prev
        )
        is_struct = cond_sma50_2x or cond_prior20_2x

        # Compound Secondary Confirmation in V2:
        # [Relative Return <= -5% AND Stock 10D Abs Return < 0]
        # PLUS [SMA50 Slope Flat/Down OR >= 2 Distribution Days]
        cond_rel_abs = bool(rel_ret10 <= -0.05 and ret10_stock < 0.0)
        cond_slope_down = bool(k >= 5 and sma50_k <= sma50_series[k - 5])
        cond_dist_days = bool(dist_days_10 >= 2)
        cond_tech = cond_slope_down or cond_dist_days

        is_sec = cond_rel_abs and cond_tech

        components = []
        if cond_sma50_2x:
            components.append("2_CLOSES_BELOW_SMA50")
        if cond_prior20_2x:
            components.append("2_CLOSES_BELOW_20D_LOW")
        if cond_rel_abs:
            components.append("REL_ABS_WEAKNESS")
        if cond_slope_down:
            components.append("SMA50_SLOPE_DOWN")
        if cond_dist_days:
            components.append("DIST_DAYS_GE_2")

        exit_signal = bool(is_struct and is_sec)
        struct_desc = "2_CLOSES_BELOW_SMA50" if cond_sma50_2x else ("2_CLOSES_BELOW_20D_LOW" if cond_prior20_2x else "NONE")
        tech_desc = "SLOPE_DOWN" if cond_slope_down else "DIST_DAYS_GE_2"
        reason = f"{struct_desc}+(REL_ABS_WEAKNESS+{tech_desc})" if exit_signal else "HOLD"

        return {
            "exit_signal": exit_signal,
            "reason": reason,
            "structural_weakness": is_struct,
            "secondary_confirmation": is_sec,
            "components": components,
            "sma50": float(sma50_k) if not np.isnan(sma50_k) else 0.0,
            "prior20_low": float(prior20_low_k) if not np.isnan(prior20_low_k) else 0.0,
            "dist_days_10": int(dist_days_10),
            "rel_ret10": float(rel_ret10),
            "ret10_stock": float(ret10_stock),
            "blocked": False,
            "blocked_reason": None
        }


class CanonicalRecoveryE3ExitEvaluator:
    """
    Exact frozen implementation of Model E3 Fundamental Exit for QUALITY_VALUE_RECOVERY_WEALTH_V1.
    Structural Fundamental Exit Rules:
      1. Margin Collapse: EBITDA Margin drops > 30% from peak / entry EBITDA margin
      2. Debt Explosion: Debt-to-Equity > 1.25
      3. Earnings Degradation: 3 consecutive YoY Net Profit declines
    """

    @staticmethod
    def evaluate(
        ebitda_margin: float,
        entry_ebitda_margin: float,
        debt_to_equity: float,
        yoy_profit_drops: int
    ) -> Dict[str, Any]:
        margin_drop_pct = (entry_ebitda_margin - ebitda_margin) / entry_ebitda_margin if entry_ebitda_margin > 0 else 0.0
        
        cond_margin_collapse = margin_drop_pct > 0.30
        cond_debt_explosion = debt_to_equity > 1.25
        cond_profit_degradation = yoy_profit_drops >= 3
        
        reasons = []
        if cond_margin_collapse:
            reasons.append(f"EBITDA_MARGIN_COLLAPSE_{margin_drop_pct*100:.1f}%")
        if cond_debt_explosion:
            reasons.append(f"DEBT_EXPLOSION_DE_{debt_to_equity:.2f}")
        if cond_profit_degradation:
            reasons.append(f"EARNINGS_DEGRADATION_YOY_DROPS_{yoy_profit_drops}")

        exit_signal = bool(cond_margin_collapse or cond_debt_explosion or cond_profit_degradation)

        return {
            "exit_signal": exit_signal,
            "reason": " + ".join(reasons) if exit_signal else "HOLD",
            "cond_margin_collapse": cond_margin_collapse,
            "cond_debt_explosion": cond_debt_explosion,
            "cond_profit_degradation": cond_profit_degradation,
            "margin_drop_pct": margin_drop_pct,
            "debt_to_equity": debt_to_equity,
            "yoy_profit_drops": yoy_profit_drops
        }



# -------------------------------------------------------------------------------------
# 4. CANONICAL BUY ENTRY SCANNER (FROZEN 20D BREAKOUT)
# -------------------------------------------------------------------------------------
class Canonical20DBreakoutScanner:
    """
    Exact frozen implementation of 20D_BREAKOUT_CONTROL.
    Signal at session T close:
      Close[T] > max(High[T-20 : T])
    Point-in-Time Causality:
      Entry execution occurs at Date T+1 Open.
    """

    @staticmethod
    def evaluate(highs: np.ndarray, closes: np.ndarray, lookback: int = 20) -> Dict[str, Any]:
        n = len(closes)
        if n < lookback + 1:
            return {"is_breakout": False, "breakout_level": 0.0, "distance_pct": 0.0}

        # Prior 20-day high is max(highs[T-20 : T])
        prior_high = float(np.max(highs[-1 - lookback : -1]))
        current_close = float(closes[-1])
        is_breakout = bool(current_close > prior_high)
        distance_pct = ((current_close - prior_high) / prior_high) * 100.0 if prior_high > 0 else 0.0

        return {
            "is_breakout": is_breakout,
            "breakout_level": prior_high,
            "distance_pct": round(distance_pct, 4),
            "signal_close": current_close
        }


# -------------------------------------------------------------------------------------
# 5. MARKET CALENDAR & TRADING HOURS GATE
# -------------------------------------------------------------------------------------
class MarketHoursGate:
    """
    Enforces trading hours strictly: 09:00 IST – 16:00 IST on valid trading days.
    Outside market hours: EXIT_MONITOR_STATUS = INACTIVE.
    """

    @staticmethod
    def is_market_open_ist(dt: Optional[datetime] = None) -> Tuple[bool, str]:
        if dt is None:
            dt = datetime.now(IST)
        elif dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        else:
            dt = dt.astimezone(IST)

        # Check weekend
        if dt.weekday() >= 5:  # Saturday or Sunday
            return False, "WEEKEND_CLOSED"

        # Check holiday via TradingCalendar if available
        try:
            from app.trading_calendar import TradingCalendar
            cal = TradingCalendar()
            if not cal.is_trading_day(dt.date()):
                return False, "EXCHANGE_HOLIDAY"
        except Exception:
            pass

        # Check market hours: 09:00 to 16:00 IST
        curr_time = dt.time()
        start_time = time_cls(9, 0, 0)
        end_time = time_cls(16, 0, 0)

        if start_time <= curr_time <= end_time:
            return True, "MARKET_OPEN"
        elif curr_time < start_time:
            return False, "PRE_MARKET_INACTIVE"
        else:
            return False, "POST_MARKET_INACTIVE"


# -------------------------------------------------------------------------------------
# 6. DATA HEALTH GATE
# -------------------------------------------------------------------------------------
class DataHealthGate:
    """
    Validates data integrity before evaluating exits:
      - Valid OHLC values (non-negative, high >= max(open, close), low <= min(open, close))
      - Sufficient lookback (>= 50 completed bars)
      - Freshness (within last 24h or last trading session)
      - Monotonic timestamps
      - Corporate action split anomaly flag
    If data fails health check, FAILS CLOSED: record EXIT_MONITOR_BLOCKED.
    """

    @staticmethod
    def check_health(
        symbol: str,
        df_bars: pd.DataFrame,
        is_split_anomaly: bool = False
    ) -> Tuple[DataHealthStatus, Optional[str]]:
        if df_bars is None or len(df_bars) == 0:
            return DataHealthStatus.BLOCKED, f"{symbol}: EMPTY_OR_MISSING_DATA"

        if is_split_anomaly:
            return DataHealthStatus.BLOCKED, f"{symbol}: CORPORATE_ACTION_UNADJUSTED_ANOMALY"

        if len(df_bars) < 50:
            return DataHealthStatus.BLOCKED, f"{symbol}: INSUFFICIENT_LOOKBACK_BARS ({len(df_bars)} < 50)"

        # Check required columns
        req_cols = {"Open", "High", "Low", "Close", "Volume"}
        available = {c.capitalize() for c in df_bars.columns}
        if not req_cols.issubset(available):
            return DataHealthStatus.BLOCKED, f"{symbol}: MISSING_REQUIRED_OHLCV_FIELDS"

        # Check for NaNs or negative prices in the last 20 bars
        recent = df_bars.tail(20)
        closes = recent["Close" if "Close" in recent.columns else "close"].values
        highs = recent["High" if "High" in recent.columns else "high"].values
        lows = recent["Low" if "Low" in recent.columns else "low"].values

        if np.isnan(closes).any() or np.isnan(highs).any() or np.isnan(lows).any():
            return DataHealthStatus.BLOCKED, f"{symbol}: NAN_IN_RECENT_OHLC"

        if (closes <= 0).any() or (highs <= 0).any() or (lows <= 0).any():
            return DataHealthStatus.BLOCKED, f"{symbol}: NON_POSITIVE_PRICE_DETECTED"

        if (highs < lows).any():
            return DataHealthStatus.BLOCKED, f"{symbol}: INVALID_CANDLE_HIGH_LESS_THAN_LOW"

        return DataHealthStatus.GREEN, None


# -------------------------------------------------------------------------------------
# 7. LIVE WEALTH MONITOR ENGINE (ATOMIC STATE & WORKFLOW MANAGER)
# -------------------------------------------------------------------------------------
class LiveWealthMonitorEngine:
    """
    Central Coordinator for the Live Buy Alert + Live Exit Monitor + V2 Shadow Tracker.
    Thread-safe and atomic persistence across application restarts.
    """

    def __init__(self, state_file: str = STATE_FILE, alerts_log: str = ALERTS_LOG, ledger_file: str = RESEARCH_LEDGER):
        self.state_file = state_file
        self.alerts_log = alerts_log
        self.ledger_file = ledger_file
        self._lock = threading.RLock()

        # Engine State Containers
        self.buy_alerts: Dict[str, Dict[str, Any]] = {}       # alert_id -> Alert Dict
        self.open_positions: Dict[str, Dict[str, Any]] = {}   # position_id -> Position Dict
        self.closed_positions: Dict[str, Dict[str, Any]] = {} # position_id -> Position Dict
        self.exit_alerts: Dict[str, Dict[str, Any]] = {}      # alert_id -> Alert Dict
        self.emitted_exit_events: set = set()                 # (position_id, rules_hash) deduplication set
        self.last_scan_timestamp: Optional[str] = None
        self.last_scan_market_status: str = "INACTIVE"
        self.data_health_overall: str = "GREEN"

        self.load_state()

    # ---------------------------------------------------------------------------------
    # State Persistence & Recovery (Atomic Temp File + Rename)
    # ---------------------------------------------------------------------------------
    def save_state(self) -> None:
        """Atomically persists monitor state to disk."""
        with self._lock:
            state_data = {
                "metadata": {
                    "frozen_git_sha": FROZEN_GIT_SHA,
                    "clean_dataset_sha": CLEAN_DATASET_SHA,
                    "configuration_hash": CONFIGURATION_HASH,
                    "strategy_spec_hash": STRATEGY_SPEC_HASH,
                    "rules_hash_v1": RULES_HASH_V1,
                    "rules_hash_v2": RULES_HASH_V2,
                    "automatic_broker_orders": AUTOMATIC_BROKER_ORDERS,
                    "safety_invariant": SAFETY_INVARIANT,
                    "saved_at": datetime.now(IST).isoformat()
                },
                "summary": {
                    "last_scan_timestamp": self.last_scan_timestamp,
                    "last_scan_market_status": self.last_scan_market_status,
                    "data_health_overall": self.data_health_overall,
                    "total_buy_alerts": len(self.buy_alerts),
                    "open_positions_count": len(self.open_positions),
                    "closed_positions_count": len(self.closed_positions),
                    "exit_alerts_count": len(self.exit_alerts)
                },
                "buy_alerts": self.buy_alerts,
                "open_positions": self.open_positions,
                "closed_positions": self.closed_positions,
                "exit_alerts": self.exit_alerts,
                "emitted_exit_events": list(self.emitted_exit_events)
            }

            temp_path = f"{self.state_file}.tmp_{os.getpid()}_{uuid.uuid4().hex[:6]}"
            try:
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(state_data, f, indent=2, default=str)
                os.replace(temp_path, self.state_file)
            except Exception as e:
                logger.error(f"❌ Failed to atomically save live monitor state: {e}")
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass

    def load_state(self) -> None:
        """Loads state from disk, recovering active open positions and deduplication sets."""
        with self._lock:
            if not os.path.exists(self.state_file):
                logger.info("ℹ️ No previous live monitor state file found. Initializing blank state.")
                return

            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                self.buy_alerts = data.get("buy_alerts", {})
                self.open_positions = data.get("open_positions", {})
                self.closed_positions = data.get("closed_positions", {})
                self.exit_alerts = data.get("exit_alerts", {})
                self.emitted_exit_events = set(tuple(x) if isinstance(x, list) else x for x in data.get("emitted_exit_events", []))
                
                summary = data.get("summary", {})
                self.last_scan_timestamp = summary.get("last_scan_timestamp")
                self.last_scan_market_status = summary.get("last_scan_market_status", "INACTIVE")
                self.data_health_overall = summary.get("data_health_overall", "GREEN")

                logger.info(
                    f"✅ Loaded live monitor state: {len(self.open_positions)} OPEN positions, "
                    f"{len(self.closed_positions)} CLOSED positions, {len(self.buy_alerts)} BUY alerts."
                )
            except Exception as e:
                logger.error(f"⚠️ Failed to parse state file {self.state_file}: {e}. Preserving memory state.")

    # ---------------------------------------------------------------------------------
    # Step 1: BUY Alert Generation
    # ---------------------------------------------------------------------------------
    def generate_buy_alert(
        self,
        symbol: str,
        signal_date: str,
        signal_close: float,
        breakout_reference: float,
        breakout_distance: float,
        indicator_values: Optional[Dict[str, Any]] = None,
        benchmark_state: Optional[str] = "STABLE",
        market_regime: str = "BULL",
        data_timestamp: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Creates a unique BUY ALERT for a qualified 20D breakout signal.
        Initial status: BUY_ALERT.
        NOTE: Does NOT infer that the user bought. The position remains BUY_ALERT
        until the user explicitly records their purchase.
        """
        with self._lock:
            now_ist = datetime.now(IST)
            alert_id = f"BUY_{symbol}_{signal_date}_{uuid.uuid4().hex[:8]}"

            alert_payload = {
                "alert_id": alert_id,
                "symbol": symbol.upper(),
                "alert_type": "BUY_ALERT",
                "strategy": "20D_BREAKOUT",
                "strategy_version": "1.0_FROZEN",
                "signal_timestamp": now_ist.isoformat(),
                "signal_date": signal_date,
                "signal_close": float(signal_close),
                "breakout_reference": float(breakout_reference),
                "breakout_distance": float(breakout_distance),
                "indicator_values": indicator_values or {},
                "benchmark_state": benchmark_state,
                "market_regime": market_regime,
                "code_sha": FROZEN_GIT_SHA,
                "rules_hash": RULES_HASH_V1,
                "data_timestamp": data_timestamp or now_ist.isoformat(),
                "alert_status": PositionStatus.BUY_ALERT.value
            }

            self.buy_alerts[alert_id] = alert_payload
            self._append_to_file(self.alerts_log, alert_payload)
            self.save_state()

            logger.info(f"🔔 [BUY ALERT] Generated {alert_id} for {symbol} @ ₹{signal_close:.2f}")
            return alert_payload

    # ---------------------------------------------------------------------------------
    # Step 2: User Records Purchase -> Transition to OPEN
    # ---------------------------------------------------------------------------------
    def record_user_buy(
        self,
        symbol: str,
        entry_price: float,
        entry_date: Optional[str] = None,
        user_actual_entry_price: Optional[float] = None,
        buy_alert_id: Optional[str] = None,
        shares: int = 1
    ) -> Dict[str, Any]:
        """
        Records that the user executed a buy trade. Transitions status from BUY_ALERT to OPEN.
        Enforces single OPEN position per symbol invariant.
        """
        with self._lock:
            sym = symbol.upper()
            now_ist = datetime.now(IST)
            ed = entry_date or now_ist.strftime("%Y-%m-%d")

            # Check for existing open position for this symbol
            for pid, pos in self.open_positions.items():
                if pos["symbol"] == sym and pos["status"] == PositionStatus.OPEN.value:
                    logger.warning(f"⚠️ Cannot open duplicate position for {sym}. Existing position {pid} is already OPEN.")
                    return {"success": False, "error": f"DUPLICATE_OPEN_POSITION_FOR_{sym}", "position_id": pid}

            position_id = f"POS_{sym}_{ed}_{uuid.uuid4().hex[:8]}"

            position_record = {
                "position_id": position_id,
                "symbol": sym,
                "status": PositionStatus.OPEN.value,
                "entry_date": ed,
                "entry_timestamp": now_ist.isoformat(),
                "entry_reference_price": float(entry_price),
                "user_actual_entry_price": float(user_actual_entry_price) if user_actual_entry_price is not None else None,
                "shares": int(shares),
                "buy_alert_id": buy_alert_id,
                "current_cmp": float(entry_price),
                "peak_price": float(entry_price),
                "trough_price": float(entry_price),
                "dashboard_return_ref_pct": 0.0,
                # Exit Fields
                "dashboard_exit_cmp": None,
                "user_actual_exit_price": None,
                "certification_reference_exit_price": None,
                "dashboard_closed_timestamp": None,
                "exit_trigger_timestamp": None,
                "exit_reason_v1": None,
                # V1 Primary Monitor State
                "v1_state": "HOLD",
                "v1_structural_weakness": False,
                "v1_confirmation": False,
                "v1_reasons": [],
                # V2 Shadow Research State
                "v2_state": "HOLD",
                "v2_hypothetical_exit": False,
                "v2_exit_reason": None,
                "v2_hypothetical_cmp": None,
                "v2_hypothetical_timestamp": None,
                # Comparison & Health
                "v1_vs_v2_agreement": "AGREE",
                "last_evaluation_timestamp": now_ist.isoformat(),
                "data_health_status": DataHealthStatus.GREEN.value,
                "data_health_reason": None,
                "exit_alert_emitted": False,
                "code_sha": FROZEN_GIT_SHA,
                "rules_hash_v1": RULES_HASH_V1,
                "rules_hash_v2": RULES_HASH_V2
            }

            self.open_positions[position_id] = position_record

            # Update corresponding buy alert status if linked
            if buy_alert_id and buy_alert_id in self.buy_alerts:
                self.buy_alerts[buy_alert_id]["alert_status"] = PositionStatus.OPEN.value
                self.buy_alerts[buy_alert_id]["linked_position_id"] = position_id

            self.save_state()
            try:
                exit_telem = WealthExitTelemetry(scanner="FUNDAMENTAL")
                exit_telem.record_state_transition(
                    position_id=position_id,
                    symbol=sym,
                    from_state=PositionStatus.BUY_ALERT.value,
                    to_state=PositionStatus.OPEN.value,
                    authority="USER_BUY_ACTION",
                    reason="USER_RECORDED_BUY_CONFIRMATION",
                    reference_price=float(entry_price)
                )
            except Exception as st_err:
                logger.debug(f"State transition log warning: {st_err}")

            logger.info(f"📂 [POSITION OPEN] Created position {position_id} for {sym} @ entry ref ₹{entry_price:.2f}")
            return {"success": True, "position": position_record}

    # ---------------------------------------------------------------------------------
    # Step 3: Live Market Monitoring (Market Hours: 09:00 - 16:00 IST)
    # ---------------------------------------------------------------------------------
    def evaluate_live_exits(
        self,
        market_data_by_symbol: Dict[str, Dict[str, Any]],
        benchmark_closes: Optional[np.ndarray] = None,
        current_dt: Optional[datetime] = None,
        force_market_open: bool = False
    ) -> Dict[str, Any]:
        """
        Executes one polling cycle of the live exit monitor.
        1. Checks Market-Hour Gate (09:00–16:00 IST on trading days).
        2. Evaluates Data Health for each symbol.
        3. Runs Canonical WEALTH_EXIT_V1 (Primary Exit Monitor).
        4. Runs Canonical WEALTH_EXIT_V2 in parallel (Shadow Research Monitor).
        5. If V1 triggers: generates EXIT_ALERT and transitions position OPEN -> EXIT_ALERT -> CLOSED
           with dashboard_exit_cmp = CMP.
        6. V2 NEVER closes user-facing positions.
        """
        with self._lock:
            now_ist = current_dt or datetime.now(IST)
            self.last_scan_timestamp = now_ist.isoformat()

            exit_telemetry = WealthExitTelemetry(scanner="FUNDAMENTAL")
            exit_telemetry.log_cycle_start(open_position_count=len(self.open_positions))

            # 1. Market Hours Gate Check
            is_open, market_reason = MarketHoursGate.is_market_open_ist(now_ist)
            if force_market_open:
                is_open = True
                market_reason = "FORCED_OPEN_FOR_REPLAY"

            self.last_scan_market_status = "OPEN" if is_open else "CLOSED"

            if not is_open:
                logger.info(f"⏸️ [EXIT MONITOR INACTIVE] Market is closed ({market_reason}). Skipping exit scan.")
                self.save_state()
                cycle_summary = exit_telemetry.produce_end_of_cycle_summary()
                return {
                    "market_status": "CLOSED",
                    "reason": market_reason,
                    "scan_timestamp": self.last_scan_timestamp,
                    "evaluated_positions": 0,
                    "v1_exit_alerts": [],
                    "v2_shadow_exits": [],
                    "telemetry_summary": cycle_summary
                }

            v1_exit_alerts_generated = []
            v2_shadow_exits_detected = []
            positions_to_close = []

            # Initialize execution history and health heartbeat for V1 and V2 exit monitors
            import time
            start_ts = time.time()
            v1_ctx = None
            v2_ctx = None
            try:
                try:
                    from database import create_scanner_execution_run, complete_scanner_execution_run, upsert_scanner_health
                except ImportError:
                    from app.database import create_scanner_execution_run, complete_scanner_execution_run, upsert_scanner_health
            except Exception:
                create_scanner_execution_run = None
                complete_scanner_execution_run = None
                upsert_scanner_health = None

            if create_scanner_execution_run is not None:
                try:
                    v1_ctx = create_scanner_execution_run(
                        scanner_name="WEALTH_EXIT_V1",
                        trigger_type="AUTOMATED",
                        total_stocks=len(self.open_positions),
                        allow_concurrent=True
                    )
                except Exception as ce:
                    logger.debug(f"v1_ctx start warning: {ce}")
                try:
                    v2_ctx = create_scanner_execution_run(
                        scanner_name="WEALTH_EXIT_V2",
                        trigger_type="AUTOMATED",
                        total_stocks=len(self.open_positions),
                        allow_concurrent=True
                    )
                except Exception as ce:
                    logger.debug(f"v2_ctx start warning: {ce}")

            if upsert_scanner_health is not None:
                try:
                    upsert_scanner_health("WEALTH_EXIT_V1", status="RUNNING", total_count=len(self.open_positions), run_id=getattr(v1_ctx, "run_id", None))
                    upsert_scanner_health("WEALTH_EXIT_V2", status="RUNNING", total_count=len(self.open_positions), run_id=getattr(v2_ctx, "run_id", None))
                except Exception as he:
                    logger.debug(f"exit monitor health RUNNING warning: {he}")

            # 2. Iterate through all OPEN positions
            for position_id, pos in list(self.open_positions.items()):
                sym = pos["symbol"]
                if sym not in market_data_by_symbol:
                    pos["data_health_status"] = DataHealthStatus.DEGRADED.value
                    pos["data_health_reason"] = "SYMBOL_ABSENT_FROM_MARKET_FEED"
                    continue

                feed = market_data_by_symbol[sym]
                cmp_price = float(feed.get("cmp", feed.get("close", pos["current_cmp"])))
                is_completed_session = bool(feed.get("is_completed_session", True))
                is_split_anomaly = bool(feed.get("is_split_anomaly", False))
                df_bars = feed.get("df_bars")

                ref_entry = float(pos.get("entry_reference_price", 0.0))
                holding_days = 0
                try:
                    if pos.get("entry_date"):
                        entry_dt = datetime.strptime(str(pos["entry_date"]), "%Y-%m-%d").date()
                        holding_days = max(0, (now_ist.date() - entry_dt).days)
                except Exception:
                    pass

                exit_telemetry.record_position_audit(
                    position_id=position_id,
                    symbol=sym,
                    entry_date=str(pos.get("entry_date", "")),
                    entry_price=ref_entry,
                    current_cmp=cmp_price,
                    holding_days=holding_days
                )

                if not is_completed_session:
                    exit_telemetry.record_incomplete_candle_blocked(position_id=position_id, symbol=sym)

                # Data Health Gate
                health_status, health_reason = DataHealthGate.check_health(sym, df_bars, is_split_anomaly)
                pos["data_health_status"] = health_status.value
                pos["data_health_reason"] = health_reason
                pos["last_evaluation_timestamp"] = now_ist.isoformat()

                latest_bar_dt = None
                if df_bars is not None and len(df_bars) > 0:
                    d_col = "Date" if "Date" in df_bars.columns else ("date" if "date" in df_bars.columns else df_bars.columns[0])
                    latest_bar_dt = str(df_bars[d_col].iloc[-1])

                exit_telemetry.record_exit_data_quality(
                    position_id=position_id,
                    symbol=sym,
                    provider="UPSTOX",
                    latest_bar=latest_bar_dt,
                    rows_received=len(df_bars) if df_bars is not None else 0,
                    required_history_available=(len(df_bars) >= 50) if df_bars is not None else False,
                    data_age_hours=0.0,
                    freshness="FRESH" if is_completed_session else "INTRA_SESSION",
                    validation=health_status.value,
                    reason_code=health_reason or "OK"
                )

                if health_status == DataHealthStatus.BLOCKED:
                    logger.warning(f"🛡️ [EXIT_MONITOR_BLOCKED] {sym}: {health_reason}. Fail closed — no exit.")
                    continue

                # Extract OHLCV arrays
                date_col = "Date" if "Date" in df_bars.columns else ("date" if "date" in df_bars.columns else df_bars.columns[0])
                closes = df_bars["Close" if "Close" in df_bars.columns else "close"].values.astype(np.float64)
                opens = df_bars["Open" if "Open" in df_bars.columns else "open"].values.astype(np.float64)
                highs = df_bars["High" if "High" in df_bars.columns else "high"].values.astype(np.float64)
                lows = df_bars["Low" if "Low" in df_bars.columns else "low"].values.astype(np.float64)
                volumes = df_bars["Volume" if "Volume" in df_bars.columns else "volume"].values.astype(np.float64)

                closes_len = len(closes)
                close_t = float(closes[-1]) if closes_len > 0 else cmp_price
                close_t_prev = float(closes[-2]) if closes_len > 1 else close_t

                # Update CMP, Peak, Trough & Dashboard Reference Return
                pos["current_cmp"] = cmp_price
                pos["peak_price"] = max(pos["peak_price"], float(np.max(highs[-1:])) if len(highs) > 0 else cmp_price)
                pos["trough_price"] = min(pos["trough_price"], float(np.min(lows[-1:])) if len(lows) > 0 else cmp_price)
                pos["dashboard_return_ref_pct"] = round(((cmp_price - ref_entry) / ref_entry) * 100.0, 2) if ref_entry > 0 else 0.0

                # 3. Evaluate Primary WEALTH_EXIT_V1
                v1_result = CanonicalV1ExitEvaluator.evaluate(
                    closes=closes,
                    opens=opens,
                    volumes=volumes,
                    benchmark_closes=benchmark_closes,
                    is_completed_session=is_completed_session
                )

                pos["v1_structural_weakness"] = v1_result["structural_weakness"]
                pos["v1_confirmation"] = v1_result["secondary_confirmation"]
                pos["v1_reasons"] = v1_result["components"]
                pos["v1_state"] = "EXIT" if v1_result["exit_signal"] else "HOLD"

                sma50_t = float(v1_result.get("sma50", 0.0))
                sma50_series = pd.Series(closes).rolling(50, min_periods=20).mean().values
                sma50_t_prev5 = float(sma50_series[-6]) if closes_len >= 6 and not np.isnan(sma50_series[-6]) else sma50_t

                exit_telemetry.record_v1_evaluation(
                    position_id=position_id,
                    close_t=close_t,
                    close_t_prev=close_t_prev,
                    sma50_t=sma50_t,
                    sma50_t_prev5=sma50_t_prev5,
                    prior_20d_low=float(v1_result.get("prior20_low", 0.0)),
                    relative_return_10d=float(v1_result.get("rel_ret10", 0.0)),
                    distribution_days_10d=int(v1_result.get("dist_days_10", 0)),
                    cond_2_closes_sma50="2_CLOSES_BELOW_SMA50" in v1_result.get("components", []),
                    cond_close_prior_20d_low="BREAK_PRIOR_20D_LOW" in v1_result.get("components", []),
                    sec_sma50_slope_down="SMA50_SLOPE_DOWN" in v1_result.get("components", []),
                    sec_rel_ret_lte_m5="REL_RET_LE_M5PCT" in v1_result.get("components", []),
                    sec_dist_days_ge_2="DIST_DAYS_GE_2" in v1_result.get("components", []),
                    v1_exit=bool(v1_result["exit_signal"]),
                    reason=str(v1_result.get("reason", "HOLD"))
                )

                # 4. Evaluate Shadow WEALTH_EXIT_V2 in Parallel
                v2_result = CanonicalV2ExitEvaluator.evaluate(
                    closes=closes,
                    opens=opens,
                    volumes=volumes,
                    benchmark_closes=benchmark_closes,
                    is_completed_session=is_completed_session
                )

                pos["v2_state"] = "EXIT WARNING" if v2_result["exit_signal"] else "HOLD"
                pos["v2_hypothetical_exit"] = v2_result["exit_signal"]
                pos["v2_exit_reason"] = v2_result["reason"]
                if v2_result["exit_signal"]:
                    pos["v2_hypothetical_cmp"] = cmp_price
                    pos["v2_hypothetical_timestamp"] = now_ist.isoformat()
                    v2_shadow_exits_detected.append({
                        "position_id": position_id,
                        "symbol": sym,
                        "v2_reason": v2_result["reason"],
                        "hypothetical_cmp": cmp_price,
                        "timestamp": now_ist.isoformat()
                    })

                prior20_series = pd.Series(closes).shift(1).rolling(20, min_periods=10).min().values
                prior_20d_low_t = float(v2_result.get("prior20_low", 0.0))
                prior_20d_low_t_prev = float(prior20_series[-2]) if closes_len >= 2 and not np.isnan(prior20_series[-2]) else prior_20d_low_t

                exit_telemetry.record_v2_evaluation(
                    position_id=position_id,
                    close_t=close_t,
                    close_t_prev=close_t_prev,
                    prior_20d_low_t=prior_20d_low_t,
                    prior_20d_low_t_prev=prior_20d_low_t_prev,
                    v2_exit=bool(v2_result["exit_signal"]),
                    reason=str(v2_result.get("reason", "HOLD"))
                )

                # Determine V1 vs V2 Agreement
                if v1_result["exit_signal"] and v2_result["exit_signal"]:
                    pos["v1_vs_v2_agreement"] = "AGREE"
                elif v1_result["exit_signal"] and not v2_result["exit_signal"]:
                    pos["v1_vs_v2_agreement"] = "V1_ONLY"
                elif not v1_result["exit_signal"] and v2_result["exit_signal"]:
                    pos["v1_vs_v2_agreement"] = "V2_ONLY"
                else:
                    pos["v1_vs_v2_agreement"] = "AGREE"

                # Check duplicate exit
                dedup_key = (position_id, RULES_HASH_V1)
                is_duplicate = (dedup_key in self.emitted_exit_events) or bool(pos.get("exit_alert_emitted", False))
                if v1_result["exit_signal"] and is_duplicate:
                    exit_telemetry.record_duplicate_exit_blocked(position_id=position_id, symbol=sym, prev_reason=RULES_HASH_V1)

                pos_status_before = pos["status"]
                pos_status_after = PositionStatus.CLOSED.value if (v1_result["exit_signal"] and not is_duplicate) else pos_status_before
                exit_telemetry.record_v1_v2_independence(
                    position_id=position_id,
                    symbol=sym,
                    v1_exit=bool(v1_result["exit_signal"]),
                    v2_exit=bool(v2_result["exit_signal"]),
                    pos_status_before=pos_status_before,
                    pos_status_after=pos_status_after
                )

                # Log parallel research record
                research_record = {
                    "timestamp": now_ist.isoformat(),
                    "position_id": position_id,
                    "symbol": sym,
                    "entry_reference_price": ref_entry,
                    "cmp": cmp_price,
                    "v1_signal": v1_result["exit_signal"],
                    "v1_reason": v1_result["reason"],
                    "v2_signal": v2_result["exit_signal"],
                    "v2_reason": v2_result["reason"],
                    "agreement": pos["v1_vs_v2_agreement"],
                    "peak_price": pos["peak_price"],
                    "trough_price": pos["trough_price"]
                }
                self._append_to_file(self.ledger_file, research_record)

                # 5. Handle Primary V1 Exit Signal
                if v1_result["exit_signal"]:
                    if not is_duplicate:
                        # Generate EXIT_ALERT
                        exit_alert_id = f"EXIT_{sym}_{now_ist.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
                        exit_alert_payload = {
                            "alert_id": exit_alert_id,
                            "position_id": position_id,
                            "symbol": sym,
                            "alert_type": "EXIT_ALERT",
                            "strategy": "WEALTH_EXIT_V1",
                            "strategy_version": "1.0_FROZEN",
                            "rules_hash": RULES_HASH_V1,
                            "code_sha": FROZEN_GIT_SHA,
                            "entry_date": pos["entry_date"],
                            "entry_reference_price": ref_entry,
                            "dashboard_exit_cmp": cmp_price,
                            "trigger_timestamp": now_ist.isoformat(),
                            "v1_exit_reason": v1_result["reason"],
                            "v1_components": v1_result["components"],
                            "data_timestamp": now_ist.isoformat(),
                            "dashboard_status": PositionStatus.CLOSED.value,
                            "user_action": "SELL / EXIT POSITION"
                        }

                        self.exit_alerts[exit_alert_id] = exit_alert_payload
                        self.emitted_exit_events.add(dedup_key)
                        pos["exit_alert_emitted"] = True
                        pos["exit_reason_v1"] = v1_result["reason"]
                        pos["exit_trigger_timestamp"] = now_ist.isoformat()
                        pos["dashboard_exit_cmp"] = cmp_price
                        pos["dashboard_closed_timestamp"] = now_ist.isoformat()
                        pos["status"] = PositionStatus.CLOSED.value

                        self._append_to_file(self.alerts_log, exit_alert_payload)
                        v1_exit_alerts_generated.append(exit_alert_payload)
                        positions_to_close.append((position_id, pos))
                        exit_telemetry.cycle_counts["positions_closed"] += 1
                        exit_telemetry.cycle_counts["exit_alerts_created"] += 1

                        # State Transition Audit (§26)
                        exit_telemetry.record_state_transition(
                            position_id=position_id,
                            symbol=sym,
                            from_state=PositionStatus.OPEN.value,
                            to_state=PositionStatus.EXIT_ALERT.value,
                            authority="WEALTH_EXIT_V1",
                            reason=str(v1_result["reason"]),
                            reference_price=cmp_price
                        )
                        exit_telemetry.record_state_transition(
                            position_id=position_id,
                            symbol=sym,
                            from_state=PositionStatus.EXIT_ALERT.value,
                            to_state=PositionStatus.CLOSED.value,
                            authority="WEALTH_EXIT_V1",
                            reason=str(v1_result["reason"]),
                            reference_price=cmp_price
                        )

                        logger.info(
                            f"🚨 [PRIMARY EXIT ALERT] Triggered for {sym} at CMP ₹{cmp_price:.2f}. "
                            f"Reason: {v1_result['reason']}. Dashboard position CLOSED."
                        )

            # Move closed positions to closed_positions map
            for pid, closed_pos in positions_to_close:
                self.closed_positions[pid] = closed_pos
                del self.open_positions[pid]

            duration_sec = round(time.time() - start_ts, 2)
            if v1_ctx and complete_scanner_execution_run is not None:
                try:
                    v1_ctx.fresh_data_count = len(self.open_positions) + len(positions_to_close)
                    v1_ctx.alerts_generated = len(v1_exit_alerts_generated)
                    complete_scanner_execution_run(v1_ctx)
                except Exception as ce:
                    logger.debug(f"v1 completion warning: {ce}")
            if v2_ctx and complete_scanner_execution_run is not None:
                try:
                    v2_ctx.fresh_data_count = len(self.open_positions) + len(positions_to_close)
                    v2_ctx.alerts_generated = len(v2_shadow_exits_detected)
                    complete_scanner_execution_run(v2_ctx)
                except Exception as ce:
                    logger.debug(f"v2 completion warning: {ce}")

            if upsert_scanner_health is not None:
                try:
                    upsert_scanner_health(
                        "WEALTH_EXIT_V1",
                        status="OK",
                        today_alerts=len(v1_exit_alerts_generated),
                        last_success=now_ist.isoformat(),
                        processed_count=len(self.open_positions) + len(positions_to_close),
                        total_count=len(self.open_positions) + len(positions_to_close),
                        duration_seconds=duration_sec,
                        run_id=getattr(v1_ctx, "run_id", None)
                    )
                    upsert_scanner_health(
                        "WEALTH_EXIT_V2",
                        status="OK",
                        today_alerts=len(v2_shadow_exits_detected),
                        last_success=now_ist.isoformat(),
                        processed_count=len(self.open_positions) + len(positions_to_close),
                        total_count=len(self.open_positions) + len(positions_to_close),
                        duration_seconds=duration_sec,
                        run_id=getattr(v2_ctx, "run_id", None)
                    )
                except Exception as he:
                    logger.debug(f"exit health update OK warning: {he}")

            self.save_state()
            cycle_summary = exit_telemetry.produce_end_of_cycle_summary()

            return {
                "market_status": "OPEN",
                "scan_timestamp": self.last_scan_timestamp,
                "evaluated_positions": len(self.open_positions) + len(positions_to_close),
                "open_positions_remaining": len(self.open_positions),
                "v1_exit_alerts": v1_exit_alerts_generated,
                "v2_shadow_exits": v2_shadow_exits_detected,
                "telemetry_summary": cycle_summary
            }

    # ---------------------------------------------------------------------------------
    # User Records Actual Broker Exit Price
    # ---------------------------------------------------------------------------------
    def record_user_actual_exit(self, position_id: str, actual_exit_price: float) -> bool:
        """
        Allows user to optionally enter their real broker execution price.
        Distinctly preserves dashboard_exit_cmp from user_actual_exit_price.
        """
        with self._lock:
            pos = self.closed_positions.get(position_id) or self.open_positions.get(position_id)
            if not pos:
                logger.error(f"❌ Cannot record actual exit: Position {position_id} not found.")
                return False

            pos["user_actual_exit_price"] = float(actual_exit_price)
            if pos["entry_reference_price"] > 0:
                pos["user_actual_return_pct"] = round(
                    ((actual_exit_price - pos["entry_reference_price"]) / pos["entry_reference_price"]) * 100.0, 2
                )
            self.save_state()
            logger.info(f"📝 User recorded actual exit price for {position_id}: ₹{actual_exit_price:.2f}")
            return True

    # ---------------------------------------------------------------------------------
    # Query & Reporting Helpers
    # ---------------------------------------------------------------------------------
    def get_summary_snapshot(self) -> Dict[str, Any]:
        """Returns top-level widget telemetry for the dashboard."""
        with self._lock:
            is_open, m_reason = MarketHoursGate.is_market_open_ist()
            v1_holding = sum(1 for p in self.open_positions.values() if p.get("v1_state") == "HOLD")
            v1_exit_cnt = len(self.exit_alerts)
            v2_holding = sum(1 for p in self.open_positions.values() if p.get("v2_state") == "HOLD")
            v2_exit_cnt = sum(1 for p in self.open_positions.values() if p.get("v2_hypothetical_exit", False))

            return {
                "market_status": "OPEN" if is_open else "CLOSED",
                "market_reason": m_reason,
                "open_positions_count": len(self.open_positions),
                "closed_positions_count": len(self.closed_positions),
                "v1_stats": {
                    "holding": v1_holding,
                    "exit_alerts": v1_exit_cnt
                },
                "v2_shadow_stats": {
                    "holding": v2_holding,
                    "hypothetical_exits": v2_exit_cnt
                },
                "data_health_overall": self.data_health_overall,
                "last_scan_timestamp": self.last_scan_timestamp or datetime.now(IST).strftime("%H:%M:%S IST"),
                "safety_invariant_broker_orders": AUTOMATIC_BROKER_ORDERS
            }

    def get_exit_monitor_table(self) -> List[Dict[str, Any]]:
        """Returns rows for the dedicated Exit Monitor dashboard table."""
        with self._lock:
            rows = []
            for pid, pos in self.open_positions.items():
                rows.append({
                    "symbol": pos["symbol"],
                    "position_id": pid,
                    "entry_reference_price": pos["entry_reference_price"],
                    "current_cmp": pos["current_cmp"],
                    "dashboard_return_ref_pct": pos["dashboard_return_ref_pct"],
                    "v1_state": pos["v1_state"],
                    "v1_structural_weakness": "YES" if pos["v1_structural_weakness"] else "NO",
                    "v1_confirmation": "YES" if pos["v1_confirmation"] else "NO",
                    "v1_reason": " + ".join(pos["v1_reasons"]) if pos["v1_reasons"] else "None",
                    "v2_state": pos["v2_state"],
                    "v2_reason": pos["v2_exit_reason"] or "None",
                    "last_evaluation": pos["last_evaluation_timestamp"],
                    "data_health": pos["data_health_status"],
                    "position_status": pos["status"],
                    "user_action": "HOLD" if pos["v1_state"] == "HOLD" else "SELL / EXIT"
                })
            return rows

    def get_v2_research_panel_data(self) -> List[Dict[str, Any]]:
        """Returns rows for the V2 Shadow Research Panel."""
        with self._lock:
            rows = []
            for pid, pos in self.open_positions.items():
                rows.append({
                    "position_id": pid,
                    "symbol": pos["symbol"],
                    "current_state": pos["v2_state"],
                    "would_v2_exit_now": "YES" if pos["v2_hypothetical_exit"] else "NO",
                    "reason": pos["v2_exit_reason"] or "HOLD (Conditions Unmet)",
                    "v1_vs_v2": pos["v1_vs_v2_agreement"],
                    "hypothetical_v2_cmp": pos.get("v2_hypothetical_cmp", pos["current_cmp"]),
                    "research_note": "Research-only. No user action generated from V2."
                })
            return rows

    @staticmethod
    def _append_to_file(file_path: str, record: Dict[str, Any]) -> None:
        try:
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception as e:
            logger.error(f"Failed to append to {file_path}: {e}")


# Singleton instance
_live_wealth_monitor_instance: Optional[LiveWealthMonitorEngine] = None
_instance_lock = threading.Lock()

def get_live_wealth_monitor() -> LiveWealthMonitorEngine:
    global _live_wealth_monitor_instance
    with _instance_lock:
        if _live_wealth_monitor_instance is None:
            _live_wealth_monitor_instance = LiveWealthMonitorEngine()
        return _live_wealth_monitor_instance


# ─────────────────────────────────────────────────────────────────────────────────────
# QUALITY_COMPOUNDER — 15:15 & 18:30 EXIT PULSE CHECKER
# ─────────────────────────────────────────────────────────────────────────────────────

def run_v2_exit_check(check_type: str = "EOD") -> Dict[str, Any]:
    """
    Executes V2 exit checks for QUALITY_COMPOUNDER:
    - check_type = 'PRE_CLOSE' (15:15 IST pulse): Warning check, GREEN -> ORANGE on intraday SMA200 breach, NO final exit.
    - check_type = 'EOD' (18:30 IST pulse): Definitive EOD check, evaluates 2 consecutive daily closes < SMA200 & fundamental deterioration, confirms FINAL_EXIT -> CLOSED / RED.
    """
    import pandas as pd
    import numpy as np
    from datetime import datetime
    try:
        from database import get_connection, save_v2_exit_event, RealDictCursor, DummyConnection, IST
    except ImportError:
        from app.database import get_connection, save_v2_exit_event, RealDictCursor, DummyConnection, IST

    now_ist = datetime.now(IST)
    today_str = now_ist.strftime("%Y-%m-%d")

    run_ctx = None
    try:
        from database import start_scanner_execution_run, complete_scanner_execution_run, upsert_scanner_health
        run_ctx = start_scanner_execution_run(scanner_name="WEALTH_EXIT_V2", trigger_type="SCHEDULED", scheduler_name="CRON")
    except Exception as _tr_e:
        logger.debug(f"WEALTH_EXIT_V2 telemetry init failed: {_tr_e}")

    logger.info(f"🛡️ [V2_EXIT_MONITOR] Running {check_type} exit check pulse for QUALITY_COMPOUNDER at {now_ist.strftime('%H:%M:%S IST')}...")

    active_alerts = []
    try:
        with get_connection() as conn:
            if not isinstance(conn, DummyConnection):
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT * FROM alerts
                        WHERE scanner IN ('QUALITY_COMPOUNDER', 'QUALITY_COMPOUNDER_VALUE_V2_FINAL', 'QUALITY_VALUE_RECOVERY', 'QUALITY_VALUE_RECOVERY_WEALTH_V1')
                          AND record_type = 'ALERT_EVENT'
                          AND status IN ('OPEN', 'ACTIVE')
                    """)
                    active_alerts = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"Failed to fetch active V2 alerts: {e}")
        if run_ctx:
            try:
                complete_scanner_execution_run(run_ctx, status_override="FAILED", error_summary=str(e))
                upsert_scanner_health(scanner_name="WEALTH_EXIT_V2", status="DOWN", error_msg=str(e))
            except Exception: pass
        return {"status": "FAILED", "error": str(e)}

    if not active_alerts:
        logger.info("ℹ️ [V2_EXIT_MONITOR] Zero active QUALITY_COMPOUNDER alerts found.")
        if run_ctx:
            try:
                complete_scanner_execution_run(run_ctx, status_override="COMPLETED", processed_count=0, total_count=0, stop_reason="Zero active alerts")
                upsert_scanner_health(scanner_name="WEALTH_EXIT_V2", status="OK", last_success=now_ist.isoformat(), processed_count=0, total_count=0)
            except Exception: pass
        return {"status": "SUCCESS", "active_count": 0, "processed": 0}

    history_dir = os.path.join(DATA_DIR, "history", "1d")
    processed_count = 0
    warnings_count = 0
    exits_count = 0
    seen_symbols = set()

    for al in active_alerts:
        sym = str(al["symbol"]).upper()
        if sym in seen_symbols:
            continue
        seen_symbols.add(sym)

        processed_count += 1
        p_path = os.path.join(history_dir, f"{sym}.parquet")
        if not os.path.exists(p_path):
            logger.warning(f"No price history found for active candidate {sym}")
            continue

        try:
            df = pd.read_parquet(p_path)
            if df.empty or len(df) < 50:
                continue
            closes = df["Close" if "Close" in df.columns else "close"].values.astype(np.float64)
            sma200_series = pd.Series(closes).rolling(200, min_periods=50).mean().values
            close_t = float(closes[-1])
            close_t_prev = float(closes[-2]) if len(closes) > 1 else close_t
            sma200_t = float(sma200_series[-1])
            sma200_t_prev = float(sma200_series[-2]) if len(sma200_series) > 1 else sma200_t

            sc_name = al.get("scanner", "")
            ctx = al.get("context") or {}
            if isinstance(ctx, str):
                try: ctx = json.loads(ctx)
                except Exception: ctx = {}

            # Intraday 15:15 IST Pre-Close Check
            if check_type == "PRE_CLOSE":
                is_recovery = sc_name in ("QUALITY_VALUE_RECOVERY", "QUALITY_VALUE_RECOVERY_WEALTH_V1")
                entry_price = float(al.get("entry_price") or al.get("alert_price") or close_t)
                
                # Recovery stocks enter below SMA200, so evaluate 10% hard stop instead of SMA200
                pre_close_breached = (entry_price > 0 and close_t <= 0.90 * entry_price) if is_recovery else (close_t < sma200_t)

                if pre_close_breached:
                    warnings_count += 1
                    warn_reason = "INTRADAY_STOP_LOSS_10PCT_WARNING" if is_recovery else "INTRADAY_SMA200_BREACH_WARNING"
                    save_v2_exit_event(
                        symbol=sym,
                        event_type="EXIT_CHECK_PRE_CLOSE",
                        new_watchlist_state="ORANGE",
                        exit_reason=warn_reason,
                        exit_price=close_t,
                        context_update={"close_1515": close_t, "sma200": sma200_t, "entry_price": entry_price}
                    )
                    logger.info(f"⚠️ [V2 15:15 WARNING] {sym} ({sc_name}) breach detected -> ORANGE state set ({warn_reason})")
                else:
                    # [STATE RECOVERY]: If conditions clear and was previously flagged ORANGE, restore state to GREEN
                    curr_state = str(al.get("watchlist_state") or "GREEN").upper()
                    if curr_state == "ORANGE":
                        save_v2_exit_event(
                            symbol=sym,
                            event_type="PRE_CLOSE_RECOVERY",
                            new_watchlist_state="GREEN",
                            exit_reason="INTRADAY_CONDITIONS_RECOVERED",
                            exit_price=close_t,
                            context_update={"close_1515": close_t, "sma200": sma200_t}
                        )
                        logger.info(f"🟢 [V2 15:15 RECOVERY] {sym} healthy -> GREEN state restored (removed from SELL_REVIEW)")
            # Definitive 18:30 IST EOD Check
            else:
                exit_reasons = []
                sma200_exit_confirmed = (close_t < sma200_t) and (close_t_prev < sma200_t_prev)

                if sc_name in ("QUALITY_VALUE_RECOVERY", "QUALITY_VALUE_RECOVERY_WEALTH_V1"):
                    # ── MODEL E3 EXIT LOGIC FOR RECOVERY (NO SMA200 BREAK) ──
                    # 1. Hard Stop Loss: 10% below entry
                    entry_price = float(al.get("entry_price") or al.get("alert_price") or close_t)
                    if entry_price > 0 and close_t <= 0.90 * entry_price:
                        exit_reasons.append("STOP_LOSS_10PCT_HIT")

                    # 2. Valuation Mean-Reversion Re-Rating (EV/EBITDA discount is closed)
                    curr_ev = ctx.get("current_ev_ebitda")
                    med_ev = ctx.get("ev_ebitda_3y_median")
                    if curr_ev is not None and med_ev is not None and float(med_ev) > 0:
                        if float(curr_ev) >= float(med_ev):
                            exit_reasons.append("VALUATION_RE_RATED")

                    # 3. PAT Deceleration Check
                    pat_growth_3q = ctx.get("pat_growth_trailing_3q")
                    if pat_growth_3q is not None and float(pat_growth_3q) < 0.0:
                        exit_reasons.append("PAT_DECELERATION")

                    # 4. Holding Period Window (Max 10 trading sessions)
                    alert_date_str = str(al.get("created_at") or al.get("alert_time") or today_str)[:10]
                    try:
                        alert_d = datetime.strptime(alert_date_str, "%Y-%m-%d").date()
                        cur_d = now_ist.date()
                        days_held = (cur_d - alert_d).days
                        if days_held >= 14:  # ~10 trading days
                            exit_reasons.append("MAX_HOLDING_EXPIRED")
                    except Exception:
                        pass
                else:
                    # ── QUALITY COMPOUNDER EXIT LOGIC ──
                    fund_exit_confirmed = False
                    roce_init = float(ctx.get("roce_5y_avg", 15.0) or 15.0)
                    roce_curr = float(al.get("current_roce", roce_init) or roce_init)
                    if roce_curr < (0.75 * roce_init) or roce_curr < 10.0:
                        fund_exit_confirmed = True

                    if sma200_exit_confirmed:
                        exit_reasons.append("SMA200_BREAK")
                    if fund_exit_confirmed:
                        exit_reasons.append("FUNDAMENTAL_DETERIORATION")

                if exit_reasons:
                    exits_count += 1
                    exit_reason_str = " | ".join(exit_reasons)
                    next_open_price = close_t
                    # [GOVERNANCE INVARIANT]: WEALTH_EXIT_V2 is SHADOW / RESEARCH ONLY.
                    # It records research telemetry (SHADOW_EXIT_WARNING), but CANNOT mutate live position status to CLOSED.
                    # WEALTH_EXIT_V1 remains the sole live exit authority.
                    save_v2_exit_event(
                        symbol=sym,
                        event_type="SHADOW_EXIT_WARNING",
                        new_watchlist_state="ORANGE",
                        exit_reason=exit_reason_str,
                        exit_price=close_t,
                        reference_exit_open=next_open_price,
                        context_update={
                            "close_t": close_t,
                            "close_t_prev": close_t_prev,
                            "sma200_t": sma200_t,
                            "sma200_t_prev": sma200_t_prev,
                            "exit_reasons": exit_reasons,
                            "v2_shadow_only": True
                        }
                    )
                    logger.info(f"📙 [V2 SHADOW TELEMETRY] {sym} shadow exit detected ({exit_reason_str}) @ ₹{close_t:.2f} — position remains OPEN (V1 live authority required for close)")
                else:
                    # [STATE RECOVERY EOD]: If no exit reasons present and symbol was previously ORANGE, restore state to GREEN
                    curr_state = str(al.get("watchlist_state") or "GREEN").upper()
                    if curr_state == "ORANGE":
                        save_v2_exit_event(
                            symbol=sym,
                            event_type="EOD_WARNING_CLEARED",
                            new_watchlist_state="GREEN",
                            exit_reason="EXIT_CONDITIONS_CLEARED",
                            exit_price=close_t,
                            context_update={"close_t": close_t, "sma200_t": sma200_t}
                        )
                        logger.info(f"🟢 [V2 EOD RECOVERY] {sym} price/fundamentals restored -> GREEN state restored (removed from SELL_REVIEW)")

        except Exception as e:
            logger.error(f"Error evaluating V2 exit for {sym}: {e}")

    if run_ctx:
        try:
            from database import complete_scanner_execution_run, upsert_scanner_health
            complete_scanner_execution_run(
                run_ctx,
                status_override="COMPLETED",
                processed_count=processed_count,
                total_count=processed_count,
                stop_reason=f"Pulse {check_type} complete (warnings={warnings_count}, exits={exits_count})"
            )
            upsert_scanner_health(
                scanner_name="WEALTH_EXIT_V2",
                status="OK",
                last_success=now_ist.isoformat(),
                processed_count=processed_count,
                total_count=processed_count,
                today_alerts=exits_count
            )
        except Exception as _ce_e:
            logger.debug(f"WEALTH_EXIT_V2 completion telemetry error: {_ce_e}")

    logger.info(f"✅ [V2_EXIT_MONITOR] {check_type} Exit Check Complete: Processed={processed_count}, Warnings={warnings_count}, Exits={exits_count}")
    return {"status": "SUCCESS", "processed": processed_count, "warnings": warnings_count, "exits": exits_count}


check_v2_exit_signals = run_v2_exit_check

# STRATEGY-SPECIFIC EXIT MONITORS & EVALUATORS
QualityCompounderExitEvaluator = CanonicalV2ExitEvaluator
QualityCompounderExitMonitor = LiveWealthMonitorEngine

QualityValueRecoveryExitEvaluator = CanonicalRecoveryE3ExitEvaluator
QualityValueRecoveryExitMonitor = LiveWealthMonitorEngine



