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

    # [V3] 15m BASE QUALITY ENGINE (0-100) — 7 Component Breakdown
    score_maturity: int = 0             # A. Max 15: Duration × quality interaction
    score_tightness: int = 0            # B. Max 20: Range/ATR (tighter = better)
    score_resistance_quality: int = 0   # C. Max 20: Ceiling std dev (sharper = better)
    score_repeated_tests: int = 0       # D. Max 15: Distinct touches (2=10, 3=13, 4+=15)
    score_compression: int = 0          # E. Max 15: Late-ATR / Early-ATR contraction
    score_higher_lows: int = 0          # F. Max 10: Rising lows = buyers getting aggressive
    score_support_integrity: int = 0    # G. Max 5:  Few floor touches = buyers well above support
    setup_score: int = 0                # Total 0–100

    # Structural insights exposed for downstream engines
    has_higher_lows: bool = False
    higher_lows_strength: float = 0.0   # late_low_min - early_low_min (in price terms)
    compression_ratio: float = 1.0      # late_range_avg / early_range_avg (<1 = contracting)
    base_rating_label: str = ""         # EXCEPTIONAL / SUPER / GOOD / WATCH / REJECT

    # Legacy field aliases (backwards compat with scanner/state code)
    score_resistance_def: int = 0
    score_tight_range: int = 0
    score_resistance_tests: int = 0
    score_compression_vcp: int = 0
    score_prior_bullish: int = 0
    score_clean_action: int = 0
    score_liquidity: int = 0
    score_duration: int = 0
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
            "base_rating_label": self.base_rating_label,
            "has_higher_lows": self.has_higher_lows,
            "compression_ratio": round(self.compression_ratio, 3),
            "score_breakdown": {
                "maturity": self.score_maturity,
                "tightness": self.score_tightness,
                "resistance_quality": self.score_resistance_quality,
                "repeated_tests": self.score_repeated_tests,
                "compression": self.score_compression,
                "higher_lows": self.score_higher_lows,
                "support_integrity": self.score_support_integrity,
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
    """Counts distinct resistance touches and detects higher-low structure."""
    # Touch tolerance: max(0.15% of price, 0.08× 15m ATR) — ATR-normalized across all price ranges
    tol_pct = config.get("RESISTANCE_TEST_TOL_PCT", 0.0015) * res.box_high
    tol_atr = config.get("RESISTANCE_TEST_TOL_ATR", 0.08) * atr_15m
    tol = max(tol_pct, tol_atr)
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


def _compute_scores(window_df: pd.DataFrame, full_df: pd.DataFrame, atr_15m: float, res: ConsolidationResult, config: Dict[str, Any]):
    """
    [V3] 15M BASE QUALITY ENGINE — 7-Component 0-100 Consolidation Quality Score:
      A. Maturity (15 pts):           Duration × quality interaction
      B. Tightness (20 pts):          Range/ATR (≤0.75 = exceptional coil)
      C. Resistance Quality (20 pts): Ceiling std dev (sharper = better)
      D. Repeated Tests (15 pts):     Distinct touches (2=10, 3=13, 4+=15)
      E. Compression/VCP (15 pts):    Late-ATR / Early-ATR (contracting = better)
      F. Higher Lows (10 pts):        Rising lows = buyers getting aggressive
      G. Support Integrity (5 pts):   Low % of bars touching floor = buyers well above support
    """
    n = len(window_df)
    score = 0

    # ── A. MATURITY (15 pts) — Duration × Quality Interaction ────────────────
    if n >= 12:
        s_mat = 15
    elif n >= 10:
        s_mat = 14
    elif n >= 8:
        s_mat = 12
    elif n >= 6:
        s_mat = 9
    elif n >= 4:
        s_mat = 6
    else:
        s_mat = 0
    # Interaction: if base is wide (tightness score < 8 threshold), cap maturity at 10
    tightness_cap = config.get("MATURITY_TIGHTNESS_THRESHOLD", 8)
    if res.box_width_atr > 1.25 and s_mat > 10:
        s_mat = 10  # Longer sloppy base = no extra bonus
    res.score_maturity = min(s_mat, config.get("SCORE_MATURITY_MAX", 15))
    score += res.score_maturity

    # ── B. TIGHTNESS (20 pts) — Range/ATR ────────────────────────────────────
    w = res.box_width_atr
    if w <= 0.75:
        s_tight = 20
    elif w <= 1.00:
        s_tight = 17
    elif w <= 1.25:
        s_tight = 13
    elif w <= 1.50:
        s_tight = 8
    else:
        s_tight = 0   # Fails hard gate in detect_15m_consolidation already
    res.score_tightness = min(s_tight, config.get("SCORE_TIGHTNESS_MAX", 20))
    score += res.score_tightness

    # ── C. RESISTANCE QUALITY (20 pts) — Ceiling Precision ───────────────────
    highs = window_df["High"].astype(float)
    top_highs = highs[highs >= res.box_high - (0.15 * atr_15m)]
    std_top = float(top_highs.std()) if len(top_highs) >= 2 else 0.0
    std_pct = std_top / res.box_high if res.box_high > 0 else 0.0
    if len(top_highs) >= 2 and std_pct <= 0.0010:
        s_rq = 20
    elif len(top_highs) >= 2 and std_pct <= 0.0020:
        s_rq = 16
    elif len(top_highs) >= 2 and std_pct <= 0.0035:
        s_rq = 12
    elif len(top_highs) >= 1:
        s_rq = 8
    else:
        s_rq = 4
    res.score_resistance_quality = min(s_rq, config.get("SCORE_RESISTANCE_QUALITY_MAX", 20))
    score += res.score_resistance_quality

    # ── D. REPEATED TESTS (15 pts) — Distinct Touches ────────────────────────
    t = res.resistance_test_count
    if t >= 4:
        s_tests = 15
    elif t == 3:
        s_tests = 13
    elif t == 2:
        s_tests = 10
    elif t == 1:
        s_tests = 3
    else:
        s_tests = 0
    res.score_repeated_tests = min(s_tests, config.get("SCORE_REPEATED_TESTS_MAX", 15))
    score += res.score_repeated_tests

    # ── E. COMPRESSION / VCP (15 pts) — Volatility Contracting ───────────────
    s_comp = 8  # Neutral: not enough bars to determine
    if n >= 4:
        half = n // 2
        early_ranges = (window_df["High"].iloc[:half] - window_df["Low"].iloc[:half]).values.astype(float)
        late_ranges  = (window_df["High"].iloc[half:] - window_df["Low"].iloc[half:]).values.astype(float)
        mean_early = float(np.mean(early_ranges)) if len(early_ranges) > 0 else 0.0
        mean_late  = float(np.mean(late_ranges))  if len(late_ranges) > 0 else 0.0
        if mean_early > 0:
            comp_ratio = mean_late / mean_early
            res.compression_ratio = round(comp_ratio, 3)
            if comp_ratio <= 0.60:
                s_comp = 15
            elif comp_ratio <= 0.75:
                s_comp = 12
            elif comp_ratio <= 0.90:
                s_comp = 8
            elif comp_ratio <= 1.00:
                s_comp = 4
            else:
                s_comp = 0  # Expanding volatility = bad
    res.score_compression = min(s_comp, config.get("SCORE_COMPRESSION_MAX", 15))
    score += res.score_compression

    # ── F. HIGHER LOWS (10 pts) — Rising Lows = Buyers Getting Aggressive ────
    s_hl = 0
    if n >= 4:
        half = n // 2
        early_lows = window_df["Low"].iloc[:half].astype(float)
        late_lows  = window_df["Low"].iloc[half:].astype(float)
        early_low_min = float(early_lows.min())
        late_low_min  = float(late_lows.min())
        hl_rise = late_low_min - early_low_min
        res.higher_lows_strength = round(hl_rise, 2)
        min_strong_rise = config.get("HIGHER_LOWS_MIN_RISE_ATR", 0.15) * atr_15m
        if hl_rise >= min_strong_rise:
            s_hl = 10
            res.has_higher_lows = True
        elif hl_rise >= 0:
            s_hl = 7
            res.has_higher_lows = True
        elif hl_rise >= -(0.10 * atr_15m):
            s_hl = 4  # Approximately flat
        else:
            s_hl = 0  # Lower lows = weakness inside base
    res.score_higher_lows = min(s_hl, config.get("SCORE_HIGHER_LOWS_MAX", 10))
    score += res.score_higher_lows

    # ── G. SUPPORT INTEGRITY (5 pts) — Buyers Well Above the Floor ───────────
    s_si = 3  # Neutral
    if n >= 4 and atr_15m > 0:
        support_zone = res.box_low + config.get("SUPPORT_ZONE_ATR_MULT", 0.20) * atr_15m
        lows = window_df["Low"].astype(float)
        pct_touching = float((lows <= support_zone).mean())
        clean_floor_pct = config.get("SUPPORT_INTEGRITY_LOW_PCT", 0.20)
        if pct_touching < clean_floor_pct:
            s_si = 5   # Buyers consistently well above support
        elif pct_touching < 0.40:
            s_si = 3
        else:
            s_si = 1   # Floor frequently visited = weaker demand
    res.score_support_integrity = min(s_si, config.get("SCORE_SUPPORT_INTEGRITY_MAX", 5))
    score += res.score_support_integrity

    # ── TOTAL + TIER LABEL ────────────────────────────────────────────────────
    res.setup_score = min(score, 100)

    # Populate legacy aliases for backward compatibility
    res.score_resistance_def  = res.score_resistance_quality
    res.score_tight_range     = res.score_tightness
    res.score_resistance_tests = res.score_repeated_tests
    res.score_compression_vcp = res.score_compression
    res.score_hl              = res.score_higher_lows

    if res.setup_score >= 90:
        res.base_rating_label = "EXCEPTIONAL"
    elif res.setup_score >= 80:
        res.base_rating_label = "SUPER"
    elif res.setup_score >= 70:
        res.base_rating_label = "GOOD"
    elif res.setup_score >= 60:
        res.base_rating_label = "WATCH"
    else:
        res.base_rating_label = "REJECT"


