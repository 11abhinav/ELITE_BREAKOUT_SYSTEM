"""
app/accumulation/sl_target.py — Support Quality Ranking, Structural SL & 3-Tier Target Engine for ACCUMULATION_SCANNER_V1.
Enforces universal target ordering (target_1 >= breakout_level AND target_1 > entry_price AND target_1 < target_2 < target_3),
entry method alignment, initial tradability gate, and position sizing guidance.
"""

import math
import logging
from typing import Dict, Any, Optional, List, Tuple
from app.accumulation.config import (
    SL_CONFIG, TARGET_CONFIG, INITIAL_TRADABILITY, POSITION_SIZING_DEFAULTS, BREAKOUT_CONFIRMATION_BUFFER_PCT
)
from app.accumulation.contracts import SLTargetResult

logger = logging.getLogger(__name__)

class AccumulationSLTargetEngine:
    """Structural Stop Loss & 3-Tier Target Engine for ACCUMULATION_SCANNER_V1."""

    @staticmethod
    def select_highest_quality_support(entry_price: float, supports: List[Tuple[float, str, int]]) -> Tuple[float, str, int]:
        """
        Ranks valid structural supports below entry price by quality score.
        Returns (support_price, support_type, support_score).
        """
        valid_supports = [s for s in supports if s[0] is not None and s[0] < entry_price]
        if not valid_supports:
            # Fallback to ATR-based support
            return (round(entry_price * 0.95, 2), "ATR_FALLBACK", 20)

        # Sort by score descending, then proximity (highest support_price)
        valid_supports.sort(key=lambda s: (s[2], s[0]), reverse=True)
        return valid_supports[0]

    @staticmethod
    def compute_sl_and_targets(
        entry_zone_low: float,
        entry_zone_high: float,
        breakout_level: float,
        close_price: float,
        eff_atr: float,
        entry_method: str = "ZONE_MIDPOINT",
        supports: Optional[List[Tuple[float, str, int]]] = None,
        resistances: Optional[List[Tuple[float, str, int]]] = None,
        account_capital: float = 1000000.0,
        account_risk_pct: float = 1.0,
    ) -> SLTargetResult:
        """
        Calculates selected entry level, structural SL, 3-tier targets, and risk metrics.
        """
        preferred_entry = round((entry_zone_low + entry_zone_high) / 2.0, 2)
        entry_trigger_level = round(breakout_level * (1.0 + BREAKOUT_CONFIRMATION_BUFFER_PCT), 2) if entry_method == "BREAKOUT_CONFIRMATION" else preferred_entry

        if entry_method == "BREAKOUT_CONFIRMATION":
            entry_price = entry_trigger_level
        else:
            entry_price = preferred_entry

        if supports is None:
            supports = [(entry_zone_low, "ZONE_LOW", 50)]

        # 1. Structural Stop Loss Calculation
        sup_price, sup_type, sup_score = AccumulationSLTargetEngine.select_highest_quality_support(entry_price, supports)
        buf = SL_CONFIG["base_atr_buf"] * eff_atr
        raw_sl = round(sup_price - buf, 2)

        # Enforce MIN_STOP_DISTANCE_ATR safety floor (at least 0.80x ATR below entry)
        max_allowed_sl = round(entry_price - SL_CONFIG["min_stop_distance_atr"] * eff_atr, 2)
        stop_loss = min(raw_sl, max_allowed_sl)

        # Enforce MAX_SL_ATR cap (no more than 3.0x ATR from entry)
        min_allowed_sl = round(entry_price - SL_CONFIG["max_sl_atr"] * eff_atr, 2)
        stop_loss = max(stop_loss, min_allowed_sl)

        risk_amount = entry_price - stop_loss
        if risk_amount <= 0:
            return SLTargetResult(
                is_valid=False, entry_price=entry_price, stop_loss=stop_loss,
                target_1=0.0, target_2=0.0, target_3=0.0, risk_pct=0.0,
                rr_1=0.0, rr_2=0.0, rr_3=0.0, support_anchor_price=sup_price, support_anchor_type=sup_type,
                rejection_reason=f"INVALID_RISK_AMOUNT (Risk ₹{risk_amount:.2f} <= 0)"
            )

        risk_pct = round((risk_amount / entry_price) * 100.0, 2)

        # 2. 3-Tier Target Construction
        # Universal Invariant: target_1 >= breakout_level AND target_1 > entry_price AND target_1 < target_2 < target_3
        min_t1 = max(breakout_level, round(entry_price + 2.0 * risk_amount, 2))
        
        # Primary resistance search
        res_levels = [r[0] for r in (resistances or []) if r[0] is not None and r[0] > min_t1]
        
        t1 = min_t1
        if res_levels:
            res_levels.sort()
            t1 = max(min_t1, round(res_levels[0], 2))

        # Measured move projection for T3
        measured_move_dist = max(breakout_level - entry_zone_low, 2.0 * eff_atr)
        t3_candidate = round(entry_price + measured_move_dist * TARGET_CONFIG["measured_move_mult"], 2)

        # Target 2 midpoint / resistance
        t2_candidate = round(t1 + (t3_candidate - t1) * 0.50, 2)
        if len(res_levels) > 1:
            for r in res_levels[1:]:
                if t1 < r < t3_candidate:
                    t2_candidate = round(r, 2)
                    break

        # Ensure strict ordering: t1 < t2 < t3 with minimum spacing
        epsilon = max(0.05, 0.005 * entry_price)
        t2 = max(t2_candidate, round(t1 + epsilon, 2))
        t3 = max(t3_candidate, round(t2 + epsilon, 2))

        rr_1 = round((t1 - entry_price) / risk_amount, 2)
        rr_2 = round((t2 - entry_price) / risk_amount, 2)
        rr_3 = round((t3 - entry_price) / risk_amount, 2)

        # 3. Initial Tradability Gate Checks
        rejection_reasons = []
        if risk_pct > INITIAL_TRADABILITY["max_risk_pct"]:
            rejection_reasons.append(f"EXCESSIVE_RISK_PCT ({risk_pct:.2f}% > max {INITIAL_TRADABILITY['max_risk_pct']}%)")
        if rr_1 < INITIAL_TRADABILITY["min_rr_1"]:
            rejection_reasons.append(f"INSUFFICIENT_RR1 ({rr_1:.2f}x < min {INITIAL_TRADABILITY['min_rr_1']}x)")
        if t1 < breakout_level:
            rejection_reasons.append(f"TARGET_1_BELOW_BREAKOUT (T1 ₹{t1:.2f} < Breakout ₹{breakout_level:.2f})")

        is_valid = len(rejection_reasons) == 0
        rejection_reason_str = None if is_valid else "; ".join(rejection_reasons)

        return SLTargetResult(
            is_valid=is_valid,
            entry_price=entry_price,
            stop_loss=stop_loss,
            target_1=t1,
            target_2=t2,
            target_3=t3,
            risk_pct=risk_pct,
            rr_1=rr_1,
            rr_2=rr_2,
            rr_3=rr_3,
            support_anchor_price=sup_price,
            support_anchor_type=sup_type,
            rejection_reason=rejection_reason_str
        )

    @staticmethod
    def calculate_position_size(
        entry_price: float,
        stop_loss: float,
        account_capital: float = 1000000.0,
        account_risk_pct: float = 1.0
    ) -> Tuple[float, int, str]:
        """
        Calculates position sizing guidance based on account risk (1% default).
        Returns (suggested_capital, suggested_position_size, position_sizing_basis).
        """
        risk_per_share = entry_price - stop_loss
        if risk_per_share <= 0 or entry_price <= 0:
            return (0.0, 0, POSITION_SIZING_DEFAULTS["position_sizing_basis"])

        max_risk_amount = account_capital * (account_risk_pct / 100.0)
        qty = math.floor(max_risk_amount / risk_per_share)
        suggested_capital = round(qty * entry_price, 2)

        return (suggested_capital, qty, POSITION_SIZING_DEFAULTS["position_sizing_basis"])
