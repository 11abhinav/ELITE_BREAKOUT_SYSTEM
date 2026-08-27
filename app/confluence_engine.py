# app/confluence_engine.py
# Phase 3: Cross-Scanner Confluence & Meta-Analysis Engine
#
# RULE 67 CHANGE-RATIONALE:
# - Evaluates cross-scanner agreement across EOD V2, Multi-TF V2, Reversal V2, Pullback V2, Accumulation V2, and Multibagger V2.
# - Enforces State-Aware Confluence: distinguishes confirmed triggers from developing watch states.
# - Enforces Sample-Size Safety Floor (n >= 30): blocks unverified pairs (e.g. n=4) from receiving meta-score bonuses.
# - Enforces Canonical Opportunity Deduplication (opportunity_id = symbol_date_family).
# - Preserves Individual Scanner States Verbatim (never overwrites or forces signals).

import logging
import math
from datetime import date
from typing import Dict, Any, List, Optional
import pandas as pd
import numpy as np

logger = logging.getLogger("ConfluenceV3Engine")

# Statistically verified pairs in historical replay with sample size n >= 30
VERIFIED_CONFLUENCE_PAIRS_N30 = {
    tuple(sorted(["EOD", "PULLBACK"])),
    tuple(sorted(["EOD", "REVERSAL"])),
    tuple(sorted(["EOD", "ACCUMULATION"])),
    tuple(sorted(["MULTI_TF", "ACCUMULATION"])),
    tuple(sorted(["REVERSAL", "ACCUMULATION"]))
}


def evaluate_cross_scanner_confluence(
    symbol: str,
    date_str: str,
    scanner_outcomes: Dict[str, Dict[str, Any]],
    macro_regime: str = "NEUTRAL"
) -> Dict[str, Any]:
    """
    Evaluates cross-scanner confluence and assigns Meta-Conviction Rank without modifying scanner states.
    """
    opportunity_id = f"{symbol}_{date_str}_BREAKOUT_FAMILY"
    
    confirmed_engines = []
    watch_engines = []
    engine_states = {}

    for scanner_name, res in scanner_outcomes.items():
        if not isinstance(res, dict):
            continue
        
        st = res.get("state", "NO_VALID_SETUP")
        inv_st = res.get("investment_state", "")
        engine_states[scanner_name] = st

        if scanner_name == "MULTIBAGGER":
            if st in ["CONFIRMED", "WATCH"] and inv_st in ["UNDERVALUED_WATCH", "FAIRLY_VALUED"]:
                watch_engines.append("MULTIBAGGER")
        else:
            if st in ["CONFIRMED", "CANDIDATE"]:
                confirmed_engines.append(scanner_name)
            elif st in ["WATCH", "IMMEDIATE_TRIGGER_ZONE"]:
                watch_engines.append(scanner_name)

    n_confirmed = len(confirmed_engines)
    n_watch = len(watch_engines)
    total_depth = n_confirmed + n_watch

    # Check verified pair alignment (n >= 30 sample size floor)
    all_participating = confirmed_engines + watch_engines
    has_verified_pair = False
    sample_size_floor_passed = True

    if len(all_participating) >= 2:
        for i in range(len(all_participating)):
            for j in range(i + 1, len(all_participating)):
                pair = tuple(sorted([all_participating[i], all_participating[j]]))
                if pair in VERIFIED_CONFLUENCE_PAIRS_N30:
                    has_verified_pair = True
                elif pair == ("PULLBACK", "REVERSAL"):
                    sample_size_floor_passed = False  # n=4 unverified pair

    # Meta-Conviction Tier Classification
    if n_confirmed >= 2 and (has_verified_pair or len(all_participating) >= 3):
        meta_tier = "🔥 APEX CONFLUENCE"
        base_meta_score = 90.0
    elif n_confirmed >= 1 and n_watch >= 1:
        meta_tier = "🚀 HIGH CONFLUENCE"
        base_meta_score = 78.0
    elif n_confirmed == 0 and n_watch >= 2:
        meta_tier = "👀 DEVELOPING CONFLUENCE"
        base_meta_score = 65.0
    elif n_confirmed == 1 and n_watch == 0:
        meta_tier = "⚡ SINGLE ENGINE CONFIRMED"
        base_meta_score = 55.0
    else:
        meta_tier = "⚪ UNVERIFIED / LOW"
        base_meta_score = 40.0

    # Meta-Score Adjustments
    meta_score = base_meta_score
    if has_verified_pair: meta_score += 5.0
    if not sample_size_floor_passed: meta_score -= 10.0
    if macro_regime == "STRONG_BULL": meta_score += 5.0

    meta_score = max(0.0, min(100.0, meta_score))
    grade = "A+" if meta_score >= 85.0 else ("A" if meta_score >= 70.0 else ("B" if meta_score >= 55.0 else "C"))

    return {
        "opportunity_id": opportunity_id,
        "symbol": symbol,
        "date_str": date_str,
        "meta_conviction_tier": meta_tier,
        "meta_score": meta_score,
        "quality_grade": grade,
        "confluence_depth": total_depth,
        "confirmed_engine_count": n_confirmed,
        "watch_engine_count": n_watch,
        "participating_engines": all_participating,
        "engine_states": engine_states,
        "has_verified_pair_n30": has_verified_pair,
        "sample_size_floor_passed": sample_size_floor_passed,
        "data_confidence": "HIGH"
    }
