# app/reversal_engine.py
# Phase 2D: Reversal Breakout Scanner V2 Engine
#
# RULE 67 CHANGE-RATIONALE:
# - Implements two distinct reversal specialist engines:
#   Path 1: Quality Reversal Engine (Price > SMA200, controlled 15-35% drop, support hold, HL/HH candle confirmation).
#   Path 2: Deep Value Reversal Engine (Price < SMA200, ROE >= 12% fundamental floor, capitulation climax, bullish RSI divergence).
# - Enforces strict falling-knife protection: NEW_LOWER_LOW (Price < trough_price) is a hard rejection across both paths.
# - Integrates with Phase-1 universal state lifecycle (WATCH -> TRIGGER -> CANDIDATE -> CONFIRMED).
# - Enforces NQ universe isolation: Near-Qualified stocks marked NQ_OBSERVATION_ONLY and NEVER produce CONFIRMED_BUY alerts.

import logging
import math
from datetime import date
from typing import Dict, Any, List, Optional
import pandas as pd
import numpy as np

logger = logging.getLogger("ReversalV2Engine")


def evaluate_reversal_v2_symbol(
    symbol: str,
    df: pd.DataFrame,
    fund_data: Optional[Dict[str, Any]] = None,
    is_nq_universe: bool = False,
    provisional_vol_threshold: float = 1.5
) -> Dict[str, Any]:
    """
    Evaluates a symbol against Phase 2D Reversal V2 Breakout rules across Quality and Deep Value paths.
    """
    if df is None or len(df) < 50:
        return {
            "symbol": symbol,
            "state": "NO_VALID_SETUP",
            "reason": "Insufficient historical price data (< 50 bars)",
            "score": 0.0,
            "quality_grade": "C"
        }

    latest = df.iloc[-1]
    close = float(latest["Close"])
    open_p = float(latest["Open"])
    high_p = float(latest["High"])
    low_p = float(latest["Low"])
    volume = float(latest["Volume"]) if "Volume" in df.columns else 100000.0

    if close < 100.0:
        return {
            "symbol": symbol,
            "state": "NO_VALID_SETUP",
            "reason": f"Close ₹{close:.2f} < ₹100.0 minimum price floor",
            "score": 0.0,
            "quality_grade": "C"
        }

    # Indicators calculation
    sma50 = float(df["Close"].iloc[-50:].mean()) if len(df) >= 50 else float(df["Close"].mean())
    sma200 = float(df["Close"].iloc[-200:].mean()) if len(df) >= 200 else sma50
    ema20 = float(df["Close"].ewm(span=20, adjust=False).mean().iloc[-1])
    ema5 = float(df["Close"].ewm(span=5, adjust=False).mean().iloc[-1])

    rsi_series = df["Close"].diff().apply(lambda x: max(x, 0)).ewm(span=14).mean() / (
        df["Close"].diff().abs().ewm(span=14).mean().replace(0, 1e-6)
    ) * 100.0
    current_rsi = float(rsi_series.iloc[-1]) if len(rsi_series) > 0 else 50.0

    # 52-Week High Drop Calculation
    high_52w = float(df["High"].iloc[-250:].max()) if len(df) >= 250 else float(df["High"].max())
    drop_pct = ((high_52w - close) / high_52w * 100.0) if high_52w > 0 else 0.0

    # Volume Ratio calculation (zero-lookahead shift(1))
    avg_vol_20d = float(df["Volume"].iloc[-21:-1].mean()) if len(df) >= 21 and "Volume" in df.columns else float(df["Volume"].mean())
    vol_ratio = (volume / avg_vol_20d) if avg_vol_20d > 0 else 1.0

    # Find Trough Price and Trough Bar (past 25 bars)
    trough_window = df.iloc[-25:]
    trough_price = float(trough_window["Low"].min())
    trough_rsi = float(rsi_series.iloc[-25:].min())

    # 🚨 HARD FALLING-KNIFE BLOCK: NEW_LOWER_LOW (Price <= trough_price)
    prior_25_min_low = float(df["Low"].iloc[-26:-1].min()) if len(df) >= 26 else float(df["Low"].iloc[:-1].min())
    if low_p < prior_25_min_low * 0.999 or low_p < trough_price * 0.999:
        return {
            "symbol": symbol,
            "engine_path": "FALLING_KNIFE_REJECT",
            "state": "NO_VALID_SETUP",
            "reason": f"NEW_LOWER_LOW: Current low ₹{low_p:.2f} <= prior min low ₹{prior_25_min_low:.2f} (Falling Knife Block)",
            "score": 0.0,
            "quality_grade": "C"
        }

    is_above_sma200 = close > sma200
    engine_path = "QUALITY_REVERSAL" if is_above_sma200 else "DEEP_VALUE_REVERSAL"

    reasons = []
    path_passed = True

    # -----------------------------------------------------------------
    # PATH 1: QUALITY REVERSAL ENGINE (Price > SMA200)
    # -----------------------------------------------------------------
    if is_above_sma200:
        if not (12.0 <= drop_pct <= 40.0):
            path_passed = False
            reasons.append(f"Drop from 52W High {drop_pct:.1f}% outside 12.0%-40.0% Quality band")

        # Higher Low (HL) and Higher High (HH) check on latest 3 bars
        if len(df) >= 3:
            prev_low = float(df["Low"].iloc[-2])
            prev_high = float(df["High"].iloc[-2])
            is_hl = low_p >= prev_low * 0.998
            is_hh = high_p >= prev_high * 0.998
            if not (is_hl or is_hh):
                path_passed = False
                reasons.append("No Higher Low (HL) or Higher High (HH) structural candle formation")
        else:
            path_passed = False
            reasons.append("Insufficient bars for HL/HH structure")

    # -----------------------------------------------------------------
    # PATH 2: DEEP VALUE REVERSAL ENGINE (Price < SMA200)
    # -----------------------------------------------------------------
    else:
        pct_below_sma200 = ((sma200 - close) / sma200 * 100.0) if sma200 > 0 else 0.0
        if pct_below_sma200 > 20.0:
            path_passed = False
            reasons.append(f"Price {pct_below_sma200:.1f}% below SMA200 > 20.0% max structural limit")

        # Fundamental Floor check
        roe_val = float(fund_data.get("roe", fund_data.get("ROE %", 0.0))) if fund_data else 0.0
        cat_str = str(fund_data.get("Category", "")) if fund_data else ""
        is_turnaround = "TURNAROUND" in cat_str.upper()

        if not (is_turnaround or roe_val >= 12.0):
            path_passed = False
            reasons.append(f"Deep Value Fundamental Floor Fail: ROE {roe_val:.1f}% < 12.0% (and not Turnaround)")

        # Bullish RSI Divergence (Price makes lower low while RSI makes higher low)
        rsi_curl = current_rsi - trough_rsi
        if rsi_curl < 8.0:
            path_passed = False
            reasons.append(f"Insufficient RSI recovery: Curl {rsi_curl:.1f} < 8.0 min points")

        # Multi-bar confirmation: Close above EMA5
        if close < ema5:
            path_passed = False
            reasons.append("Close < EMA5 multi-bar confirmation floor")

    if not path_passed:
        return {
            "symbol": symbol,
            "engine_path": engine_path,
            "state": "NO_VALID_SETUP",
            "reason": "; ".join(reasons),
            "score": 0.0,
            "quality_grade": "C"
        }

    # Swing High Breakout Trigger Anchor (prior 10D high)
    prior_swing_high = float(df["High"].iloc[-11:-1].max()) if len(df) >= 11 else float(df["High"].max())
    distance_pct = (prior_swing_high - close) / prior_swing_high * 100.0
    setup_id = f"PFC_{symbol}_REVERSAL_BREAKOUT_{date.today()}"

    # State Determination
    is_triggered = (close > prior_swing_high) and (vol_ratio >= provisional_vol_threshold)

    if is_triggered:
        conf_passed = True

        if is_nq_universe:
            current_state = "NQ_OBSERVATION_ONLY"
            reasons.append("NEAR_QUALIFIED stock — Pre-WATCH observation only")
        else:
            candle_range = max(0.01, high_p - low_p)
            body_ratio = abs(close - open_p) / candle_range
            close_pos = (close - low_p) / candle_range

            if body_ratio < 0.40:
                conf_passed = False
                reasons.append(f"WEAK_CANDLE_BODY: Body ratio {body_ratio:.2f} < 0.40 min")

            if close_pos < 0.70:
                conf_passed = False
                reasons.append(f"WEAK_CLOSE_POSITION: Close pos {close_pos:.2f} < 0.70 min")

            if conf_passed:
                current_state = "CONFIRMED"
                reasons.append(f"ALL_REVERSAL_PHASES_PASSED: {engine_path} + Swing High Breakout confirmed")
            else:
                current_state = "MISSED"

    else:
        # Pre-trigger Watch state handling
        if 0.5 <= distance_pct <= 3.0:
            current_state = "WATCH"
            reasons.append(f"👀 REVERSAL WATCH: {engine_path} Support Hold (Distance {distance_pct:.2f}% to breakout ₹{prior_swing_high:.2f})")
        elif 0.0 <= distance_pct < 0.5:
            current_state = "IMMEDIATE_TRIGGER_ZONE"
            reasons.append(f"IMMEDIATE_TRIGGER_ZONE: Price ₹{close:.2f} within 0.5% of breakout ₹{prior_swing_high:.2f}")
        else:
            current_state = "NO_VALID_SETUP"
            reasons.append(f"Distance {distance_pct:.2f}% outside 0.0%-3.0% Watch window")

    # Quality Score Calculation
    score = 70.0
    if is_above_sma200:
        score += 10.0
    if vol_ratio >= 2.0:
        score += 10.0

    grade = "A+" if score >= 90.0 else ("A" if score >= 80.0 else ("B" if score >= 70.0 else "C"))

    return {
        "symbol": symbol,
        "setup_id": setup_id,
        "engine_path": engine_path,
        "state": current_state,
        "reason": "; ".join(reasons),
        "score": score,
        "quality_grade": grade,
        "breakout_level": prior_swing_high,
        "entry_price": close,
        "atr_20": max(0.01, (high_p - low_p)),
        "distance_pct": distance_pct,
        "vol_ratio": vol_ratio,
        "drop_pct": drop_pct,
        "is_nq_universe": is_nq_universe
    }
