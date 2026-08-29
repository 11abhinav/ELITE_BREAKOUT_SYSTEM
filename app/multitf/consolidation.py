# =====================================================================================
# app/multitf/consolidation.py
# MULTI_TF V2 — 15m Consolidation Engine
#
# Responsibility: Identifies high-quality, mature compressions on the 15m chart.
#
# Rules:
#   - Operates strictly on CLOSED 15m candles.
#   - Finds the valid session window (resets on overnight gaps).
#   - Builds a robust percentile-based box (10th-90th percentiles).
#   - Scores based on Duration, Compression, ATR Contraction, Occupancy, Tests, Higher-Lows.
#   - Emits a ConsolidationResult dataclass.
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
    
    # Scores (0-100 total)
    score_duration: int = 0
    score_compression: int = 0
    score_atr: int = 0
    score_occupancy: int = 0
    score_tests: int = 0
    score_hl: int = 0
    score_vol: int = 0
    setup_score: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "box_id": self.box_id,
            "bars_count": self.bars_count,
            "sessions_count": self.sessions_count,
            "box_high": self.box_high,
            "box_low": self.box_low,
            "box_width_pct": self.box_width_pct,
            "box_occupancy": self.box_occupancy,
            "resistance_test_count": self.resistance_test_count,
            "setup_score": self.setup_score
        }


def detect_15m_consolidation(
    df_15m_closed: Optional[pd.DataFrame],
    atr_15m: float,
    ist_now: datetime,
    config: Dict[str, Any]
) -> ConsolidationResult:
    """
    Main entry point for 15m consolidation detection.
    """
    if df_15m_closed is None or len(df_15m_closed) < config.get("MIN_CONSOLIDATION_BARS", 24):
        return ConsolidationResult(symbol="?", is_valid=False)

    symbol = df_15m_closed.attrs.get("symbol", "?")
    res = ConsolidationResult(symbol=symbol, is_valid=False)

    try:
        # 1. Find Valid Session Window (Gap Policy)
        window_df, sessions_count = _find_valid_window(df_15m_closed, atr_15m, config)
        if len(window_df) < config.get("MIN_CONSOLIDATION_BARS", 24):
            return res

        # 2. Build Robust Box
        _build_geometry(window_df, atr_15m, res, config)
        
        # 3. Validation Gates
        if res.box_width_pct > config.get("MAX_BOX_WIDTH_PCT", 0.025):
            return res
        if res.box_width_atr > config.get("MAX_BOX_WIDTH_ATR", 3.0):
            return res
        if res.box_occupancy < config.get("MIN_BOX_OCCUPANCY", 0.70):
            return res

        # 4. Box ID (Deterministic)
        _generate_box_id(window_df, res)
        
        # 5. Structure (Tests & Pivots)
        _compute_structure(window_df, atr_15m, res, config)
        
        # Gate: Needs at least 2 distinct tests of the ceiling
        if res.resistance_test_count < config.get("MIN_RESISTANCE_TESTS", 2):
            return res
            
        # 6. Scoring
        _compute_scores(window_df, df_15m_closed, atr_15m, res, config)
        
        # Final Gate
        if res.setup_score >= config.get("MIN_SETUP_SCORE", 60):
            res.is_valid = True
            
        return res

    except Exception as exc:
        logger.warning("[%s] detect_15m_consolidation failed: %s", symbol, exc)
        return ConsolidationResult(symbol=symbol, is_valid=False)


def _find_valid_window(df: pd.DataFrame, atr_15m: float, config: Dict[str, Any]) -> Tuple[pd.DataFrame, int]:
    """
    Scans backward from the most recent bar.
    If an overnight gap exceeds GAP_PCT_THRESHOLD or GAP_ATR_MULT, the consolidation window breaks.
    Returns the dataframe slice of the unbroken window, and the number of unique sessions.
    """
    gap_pct_limit = config.get("GAP_PCT_THRESHOLD", 0.0075)
    gap_atr_limit = config.get("GAP_ATR_MULT", 1.0) * atr_15m
    
    window_start_idx = df.index[0]
    dates = df["session_date"].unique()
    
    # Iterate backwards through days to find gap breaks
    for i in range(len(dates) - 1, 0, -1):
        curr_day = df[df["session_date"] == dates[i]]
        prev_day = df[df["session_date"] == dates[i-1]]
        
        if curr_day.empty or prev_day.empty:
            continue
            
        open_px = curr_day.iloc[0]["Open"]
        prev_close_px = prev_day.iloc[-1]["Close"]
        
        gap_abs = abs(open_px - prev_close_px)
        gap_pct = gap_abs / prev_close_px
        
        if gap_pct > gap_pct_limit or gap_abs > gap_atr_limit:
            # Consolidation broken by gap. The valid window starts at the open of curr_day.
            window_start_idx = curr_day.index[0]
            break
            
    window_df = df.loc[window_start_idx:].copy()
    sessions_count = window_df["session_date"].nunique()
    
    return window_df, sessions_count


def _build_geometry(df: pd.DataFrame, atr_15m: float, res: ConsolidationResult, config: Dict[str, Any]):
    """Calculates percentile box and occupancy."""
    highs = df["High"]
    lows = df["Low"]
    closes = df["Close"]
    
    q_high = config.get("BOX_HIGH_QUANTILE", 0.90)
    q_low = config.get("BOX_LOW_QUANTILE", 0.10)
    
    res.box_high = float(highs.quantile(q_high))
    res.box_low = float(lows.quantile(q_low))
    res.hard_high = float(highs.max())
    res.hard_low = float(lows.min())
    
    res.box_mid = (res.box_high + res.box_low) / 2.0
    res.box_value_center = float(closes.median())
    
    res.box_width_pct = (res.box_high - res.box_low) / res.box_mid
    res.box_width_atr = (res.box_high - res.box_low) / atr_15m if atr_15m > 0 else 999.0
    
    # Occupancy: % of closes inside box (with small ATR tolerance)
    tol = 0.10 * atr_15m
    inside = closes.between(res.box_low - tol, res.box_high + tol)
    res.box_occupancy = float(inside.mean())
    
    res.bars_count = len(df)
    res.sessions_count = df["session_date"].nunique()
    res.start_ts = df.index[0]
    res.end_ts = df.index[-1]


def _generate_box_id(df: pd.DataFrame, res: ConsolidationResult):
    """Generates a deterministic hash for this specific consolidation instance."""
    import hashlib
    date_str = df.iloc[-1]["session_date"].strftime("%Y%m%d")
    h_str = f"{res.box_high:.1f}"
    l_str = f"{res.box_low:.1f}"
    raw = f"{res.symbol}_{date_str}_{h_str}_{l_str}"
    res.box_id = hashlib.md5(raw.encode()).hexdigest()[:10]


def _compute_structure(df: pd.DataFrame, atr_15m: float, res: ConsolidationResult, config: Dict[str, Any]):
    """Counts distinct resistance tests and finds higher-low pivots."""
    # 1. Resistance Tests
    tol = config.get("RESISTANCE_TEST_TOL_ATR", 0.15) * atr_15m
    test_zone_low = res.box_high - tol
    test_zone_high = res.box_high + tol
    
    tests = 0
    in_test = False
    
    for high in df["High"]:
        if test_zone_low <= high <= test_zone_high:
            if not in_test:
                tests += 1
                in_test = True
        elif high < test_zone_low:
            in_test = False
            
    res.resistance_test_count = tests
    
    # 2. Pivot Higher-Lows (simplified robust check without look-ahead)
    # We look at the lowest Lows in the last 1/3rd of the box vs the first 2/3rds.
    third = len(df) // 3
    early_lows = df["Low"].iloc[:-third]
    late_lows = df["Low"].iloc[-third:]
    
    if len(early_lows) > 0 and len(late_lows) > 0:
        early_min = early_lows.min()
        late_min = late_lows.min()
        
        if late_min > early_min + (0.10 * atr_15m):
            res.last_confirmed_pivot_level = late_min
            # rough timestamp estimate
            res.last_confirmed_pivot_ts = late_lows.idxmin()


def _compute_scores(window_df: pd.DataFrame, full_df: pd.DataFrame, atr_15m: float, res: ConsolidationResult, config: Dict[str, Any]):
    """Assigns the 0-100 setup score."""
    score = 0
    
    # 1. Duration (Max 20)
    b = res.bars_count
    if b >= 40: s_dur = 20
    elif b >= 32: s_dur = 16
    elif b >= 24: s_dur = 12
    else: s_dur = 0
    res.score_duration = min(s_dur, config.get("SCORE_DURATION_MAX", 20))
    score += res.score_duration
    
    # 2. Compression (Max 20) - recent range vs older range
    half = len(window_df) // 2
    if half >= 10:
        recent_range = window_df["High"].iloc[-half:].max() - window_df["Low"].iloc[-half:].min()
        older_range = window_df["High"].iloc[:-half].max() - window_df["Low"].iloc[:-half].min()
        if older_range > 0:
            ratio = recent_range / older_range
            if ratio < 0.70: s_comp = 20
            elif ratio < 0.85: s_comp = 10
            else: s_comp = 0
            res.score_compression = min(s_comp, config.get("SCORE_COMPRESSION_MAX", 20))
            score += res.score_compression

    # 3. Occupancy (Max 10)
    occ = res.box_occupancy
    if occ >= 0.90: s_occ = 10
    elif occ >= 0.80: s_occ = 7
    elif occ >= 0.70: s_occ = 4
    else: s_occ = 0
    res.score_occupancy = min(s_occ, config.get("SCORE_OCCUPANCY_MAX", 10))
    score += res.score_occupancy
    
    # 4. Tests (Max 15)
    t = res.resistance_test_count
    if t >= 4: s_test = 15
    elif t == 3: s_test = 12
    elif t == 2: s_test = 8
    else: s_test = 0
    res.score_tests = min(s_test, config.get("SCORE_RESISTANCE_TESTS_MAX", 15))
    score += res.score_tests
    
    # 5. Higher Lows (Max 15)
    if res.last_confirmed_pivot_level > res.box_low + (0.15 * atr_15m):
        res.score_hl = config.get("SCORE_HIGHER_LOWS_MAX", 15)
        score += res.score_hl
        
    res.setup_score = score
