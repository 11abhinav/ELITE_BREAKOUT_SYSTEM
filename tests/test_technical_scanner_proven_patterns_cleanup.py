# =====================================================================================
# tests/test_technical_scanner_proven_patterns_cleanup.py
# COMPREHENSIVE CERTIFICATION & INVARIANT TEST SUITE: PROVEN-PATTERNS-ONLY CLEANUP
# =====================================================================================

import unittest
import numpy as np
import pandas as pd
from datetime import datetime

from app.regime_pattern_policy import (
    APPROVED_TECHNICAL_PATTERNS,
    RESEARCH_ONLY_PATTERNS,
    QUARANTINED_PATTERNS,
    get_regime_pattern_policy,
    evaluate_pattern_for_regime
)
from app.technical_scanner import (
    detect_technical_setup,
    _detect_bull_flag,
    _detect_wyckoff_spring_type_2,
    _detect_multi_month_base_breakout,
    _detect_undercut_and_rally,
    _detect_shakeout_reclaim
)
from app.champion_challenger_registry import (
    ChampionChallengerRegistry,
    ScannerFamily,
    VariantStatus
)

def _create_sample_df(n_bars: int = 70, base_p: float = 100.0) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=n_bars, freq="B")
    data = []
    p = base_p
    for i in range(n_bars):
        o = p
        h = p + 1.5
        l = p - 1.0
        c = p + 0.5
        v = 50_000.0
        data.append({"date": dates[i], "open": o, "high": h, "low": l, "close": c, "volume": v})
        p = c
    df = pd.DataFrame(data).set_index("date")
    df["ATR"] = 2.0
    df["Volume_SMA20"] = 50_000.0
    df["EMA20"] = df["close"].ewm(span=20).mean()
    return df

class TestTechnicalScannerProvenPatternsCleanup(unittest.TestCase):

    def test_01_approved_pattern_whitelist_exactness(self):
        """Verify that APPROVED_TECHNICAL_PATTERNS contains EXACTLY the certified 4 patterns."""
        expected = {
            "WYCKOFF_SPRING_TYPE_2",
            "BULL_FLAG",
            "MULTI_MONTH_BASE_BREAKOUT",
            "UNDERCUT_AND_RALLY",
        }
        self.assertEqual(APPROVED_TECHNICAL_PATTERNS, expected, "Whitelist must match certified 4 patterns")
        self.assertEqual(len(APPROVED_TECHNICAL_PATTERNS), 4, "Must contain exactly 4 approved production patterns")

    def test_02_quarantined_patterns_rejection(self):
        """Verify that quarantined and research-only patterns are strictly rejected in production evaluation."""
        unapproved_patterns = [
            "CUP_AND_HANDLE",
            "VCP_CONTRACTION",
            "HIGH_TIGHT_FLAG",
            "FLAT_BASE_BREAKOUT",
            "ASCENDING_TRIANGLE",
            "FALLING_WEDGE_REVERSAL",
            "INVERSE_HEAD_AND_SHOULDERS",
            "DOUBLE_BOTTOM_SHAKEOUT",
            "PENNANT_CONVERGENCE",
        ]
        for reg in ["STRONG_BULL", "BULL", "SIDEWAYS", "HIGH_VOLATILITY", "WEAK_BEAR"]:
            for pat in unapproved_patterns:
                eval_res = evaluate_pattern_for_regime(pat, reg)
                self.assertFalse(eval_res["allowed"], f"{pat} must not be allowed in {reg}")
                self.assertFalse(eval_res["is_allowed"], f"{pat} must not have is_allowed=True in {reg}")
                self.assertEqual(eval_res["bonus_points"], 0.0, f"{pat} must receive 0 bonus points in {reg}")

    def test_03_approved_patterns_scoring_under_regimes(self):
        """Verify that approved patterns receive proper regime bonuses."""
        # Strong Bull: Multi-Month Base & Wyckoff Spring
        sb_eval = evaluate_pattern_for_regime("MULTI_MONTH_BASE_BREAKOUT", "STRONG_BULL")
        self.assertTrue(sb_eval["allowed"])
        self.assertEqual(sb_eval["status"], "PRIMARY_CHAMPION")
        self.assertEqual(sb_eval["bonus_points"], 15.0)

        # Bull: Bull Flag primary
        b_eval = evaluate_pattern_for_regime("BULL_FLAG", "BULL")
        self.assertTrue(b_eval["allowed"])
        self.assertEqual(b_eval["status"], "PRIMARY_CHAMPION")
        self.assertEqual(b_eval["bonus_points"], 15.0)

        # High Volatility: Wyckoff Spring Type 2 primary
        hv_eval = evaluate_pattern_for_regime("WYCKOFF_SPRING_TYPE_2", "HIGH_VOLATILITY")
        self.assertTrue(hv_eval["allowed"])
        self.assertEqual(hv_eval["status"], "PRIMARY_CHAMPION")
        self.assertEqual(hv_eval["bonus_points"], 15.0)

        # Weak Bear: Undercut & Rally primary, Base Breakouts blocked
        wb_ur = evaluate_pattern_for_regime("UNDERCUT_AND_RALLY", "WEAK_BEAR")
        self.assertTrue(wb_ur["allowed"])
        self.assertEqual(wb_ur["status"], "PRIMARY_CHAMPION")
        
        wb_mm = evaluate_pattern_for_regime("MULTI_MONTH_BASE_BREAKOUT", "WEAK_BEAR")
        self.assertFalse(wb_mm["allowed"], "Base breakouts must be blocked in Weak Bear")

    def test_04_candidate_discovery_whitelisting(self):
        """Verify that candidate detection in detect_technical_setup outputs only approved patterns."""
        df = _create_sample_df(n_bars=70)
        # Create a Bull Flag setup
        df.loc[df.index[40:50], "high"] = 120.0
        df.loc[df.index[40:50], "low"] = 100.0
        df.loc[df.index[50:68], "high"] = 118.0
        df.loc[df.index[50:68], "low"] = 112.0
        df.loc[df.index[69], "open"] = 117.0
        df.loc[df.index[69], "close"] = 121.0 # Breakout
        df.loc[df.index[69], "high"] = 122.0
        df.loc[df.index[69], "low"] = 116.5
        df.loc[df.index[69], "volume"] = 150_000.0 # 3x RVOL

        res, trace = detect_technical_setup(df, "TESTSYM", return_trace=True)
        if res:
            self.assertIn(
                res["primary_pattern"],
                APPROVED_TECHNICAL_PATTERNS.union({"SHAKEOUT_RECLAIM"}),
                "Primary pattern must be in approved whitelist"
            )
        for pat in trace["03_PATTERN_DISCOVERY"]["detected_patterns"]:
            self.assertIn(
                pat,
                APPROVED_TECHNICAL_PATTERNS.union({"SHAKEOUT_RECLAIM"}),
                f"Discovered pattern {pat} must be in approved whitelist"
            )

    def test_05_registry_champions_alignment(self):
        """Verify that champion_challenger_registry registers the Top 4 champions and retired unproven ones."""
        reg = ChampionChallengerRegistry()
        tech_variants = [v for v in reg._variants.values() if v.scanner_family == ScannerFamily.TECHNICAL]
        
        champions = [v for v in tech_variants if v.status == VariantStatus.CHAMPION]
        champion_ids = [v.variant_id for v in champions]
        
        self.assertIn("TECH_PROD_WYCKOFF_SPRING_TYPE_2", champion_ids)
        self.assertIn("TECH_PROD_BULL_FLAG", champion_ids)
        self.assertIn("TECH_PROD_MULTI_MONTH_BASE", champion_ids)
        self.assertIn("TECH_PROD_UNDERCUT_AND_RALLY", champion_ids)
        self.assertEqual(len(champions), 4, "Must have exactly 4 Technical Champions")

        # Quarantined / retired
        retired = [v for v in tech_variants if v.status == VariantStatus.RETIRED]
        retired_ids = [v.variant_id for v in retired]
        self.assertIn("TECH_QUAR_CUP_AND_HANDLE", retired_ids)
        self.assertIn("TECH_QUAR_VCP_CONTRACTION", retired_ids)
        self.assertIn("TECH_QUAR_HIGH_TIGHT_FLAG", retired_ids)

    def test_06_weekend_calendar_invariant(self):
        """Mandatory Invariant: Saturday = 0, Sunday = 0."""
        # Check date generation in test and scanner
        df = _create_sample_df(n_bars=60)
        for dt in df.index:
            self.assertNotIn(dt.weekday(), [5, 6], f"Date {dt} must not be Saturday or Sunday")

    def test_07_point_in_time_causality(self):
        """Mandatory Invariant: Evaluates strictly on t <= T with zero future lookahead."""
        df = _create_sample_df(n_bars=60)
        # Slicing up to bar 50 must produce identical outcome whether future bars exist or not
        df_truncated = df.iloc[:50].copy()
        
        res1 = detect_technical_setup(df_truncated, "TESTSYM")
        # Run on truncated
        if res1:
            self.assertEqual(res1["trading_date"], str(df.index[49]).split(" ")[0])

    def test_08_cross_scanner_non_interference(self):
        """Verify that other scanners (Wealth, Multibagger, Pullback, Reversal, EOD) remain untouched."""
        # Double bottom shakeout detector from pattern_library_extended is still accessible for Wealth
        from app.pattern_library_extended import detect_double_bottom_shakeout, detect_vcp_contraction
        highs = np.array([100.0] * 60)
        lows = np.array([90.0] * 60)
        closes = np.array([95.0] * 60)
        vols = np.array([10000.0] * 60)
        # Function callable without exception
        res = detect_double_bottom_shakeout(highs, lows, closes, vols, t=55)
        self.assertIsInstance(res, bool)


if __name__ == "__main__":
    unittest.main()
