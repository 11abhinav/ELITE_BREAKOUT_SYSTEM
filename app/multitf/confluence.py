# =====================================================================================
# app/multitf/confluence.py
# MULTI_TF V2 — Confluence Engine
#
# Responsibility: Grades the final Confirmed Breakout signal by combining Structure (15m),
# Momentum (5m), Volume, and Context (1H/30m/Regime).
#
# Emits a final 0-100 score. If this score exceeds MIN_CONFLUENCE_SCORE, the setup is
# sent to OpportunityManager for execution.
# =====================================================================================

import logging
from dataclasses import dataclass
from typing import Dict, Any

from multitf.consolidation import ConsolidationResult
from multitf.pressure import PressureResult

logger = logging.getLogger("multitf.confluence")


@dataclass
class ConfluenceResult:
    """Final grade for a confirmed breakout."""
    is_approved: bool = False
    
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
    Called ONLY when pressure.is_confirmed is True.
    """
    res = ConfluenceResult()
    if not pressure.is_confirmed:
        return res
        
    score = 0
    
    # 1. Structure (from 15m Consolidation) — Max 35
    # The setup_score is 0-100, we scale it to max 35.
    struct_max = config.get("CONFLUENCE_STRUCTURE_MAX", 35)
    s_struct = int((consolidation.setup_score / 100.0) * struct_max)
    res.score_structure = min(s_struct, struct_max)
    score += res.score_structure
    
    # 2. Momentum (from 5m Pressure) — Max 30
    # The momentum_score from pressure is already 0-30 scaled.
    mom_max = config.get("CONFLUENCE_MOMENTUM_MAX", 30)
    s_mom = min(pressure.momentum_score, mom_max)
    res.score_momentum = s_mom
    score += res.score_momentum
    
    # 3. Volume Confirmation — Max 15
    vol_max = config.get("CONFLUENCE_VOLUME_MAX", 15)
    vr = pressure.volume_ratio
    if vr >= 2.5: s_vol = 15
    elif vr >= 2.0: s_vol = 12
    elif vr >= 1.5: s_vol = 8
    else: s_vol = 4
    res.score_volume = min(s_vol, vol_max)
    score += res.score_volume
    
    # 4. Context Alignment (1H + 30m + Regime) — Max 20
    ctx_max = config.get("CONFLUENCE_CONTEXT_MAX", 20)
    
    c_1h = ctx_1h.get("score", 0)       # -10 to +10
    c_30m = ctx_30m.get("score", 0)     # 0 to 10
    c_mkt = market_ctx.get("score", 0)  # -10 to +10
    
    raw_ctx = c_1h + c_30m + c_mkt
    
    # Scale: A raw score of 20+ yields max points. Negative raw score yields 0.
    if raw_ctx <= 0:
        s_ctx = 0
    else:
        s_ctx = int((raw_ctx / 30.0) * ctx_max)
        
    res.score_context = min(s_ctx, ctx_max)
    score += res.score_context
    
    # Final Evaluation
    res.total_score = score
    
    # Strict Mandatory Gates (Config-driven)
    struct_pass = res.score_structure >= config.get("MIN_STRUCTURE_CONFLUENCE", 15)
    mom_pass = res.score_momentum >= config.get("MIN_MOMENTUM_CONFLUENCE", 15)
    ctx_pass = res.score_context >= config.get("MIN_CONTEXT_CONFLUENCE", 10)
    total_pass = score >= config.get("MIN_TOTAL_CONFLUENCE", 60)
    
    if struct_pass and mom_pass and ctx_pass and total_pass:
        res.is_approved = True
        
    return res
