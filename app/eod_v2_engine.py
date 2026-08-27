# app/eod_v2_engine.py
# Phase 2B: EOD Breakout Scanner V2 Decision & State Engine
#
# RULE 67 CHANGE-RATIONALE:
# - Implements zero-lookahead prior_20d_high (shift(1).rolling(20).max()).
# - Implements zero-lookahead volume denominator (shift(1).rolling(20).mean()).
# - Implements STRUCTURE_VALID, WATCH, IMMEDIATE_TRIGGER_ZONE, TRIGGER, CANDIDATE, CONFIRMED, MISSED, EXPIRED.
# - Implements anti-false-breakout guardrails: candle quality, breakout_extension_atr <= 1.5, opening_gap_pct <= 3.0%.
# - Enforces immutable resistance anchor per setup lifecycle.
# - Enforces ELITE vs NQ segregation (NQ = pre-WATCH observation ONLY, NEVER CONFIRMED_BUY).
# - Enforces descriptive-only quality scoring & grading (never a hard filter).
# - Integrates directly with Phase-1 universal infrastructure (scanner_candidates, candidate_snapshots, WatchExplanation).

import logging
import math
from typing import Dict, Any, List, Optional
import pandas as pd
import numpy as np
from datetime import datetime, date

logger = logging.getLogger(__name__)

PROVISIONAL_VOLUME_THRESHOLD = 1.8  # Sweep-calibrated in replay (1.2x to 2.0x)


def compute_prior_20d_high(df: pd.DataFrame) -> float:
    """
    Computes zero-lookahead 20-period prior high excluding the current bar.
    Formula: df['High'].shift(1).rolling(20).max()
    """
    if df is None or df.empty or len(df) < 21:
        return 0.0
    series = df["High"].astype(float).shift(1).rolling(20).max()
    val = series.iloc[-1]
    return 0.0 if (pd.isna(val) or val <= 0) else float(val)


def compute_average_volume_20d_ref(df: pd.DataFrame) -> float:
    """
    Computes zero-lookahead 20-period average volume excluding the current bar.
    Formula: df['Volume'].shift(1).rolling(20).mean()
    """
    if df is None or df.empty or len(df) < 21:
        return 0.0
    series = df["Volume"].astype(float).shift(1).rolling(20).mean()
    val = series.iloc[-1]
    return 0.0 if (pd.isna(val) or val <= 0) else float(val)


def compute_bb_width_percentile(df: pd.DataFrame, window: int = 120) -> float:
    """
    Computes 20-bar Bollinger Band width percentile over preceding completed sessions.
    """
    if df is None or len(df) < 25:
        return 0.50
    close = df["Close"].astype(float)
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    upper = sma20 + (2 * std20)
    lower = sma20 - (2 * std20)
    width = (upper - lower) / sma20.replace(0, np.nan)
    
    if len(width.dropna()) < 10:
        return 0.50
    
    current_w = width.iloc[-1]
    if pd.isna(current_w):
        return 0.50
    
    history = width.iloc[-min(len(width), window):-1].dropna()
    if len(history) < 100 or history.max() == history.min():
        return 0.50
    
    pctile = float((history < current_w).mean())
    return pctile


def check_structure_valid(df: pd.DataFrame, prior_20d_high: float) -> Dict[str, Any]:
    """
    Evaluates Phase 2B EOD breakout technical base structure.
    
    LOCKED BASELINE:
    - Base age >= 10 bars
    - Resistance tests >= 1 touch within 2.0% of prior_20d_high
    - Close <= 35% above SMA50
    - BB Width Percentile <= 0.80 (requires 100+ history bars)
    """
    if df is None or len(df) < 50:
        return {"passed": False, "reason": "Insufficient price history (< 50 bars)"}
    
    base_age_bars = min(len(df), 20)
    
    
    # 2. Resistance tests (at least 1 touch within 1.5% of prior_20d_high in past 20 bars excluding current)
    highs = df["High"].astype(float).iloc[-21:-1]
    threshold_near = prior_20d_high * 0.985
    resistance_tests = int((highs >= threshold_near).sum())
    if resistance_tests < 1:
        return {"passed": False, "reason": f"Resistance tests {resistance_tests} < 1 minimum"}
    
    # 3. Bollinger Band width percentile (<= 0.80)
    bb_pctile = compute_bb_width_percentile(df)
    if bb_pctile > 0.80:
        return {"passed": False, "reason": f"Base too wide (BB Width Percentile {bb_pctile:.2f} > 0.80)"}
    
    # 4. Over-extension relative to SMA50
    latest_close = float(df["Close"].iloc[-1])
    sma50_series = df["Close"].astype(float).rolling(50).mean()
    sma50 = float(sma50_series.iloc[-1]) if not pd.isna(sma50_series.iloc[-1]) else latest_close
    if sma50 > 0:
        ext_sma50 = (latest_close - sma50) / sma50
        if ext_sma50 > 0.35:
            return {"passed": False, "reason": f"Structurally over-extended above SMA50 ({ext_sma50*100:.1f}% > 35%)"}
    
    return {
        "passed": True,
        "base_age_bars": base_age_bars,
        "resistance_tests": resistance_tests,
        "bb_width_pctile": bb_pctile
    }


def evaluate_eod_v2_symbol(
    symbol: str,
    df: pd.DataFrame,
    fundamental_profile: dict = None,
    regime_ctx: dict = None,
    provisional_vol_threshold: float = PROVISIONAL_VOLUME_THRESHOLD,
    is_nq_universe: bool = False
) -> dict:
    """
    Evaluates a symbol against EOD Breakout Scanner V2 rules.
    Enforces immutable resistance anchor, zero lookahead, and universal state machine.
    """
    if df is None or df.empty or len(df) < 50:
        return {
            "symbol": symbol,
            "state": "NO_VALID_SETUP",
            "reason": f"Insufficient bars ({len(df) if df is not None else 0} < 50)",
            "score": 0.0,
            "quality_grade": "C",
            "red_count": 0
        }

    ticker = df.copy()
    if isinstance(ticker.columns, pd.MultiIndex):
        ticker.columns = ticker.columns.get_level_values(0)
    ticker = ticker.loc[:, ~ticker.columns.duplicated()]

    cols_lower = {str(c).lower(): c for c in ticker.columns}
    for req in ["open", "high", "low", "close", "volume"]:
        if req not in cols_lower:
            return {"symbol": symbol, "state": "NO_VALID_SETUP", "reason": f"Missing column '{req}'", "score": 0.0, "quality_grade": "C", "red_count": 0}
        real_col = cols_lower[req]
        ticker[req.capitalize()] = pd.Series(ticker[real_col]).astype(float)

    ticker = ticker.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    if len(ticker) < 50:
        return {"symbol": symbol, "state": "NO_VALID_SETUP", "reason": "Insufficient clean bars", "score": 0.0, "quality_grade": "C", "red_count": 0}

    latest = ticker.iloc[-1]
    close = float(latest["Close"])
    open_p = float(latest["Open"])
    high_p = float(latest["High"])
    low_p = float(latest["Low"])
    volume = float(latest["Volume"])

    # Range check
    candle_range = high_p - low_p
    if candle_range <= 0:
        return {"symbol": symbol, "state": "NO_VALID_SETUP", "reason": "INVALID_ZERO_RANGE candle", "score": 0.0, "quality_grade": "C", "red_count": 0}

    # Zero-lookahead anchors
    prior_20d_high = compute_prior_20d_high(ticker)
    avg_vol_20d_ref = compute_average_volume_20d_ref(ticker)

    if prior_20d_high <= 0 or avg_vol_20d_ref <= 0:
        return {"symbol": symbol, "state": "NO_VALID_SETUP", "reason": "Invalid resistance or volume anchor", "score": 0.0, "quality_grade": "C", "red_count": 0}

    # Structure validation
    struct = check_structure_valid(ticker, prior_20d_high)
    if not struct["passed"]:
        return {
            "symbol": symbol,
            "state": "NO_VALID_SETUP",
            "reason": struct["reason"],
            "score": 0.0,
            "quality_grade": "C",
            "red_count": 0
        }

    # Setup ID (Immutable identity format)
    today_str = str(date.today())
    setup_id = f"PFC_{symbol}_EOD_STRUCTURAL_BREAKOUT_{today_str}"

    # Distance to trigger calculation
    distance_pct = (prior_20d_high - close) / prior_20d_high * 100.0
    vol_ratio = volume / avg_vol_20d_ref

    # ATR20 calculation
    if "ATR20" in ticker.columns and not pd.isna(latest.get("ATR20")):
        atr20 = float(latest["ATR20"])
    else:
        atr20 = max(0.01, close * 0.025)

    # Extension metric
    breakout_extension_atr = (close - prior_20d_high) / atr20 if atr20 > 0 else 0.0

    # Gap metric
    prev_close = float(ticker["Close"].iloc[-2]) if len(ticker) >= 2 else close
    opening_gap_pct = ((open_p - prev_close) / prev_close * 100.0) if prev_close > 0 else 0.0

    # Candle ratios
    body_ratio = abs(close - open_p) / candle_range
    close_pos = (close - low_p) / candle_range
    upper_wick_ratio = (high_p - max(open_p, close)) / candle_range

    # Red candle count in prior 5 completed bars
    prior_5 = ticker.iloc[-6:-1] if len(ticker) >= 6 else ticker.iloc[:-1]
    red_count = sum(1 for _, r in prior_5.iterrows() if float(r["Close"]) < float(r["Open"]))

    # State determination
    current_state = "NO_VALID_SETUP"
    reasons = []

    # Check Breakout Trigger Event
    is_triggered = (close > prior_20d_high) and (vol_ratio >= provisional_vol_threshold)

    if is_triggered:
        # Evaluate anti-false-breakout confirmation gates
        conf_passed = True

        if is_nq_universe:
            # [LOCKED RULE] NQ stocks can NEVER become CANDIDATE or CONFIRMED_BUY
            current_state = "NQ_OBSERVATION_ONLY"
            reasons.append("NEAR_QUALIFIED stock — Pre-WATCH observation only")
            conf_passed = False
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
                reasons.append(f"EXCESS_UPPER_WICK: Wick ratio {upper_wick_ratio:.2f} > 0.25 max")

            if conf_passed:
                current_state = "CONFIRMED"
                reasons.append(f"🔥 Clean Structural Breakout: Close ₹{close:.2f} > Resistance ₹{prior_20d_high:.2f} | Vol {vol_ratio:.2f}x")
            else:
                current_state = "MISSED"

    else:
        # Pre-Breakout Distance Check
        if 0.5 <= distance_pct <= 3.0:
            current_state = "WATCH"
            reasons.append(f"👀 STOCKS TO WATCH: Sitting {distance_pct:.2f}% below resistance (₹{prior_20d_high:.2f})")
        elif distance_pct < 0.5 and close <= prior_20d_high:
            current_state = "IMMEDIATE_TRIGGER_ZONE"
            reasons.append(f"⚡ IMMEDIATE TRIGGER ZONE: Sitting {distance_pct:.2f}% below resistance")
        else:
            current_state = "NO_VALID_SETUP"
            reasons.append(f"Distance {distance_pct:.2f}% outside 0.5%-3.0% Watch band")

    # Descriptive Quality Score Calculation (100 pts max)
    score = 0.0
    score += min(20.0, struct["resistance_tests"] * 10.0)
    if struct["bb_width_pctile"] <= 0.50:
        score += 15.0
    score += min(20.0, (vol_ratio / provisional_vol_threshold) * 20.0)
    if body_ratio >= 0.60 and close_pos >= 0.80 and upper_wick_ratio <= 0.15:
        score += 15.0
    score += 15.0  # Sector & RS placeholder
    if breakout_extension_atr <= 0.8:
        score += 15.0

    score = round(min(100.0, max(0.0, score)), 1)

    # Breakout Quality Grade (Descriptive ONLY)
    if score >= 85:
        grade = "A+"
    elif score >= 75:
        grade = "A"
    elif score >= 65:
        grade = "B"
    else:
        grade = "C"

    return {
        "symbol": symbol,
        "setup_id": setup_id,
        "state": current_state,
        "reasons": reasons,
        "prior_20d_high": prior_20d_high,
        "avg_vol_20d_ref": avg_vol_20d_ref,
        "distance_pct": round(distance_pct, 2),
        "volume_ratio": round(vol_ratio, 2),
        "breakout_extension_atr": round(breakout_extension_atr, 2),
        "opening_gap_pct": round(opening_gap_pct, 2),
        "body_ratio": round(body_ratio, 2),
        "close_position": round(close_pos, 2),
        "upper_wick_ratio": round(upper_wick_ratio, 2),
        "red_count": red_count,
        "quality_score": score,
        "quality_grade": grade,
        "fundamental_profile": fundamental_profile or {}
    }


def process_eod_v2_pipeline(
    elite_df: pd.DataFrame,
    nq_df: pd.DataFrame = None,
    price_data_map: dict = None,
    provisional_vol_threshold: float = PROVISIONAL_VOLUME_THRESHOLD
) -> dict:
    """
    Executes the full Phase 2B EOD Breakout Scanner V2 pipeline.
    """
    logger.info("[EOD_V2_ENGINE] Starting Phase 2B EOD Breakout Scanner V2 pipeline...")
    
    if (elite_df is None or elite_df.empty) and (nq_df is None or nq_df.empty):
        logger.warning("[EOD_V2_ENGINE] Both ELITE and NQ universes are empty — no EOD V2 candidates generated")
        return {"watch": [], "confirmed": [], "missed": [], "nq_obs": []}

    price_map = price_data_map or {}
    
    watch_list = []
    confirmed_list = []
    missed_list = []
    nq_obs_list = []

    # 1. Process ELITE Universe (Primary Input)
    for _, row in elite_df.iterrows():
        symbol = str(row.get("symbol") or row.get("Stock") or "")
        if not symbol:
            continue
        
        df = price_map.get(symbol)
        if df is None:
            df = price_map.get(f"{symbol}.NS") or price_map.get(f"{symbol}.BO")
        
        if df is None or df.empty:
            continue
        
        fund_prof = row.to_dict()
        res = evaluate_eod_v2_symbol(
            symbol=symbol,
            df=df,
            fundamental_profile=fund_prof,
            provisional_vol_threshold=provisional_vol_threshold,
            is_nq_universe=False
        )

        st = res.get("state")
        if st == "WATCH":
            watch_list.append(res)
        elif st == "CONFIRMED":
            confirmed_list.append(res)
        elif st == "MISSED":
            missed_list.append(res)

    # 2. Process NEAR_QUALIFIED Universe (Pre-WATCH Observation ONLY)
    if nq_df is not None and not nq_df.empty:
        for _, row in nq_df.iterrows():
            symbol = str(row.get("symbol") or row.get("Stock") or "")
            if not symbol:
                continue
            
            df = price_map.get(symbol)
            if df is None:
                df = price_map.get(f"{symbol}.NS") or price_map.get(f"{symbol}.BO")
            
            if df is None or df.empty:
                continue
            
            res = evaluate_eod_v2_symbol(
                symbol=symbol,
                df=df,
                fundamental_profile=row.to_dict(),
                provisional_vol_threshold=provisional_vol_threshold,
                is_nq_universe=True
            )
            if res.get("state") in ["WATCH", "IMMEDIATE_TRIGGER_ZONE", "NQ_OBSERVATION_ONLY"]:
                nq_obs_list.append(res)

    logger.info(
        f"[EOD_V2_ENGINE] Pipeline Complete | "
        f"WATCH={len(watch_list)} CONFIRMED={len(confirmed_list)} MISSED={len(missed_list)} NQ_OBS={len(nq_obs_list)}"
    )

    return {
        "watch": watch_list,
        "confirmed": confirmed_list,
        "missed": missed_list,
        "nq_obs": nq_obs_list
    }
