# =====================================================================================
# app/technical_scanner.py
# PRODUCTION-GRADE UNIFIED TECHNICAL PATTERN & ANTI-FAKE SCANNER — Daily 18:15 IST
#
# RULE 67 MANDATORY CHANGE-RATIONALE:
# - Institutional-grade technical scanner conforming to the "Permissive Pattern Discovery,
#   Aggressive Pattern-Specific Validation" architecture.
# - Recognizes 8 core primary structures:
#   * Tier A: BULL_FLAG, SHAKEOUT_RECLAIM, DOUBLE_BOTTOM, V_REVERSAL
#   * Tier B: CUP_HANDLE, ASCENDING_TRIANGLE, BULL_PENNANT, HIGHER_LOW_REVERSAL
# - Pattern-Specific Anti-Fake & Volume Signature validations:
#   * Bull Flag: Pole directional efficiency (>=0.55), shallow flag retrace (<=45%),
#     flag volume contraction (avg_flag_vol / avg_pole_vol <= 0.85).
#   * Shakeout: Selling exhaustion, bullish absorption, volume absorption vs selloff.
#   * Double Bottom: Meaningful neckline separation (>=3.5% height), twin trough diff (<=2.5%).
#   * Cup & Handle: Rounded base (5-30% depth), handle in upper 35% of cup with drying volume.
#   * Ascending Triangle: Multi-touch flat resistance + ascending swing lows compression.
#   * V-Reversal: Sharp drop >=5% + steep multi-bar recovery >=60% on heavy volume (RVOL >= 1.30).
# - Universal Common Hard Gates:
#   * Volume: RVOL >= 1.20x strictly non-negotiable.
#   * Price Action: CLV >= 0.65, Upper Wick <= 30%.
#   * Liquidity: Volume >= 25k, Turnover >= ₹50 Lakhs.
#   * Risk: Room to Resistance >= 1.5R.
# - 100-Point Additive Scoring Model (<70 Reject, 70-79 Strong, 80-89 Very Strong, 90-100 Elite).
# - Tier C Confluence Boosters (Hammer, EMA/SMA reclaims, RSI divergence, OBV accumulation).
# =====================================================================================

import logging
import math
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from database import (
    complete_scanner_execution_run,
    get_elite_watchlist,
    init_db,
    save_alert_if_new,
    start_scanner_execution_run,
    upsert_scanner_health,
)
from lock_utils import ProcessLock
from price_cache import fetch_watchlist_data
from technical_indicators import apply_indicators
from telemetry_manager import telemetry
from watchlist_cache import get_watchlist

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_scan_lock = ProcessLock("technical_scanner_lock")
_global_lock = ProcessLock("global_scanner_lock")

# =====================================================================================
# CONFIGURABLE STRATEGY PARAMETERS & THRESHOLDS
# =====================================================================================

# Universal Hard Gates
MIN_RVOL_HARD_GATE = 1.20           # Minimum RVOL for trigger candle (RVOL < 1.20 -> REJECT)
MIN_CLV_HARD_GATE = 0.65            # Minimum Close Location Value ((C - L) / (H - L) >= 0.65)
MAX_UPPER_WICK_PCT = 0.30           # Maximum upper wick ratio ((H - max(O, C)) / (H - L) <= 0.30)
MIN_AVG_TURNOVER_INR = 50_00_000    # Minimum 20-day average turnover: ₹50 Lakhs
MIN_AVG_VOLUME = 25_000             # Minimum 20-day average volume: 25k shares

# Risk & Target Parameters
MAX_SL_PCT = 0.06                   # Maximum structural SL distance cap (6.0%)
MIN_SL_PCT = 0.012                  # Minimum risk buffer (1.2%)
MIN_ROOM_TO_RESISTANCE_R = 1.5      # Minimum R-multiple room to major overhead resistance

# Pattern-Specific Thresholds
BULL_FLAG_MIN_POLE_GAIN = 5.0       # Minimum pole gain % (or 2.0x ATR)
BULL_FLAG_MIN_POLE_EFFICIENCY = 0.55# Net move / Gross move ratio for directional efficiency
BULL_FLAG_MAX_RETRACE = 0.45        # Maximum retracement ratio of pole (45%)
BULL_FLAG_MAX_VOL_RATIO = 0.85      # Avg Flag Volume / Avg Pole Volume <= 0.85

SHAKEOUT_MIN_DECLINE_PCT = 4.0      # Minimum prior drop % for shakeout setup
DOUBLE_BOTTOM_MAX_DIFF_PCT = 2.5    # Max % difference between Trough 1 and Trough 2
DOUBLE_BOTTOM_MIN_HEIGHT_PCT = 3.5  # Minimum neckline height % above troughs

CUP_HANDLE_MIN_DEPTH_PCT = 5.0      # Minimum cup depth %
CUP_HANDLE_MAX_DEPTH_PCT = 30.0     # Maximum cup depth %
CUP_HANDLE_MAX_HANDLE_RETRACE = 0.35# Handle depth / Cup depth <= 35%


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        if val is None or (isinstance(val, float) and (math.isnan(val) or math.isinf(val))):
            return default
        return float(val)
    except Exception:
        return default


# =====================================================================================
# 1. PERMISSIVE PATTERN DISCOVERY SUB-DETECTORS (8 CORE STRUCTURES)
# =====================================================================================

def _detect_bull_flag(df: pd.DataFrame, atr14: float) -> Optional[Dict[str, Any]]:
    """
    Tier A Pattern: Bull Flag + Pole
    - Pole: Directional impulse over 3 to 10 bars (gain >= 5% or >= 2x ATR, efficiency >= 0.55).
    - Flag: Controlled consolidation over 3 to 12 bars, retrace <= 45%, volume ratio <= 0.85.
    """
    n = len(df)
    if n < 20:
        return None

    highs = df["High"].values
    lows = df["Low"].values
    closes = df["Close"].values
    opens = df["Open"].values
    volumes = df["Volume"].values

    today_idx = n - 1
    c_today = _safe_float(closes[today_idx])
    o_today = _safe_float(opens[today_idx])

    if c_today <= o_today:
        return None

    best_setup = None

    for flag_len in range(3, 13):
        pole_end_idx = today_idx - flag_len
        if pole_end_idx < 4:
            continue

        flag_highs = highs[pole_end_idx: today_idx]
        flag_lows = lows[pole_end_idx: today_idx]
        flag_vols = volumes[pole_end_idx: today_idx]

        flag_resistance = float(np.max(flag_highs))
        flag_support = float(np.min(flag_lows))

        if c_today < flag_resistance * 1.001:
            continue

        for pole_len in range(3, 11):
            pole_start_idx = pole_end_idx - pole_len
            if pole_start_idx < 0:
                continue

            pole_sub_lows = lows[pole_start_idx: pole_end_idx]
            pole_sub_highs = highs[pole_start_idx: pole_end_idx + 1]
            pole_low = float(np.min(pole_sub_lows))
            pole_high = float(np.max(pole_sub_highs))
            pole_move = pole_high - pole_low
            pole_pct = (pole_move / max(pole_low, 1.0)) * 100.0

            if pole_pct < BULL_FLAG_MIN_POLE_GAIN and pole_move < (2.0 * atr14):
                continue

            # Pole directional efficiency
            pole_closes = closes[pole_start_idx: pole_end_idx + 1]
            gross_movement = np.sum(np.abs(np.diff(pole_closes)))
            net_movement = abs(pole_closes[-1] - pole_closes[0])
            pole_efficiency = (net_movement / max(gross_movement, 0.01))

            if pole_efficiency < BULL_FLAG_MIN_POLE_EFFICIENCY:
                continue

            # Flag retracement check
            retrace_depth = pole_high - flag_support
            retrace_ratio = retrace_depth / max(pole_move, 0.01)
            if retrace_ratio > BULL_FLAG_MAX_RETRACE or retrace_ratio < 0:
                continue

            # Volume signature
            pole_vols = volumes[pole_start_idx: pole_end_idx + 1]
            avg_pole_vol = float(np.mean(pole_vols)) if len(pole_vols) > 0 else 1.0
            avg_flag_vol = float(np.mean(flag_vols)) if len(flag_vols) > 0 else 1.0
            vol_ratio_flag_to_pole = avg_flag_vol / max(avg_pole_vol, 1.0)

            if vol_ratio_flag_to_pole > BULL_FLAG_MAX_VOL_RATIO:
                continue

            # Resistance above flag
            overhead_highs = highs[max(0, pole_start_idx - 30): pole_start_idx]
            higher_levels = [h for h in overhead_highs if h > flag_resistance * 1.01]
            target_res = float(min(higher_levels)) if higher_levels else (c_today * 1.15)

            setup = {
                "pattern": "BULL_FLAG",
                "tier": "TIER_A",
                "pole_gain_pct": round(pole_pct, 2),
                "pole_bars": pole_len,
                "pole_efficiency": round(pole_efficiency, 2),
                "flag_bars": flag_len,
                "flag_resistance": round(flag_resistance, 2),
                "flag_support": round(flag_support, 2),
                "retracement_pct": round(retrace_ratio * 100.0, 1),
                "vol_ratio_flag_to_pole": round(vol_ratio_flag_to_pole, 2),
                "invalidation_level": round(flag_support * 0.995, 2),
                "target_resistance": round(target_res, 2),
                "pattern_quality_score": 25 if (pole_pct >= 10.0 and vol_ratio_flag_to_pole <= 0.70) else 22,
                "description": f"Bull Flag (Pole +{pole_pct:.1f}%, Flag {flag_len}b, Retrace {retrace_ratio*100:.0f}%, VolRatio {vol_ratio_flag_to_pole:.2f}x)",
            }
            if best_setup is None or setup["pole_gain_pct"] > best_setup["pole_gain_pct"]:
                best_setup = setup

    return best_setup


def _detect_shakeout_reclaim(df: pd.DataFrame, atr14: float) -> Optional[Dict[str, Any]]:
    """
    Tier A Pattern: Shakeout Reclaim (Bottom Absorption)
    - Selloff: Decline >= 4.0% or >= 1.2x ATR over 3 to 15 sessions into support.
    - Absorption: Green candle at support engulfing preceding red candle.
    - Target Resistance: Drop origin high (resistance of the selloff origin).
    """
    n = len(df)
    if n < 20:
        return None

    highs = df["High"].values
    lows = df["Low"].values
    closes = df["Close"].values
    opens = df["Open"].values
    volumes = df["Volume"].values

    today_idx = n - 1
    prev_idx = today_idx - 1

    c_today = _safe_float(closes[today_idx])
    o_today = _safe_float(opens[today_idx])
    c_prev = _safe_float(closes[prev_idx])
    o_prev = _safe_float(opens[prev_idx])
    h_prev = _safe_float(highs[prev_idx])
    l_prev = _safe_float(lows[prev_idx])
    v_today = _safe_float(volumes[today_idx])

    if c_today <= o_today or c_today <= c_prev:
        return None

    prev_body = abs(o_prev - c_prev)
    today_body = c_today - o_today

    is_body_engulfing = (c_today >= o_prev) and (today_body >= prev_body * 0.85)
    is_full_reclaim = (c_today >= h_prev)

    if not (is_body_engulfing or is_full_reclaim):
        return None

    engulf_type = "LEVEL_B_FULL_RECLAIM" if is_full_reclaim else "LEVEL_A_BODY_ENGULFING"

    lookback = min(16, n - 2)
    recent_highs = highs[today_idx - lookback: today_idx]
    recent_lows = lows[today_idx - lookback: today_idx]
    recent_vols = volumes[today_idx - lookback: today_idx]

    drop_high = float(np.max(recent_highs))
    trough_low = float(np.min(recent_lows))
    drop_points = drop_high - trough_low
    drop_pct = (drop_points / max(drop_high, 1.0)) * 100.0

    if drop_pct < SHAKEOUT_MIN_DECLINE_PCT and drop_points < (1.2 * atr14):
        return None

    avg_selloff_vol = float(np.mean(recent_vols)) if len(recent_vols) > 0 else 1.0
    vol_vs_selloff = v_today / max(avg_selloff_vol, 1.0)
    if vol_vs_selloff < 0.85:
        return None

    base_support = min(lows[today_idx], l_prev, trough_low)

    return {
        "pattern": "SHAKEOUT_RECLAIM",
        "tier": "TIER_A",
        "engulfing_type": engulf_type,
        "drop_origin_high": round(drop_high, 2),
        "trough_low": round(trough_low, 2),
        "selloff_depth_pct": round(drop_pct, 2),
        "vol_vs_selloff": round(vol_vs_selloff, 2),
        "invalidation_level": round(base_support * 0.995, 2),
        "target_resistance": round(drop_high, 2),
        "pattern_quality_score": 25 if is_full_reclaim else 21,
        "description": f"Shakeout Reclaim ({engulf_type.replace('_', ' ')}, Prior Drop -{drop_pct:.1f}%, Vol vs Selloff {vol_vs_selloff:.2f}x)",
    }


def _detect_double_bottom(df: pd.DataFrame, atr14: float) -> Optional[Dict[str, Any]]:
    """
    Tier A Pattern: Double Bottom Breakout
    - Two troughs within 2.5% price difference separated by 5 to 30 bars.
    - Neckline peak between troughs with height >= 3.5%.
    - Today closes cleanly above neckline resistance.
    """
    n = len(df)
    if n < 25:
        return None

    highs = df["High"].values
    lows = df["Low"].values
    closes = df["Close"].values
    today_idx = n - 1
    c_today = _safe_float(closes[today_idx])

    window_start = max(0, today_idx - 40)
    sub_lows = lows[window_start: today_idx]

    if len(sub_lows) < 15:
        return None

    local_min_indices = []
    for i in range(1, len(sub_lows) - 1):
        if sub_lows[i] <= sub_lows[i - 1] and sub_lows[i] <= sub_lows[i + 1]:
            local_min_indices.append(window_start + i)

    if len(local_min_indices) < 2:
        return None

    for t1 in local_min_indices[:-1]:
        for t2 in local_min_indices:
            if t2 <= t1 + 4 or t2 >= today_idx - 1:
                continue

            l1_val = float(lows[t1])
            l2_val = float(lows[t2])

            diff_pct = abs(l1_val - l2_val) / min(l1_val, l2_val) * 100.0
            if diff_pct > DOUBLE_BOTTOM_MAX_DIFF_PCT:
                continue

            neckline_val = float(np.max(highs[t1: t2 + 1]))
            pattern_height_pct = (neckline_val - min(l1_val, l2_val)) / min(l1_val, l2_val) * 100.0

            if pattern_height_pct < DOUBLE_BOTTOM_MIN_HEIGHT_PCT:
                continue

            if c_today >= neckline_val * 1.002:
                sl_level = round(max(l2_val * 0.995, neckline_val * 0.96), 2)
                # Look for major overhead swing high before the double bottom pattern
                pre_pattern_highs = highs[max(0, t1 - 40): t1]
                higher_res = [h for h in pre_pattern_highs if h > c_today * 1.02]
                major_target_res = float(max(higher_res)) if higher_res else (c_today * 1.20)
                return {
                    "pattern": "DOUBLE_BOTTOM",
                    "tier": "TIER_A",
                    "trough_1": round(l1_val, 2),
                    "trough_2": round(l2_val, 2),
                    "neckline": round(neckline_val, 2),
                    "trough_diff_pct": round(diff_pct, 2),
                    "pattern_height_pct": round(pattern_height_pct, 2),
                    "invalidation_level": sl_level,
                    "target_resistance": round(major_target_res, 2),
                    "pattern_quality_score": 24,
                    "description": f"Double Bottom Breakout (Neckline ₹{neckline_val:.2f}, Height {pattern_height_pct:.1f}%)",
                }
    return None


def _detect_v_reversal(df: pd.DataFrame, atr14: float) -> Optional[Dict[str, Any]]:
    """
    Tier A Pattern: V-Reversal Recovery
    - Sharp decline >= 5% followed by multiple consecutive recovery bars and higher low/high structure.
    """
    n = len(df)
    if n < 15:
        return None

    highs = df["High"].values
    lows = df["Low"].values
    closes = df["Close"].values
    opens = df["Open"].values

    today_idx = n - 1
    c_today = _safe_float(closes[today_idx])
    o_today = _safe_float(opens[today_idx])

    if c_today <= o_today:
        return None

    lookback = min(12, n - 2)
    recent_highs = highs[today_idx - lookback: today_idx - 2]
    recent_lows = lows[today_idx - lookback: today_idx]

    if len(recent_highs) < 2 or len(recent_lows) < 2:
        return None

    drop_high = float(np.max(recent_highs))
    trough_low = float(np.min(recent_lows))
    drop_points = drop_high - trough_low
    drop_pct = (drop_points / max(drop_high, 1.0)) * 100.0

    if drop_pct < 5.0 and drop_points < (1.5 * atr14):
        return None

    recovery_pct = (c_today - trough_low) / max(drop_points, 0.01) * 100.0
    if recovery_pct < 35.0:
        return None

    # Pre-drop overhead high or measured expansion
    lookback_pre = min(50, n - 2)
    pre_drop_highs = highs[max(0, today_idx - lookback_pre): today_idx - lookback]
    higher_res = [h for h in pre_drop_highs if h > c_today * 1.03]
    major_target_res = float(max(higher_res)) if higher_res else (drop_high + (drop_points * 0.382))

    sl_level = round(trough_low * 0.995, 2)
    return {
        "pattern": "V_REVERSAL",
        "tier": "TIER_A",
        "drop_origin_high": round(drop_high, 2),
        "trough_low": round(trough_low, 2),
        "selloff_depth_pct": round(drop_pct, 1),
        "invalidation_level": sl_level,
        "target_resistance": round(major_target_res, 2),
        "pattern_quality_score": 22,
        "description": f"V-Reversal Recovery (-{drop_pct:.1f}% Drop, Recovered {recovery_pct:.0f}%)",
    }


def _detect_cup_and_handle(df: pd.DataFrame, atr14: float) -> Optional[Dict[str, Any]]:
    """
    Tier B Pattern: Cup & Handle Breakout
    - Rounded U-base over 20-50 bars (depth 5% - 30%).
    - Handle pullback in upper 35% portion of cup (depth <= 35% of cup).
    - Breakout above rim.
    """
    n = len(df)
    if n < 30:
        return None

    highs = df["High"].values
    lows = df["Low"].values
    closes = df["Close"].values
    today_idx = n - 1
    c_today = _safe_float(closes[today_idx])

    for handle_len in range(3, 10):
        rim_idx = today_idx - handle_len
        if rim_idx < 15:
            continue

        handle_low = float(np.min(lows[rim_idx: today_idx]))
        rim_high = float(highs[rim_idx])

        for cup_len in range(15, min(45, rim_idx)):
            left_rim_idx = rim_idx - cup_len
            left_rim_high = float(np.max(highs[left_rim_idx: left_rim_idx + 4]))
            cup_bottom = float(np.min(lows[left_rim_idx: rim_idx]))

            rim_diff = abs(left_rim_high - rim_high) / min(left_rim_high, rim_high) * 100.0
            if rim_diff > 3.5:
                continue

            cup_depth = rim_high - cup_bottom
            cup_depth_pct = (cup_depth / rim_high) * 100.0
            if cup_depth_pct < CUP_HANDLE_MIN_DEPTH_PCT or cup_depth_pct > CUP_HANDLE_MAX_DEPTH_PCT:
                continue

            handle_depth = rim_high - handle_low
            if handle_depth > (cup_depth * CUP_HANDLE_MAX_HANDLE_RETRACE):
                continue

            if c_today >= rim_high * 1.002:
                sl_level = round(handle_low * 0.995, 2)
                measured_target = rim_high + cup_depth
                return {
                    "pattern": "CUP_HANDLE",
                    "tier": "TIER_B",
                    "rim_level": round(rim_high, 2),
                    "cup_depth_pct": round(cup_depth_pct, 1),
                    "handle_bars": handle_len,
                    "invalidation_level": sl_level,
                    "target_resistance": round(measured_target, 2),
                    "pattern_quality_score": 19,
                    "description": f"Cup & Handle Breakout (Rim ₹{rim_high:.2f}, Cup -{cup_depth_pct:.1f}%)",
                }
    return None


def _detect_ascending_triangle(df: pd.DataFrame, atr14: float) -> Optional[Dict[str, Any]]:
    """
    Tier B Pattern: Ascending Triangle Breakout
    - Flat horizontal resistance line (2+ peaks within 1.2%).
    - Ascending swing lows compressing upward (2+ higher lows).
    """
    n = len(df)
    if n < 20:
        return None

    highs = df["High"].values
    lows = df["Low"].values
    closes = df["Close"].values
    today_idx = n - 1
    c_today = _safe_float(closes[today_idx])

    lookback = min(30, n - 2)
    sub_highs = highs[today_idx - lookback: today_idx]
    sub_lows = lows[today_idx - lookback: today_idx]

    if len(sub_highs) < 12:
        return None

    peaks = []
    for i in range(1, len(sub_highs) - 1):
        if sub_highs[i] > sub_highs[i - 1] and sub_highs[i] > sub_highs[i + 1]:
            peaks.append(float(sub_highs[i]))

    if len(peaks) < 2:
        return None

    res_level = float(np.max(peaks))
    near_peaks = [p for p in peaks if (res_level - p) / res_level <= 0.015]
    if len(near_peaks) < 2:
        return None

    troughs = []
    for i in range(1, len(sub_lows) - 1):
        if sub_lows[i] < sub_lows[i - 1] and sub_lows[i] < sub_lows[i + 1]:
            troughs.append(float(sub_lows[i]))

    if len(troughs) < 2 or troughs[-1] <= troughs[0]:
        return None

    if c_today >= res_level * 1.002:
        last_low = troughs[-1]
        sl_level = round(last_low * 0.995, 2)
        measured_target = res_level + (res_level - troughs[0])
        return {
            "pattern": "ASCENDING_TRIANGLE",
            "tier": "TIER_B",
            "resistance_level": round(res_level, 2),
            "ascending_lows_count": len(troughs),
            "invalidation_level": sl_level,
            "target_resistance": round(measured_target, 2),
            "pattern_quality_score": 18,
            "description": f"Ascending Triangle Breakout (Res ₹{res_level:.2f}, Lows +{len(troughs)})",
        }
    return None


def _detect_bull_pennant(df: pd.DataFrame, atr14: float) -> Optional[Dict[str, Any]]:
    """
    Tier B Pattern: Bull Pennant
    - Symmetrical converging trendlines after strong pole.
    """
    n = len(df)
    if n < 15:
        return None

    highs = df["High"].values
    lows = df["Low"].values
    closes = df["Close"].values
    today_idx = n - 1
    c_today = _safe_float(closes[today_idx])

    for pennant_len in range(3, 8):
        pole_end = today_idx - pennant_len
        if pole_end < 4:
            continue

        pole_start = max(0, pole_end - 10)
        pole_low = float(np.min(lows[pole_start: pole_end]))
        pole_high = float(np.max(highs[pole_start: pole_end + 1]))
        pole_gain = (pole_high - pole_low) / max(pole_low, 1.0) * 100.0

        if pole_gain < 5.0:
            continue

        p_highs = highs[pole_end: today_idx]
        p_lows = lows[pole_end: today_idx]

        if len(p_highs) >= 3 and p_highs[-1] <= p_highs[0] and p_lows[-1] >= p_lows[0]:
            pennant_top = float(np.max(p_highs))
            if c_today >= pennant_top * 1.002:
                sl_level = round(float(np.min(p_lows)) * 0.995, 2)
                pole_move = pole_high - pole_low
                pre_highs = highs[max(0, pole_start - 30): pole_start]
                higher_res = [h for h in pre_highs if h > c_today * 1.02]
                target_res = float(max(higher_res)) if higher_res else (pennant_top + pole_move)
                return {
                    "pattern": "BULL_PENNANT",
                    "tier": "TIER_B",
                    "pole_gain_pct": round(pole_gain, 1),
                    "pennant_bars": pennant_len,
                    "invalidation_level": sl_level,
                    "target_resistance": round(target_res, 2),
                    "pattern_quality_score": 17,
                    "description": f"Bull Pennant Breakout (Pole +{pole_gain:.1f}%, Pennant {pennant_len}b)",
                }
    return None


def _detect_higher_low_reversal(df: pd.DataFrame, atr14: float) -> Optional[Dict[str, Any]]:
    """
    Tier B Pattern: Higher-Low Structure Break
    - Higher swing low followed by break above prior swing high.
    """
    n = len(df)
    if n < 20:
        return None

    highs = df["High"].values
    lows = df["Low"].values
    closes = df["Close"].values
    today_idx = n - 1
    c_today = _safe_float(closes[today_idx])

    sub_lows = lows[today_idx - 25: today_idx]
    sub_highs = highs[today_idx - 25: today_idx]

    if len(sub_lows) < 12:
        return None

    trough_indices = []
    for i in range(1, len(sub_lows) - 1):
        if sub_lows[i] < sub_lows[i - 1] and sub_lows[i] < sub_lows[i + 1]:
            trough_indices.append(i)

    if len(trough_indices) < 2:
        return None

    l1_idx = trough_indices[-2]
    l2_idx = trough_indices[-1]

    if sub_lows[l2_idx] <= sub_lows[l1_idx] * 1.005:
        return None

    h1_val = float(np.max(sub_highs[l1_idx: l2_idx + 1]))

    if c_today >= h1_val * 1.002:
        sl_level = round(float(sub_lows[l2_idx]) * 0.995, 2)
        overhead_high = float(np.max(sub_highs))
        target_res = overhead_high if overhead_high > c_today * 1.02 else (c_today * 1.15)
        return {
            "pattern": "HIGHER_LOW_REVERSAL",
            "tier": "TIER_B",
            "swing_high": round(h1_val, 2),
            "higher_low": round(float(sub_lows[l2_idx]), 2),
            "invalidation_level": sl_level,
            "target_resistance": round(target_res, 2),
            "pattern_quality_score": 16,
            "description": f"Higher-Low Structure Break (Swing High ₹{h1_val:.2f}, Low ₹{sub_lows[l2_idx]:.2f})",
        }
    return None


# =====================================================================================
# 2. TIER C CONFLUENCE BOOSTERS (SECONDARY BOOSTERS)
# =====================================================================================

def _detect_confluence_factors(df: pd.DataFrame) -> Tuple[List[str], int]:
    """
    Evaluates secondary confluence factors (Max +5 points total).
    """
    confluences = []
    bonus_pts = 0
    n = len(df)
    if n < 15:
        return confluences, bonus_pts

    c_today = _safe_float(df["Close"].iloc[-1])
    o_today = _safe_float(df["Open"].iloc[-1])
    h_today = _safe_float(df["High"].iloc[-1])
    l_today = _safe_float(df["Low"].iloc[-1])
    c_prev = _safe_float(df["Close"].iloc[-2])

    candle_range = h_today - l_today
    body = abs(c_today - o_today)

    if candle_range > 0:
        lower_wick = min(c_today, o_today) - l_today
        upper_wick = h_today - max(c_today, o_today)
        if lower_wick >= 1.8 * body and upper_wick <= 0.25 * candle_range:
            confluences.append("HAMMER_AT_SUPPORT")
            bonus_pts += 2

    if "EMA20" in df.columns:
        ema20 = float(df["EMA20"].iloc[-1])
        ema20_prev = float(df["EMA20"].iloc[-2])
        if c_prev <= ema20_prev and c_today > ema20:
            confluences.append("EMA20_RECLAIM")
            bonus_pts += 1
        elif c_today > ema20:
            confluences.append("ABOVE_EMA20")

    if "SMA50" in df.columns:
        sma50 = float(df["SMA50"].iloc[-1])
        if c_today > sma50:
            confluences.append("ABOVE_SMA50")
            bonus_pts += 1

    if "SMA200" in df.columns:
        sma200 = float(df["SMA200"].iloc[-1])
        if c_today > sma200:
            confluences.append("ABOVE_SMA200_UPTREND")
            bonus_pts += 1

    if "RSI_14" in df.columns and n >= 25:
        rsi_today = float(df["RSI_14"].iloc[-1])
        rsi_min_past = float(df["RSI_14"].iloc[-15:-1].min())
        price_min_past = float(df["Low"].iloc[-15:-1].min())
        if l_today <= price_min_past * 1.01 and rsi_today > rsi_min_past + 3.0:
            confluences.append("RSI_BULLISH_DIVERGENCE")
            bonus_pts += 2

    vol_sma20 = float(df["Volume_SMA20"].iloc[-1]) if "Volume_SMA20" in df.columns else np.mean(df["Volume"].iloc[-20:])
    if float(df["Volume"].iloc[-1]) >= 1.75 * max(vol_sma20, 1.0):
        confluences.append("INSTITUTIONAL_VOLUME_SURGE")
        bonus_pts += 1

    return confluences, min(5, bonus_pts)


# =====================================================================================
# 3. COMMON QUALITY, RISK, AND ANTI-FAKE VALIDATION ENGINE
# =====================================================================================

def detect_technical_setup(df: pd.DataFrame, symbol: str) -> Optional[Dict[str, Any]]:
    """
    Unified Technical Pattern & Anti-Fake Engine:
    
    1. Common Hard Filters:
       - Liquidity Filter: 20-day Volume >= 25k & Turnover >= ₹50L.
       - Hard Volume Gate: RVOL >= 1.20x (Strict Reject if Low Volume).
       - Close Strength Gate: CLV >= 0.65.
       - Upper Wick Filter: Upper Wick <= 30% of range.
    2. Permissive Pattern Discovery (8 Core Structures).
    3. Pattern-Specific Validation & Volume Signature Matching.
    4. Risk Engine & Room-to-Resistance Hard Gate (>= 1.5R).
    5. Tier C Confluence Boosters.
    6. Clean 100-Point Additive Scoring Engine.
    """
    if df is None or df.empty or len(df) < 30:
        return None

    required_cols = ["Open", "High", "Low", "Close", "Volume"]
    if not all(col in df.columns for col in required_cols):
        return None

    if "EMA20" not in df.columns or "ATR_14" not in df.columns or "Volume_SMA20" not in df.columns:
        df = apply_indicators(df, timeframe="1d")

    close_arr = df["Close"].values
    open_arr = df["Open"].values
    high_arr = df["High"].values
    low_arr = df["Low"].values
    vol_arr = df["Volume"].values

    n = len(df)
    today_idx = n - 1
    c_today = _safe_float(close_arr[today_idx])
    o_today = _safe_float(open_arr[today_idx])
    h_today = _safe_float(high_arr[today_idx])
    l_today = _safe_float(low_arr[today_idx])
    v_today = _safe_float(vol_arr[today_idx])

    if c_today <= o_today or c_today <= 0:
        return None

    candle_range = h_today - l_today
    if candle_range <= 0:
        return None

    # ── COMMON HARD FILTER 1: LIQUIDITY & TURNOVER ─────────────────────────────────
    vol_sma20 = float(df["Volume_SMA20"].iloc[-1]) if "Volume_SMA20" in df.columns else np.mean(vol_arr[-20:])
    avg_turnover = vol_sma20 * c_today
    if vol_sma20 < MIN_AVG_VOLUME and avg_turnover < MIN_AVG_TURNOVER_INR:
        return None  # REJECT: Illiquid stock

    # ── COMMON HARD FILTER 2: RVOL EXPANSION (>= 1.20x) ─────────────────────────────
    vol_ratio = v_today / max(vol_sma20, 1.0)
    if vol_ratio < MIN_RVOL_HARD_GATE:
        return None  # REJECT: Low volume breakout / false bounce trap

    # ── COMMON HARD FILTER 3: CLOSE STRENGTH (CLV >= 0.65) ──────────────────────────
    clv = (c_today - l_today) / candle_range
    if clv < MIN_CLV_HARD_GATE:
        return None  # REJECT: Weak close / faded near lows

    # ── COMMON HARD FILTER 4: UPPER WICK FILTER (<= 30%) ────────────────────────────
    upper_wick = h_today - max(c_today, o_today)
    upper_wick_pct = upper_wick / candle_range
    if upper_wick_pct > MAX_UPPER_WICK_PCT:
        return None  # REJECT: Large upper wick rejection trap

    atr14 = float(df["ATR_14"].iloc[-1]) if "ATR_14" in df.columns else (c_today * 0.02)
    if atr14 <= 0:
        atr14 = c_today * 0.02

    # ── PERMISSIVE PATTERN DISCOVERY (8 PRIMARY STRUCTURES) ─────────────────────────
    candidate_patterns = []

    # Tier A Patterns
    bf = _detect_bull_flag(df, atr14)
    if bf:
        candidate_patterns.append(bf)

    sr = _detect_shakeout_reclaim(df, atr14)
    if sr:
        candidate_patterns.append(sr)

    db = _detect_double_bottom(df, atr14)
    if db:
        candidate_patterns.append(db)

    vr = _detect_v_reversal(df, atr14)
    if vr:
        candidate_patterns.append(vr)

    # Tier B Patterns
    ch = _detect_cup_and_handle(df, atr14)
    if ch:
        candidate_patterns.append(ch)

    at = _detect_ascending_triangle(df, atr14)
    if at:
        candidate_patterns.append(at)

    bp = _detect_bull_pennant(df, atr14)
    if bp:
        candidate_patterns.append(bp)

    hl = _detect_higher_low_reversal(df, atr14)
    if hl:
        candidate_patterns.append(hl)

    if not candidate_patterns:
        return None

    # Primary pattern ranking hierarchy
    PATTERN_PRIORITY_RANK = {
        "BULL_FLAG": 1,
        "DOUBLE_BOTTOM": 2,
        "CUP_HANDLE": 3,
        "ASCENDING_TRIANGLE": 4,
        "BULL_PENNANT": 5,
        "SHAKEOUT_RECLAIM": 6,
        "V_REVERSAL": 7,
        "HIGHER_LOW_REVERSAL": 8,
    }

    candidate_patterns.sort(key=lambda p: (
        PATTERN_PRIORITY_RANK.get(p["pattern"], 99),
        -p.get("pattern_quality_score", 0)
    ))
    primary = candidate_patterns[0]
    secondary_pattern_names = [p["pattern"] for p in candidate_patterns[1:]]

    # ── RISK ENGINE & STOP LOSS CALCULATION ─────────────────────────────────────────
    raw_invalidation = primary.get("invalidation_level", l_today * 0.99)
    hard_sl_floor = c_today * (1.0 - MAX_SL_PCT)
    stop_loss = round(max(raw_invalidation, hard_sl_floor), 2)

    risk_points = max(c_today - stop_loss, c_today * MIN_SL_PCT)
    stop_loss = round(c_today - risk_points, 2)
    risk_pct = round((risk_points / c_today) * 100.0, 2)

    # Dynamic R:R Targets
    target_1 = round(c_today + (1.5 * risk_points), 2)
    target_2 = round(c_today + (3.0 * risk_points), 2)
    target_3 = round(c_today + (5.0 * risk_points), 2)
    rr_1 = round((target_1 - c_today) / max(risk_points, 0.01), 2)

    # ── ROOM TO OVERHEAD RESISTANCE HARD GATE (>= 1.5R) ─────────────────────────────
    # Use pattern-specific target resistance level
    target_res = primary.get("target_resistance", c_today * 1.15)
    room_to_resistance_points = target_res - c_today
    room_to_resistance_r = room_to_resistance_points / max(risk_points, 0.01)

    # If the pattern's overhead resistance is too close (< 1.5R), reject
    if room_to_resistance_r < MIN_ROOM_TO_RESISTANCE_R and target_res > c_today:
        return None  # REJECT: Insufficient room to overhead resistance

    # ── TIER C CONFLUENCE BOOSTERS ──────────────────────────────────────────────────
    confluences, confluence_pts = _detect_confluence_factors(df)

    # ── CLEAN 100-POINT ADDITIVE SCORING SYSTEM ────────────────────────────────────
    # 1. Pattern Quality (0 to 25 pts)
    score_pattern = float(primary.get("pattern_quality_score", 20))

    # 2. Volume Confirmation & Signature (0 to 25 pts)
    score_volume = 15.0  # Base for passing RVOL >= 1.20x
    if vol_ratio >= 2.0:
        score_volume += 10.0
    elif vol_ratio >= 1.5:
        score_volume += 6.0
    elif vol_ratio >= 1.3:
        score_volume += 3.0

    # 3. Price Action & Close Quality (0 to 20 pts)
    if clv >= 0.85 and upper_wick_pct <= 0.15:
        score_price_action = 20.0
    elif clv >= 0.70 and upper_wick_pct <= 0.25:
        score_price_action = 16.0
    else:
        score_price_action = 12.0

    # 4. Structure & Cleanliness (0 to 15 pts)
    score_structure = 12.0
    if len(secondary_pattern_names) > 0:
        score_structure += 3.0

    # 5. Risk / Room to Resistance (0 to 10 pts)
    if room_to_resistance_r >= 3.0:
        score_risk = 10.0
    elif room_to_resistance_r >= 2.0:
        score_risk = 8.0
    else:
        score_risk = 6.0

    # 6. Confluence (0 to 5 pts)
    score_confluence = float(confluence_pts)

    total_score = int(score_pattern + score_volume + score_price_action + score_structure + score_risk + score_confluence)
    total_score = min(100, max(0, total_score))

    # Minimum threshold to qualify for an alert
    if total_score < 70:
        return None  # REJECT: Sub-threshold quality

    # Classification Hierarchy
    if total_score >= 90:
        classification = "🔥🔥 ELITE"
    elif total_score >= 80:
        classification = "🔥 VERY STRONG"
    else:
        classification = "⚡ STRONG"

    return {
        "symbol": symbol,
        "cmp": c_today,
        "entry_price": c_today,
        "primary_pattern": primary["pattern"],
        "tier": primary["tier"],
        "description": primary["description"],
        "secondary_patterns": secondary_pattern_names,
        "confluences": confluences,
        "pattern_details": primary,
        "stop_loss": stop_loss,
        "target_1": target_1,
        "target_2": target_2,
        "target_3": target_3,
        "rr_1": rr_1,
        "risk_pct": risk_pct,
        "room_to_resistance_r": round(room_to_resistance_r, 1),
        "score": total_score,
        "classification": classification,
        "score_breakdown": {
            "pattern": int(score_pattern),
            "volume": int(score_volume),
            "price_action": int(score_price_action),
            "structure": int(score_structure),
            "risk": int(score_risk),
            "confluence": int(score_confluence),
        },
        "clv": round(clv, 2),
        "upper_wick_pct": round(upper_wick_pct, 2),
        "rvol": round(vol_ratio, 2),
        "alert_time": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
    }


# =====================================================================================
# 4. SCANNER EXECUTION RUNNER
# =====================================================================================

def run_technical_scan(
    run_date: Optional[str] = None,
    is_test_mode: bool = False,
    run_ctx: Any = None,
    trigger_type: str = "SCHEDULED",
    scheduler_name: str = "CRON",
) -> int:
    """
    Main Execution Entry Point for Unified TECHNICAL Scanner.
    Runs daily at 18:15 IST (6:15 PM IST) post-market close.
    """
    if _scan_lock.locked():
        logger.warning("🛑 [DUPLICATE GUARD] TECHNICAL Scanner is ALREADY actively running in thread lock. Skipping duplicate trigger.")
        if run_ctx:
            complete_scanner_execution_run(run_ctx, status_override="SKIPPED_DUPLICATE", stop_reason="Scanner lock busy")
        return 0

    acquired_global = False
    acquired_scan = False
    start_time = time.monotonic()
    real_run_ctx = run_ctx

    try:
        if not _scan_lock.acquire(blocking=False):
            logger.warning("🔒 [TECHNICAL] Scanner is already running. Skipping duplicate cycle.")
            return 0
        acquired_scan = True

        # Acquire universal global scanner lock
        if not _global_lock.acquire(blocking=False, owner_scanner="TECHNICAL", operation="FULL_SCAN"):
            logger.info("⏳ [TECHNICAL] Global scanner lock busy — waiting in queue until active scanner finishes...")
            upsert_scanner_health("TECHNICAL", "QUEUED", error_msg="Waiting in queue for active scanner to release lock...")

            try:
                acquired_global = _global_lock.acquire(blocking=True, owner_scanner="TECHNICAL", operation="FULL_SCAN", run_ctx=real_run_ctx)
            except Exception as lock_err:
                logger.error(f"❌ [TECHNICAL] Error acquiring global lock: {lock_err}")
                acquired_global = False

            if not acquired_global:
                logger.error("❌ [TECHNICAL] Failed to acquire global scanner lock after queue wait.")
                if real_run_ctx:
                    complete_scanner_execution_run(real_run_ctx, status_override="FAILED", stop_reason="Global lock acquire timeout")
                upsert_scanner_health("TECHNICAL", "IDLE", error_msg="Lock acquisition timed out")
                return 0
        else:
            acquired_global = True

        telemetry.log_scheduler_event("TECHNICAL", "CYCLE_START")

        logger.info("=" * 70)
        logger.info("🚀 TECHNICAL SCANNER | Starting 6:15 PM Multi-Pattern Technical Execution...")
        logger.info("=" * 70)

        if not real_run_ctx:
            try:
                real_run_ctx = start_scanner_execution_run(
                    scanner_name="TECHNICAL",
                    trigger_type=trigger_type,
                    scheduler_name=scheduler_name,
                )
            except Exception as exc:
                logger.warning(f"⚠️ [TECHNICAL] Could not create run_ctx: {exc}")
                real_run_ctx = None

        init_db()
        upsert_scanner_health(
            scanner_name="TECHNICAL",
            status="RUNNING",
            error_msg="Multi-Pattern Technical scan in progress...",
            scheduled_for="Daily 18:15 IST (Post-Close Technical Scan)",
        )

        # 1. Fetch Universe Watchlist (From Daily Builder master universe)
        wl_df = get_watchlist("TECHNICAL")
        if isinstance(wl_df, pd.DataFrame) and "Stock" in wl_df.columns:
            watchlist = wl_df["Stock"].dropna().tolist()
        elif isinstance(wl_df, (list, set, tuple)):
            watchlist = list(wl_df)
        else:
            watchlist = get_elite_watchlist() or []

        if not watchlist:
            logger.warning("⚠️ [TECHNICAL] Watchlist is empty.")
            upsert_scanner_health(
                scanner_name="TECHNICAL",
                status="OK",
                outcome="SUCCESS",
                processed_count=0,
                duration_seconds=round(time.monotonic() - start_time, 2),
                scheduled_for="Daily 18:15 IST (Post-Close Technical Scan)",
            )
            telemetry.log_scheduler_event("TECHNICAL", "CYCLE_COMPLETE")
            if real_run_ctx:
                complete_scanner_execution_run(real_run_ctx)
            return 0

        try:
            from surveillance import get_live_blacklist
            bl = get_live_blacklist()
            if bl:
                watchlist = [s for s in watchlist if str(s).upper() not in bl]
        except Exception:
            pass

        logger.info(f"📋 [TECHNICAL] Screening {len(watchlist)} universe stocks on Daily timeframe...")

        # 2. Fetch 1d OHLCV Data for Watchlist (with delta caching and heartbeat tracking)
        all_1d = fetch_watchlist_data(
            watchlist,
            period="1y",
            interval="1d",
            requester="TECHNICAL",
            run_ctx=real_run_ctx,
        )

        qualified_candidates: List[Dict[str, Any]] = []
        alerts_saved = 0

        for symbol in watchlist:
            df = all_1d.get(symbol)
            if df is None or df.empty:
                continue

            try:
                res = detect_technical_setup(df, symbol)
                if res and res.get("score", 0) >= 70:
                    qualified_candidates.append(res)
            except Exception as e:
                logger.debug(f"Error evaluating {symbol} in technical scanner: {e}")

        logger.info(
            f"🎯 [TECHNICAL] Screened {len(watchlist)} symbols -> Found {len(qualified_candidates)} qualified Technical setups!"
        )

        # 3. Sort by Score and Register Breakout Alerts
        qualified_candidates.sort(key=lambda x: x["score"], reverse=True)

        for cand in qualified_candidates:
            sym = cand["symbol"]
            cmp_price = cand["cmp"]
            score = cand["score"]
            sl = cand["stop_loss"]
            t1 = cand["target_1"]
            t2 = cand["target_2"]
            t3 = cand["target_3"]
            pat = cand["primary_pattern"]
            classification = cand["classification"]
            rvol = cand["rvol"]
            desc = cand["description"]

            logger.info(
                f"{classification} [TECHNICAL TRIGGERED] {sym} | Pattern: {pat} | CMP: ₹{cmp_price:.2f} | "
                f"RVOL: {rvol:.2f}x | SL: ₹{sl:.2f} | T1: ₹{t1:.2f} | Score: {score}/100 | {desc}"
            )

            if not is_test_mode:
                inserted, reason, _, _ = save_alert_if_new(
                    symbol=sym,
                    breakout_type="TECHNICAL",
                    alert_time=datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
                    scanner="TECHNICAL",
                    category="SWING",
                    entry_price=cmp_price,
                    stop_loss=sl,
                    target_1=t1,
                    target_2=t2,
                    target_3=t3,
                    signals=pat,
                    score=int(score),
                    context={
                        "primary_pattern": pat,
                        "tier": cand["tier"],
                        "description": desc,
                        "secondary_patterns": cand.get("secondary_patterns", []),
                        "confluences": cand.get("confluences", []),
                        "rvol": rvol,
                        "clv": cand["clv"],
                        "upper_wick_pct": cand["upper_wick_pct"],
                        "classification": classification,
                        "risk_pct": cand["risk_pct"],
                        "room_to_resistance_r": cand["room_to_resistance_r"],
                        "score_breakdown": cand["score_breakdown"],
                    },
                    entry_mode="BREAKOUT_TRIGGER",
                )
                if inserted:
                    alerts_saved += 1
                    try:
                        from telegram_engine import queue_telegram_message
                        sec_pats = cand.get("secondary_patterns", [])
                        sec_str = ", ".join(s.replace("_", " ") for s in sec_pats) if sec_pats else "None"
                        conf_list = cand.get("confluences", [])
                        conf_str = ", ".join(c.replace("_", " ") for c in conf_list) if conf_list else "None"
                        sb = cand.get("score_breakdown", {})

                        tg_msg = (
                            f"🚀 <b>TECHNICAL SCANNER ALERT ({classification})</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"📈 <b>Stock:</b> #{sym}\n"
                            f"💰 <b>Entry CMP:</b> ₹{cmp_price:.2f}\n"
                            f"📐 <b>Primary Pattern:</b> {pat.replace('_', ' ')} (Tier {cand['tier']})\n"
                            f"📝 <b>Structure Details:</b> {desc}\n"
                            f"📦 <b>Volume Surge:</b> {rvol:.2f}x RVOL (Hard Gate: ≥1.20x)\n"
                            f"🕯️ <b>Candle Quality:</b> CLV {cand['clv']:.2f} | Upper Wick {cand['upper_wick_pct']:.1f}%\n"
                            f"✨ <b>Confluences:</b> {conf_str}\n"
                            f"🔄 <b>Secondary Patterns:</b> {sec_str}\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"🛡️ <b>Stop Loss:</b> ₹{sl:.2f} (-{cand['risk_pct']}%)\n"
                            f"🎯 <b>Target 1:</b> ₹{t1:.2f} (1:1.5 RR)\n"
                            f"🎯 <b>Target 2:</b> ₹{t2:.2f} (1:3.0 RR)\n"
                            f"🎯 <b>Target 3:</b> ₹{t3:.2f} (1:4.5 RR)\n"
                            f"🚀 <b>Room to Resistance:</b> {cand['room_to_resistance_r']:.1f}R (Clear space ≥1.5R)\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"⭐ <b>Institutional Score:</b> {score}/100\n"
                            f"📊 <b>Score Breakdown:</b> Pat {sb.get('pattern_score', 0)}/25 | Vol {sb.get('volume_score', 0)}/25 | PA {sb.get('price_action_score', 0)}/20 | Struct {sb.get('structure_score', 0)}/15 | Risk {sb.get('risk_score', 0)}/10 | Conf {sb.get('confluence_score', 0)}/5\n"
                            f"🏷️ <b>Category:</b> SWING (Daily 1D · 3–15 Days)\n"
                            f"⏰ <b>Trigger Time:</b> {datetime.now(IST).strftime('%I:%M %p IST (%Y-%m-%d)')}"
                        )
                        queue_telegram_message(tg_msg, symbol=sym)
                    except Exception as _tg_err:
                        logger.debug(f"Telegram notification dispatch error: {_tg_err}")

        duration = round(time.monotonic() - start_time, 2)
        logger.info(
            f"✅ [TECHNICAL] Cycle complete in {duration}s | Processed: {len(watchlist)} | Alerts Saved: {alerts_saved}"
        )

        upsert_scanner_health(
            scanner_name="TECHNICAL",
            status="OK",
            last_success=datetime.now(IST).isoformat(),
            today_alerts=alerts_saved,
            processed_count=len(watchlist),
            total_count=len(watchlist),
            duration_seconds=duration,
            outcome="SUCCESS",
            error_msg=None,
            scheduled_for="Daily 18:15 IST (Post-Close Technical Scan)",
        )

        telemetry.log_scheduler_event("TECHNICAL", "CYCLE_COMPLETE")
        if real_run_ctx:
            complete_scanner_execution_run(real_run_ctx)

        return alerts_saved

    except Exception as exc:
        duration = round(time.monotonic() - start_time, 2)
        logger.exception(f"❌ [TECHNICAL] Fatal error during cycle: {exc}")
        upsert_scanner_health(
            scanner_name="TECHNICAL",
            status="DOWN",
            error_msg=str(exc)[:500],
            duration_seconds=duration,
            outcome="FAILED",
            scheduled_for="Daily 18:15 IST (Post-Close Technical Scan)",
        )
        telemetry.log_scheduler_event("TECHNICAL", "CYCLE_FAILED", error=str(exc))
        if real_run_ctx:
            try:
                complete_scanner_execution_run(real_run_ctx, exception=exc)
            except Exception:
                pass
        return 0
    finally:
        if acquired_global:
            try:
                _global_lock.release()
            except Exception as _ge:
                logger.debug(f"Error releasing global lock: {_ge}")
        if acquired_scan:
            try:
                _scan_lock.release()
            except Exception as _se:
                logger.debug(f"Error releasing scan lock: {_se}")

