# app/accumulation_engine.py
# Phase 2F: Accumulation Breakout Scanner V2 Engine
#
# RULE 67 CHANGE-RATIONALE:
# - Implements Accumulation V2 Engine with 7-Stage Maturity Pipeline (Stages 1–7).
# - Enforces explicit separation between STRUCTURAL_SUPPORT_BREACH (Close < support - 0.5*ATR) and distribution_risk_score (sum of 6 symptoms).
# - Enforces Point-in-Time Flow & Delivery Timestamp Integrity (publication_date <= eval_date).
# - Evaluates Volume-Spread Analysis (VSA) Quiet Absorption and Multi-Stage VCP Contraction Series (T3 < T2 < T1).
# - Implements Accumulation Classes: STRONG_ACCUMULATION, ACCUMULATION, MIXED, DISTRIBUTION, UNKNOWN.
# - Enforces NQ universe isolation: Near-Qualified stocks marked NQ_OBSERVATION_ONLY and NEVER produce CONFIRMED_BUY alerts.

import logging
import math
from datetime import date
from typing import Dict, Any, List, Optional
import pandas as pd
import numpy as np

logger = logging.getLogger("AccumulationV2Engine")


def evaluate_accumulation_v2_symbol(
    symbol: str,
    df: pd.DataFrame,
    fund_data: Optional[Dict[str, Any]] = None,
    flow_data: Optional[Dict[str, Any]] = None,
    eval_date_str: str = "",
    is_nq_universe: bool = False,
    provisional_vol_threshold: float = 1.3
) -> Dict[str, Any]:
    """
    Evaluates a symbol against Phase 2F Accumulation V2 Breakout rules.
    """
    if df is None or len(df) < 50:
        return {
            "symbol": symbol,
            "state": "NO_VALID_SETUP",
            "reason": "Insufficient historical price data (< 50 bars)",
            "score": 0.0,
            "quality_grade": "C",
            "stage_progress": 0,
            "maturity_score": 0.0,
            "accumulation_class": "UNKNOWN",
            "data_confidence": "LOW"
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
            "stage_progress": 0,
            "maturity_score": 0.0,
            "accumulation_class": "UNKNOWN",
            "data_confidence": "LOW"
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
    close_position = (close - low_p) / c_range

    # 1. PRIMARY TREND VALIDATION
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
            "stage_progress": 1,
            "maturity_score": 0.0,
            "accumulation_class": "UNKNOWN",
            "data_confidence": "LOW"
        }

    # 2. POINT-IN-TIME FLOW & DELIVERY INTEGRITY
    data_confidence = "HIGH"
    flow_cleared = False
    if flow_data:
        pub_date = str(flow_data.get("publication_date", ""))
        if pub_date and eval_date_str and pub_date > eval_date_str:
            data_confidence = "LOW"
            flow_cleared = False
        else:
            flow_cleared = bool(flow_data.get("delivery_growth_pct", 0) > 10.0 or flow_data.get("mf_buying", False))
    else:
        data_confidence = "MEDIUM"

    # 3. BASE DURATION & SUPPORT LEVEL
    base_duration_days = min(60, len(df) - 20)
    support_level = float(df["Low"].iloc[-15:-1].min()) if len(df) >= 15 else low_p

    # 🚨 STRUCTURAL SUPPORT BREACH (IMMEDIATE HARD REJECT)
    if close < (support_level - 0.5 * atr20):
        return {
            "symbol": symbol,
            "state": "NO_VALID_SETUP",
            "reason": f"HARD_STRUCTURAL_REJECT: Close ₹{close:.2f} < support ₹{support_level:.2f} - 0.5*ATR",
            "score": 0.0,
            "quality_grade": "C",
            "stage_progress": 2,
            "maturity_score": 0.0,
            "accumulation_class": "DISTRIBUTION",
            "data_confidence": data_confidence
        }

    # 4. MULTI-STAGE VCP CONTRACTION MATH
    t1_depth = float((df["High"].iloc[-30:-20].max() - df["Low"].iloc[-30:-20].min()) / df["High"].iloc[-30:-20].max() * 100.0) if len(df) >= 30 else 15.0
    t2_depth = float((df["High"].iloc[-20:-10].max() - df["Low"].iloc[-20:-10].min()) / df["High"].iloc[-20:-10].max() * 100.0) if len(df) >= 20 else 10.0
    t3_depth = float((df["High"].iloc[-10:-1].max() - df["Low"].iloc[-10:-1].min()) / df["High"].iloc[-10:-1].max() * 100.0) if len(df) >= 10 else 5.0
    vcp_valid = (t3_depth < t2_depth < t1_depth) and (t3_depth <= 8.0)

    # 5. VSA QUIET ABSORPTION MATH
    avg_vol_20d = float(df["Volume"].iloc[-21:-1].mean()) if len(df) >= 21 and "Volume" in df.columns else float(df["Volume"].mean())
    vol_ratio = (volume / avg_vol_20d) if avg_vol_20d > 0 else 1.0
    is_quiet_absorption = (vol_ratio >= 1.2) and (c_range <= 1.2 * atr20) and (close_position >= 0.60)

    # 6. EXPLICIT 6-SYMPTOM DISTRIBUTION RISK SCORING
    down_mask = df["Close"].iloc[-10:-1] < df["Open"].iloc[-10:-1]
    down_vols = df["Volume"].iloc[-10:-1][down_mask]
    avg_down_vol = float(down_vols.mean()) if not down_vols.empty else avg_vol_20d
    down_vol_ratio = (avg_down_vol / avg_vol_20d) if avg_vol_20d > 0 else 1.0

    is_wide_red_spread = (close < open_p) and (body > 2.0 * atr20) and (close_position <= 0.35)
    
    distribution_risk_score = 0
    if down_vol_ratio >= 1.3: distribution_risk_score += 1
    if is_wide_red_spread: distribution_risk_score += 1
    if close_position <= 0.20: distribution_risk_score += 1
    if low_p < support_level - 0.2 * atr20: distribution_risk_score += 1
    if len(df) >= 3 and df["Volume"].iloc[-1] > avg_vol_20d and df["Close"].iloc[-1] < df["Open"].iloc[-1]: distribution_risk_score += 1
    if len(df) >= 3 and df["Close"].iloc[-1] < df["Close"].iloc[-2] < df["Close"].iloc[-3]: distribution_risk_score += 1

    if distribution_risk_score >= 3:
        return {
            "symbol": symbol,
            "state": "NO_VALID_SETUP",
            "reason": f"DISTRIBUTION_BREAKDOWN: Distribution Risk Score {distribution_risk_score} >= 3",
            "score": 0.0,
            "quality_grade": "C",
            "stage_progress": 4,
            "maturity_score": 0.0,
            "accumulation_class": "DISTRIBUTION",
            "data_confidence": data_confidence
        }

    # 7. RESISTANCE PROXIMITY & EXPANSION TRIGGER
    resistance_level = float(df["High"].iloc[-30:-1].max()) if len(df) >= 30 else float(df["High"].max())
    distance_to_resistance_pct = (resistance_level - close) / resistance_level * 100.0

    is_expansion = (close > resistance_level)
    is_triggered = is_expansion and (vol_ratio >= provisional_vol_threshold)
    setup_id = f"PFC_{symbol}_ACCUMULATION_BREAKOUT_{date.today()}"
    reasons = []

    # Calculate 7-Stage Maturity Progress
    stage_progress = 1
    if base_duration_days >= 15: stage_progress = 2
    if vcp_valid: stage_progress = 3
    if is_quiet_absorption: stage_progress = 4
    if distance_to_resistance_pct <= 3.0: stage_progress = 5
    if is_expansion: stage_progress = 6
    if is_triggered: stage_progress = 7

    maturity_score = round((stage_progress / 7.0) * 100.0, 1)

    # Accumulation Class Assignment (Point-in-Time Evidence)
    is_quiet_vol = (vol_ratio <= 0.95)
    if vcp_valid and is_quiet_vol and distribution_risk_score == 0:
        accumulation_class = "STRONG_ACCUMULATION"
    elif (vcp_valid or base_duration_days >= 15) and distribution_risk_score <= 1:
        accumulation_class = "ACCUMULATION"
    elif distribution_risk_score == 2:
        accumulation_class = "MIXED"
    else:
        accumulation_class = "UNKNOWN"

    # State Lifecycle Integration
    if is_triggered:
        conf_passed = (body / c_range >= 0.40) and (close_position >= 0.70)
        if is_nq_universe:
            current_state = "NQ_OBSERVATION_ONLY"
            reasons.append("NEAR_QUALIFIED stock — Pre-WATCH observation only")
        elif conf_passed:
            current_state = "CONFIRMED"
            reasons.append(f"CONFIRMED_ACCUMULATION_BREAKOUT: Breakout over ₹{resistance_level:.2f} with {vol_ratio:.2f}x Vol")
        else:
            current_state = "MISSED"
    else:
        if 0.5 <= distance_to_resistance_pct <= 3.0:
            current_state = "WATCH"
            reasons.append(f"👀 ACCUMULATION WATCH (Stage {stage_progress}/7): Maturity {maturity_score:.0f}% | Class {accumulation_class} | Resistance ₹{resistance_level:.2f}")
        elif 0.0 <= distance_to_resistance_pct < 0.5:
            current_state = "IMMEDIATE_TRIGGER_ZONE"
            reasons.append(f"IMMEDIATE_TRIGGER_ZONE: Price ₹{close:.2f} within 0.5% of resistance ₹{resistance_level:.2f}")
        else:
            current_state = "NO_VALID_SETUP"
            reasons.append(f"Distance {distance_to_resistance_pct:.2f}% outside 0.0%-3.0% Watch window")

    # Quality Score Calculation
    score = 70.0
    if accumulation_class == "STRONG_ACCUMULATION": score += 15.0
    elif accumulation_class == "ACCUMULATION": score += 8.0

    if vcp_valid: score += 10.0
    if is_quiet_absorption: score += 5.0

    grade = "A+" if score >= 90.0 else ("A" if score >= 80.0 else ("B" if score >= 70.0 else "C"))

    return {
        "symbol": symbol,
        "setup_id": setup_id,
        "state": current_state,
        "stage_progress": stage_progress,
        "maturity_score": maturity_score,
        "accumulation_class": accumulation_class,
        "distribution_risk_score": distribution_risk_score,
        "data_confidence": data_confidence,
        "reason": "; ".join(reasons),
        "score": score,
        "quality_grade": grade,
        "breakout_level": resistance_level,
        "entry_price": close,
        "atr_20": atr20,
        "distance_pct": distance_to_resistance_pct,
        "vol_ratio": vol_ratio,
        "is_nq_universe": is_nq_universe
    }
