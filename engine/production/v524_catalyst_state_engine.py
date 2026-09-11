"""
V5.24 Deterministic Catalyst State Engine — Production Implementation.

Separates live intraday Gem routing (<= 60m TTL) from after-market completed-candle
structural revalidation. Implements a 5-state deterministic state machine:
  1. FRESH: Live morning breakout (<= 60m old, high velocity).
  2. SURVIVED: EOD structural confirmation (tight base, CLV >= 0.68, runway intact).
  3. COOLING: Neutral drift / volume dried up without structural failure.
  4. EXHAUSTED: Climax extension (> 3.5R runup, upper wick fade > 45%).
  5. INVALIDATED: Price broke below morning breakout structure or VWAP.

Consuming Engines:
  - engine/production/v520_gem_router_engine.py (Routing & Sizing)
  - app/trade_ranking_engine.py (Hierarchical Ranking & Policy Attribution)
"""

import math
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Any


class CatalystState(str, Enum):
    FRESH = "FRESH"                 # Intraday active breakout (<= 60m TTL, high momentum)
    SURVIVED = "SURVIVED"           # EOD structural confirmation (tight base, CLV >= 0.68, runway intact)
    COOLING = "COOLING"             # Neutral drift / volume dried up without structural failure
    EXHAUSTED = "EXHAUSTED"         # Climax extension (> 3.5R runup, upper wick fade > 45%)
    INVALIDATED = "INVALIDATED"     # Price broke below morning breakout structure or VWAP
    NO_CATALYST = "NO_CATALYST"     # Organic non-Gem candidate


@dataclass
class CatalystEvaluationResult:
    symbol: str
    state: CatalystState
    gem_age_minutes: float
    clv: float
    extension_r: float
    volume_retention: float
    has_structural_runway: bool
    is_live_intraday_eligible: bool
    is_aftermarket_context_eligible: bool
    risk_allocation_r: float
    priority_tier: int
    veto_reason: Optional[str] = None
    policy_description: str = ""


class DeterministicCatalystStateEngine:
    """
    Deterministic EOD & Intraday Catalyst State Evaluator.
    Eliminates semantic staleness and ambiguous boundary gaps.
    """

    @staticmethod
    def evaluate_candidate(
        candidate: Dict[str, Any],
        evaluation_time_hours: float = 16.0,  # 16:00 IST
        is_intraday_scheduler: bool = False
    ) -> CatalystEvaluationResult:
        symbol = str(candidate.get("symbol", candidate.get("candidate_id", "UNKNOWN")))
        had_morning_gem = bool(candidate.get("had_morning_gem", candidate.get("is_gem", False)))

        if not had_morning_gem:
            return CatalystEvaluationResult(
                symbol=symbol,
                state=CatalystState.NO_CATALYST,
                gem_age_minutes=9999.0,
                clv=float(candidate.get("clv", 0.50)),
                extension_r=0.0,
                volume_retention=1.0,
                has_structural_runway=True,
                is_live_intraday_eligible=False,
                is_aftermarket_context_eligible=False,
                risk_allocation_r=1.00,
                priority_tier=2,
                veto_reason=None,
                policy_description="Clean Standalone Baseline (1.00R Size)"
            )

        gem_timestamp_hours = float(candidate.get("gem_timestamp_hours", 10.0))  # 10:00 IST default
        gem_age_hours = max(0.0, evaluation_time_hours - gem_timestamp_hours)
        gem_age_minutes = gem_age_hours * 60.0

        # Structural Dimensions
        extension_r = float(candidate.get("extension_r", candidate.get("intraday_extension_r", 2.0)))
        clv = float(candidate.get("clv", candidate.get("close_location_val", 0.65)))
        vol_persistence = float(candidate.get("volume_persistence", candidate.get("volume_retention_ratio", 1.0)))
        has_runway = bool(candidate.get("has_structural_runway", True))
        holds_structure = bool(candidate.get("holds_breakout_structure", True))

        # 1. INTRADAY EVALUATION (<= 60m Window)
        if is_intraday_scheduler:
            if gem_age_minutes <= 60.0 and holds_structure:
                return CatalystEvaluationResult(
                    symbol=symbol,
                    state=CatalystState.FRESH,
                    gem_age_minutes=gem_age_minutes,
                    clv=clv,
                    extension_r=extension_r,
                    volume_retention=vol_persistence,
                    has_structural_runway=has_runway,
                    is_live_intraday_eligible=True,
                    is_aftermarket_context_eligible=False,
                    risk_allocation_r=1.50,
                    priority_tier=1,
                    veto_reason=None,
                    policy_description="Live Intraday Gem Active (<= 60m TTL, 1.50R Size, Priority 1)"
                )
            elif not holds_structure:
                return CatalystEvaluationResult(
                    symbol=symbol,
                    state=CatalystState.INVALIDATED,
                    gem_age_minutes=gem_age_minutes,
                    clv=clv,
                    extension_r=extension_r,
                    volume_retention=vol_persistence,
                    has_structural_runway=has_runway,
                    is_live_intraday_eligible=False,
                    is_aftermarket_context_eligible=False,
                    risk_allocation_r=0.00,
                    priority_tier=3,
                    veto_reason="Price broke below morning breakout level / VWAP",
                    policy_description="Intraday Breakdown Invalidation (VETO)"
                )

        # 2. AFTER-MARKET EVALUATION (15:30 - 16:00 IST)
        # Deterministic boundary specification (Zero ambiguous gaps)
        if not holds_structure:
            state = CatalystState.INVALIDATED
            is_context_eligible = False
            r_alloc = 0.00
            p_tier = 3
            veto = "Structural Breakdown: Price lost breakout support or VWAP into close"
            desc = "Invalidated Catalyst (VETO / No Alert)"

        elif extension_r <= 3.2 and clv >= 0.68 and vol_persistence >= 1.1 and has_runway:
            state = CatalystState.SURVIVED
            is_context_eligible = True
            r_alloc = 1.50
            p_tier = 1
            veto = None
            desc = "Certified Catalyst Survivor (1.50R Size, Priority 1 Context Boost)"

        elif extension_r > 3.6 or clv < 0.50 or vol_persistence < 0.8:
            state = CatalystState.EXHAUSTED
            is_context_eligible = False
            r_alloc = 0.00
            p_tier = 3
            veto = "Climax Exhaustion: Extension > 3.6R or Upper Wick Fade > 50%"
            desc = "Climax Runner Exhaustion (VETO for Long Continuation Breakouts)"

        else:
            state = CatalystState.COOLING
            is_context_eligible = False
            r_alloc = 1.00
            p_tier = 2
            veto = None
            desc = "Catalyst Cooling / Neutral Drift (Standard 1.00R Baseline)"

        return CatalystEvaluationResult(
            symbol=symbol,
            state=state,
            gem_age_minutes=gem_age_minutes,
            clv=clv,
            extension_r=extension_r,
            volume_retention=vol_persistence,
            has_structural_runway=has_runway,
            is_live_intraday_eligible=False,
            is_aftermarket_context_eligible=is_context_eligible,
            risk_allocation_r=r_alloc,
            priority_tier=p_tier,
            veto_reason=veto,
            policy_description=desc
        )
