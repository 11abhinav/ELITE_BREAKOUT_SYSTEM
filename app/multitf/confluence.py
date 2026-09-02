# =====================================================================================
# app/multitf/confluence.py
# MULTI_TF V3 — Confluence Bridge (Legacy Compatibility Layer)
#
# This module now acts as a bridge:
#   - The V3 primary flow uses BreakoutStrengthEngine + classify_alert_severity()
#     directly in scanner.py
#   - evaluate_breakout_confluence() is preserved here for test backwards-compat
#     and as a final sanity gate (base_score >= MIN_SETUP_SCORE check)
#
# V3 primary alert classification → see multitf/breakout_strength.py
# V3 alert message building       → see multitf/alert_builder.py
# =====================================================================================

import logging
from dataclasses import dataclass
from typing import Dict, Any

from multitf.consolidation import ConsolidationResult
from multitf.pressure import PressureResult

logger = logging.getLogger("multitf.confluence")


@dataclass
class ConfluenceResult:
    """Final gate result for a confirmed breakout (V3 bridge)."""
    is_approved: bool = False

    # Legacy scoring fields (kept for backwards-compat with tests)
    score_structure: int = 0
    score_momentum: int = 0
    score_volume: int = 0
    score_context: int = 0
    total_score: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_approved": self.is_approved,
            "total_score": self.total_score,
            "components": {
                "structure": self.score_structure,
                "momentum": self.score_momentum,
                "volume": self.score_volume,
                "context": self.score_context
            }
        }


def evaluate_breakout_confluence(
    consolidation: ConsolidationResult,
    pressure: PressureResult,
    ctx_1h: Dict[str, Any],
    ctx_30m: Dict[str, Any],
    market_ctx: Dict[str, Any],
    config: Dict[str, Any]
) -> ConfluenceResult:
    """
    [V3 Bridge] Legacy confluence gate — still used by tests and as a fallback pre-filter.

    In the V3 live scanner flow, this is called first to confirm the base quality
    meets minimum gates. Full severity classification is then delegated to
    classify_alert_severity() in breakout_strength.py.

    Weights:
      Structure (40%) + Momentum (25%) + Volume (15%) + Context (20%)
    """
    res = ConfluenceResult()
    if not pressure.is_confirmed:
        return res

    score = 0

    # 1. Structure (from 15m Consolidation Base) — Max 40
    struct_max = config.get("CONFLUENCE_STRUCTURE_MAX", 40)
    s_struct = int((consolidation.setup_score / 100.0) * struct_max)
    res.score_structure = min(s_struct, struct_max)
    score += res.score_structure

    # 2. Momentum (from 5m Pressure) — Max 25
    mom_max = config.get("CONFLUENCE_MOMENTUM_MAX", 25)
    s_mom = min(pressure.momentum_score, mom_max)
    res.score_momentum = s_mom
    score += res.score_momentum

    # 3. Volume Confirmation — Max 15
    vol_max = config.get("CONFLUENCE_VOLUME_MAX", 15)
    vr = pressure.volume_ratio
    if vr >= 2.0:
        s_vol = 15
    elif vr >= 1.5:
        s_vol = 12
    elif vr >= 1.25:
        s_vol = 9
    elif vr >= 1.15:
        s_vol = 6
    else:
        s_vol = 3
    res.score_volume = min(s_vol, vol_max)
    score += res.score_volume

    # 4. Context Alignment (1H + 30m + Regime) — Max 20 (Soft Context Gate)
    ctx_max = config.get("CONFLUENCE_CONTEXT_MAX", 20)
    c_1h  = ctx_1h.get("score", 0)      # -10 to +10
    c_30m = ctx_30m.get("score", 0)     # 0 to 10
    c_mkt = market_ctx.get("score", 0)  # -10 to +10

    raw_ctx = c_1h + c_30m + c_mkt
    if raw_ctx <= 0:
        s_ctx = 0
    else:
        s_ctx = int((raw_ctx / 30.0) * ctx_max)

    res.score_context = min(s_ctx, ctx_max)
    score += res.score_context

    # Final Confluence Score
    res.total_score = min(score, 100)

    # Multi-component approval gates
    min_struct = config.get("MIN_STRUCTURE_CONFLUENCE", 25)
    min_mom    = config.get("MIN_MOMENTUM_CONFLUENCE", 15)
    min_ctx    = config.get("MIN_CONTEXT_CONFLUENCE", 0)
    min_total  = config.get("MIN_TOTAL_CONFLUENCE", 65)

    struct_pass = res.score_structure >= min_struct
    mom_pass    = (res.score_momentum + res.score_volume) >= min_mom
    ctx_pass    = res.score_context >= min_ctx
    total_pass  = res.total_score >= min_total

    # [SOFT MARKET REGIME SHIELD]: Suppress marginal breakouts during broad market waterfall
    mkt_status = str(market_ctx.get("status", "NORMAL")).upper()
    is_severe_bear = mkt_status in ("BEAR", "STRONG_BEAR", "CRASH", "WATERFALL")
    if is_severe_bear:
        # Require strong relative-strength leader (score >= 80 and RVOL >= 1.5×)
        bear_min_score = config.get("BEAR_MIN_TOTAL_SCORE", 80)
        bear_min_rvol  = config.get("BEAR_MIN_RVOL", 1.50)
        if res.total_score < bear_min_score or pressure.volume_ratio < bear_min_rvol:
            total_pass = False

    if struct_pass and mom_pass and ctx_pass and total_pass:
        res.is_approved = True

    return res
