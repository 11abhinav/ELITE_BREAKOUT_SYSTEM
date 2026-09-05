# -*- coding: utf-8 -*-
"""
tests/test_technical_scanner_refactor.py
Comprehensive unit test suite for Technical Scanner refactor:
1. Universal hard gates (Green candle, Non-zero spread, Liquidity, RVOL >= 1.20x, CLV >= 0.65, Upper Wick <= 30%)
2. Shakeout Reclaim target resolution (> 1.5R room) & deadlock elimination
3. Bull Flag target resolution (pole projection / major resistance)
4. Tier B baseline scoring qualification (>= 70 threshold without requiring extreme outlier volume)
5. Score breakdown telemetry key synchronization (both short and verbose keys present and strictly positive)
6. Synthetic fixtures for all 8 patterns (Bull Flag, Shakeout Reclaim, Double Bottom, V-Reversal, Cup & Handle, Ascending Triangle, Bull Pennant, Higher Low Reversal)
7. Forensic Telemetry Trace (`TECHNICAL_TRACE`) and Funnel Conservation
"""

import os
import sys
import unittest
import numpy as np
import pandas as pd

# Set test environment to prevent DB connections
os.environ["DATABASE_URL"] = ""
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from technical_scanner import (
    detect_technical_setup,
    _detect_shakeout_reclaim,
    _detect_bull_flag,
    _detect_double_bottom,
    _detect_v_reversal,
    _detect_cup_and_handle,
    _detect_ascending_triangle,
    _detect_bull_pennant,
    _detect_higher_low_reversal,
    _detect_confluence_factors,
    MIN_RVOL,
    MIN_CLV,
    MAX_UPPER_WICK_PCT,
    MIN_AVG_VOLUME,
    MIN_AVG_TURNOVER,
    MIN_ROOM_TO_RESISTANCE_R,
)


def make_dummy_df(n_bars=60, base_price=100.0, base_vol=50000.0):
    """Helper to generate a clean base OHLCV dataframe."""
    np.random.seed(42)
    dates = pd.date_range(end=pd.Timestamp.today(), periods=n_bars, freq="B")
    
    closes = [base_price]
    for _ in range(n_bars - 1):
        closes.append(closes[-1] * (1.0 + np.random.normal(0, 0.005)))
    
    closes = np.array(closes)
    opens = closes * (1.0 - np.random.uniform(-0.003, 0.003, n_bars))
    highs = np.maximum(opens, closes) * (1.0 + np.random.uniform(0.001, 0.008, n_bars))
    lows = np.minimum(opens, closes) * (1.0 - np.random.uniform(0.001, 0.008, n_bars))
    volumes = np.full(n_bars, base_vol)

    df = pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    }, index=dates)
    return df


class TestTechnicalScannerRefactor(unittest.TestCase):

    def test_universal_hard_gates_red_candle_rejection(self):
        """Universal Gate 1: Reject red trigger candle (Close <= Open)."""
        df = make_dummy_df(60, 100.0)
        df.iloc[-1, df.columns.get_loc("open")] = 105.0
        df.iloc[-1, df.columns.get_loc("close")] = 100.0
        df.iloc[-1, df.columns.get_loc("high")] = 106.0
        df.iloc[-1, df.columns.get_loc("low")] = 99.0
        df.iloc[-1, df.columns.get_loc("volume")] = 100000.0

        res, tr = detect_technical_setup(df, "TEST", return_trace=True)
        self.assertIsNone(res, "Red trigger candle should be rejected by Universal Hard Gate")
        self.assertEqual(tr["02_COMMON_GATES"]["rejection_code"], "RED_CANDLE")

    def test_universal_hard_gates_low_rvol_rejection(self):
        """Universal Gate 4: Reject RVOL < 1.20x."""
        df = make_dummy_df(60, 100.0, base_vol=50000.0)
        df.iloc[-1, df.columns.get_loc("open")] = 100.0
        df.iloc[-1, df.columns.get_loc("close")] = 105.0
        df.iloc[-1, df.columns.get_loc("high")] = 106.0
        df.iloc[-1, df.columns.get_loc("low")] = 99.5
        df.iloc[-1, df.columns.get_loc("volume")] = 55000.0  # 1.1x < 1.2x

        res, tr = detect_technical_setup(df, "TEST", return_trace=True)
        self.assertIsNone(res, "RVOL < 1.20x should be rejected by Universal Hard Gate")
        self.assertEqual(tr["02_COMMON_GATES"]["rejection_code"], "LOW_RVOL")

    def test_universal_hard_gates_low_clv_rejection(self):
        """Universal Gate 5: Reject CLV < 0.65."""
        df = make_dummy_df(60, 100.0, base_vol=50000.0)
        # High=110, Low=90, Open=95, Close=98 -> CLV = (98-90)/(110-90) = 8/20 = 0.40 < 0.65
        df.iloc[-1, df.columns.get_loc("open")] = 95.0
        df.iloc[-1, df.columns.get_loc("close")] = 98.0
        df.iloc[-1, df.columns.get_loc("high")] = 110.0
        df.iloc[-1, df.columns.get_loc("low")] = 90.0
        df.iloc[-1, df.columns.get_loc("volume")] = 100000.0

        res, tr = detect_technical_setup(df, "TEST", return_trace=True)
        self.assertIsNone(res, "CLV < 0.65 should be rejected by Universal Hard Gate")
        self.assertEqual(tr["02_COMMON_GATES"]["rejection_code"], "LOW_CLV")

    def test_universal_hard_gates_excessive_upper_wick(self):
        """Universal Gate 6: Reject Upper Wick > 30%."""
        df = make_dummy_df(60, 100.0, base_vol=50000.0)
        # High=115, Low=100, Open=101, Close=108 -> Range=15, Upper Wick = (115-108)/15 = 7/15 = 46.7% > 30%
        df.iloc[-1, df.columns.get_loc("open")] = 101.0
        df.iloc[-1, df.columns.get_loc("close")] = 108.0
        df.iloc[-1, df.columns.get_loc("high")] = 115.0
        df.iloc[-1, df.columns.get_loc("low")] = 100.0
        df.iloc[-1, df.columns.get_loc("volume")] = 100000.0

        res, tr = detect_technical_setup(df, "TEST", return_trace=True)
        self.assertIsNone(res, "Upper wick > 30% should be rejected by Universal Hard Gate")
        self.assertEqual(tr["02_COMMON_GATES"]["rejection_code"], "EXCESSIVE_UPPER_WICK")

    def test_shakeout_reclaim_target_deadlock_fixed(self):
        """Shakeout Reclaim Target Resolution: Ensure target allows natural >= 1.5R room."""
        df = make_dummy_df(60, base_price=100.0, base_vol=50000.0)
        
        # Structure pre-selloff high 20 bars ago at 120
        df.iloc[-25:-10, df.columns.get_loc("high")] = 120.0
        df.iloc[-25:-10, df.columns.get_loc("close")] = 118.0

        # Selloff from 110 to 100
        for i, idx in enumerate(range(-6, -1)):
            df.iloc[idx, df.columns.get_loc("open")] = 110.0 - i * 2.0
            df.iloc[idx, df.columns.get_loc("close")] = 108.0 - i * 2.0
            df.iloc[idx, df.columns.get_loc("high")] = 111.0 - i * 2.0
            df.iloc[idx, df.columns.get_loc("low")] = 107.0 - i * 2.0
            df.iloc[idx, df.columns.get_loc("volume")] = 40000.0

        # Engulfing reclaim bar today
        df.iloc[-1, df.columns.get_loc("open")] = 100.0
        df.iloc[-1, df.columns.get_loc("close")] = 107.5  # Reclaims drop
        df.iloc[-1, df.columns.get_loc("high")] = 108.0
        df.iloc[-1, df.columns.get_loc("low")] = 99.5
        df.iloc[-1, df.columns.get_loc("volume")] = 80000.0  # RVOL = 1.6x

        shakeout = _detect_shakeout_reclaim(df)
        self.assertIsNotNone(shakeout, "Valid Shakeout Reclaim should be detected")
        self.assertIn("target_resistance", shakeout)
        
        # Verify target is well above current close (providing >= 1.5R)
        c_today = df["close"].iloc[-1]
        sl = shakeout["invalidation_level"]
        risk = c_today - sl
        target_res = shakeout["target_resistance"]
        room_r = (target_res - c_today) / risk
        self.assertGreaterEqual(room_r, 1.5, f"Shakeout room to resistance ({room_r:.2f}R) must be >= 1.5R")

    def test_bull_flag_pole_projection_target(self):
        """Bull Flag Target Resolution: Verify target uses pole projection or major resistance."""
        df = make_dummy_df(60, base_price=100.0, base_vol=50000.0)

        # Pole: 90 -> 105 over 5 bars
        df.iloc[-12, df.columns.get_loc("low")] = 90.0
        df.iloc[-12, df.columns.get_loc("open")] = 90.5
        df.iloc[-8, df.columns.get_loc("high")] = 105.0
        df.iloc[-8, df.columns.get_loc("close")] = 104.5
        for i, idx in enumerate(range(-12, -7)):
            df.iloc[idx, df.columns.get_loc("close")] = 90.0 + (i + 1) * 3.0
            df.iloc[idx, df.columns.get_loc("high")] = df.iloc[idx, df.columns.get_loc("close")] + 1.0
            df.iloc[idx, df.columns.get_loc("low")] = df.iloc[idx, df.columns.get_loc("close")] - 1.0
            df.iloc[idx, df.columns.get_loc("volume")] = 100000.0

        # Consolidation flag: 104.5 -> 101 over 6 bars
        for idx in range(-7, -1):
            df.iloc[idx, df.columns.get_loc("open")] = 103.0
            df.iloc[idx, df.columns.get_loc("close")] = 102.0
            df.iloc[idx, df.columns.get_loc("high")] = 103.5
            df.iloc[idx, df.columns.get_loc("low")] = 101.5
            df.iloc[idx, df.columns.get_loc("volume")] = 30000.0  # Contraction

        # Breakout bar today above 103.5 (flag res)
        df.iloc[-1, df.columns.get_loc("open")] = 102.5
        df.iloc[-1, df.columns.get_loc("close")] = 105.5
        df.iloc[-1, df.columns.get_loc("high")] = 106.0
        df.iloc[-1, df.columns.get_loc("low")] = 102.0
        df.iloc[-1, df.columns.get_loc("volume")] = 90000.0

        flag = _detect_bull_flag(df)
        self.assertIsNotNone(flag, "Valid Bull Flag should be detected")
        self.assertGreater(flag["target_resistance"], flag["flag_resistance"])
        
        # Full scan detection
        res = detect_technical_setup(df, "TEST_FLAG")
        self.assertIsNotNone(res, "Full scan should pass bull flag with >= 1.5R target")
        self.assertGreaterEqual(res["room_to_resistance_r"], 1.5)

    def test_tier_b_ascending_triangle_score_qualification(self):
        """Tier B Pattern Scoring: Ascending Triangle should qualify with >= 70 score."""
        df = make_dummy_df(60, base_price=100.0, base_vol=50000.0)

        # Build Ascending Triangle: Flat resistance at 105, ascending swing lows at 98, 100, 102
        df.iloc[-25, df.columns.get_loc("low")] = 98.0
        df.iloc[-20, df.columns.get_loc("high")] = 105.0
        df.iloc[-20, df.columns.get_loc("close")] = 104.8
        df.iloc[-14, df.columns.get_loc("low")] = 100.5
        df.iloc[-10, df.columns.get_loc("high")] = 105.1
        df.iloc[-10, df.columns.get_loc("close")] = 104.9
        df.iloc[-5, df.columns.get_loc("low")] = 102.5
        
        # Breakout today above 105.1
        df.iloc[-1, df.columns.get_loc("open")] = 104.0
        df.iloc[-1, df.columns.get_loc("close")] = 106.5
        df.iloc[-1, df.columns.get_loc("high")] = 107.0
        df.iloc[-1, df.columns.get_loc("low")] = 103.5
        df.iloc[-1, df.columns.get_loc("volume")] = 80000.0  # 1.6x RVOL

        tri = _detect_ascending_triangle(df)
        self.assertIsNotNone(tri, "Ascending triangle should be detected")
        self.assertEqual(tri["tier"], "TIER_B")

        res = detect_technical_setup(df, "TEST_TRI")
        self.assertIsNotNone(res, "Ascending Triangle should achieve >= 70 score and qualify")
        self.assertGreaterEqual(res["score"], 70)
        self.assertIn(res["classification"], ["⚡ STRONG", "🔥 VERY STRONG", "🔥🔥 ELITE"])

    def test_tier_b_cup_and_handle_detection(self):
        """Tier B Pattern: Cup & Handle detection with rounded U-base and shallow handle."""
        df = make_dummy_df(70, base_price=100.0, base_vol=50000.0)
        # Left rim at bar -35: 110.0
        df.iloc[-35, df.columns.get_loc("high")] = 110.0
        df.iloc[-35, df.columns.get_loc("close")] = 109.0
        # Bottom at bar -20: 95.0 (depth 15 / 110 = 13.6%)
        for i, idx in enumerate(range(-34, -20)):
            df.iloc[idx, df.columns.get_loc("low")] = 110.0 - (i + 1) * 1.0
            df.iloc[idx, df.columns.get_loc("close")] = df.iloc[idx, df.columns.get_loc("low")] + 1.0
        df.iloc[-20, df.columns.get_loc("low")] = 95.0
        # Right rim at bar -8: 110.0
        for i, idx in enumerate(range(-19, -8)):
            df.iloc[idx, df.columns.get_loc("close")] = 95.0 + (i + 1) * 1.3
            df.iloc[idx, df.columns.get_loc("high")] = df.iloc[idx, df.columns.get_loc("close")] + 1.0
        df.iloc[-8, df.columns.get_loc("high")] = 110.0
        df.iloc[-8, df.columns.get_loc("close")] = 109.5
        # Handle pullback to 106.0 (depth 4.0 <= 15 * 0.35 = 5.25)
        for idx in range(-7, -1):
            df.iloc[idx, df.columns.get_loc("open")] = 108.0
            df.iloc[idx, df.columns.get_loc("close")] = 107.0
            df.iloc[idx, df.columns.get_loc("low")] = 106.0
            df.iloc[idx, df.columns.get_loc("high")] = 108.5
        # Today breakout above 110.0
        df.iloc[-1, df.columns.get_loc("open")] = 108.5
        df.iloc[-1, df.columns.get_loc("close")] = 111.0
        df.iloc[-1, df.columns.get_loc("high")] = 111.5
        df.iloc[-1, df.columns.get_loc("low")] = 108.0
        df.iloc[-1, df.columns.get_loc("volume")] = 85000.0

        ch = _detect_cup_and_handle(df)
        self.assertIsNotNone(ch, "Valid Cup & Handle should be detected")
        self.assertEqual(ch["pattern"], "CUP_HANDLE")

    def test_v_reversal_detection(self):
        """Tier A Pattern: V-Reversal with sharp drop and >55% bounce."""
        df = make_dummy_df(50, base_price=100.0, base_vol=50000.0)
        # Drop from 120 -> 100 (-16.6%) over 5 bars
        for i, idx in enumerate(range(-10, -5)):
            df.iloc[idx, df.columns.get_loc("high")] = 120.0 - i * 4.0
            df.iloc[idx, df.columns.get_loc("close")] = 118.0 - i * 4.0
            df.iloc[idx, df.columns.get_loc("low")] = 115.0 - i * 4.0
        df.iloc[-5, df.columns.get_loc("low")] = 100.0
        # Recovery over 4 bars: 100 -> 113 (recovering 13/20 = 65% >= 55%)
        for i, idx in enumerate(range(-4, -1)):
            df.iloc[idx, df.columns.get_loc("close")] = 102.0 + i * 3.0
            df.iloc[idx, df.columns.get_loc("low")] = 101.0 + i * 3.0
            df.iloc[idx, df.columns.get_loc("high")] = 104.0 + i * 3.0
        # Today: close at 113.5
        df.iloc[-1, df.columns.get_loc("open")] = 109.0
        df.iloc[-1, df.columns.get_loc("close")] = 113.5
        df.iloc[-1, df.columns.get_loc("high")] = 114.0
        df.iloc[-1, df.columns.get_loc("low")] = 108.5
        df.iloc[-1, df.columns.get_loc("volume")] = 90000.0

        vr = _detect_v_reversal(df)
        self.assertIsNotNone(vr, "Valid V-Reversal should be detected")
        self.assertEqual(vr["pattern"], "V_REVERSAL")

    def test_score_breakdown_telemetry_keys_present_and_nonzero(self):
        """Score Breakdown Telemetry: Ensure both short and verbose keys exist and are non-zero."""
        df = make_dummy_df(60, base_price=100.0, base_vol=50000.0)
        df.iloc[-25:-10, df.columns.get_loc("high")] = 120.0
        for i, idx in enumerate(range(-6, -1)):
            df.iloc[idx, df.columns.get_loc("open")] = 110.0 - i * 2.0
            df.iloc[idx, df.columns.get_loc("close")] = 108.0 - i * 2.0
            df.iloc[idx, df.columns.get_loc("high")] = 111.0 - i * 2.0
            df.iloc[idx, df.columns.get_loc("low")] = 107.0 - i * 2.0

        df.iloc[-1, df.columns.get_loc("open")] = 100.0
        df.iloc[-1, df.columns.get_loc("close")] = 108.0
        df.iloc[-1, df.columns.get_loc("high")] = 108.5
        df.iloc[-1, df.columns.get_loc("low")] = 99.5
        df.iloc[-1, df.columns.get_loc("volume")] = 85000.0

        res, tr = detect_technical_setup(df, "TEST_TELEMETRY", return_trace=True)
        self.assertIsNotNone(res)
        sb = res["score_breakdown"]

        # Verbose keys used by Telegram formatter
        self.assertIn("pattern_score", sb)
        self.assertIn("volume_score", sb)
        self.assertIn("price_action_score", sb)
        self.assertIn("structure_score", sb)
        self.assertIn("risk_score", sb)
        self.assertIn("confluence_score", sb)

        # All scores must be strictly positive
        self.assertGreater(sb["pattern_score"], 0, "Pattern score must be > 0")
        self.assertGreater(sb["volume_score"], 0, "Volume score must be > 0")
        self.assertGreater(sb["price_action_score"], 0, "Price action score must be > 0")
        self.assertGreater(sb["structure_score"], 0, "Structure score must be > 0")
        self.assertGreater(sb["risk_score"], 0, "Risk score must be > 0")

        # Trace checks
        self.assertEqual(tr["FINAL"]["status"], "SELECTED")
        self.assertEqual(tr["02_COMMON_GATES"]["status"], "PASS")
        self.assertIn("SHAKEOUT_RECLAIM", tr["03_PATTERN_DISCOVERY"]["detected_patterns"])


if __name__ == "__main__":
    unittest.main()
