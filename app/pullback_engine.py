# app/pullback_engine.py
# Phase 2E: Pullback Breakout Scanner V2 Engine
#
# RULE 67 CHANGE-RATIONALE:
# - Implements Pullback V2 engine with support hierarchy: Structural Swing Low > Anchored VWAP > EMA20 > Fibonacci.
# - Enforces Distribution Risk Scoring System: Hard reject if distribution_risk_score >= 3 OR Close < support_level - 0.5 * ATR.
# - Enforces support_holds == True: Low >= support_level - 0.5 * ATR and Close >= support_level - 0.2 * ATR.
# - Enforces No Volume Bypass: Resumption trigger candle requires Volume Ratio >= provisional_vol_threshold (default 1.5x).
# - Emits Stage 1 to Stage 5 progress tracking for diagnostic visibility.
# - Enforces NQ universe isolation: Near-Qualified stocks marked NQ_OBSERVATION_ONLY and NEVER produce CONFIRMED_BUY alerts.

import logging
import math
from datetime import date
from typing import Dict, Any, List, Optional
import pandas as pd
import numpy as np

logger = logging.getLogger("PullbackV2Engine")


def evaluate_pullback_v2_symbol(
    symbol: str,
    df: pd.DataFrame,
    fund_data: Optional[Dict[str, Any]] = None,
    is_nq_universe: bool = False,
    provisional_vol_threshold: float = 1.5
) -> Dict[str, Any]:
    """
    Evaluates a symbol against Phase 2E Pullback V2 Breakout rules.
    """
    if df is None or len(df) < 50:
        return {
            "symbol": symbol,
            "state": "NO_VALID_SETUP",
            "reason": "Insufficient historical price data (< 50 bars)",
            "score": 0.0,
            "quality_grade": "C",
            "stage_progress": 0
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
            "quality_grade": "C",
            "stage_progress": 0
        }

    # Indicators calculation
    sma50_series = df["Close"].rolling(window=50, min_periods=20).mean()
    sma200_series = df["Close"].rolling(window=200, min_periods=50).mean() if len(df) >= 200 else sma50_series
    sma50 = float(sma50_series.iloc[-1])
    sma200 = float(sma200_series.iloc[-1])
    ema20 = float(df["Close"].ewm(span=20, adjust=False).mean().iloc[-1])

    c_range = max(0.01, high_p - low_p)
    atr20 = float((df["High"] - df["Low"]).rolling(20).mean().iloc[-1]) if len(df) >= 20 else (close * 0.025)
    body = abs(close - open_p)

    # 1. STAGE 1: PRIMARY TREND VALIDATION
    sma50_slope = (float(sma50_series.iloc[-1]) - float(sma50_series.iloc[-10])) if len(sma50_series) >= 10 else 1.0
    sma200_aligned = (sma50 >= sma200 * 0.999) if len(df) >= 200 else True
    primary_trend_pass = (close > sma50 * 0.98) and sma200_aligned and (sma50_slope >= -0.01)

    if not primary_trend_pass:
        return {
            "symbol": symbol,
            "state": "NO_VALID_SETUP",
            "reason": f"Primary Trend Fail: Close ₹{close:.2f} <= SMA50 ₹{sma50:.2f} > SMA200 ₹{sma200:.2f}",
            "score": 0.0,
            "quality_grade": "C",
            "stage_progress": 1
        }

    # 2. STAGE 2: IMPULSE ANCHOR & RETRACEMENT IDENTIFICATION
    swing_low_pivot = float(df["Low"].iloc[-60:-15].min()) if len(df) >= 60 else float(df["Low"].min())
    impulse_high = float(df["High"].iloc[-30:-1].max()) if len(df) >= 30 else float(df["High"].max())

    impulse_return_pct = ((impulse_high - swing_low_pivot) / swing_low_pivot * 100.0) if swing_low_pivot > 0 else 0.0
    retracement_pct = ((impulse_high - close) / (impulse_high - swing_low_pivot) * 100.0) if (impulse_high - swing_low_pivot) > 0 else 0.0

    if impulse_return_pct < 8.0:
        return {
            "symbol": symbol,
            "state": "NO_VALID_SETUP",
            "reason": f"Impulse Return {impulse_return_pct:.1f}% < 8.0% minimum threshold",
            "score": 0.0,
            "quality_grade": "C",
            "stage_progress": 1
        }

    if not (5.0 <= retracement_pct <= 60.0):
        return {
            "symbol": symbol,
            "support_type": "STRUCTURAL_SWING_LOW",
            "state": "NO_VALID_SETUP",
            "reason": f"Retracement Depth {retracement_pct:.1f}% outside 5.0%-60.0% band",
            "score": 0.0,
            "quality_grade": "C",
            "stage_progress": 2
        }

    # 3. SUPPORT HIERARCHY & SUPPORT-HOLD MATHEMATICS
    fib_50 = impulse_high - (0.50 * (impulse_high - swing_low_pivot))
    structural_low = float(df["Low"].iloc[-15:-1].min()) if len(df) >= 15 else float(df["Low"].min())
    avwap = (float((df["Volume"] * df["Close"]).iloc[-20:].sum()) / max(1.0, float(df["Volume"].iloc[-20:].sum()))) if len(df) >= 20 else ema20

    # Support Confluence Measurement
    candidates = [
        ("STRUCTURAL_SWING_LOW", structural_low),
        ("ANCHORED_VWAP", avwap),
        ("EMA20", ema20),
        ("FIBONACCI", fib_50)
    ]

    confluence_count = sum(1 for _, lev in candidates if abs(close - lev) / close <= 0.02)

    # Strict Hierarchy Precedence
    support_type = "EMA20"
    support_level = ema20

    if abs(close - structural_low) / close <= 0.035:
        support_type = "STRUCTURAL_SWING_LOW"
        support_level = structural_low
    elif abs(close - avwap) / close <= 0.025:
        support_type = "ANCHORED_VWAP"
        support_level = avwap
    elif abs(close - ema20) / close <= 0.025:
        support_type = "EMA20"
        support_level = ema20
    elif abs(close - fib_50) / close <= 0.035:
        support_type = "FIBONACCI"
        support_level = fib_50

    distance_to_support_pct = (close - support_level) / support_level * 100.0
    support_holds = (low_p >= support_level - 0.5 * atr20) and (close >= support_level - 0.2 * atr20)

    # 🚨 HARD CLOSE STRUCTURAL BREACH
    if close < (support_level - 0.5 * atr20):
        return {
            "symbol": symbol,
            "state": "NO_VALID_SETUP",
            "reason": f"HARD_STRUCTURAL_REJECT: Close ₹{close:.2f} < support ₹{support_level:.2f} - 0.5*ATR",
            "score": 0.0,
            "quality_grade": "C",
            "stage_progress": 2
        }

    # 4. DISTRIBUTION RISK SCORING SYSTEM
    avg_vol_20d = float(df["Volume"].iloc[-21:-1].mean()) if len(df) >= 21 and "Volume" in df.columns else float(df["Volume"].mean())
    resumption_volume_ratio = (volume / avg_vol_20d) if avg_vol_20d > 0 else 1.0

    # Down-day volume calculation
    down_mask = df["Close"].iloc[-10:-1] < df["Open"].iloc[-10:-1]
    down_vols = df["Volume"].iloc[-10:-1][down_mask]
    avg_down_vol = float(down_vols.mean()) if not down_vols.empty else avg_vol_20d
    down_volume_ratio = (avg_down_vol / avg_vol_20d) if avg_vol_20d > 0 else 1.0

    wide_red_candle = (close < open_p) and (body > 2.0 * atr20)

    distribution_risk_score = 0
    if down_volume_ratio >= 1.3: distribution_risk_score += 1
    if wide_red_candle: distribution_risk_score += 1
    if low_p < support_level - 0.2 * atr20: distribution_risk_score += 1
    if len(df) >= 3 and df["Close"].iloc[-1] < df["Close"].iloc[-2] < df["Close"].iloc[-3]: distribution_risk_score += 1

    if distribution_risk_score >= 3:
        return {
            "symbol": symbol,
            "state": "NO_VALID_SETUP",
            "reason": f"DISTRIBUTION_BREAKDOWN: Distribution Risk Score {distribution_risk_score} >= 3 (Down Vol {down_volume_ratio:.2f}x)",
            "score": 0.0,
            "quality_grade": "C",
            "stage_progress": 2
        }

    # 5. RESUMPTION LEVEL & TRIGGER DEFINITION
    resumption_level = float(df["High"].iloc[-6:-1].max()) if len(df) >= 6 else float(df["High"].max())
    distance_to_resumption_pct = (resumption_level - close) / resumption_level * 100.0

    is_triggered = (close > resumption_level) and (resumption_volume_ratio >= provisional_vol_threshold)
    setup_id = f"PFC_{symbol}_PULLBACK_BREAKOUT_{date.today()}"
    reasons = []

    # 6. STATE LIFECYCLE & STAGE SEPARATION
    if is_triggered:
        if not support_holds:
            return {
                "symbol": symbol,
                "state": "NO_VALID_SETUP",
                "reason": "Resumption trigger occurred but support_holds == False",
                "score": 0.0,
                "quality_grade": "C",
                "stage_progress": 3
            }

        conf_passed = (body / c_range >= 0.40) and ((close - low_p) / c_range >= 0.70)

        if is_nq_universe:
            current_state = "NQ_OBSERVATION_ONLY"
            reasons.append("NEAR_QUALIFIED stock — Pre-WATCH observation only")
            stage_progress = 4
        elif conf_passed:
            current_state = "CONFIRMED"
            reasons.append(f"CONFIRMED_PULLBACK_RESUMPTION: Resumption over ₹{resumption_level:.2f} with {resumption_volume_ratio:.2f}x Vol")
            stage_progress = 5
        else:
            current_state = "MISSED"
            stage_progress = 4

    else:
        # Pre-trigger Watch state
        if 0.5 <= distance_to_resumption_pct <= 3.0:
            current_state = "WATCH"
            reasons.append(f"👀 PULLBACK WATCH (Stage 3): Support {support_type} ₹{support_level:.2f} Holding | Resumption Pending ₹{resumption_level:.2f}")
            stage_progress = 3
        elif 0.0 <= distance_to_resumption_pct < 0.5:
            current_state = "IMMEDIATE_TRIGGER_ZONE"
            reasons.append(f"IMMEDIATE_TRIGGER_ZONE: Price ₹{close:.2f} within 0.5% of resumption ₹{resumption_level:.2f}")
            stage_progress = 4
        else:
            current_state = "NO_VALID_SETUP"
            reasons.append(f"Distance {distance_to_resumption_pct:.2f}% outside 0.0%-3.0% Watch window")
            stage_progress = 2

    # Quality Score Calculation
    score = 70.0
    if support_type == "STRUCTURAL_SWING_LOW": score += 10.0
    elif support_type == "EMA20": score += 5.0

    if pullback_avg_vol := (volume / avg_vol_20d if avg_vol_20d > 0 else 1.0) <= 0.85:
        score += 10.0

    grade = "A+" if score >= 90.0 else ("A" if score >= 80.0 else ("B" if score >= 70.0 else "C"))

    return {
        "symbol": symbol,
        "setup_id": setup_id,
        "support_type": support_type,
        "support_level": support_level,
        "state": current_state,
        "stage_progress": stage_progress,
        "distribution_risk_score": distribution_risk_score,
        "reason": "; ".join(reasons),
        "score": score,
        "quality_grade": grade,
        "breakout_level": resumption_level,
        "entry_price": close,
        "atr_20": atr20,
        "distance_pct": distance_to_resumption_pct,
        "vol_ratio": resumption_volume_ratio,
        "retracement_pct": retracement_pct,
        "is_nq_universe": is_nq_universe
    }
