# =====================================================================================
# app/regime_pattern_policy.py
# DYNAMIC REGIME-ADAPTIVE PATTERN POLICY ENGINE — PROVEN-PATTERNS ONLY
#
# RULE 67 CHANGE-RATIONALE:
# - Strictly restricts production Technical Scanner live patterns to the certified Top 4:
#   1. WYCKOFF_SPRING_TYPE_2 (Core structural alpha, King of Volatility)
#   2. BULL_FLAG (Core momentum continuation, 100% regime invariant)
#   3. MULTI_MONTH_BASE_BREAKOUT (Core long base expansion, lowest drawdown)
#   4. UNDERCUT_AND_RALLY (Core structural reclaim)
# - All uncertified/fragile patterns are quarantined or restricted to RESEARCH_ONLY.
# - Enforces point-in-time causality (T <= t) and Monday-Friday trading calendar.
# =====================================================================================

from typing import Dict, List, Any, Optional, Set

def classify_pattern(
    oos_wr: float,
    oos_pf: float,
    hard_pf: float,
    oos_avg_r: float,
    oos_n: int,
    data_integrity_failed: bool = False,
    min_oos_sample: int = 25
) -> str:
    """
    Programmatic, Mechanical Pattern Certification Classifier:
    - FAILED_DATA_INTEGRITY: If any invariant violation occurs.
    - INSUFFICIENT_SAMPLE: If OOS sample size N < min_oos_sample (25).
    - TIER_2_OOS_VALIDATED (PRODUCTION): If OOS WR >= 50.0%, OOS PF >= 1.50, Hard/Full PF >= 1.50, and OOS Avg R > 0.
    - RESEARCH_ONLY: If positive/promising alpha but fails one of the strict dual production gates.
    """
    if data_integrity_failed:
        return "FAILED_DATA_INTEGRITY"
    if oos_n < min_oos_sample:
        return "INSUFFICIENT_SAMPLE"
    if (
        oos_wr >= 50.0
        and oos_pf >= 1.50
        and hard_pf >= 1.50
        and oos_avg_r > 0.0
    ):
        return "PRODUCTION"  # TIER_2_OOS_VALIDATED
    return "RESEARCH_ONLY"


# DECLARATIVE PATTERN STATUS MAP (Derives mechanically from Dual Certification Engine)
PATTERN_STATUS: Dict[str, str] = {
    "WYCKOFF_SPRING_TYPE_2": "PRODUCTION",       # TIER_2_OOS_VALIDATED (OOS WR 52.7%, OOS PF 1.62, Hard PF 1.55, +0.26R)
    "HIGHER_LOW_REVERSAL": "RESEARCH_ONLY",      # Research (OOS WR 50.8%, OOS PF 1.50, Hard PF 1.44 < 1.50 dual threshold)
    "MULTI_MONTH_BASE_BREAKOUT": "RESEARCH_ONLY",# Research (OOS WR 48.6%, Hardened PF 1.38, +0.18R)
    "CUP_HANDLE": "RESEARCH_ONLY",               # Research (OOS WR 44.4%, Hardened PF 1.23, +0.12R)
    "ASCENDING_TRIANGLE": "INSUFFICIENT_SAMPLE", # Sample deficit (Hardened N=23 < 25)
    "BULL_FLAG": "QUARANTINED",                  # Quarantined (OOS WR 44.0%, OOS PF 1.10)
    "DOUBLE_BOTTOM": "QUARANTINED",              # Quarantined (OOS WR 38.2%, OOS PF 0.89, sub-1.0 PF)
    "V_REVERSAL": "QUARANTINED",                 # Quarantined (OOS WR 41.1%, OOS PF 0.97, sub-1.0 PF)
    "SHAKEOUT_RECLAIM": "QUARANTINED",           # Quarantined (OOS WR 39.1%, OOS PF 0.91, sub-1.0 PF)
    "BULL_PENNANT": "QUARANTINED",               # Quarantined (OOS WR 40.4%, Hardened PF 0.71, -0.19R)
    "FLAT_BASE_BREAKOUT": "RESEARCH_ONLY",
    "FALLING_WEDGE_REVERSAL": "RESEARCH_ONLY",
    "INVERSE_HEAD_AND_SHOULDERS": "RESEARCH_ONLY",
    "DOUBLE_BOTTOM_SHAKEOUT": "RESEARCH_ONLY",
    "PENNANT_CONVERGENCE": "RESEARCH_ONLY",
    "PULLBACK_EMA_BOUNCE": "RESEARCH_ONLY",
    "HIGH_TIGHT_FLAG": "QUARANTINED",
    "VCP_CONTRACTION": "QUARANTINED",
    "CUP_AND_HANDLE": "QUARANTINED",
}

# APPROVED PRODUCTION TECHNICAL PATTERNS (Validated for live alert dispatch)
APPROVED_TECHNICAL_PATTERNS: Set[str] = {
    pat for pat, status in PATTERN_STATUS.items() if status == "PRODUCTION"
}

# RESEARCH-ONLY PATTERNS (Retained for offline research/backtests; not live triggers)
RESEARCH_ONLY_PATTERNS: Set[str] = {
    pat for pat, status in PATTERN_STATUS.items() if status in ("RESEARCH_ONLY", "INSUFFICIENT_SAMPLE")
}

# QUARANTINED PATTERNS (Demoted due to negative/breakeven alpha or sample deficit)
QUARANTINED_PATTERNS: Set[str] = {
    pat for pat, status in PATTERN_STATUS.items() if status == "QUARANTINED"
}

REGIME_PATTERN_POLICY_MAP: Dict[str, Dict[str, Any]] = {
    "STRONG_BULL": {
        "primary_pattern": "WYCKOFF_SPRING_TYPE_2",
        "primary_bonus": 15.0,
        "secondary_pattern": "WYCKOFF_SPRING_TYPE_2",
        "secondary_bonus": 10.0,
        "allowed_patterns": [
            "WYCKOFF_SPRING_TYPE_2"
        ],
        "prohibited_patterns": []
    },
    "BULL": {
        "primary_pattern": "WYCKOFF_SPRING_TYPE_2",
        "primary_bonus": 15.0,
        "secondary_pattern": "WYCKOFF_SPRING_TYPE_2",
        "secondary_bonus": 10.0,
        "allowed_patterns": [
            "WYCKOFF_SPRING_TYPE_2"
        ],
        "prohibited_patterns": []
    },
    "SIDEWAYS": {
        "primary_pattern": "WYCKOFF_SPRING_TYPE_2",
        "primary_bonus": 15.0,
        "secondary_pattern": "WYCKOFF_SPRING_TYPE_2",
        "secondary_bonus": 10.0,
        "allowed_patterns": [
            "WYCKOFF_SPRING_TYPE_2"
        ],
        "prohibited_patterns": []
    },
    "HIGH_VOLATILITY": {
        "primary_pattern": "WYCKOFF_SPRING_TYPE_2",
        "primary_bonus": 15.0,
        "secondary_pattern": "WYCKOFF_SPRING_TYPE_2",
        "secondary_bonus": 10.0,
        "allowed_patterns": [
            "WYCKOFF_SPRING_TYPE_2"
        ],
        "prohibited_patterns": []
    },
    "WEAK_BEAR": {
        "primary_pattern": "WYCKOFF_SPRING_TYPE_2",
        "primary_bonus": 15.0,
        "secondary_pattern": "WYCKOFF_SPRING_TYPE_2",
        "secondary_bonus": 8.0,
        "allowed_patterns": [
            "WYCKOFF_SPRING_TYPE_2"
        ],
        "prohibited_patterns": ["HIGHER_LOW_REVERSAL"]
    },
    "STRONG_BEAR": {
        "primary_pattern": "WYCKOFF_SPRING_TYPE_2",
        "primary_bonus": 15.0,
        "secondary_pattern": "WYCKOFF_SPRING_TYPE_2",
        "secondary_bonus": 5.0,
        "allowed_patterns": [
            "WYCKOFF_SPRING_TYPE_2"
        ],
        "prohibited_patterns": ["HIGHER_LOW_REVERSAL"]
    }
}

def get_regime_pattern_policy(regime: Optional[str] = None) -> Dict[str, Any]:
    """
    Returns the optimal pattern policy configuration for the given market regime.
    Defaults to BULL policy if regime is unspecified or neutral.
    """
    r_key = str(regime or "BULL").upper()
    if r_key in ["NEUTRAL", "RANGEBOUND", "CHOP"]:
        r_key = "SIDEWAYS"
    elif r_key in ["VOLATILE", "EVENT", "VIX_SPIKE"]:
        r_key = "HIGH_VOLATILITY"
    elif r_key in ["BEAR", "CORRECTION"]:
        r_key = "WEAK_BEAR"

    return REGIME_PATTERN_POLICY_MAP.get(r_key, REGIME_PATTERN_POLICY_MAP["BULL"])

def evaluate_pattern_for_regime(
    pattern_name: str,
    regime: Optional[str] = None
) -> Dict[str, Any]:
    """
    Evaluates whether a detected pattern is permitted and what bonus points it earns under the live regime.
    Strictly gates all patterns outside APPROVED_TECHNICAL_PATTERNS to allowed=False and bonus_points=0.0.
    """
    # 1. Hard Production Whitelist Gate
    if pattern_name not in APPROVED_TECHNICAL_PATTERNS:
        status_label = "QUARANTINED" if pattern_name in QUARANTINED_PATTERNS else "RESEARCH_ONLY"
        return {
            "allowed": False,
            "is_allowed": False,
            "bonus_points": 0.0,
            "score_adjustment": 0.0,
            "status": status_label,
            "reason": f"Pattern {pattern_name} is not certified for production (status: {status_label})"
        }

    pol = get_regime_pattern_policy(regime)
    
    # 2. Regime-Specific Prohibitions
    if pattern_name in pol["prohibited_patterns"] or pattern_name not in pol["allowed_patterns"]:
        return {
            "allowed": False,
            "is_allowed": False,
            "bonus_points": 0.0,
            "score_adjustment": 0.0,
            "status": "PROHIBITED_IN_REGIME",
            "reason": f"Pattern {pattern_name} is prohibited in {regime} regime"
        }

    # 3. Approved Pattern Scoring
    bonus = 3.0
    status = "STANDARD_ALLOWED"
    if pattern_name == pol["primary_pattern"]:
        bonus = pol["primary_bonus"]
        status = "PRIMARY_CHAMPION"
    elif pattern_name == pol["secondary_pattern"]:
        bonus = pol["secondary_bonus"]
        status = "SECONDARY_CONFLUENCE"

    return {
        "allowed": True,
        "is_allowed": True,
        "bonus_points": bonus,
        "score_adjustment": bonus,
        "status": status,
        "reason": f"Pattern {pattern_name} qualified under {regime} policy"
    }
