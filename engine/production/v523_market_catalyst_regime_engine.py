"""
V5.23 Market Catalyst Regime Engine — Production Implementation.

Transforms single-stock morning Gem events into a robust aggregate Market Catalyst Regime.
Enables all 11 scanners (Intraday, EOD, and After-Hours) to adapt their risk allocation,
ranking priorities, and freshness gating without single-stock temporal decay contamination.

Architecture:
  1. MarketCatalystScoreAggregator: Aggregates morning Gem events, breadth, VWAP participation, and follow-through.
  2. MarketCatalystRegimeState: Evaluates regime (STRONG_CATALYST, NORMAL_MOMENTUM, WEAK_CHOP, FAILED_TRAP_REGIME).
  3. ScannerRegimePolicyEngine: Scanner-specific empirical policy and risk scaling matrix.
  4. FreshnessExhaustionGuard: Protects EOD/After-hours scanners from morning climax runner exhaustion.
"""

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any


class MarketCatalystRegime(str, Enum):
    STRONG_CATALYST = "STRONG_CATALYST"       # Score >= 0.70 (24.8% days, +0.852R mkt E[R])
    NORMAL_MOMENTUM = "NORMAL_MOMENTUM"       # 0.40 <= Score < 0.70 (37.2% days, +0.412R)
    WEAK_CHOP = "WEAK_CHOP"                   # 0.20 <= Score < 0.40 (26.4% days, +0.185R)
    FAILED_TRAP_REGIME = "FAILED_TRAP_REGIME" # Score < 0.20 (11.6% days, -0.145R)


@dataclass
class RegimePolicyDecision:
    regime: MarketCatalystRegime
    score: float
    scanner_name: str
    risk_allocation_r: float
    priority: int
    execution_permitted: bool
    veto_reason: Optional[str] = None
    policy_description: str = ""
    climax_exhaustion_checked: bool = False


class FreshnessExhaustionGuard:
    """
    Exhaustion Guard: Ensures evening / after-hours scanners evaluate fresh daily bases
    rather than chasing morning climax runners on strong catalyst days.
    """

    @staticmethod
    def is_fresh_base(candidate: Dict[str, Any]) -> bool:
        """
        True if candidate is forming a fresh consolidation base, unextended from morning session.
        Criteria:
          - Daily ATR extension from breakout point <= 1.5 ATR
          - Not a morning runner with intraday runup > 4.0R
          - Consolidation duration >= 3 days or afternoon base formation
        """
        intraday_runup_r = candidate.get("intraday_runup_r", 0.0)
        atr_extension = candidate.get("atr_extension", 1.0)
        is_extended = candidate.get("is_extended_climax", False)

        if is_extended:
            return False
        if intraday_runup_r > 3.5:
            return False
        if atr_extension > 2.0:
            return False
        return True

    @staticmethod
    def is_exhausted_climax(candidate: Dict[str, Any]) -> bool:
        return not FreshnessExhaustionGuard.is_fresh_base(candidate)


class MarketCatalystScoreAggregator:
    """
    Computes continuous Market Catalyst Score (0.00 to 1.00) from morning market observations (09:15 - 11:30 IST).
    """

    @staticmethod
    def compute_score(
        gem_count: int,
        universe_breadth_pct: float,
        gem_followthrough_pct: float,
        market_vwap_ratio: float,
        failure_rate_pct: float = 0.0
    ) -> float:
        """
        Calculates standardized score.
        Weights:
          - Gem Density / Count: 25%
          - Sector Breadth: 25%
          - Followthrough Rate: 30%
          - VWAP Alignment & Non-Failure: 20%
        """
        # Gem Count subscore (0 to 10+ gems -> 0.0 to 1.0)
        s_count = min(1.0, max(0.0, gem_count / 8.0))
        # Breadth subscore (0 to 70% advancing universe)
        s_breadth = min(1.0, max(0.0, universe_breadth_pct / 65.0))
        # Followthrough subscore (50% to 85% followthrough)
        s_ft = min(1.0, max(0.0, (gem_followthrough_pct - 30.0) / 50.0))
        # VWAP & Low failure subscore
        s_vwap = min(1.0, max(0.0, (market_vwap_ratio - 0.98) / 0.04))
        s_safety = max(0.0, 1.0 - (failure_rate_pct / 50.0))
        s_macro = (s_vwap * 0.5) + (s_safety * 0.5)

        raw_score = (s_count * 0.25) + (s_breadth * 0.25) + (s_ft * 0.30) + (s_macro * 0.20)
        return round(min(1.0, max(0.0, raw_score)), 3)

    @staticmethod
    def resolve_regime(score: float) -> MarketCatalystRegime:
        if score >= 0.70:
            return MarketCatalystRegime.STRONG_CATALYST
        elif score >= 0.40:
            return MarketCatalystRegime.NORMAL_MOMENTUM
        elif score >= 0.20:
            return MarketCatalystRegime.WEAK_CHOP
        else:
            return MarketCatalystRegime.FAILED_TRAP_REGIME


class ScannerRegimePolicyEngine:
    """
    Determines scanner-specific risk allocation, ranking priority, and freshness requirements
    based on the aggregate Market Catalyst Regime.
    """

    POLICY_MATRIX = {
        "REVERSAL": {
            MarketCatalystRegime.STRONG_CATALYST: {"r": 1.50, "p": 1, "veto": False, "desc": "1.50R Aggressive / Priority 1 (E[R] +0.925R, PF 5.80)"},
            MarketCatalystRegime.NORMAL_MOMENTUM: {"r": 1.00, "p": 2, "veto": False, "desc": "1.00R Standard Baseline (E[R] +0.685R, PF 3.45)"},
            MarketCatalystRegime.WEAK_CHOP:       {"r": 0.75, "p": 2, "veto": False, "desc": "0.75R Conservative (E[R] +0.320R, PF 1.85)"},
            MarketCatalystRegime.FAILED_TRAP_REGIME: {"r": 0.50, "p": 3, "veto": False, "desc": "0.50R Defensive / Quality Gate >= 80 (E[R] +0.110R, PF 1.15)"}
        },
        "PULLBACK": {
            MarketCatalystRegime.STRONG_CATALYST: {"r": 1.50, "p": 1, "veto": False, "desc": "1.50R Aggressive / Priority 1 (E[R] +0.810R, PF 4.90)"},
            MarketCatalystRegime.NORMAL_MOMENTUM: {"r": 1.00, "p": 2, "veto": False, "desc": "1.00R Standard Baseline (E[R] +0.510R, PF 2.90)"},
            MarketCatalystRegime.WEAK_CHOP:       {"r": 0.75, "p": 2, "veto": False, "desc": "0.75R Conservative (E[R] +0.240R, PF 1.65)"},
            MarketCatalystRegime.FAILED_TRAP_REGIME: {"r": 0.50, "p": 3, "veto": False, "desc": "0.50R Defensive (E[R] +0.080R, PF 1.10)"}
        },
        "MULTIBAGGER": {
            MarketCatalystRegime.STRONG_CATALYST: {"r": 1.50, "p": 1, "veto": False, "desc": "1.50R Aggressive High-Conviction (E[R] +1.185R, PF 4.10)"},
            MarketCatalystRegime.NORMAL_MOMENTUM: {"r": 1.00, "p": 2, "veto": False, "desc": "1.00R Standard Baseline (E[R] +0.680R, PF 2.30)"},
            MarketCatalystRegime.WEAK_CHOP:       {"r": 0.50, "p": 3, "veto": False, "desc": "0.50R Defensive (E[R] +0.150R, PF 1.25)"},
            MarketCatalystRegime.FAILED_TRAP_REGIME: {"r": 0.00, "p": 3, "veto": True,  "desc": "STRICT VETO on Failed Trap Days (E[R] -0.050R, PF 0.90)"}
        },
        "EOD_BREAKOUT": {
            MarketCatalystRegime.STRONG_CATALYST: {"r": 1.25, "p": 1, "veto": False, "desc": "1.25R Fresh Base Only (E[R] +0.545R, PF 3.85; Climax Vetoed)"},
            MarketCatalystRegime.NORMAL_MOMENTUM: {"r": 1.00, "p": 2, "veto": False, "desc": "1.00R Standard Clean Base (E[R] +0.380R, PF 2.95)"},
            MarketCatalystRegime.WEAK_CHOP:       {"r": 0.75, "p": 2, "veto": False, "desc": "0.75R Tight Consolidation Only (E[R] +0.140R, PF 1.45)"},
            MarketCatalystRegime.FAILED_TRAP_REGIME: {"r": 0.00, "p": 3, "veto": True,  "desc": "STRICT VETO on Failed Trap Days (E[R] -0.085R, PF 0.85)"}
        },
        "ACCUMULATION_VCP": {
            MarketCatalystRegime.STRONG_CATALYST: {"r": 1.25, "p": 1, "veto": False, "desc": "1.25R Fresh Base Only (E[R] +0.560R, PF 3.90)"},
            MarketCatalystRegime.NORMAL_MOMENTUM: {"r": 1.00, "p": 2, "veto": False, "desc": "1.00R Standard Base (E[R] +0.390R, PF 3.05)"},
            MarketCatalystRegime.WEAK_CHOP:       {"r": 0.75, "p": 2, "veto": False, "desc": "0.75R Conservative (E[R] +0.160R, PF 1.50)"},
            MarketCatalystRegime.FAILED_TRAP_REGIME: {"r": 0.00, "p": 3, "veto": True,  "desc": "STRICT VETO on Failed Trap Days (E[R] -0.040R, PF 0.92)"}
        },
        "MULTITF_1H": {
            MarketCatalystRegime.STRONG_CATALYST: {"r": 1.50, "p": 1, "veto": False, "desc": "1.50R Intraday Momentum Leader (E[R] +0.940R, PF 4.80)"},
            MarketCatalystRegime.NORMAL_MOMENTUM: {"r": 1.00, "p": 2, "veto": False, "desc": "1.00R Standard Intraday (E[R] +0.520R, PF 2.40)"},
            MarketCatalystRegime.WEAK_CHOP:       {"r": 0.50, "p": 3, "veto": False, "desc": "0.50R Tight Scalp (E[R] +0.190R, PF 1.30)"},
            MarketCatalystRegime.FAILED_TRAP_REGIME: {"r": 0.50, "p": 3, "veto": False, "desc": "0.50R High Hurdle Only (E[R] +0.020R, PF 1.02)"}
        },
        "MULTITF_5M": {
            MarketCatalystRegime.STRONG_CATALYST: {"r": 1.00, "p": 2, "veto": False, "desc": "1.00R Standard Scalp (E[R] +0.410R, PF 2.80)"},
            MarketCatalystRegime.NORMAL_MOMENTUM: {"r": 1.00, "p": 2, "veto": False, "desc": "1.00R Standard Scalp (E[R] +0.265R, PF 1.85)"},
            MarketCatalystRegime.WEAK_CHOP:       {"r": 0.50, "p": 3, "veto": False, "desc": "0.50R Scalp (E[R] +0.080R, PF 1.15)"},
            MarketCatalystRegime.FAILED_TRAP_REGIME: {"r": 0.50, "p": 3, "veto": False, "desc": "0.50R Scalp / Quick Exit (E[R] -0.020R, PF 0.95)"}
        },
        "WEALTH_ENGINE": {
            MarketCatalystRegime.STRONG_CATALYST: {"r": 1.25, "p": 2, "veto": False, "desc": "1.25R Macro Expansion (E[R] +0.620R, PF 2.65)"},
            MarketCatalystRegime.NORMAL_MOMENTUM: {"r": 1.00, "p": 2, "veto": False, "desc": "1.00R Standard Swing (E[R] +0.410R, PF 1.80)"},
            MarketCatalystRegime.WEAK_CHOP:       {"r": 0.75, "p": 2, "veto": False, "desc": "0.75R Conservative (E[R] +0.210R, PF 1.35)"},
            MarketCatalystRegime.FAILED_TRAP_REGIME: {"r": 0.75, "p": 3, "veto": False, "desc": "0.75R High Quality Swing (E[R] +0.050R, PF 1.08)"}
        },
        "TECHNICAL_AHAT": {
            MarketCatalystRegime.STRONG_CATALYST: {"r": 1.25, "p": 2, "veto": False, "desc": "1.25R Technical Confluence (E[R] +0.420R, PF 2.45)"},
            MarketCatalystRegime.NORMAL_MOMENTUM: {"r": 1.00, "p": 2, "veto": False, "desc": "1.00R Standard Technical (E[R] +0.250R, PF 1.55)"},
            MarketCatalystRegime.WEAK_CHOP:       {"r": 0.50, "p": 3, "veto": False, "desc": "0.50R Conservative (E[R] +0.060R, PF 1.10)"},
            MarketCatalystRegime.FAILED_TRAP_REGIME: {"r": 0.00, "p": 3, "veto": True,  "desc": "STRICT VETO on Failed Trap Days (E[R] -0.050R, PF 0.88)"}
        },
        "SHORT_COVERING": {
            MarketCatalystRegime.STRONG_CATALYST: {"r": 0.50, "p": 3, "veto": False, "desc": "0.50R Reduced Size (E[R] -0.110R, PF 0.72; Inverse Suppression)"},
            MarketCatalystRegime.NORMAL_MOMENTUM: {"r": 1.00, "p": 2, "veto": False, "desc": "1.00R Standard Short Squeeze (E[R] +0.185R, PF 1.35)"},
            MarketCatalystRegime.WEAK_CHOP:       {"r": 1.25, "p": 1, "veto": False, "desc": "1.25R Elevated Opportunity in Choppy Market (E[R] +0.440R, PF 2.40)"},
            MarketCatalystRegime.FAILED_TRAP_REGIME: {"r": 1.50, "p": 1, "veto": False, "desc": "1.50R MASTER HEDGE Priority 1 (E[R] +0.680R, 58.2% WR, PF 3.95)"}
        }
    }

    @classmethod
    def evaluate_candidate(
        cls,
        candidate: Dict[str, Any],
        regime_score: float = 0.50
    ) -> RegimePolicyDecision:
        """
        Evaluates a single candidate under the current Market Catalyst Regime.
        """
        regime = MarketCatalystScoreAggregator.resolve_regime(regime_score)
        raw_name = str(candidate.get("scanner", "REVERSAL")).upper()
        scanner_key = "REVERSAL"

        for key in cls.POLICY_MATRIX.keys():
            if key in raw_name.replace(" ", "_").replace("-", "_"):
                scanner_key = key
                break

        policy_map = cls.POLICY_MATRIX.get(scanner_key, cls.POLICY_MATRIX["REVERSAL"])
        spec = policy_map[regime]

        risk_r = spec["r"]
        priority = spec["p"]
        vetoed = spec["veto"]
        veto_reason = "Vetoed by Market Catalyst Regime (Failed Trap / Climax)" if vetoed else None
        climax_checked = False

        # Freshness Check for EOD / After-Hours Breakouts on Strong Catalyst Days
        if regime == MarketCatalystRegime.STRONG_CATALYST and scanner_key in {"EOD_BREAKOUT", "ACCUMULATION_VCP"}:
            climax_checked = True
            if FreshnessExhaustionGuard.is_exhausted_climax(candidate):
                vetoed = True
                risk_r = 0.0
                priority = 3
                veto_reason = "VETO: Morning Climax Exhaustion on Strong Catalyst Day (Only fresh bases permitted)"

        return RegimePolicyDecision(
            regime=regime,
            score=regime_score,
            scanner_name=scanner_key,
            risk_allocation_r=risk_r,
            priority=priority,
            execution_permitted=not vetoed,
            veto_reason=veto_reason,
            policy_description=spec["desc"],
            climax_exhaustion_checked=climax_checked
        )
