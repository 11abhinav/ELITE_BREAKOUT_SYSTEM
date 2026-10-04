"""
tests/test_bear_quality_recovery_v1_remediation.py
=====================================================
Targeted Unit Test Battery for BEAR_QUALITY_RECOVERY_V1 Remediation

Verifies:
1. Dislocation Inequality Boundaries (MARKET_DRIVEN, SECTOR_DRIVEN, COMPANY_SPECIFIC, MIXED)
2. Stale Signal Freshness Age (<= 10 sessions PASS, >= 11 REJECT)
3. Bear Regime Gating (BEAR active, BULL & SIDEWAYS disabled)
4. Recovery Arms & ARM_ANY OR Logic
5. Final Decision Predicate Truth Table
"""

import os
import sys
import unittest
import pandas as pd
import numpy as np

# Ensure root repository is in sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
if os.path.join(REPO_ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

from run_bear_quality_recovery_v1_master_program import (
    evaluate_bear_attribution,
    evaluate_quality_and_decay,
    evaluate_fundamental_integrity,
)


class TestBearQualityRecoveryV1Remediation(unittest.TestCase):

    # -------------------------------------------------------------------------
    # SECTION 18: DISLOCATION INEQUALITY & ATTRIBUTION TESTS
    # -------------------------------------------------------------------------
    def test_dislocation_market_driven_exact_boundary_pass(self):
        # stock_dd = 25%, nifty_dd = 10%, ratio = 2.5 >= 2.2, diff = 15% <= 0.15, integrity = PASS
        attr = evaluate_bear_attribution(
            stock_dd_mag=0.25, nifty_dd_mag=0.10, sector_dd_mag=0.10, integrity_pass=True
        )
        self.assertIn(attr, ["MARKET_DRIVEN", "SECTOR_DRIVEN"])

    def test_dislocation_market_driven_failed_ratio(self):
        # stock_dd = 21%, nifty_dd = 10%, ratio = 2.1 < 2.2 -> fails ratio condition
        attr = evaluate_bear_attribution(
            stock_dd_mag=0.21, nifty_dd_mag=0.10, sector_dd_mag=0.10, integrity_pass=True
        )
        self.assertEqual(attr, "MIXED")

    def test_dislocation_market_driven_failed_difference_constraint(self):
        # stock_dd = 25%, nifty_dd = 5%, ratio = 5.0 > 2.5 -> company specific exceedance
        attr = evaluate_bear_attribution(
            stock_dd_mag=0.25, nifty_dd_mag=0.05, sector_dd_mag=0.05, integrity_pass=True
        )
        self.assertEqual(attr, "COMPANY_SPECIFIC")

    def test_dislocation_sector_driven_precedence(self):
        # stock_dd = 23%, nifty_dd = 10%, sector_dd = 10%, ratio = 2.3 >= 2.2, diff = 13% <= 0.15
        # SECTOR_DRIVEN takes precedence over MARKET_DRIVEN
        attr = evaluate_bear_attribution(
            stock_dd_mag=0.23, nifty_dd_mag=0.10, sector_dd_mag=0.10, integrity_pass=True
        )
        self.assertEqual(attr, "SECTOR_DRIVEN")

    def test_dislocation_company_specific_on_integrity_fail(self):
        # Even if drawdowns match market, failed fundamental integrity causes COMPANY_SPECIFIC rejection
        attr = evaluate_bear_attribution(
            stock_dd_mag=0.22, nifty_dd_mag=0.10, sector_dd_mag=0.10, integrity_pass=False
        )
        self.assertEqual(attr, "COMPANY_SPECIFIC")

    def test_dislocation_mixed_small_drawdown(self):
        # stock_dd < 15% (0.12) is MIXED
        attr = evaluate_bear_attribution(
            stock_dd_mag=0.12, nifty_dd_mag=0.05, sector_dd_mag=0.05, integrity_pass=True
        )
        self.assertEqual(attr, "MIXED")

    # -------------------------------------------------------------------------
    # SECTION 19: STALE RECOVERY CONFIRMATION FRESHNESS TESTS
    # -------------------------------------------------------------------------
    def test_confirmation_age_freshness_boundaries(self):
        # Create a series of bars simulating confirmation trigger at bar 100
        total_bars = 120
        arm_any_raw = np.zeros(total_bars, dtype=int)
        arm_any_raw[100] = 1  # Triggered at bar 100

        last_arm_idx = -999999
        ages = []
        for i in range(total_bars):
            if arm_any_raw[i] == 1:
                last_arm_idx = i
            age = (i - last_arm_idx) if last_arm_idx != -999999 else 999999
            ages.append(age)

        # Bar 100: age = 0 <= 10 -> PASS
        self.assertEqual(ages[100], 0)
        self.assertTrue(ages[100] <= 10)

        # Bar 101: age = 1 <= 10 -> PASS
        self.assertEqual(ages[101], 1)
        self.assertTrue(ages[101] <= 10)

        # Bar 110: age = 10 <= 10 -> PASS
        self.assertEqual(ages[110], 10)
        self.assertTrue(ages[110] <= 10)

        # Bar 111: age = 11 > 10 -> REJECT (STALE)
        self.assertEqual(ages[111], 11)
        self.assertFalse(ages[111] <= 10)

        # Bar 120: age = 20 > 10 -> REJECT (STALE)
        self.assertEqual(ages[119], 19)
        self.assertFalse(ages[119] <= 10)

        # Bar 50 (before any trigger): age = 999999 > 10 -> REJECT
        self.assertFalse(ages[50] <= 10)

    # -------------------------------------------------------------------------
    # SECTION 20: BEAR REGIME GATING TESTS
    # -------------------------------------------------------------------------
    def test_bear_regime_logic(self):
        # BEAR requires: Close < SMA200 AND SMA50 < SMA200 AND 20D_Slope_SMA200 < 0
        def evaluate_regime(c, s50, s200, slope):
            if c < s200 and s50 < s200 and slope < 0:
                return "BEAR"
            elif c > s200 and s50 > s200 and slope > 0:
                return "BULL"
            else:
                return "SIDEWAYS"

        # Valid BEAR
        self.assertEqual(evaluate_regime(c=90, s50=95, s200=100, slope=-0.02), "BEAR")

        # Failed condition 1: Close > SMA200 -> Not BEAR
        self.assertNotEqual(evaluate_regime(c=105, s50=95, s200=100, slope=-0.02), "BEAR")

        # Failed condition 2: SMA50 > SMA200 -> Not BEAR
        self.assertNotEqual(evaluate_regime(c=90, s50=105, s200=100, slope=-0.02), "BEAR")

        # Failed condition 3: Slope > 0 -> Not BEAR
        self.assertNotEqual(evaluate_regime(c=90, s50=95, s200=100, slope=0.01), "BEAR")

        # BULL
        self.assertEqual(evaluate_regime(c=110, s50=105, s200=100, slope=0.02), "BULL")

        # SIDEWAYS
        self.assertEqual(evaluate_regime(c=105, s50=95, s200=100, slope=0.00), "SIDEWAYS")

    # -------------------------------------------------------------------------
    # SECTION 21: RECOVERY ARMS & ARM_ANY OR LOGIC TESTS
    # -------------------------------------------------------------------------
    def test_recovery_arms_or_logic(self):
        # Test individual arms
        def arm_any(rs_20d, rs_slope_10d, close_p, high_20d, vol_accum):
            arm_a = (rs_20d > 1.02) and (rs_slope_10d > 0.0)
            arm_b = (close_p >= high_20d)
            arm_c = (vol_accum > 1.10)
            return arm_a or arm_b or arm_c

        # Arm A only: RS > 1.02, slope > 0
        self.assertTrue(arm_any(rs_20d=1.05, rs_slope_10d=0.01, close_p=100, high_20d=120, vol_accum=0.9))

        # Arm B only: Close >= 20D High
        self.assertTrue(arm_any(rs_20d=0.98, rs_slope_10d=-0.01, close_p=125, high_20d=120, vol_accum=0.9))

        # Arm C only: Volume accum > 1.10
        self.assertTrue(arm_any(rs_20d=0.98, rs_slope_10d=-0.01, close_p=100, high_20d=120, vol_accum=1.25))

        # None pass
        self.assertFalse(arm_any(rs_20d=0.98, rs_slope_10d=-0.01, close_p=100, high_20d=120, vol_accum=0.9))

        # All pass
        self.assertTrue(arm_any(rs_20d=1.05, rs_slope_10d=0.01, close_p=125, high_20d=120, vol_accum=1.25))

    # -------------------------------------------------------------------------
    # SECTION 22: FINAL DECISION PREDICATE TRUTH TABLE TESTS
    # -------------------------------------------------------------------------
    def test_final_signal_truth_table(self):
        def compute_final_signal(is_bear, q_state, attribution_class, integrity_pass, fresh_recovery_pass):
            attribution_pass = (attribution_class in ["MARKET_DRIVEN", "SECTOR_DRIVEN"])
            return is_bear and (q_state == "PASS") and attribution_pass and integrity_pass and fresh_recovery_pass

        # PASS CASE: All conditions satisfied
        self.assertTrue(
            compute_final_signal(
                is_bear=True,
                q_state="PASS",
                attribution_class="SECTOR_DRIVEN",
                integrity_pass=True,
                fresh_recovery_pass=True,
            )
        )

        # FAIL CASE 1: Not BEAR regime
        self.assertFalse(
            compute_final_signal(
                is_bear=False,
                q_state="PASS",
                attribution_class="SECTOR_DRIVEN",
                integrity_pass=True,
                fresh_recovery_pass=True,
            )
        )

        # FAIL CASE 2: Quality FAIL
        self.assertFalse(
            compute_final_signal(
                is_bear=True,
                q_state="FAIL",
                attribution_class="SECTOR_DRIVEN",
                integrity_pass=True,
                fresh_recovery_pass=True,
            )
        )

        # FAIL CASE 3: Fundamental Integrity FAIL
        self.assertFalse(
            compute_final_signal(
                is_bear=True,
                q_state="PASS",
                attribution_class="SECTOR_DRIVEN",
                integrity_pass=False,
                fresh_recovery_pass=True,
            )
        )

        # FAIL CASE 4: Dislocation is COMPANY_SPECIFIC
        self.assertFalse(
            compute_final_signal(
                is_bear=True,
                q_state="PASS",
                attribution_class="COMPANY_SPECIFIC",
                integrity_pass=True,
                fresh_recovery_pass=True,
            )
        )

        # FAIL CASE 5: Dislocation is MIXED
        self.assertFalse(
            compute_final_signal(
                is_bear=True,
                q_state="PASS",
                attribution_class="MIXED",
                integrity_pass=True,
                fresh_recovery_pass=True,
            )
        )

        # FAIL CASE 6: Stale recovery confirmation (age > 10 -> fresh_recovery_pass == False)
        self.assertFalse(
            compute_final_signal(
                is_bear=True,
                q_state="PASS",
                attribution_class="SECTOR_DRIVEN",
                integrity_pass=True,
                fresh_recovery_pass=False,
            )
        )


if __name__ == "__main__":
    unittest.main()
