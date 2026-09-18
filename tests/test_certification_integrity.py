# =====================================================================================
# tests/test_certification_integrity.py
# COMPREHENSIVE CERTIFICATION INTEGRITY SUITE & CAUSAL INVARIANT AUDIT
# =====================================================================================

import os
import sys
import unittest
import math
import pandas as pd
import numpy as np

# Ensure app is on path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "app"))
sys.path.insert(0, os.path.join(BASE_DIR, "scratch"))

from regime_pattern_policy import (
    classify_pattern,
    PATTERN_STATUS,
    APPROVED_TECHNICAL_PATTERNS,
    RESEARCH_ONLY_PATTERNS,
    QUARANTINED_PATTERNS,
    evaluate_pattern_for_regime,
)
from technical_scanner import (
    MIN_RVOL_HARD_GATE,
    MIN_CLV_HARD_GATE,
    MAX_UPPER_WICK_PCT,
    detect_technical_setup,
)
from backtest_technical_scanner_v2 import (
    simulate_trade_conservative,
    compute_reconciled_metrics,
    wilson_score_interval,
)


class TestCertificationIntegritySuite(unittest.TestCase):
    """
    Automated Invariant & Data Integrity Test Suite:
    Guarantees that all institutional backtest and policy promotion rules are mechanically enforced.
    """

    def test_dual_certification_classifier_mechanics(self):
        """Validates the mechanical classifier under strict dual certification rules."""
        # 1. Wyckoff qualifies for PRODUCTION
        wyckoff_status = classify_pattern(
            oos_wr=52.7, oos_pf=1.62, hard_pf=1.55, oos_avg_r=0.26, oos_n=562
        )
        self.assertEqual(wyckoff_status, "PRODUCTION")

        # 2. Higher Low Reversal fails Hard PF threshold (< 1.50) -> RESEARCH_ONLY
        hl_status = classify_pattern(
            oos_wr=50.8, oos_pf=1.50, hard_pf=1.44, oos_avg_r=0.22, oos_n=122
        )
        self.assertEqual(hl_status, "RESEARCH_ONLY")

        # 3. Insufficient sample size (N < 25)
        small_sample_status = classify_pattern(
            oos_wr=60.0, oos_pf=2.0, hard_pf=2.0, oos_avg_r=0.5, oos_n=20
        )
        self.assertEqual(small_sample_status, "INSUFFICIENT_SAMPLE")

        # 4. Invariant violation failure
        violation_status = classify_pattern(
            oos_wr=55.0, oos_pf=1.8, hard_pf=1.8, oos_avg_r=0.3, oos_n=100, data_integrity_failed=True
        )
        self.assertEqual(violation_status, "FAILED_DATA_INTEGRITY")

    def test_conservative_intrabar_ambiguity_treated_as_loss(self):
        """Guarantees that a candle hitting both Stop Loss and Target 1 is strictly marked LOSS."""
        dates = pd.date_range("2026-01-01", periods=10, freq="B")
        # Bar 0: Entry = 100, SL = 95, T1 = 107.5
        # Bar 1: Low = 94 (SL hit), High = 108 (T1 hit) -> Intrabar Ambiguity!
        df = pd.DataFrame({
            "Open": [100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
            "High": [101.0, 108.0, 102.0, 102.0, 102.0, 102.0, 102.0, 102.0, 102.0, 102.0],
            "Low":  [99.0,  94.0,  98.0,  98.0,  98.0,  98.0,  98.0,  98.0,  98.0,  98.0],
            "Close": [100.5, 106.0, 101.0, 101.0, 101.0, 101.0, 101.0, 101.0, 101.0, 101.0],
            "Volume": [100000] * 10
        }, index=dates)

        res = simulate_trade_conservative(df, entry_idx=0, entry_price=100.0, stop_loss=95.0, target_1=107.5, max_holding_bars=20)
        self.assertEqual(res["outcome"], "LOSS")
        self.assertEqual(res["r_multiple"], -1.0)
        self.assertEqual(res["exit_reason"], "INTRABAR_AMBIGUITY_LOSS")

    def test_timeout_horizon_capped_at_20_bars(self):
        """Guarantees that trade holding period is bounded at exactly 20 bars."""
        dates = pd.date_range("2026-01-01", periods=30, freq="B")
        df = pd.DataFrame({
            "Open": [100.0] * 30,
            "High": [102.0] * 30,
            "Low":  [98.0] * 30,
            "Close": [101.0] * 30,
            "Volume": [100000] * 30
        }, index=dates)

        res = simulate_trade_conservative(df, entry_idx=0, entry_price=100.0, stop_loss=95.0, target_1=110.0, max_holding_bars=20)
        self.assertEqual(res["holding_bars"], 20)
        self.assertEqual(res["exit_reason"], "TIMEOUT_20D")

    def test_nan_safe_reconciled_metrics(self):
        """Guarantees zero NaN distortion in Gross Profit, Gross Loss, and Profit Factor calculations."""
        mock_trades = [
            {"outcome": "WIN", "r_multiple": 1.5, "holding_bars": 3},
            {"outcome": "WIN", "r_multiple": 1.5, "holding_bars": 5},
            {"outcome": "LOSS", "r_multiple": -1.0, "holding_bars": 2},
            {"outcome": "LOSS", "r_multiple": -1.0, "holding_bars": 4},
        ]
        m = compute_reconciled_metrics(mock_trades)
        self.assertEqual(m["N"], 4)
        self.assertEqual(m["Winners"], 2)
        self.assertEqual(m["Losers"], 2)
        self.assertEqual(m["WR"], 50.0)
        self.assertEqual(m["Gross_Profit_R"], 3.0)
        self.assertEqual(m["Gross_Loss_R"], 2.0)
        self.assertEqual(m["PF"], 1.5)
        self.assertEqual(m["Total_R"], 1.0)
        self.assertEqual(m["Avg_R"], 0.25)

    def test_policy_routing_quarantines_fragile_patterns(self):
        """Guarantees that all fragile patterns are strictly blocked from alert dispatch."""
        quarantined = ["BULL_FLAG", "DOUBLE_BOTTOM", "V_REVERSAL", "SHAKEOUT_RECLAIM", "BULL_PENNANT"]
        for pat in quarantined:
            eval_res = evaluate_pattern_for_regime(pat, "BULL")
            self.assertFalse(eval_res["allowed"], f"Quarantined pattern {pat} must NOT be allowed in live alerts")
            self.assertIn(pat, QUARANTINED_PATTERNS)

    def test_single_production_pattern_in_regime_policy(self):
        """Guarantees that only WYCKOFF_SPRING_TYPE_2 is in APPROVED_TECHNICAL_PATTERNS under dual rule."""
        self.assertEqual(APPROVED_TECHNICAL_PATTERNS, {"WYCKOFF_SPRING_TYPE_2"})
        self.assertEqual(PATTERN_STATUS["WYCKOFF_SPRING_TYPE_2"], "PRODUCTION")
        self.assertEqual(PATTERN_STATUS["HIGHER_LOW_REVERSAL"], "RESEARCH_ONLY")


if __name__ == "__main__":
    unittest.main()
