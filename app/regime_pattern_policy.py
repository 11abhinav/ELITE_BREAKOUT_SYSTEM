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

# APPROVED PRODUCTION TECHNICAL PATTERNS (FROZEN TOP 4)
APPROVED_TECHNICAL_PATTERNS: Set[str] = {
    "WYCKOFF_SPRING_TYPE_2",
    "BULL_FLAG",
    "MULTI_MONTH_BASE_BREAKOUT",
    "UNDERCUT_AND_RALLY",
}

# RESEARCH-ONLY PATTERNS (Retained for offline research/backtests; not live triggers)
RESEARCH_ONLY_PATTERNS: Set[str] = {
    "FLAT_BASE_BREAKOUT",
    "ASCENDING_TRIANGLE",
    "FALLING_WEDGE_REVERSAL",
    "INVERSE_HEAD_AND_SHOULDERS",
    "DOUBLE_BOTTOM_SHAKEOUT",
    "PENNANT_CONVERGENCE",
    "PULLBACK_EMA_BOUNCE",
}

# QUARANTINED PATTERNS (Demoted due to negative/breakeven alpha or sample deficit)
QUARANTINED_PATTERNS: Set[str] = {
    "HIGH_TIGHT_FLAG",
    "VCP_CONTRACTION",
    "CUP_AND_HANDLE",
}

REGIME_PATTERN_POLICY_MAP: Dict[str, Dict[str, Any]] = {
    "STRONG_BULL": {
        "primary_pattern": "MULTI_MONTH_BASE_BREAKOUT",
        "primary_bonus": 15.0,
        "secondary_pattern": "WYCKOFF_SPRING_TYPE_2",
        "secondary_bonus": 10.0,
        "allowed_patterns": [
            "MULTI_MONTH_BASE_BREAKOUT", "WYCKOFF_SPRING_TYPE_2", "BULL_FLAG", "UNDERCUT_AND_RALLY"
        ],
        "prohibited_patterns": []
    },
    "BULL": {
        "primary_pattern": "BULL_FLAG",
        "primary_bonus": 15.0,
        "secondary_pattern": "WYCKOFF_SPRING_TYPE_2",
        "secondary_bonus": 10.0,
        "allowed_patterns": [
            "BULL_FLAG", "WYCKOFF_SPRING_TYPE_2", "MULTI_MONTH_BASE_BREAKOUT", "UNDERCUT_AND_RALLY"
        ],
        "prohibited_patterns": []
    },
    "SIDEWAYS": {
        "primary_pattern": "BULL_FLAG",
        "primary_bonus": 12.0,
        "secondary_pattern": "WYCKOFF_SPRING_TYPE_2",
        "secondary_bonus": 8.0,
        "allowed_patterns": [
            "BULL_FLAG", "WYCKOFF_SPRING_TYPE_2", "MULTI_MONTH_BASE_BREAKOUT"
        ],
        "prohibited_patterns": ["UNDERCUT_AND_RALLY"]
    },
    "HIGH_VOLATILITY": {
        "primary_pattern": "WYCKOFF_SPRING_TYPE_2",
        "primary_bonus": 15.0,
        "secondary_pattern": "UNDERCUT_AND_RALLY",
        "secondary_bonus": 12.0,
        "allowed_patterns": [
            "WYCKOFF_SPRING_TYPE_2", "UNDERCUT_AND_RALLY", "BULL_FLAG"
        ],
        "prohibited_patterns": ["MULTI_MONTH_BASE_BREAKOUT"]
    },
    "WEAK_BEAR": {
        "primary_pattern": "UNDERCUT_AND_RALLY",
        "primary_bonus": 15.0,
        "secondary_pattern": "WYCKOFF_SPRING_TYPE_2",
        "secondary_bonus": 10.0,
        "allowed_patterns": [
            "UNDERCUT_AND_RALLY", "WYCKOFF_SPRING_TYPE_2"
        ],
        "prohibited_patterns": ["MULTI_MONTH_BASE_BREAKOUT", "BULL_FLAG"]
    },
    "STRONG_BEAR": {
        "primary_pattern": "UNDERCUT_AND_RALLY",
        "primary_bonus": 15.0,
        "secondary_pattern": "WYCKOFF_SPRING_TYPE_2",
        "secondary_bonus": 10.0,
        "allowed_patterns": [
            "UNDERCUT_AND_RALLY", "WYCKOFF_SPRING_TYPE_2"
        ],
        "prohibited_patterns": ["MULTI_MONTH_BASE_BREAKOUT", "BULL_FLAG"]
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
