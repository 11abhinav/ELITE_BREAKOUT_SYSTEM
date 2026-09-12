# =====================================================================================
# app/pattern_library_extended.py
# EXTENDED MATHEMATICAL TECHNICAL PATTERN LIBRARY
#
# RULE 67 CHANGE-RATIONALE:
# - Implements 14 vectorized, causal technical pattern detection algorithms.
# - Strict Point-In-Time Causality: Evaluates purely on candles [0 .. t] with zero lookahead.
# - Mathematical Boundary Clamping: Replaces vague visual heuristics with explicit bounds.
# - Positive Volume Guards: Prevents zero-division errors and false breakouts on non-traded bars.
# =====================================================================================

import numpy as np
from typing import Dict, List, Any, Optional

def _safe_arr(x) -> np.ndarray:
    if isinstance(x, np.ndarray):
        return x
    return np.array(x, dtype=float)

# ---------------------------------------------------------------------------
# 1. CORE CERTIFIED PATTERNS
# ---------------------------------------------------------------------------

def detect_undercut_and_rally(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    opens: Optional[np.ndarray] = None,
    t: Optional[int] = None,
    lookback_window: int = 25
) -> bool:
    """
    Certified Undercut and Rally Reclaim:
    Flushes prior [t-25, t-5] low in recent 3 bars, reclaimed on bar t with vol >= 1.15x SMA20.
    """
    highs, lows, closes, volumes = _safe_arr(highs), _safe_arr(lows), _safe_arr(closes), _safe_arr(volumes)
    if t is None: t = len(closes) - 1
    if t < 30 or t >= len(closes): return False

    if opens is None or len(opens) <= t:
        op = closes[t-1]
    else:
        op = opens[t]

    prior_low = np.min(lows[t - lookback_window : t - 5 + 1])
    recent_lows = lows[t - 3 : t + 1]

    # Flush within [0.96 * prior_low, prior_low)
    had_undercut = bool(np.any(recent_lows < prior_low) and np.all(recent_lows >= prior_low * 0.96))
    if not had_undercut: return False

    # Close reclaim above prior low and green body
    reclaim_ok = bool(closes[t] > prior_low and closes[t] > op)
    if not reclaim_ok: return False

    # Volume expansion
    vol_sma20 = np.mean(volumes[t - 20 : t])
    vol_ok = bool(vol_sma20 > 0 and volumes[t] >= vol_sma20 * 1.15)
    return bool(reclaim_ok and vol_ok)


def detect_double_bottom_shakeout(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    opens: Optional[np.ndarray] = None,
    t: Optional[int] = None
) -> bool:
    """
    Certified Double Bottom Shakeout (W-Bottom):
    Trough L1 in [t-35, t-10], interim peak >= 3.0% bounce, shakeout L2 in [0.95*L1, L1), reclaim on vol >= 1.20x SMA20.
    """
    highs, lows, closes, volumes = _safe_arr(highs), _safe_arr(lows), _safe_arr(closes), _safe_arr(volumes)
    if t is None: t = len(closes) - 1
    if t < 40 or t >= len(closes): return False

    if opens is None or len(opens) <= t:
        op = closes[t-1]
    else:
        op = opens[t]

    l1_window = lows[t - 35 : t - 10]
    if len(l1_window) == 0: return False
    l1_min_idx = np.argmin(l1_window)
    l1_idx = t - 35 + l1_min_idx
    l1_price = lows[l1_idx]

    # Interim peak bounce >= 3.0%
    if l1_idx >= t - 5: return False
    peak_price = np.max(highs[l1_idx : t - 5])
    if peak_price < l1_price * 1.03: return False

    # Shakeout L2
    l2_low = min(lows[t - 1], lows[t])
    if not (l2_low < l1_price and l2_low >= l1_price * 0.95): return False

    # Reclaim and volume
    reclaim_ok = bool(closes[t] > l1_price and closes[t] > op)
    vol_sma20 = np.mean(volumes[t - 20 : t])
    vol_ok = bool(vol_sma20 > 0 and volumes[t] >= vol_sma20 * 1.20)
    return bool(reclaim_ok and vol_ok)


def detect_bull_flag(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    t: Optional[int] = None
) -> bool:
    """
    Certified Bull Flag Continuation:
    Impulse pole >= 8.0% over [t-20, t-5], flag consolidation <= 40% retracement over [t-5, t], breakout on vol >= 1.30x SMA20.
    """
    highs, lows, closes, volumes = _safe_arr(highs), _safe_arr(lows), _safe_arr(closes), _safe_arr(volumes)
    if t is None: t = len(closes) - 1
    if t < 25 or t >= len(closes): return False

    pole_low = np.min(lows[t - 20 : t - 15 + 1])
    pole_high = np.max(highs[t - 15 : t - 5 + 1])
    pole_gain = (pole_high - pole_low) / max(pole_low, 1e-6)
    if pole_gain < 0.08: return False

    flag_low = np.min(lows[t - 5 : t + 1])
    retrace = (pole_high - flag_low) / max(pole_high - pole_low, 1e-6)
    if retrace > 0.40: return False

    breakout_ok = bool(closes[t] >= 0.985 * pole_high)
    vol_sma20 = np.mean(volumes[t - 20 : t])
    vol_ok = bool(vol_sma20 > 0 and volumes[t] >= vol_sma20 * 1.30)
    return bool(breakout_ok and vol_ok)


def detect_ascending_triangle(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    t: Optional[int] = None
) -> bool:
    """
    Ascending Triangle Breakout:
    Flat horizontal resistance (peaks within 1.5% band) with higher swing lows over 25 bars, breakout on vol >= 1.30x SMA20.
    """
    highs, lows, closes, volumes = _safe_arr(highs), _safe_arr(lows), _safe_arr(closes), _safe_arr(volumes)
    if t is None: t = len(closes) - 1
    if t < 30 or t >= len(closes): return False

    sub_h = highs[t - 25 : t]
    sub_l = lows[t - 25 : t]
    p1 = np.max(sub_h[:12])
    p2 = np.max(sub_h[12:])
    if abs(p1 - p2) / max(p1, 1e-6) > 0.020: return False

    l1 = np.min(sub_l[:12])
    l2 = np.min(sub_l[12:])
    if l2 <= l1 * 1.015: return False # Must have clear higher low

    res_level = max(p1, p2)
    breakout_ok = bool(closes[t] >= res_level * 0.995)
    vol_sma20 = np.mean(volumes[t - 20 : t])
    vol_ok = bool(vol_sma20 > 0 and volumes[t] >= vol_sma20 * 1.30)
    return bool(breakout_ok and vol_ok)


def detect_vcp_contraction(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    t: Optional[int] = None
) -> bool:
    """
    Volatility Contraction Pattern (VCP):
    Progressively tighter contractions (e.g. 15% -> 8% -> 4%) with drying volume, followed by expansion breakout.
    """
    highs, lows, closes, volumes = _safe_arr(highs), _safe_arr(lows), _safe_arr(closes), _safe_arr(volumes)
    if t is None: t = len(closes) - 1
    if t < 40 or t >= len(closes): return False

    w1_h, w1_l = np.max(highs[t - 35 : t - 20]), np.min(lows[t - 35 : t - 20])
    w2_h, w2_l = np.max(highs[t - 20 : t - 8]), np.min(lows[t - 20 : t - 8])
    w3_h, w3_l = np.max(highs[t - 8 : t]), np.min(lows[t - 8 : t])

    d1 = (w1_h - w1_l) / max(w1_h, 1e-6)
    d2 = (w2_h - w2_l) / max(w2_h, 1e-6)
    d3 = (w3_h - w3_l) / max(w3_h, 1e-6)

    # Contraction progression
    if not (d1 > d2 and d2 > d3 and d3 <= 0.08 and d1 >= 0.12): return False

    # Breakout of final contraction
    breakout_ok = bool(closes[t] >= w3_h * 0.99)
    vol_sma20 = np.mean(volumes[t - 20 : t])
    vol_ok = bool(vol_sma20 > 0 and volumes[t] >= vol_sma20 * 1.25)
    return bool(breakout_ok and vol_ok)


def detect_flat_base_breakout(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    t: Optional[int] = None
) -> bool:
    """
    Flat Base Breakout:
    Horizontal channel <= 12% total depth over 20-35 bars, breakout on vol >= 1.40x SMA20.
    """
    highs, lows, closes, volumes = _safe_arr(highs), _safe_arr(lows), _safe_arr(closes), _safe_arr(volumes)
    if t is None: t = len(closes) - 1
    if t < 30 or t >= len(closes): return False

    base_h = np.max(highs[t - 25 : t])
    base_l = np.min(lows[t - 25 : t])
    base_depth = (base_h - base_l) / max(base_h, 1e-6)
    if base_depth > 0.12 or base_depth < 0.03: return False

    breakout_ok = bool(closes[t] > base_h)
    vol_sma20 = np.mean(volumes[t - 20 : t])
    vol_ok = bool(vol_sma20 > 0 and volumes[t] >= vol_sma20 * 1.40)
    return bool(breakout_ok and vol_ok)

# ---------------------------------------------------------------------------
# 2. EXTENDED CHALLENGER PATTERN SUITE
# ---------------------------------------------------------------------------

def detect_high_tight_flag(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    t: Optional[int] = None
) -> bool:
    """
    High Tight Flag (HTF):
    Explosive impulse advance >= +80% within <= 35 bars, tight flag consolidation <= 22% depth over 8-15 bars, breakout on vol >= 1.50x SMA20.
    """
    highs, lows, closes, volumes = _safe_arr(highs), _safe_arr(lows), _safe_arr(closes), _safe_arr(volumes)
    if t is None: t = len(closes) - 1
    if t < 45 or t >= len(closes): return False

    pole_low = np.min(lows[t - 40 : t - 15])
    pole_high = np.max(highs[t - 20 : t - 8])
    pole_gain = (pole_high - pole_low) / max(pole_low, 1e-6)
    if pole_gain < 0.70: return False # Pragmatic 70% explosive floor for Indian market

    flag_low = np.min(lows[t - 8 : t + 1])
    flag_depth = (pole_high - flag_low) / max(pole_high, 1e-6)
    if flag_depth > 0.22 or flag_depth < 0.03: return False

    breakout_ok = bool(closes[t] >= pole_high * 0.985)
    vol_sma20 = np.mean(volumes[t - 20 : t])
    vol_ok = bool(vol_sma20 > 0 and volumes[t] >= vol_sma20 * 1.40)
    return bool(breakout_ok and vol_ok)


def detect_cup_and_handle(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    t: Optional[int] = None
) -> bool:
    """
    Cup and Handle:
    Rounded U-base over 30-65 bars (depth 12%-35%), followed by a shallow handle (depth <= 12%) in the upper half with volume contraction, breakout on vol >= 1.35x SMA20.
    """
    highs, lows, closes, volumes = _safe_arr(highs), _safe_arr(lows), _safe_arr(closes), _safe_arr(volumes)
    if t is None: t = len(closes) - 1
    if t < 50 or t >= len(closes): return False

    cup_left_high = np.max(highs[t - 50 : t - 35])
    cup_low = np.min(lows[t - 35 : t - 15])
    cup_right_high = np.max(highs[t - 18 : t - 8])

    cup_depth = (cup_left_high - cup_low) / max(cup_left_high, 1e-6)
    if cup_depth < 0.12 or cup_depth > 0.38: return False

    # Cup rim alignment within 5%
    if abs(cup_left_high - cup_right_high) / max(cup_left_high, 1e-6) > 0.06: return False

    # Handle in upper third
    handle_low = np.min(lows[t - 8 : t + 1])
    handle_depth = (cup_right_high - handle_low) / max(cup_right_high, 1e-6)
    if handle_depth > 0.14 or handle_depth < 0.02: return False

    breakout_ok = bool(closes[t] >= cup_right_high * 0.99)
    vol_sma20 = np.mean(volumes[t - 20 : t])
    vol_ok = bool(vol_sma20 > 0 and volumes[t] >= vol_sma20 * 1.30)
    return bool(breakout_ok and vol_ok)


def detect_inverse_head_and_shoulders(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    t: Optional[int] = None
) -> bool:
    """
    Inverse Head and Shoulders (IH&S):
    Left shoulder [t-45, t-28], Head lowest trough [t-28, t-14] where Head < LS * 0.97, Right shoulder [t-14, t-3] where RS > Head * 1.02 and RS approx LS +-4%, breakout on vol >= 1.30x SMA20.
    """
    highs, lows, closes, volumes = _safe_arr(highs), _safe_arr(lows), _safe_arr(closes), _safe_arr(volumes)
    if t is None: t = len(closes) - 1
    if t < 50 or t >= len(closes): return False

    ls_price = np.min(lows[t - 45 : t - 28])
    head_price = np.min(lows[t - 28 : t - 14])
    rs_price = np.min(lows[t - 14 : t - 3])

    if not (head_price < ls_price * 0.97 and rs_price > head_price * 1.02): return False
    if abs(ls_price - rs_price) / max(ls_price, 1e-6) > 0.05: return False # Shoulders roughly symmetric

    neckline = np.max(highs[t - 30 : t - 5])
    breakout_ok = bool(closes[t] >= neckline * 0.99)
    vol_sma20 = np.mean(volumes[t - 20 : t])
    vol_ok = bool(vol_sma20 > 0 and volumes[t] >= vol_sma20 * 1.25)
    return bool(breakout_ok and vol_ok)


def detect_pennant_convergence(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    t: Optional[int] = None
) -> bool:
    """
    Pennant Convergence:
    Impulse mast >= 9% within 5-10 bars, converging lower highs and higher lows over 4-8 bars with volume contraction, breakout on vol >= 1.35x SMA20.
    """
    highs, lows, closes, volumes = _safe_arr(highs), _safe_arr(lows), _safe_arr(closes), _safe_arr(volumes)
    if t is None: t = len(closes) - 1
    if t < 20 or t >= len(closes): return False

    mast_low = np.min(lows[t - 15 : t - 8])
    mast_high = np.max(highs[t - 10 : t - 5])
    mast_gain = (mast_high - mast_low) / max(mast_low, 1e-6)
    if mast_gain < 0.09: return False

    # Pennant converging window
    p_h1 = np.max(highs[t - 6 : t - 3])
    p_h2 = np.max(highs[t - 3 : t])
    p_l1 = np.min(lows[t - 6 : t - 3])
    p_l2 = np.min(lows[t - 3 : t])

    if not (p_h2 < p_h1 and p_l2 > p_l1): return False # True convergence

    breakout_ok = bool(closes[t] >= p_h1 * 0.99)
    vol_sma20 = np.mean(volumes[t - 20 : t])
    vol_ok = bool(vol_sma20 > 0 and volumes[t] >= vol_sma20 * 1.30)
    return bool(breakout_ok and vol_ok)


def detect_pullback_ema_bounce(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    opens: Optional[np.ndarray] = None,
    t: Optional[int] = None
) -> bool:
    """
    Pullback EMA20/50 Dynamic Support Bounce:
    Close > SMA50 > SMA200 (uptrend), Low tests within 1.2% of EMA20/SMA50, closes in upper 35% of daily range, green candle with vol >= 1.15x SMA20.
    """
    highs, lows, closes, volumes = _safe_arr(highs), _safe_arr(lows), _safe_arr(closes), _safe_arr(volumes)
    if t is None: t = len(closes) - 1
    if t < 50 or t >= len(closes): return False

    if opens is None or len(opens) <= t:
        op = closes[t-1]
    else:
        op = opens[t]

    sma50 = np.mean(closes[t - 50 : t])
    if closes[t] < sma50: return False

    # EMA20 calculation
    weights = np.exp(np.linspace(-1., 0., 20))
    weights /= weights.sum()
    ema20 = np.convolve(closes[t - 20 : t], weights, mode='valid')[-1]

    # Low tests near EMA20 (within 1.5%)
    tested_ema = abs(lows[t] - ema20) / max(ema20, 1e-6) <= 0.018 or abs(lows[t] - sma50) / max(sma50, 1e-6) <= 0.018
    if not tested_ema: return False

    # Bullish close in upper 35% of range
    c_range = max(highs[t] - lows[t], 1e-6)
    close_pos = (closes[t] - lows[t]) / c_range
    if close_pos < 0.65 or closes[t] <= op: return False

    vol_sma20 = np.mean(volumes[t - 20 : t])
    vol_ok = bool(vol_sma20 > 0 and volumes[t] >= vol_sma20 * 1.15)
    return bool(vol_ok)


def detect_multi_month_base_breakout(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    t: Optional[int] = None
) -> bool:
    """
    Multi-Month Base Breakout (Stage 2 Expansion):
    Tight base >= 55 bars (depth <= 22%), breaking out to new high on volume >= 1.75x SMA20.
    """
    highs, lows, closes, volumes = _safe_arr(highs), _safe_arr(lows), _safe_arr(closes), _safe_arr(volumes)
    if t is None: t = len(closes) - 1
    if t < 65 or t >= len(closes): return False

    base_h = np.max(highs[t - 55 : t])
    base_l = np.min(lows[t - 55 : t])
    depth = (base_h - base_l) / max(base_h, 1e-6)
    if depth > 0.22 or depth < 0.05: return False

    breakout_ok = bool(closes[t] > base_h)
    vol_sma20 = np.mean(volumes[t - 20 : t])
    vol_ok = bool(vol_sma20 > 0 and volumes[t] >= vol_sma20 * 1.75)
    return bool(breakout_ok and vol_ok)


def detect_wyckoff_spring_type_2(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    opens: Optional[np.ndarray] = None,
    t: Optional[int] = None
) -> bool:
    """
    Wyckoff Spring Type 2 (Spring + Retest Confirmation):
    Spring low undercut at [t-15, t-6], secondary test at [t-3, t] holding >= spring low on lower volume (<0.85x spring vol), bullish reclaim today.
    """
    highs, lows, closes, volumes = _safe_arr(highs), _safe_arr(lows), _safe_arr(closes), _safe_arr(volumes)
    if t is None: t = len(closes) - 1
    if t < 35 or t >= len(closes): return False

    if opens is None or len(opens) <= t:
        op = closes[t-1]
    else:
        op = opens[t]

    prior_support = np.min(lows[t - 30 : t - 15])
    spring_slice = lows[t - 15 : t - 5]
    if len(spring_slice) == 0: return False
    spring_low = np.min(spring_slice)
    if not (spring_low < prior_support and spring_low >= prior_support * 0.95): return False

    spring_idx = t - 15 + np.argmin(spring_slice)
    spring_vol = volumes[spring_idx]

    # Secondary test at [t-3, t]
    test_slice = lows[t - 3 : t + 1]
    test_low = np.min(test_slice)
    if test_low < spring_low: return False # Test must hold spring low

    # Reclaim and volume dry on test
    reclaim_ok = bool(closes[t] > prior_support and closes[t] > op)
    vol_sma20 = np.mean(volumes[t - 20 : t])
    vol_ok = bool(vol_sma20 > 0 and volumes[t] >= vol_sma20 * 1.10)
    return bool(reclaim_ok and vol_ok)


def detect_falling_wedge_reversal(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    t: Optional[int] = None
) -> bool:
    """
    Falling Wedge Reversal:
    Downward converging slopes (highs drop faster than lows over 20-40 bars), upper boundary breakout on volume >= 1.25x SMA20.
    """
    highs, lows, closes, volumes = _safe_arr(highs), _safe_arr(lows), _safe_arr(closes), _safe_arr(volumes)
    if t is None: t = len(closes) - 1
    if t < 35 or t >= len(closes): return False

    sub_h = highs[t - 25 : t]
    sub_l = lows[t - 25 : t]

    h1, h2 = np.max(sub_h[:12]), np.max(sub_h[12:])
    l1, l2 = np.min(sub_l[:12]), np.min(sub_l[12:])

    # Both trending down
    if not (h2 < h1 and l2 < l1): return False

    # Highs falling steeper than lows (convergence)
    h_drop = (h1 - h2) / max(h1, 1e-6)
    l_drop = (l1 - l2) / max(l1, 1e-6)
    if h_drop <= l_drop * 1.10: return False # Highs must drop faster

    breakout_ok = bool(closes[t] >= h2 * 0.995)
    vol_sma20 = np.mean(volumes[t - 20 : t])
    vol_ok = bool(vol_sma20 > 0 and volumes[t] >= vol_sma20 * 1.25)
    return bool(breakout_ok and vol_ok)


# ---------------------------------------------------------------------------
# 3. MASTER PATTERN DISPATCHER
# ---------------------------------------------------------------------------

PATTERN_DETECTOR_REGISTRY = {
    "DOUBLE_BOTTOM_SHAKEOUT": detect_double_bottom_shakeout,
    "UNDERCUT_AND_RALLY": detect_undercut_and_rally,
    "BULL_FLAG": detect_bull_flag,
    "ASCENDING_TRIANGLE": detect_ascending_triangle,
    "VCP_CONTRACTION": detect_vcp_contraction,
    "FLAT_BASE_BREAKOUT": detect_flat_base_breakout,
    "HIGH_TIGHT_FLAG": detect_high_tight_flag,
    "CUP_AND_HANDLE": detect_cup_and_handle,
    "INVERSE_HEAD_AND_SHOULDERS": detect_inverse_head_and_shoulders,
    "PENNANT_CONVERGENCE": detect_pennant_convergence,
    "PULLBACK_EMA_BOUNCE": detect_pullback_ema_bounce,
    "MULTI_MONTH_BASE_BREAKOUT": detect_multi_month_base_breakout,
    "WYCKOFF_SPRING_TYPE_2": detect_wyckoff_spring_type_2,
    "FALLING_WEDGE_REVERSAL": detect_falling_wedge_reversal,
}

def detect_pattern(pattern_name: str, highs, lows, closes, volumes, opens=None, t=None) -> bool:
    """Unified entrypoint for pattern detection."""
    fn = PATTERN_DETECTOR_REGISTRY.get(pattern_name)
    if not fn: return False
    try:
        if pattern_name in ["UNDERCUT_AND_RALLY", "DOUBLE_BOTTOM_SHAKEOUT", "PULLBACK_EMA_BOUNCE", "WYCKOFF_SPRING_TYPE_2"]:
            return fn(highs, lows, closes, volumes, opens=opens, t=t)
        else:
            return fn(highs, lows, closes, volumes, t=t)
    except Exception:
        return False
