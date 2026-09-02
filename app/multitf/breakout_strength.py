# =====================================================================================
# app/multitf/breakout_strength.py
# MULTI_TF V3 — 5m Breakout Strength Engine
#
# Responsibility: Evaluates HOW POWERFUL a confirmed 5m breakout is.
#   This is a completely separate engine from the 15m Base Quality Engine.
#
# Score: 0-100 across 7 components:
#   A. Volume Expansion / RVOL       (30 pts)
#   B. Volume Acceleration           (10 pts)  vs previous 5m bar
#   C. Breakout Magnitude            (15 pts)  (close - resistance) / 5m ATR
#   D. Candle Quality                (15 pts)  close position + range expansion
#   E. Breakout Velocity             (10 pts)  ATR/min
#   F. Resistance Penetration        (10 pts)  % above resistance
#   G. Market-Relative Strength      (10 pts)  stock vs NIFTY at same 5m bar
#
# Breakout Tier Labels:
#   90-100  → EXPLOSIVE   🚀
#   80-89   → VERY STRONG 🔥
#   70-79   → STRONG      🟢
#   60-69   → NORMAL      🟡
#   <60     → WEAK (DB log only — no push notification)
# =====================================================================================

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, Optional, List

import pandas as pd
import numpy as np

logger = logging.getLogger("multitf.breakout_strength")


@dataclass
class BreakoutStrengthResult:
    """Full breakdown of 5m Breakout Strength (0-100)."""
    # Component scores
    score_rvol: int = 0             # A. Volume Expansion (30 pts)
    score_vol_accel: int = 0        # B. Volume Acceleration (10 pts)
    score_magnitude: int = 0        # C. Breakout Magnitude (15 pts)
    score_candle_quality: int = 0   # D. Candle Quality (15 pts)
    score_velocity: int = 0         # E. Breakout Velocity (10 pts)
    score_penetration: int = 0      # F. Resistance Penetration (10 pts)
    score_market_rs: int = 0        # G. Market-Relative Strength (10 pts)
    breakout_score: int = 0         # Total 0–100

    # Qualitative labels
    rvol_label: str = ""            # EXCEPTIONAL / VERY_STRONG / STRONG / CONFIRMED / NORMAL / WEAK
    velocity_label: str = ""        # EXPLOSIVE / VERY_FAST / FAST / NORMAL
    breakout_rating_label: str = "" # EXPLOSIVE / VERY_STRONG / STRONG / NORMAL / WEAK

    # Raw computed metrics (exposed for alert builder)
    volume_ratio: float = 0.0       # RVOL (time-of-day normalized)
    volume_acceleration: float = 0.0  # current_vol / prev_5m_vol
    current_5m_volume: float = 0.0
    expected_volume: float = 0.0
    prev_5m_volume: float = 0.0
    penetration_atr: float = 0.0    # (close - resistance) / 5m ATR
    penetration_pct: float = 0.0    # (close - resistance) / resistance
    velocity_atr_per_min: float = 0.0  # ATR/min
    close_position: float = 0.0
    range_ratio: float = 0.0
    checklist: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "breakout_score": self.breakout_score,
            "breakout_rating_label": self.breakout_rating_label,
            "rvol": round(self.volume_ratio, 2),
            "rvol_label": self.rvol_label,
            "velocity_label": self.velocity_label,
            "volume_acceleration": round(self.volume_acceleration, 2),
            "current_5m_volume": int(self.current_5m_volume),
            "expected_volume": int(self.expected_volume),
            "prev_5m_volume": int(self.prev_5m_volume),
            "penetration_atr": round(self.penetration_atr, 3),
            "penetration_pct": round(self.penetration_pct * 100, 3),
            "velocity_atr_per_min": round(self.velocity_atr_per_min, 4),
            "close_position": round(self.close_position, 3),
            "range_ratio": round(self.range_ratio, 2),
            "score_breakdown": {
                "rvol": self.score_rvol,
                "vol_accel": self.score_vol_accel,
                "magnitude": self.score_magnitude,
                "candle_quality": self.score_candle_quality,
                "velocity": self.score_velocity,
                "penetration": self.score_penetration,
                "market_rs": self.score_market_rs,
            }
        }


def compute_breakout_strength(
    pressure_result,
    consolidation_result,
    df_5m_closed: pd.DataFrame,
    nifty_5m: Optional[pd.DataFrame],
    ist_now: datetime,
    config: Dict[str, Any]
) -> BreakoutStrengthResult:
    """
    Computes the 5m Breakout Strength Score (0-100) for a confirmed breakout.

    Args:
        pressure_result: PressureResult from pressure.py (already confirmed)
        consolidation_result: ConsolidationResult from consolidation.py
        df_5m_closed: DataFrame of closed 5m bars
        nifty_5m: Optional NIFTY 5m data for market-RS computation
        ist_now: Current IST timestamp
        config: MULTI_TF_V2_CONFIG dict

    Returns:
        BreakoutStrengthResult with full score breakdown
    """
    res = BreakoutStrengthResult()
    if df_5m_closed is None or df_5m_closed.empty:
        return res

    last = df_5m_closed.iloc[-1]
    c = float(last["Close"])
    o = float(last["Open"])
    h = float(last["High"])
    l = float(last["Low"])
    v = float(last["Volume"])
    box_high = consolidation_result.box_high
    atr_5m = pressure_result.distance_to_box_high  # use distance proxy if no atr field

    # Resolve 5m ATR from range median
    ranges = df_5m_closed["High"] - df_5m_closed["Low"]
    median_range = float(ranges.median()) if len(ranges) > 3 else max(h - l, 0.01)
    atr_5m_resolved = median_range if median_range > 0 else 0.01

    candle_range = h - l
    close_pos = (c - l) / candle_range if candle_range > 0 else 0.0
    range_ratio = candle_range / median_range if median_range > 0 else 1.0
    res.close_position = close_pos
    res.range_ratio = range_ratio
    res.current_5m_volume = v

    # ── A. VOLUME EXPANSION / RVOL (30 pts) ──────────────────────────────────
    vr = pressure_result.volume_ratio
    res.volume_ratio = vr
    expected_vol = pressure_result.__dict__.get("expected_volume", v / vr if vr > 0 else v)
    res.expected_volume = expected_vol

    if vr >= config.get("RVOL_EXCEPTIONAL", 3.0):
        s_rvol = 30
        res.rvol_label = "EXCEPTIONAL"
    elif vr >= config.get("RVOL_VERY_STRONG", 2.0):
        s_rvol = 27
        res.rvol_label = "VERY_STRONG"
    elif vr >= config.get("RVOL_STRONG", 1.5):
        s_rvol = 22
        res.rvol_label = "STRONG"
    elif vr >= config.get("RVOL_CONFIRMED", 1.25):
        s_rvol = 15
        res.rvol_label = "CONFIRMED"
    elif vr >= config.get("RVOL_NORMAL", 1.0):
        s_rvol = 8
        res.rvol_label = "NORMAL"
    else:
        s_rvol = 0
        res.rvol_label = "WEAK"
    res.score_rvol = min(s_rvol, config.get("SCORE_RVOL_MAX", 30))

    # ── B. VOLUME ACCELERATION (10 pts) — vs previous 5m bar ─────────────────
    prev_vol = 0.0
    if len(df_5m_closed) >= 2:
        prev_vol = float(df_5m_closed.iloc[-2]["Volume"])
    res.prev_5m_volume = prev_vol

    if prev_vol > 0:
        vol_accel = v / prev_vol
    else:
        vol_accel = 1.0
    res.volume_acceleration = round(vol_accel, 2)

    if vol_accel >= 3.0:
        s_va = 10
    elif vol_accel >= 2.0:
        s_va = 8
    elif vol_accel >= 1.5:
        s_va = 6
    elif vol_accel >= 1.0:
        s_va = 3
    else:
        s_va = 0
    res.score_vol_accel = min(s_va, config.get("SCORE_VOL_ACCEL_MAX", 10))

    # ── C. BREAKOUT MAGNITUDE (15 pts) — (close - res) / 5m ATR ──────────────
    penetration_price = max(c - box_high, 0.0)
    penetration_atr = penetration_price / atr_5m_resolved
    penetration_pct = penetration_price / box_high if box_high > 0 else 0.0
    res.penetration_atr = round(penetration_atr, 3)
    res.penetration_pct = round(penetration_pct, 4)

    ideal_min = config.get("MAGNITUDE_IDEAL_MIN_ATR", 0.25)
    ideal_max = config.get("MAGNITUDE_IDEAL_MAX_ATR", 0.70)

    if penetration_atr >= ideal_max:
        s_mag = 10   # Strong but approaching extension territory
    elif penetration_atr >= ideal_min:
        # Linear scale 8→15 within ideal zone
        frac = (penetration_atr - ideal_min) / (ideal_max - ideal_min)
        s_mag = int(8 + frac * 7)
    elif penetration_atr >= 0.10:
        s_mag = 6
    else:
        s_mag = 3   # Barely through
    res.score_magnitude = min(s_mag, config.get("SCORE_MAGNITUDE_MAX", 15))

    # ── D. CANDLE QUALITY (15 pts) — Close Position + Range Expansion ─────────
    # Close position component (8 pts)
    if close_pos >= 0.90:
        s_cp = 8
    elif close_pos >= 0.75:
        s_cp = 6
    elif close_pos >= 0.60:
        s_cp = 4
    else:
        s_cp = 1

    # Range expansion component (7 pts)
    if range_ratio >= 2.0:
        s_rr = 7
    elif range_ratio >= 1.5:
        s_rr = 5
    elif range_ratio >= 1.25:
        s_rr = 3
    else:
        s_rr = 1

    res.score_candle_quality = min(s_cp + s_rr, config.get("SCORE_CANDLE_QUALITY_MAX", 15))

    # ── E. BREAKOUT VELOCITY (10 pts) — ATR/min ───────────────────────────────
    # Estimate: use the breakout candle's range as proxy for price move over 5 mins
    # Velocity = penetration_atr / time_elapsed_in_minutes
    # For a completed 5m bar, use 5.0 minutes as elapsed time
    time_elapsed_min = 5.0
    velocity_atr_min = penetration_atr / time_elapsed_min if time_elapsed_min > 0 else 0.0
    res.velocity_atr_per_min = round(velocity_atr_min, 5)

    explosive_thresh = config.get("VELOCITY_EXPLOSIVE_ATR_MIN", 0.15)
    fast_thresh = config.get("VELOCITY_VERY_FAST_ATR_MIN", 0.08)
    normal_thresh = config.get("VELOCITY_FAST_ATR_MIN", 0.04)

    if velocity_atr_min >= explosive_thresh:
        s_vel = 10
        res.velocity_label = "EXPLOSIVE"
    elif velocity_atr_min >= fast_thresh:
        s_vel = 7
        res.velocity_label = "VERY_FAST"
    elif velocity_atr_min >= normal_thresh:
        s_vel = 4
        res.velocity_label = "FAST"
    else:
        s_vel = 2
        res.velocity_label = "NORMAL"
    res.score_velocity = min(s_vel, config.get("SCORE_VELOCITY_MAX", 10))

    # ── F. RESISTANCE PENETRATION (10 pts) — % Above Resistance ──────────────
    pct_above = penetration_pct * 100  # in percent
    if 0.60 <= pct_above <= 1.20:
        s_pen = 10
    elif 0.30 <= pct_above < 0.60:
        s_pen = 7
    elif 0.00 < pct_above < 0.30:
        s_pen = 3
    else:
        s_pen = 6   # > 1.2% likely overextended
    res.score_penetration = min(s_pen, config.get("SCORE_PENETRATION_MAX", 10))

    # ── G. MARKET-RELATIVE STRENGTH (10 pts) — Stock vs NIFTY ────────────────
    neutral_pts = config.get("MARKET_RS_NEUTRAL", 5)
    s_mkt = neutral_pts  # Default: neutral if NIFTY data unavailable

    if nifty_5m is not None and not nifty_5m.empty and len(nifty_5m) >= 2:
        try:
            nifty_last_close = float(nifty_5m["Close"].iloc[-1])
            nifty_prev_close = float(nifty_5m["Close"].iloc[-2])
            nifty_chg_pct = (nifty_last_close - nifty_prev_close) / nifty_prev_close if nifty_prev_close > 0 else 0.0

            prev_stock_close = float(df_5m_closed.iloc[-2]["Close"]) if len(df_5m_closed) >= 2 else c
            stock_chg_pct = (c - prev_stock_close) / prev_stock_close if prev_stock_close > 0 else 0.0

            rs_diff = stock_chg_pct - nifty_chg_pct
            strong_lead = config.get("MARKET_RS_STRONG_LEAD", 0.005)

            if rs_diff >= strong_lead:
                s_mkt = 10
            elif rs_diff >= 0:
                s_mkt = 6
            elif rs_diff >= -0.003:
                s_mkt = 3
            else:
                s_mkt = 0
        except Exception as ex:
            logger.debug("[breakout_strength] NIFTY RS calc failed: %s", ex)
            s_mkt = neutral_pts

    res.score_market_rs = min(s_mkt, config.get("SCORE_MARKET_RS_MAX", 10))

    # ── TOTAL SCORE + TIER LABEL ──────────────────────────────────────────────
    total = (res.score_rvol + res.score_vol_accel + res.score_magnitude +
             res.score_candle_quality + res.score_velocity +
             res.score_penetration + res.score_market_rs)
    res.breakout_score = min(total, 100)

    min_breakout = config.get("MIN_BREAKOUT_SCORE", 70)
    if res.breakout_score >= config.get("EXPLOSIVE_BREAKOUT_SCORE", 90):
        res.breakout_rating_label = "EXPLOSIVE"
    elif res.breakout_score >= config.get("STRONG_BREAKOUT_SCORE", 80):
        res.breakout_rating_label = "VERY_STRONG"
    elif res.breakout_score >= min_breakout:
        res.breakout_rating_label = "STRONG"
    elif res.breakout_score >= 60:
        res.breakout_rating_label = "NORMAL"
    else:
        res.breakout_rating_label = "WEAK"

    # Build checklist for alert message
    res.checklist = _build_checklist(res, consolidation_result, config)
    return res


def classify_alert_severity(
    consolidation_result,
    breakout_result: BreakoutStrengthResult,
    config: Dict[str, Any],
    market_status: str = "NORMAL"
) -> str:
    """
    Classifies the final alert severity tier based on both engine scores.

    Returns:
        'A_PLUS'     — 💎 Exceptional base + explosive breakout
        'EXPLOSIVE'  — 🚀 Very high-quality base + very strong breakout
        'SUPER'      — 🔥 Both engines strong
        'GOOD'       — 🟢 Both engines meet production threshold
        'WEAK'       — ⚠️ DB log only, no push
    """
    base  = consolidation_result.setup_score
    brk   = breakout_result.breakout_score
    vr    = breakout_result.volume_ratio
    hl    = consolidation_result.has_higher_lows
    tests = consolidation_result.resistance_test_count

    # Soft market regime shield: require stronger evidence in bear/crash
    mkt_upper = market_status.upper()
    is_severe_bear = mkt_upper in ("BEAR", "STRONG_BEAR", "CRASH", "WATERFALL")
    bear_min = config.get("BEAR_MIN_TOTAL_SCORE", 80)
    bear_rvol = config.get("BEAR_MIN_RVOL", 1.50)
    if is_severe_bear and (base < bear_min or vr < bear_rvol):
        return "WEAK"

    # A+ SETUP: max quality on both engines
    if (base >= config.get("SEVERITY_APLUS_BASE", 90)
            and brk >= config.get("SEVERITY_APLUS_BREAKOUT", 90)
            and vr >= config.get("SEVERITY_APLUS_RVOL", 2.0)
            and hl and tests >= 3):
        return "A_PLUS"

    # EXPLOSIVE BREAKOUT
    if (base >= config.get("SEVERITY_EXPLOSIVE_BASE", 85)
            and brk >= config.get("SEVERITY_EXPLOSIVE_BREAKOUT", 88)
            and vr >= config.get("SEVERITY_EXPLOSIVE_RVOL", 2.0)):
        return "EXPLOSIVE"

    # SUPER BREAKOUT
    if (base >= config.get("SEVERITY_SUPER_BASE", 80)
            and brk >= config.get("SEVERITY_SUPER_BREAKOUT", 80)):
        return "SUPER"

    # GOOD BREAKOUT
    if (base >= config.get("SEVERITY_GOOD_BASE", 70)
            and brk >= config.get("SEVERITY_GOOD_BREAKOUT", 70)):
        return "GOOD"

    return "WEAK"


SEVERITY_EMOJI = {
    "A_PLUS":   "💎",
    "EXPLOSIVE": "🚀",
    "SUPER":    "🔥",
    "GOOD":     "🟢",
    "WEAK":     "⚠️"
}

SEVERITY_LABEL = {
    "A_PLUS":   "A+ SETUP",
    "EXPLOSIVE": "EXPLOSIVE BREAKOUT",
    "SUPER":    "SUPER BREAKOUT",
    "GOOD":     "GOOD BREAKOUT",
    "WEAK":     "WEAK (No Trade)"
}


def _build_checklist(res: BreakoutStrengthResult, cons, config: Dict[str, Any]) -> List[str]:
    """Builds the human-readable ✅/❌ checklist for alert messages."""
    checks = []
    min_setup = config.get("MIN_SETUP_SCORE", 70)
    min_brk   = config.get("MIN_BREAKOUT_SCORE", 70)

    tick = lambda cond: "✅" if cond else "❌"

    checks.append(f"{tick(cons.bars_count >= 6)} Mature base ({cons.bars_count} candles)")
    checks.append(f"{tick(cons.box_width_atr <= 1.25)} Tight range ({cons.box_width_atr:.2f}× ATR)")
    checks.append(f"{tick(cons.resistance_test_count >= 2)} {cons.resistance_test_count} resistance tests")
    checks.append(f"{tick(cons.has_higher_lows)} Higher lows {'confirmed ↑' if cons.has_higher_lows else 'absent'}")
    checks.append(f"{tick(cons.compression_ratio <= 0.90)} Volatility compression ({cons.compression_ratio:.2f})")
    checks.append(f"{tick(True)} 5m close above resistance")
    checks.append(f"{tick(res.volume_ratio >= 1.25)} RVOL {res.volume_ratio:.2f}× ({res.rvol_label})")
    checks.append(f"{tick(res.close_position >= 0.60)} Strong candle (close pos {res.close_position:.2f})")
    checks.append(f"{tick(res.velocity_label in ('FAST', 'VERY_FAST', 'EXPLOSIVE'))} Velocity: {res.velocity_label}")
    checks.append(f"{tick(res.score_rvol + res.score_vol_accel + res.score_magnitude >= 40)} Breakout strength {res.breakout_score}/100")

    return checks
