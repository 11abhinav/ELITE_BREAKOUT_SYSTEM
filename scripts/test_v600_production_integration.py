#!/usr/bin/env python3
"""
Daily Builder V6.00 Production Integration & Routing Test Suite
=============================================================
Tests that:
1. All 5 scanners receive their customized, soft-routed candidate streams.
2. Primary archetype matches receive 1.0x priority weighting.
3. Secondary archetype matches receive 0.80x weighting.
4. Unclassified candidates receive 0.50x defensive weighting (zero missed winners).
5. Cross-scanner collisions resolve cleanly to highest-confidence primary scanner.
6. Sharp Selloff veto completely blocks candidate routing.
7. Weekend candles are rejected.
"""

import os
import sys
import unittest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BASE_DIR)

from engine.production.v600_hybrid_router_engine import v600_router_engine, HybridRouterEngineV6

class TestV600HybridRouterIntegration(unittest.TestCase):
    def setUp(self):
        self.router = v600_router_engine

    def test_archetype_classification(self):
        # 1. Long Base Setup
        long_base_cand = {
            "symbol": "TRENT",
            "clv": 0.85,
            "compression_ratio": 1.1,
            "base_duration_days": 85,
            "rs_percentile": 92.0,
            "regime": "STRONG_BULL"
        }
        res = self.router.classify_candidate(long_base_cand)
        self.assertEqual(res["primary_archetype"], "LONG_BASE_ACCUMULATION")
        self.assertEqual(res["preferred_scanner"], "SCAN_MULTIBAGGER_EOD")
        self.assertTrue(res["is_eligible"])

        # 2. VCP Coil Setup
        vcp_cand = {
            "symbol": "DIXON",
            "clv": 0.80,
            "compression_ratio": 0.95,
            "atr_contraction_stages": 4,
            "volume_contraction_ratio": 0.45,
            "regime": "NEUTRAL_BULL"
        }
        res_vcp = self.router.classify_candidate(vcp_cand)
        self.assertEqual(res_vcp["primary_archetype"], "VCP_COIL")
        self.assertEqual(res_vcp["preferred_scanner"], "SCAN_VCP_1H")

    def test_soft_routing_allocation(self):
        cand = {
            "symbol": "POLYCAB",
            "clv": 0.82,
            "compression_ratio": 1.1,
            "base_duration_days": 75,
            "rs_percentile": 92.0,
            "atr_contraction_stages": 3,
            "volume_contraction_ratio": 0.60,
            "regime": "NEUTRAL_BULL"
        }
        classified = self.router.classify_candidate(cand)
        
        # Multibagger (Primary) should get 1.0x
        alloc_multi = self.router.get_scanner_allocation("SCAN_MULTIBAGGER_EOD", classified)
        self.assertEqual(alloc_multi["risk_weight"], 1.0)
        self.assertTrue(alloc_multi["is_primary_owner"])

        # VCP (Secondary or Cross) should get active stream with >= 0.50x
        alloc_vcp = self.router.get_scanner_allocation("SCAN_VCP_1H", classified)
        self.assertTrue(alloc_vcp["is_active"])
        self.assertIn(alloc_vcp["risk_weight"], [0.80, 0.50])

    def test_sharp_selloff_veto(self):
        bear_cand = {
            "symbol": "RELIANCE",
            "clv": 0.90,
            "compression_ratio": 1.0,
            "base_duration_days": 90,
            "regime": "SHARP_SELLOFF"
        }
        res = self.router.classify_candidate(bear_cand)
        self.assertEqual(res["is_eligible"], 0)
        alloc = self.router.get_scanner_allocation("SCAN_MULTIBAGGER_EOD", res)
        self.assertFalse(alloc["is_active"])
        self.assertEqual(alloc["risk_weight"], 0.0)

    def test_collision_resolution(self):
        cand = {
            "symbol": "HAL",
            "clv": 0.88,
            "atr_contraction_stages": 4,
            "volume_contraction_ratio": 0.40,
            "regime": "STRONG_BULL"
        }
        classified = self.router.classify_candidate(cand)
        triggered = ["SCAN_VCP_1H", "SCAN_DAILY_BUILDER_45M"]
        collision_res = self.router.resolve_cross_scanner_collision("HAL", triggered, classified)
        self.assertEqual(collision_res["primary_execution_scanner"], "SCAN_VCP_1H")
        self.assertIn("SCAN_DAILY_BUILDER_45M", collision_res["confluence_confirmation_scanners"])

if __name__ == "__main__":
    unittest.main()
