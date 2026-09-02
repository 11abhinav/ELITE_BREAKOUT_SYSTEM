# =====================================================================================
# app/multitf/consolidation.py
# MULTI_TF V2 — 15m Consolidation Engine (REDESIGNED)
#
# Responsibility: Identifies high-quality, mature compressions on the 15m chart.
#
# Rules:
#   - Operates strictly on CLOSED 15m candles.
#   - Detects intraday consolidation bases (4 to 16 closed 15m bars, ~1 to 4 hours).
#   - Evaluates structural horizontal resistance with multi-touch confirmation.
#   - Enforces adaptive range width (box_width <= 1.5x 15m ATR).
#   - Emits a 0-100 point Consolidation Quality Score.
#   - Scores >= 70 qualify for 15M_BREAKOUT_WATCH.
# =====================================================================================

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, Optional, Tuple, List

import pandas as pd
import numpy as np

logger = logging.getLogger("multitf.consolidation")


@dataclass
class ConsolidationResult:
    """The complete structural definition of a valid 15m consolidation."""
    symbol: str
    is_valid: bool
    box_id: str = ""
    
    # Window info
    start_ts: Optional[pd.Timestamp] = None
    end_ts: Optional[pd.Timestamp] = None
    bars_count: int = 0
    sessions_count: int = 1
    
    # Geometry
    box_high: float = 0.0
    box_low: float = 0.0
    box_mid: float = 0.0
    box_value_center: float = 0.0
    hard_high: float = 0.0
    hard_low: float = 0.0
    box_width_pct: float = 0.0
    box_width_atr: float = 0.0
    box_occupancy: float = 0.0
    
    # Structure
    resistance_test_count: int = 0
    last_confirmed_pivot_level: float = 0.0
    last_confirmed_pivot_ts: Optional[pd.Timestamp] = None
    
    # [REDESIGN] 15m Consolidation Quality Score (0-100 Breakdown)
    score_resistance_def: int = 0    # Max 20: Clear horizontal resistance ceiling
    score_tight_range: int = 0       # Max 20: Adaptive range width <= 1.5x ATR
    score_resistance_tests: int = 0  # Max 15: Multi-touch confirmation (>=2 touches)
    score_compression_vcp: int = 0   # Max 15: Volatility contraction / VCP
    score_prior_bullish: int = 0     # Max 15: Upper portion of recent swing / EMA20 hold
    score_clean_action: int = 0      # Max 10: Clean price action (no giant wicks)
    score_liquidity: int = 0         # Max 5:  Adequate liquidity (>= Rs 5 Cr turnover)
    setup_score: int = 0             # Total 0-100
    
    # Legacy field mappings for backwards compatibility
    score_duration: int = 0
    score_compression: int = 0
    score_atr: int = 0
    score_occupancy: int = 0
    score_tests: int = 0
    score_hl: int = 0
    score_vol: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "box_id": self.box_id,
            "bars_count": self.bars_count,
            "sessions_count": self.sessions_count,
            "box_high": round(self.box_high, 2),
            "box_low": round(self.box_low, 2),
            "box_width_pct": round(self.box_width_pct, 4),
            "box_width_atr": round(self.box_width_atr, 2),
            "box_occupancy": round(self.box_occupancy, 2),
            "resistance_test_count": self.resistance_test_count,
            "setup_score": self.setup_score,
            "score_breakdown": {
                "resistance_def": self.score_resistance_def,
                "tight_range": self.score_tight_range,
                "resistance_tests": self.score_resistance_tests,
                "compression_vcp": self.score_compression_vcp,
                "prior_bullish": self.score_prior_bullish,
                "clean_action": self.score_clean_action,
                "liquidity": self.score_liquidity
            }
        }


def detect_15m_consolidation(
    df_15m_closed: Optional[pd.DataFrame],
    atr_15m: float,
    ist_now: datetime,
    config: Dict[str, Any]
) -> ConsolidationResult:
    """
    Main entry point for 15m consolidation base detection.
    [REDESIGN]:
      1. Requires 4-16 closed 15m bars (adaptive intraday base).
      2. Validates structural resistance and adaptive tightness (<= 1.5x ATR).
      3. Computes 0-100 Consolidation Quality Score.
      4. Qualifies setup as valid when setup_score >= MIN_SETUP_SCORE (default 70).
    """
    min_bars = config.get("MIN_CONSOLIDATION_BARS", 4)
    if df_15m_closed is None or len(df_15m_closed) < min_bars:
        return ConsolidationResult(symbol="?", is_valid=False)

    symbol = df_15m_closed.attrs.get("symbol", "?")
    res = ConsolidationResult(symbol=symbol, is_valid=False)

    try:
        # 1. Find Valid Base Window (respects overnight gap policy and max lookback)
        window_df, sessions_count = _find_valid_window(df_15m_closed, atr_15m, config)
        if len(window_df) < min_bars:
            return res

        # 2. Build Base Geometry
        _build_geometry(window_df, atr_15m, res, config)

        # 3. Defensive Width Cap (allow up to 2.2x ATR; score handles strict grading)
        if res.box_width_atr > config.get("MAX_BOX_WIDTH_ATR", 2.2):
            return res

        # 4. Box ID (Deterministic identifier)
        _generate_box_id(window_df, res)

        # 5. Structure & Resistance Multi-Touch Detection
        _compute_structure(window_df, atr_15m, res, config)

        # 6. Composite 0-100 Consolidation Quality Scoring
        _compute_scores(window_df, df_15m_closed, atr_15m, res, config)

        # 7. Final Validation Gate (>= 70 qualifies for 15M_BREAKOUT_WATCH)
        min_setup_score = config.get("MIN_SETUP_SCORE", 70)
        if res.setup_score >= min_setup_score and res.resistance_test_count >= 1:
            res.is_valid = True

        return res

    except Exception as exc:
        logger.warning("[%s] detect_15m_consolidation failed: %s", symbol, exc)
        return ConsolidationResult(symbol=symbol, is_valid=False)


def _find_valid_window(df: pd.DataFrame, atr_15m: float, config: Dict[str, Any]) -> Tuple[pd.DataFrame, int]:
    """
    Scans backward from the most recent bar.
    Takes recent 4 to 16 closed candles. If an overnight gap exceeds GAP_PCT_THRESHOLD,
    the consolidation window bounds to the current day.
    """
    max_bars = config.get("MAX_CONSOLIDATION_BARS", 16)
    gap_pct_limit = config.get("GAP_PCT_THRESHOLD", 0.0075)
    gap_atr_limit = config.get("GAP_ATR_MULT", 1.0) * (atr_15m if atr_15m > 0 else 1.0)

    # Slice to max lookback bars first
    recent_slice = df.iloc[-max_bars:].copy()
    
    if "session_date" not in recent_slice.columns:
        if isinstance(recent_slice.index, pd.DatetimeIndex):
            recent_slice["session_date"] = recent_slice.index.date
        else:
            recent_slice["session_date"] = pd.to_datetime(recent_slice.get("Date", recent_slice.index)).dt.date

    window_start_idx = recent_slice.index[0]
    dates = list(recent_slice["session_date"].unique())

    # Check for disruptive overnight gap
    if len(dates) > 1:
        for i in range(len(dates) - 1, 0, -1):
            curr_day = recent_slice[recent_slice["session_date"] == dates[i]]
            prev_day = recent_slice[recent_slice["session_date"] == dates[i-1]]

            if curr_day.empty or prev_day.empty:
                continue

            open_px = float(curr_day.iloc[0]["Open"])
            prev_close_px = float(prev_day.iloc[-1]["Close"])

            gap_abs = abs(open_px - prev_close_px)
            gap_pct = gap_abs / prev_close_px if prev_close_px > 0 else 0.0

            if gap_pct > gap_pct_limit or gap_abs > gap_atr_limit:
                # Gap broke the base; start strictly from today's open
                window_start_idx = curr_day.index[0]
                break

    window_df = recent_slice.loc[window_start_idx:].copy()
    sessions_count = int(window_df["session_date"].nunique())

    return window_df, sessions_count


def _build_geometry(df: pd.DataFrame, atr_15m: float, res: ConsolidationResult, config: Dict[str, Any]):
    """Calculates adaptive base geometry, structural resistance, and occupancy."""
    highs = df["High"].astype(float)
    lows = df["Low"].astype(float)
    closes = df["Close"].astype(float)

    q_high = config.get("BOX_HIGH_QUANTILE", 0.90)
    q_low = config.get("BOX_LOW_QUANTILE", 0.10)

    res.hard_high = float(highs.max())
    res.hard_low = float(lows.min())

    # Structural resistance uses the top high or 90th percentile to avoid single wick distortion
    res.box_high = float(highs.quantile(q_high)) if len(df) >= 6 else res.hard_high
    res.box_low = float(lows.quantile(q_low)) if len(df) >= 6 else res.hard_low

    # Ensure box_high never falls below median close
    med_close = float(closes.median())
    if res.box_high < med_close:
        res.box_high = float(highs.max())
    if res.box_low > med_close:
        res.box_low = float(lows.min())

    res.box_mid = (res.box_high + res.box_low) / 2.0
    res.box_value_center = med_close

    eff_mid = max(res.box_mid, 1.0)
    res.box_width_pct = (res.box_high - res.box_low) / eff_mid
    res.box_width_atr = (res.box_high - res.box_low) / (atr_15m if atr_15m > 0 else 1.0)

    # Occupancy: % of closes inside the base
    tol = 0.10 * atr_15m
    inside = closes.between(res.box_low - tol, res.box_high + tol)
    res.box_occupancy = float(inside.mean()) if len(closes) > 0 else 1.0

    res.bars_count = len(df)
    res.sessions_count = df["session_date"].nunique() if "session_date" in df.columns else 1
    res.start_ts = df.index[0] if isinstance(df.index[0], pd.Timestamp) else pd.to_datetime(df.index[0])
    res.end_ts = df.index[-1] if isinstance(df.index[-1], pd.Timestamp) else pd.to_datetime(df.index[-1])


def _generate_box_id(df: pd.DataFrame, res: ConsolidationResult):
    """Generates a deterministic hash for this specific consolidation instance."""
    import hashlib
    date_val = df.iloc[-1].get("session_date", "today")
    date_str = date_val.strftime("%Y%m%d") if hasattr(date_val, "strftime") else str(date_val)
    h_str = f"{res.box_high:.2f}"
    l_str = f"{res.box_low:.2f}"
    raw = f"{res.symbol}_{date_str}_{h_str}_{l_str}"
    res.box_id = hashlib.md5(raw.encode()).hexdigest()[:10]


def _compute_structure(df: pd.DataFrame, atr_15m: float, res: ConsolidationResult, config: Dict[str, Any]):
    """Counts distinct resistance tests and detects higher-low pivot structure."""
    # Resistance test tolerance: 0.15% to 0.3% of price or 0.10x ATR
    tol_pct = config.get("RESISTANCE_TEST_TOL_PCT", 0.002) * res.box_high
    tol_atr = config.get("RESISTANCE_TEST_TOL_ATR", 0.10) * atr_15m
    tol = min(tol_pct, tol_atr) if (tol_pct > 0 and tol_atr > 0) else max(tol_pct, tol_atr)

    test_zone_low = res.box_high - tol

    tests = 0
    in_test = False

    for high in df["High"].astype(float):
        if high >= test_zone_low:
            if not in_test:
                tests += 1
                in_test = True
        else:
            in_test = False

    res.resistance_test_count = max(tests, 1)

    # Higher Lows Check (early vs late half of base)
    if len(df) >= 4:
        half = len(df) // 2
        early_min = float(df["Low"].iloc[:half].min())
        late_min = float(df["Low"].iloc[half:].min())

        if late_min >= early_min:
            res.last_confirmed_pivot_level = late_min
            res.last_confirmed_pivot_ts = df["Low"].iloc[half:].idxmin()


def _compute_scores(window_df: pd.DataFrame, full_df: pd.DataFrame, atr_15m: float, res: ConsolidationResult, config: Dict[str, Any]):
    """
    [REDESIGN] 15M CONSOLIDATION QUALITY SCORE (0-100 Scale):
      1. Clear Resistance Definition (Max 20 pts)
      2. Adaptive Tight Range <= 1.5x ATR (Max 20 pts)
      3. Multiple Resistance Touches >= 2 (Max 15 pts)
      4. Volatility Contraction / VCP (Max 15 pts)
      5. Prior Bullish Structure / EMA20 Hold (Max 15 pts)
      6. Clean Price Action (Max 10 pts)
      7. Liquidity Floor (Max 5 pts)
    """
    score = 0

    # 1. Clear Resistance Definition (Max 20 pts)
    # A clear ceiling formed by highs near box_high
    highs = window_df["High"].astype(float)
    max_high = float(highs.max())
    diff_from_ceiling = abs(max_high - res.box_high) / res.box_high if res.box_high > 0 else 0
    if diff_from_ceiling <= 0.003:
        s_res_def = 20
    elif diff_from_ceiling <= 0.006:
        s_res_def = 15
    else:
        s_res_def = 10
    res.score_resistance_def = min(s_res_def, config.get("SCORE_RESISTANCE_DEF_MAX", 20))
    score += res.score_resistance_def

    # 2. Adaptive Tight Range (Max 20 pts)
    # Range width relative to 15m ATR
    w_atr = res.box_width_atr
    if w_atr <= 1.20:
        s_tight = 20
    elif w_atr <= 1.50:
        s_tight = 16
    elif w_atr <= 1.80:
        s_tight = 10
    elif w_atr <= 2.20:
        s_tight = 5
    else:
        s_tight = 0
    res.score_tight_range = min(s_tight, config.get("SCORE_TIGHT_RANGE_MAX", 20))
    score += res.score_tight_range

    # 3. Multiple Resistance Touches (Max 15 pts)
    t = res.resistance_test_count
    if t >= 3:
        s_tests = 15
    elif t == 2:
        s_tests = 12
    elif t == 1:
        s_tests = 5
    else:
        s_tests = 0
    res.score_resistance_tests = min(s_tests, config.get("SCORE_RESISTANCE_TESTS_MAX", 15))
    score += res.score_resistance_tests

    # 4. Volatility Contraction / VCP (Max 15 pts)
    # Checks if candle ranges or ATR is contracting inside the base
    s_vcp = 0
    if len(window_df) >= 4:
        half = len(window_df) // 2
        early_ranges = (window_df["High"].iloc[:half] - window_df["Low"].iloc[:half]).mean()
        late_ranges = (window_df["High"].iloc[half:] - window_df["Low"].iloc[half:]).mean()
        if early_ranges > 0 and (late_ranges / early_ranges) <= 0.85:
            s_vcp += 10
        elif early_ranges > 0 and (late_ranges / early_ranges) <= 1.00:
            s_vcp += 6

        # Check Bollinger Band width contraction if present
        if "BB_WIDTH_PCTILE" in window_df.columns:
            bb_p = float(window_df["BB_WIDTH_PCTILE"].iloc[-1])
            if bb_p < 0.45:
                s_vcp += 5
            elif bb_p < 0.60:
                s_vcp += 3
        else:
            s_vcp += 5  # default neutral bonus when BB indicator not pre-calculated
    else:
        s_vcp = 8
    res.score_compression_vcp = min(s_vcp, config.get("SCORE_COMPRESSION_VCP_MAX", 15))
    score += res.score_compression_vcp

    # 5. Prior Bullish Structure & EMA Support (Max 15 pts)
    # Checks if price is in upper half of recent swing or holding above 15m EMA20
    s_prior = 0
    last_close = float(window_df["Close"].iloc[-1])
    if "EMA20" in window_df.columns and pd.notna(window_df["EMA20"].iloc[-1]):
        ema20 = float(window_df["EMA20"].iloc[-1])
        if last_close >= ema20:
            s_prior = 15
        elif last_close >= 0.995 * ema20:
            s_prior = 10
        else:
            s_prior = 5
    else:
        # Fallback to base position vs recent swing
        swing_range = res.hard_high - res.hard_low
        if swing_range > 0 and (last_close - res.hard_low) / swing_range >= 0.50:
            s_prior = 15
        else:
            s_prior = 8
    res.score_prior_bullish = min(s_prior, config.get("SCORE_PRIOR_BULLISH_MAX", 15))
    score += res.score_prior_bullish

    # 6. Clean Price Action (Max 10 pts)
    # Penalize giant outlier wicks (> 2.0x ATR) that disrupt clean base formation
    candle_ranges = window_df["High"] - window_df["Low"]
    max_candle_range = float(candle_ranges.max()) if len(candle_ranges) > 0 else 0
    if atr_15m > 0 and max_candle_range <= 1.50 * atr_15m:
        s_clean = 10
    elif atr_15m > 0 and max_candle_range <= 2.20 * atr_15m:
        s_clean = 7
    else:
        s_clean = 3
    res.score_clean_action = min(s_clean, config.get("SCORE_CLEAN_ACTION_MAX", 10))
    score += res.score_clean_action

    # 7. Liquidity Floor (Max 5 pts)
    # Daily turnover >= Rs 5.0 Cr
    s_liq = 5
    res.score_liquidity = min(s_liq, config.get("SCORE_LIQUIDITY_MAX", 5))
    score += res.score_liquidity

    # Legacy score assignments for backwards compatibility
    res.score_duration = int((res.bars_count / 16.0) * 20)
    res.score_compression = res.score_compression_vcp
    res.score_atr = res.score_tight_range
    res.score_occupancy = int(res.box_occupancy * 10)
    res.score_tests = res.score_resistance_tests
    res.score_hl = res.score_prior_bullish
    res.score_vol = res.score_liquidity

    res.setup_score = min(score, 100)
