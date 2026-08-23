"""
app/accumulation_sl_target.py

Structural Stop Loss, Target Generation, and Exit Lifecycle Engine for ACCUMULATION_SCANNER_V1.
Completely isolated paper-trade lifecycle management.
"""

import math
import logging
from typing import Dict, Any, List, Optional
from accumulation_config import SL_TARGET_CONFIG

logger = logging.getLogger(__name__)


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        if val is None or (isinstance(val, float) and (math.isnan(val) or math.isinf(val))):
            return default
        return float(val)
    except Exception:
        return default


def compute_accumulation_sl_target(
    cmp: float,
    resistance: float,
    recent_swing_low: float,
    range_low: float,
    nearest_support: float,
    atr: float,
    high_52w: float,
    base_height: float,
    config: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Calculates structural entry zone, structural stop loss, 3-tier targets, risk-reward, and tradable status.
    """
    cfg = config or SL_TARGET_CONFIG
    atr_val = _safe_float(atr, cmp * 0.02)
    if atr_val <= 0:
        atr_val = cmp * 0.02

    res_val = _safe_float(resistance, cmp * 1.05)
    if res_val <= cmp:
        res_val = cmp * 1.05

    # 1. Entry Zone
    confirm_buffer = cfg.get("CONFIRMATION_BUFFER_PCT", 0.005)
    zone_range_pct = cfg.get("EARLY_ENTRY_ZONE_RANGE_PCT", 0.015)
    
    breakout_level = round(res_val * (1.0 + confirm_buffer), 2)
    entry_zone_low = round(cmp * (1.0 - zone_range_pct), 2)
    entry_zone_high = round(breakout_level, 2)
    entry_price = round((entry_zone_low + entry_zone_high) / 2.0, 2)

    # 2. Structural Stop Loss
    swing_low_val = _safe_float(recent_swing_low, cmp * 0.95)
    range_low_val = _safe_float(range_low, cmp * 0.95)
    support_val = _safe_float(nearest_support, cmp * 0.95)

    base_structural_support = max(swing_low_val, range_low_val, support_val)
    if base_structural_support >= cmp:
        base_structural_support = cmp * 0.95

    atr_buffer = cfg.get("ATR_SAFETY_BUFFER", 0.50) * atr_val
    stop_loss = round(max(0.01, base_structural_support - atr_buffer), 2)
    sl_reason = f"STRUCTURAL_SUPPORT ₹{base_structural_support:.2f} - {cfg.get('ATR_SAFETY_BUFFER', 0.50)}xATR (₹{atr_buffer:.2f}) = ₹{stop_loss:.2f}"

    # 3. 3-Tier Targets
    # Target 1: Immediate breakout / resistance extension
    target_1 = round(breakout_level * 1.05, 2)
    
    # Target 2: Next major swing high / 20D/200D resistance extension
    target_2 = round(max(target_1 * 1.06, _safe_float(high_52w, target_1 * 1.08)), 2)

    # Target 3: Measured Move (Entry + Base Height) or 52W High Extension
    m_move = entry_price + max(_safe_float(base_height, atr_val * 5), atr_val * 4)
    target_3 = round(max(target_2 * 1.06, m_move), 2)

    # 4. Risk / Reward Metrics
    risk_per_share = max(0.01, entry_price - stop_loss)
    risk_pct = round((risk_per_share / entry_price) * 100.0, 2)

    reward_1 = max(0.0, target_1 - entry_price)
    reward_2 = max(0.0, target_2 - entry_price)
    reward_3 = max(0.0, target_3 - entry_price)

    rr_1 = round(reward_1 / risk_per_share, 2)
    rr_2 = round(reward_2 / risk_per_share, 2)
    rr_3 = round(reward_3 / risk_per_share, 2)

    # 5. Tradable Gate Verification
    min_rr = cfg.get("MIN_INITIAL_RR", 2.0)
    tradable = True
    tradability_reason = "PASSED_MIN_RR"
    if rr_1 < min_rr:
        tradable = False
        tradability_reason = f"INSUFFICIENT_INITIAL_RR (RR_1 {rr_1:.2f} < {min_rr:.1f})"

    return {
        "cmp": cmp,
        "entry_price": entry_price,
        "entry_zone_low": entry_zone_low,
        "entry_zone_high": entry_zone_high,
        "breakout_level": breakout_level,
        "stop_loss": stop_loss,
        "sl_reason": sl_reason,
        "target_1": target_1,
        "target_2": target_2,
        "target_3": target_3,
        "risk_pct": risk_pct,
        "risk_per_share": round(risk_per_share, 2),
        "rr_1": rr_1,
        "rr_2": rr_2,
        "rr_3": rr_3,
        "tradable": tradable,
        "tradability_reason": tradability_reason,
        "time_stop_days": cfg.get("MAX_ACCUMULATION_HOLD_DAYS", 40),
        "invalidation_condition": "Close below SL OR accumulation structure breakdown OR RS failure"
    }


def evaluate_accumulation_exit(
    position: Dict[str, Any],
    current_market: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Evaluates paper position exit triggers.
    Returns: {"exit_signal": str, "reason": str, "should_exit": bool}
    """
    cmp = _safe_float(current_market.get("close"))
    sl = _safe_float(position.get("stop_loss"))
    t1 = _safe_float(position.get("target_1"))
    t2 = _safe_float(position.get("target_2"))
    t3 = _safe_float(position.get("target_3"))
    days_held = int(current_market.get("days_held", 0))
    max_days = int(position.get("time_stop_days", 40))

    accumulation_score = _safe_float(current_market.get("accumulation_score"), 100.0)
    initial_score = _safe_float(position.get("initial_accumulation_score"), 70.0)
    rs_nifty_20d = _safe_float(current_market.get("rs_nifty_20d"), 0.0)
    range_support = _safe_float(current_market.get("range_support"), sl)

    # 1. Hard Stop Loss
    if cmp <= sl and sl > 0:
        return {
            "exit_signal": "STOP_LOSS",
            "reason": f"Price ₹{cmp:.2f} <= Stop Loss ₹{sl:.2f}",
            "should_exit": True
        }

    # 2. Structural Support Breakdown
    if cmp < range_support and range_support > 0:
        return {
            "exit_signal": "STRUCTURE_INVALIDATED",
            "reason": f"Price ₹{cmp:.2f} broke accumulation range support ₹{range_support:.2f}",
            "should_exit": True
        }

    # 3. Accumulation Thesis Collapse
    if accumulation_score < (initial_score * 0.70):
        return {
            "exit_signal": "ACCUMULATION_INVALIDATED",
            "reason": f"Accumulation score collapsed ({accumulation_score:.1f} < 70% of initial {initial_score:.1f})",
            "should_exit": True
        }

    # 4. Relative Strength Breakdown
    if rs_nifty_20d < -8.0:
        return {
            "exit_signal": "RELATIVE_STRENGTH_FAILURE",
            "reason": f"Relative strength vs Nifty collapsed ({rs_nifty_20d:.1f}% < -8.0%)",
            "should_exit": True
        }

    # 5. Targets
    if t3 > 0 and cmp >= t3:
        return {
            "exit_signal": "TARGET_3",
            "reason": f"Target 3 reached (₹{cmp:.2f} >= ₹{t3:.2f})",
            "should_exit": True
        }
    if t2 > 0 and cmp >= t2:
        return {
            "exit_signal": "TARGET_2",
            "reason": f"Target 2 reached (₹{cmp:.2f} >= ₹{t2:.2f})",
            "should_exit": True
        }
    if t1 > 0 and cmp >= t1:
        return {
            "exit_signal": "TARGET_1",
            "reason": f"Target 1 reached (₹{cmp:.2f} >= ₹{t1:.2f})",
            "should_exit": False  # Partial / Trailing
        }

    # 6. Time Stop
    if days_held >= max_days:
        if cmp < position.get("breakout_level", cmp * 1.05):
            return {
                "exit_signal": "TIME_STOP",
                "reason": f"Held {days_held} trading days without breakout (max {max_days} days)",
                "should_exit": True
            }

    return {
        "exit_signal": "HOLD",
        "reason": "Thesis intact",
        "should_exit": False
    }
