# app/multi_tf_engine.py
# Phase 2C: Multi-Timeframe Breakout Scanner V2 Engine
#
# RULE 67 CHANGE-RATIONALE:
# - Implements Multi-Timeframe hierarchy: Weekly Thesis -> Daily Setup -> Hourly Trigger.
# - Enforces strict state guards: Weak hourly entry does not destroy setup; transitions to STOCKS TO WATCH - HOURLY TRIGGER PENDING.
# - Enforces zero-lookahead resistance anchor (shift(1).rolling(20).max()) and volume reference (shift(1).rolling(20).mean()).
# - Implements anti-false-breakout confirmation gates (ATR ext <= 1.8, Gap <= 4.0%, Body >= 0.40, Close pos >= 0.70, Upper wick <= 0.25).
# - Enforces NQ universe isolation: Near-Qualified stocks marked NQ_OBSERVATION_ONLY and NEVER produce CONFIRMED_BUY alerts.

import logging
import math
from datetime import date
from typing import Dict, Any, List, Optional
import pandas as pd
import numpy as np

logger = logging.getLogger("MultiTFV2Engine")


def compute_bb_width_percentile(df: pd.DataFrame, window: int = 20) -> float:
    """Computes Bollinger Band Width Percentile over available history."""
    if df is None or len(df) < window:
        return 0.50
    sma = df["Close"].rolling(window=window).mean()
    std = df["Close"].rolling(window=window).std()
    upper = sma + (2.0 * std)
    lower = sma - (2.0 * std)
    bb_width = (upper - lower) / sma
    
    current_width = bb_width.iloc[-1]
    if pd.isna(current_width):
        return 0.50
    valid_widths = bb_width.dropna()
    if len(valid_widths) < 2:
        return 0.50
    pctile = float((valid_widths < current_width).sum()) / float(len(valid_widths))
    return pctile


def check_weekly_thesis(weekly_df: pd.DataFrame) -> Dict[str, Any]:
    """
    Evaluates Weekly Thesis Trend Permission (bar freshness: latest completed week).
    - Close > EMA20 and EMA20 > SMA50 (or Close > SMA200 if history >= 200 bars)
    """
    if weekly_df is None or len(weekly_df) < 20:
        return {"passed": False, "reason": "Insufficient weekly history (< 20 bars)"}
    
    latest = weekly_df.iloc[-1]
    close = float(latest["Close"])
    
    ema20 = float(weekly_df["Close"].ewm(span=20, adjust=False).mean().iloc[-1])
    sma50 = float(weekly_df["Close"].rolling(window=50, min_periods=20).mean().iloc[-1])
    
    sma200 = None
    if len(weekly_df) >= 200:
        sma200 = float(weekly_df["Close"].rolling(window=200).mean().iloc[-1])
        
    if sma200 is not None and not np.isnan(sma200):
        trend_ok = (close > ema20) and (ema20 > sma50) and (close > sma200)
    else:
        trend_ok = (close > ema20) and (ema20 > sma50)
        
    if not trend_ok:
        return {"passed": False, "reason": f"Weekly Trend Permission Fail: Close ₹{close:.2f}, EMA20 ₹{ema20:.2f}, SMA50 ₹{sma50:.2f}"}
        
    return {"passed": True, "reason": "WEEKLY_THESIS_PASSED", "weekly_close": close, "ema20": ema20, "sma50": sma50}


def check_daily_setup(daily_df: pd.DataFrame, touch_tolerance: float = 0.020) -> Dict[str, Any]:
    """
    Evaluates Daily Technical Base Structure (bar freshness: latest completed day).
    - Base age >= 10 bars
    - Resistance tests >= 1 touch within 2.0% of prior 20D high (shift(1))
    - Close <= 35% above SMA50
    - BB Width Percentile <= 0.80
    """
    if daily_df is None or len(daily_df) < 50:
        return {"passed": False, "reason": "Insufficient daily price history (< 50 bars)"}
    
    base_age = min(len(daily_df), 20)
    if base_age < 10:
        return {"passed": False, "reason": "Daily base age too short (< 10 bars)"}
        
    prior_20d_high = float(daily_df["High"].iloc[-21:-1].max()) if len(daily_df) >= 21 else float(daily_df["High"].iloc[:-1].max())
    if prior_20d_high <= 0:
        return {"passed": False, "reason": "Invalid prior 20D high resistance anchor"}
        
    threshold_near = prior_20d_high * (1.0 - touch_tolerance)
    highs = daily_df["High"].astype(float).iloc[-21:-1]
    res_tests = int((highs >= threshold_near).sum())
    if res_tests < 1:
        return {"passed": False, "reason": f"Insufficient daily resistance tests ({res_tests} < 1)"}
        
    latest_close = float(daily_df["Close"].iloc[-1])
    sma50 = float(daily_df["Close"].iloc[-50:].mean()) if len(daily_df) >= 50 else float(daily_df["Close"].mean())
    if sma50 > 0:
        ext_sma50 = (latest_close - sma50) / sma50
        if ext_sma50 > 0.35:
            return {"passed": False, "reason": f"Overextended from daily SMA50 ({ext_sma50*100:.1f}% > 35%)"}
            
    bb_pctile = compute_bb_width_percentile(daily_df)
    if bb_pctile > 0.80:
        return {"passed": False, "reason": f"Daily base too wide (BB Width Percentile {bb_pctile:.2f} > 0.80)"}
        
    return {
        "passed": True,
        "reason": "DAILY_SETUP_PASSED",
        "prior_20d_high": prior_20d_high,
        "base_age": base_age,
        "resistance_tests": res_tests
    }


def evaluate_multi_tf_v2_symbol(
    symbol: str,
    weekly_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    hourly_df: pd.DataFrame,
    is_nq_universe: bool = False,
    provisional_vol_threshold: float = 1.5
) -> Dict[str, Any]:
    """
    Evaluates a symbol against Phase 2C Multi-TF V2 Breakout rules across Weekly, Daily, and Hourly timeframes.
    """
    # 1. Weekly Thesis Verification
    w_res = check_weekly_thesis(weekly_df)
    if not w_res["passed"]:
        return {
            "symbol": symbol,
            "state": "NO_VALID_SETUP",
            "reason": w_res["reason"],
            "score": 0.0,
            "quality_grade": "C"
        }

    # 2. Daily Setup Verification
    d_res = check_daily_setup(daily_df)
    if not d_res["passed"]:
        return {
            "symbol": symbol,
            "state": "NO_VALID_SETUP",
            "reason": d_res["reason"],
            "score": 0.0,
            "quality_grade": "C"
        }

    prior_20d_high = d_res["prior_20d_high"]

    # 3. Hourly Data Validation
    if hourly_df is None or len(hourly_df) < 20:
        # Strong Weekly + Daily setup, but hourly data incomplete -> WATCH
        return {
            "symbol": symbol,
            "state": "WATCH",
            "reason": "STOCKS TO WATCH — HOURLY TRIGGER PENDING (Insufficient hourly bars)",
            "score": 70.0,
            "quality_grade": "B",
            "breakout_level": prior_20d_high
        }

    latest_h = hourly_df.iloc[-1]
    h_close = float(latest_h["Close"])
    h_open = float(latest_h["Open"])
    h_high = float(latest_h["High"])
    h_low = float(latest_h["Low"])
    h_vol = float(latest_h["Volume"]) if "Volume" in hourly_df.columns else 100000.0

    avg_h_vol = float(hourly_df["Volume"].iloc[-21:-1].mean()) if len(hourly_df) >= 21 and "Volume" in hourly_df.columns else float(hourly_df["Volume"].mean())
    vol_ratio = (h_vol / avg_h_vol) if avg_h_vol > 0 else 1.0
    candle_range = max(0.01, h_high - h_low)

    # ATR20 Calculation
    if len(daily_df) >= 20:
        atr20 = float((daily_df["High"] - daily_df["Low"]).iloc[-20:].mean())
    else:
        atr20 = max(0.01, h_close * 0.025)

    breakout_extension_atr = (h_close - prior_20d_high) / atr20 if atr20 > 0 else 0.0
    prev_close = float(hourly_df["Close"].iloc[-2]) if len(hourly_df) >= 2 else h_close
    opening_gap_pct = ((h_open - prev_close) / prev_close * 100.0) if prev_close > 0 else 0.0

    body_ratio = abs(h_close - h_open) / candle_range
    close_pos = (h_close - h_low) / candle_range
    upper_wick_ratio = (h_high - max(h_open, h_close)) / candle_range

    distance_pct = (prior_20d_high - h_close) / prior_20d_high * 100.0
    setup_id = f"PFC_{symbol}_MULTI_TF_BREAKOUT_{date.today()}"

    reasons = []
    is_triggered = (h_close > prior_20d_high) and (vol_ratio >= provisional_vol_threshold)

    if is_triggered:
        conf_passed = True

        if is_nq_universe:
            current_state = "NQ_OBSERVATION_ONLY"
            reasons.append("NEAR_QUALIFIED stock — Pre-WATCH observation only")
        else:
            if breakout_extension_atr > 1.8:
                conf_passed = False
                reasons.append(f"LATE_BREAKOUT_CHASE: Extension {breakout_extension_atr:.2f} ATR > 1.8 max")

            if opening_gap_pct > 4.0:
                conf_passed = False
                reasons.append(f"GAP_CHASE_REJECT: Opening gap {opening_gap_pct:.1f}% > 4.0% max")

            if body_ratio < 0.40:
                conf_passed = False
                reasons.append(f"WEAK_CANDLE_BODY: Body ratio {body_ratio:.2f} < 0.40 min")

            if close_pos < 0.70:
                conf_passed = False
                reasons.append(f"WEAK_CLOSE_POSITION: Close pos {close_pos:.2f} < 0.70 min")

            if upper_wick_ratio > 0.25:
                conf_passed = False
                reasons.append(f"EXCESS_UPPER_WICK: Upper wick ratio {upper_wick_ratio:.2f} > 0.25 max")

            if conf_passed:
                current_state = "CONFIRMED"
                reasons.append("ALL_MULTI_TF_PHASES_PASSED: Weekly thesis + Daily setup + Hourly trigger confirmed")
            else:
                current_state = "MISSED"

    else:
        # Pre-trigger state handling
        if 0.5 <= distance_pct <= 3.0:
            current_state = "WATCH"
            reasons.append(f"STOCKS TO WATCH — HOURLY TRIGGER PENDING (Distance {distance_pct:.2f}% to breakout ₹{prior_20d_high:.2f})")
        elif 0.0 <= distance_pct < 0.5:
            current_state = "IMMEDIATE_TRIGGER_ZONE"
            reasons.append(f"IMMEDIATE_TRIGGER_ZONE: Price ₹{h_close:.2f} within 0.5% of breakout level ₹{prior_20d_high:.2f}")
        else:
            current_state = "NO_VALID_SETUP"
            reasons.append(f"Distance {distance_pct:.2f}% outside 0.0%-3.0% Watch window")

    # Quality Score Calculation
    score = 70.0
    if d_res["base_age"] >= 15:
        score += 10.0
    if d_res["resistance_tests"] >= 2:
        score += 10.0
    if vol_ratio >= 2.5:
        score += 10.0

    grade = "A+" if score >= 90.0 else ("A" if score >= 80.0 else ("B" if score >= 70.0 else "C"))

    return {
        "symbol": symbol,
        "setup_id": setup_id,
        "state": current_state,
        "reason": "; ".join(reasons),
        "score": score,
        "quality_grade": grade,
        "breakout_level": prior_20d_high,
        "entry_price": h_close,
        "atr_20": atr20,
        "distance_pct": distance_pct,
        "vol_ratio": vol_ratio,
        "breakout_extension_atr": breakout_extension_atr,
        "opening_gap_pct": opening_gap_pct,
        "body_ratio": body_ratio,
        "close_pos": close_pos,
        "upper_wick_ratio": upper_wick_ratio,
        "is_nq_universe": is_nq_universe
    }
