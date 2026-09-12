#!/usr/bin/env python3
"""
DAILY BUILDER V6.00 — HYBRID SPECIALIZED ROUTING PRODUCTION ENGINE
================================================================
Authoritative Production Routing & Archetype Classification Engine
Certified under Governance V2 following Full 3-Year Historical Tournament.

Core Architecture:
- Discovers, scores, and tags candidates into 5 behavioral archetypes.
- Generates multi-label metadata (primary, secondary, confidence).
- Implements Soft Routing: distributes 100% of candidates with dynamic
  priority sizing (1.0x Primary, 0.80x Secondary, 0.50x Unclassified).
- Provides deterministic collision resolution without duplicate capital risk.
"""

import os
import sys
import math
import datetime
from typing import Dict, List, Any, Optional, Tuple

ARCHETYPES = [
    "VCP_COIL",
    "LONG_BASE_ACCUMULATION",
    "PULLBACK_KEY_LEVEL",
    "SQUEEZE_SHORT_COVERING",
    "CLEAN_MOMENTUM_BREAKOUT"
]

SCANNER_MAP = {
    "VCP_COIL": "SCAN_VCP_1H",
    "LONG_BASE_ACCUMULATION": "SCAN_MULTIBAGGER_EOD",
    "PULLBACK_KEY_LEVEL": "SCAN_REVERSAL_KEYLEVEL",
    "SQUEEZE_SHORT_COVERING": "SCAN_SHORT_COVERING",
    "CLEAN_MOMENTUM_BREAKOUT": "SCAN_DAILY_BUILDER_45M"
}

ROUTING_WEIGHTS = {
    "PRIMARY_MATCH": 1.0,
    "SECONDARY_MATCH": 0.80,
    "DEFENSIVE_CROSS": 0.50
}

class HybridRouterEngineV6:
    """
    Production-grade Daily Builder V6 Hybrid Router.
    """
    VERSION = "V6.00_DAILY_BUILDER_HYBRID_ROUTER"
    PARENT_VERSION = "V5.30_PRODUCTION"

    def __init__(self, quality_floor: float = 60.0, confidence_floor: float = 0.20):
        self.quality_floor = quality_floor
        self.confidence_floor = confidence_floor

    def classify_candidate(self, cand: Dict[str, Any]) -> Dict[str, Any]:
        """
        Classifies a single Daily Builder candidate into 5 behavioral archetypes
        using point-in-time technical and structural features.
        """
        clv = float(cand.get("clv", 0.5))
        comp_ratio = float(cand.get("compression_ratio", cand.get("base_tightness", 1.5)))
        base_days = int(cand.get("base_duration_days", cand.get("comp_days", 20)))
        atr_stages = int(cand.get("atr_contraction_stages", 2))
        vol_contraction = float(cand.get("volume_contraction_ratio", 1.0))
        rvol = float(cand.get("rvol", 1.2))
        ema_dist = float(cand.get("ema_support_dist_pct", 1.5))
        freshness = float(cand.get("freshness", 0.5))
        rs_pct = float(cand.get("rs_percentile", 50.0))
        regime = str(cand.get("regime", "NEUTRAL_BULL"))

        # Base compression score (0.0 to 1.0)
        comp_score = max(0.0, min(1.0, 1.0 - (comp_ratio - 1.0) / 2.0))

        # 1. VCP / Coil Score
        vcp_score = round(min(100.0, (atr_stages / 4.0 * 40.0) + (max(0, 1.5 - vol_contraction) / 1.5 * 35.0) + (comp_score * 25.0)), 1)

        # 2. Long Base / Accumulation Score
        long_base_score = round(min(100.0, (min(base_days, 90) / 90.0 * 45.0) + (rs_pct / 100.0 * 35.0) + (clv * 20.0)), 1)

        # 3. Pullback / Key Level Score
        pullback_score = round(min(100.0, (max(0, 3.0 - ema_dist) / 3.0 * 50.0) + (clv * 35.0) + (comp_score * 15.0)), 1)

        # 4. Squeeze / Short Covering Score
        squeeze_score = round(min(100.0, (min(rvol, 4.0) / 4.0 * 55.0) + (clv * 25.0) + (comp_score * 20.0)), 1)

        # 5. Clean Momentum Breakout Score
        breakout_score = round(min(100.0, (clv * 40.0) + (freshness * 30.0) + (comp_score * 30.0)), 1)

        archetype_scores = {
            "VCP_COIL": vcp_score,
            "LONG_BASE_ACCUMULATION": long_base_score,
            "PULLBACK_KEY_LEVEL": pullback_score,
            "SQUEEZE_SHORT_COVERING": squeeze_score,
            "CLEAN_MOMENTUM_BREAKOUT": breakout_score
        }

        sorted_archs = sorted(archetype_scores.items(), key=lambda x: x[1], reverse=True)
        primary_arch, primary_score = sorted_archs[0]
        secondary_arch, secondary_score = sorted_archs[1]

        # Confidence calculation
        confidence = round((primary_score - secondary_score) / 100.0 + (primary_score / 200.0), 3)
        confidence = min(0.99, max(0.10, confidence))

        # Composite V5.30 / V6 Quality Score
        v530_quality = round((1.5 * clv + 1.5 * comp_score + 1.0 * freshness + 0.8 * (rs_pct / 100.0)) / 4.8 * 100.0, 1)

        # Sharp selloff veto rule
        is_eligible = 1 if (regime != "SHARP_SELLOFF" and v530_quality >= self.quality_floor and clv >= 0.40) else 0

        return {
            "symbol": cand.get("symbol", "UNKNOWN"),
            "v530_quality_score": v530_quality,
            "is_eligible": is_eligible,
            "regime": regime,
            "primary_archetype": primary_arch,
            "primary_score": primary_score,
            "secondary_archetype": secondary_arch,
            "secondary_score": secondary_score,
            "confidence": confidence,
            "archetype_scores": archetype_scores,
            "preferred_scanner": SCANNER_MAP.get(primary_arch, "SCAN_DAILY_BUILDER_45M")
        }

    def get_scanner_allocation(self, scanner_id: str, classified_cand: Dict[str, Any]) -> Dict[str, Any]:
        """
        Determines the priority weight, execution eligibility, and sizing for
        a specific downstream scanner evaluating a classified candidate.
        """
        p_arch = classified_cand["primary_archetype"]
        s_arch = classified_cand["secondary_archetype"]
        is_eligible = classified_cand["is_eligible"]

        preferred_scan = SCANNER_MAP.get(p_arch)
        secondary_scan = SCANNER_MAP.get(s_arch)

        if not is_eligible:
            return {
                "scanner_id": scanner_id,
                "is_active": False,
                "risk_weight": 0.0,
                "priority_tier": "VETOED",
                "is_primary_owner": False
            }

        if scanner_id == preferred_scan:
            # Primary Dedicated Match -> Full 1.0x Size, Top Priority
            return {
                "scanner_id": scanner_id,
                "is_active": True,
                "risk_weight": ROUTING_WEIGHTS["PRIMARY_MATCH"],
                "priority_tier": "TOP_PRIORITY_DEDICATED",
                "is_primary_owner": True
            }
        elif scanner_id == secondary_scan and classified_cand["secondary_score"] >= 65.0:
            # Secondary Confluence Match -> 0.80x Size, High Priority
            return {
                "scanner_id": scanner_id,
                "is_active": True,
                "risk_weight": ROUTING_WEIGHTS["SECONDARY_MATCH"],
                "priority_tier": "SECONDARY_CONFLUENCE",
                "is_primary_owner": False
            }
        else:
            # Defensive Cross-Archetype -> 0.50x Size (Zero Missed Winners)
            return {
                "scanner_id": scanner_id,
                "is_active": True,
                "risk_weight": ROUTING_WEIGHTS["DEFENSIVE_CROSS"],
                "priority_tier": "DEFENSIVE_CROSS_ARCHETYPE",
                "is_primary_owner": False
            }

    def route_candidate_pool(self, raw_candidates: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """
        Processes a full Daily Builder candidate pool and generates customized,
        soft-routed input candidate streams for all 5 downstream scanners.
        """
        routed_streams = {
            "SCAN_VCP_1H": [],
            "SCAN_MULTIBAGGER_EOD": [],
            "SCAN_REVERSAL_KEYLEVEL": [],
            "SCAN_SHORT_COVERING": [],
            "SCAN_DAILY_BUILDER_45M": []
        }

        for raw_c in raw_candidates:
            classified = self.classify_candidate(raw_c)
            if not classified["is_eligible"]:
                continue

            for s_id in routed_streams.keys():
                alloc = self.get_scanner_allocation(s_id, classified)
                if alloc["is_active"]:
                    augmented_cand = dict(raw_c)
                    augmented_cand.update({
                        "routing_metadata": classified,
                        "scanner_allocation": alloc,
                        "risk_weight": alloc["risk_weight"],
                        "priority_tier": alloc["priority_tier"],
                        "is_primary_owner": alloc["is_primary_owner"]
                    })
                    routed_streams[s_id].append(augmented_cand)

        # Sort each stream by priority tier and archetype score
        for s_id, stream in routed_streams.items():
            stream.sort(key=lambda x: (
                x["scanner_allocation"]["risk_weight"],
                x["routing_metadata"]["primary_score"]
            ), reverse=True)

        return routed_streams

    def resolve_cross_scanner_collision(self, symbol: str, triggered_scanners: List[str], classified_cand: Dict[str, Any]) -> Dict[str, Any]:
        """
        Deterministically resolves when multiple scanners trigger on the same symbol on the same session.
        """
        preferred_scan = SCANNER_MAP.get(classified_cand["primary_archetype"])
        
        if preferred_scan in triggered_scanners:
            primary_winner = preferred_scan
        else:
            primary_winner = triggered_scanners[0]

        secondary_scanners = [s for s in triggered_scanners if s != primary_winner]

        return {
            "symbol": symbol,
            "primary_execution_scanner": primary_winner,
            "confluence_confirmation_scanners": secondary_scanners,
            "primary_risk_weight": ROUTING_WEIGHTS["PRIMARY_MATCH"],
            "has_apex_confluence": len(secondary_scanners) >= 2
        }

# Global Singleton Instance for Production Call Chains
v600_router_engine = HybridRouterEngineV6()
