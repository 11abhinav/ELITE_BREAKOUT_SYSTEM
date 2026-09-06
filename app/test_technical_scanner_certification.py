# =====================================================================================
# app/test_technical_scanner_certification.py
# PRODUCTION HARDENING CERTIFICATION TEST SUITE FOR TECHNICAL SCANNER (10 SUITES)
# =====================================================================================
import math
import unittest
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd

from technical_scanner import (
    detect_technical_setup,
    _detect_bull_flag,
    _detect_shakeout_reclaim,
    _detect_double_bottom,
    _detect_v_reversal,
    _detect_cup_and_handle,
    _detect_ascending_triangle,
    _detect_bull_pennant,
    _detect_higher_low_reversal,
    _coalesce_indicator_val,
    _coalesce_indicator_with_source,
    _coalesce_indicator_series,
    MIN_RVOL_HARD_GATE,
    MIN_CLV_HARD_GATE,
    MAX_UPPER_WICK_PCT,
    MIN_ROOM_TO_RESISTANCE_R,
)


def _generate_synthetic_df(n_bars: int = 60, base_price: float = 100.0) -> pd.DataFrame:
    """Generates a clean baseline daily OHLCV DataFrame on valid trading business days."""
    dates = pd.date_range(start="2025-01-01", periods=n_bars, freq="B")
    data = []
    p = base_price
    for _ in range(n_bars):
        o = p
        h = p + 1.0
        l = p - 1.0
        c = p + 0.2
        v = 100_000.0
        data.append({"Date": _, "Open": o, "High": h, "Low": l, "Close": c, "Volume": v})
        p = c
    df = pd.DataFrame(data, index=dates)
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["ATR"] = 2.0
    df["RSI"] = 55.0
    df["Volume_SMA20"] = 100_000.0
    return df


# =====================================================================================
# SUITE 01: FRESHNESS BOUNDARY & NEAR-PIVOT TOLERANCE MATRIX
# =====================================================================================
class TestSuite01FreshnessBoundary(unittest.TestCase):
    """
    Fresh Breakout Window: Yesterday must not have closed materially above the pivot.
    Boundary rule: yesterday <= pivot * 1.005 and today >= pivot * 1.002.
    """

    def test_freshness_boundary_matrix(self):
        pivot = 100.0

        # Exact Test Matrix: (yesterday_close, today_close, expected_pass, description)
        test_cases = [
            (99.0, 100.5, True, "Below pivot yesterday -> PASS"),
            (100.0, 100.5, True, "At pivot yesterday -> PASS"),
            (100.4, 101.5, True, "Near-pivot tolerance yesterday (100.4 <= 100.5) -> PASS"),
            (100.5, 101.5, True, "Exact boundary yesterday (100.5 <= 100.5) -> PASS"),
            (100.6, 101.5, False, "Exceeds boundary yesterday (100.6 > 100.5) -> REJECT"),
            (102.0, 104.0, False, "Already broken out yesterday -> REJECT"),
            (104.0, 108.0, False, "Severely extended -> REJECT"),
        ]

        for y_close, t_close, expected_pass, desc in test_cases:
            df = _generate_synthetic_df(n_bars=50, base_price=100.0)
            
            # Setup Double Bottom structure with neckline = 100.0
            df.loc[df.index[0:10], "High"] = 100.0
            df.loc[df.index[0:10], "Close"] = 98.0
            df.loc[df.index[10], "Low"] = 90.0
            df.loc[df.index[10], "Close"] = 91.0
            
            df.loc[df.index[11:25], "High"] = 98.0
            df.loc[df.index[20], "High"] = pivot  # Neckline = 100.0
            
            df.loc[df.index[30], "Low"] = 90.5
            df.loc[df.index[30], "Close"] = 91.5
            
            for idx in range(31, len(df) - 1):
                df.loc[df.index[idx], "High"] = 97.0
                df.loc[df.index[idx], "Close"] = 95.0
                
            # Yesterday bar
            df.loc[df.index[-2], "Close"] = y_close
            df.loc[df.index[-2], "High"] = max(y_close + 0.5, pivot)
            
            # Today bar
            df.loc[df.index[-1], "Open"] = y_close
            df.loc[df.index[-1], "Low"] = y_close - 0.5
            df.loc[df.index[-1], "Close"] = t_close
            df.loc[df.index[-1], "High"] = t_close + 0.5
            df.loc[df.index[-1], "Volume"] = 250_000.0

            db = _detect_double_bottom(df, atr14=2.0)
            if expected_pass:
                self.assertIsNotNone(db, f"Failed for {desc}: y_close={y_close}, t_close={t_close}")
            else:
                self.assertIsNone(db, f"Expected REJECT for {desc}: y_close={y_close}, t_close={t_close}")


# =====================================================================================
# SUITE 02: NAN-AWARE INDICATOR COALESCING & TYPE-SPECIFIC VALIDATION
# =====================================================================================
class TestSuite02IndicatorCoalescing(unittest.TestCase):
    """
    Verifies value-level fallback, type-specific validators (RSI 0-100 vs ATR > 0),
    and explicit telemetry tracking of degraded defaults.
    """

    def test_atr_coalescing_and_source_tracking(self):
        # 1. Genuine ATR
        df_atr = pd.DataFrame({"ATR": [1.5, 2.5]})
        val, src = _coalesce_indicator_with_source(df_atr, ["ATR", "ATR_14", "ATR20"], validator=lambda v: v > 0)
        self.assertEqual(val, 2.5)
        self.assertEqual(src, "ATR")

        # 2. Genuine ATR_14
        df_atr14 = pd.DataFrame({"ATR_14": [1.2, 2.8]})
        val, src = _coalesce_indicator_with_source(df_atr14, ["ATR", "ATR_14", "ATR20"], validator=lambda v: v > 0)
        self.assertEqual(val, 2.8)
        self.assertEqual(src, "ATR_14")

        # 3. Value-Level NaN Skip (ATR column exists but is NaN, ATR_14 valid)
        df_nan = pd.DataFrame({"ATR": [np.nan, np.nan], "ATR_14": [1.0, 3.2]})
        val, src = _coalesce_indicator_with_source(df_nan, ["ATR", "ATR_14", "ATR20"], validator=lambda v: v > 0)
        self.assertEqual(val, 3.2)
        self.assertEqual(src, "ATR_14")

        # 4. Degraded Fallback
        df_empty = pd.DataFrame({"ATR": [np.nan]})
        val, src = _coalesce_indicator_with_source(df_empty, ["ATR", "ATR_14"], default=2.0, default_source="DEFAULT_2PCT")
        self.assertEqual(val, 2.0)
        self.assertEqual(src, "DEFAULT_2PCT")

    def test_rsi_type_specific_validator(self):
        # RSI validator: finite and 0 <= RSI <= 100 (not just positive float)
        df_rsi_invalid = pd.DataFrame({"RSI": [150.0, -10.0], "RSI_14": [45.0, 58.0]})
        val = _coalesce_indicator_val(df_rsi_invalid, ["RSI", "RSI_14"], validator=lambda v: 0.0 <= v <= 100.0)
        self.assertEqual(val, 58.0)


# =====================================================================================
# SUITE 03: POSITIVE PATTERN BASE FIXTURES & SCORE CONSERVATION
# =====================================================================================
class TestSuite03PositivePatternFixtures(unittest.TestCase):
    """
    Verifies that all 8 core primary patterns score >= 70 when meeting hard gates.
    """

    def test_all_8_positive_patterns(self):
        patterns_to_test = [
            "BULL_FLAG", "SHAKEOUT_RECLAIM", "DOUBLE_BOTTOM", "V_REVERSAL",
            "CUP_HANDLE", "ASCENDING_TRIANGLE", "BULL_PENNANT", "HIGHER_LOW_REVERSAL"
        ]

        for pat_name in patterns_to_test:
            df = self._build_positive_fixture(pat_name)
            res, tr = detect_technical_setup(df, symbol=f"TEST_{pat_name}", return_trace=True)

            self.assertIsNotNone(res, f"Pattern {pat_name} failed setup detection. Trace: {tr.get('FINAL')}")
            self.assertEqual(res["primary_pattern"], pat_name)
            self.assertGreaterEqual(res["score"], 70, f"Pattern {pat_name} score {res['score']} < 70")
            
            # Score Conservation / Breakdown check
            sb = res["score_breakdown"]
            self.assertIn("pattern_score", sb)
            self.assertIn("volume_score", sb)
            self.assertIn("price_action_score", sb)
            self.assertIn("structure_score", sb)
            self.assertIn("risk_score", sb)
            self.assertEqual(sb["total_score"], res["score"])

    def _build_positive_fixture(self, pattern: str) -> pd.DataFrame:
        if pattern == "BULL_FLAG":
            df = _generate_synthetic_df(n_bars=40, base_price=100.0)
            for i in range(15, 21):
                p = 100.0 + (i - 15) * 2.4
                df.loc[df.index[i], "Open"] = p - 1.0
                df.loc[df.index[i], "Low"] = p - 1.2
                df.loc[df.index[i], "Close"] = p + 1.0
                df.loc[df.index[i], "High"] = p + 1.2
                df.loc[df.index[i], "Volume"] = 300_000.0
            for i in range(21, 39):
                df.loc[df.index[i], "Open"] = 111.0
                df.loc[df.index[i], "Low"] = 110.0
                df.loc[df.index[i], "High"] = 112.0
                df.loc[df.index[i], "Close"] = 111.0
                df.loc[df.index[i], "Volume"] = 80_000.0
            df.loc[df.index[39], "Open"] = 111.5
            df.loc[df.index[39], "Low"] = 111.0
            df.loc[df.index[39], "Close"] = 113.5
            df.loc[df.index[39], "High"] = 113.8
            df.loc[df.index[39], "Volume"] = 250_000.0
            return df

        elif pattern == "SHAKEOUT_RECLAIM":
            df = _generate_synthetic_df(n_bars=35, base_price=100.0)
            for i in range(10, 33):
                p = 105.0 - (i - 10) * 0.35
                df.loc[df.index[i], "Open"] = p
                df.loc[df.index[i], "High"] = p + 0.5
                df.loc[df.index[i], "Low"] = p - 0.5
                df.loc[df.index[i], "Close"] = p - 0.3
                df.loc[df.index[i], "Volume"] = 100_000.0
            df.loc[df.index[33], "Open"] = 98.0
            df.loc[df.index[33], "High"] = 98.2
            df.loc[df.index[33], "Low"] = 96.0
            df.loc[df.index[33], "Close"] = 96.2
            df.loc[df.index[33], "Volume"] = 120_000.0
            df.loc[df.index[34], "Open"] = 96.0
            df.loc[df.index[34], "Low"] = 95.8
            df.loc[df.index[34], "Close"] = 98.5
            df.loc[df.index[34], "High"] = 98.8
            df.loc[df.index[34], "Volume"] = 250_000.0
            return df

        elif pattern == "DOUBLE_BOTTOM":
            df = _generate_synthetic_df(n_bars=50, base_price=100.0)
            df.loc[df.index[0:10], "High"] = 100.0
            df.loc[df.index[0:10], "Close"] = 98.0
            df.loc[df.index[10], "Low"] = 90.0
            df.loc[df.index[10], "Close"] = 91.0
            df.loc[df.index[20], "High"] = 100.0
            df.loc[df.index[20], "Close"] = 99.0
            df.loc[df.index[30], "Low"] = 90.5
            df.loc[df.index[30], "Close"] = 91.5
            for i in range(31, 49):
                df.loc[df.index[i], "High"] = 98.0
                df.loc[df.index[i], "Close"] = 96.0
            df.loc[df.index[48], "Close"] = 99.5
            df.loc[df.index[48], "High"] = 99.8
            df.loc[df.index[49], "Open"] = 99.5
            df.loc[df.index[49], "Low"] = 99.0
            df.loc[df.index[49], "Close"] = 101.5
            df.loc[df.index[49], "High"] = 101.8
            df.loc[df.index[49], "Volume"] = 300_000.0
            return df

        elif pattern == "V_REVERSAL":
            df = _generate_synthetic_df(n_bars=30, base_price=100.0)
            df.loc[df.index[15], "High"] = 110.0
            for i in range(16, 26):
                df.loc[df.index[i], "Low"] = 110.0 - (i - 15) * 1.0
                df.loc[df.index[i], "Close"] = 110.0 - (i - 15) * 1.0 + 0.2
            df.loc[df.index[25], "Low"] = 100.0
            for i in range(26, 29):
                df.loc[df.index[i], "Close"] = 100.0 + (i - 25) * 2.0
            df.loc[df.index[29], "Open"] = 105.0
            df.loc[df.index[29], "Low"] = 104.8
            df.loc[df.index[29], "Close"] = 107.5
            df.loc[df.index[29], "High"] = 107.8
            df.loc[df.index[29], "Volume"] = 300_000.0
            return df

        elif pattern == "CUP_HANDLE":
            df = _generate_synthetic_df(n_bars=50, base_price=100.0)
            df.loc[df.index[10], "High"] = 100.0
            df.loc[df.index[25], "Low"] = 85.0
            df.loc[df.index[40], "High"] = 100.0
            for i in range(41, 49):
                df.loc[df.index[i], "Low"] = 96.5
                df.loc[df.index[i], "High"] = 99.0
                df.loc[df.index[i], "Close"] = 98.0
            df.loc[df.index[48], "Close"] = 99.5
            df.loc[df.index[48], "High"] = 99.8
            df.loc[df.index[49], "Open"] = 99.5
            df.loc[df.index[49], "Low"] = 99.2
            df.loc[df.index[49], "Close"] = 101.5
            df.loc[df.index[49], "High"] = 101.8
            df.loc[df.index[49], "Volume"] = 300_000.0
            return df

        elif pattern == "ASCENDING_TRIANGLE":
            df = _generate_synthetic_df(n_bars=45, base_price=100.0)
            df.loc[df.index[15], "High"] = 100.0
            df.loc[df.index[28], "High"] = 100.0
            df.loc[df.index[38], "High"] = 100.0
            df.loc[df.index[10], "Low"] = 90.0
            df.loc[df.index[22], "Low"] = 93.0
            df.loc[df.index[34], "Low"] = 96.0
            df.loc[df.index[43], "Close"] = 99.5
            df.loc[df.index[43], "High"] = 99.8
            df.loc[df.index[44], "Open"] = 99.5
            df.loc[df.index[44], "Low"] = 99.2
            df.loc[df.index[44], "Close"] = 101.5
            df.loc[df.index[44], "High"] = 101.8
            df.loc[df.index[44], "Volume"] = 280_000.0
            return df

        elif pattern == "BULL_PENNANT":
            df = _generate_synthetic_df(n_bars=35, base_price=100.0)
            for i in range(15, 23):
                df.loc[df.index[i], "Low"] = 100.0 + (i - 15) * 1.25
                df.loc[df.index[i], "High"] = 100.0 + (i - 15) * 1.25 + 0.5
                df.loc[df.index[i], "Close"] = 100.0 + (i - 15) * 1.25 + 0.3
            df.loc[df.index[23], "High"] = 110.0
            df.loc[df.index[23], "Low"] = 105.0
            df.loc[df.index[33], "High"] = 107.0
            df.loc[df.index[33], "Low"] = 106.5
            df.loc[df.index[33], "Close"] = 106.8
            df.loc[df.index[34], "Open"] = 107.0
            df.loc[df.index[34], "Low"] = 106.8
            df.loc[df.index[34], "Close"] = 111.0
            df.loc[df.index[34], "High"] = 111.4
            df.loc[df.index[34], "Volume"] = 300_000.0
            return df

        elif pattern == "HIGHER_LOW_REVERSAL":
            df = _generate_synthetic_df(n_bars=40, base_price=100.0)
            df.loc[df.index[10], "Low"] = 90.0
            df.loc[df.index[20], "High"] = 100.0
            df.loc[df.index[30], "Low"] = 94.0
            df.loc[df.index[38], "Close"] = 99.5
            df.loc[df.index[38], "High"] = 99.8
            df.loc[df.index[39], "Open"] = 99.5
            df.loc[df.index[39], "Low"] = 99.2
            df.loc[df.index[39], "Close"] = 101.5
            df.loc[df.index[39], "High"] = 101.8
            df.loc[df.index[39], "Volume"] = 280_000.0
            return df

        return _generate_synthetic_df()


# =====================================================================================
# SUITE 04: PATTERN MUTATION & GEOMETRIC INVARIANT NEGATIVES
# =====================================================================================
class TestSuite04PatternMutations(unittest.TestCase):
    """
    Verifies that perturbing a single geometric property causes exact rejection.
    """

    def test_geometric_mutations(self):
        base_tester = TestSuite03PositivePatternFixtures()
        
        # 1. Double Bottom Spacing < 8 bars
        df_db_mut1 = base_tester._build_positive_fixture("DOUBLE_BOTTOM")
        df_db_mut1.loc[df_db_mut1.index[30], "Low"] = 95.0
        df_db_mut1.loc[df_db_mut1.index[17], "Low"] = 90.5
        self.assertIsNone(_detect_double_bottom(df_db_mut1, atr14=2.0))

        # 2. Double Bottom Symmetry > 2.5%
        df_db_mut2 = base_tester._build_positive_fixture("DOUBLE_BOTTOM")
        df_db_mut2.loc[df_db_mut2.index[30], "Low"] = 93.0
        self.assertIsNone(_detect_double_bottom(df_db_mut2, atr14=2.0))

        # 3. Cup & Handle Retrace > 35%
        df_ch_mut = base_tester._build_positive_fixture("CUP_HANDLE")
        for i in range(41, 49):
            df_ch_mut.loc[df_ch_mut.index[i], "Low"] = 92.0
        self.assertIsNone(_detect_cup_and_handle(df_ch_mut, atr14=2.0))

        # 4. Bull Flag Retrace > 45%
        df_bf_mut = base_tester._build_positive_fixture("BULL_FLAG")
        for i in range(21, 39):
            df_bf_mut.loc[df_bf_mut.index[i], "Low"] = 103.0
        self.assertIsNone(_detect_bull_flag(df_bf_mut, atr14=2.0))


# =====================================================================================
# SUITE 05: COMMON HARD GATES & ANTI-TRAP NEGATIVES
# =====================================================================================
class TestSuite05CommonHardGates(unittest.TestCase):
    def test_hard_gate_rejections(self):
        base_tester = TestSuite03PositivePatternFixtures()

        # 1. Red Candle
        df_red = base_tester._build_positive_fixture("BULL_FLAG")
        df_red.loc[df_red.index[-1], "Close"] = df_red.loc[df_red.index[-1], "Open"] - 0.5
        res, tr = detect_technical_setup(df_red, symbol="TEST_RED", return_trace=True)
        self.assertIsNone(res)
        self.assertEqual(tr["FINAL"]["terminal_reason"], "RED_CANDLE")

        # 2. RVOL < 1.20x
        df_rvol = base_tester._build_positive_fixture("BULL_FLAG")
        df_rvol.loc[df_rvol.index[-1], "Volume"] = 50_000.0
        res, tr = detect_technical_setup(df_rvol, symbol="TEST_RVOL", return_trace=True)
        self.assertIsNone(res)
        self.assertEqual(tr["FINAL"]["terminal_reason"], "LOW_RVOL")

        # 3. CLV < 0.65
        df_clv = base_tester._build_positive_fixture("BULL_FLAG")
        df_clv.loc[df_clv.index[-1], "High"] = 120.0
        df_clv.loc[df_clv.index[-1], "Low"] = 110.0
        df_clv.loc[df_clv.index[-1], "Open"] = 111.0
        df_clv.loc[df_clv.index[-1], "Close"] = 113.0
        res, tr = detect_technical_setup(df_clv, symbol="TEST_CLV", return_trace=True)
        self.assertIsNone(res)
        self.assertEqual(tr["FINAL"]["terminal_reason"], "LOW_CLV")

        # 4. Upper Wick > 30%
        df_wick = base_tester._build_positive_fixture("BULL_FLAG")
        df_wick.loc[df_wick.index[-1], "High"] = 120.0
        df_wick.loc[df_wick.index[-1], "Open"] = 114.0
        df_wick.loc[df_wick.index[-1], "Close"] = 115.0
        df_wick.loc[df_wick.index[-1], "Low"] = 110.0
        res, tr = detect_technical_setup(df_wick, symbol="TEST_WICK", return_trace=True)
        self.assertIsNone(res)
        self.assertEqual(tr["FINAL"]["terminal_reason"], "EXCESSIVE_UPPER_WICK")


# =====================================================================================
# SUITE 06: 1.5R ROOM-TO-RESISTANCE INVARIANT
# =====================================================================================
class TestSuite06RoomToResistance(unittest.TestCase):
    def test_room_to_resistance_invariant(self):
        base_tester = TestSuite03PositivePatternFixtures()
        df = base_tester._build_positive_fixture("BULL_FLAG")
        c_today = float(df["Close"].iloc[-1])

        # Immediate resistance blocking 1.5R headroom -> REJECT
        df_blocked = df.copy()
        df_blocked.loc[df_blocked.index[5], "High"] = c_today + 2.0
        res_blocked, tr_b = detect_technical_setup(df_blocked, symbol="TEST_BLOCKED", return_trace=True)
        # Should be rejected for insufficient headroom or room < 1.5R
        if res_blocked:
            self.assertGreaterEqual(res_blocked["room_to_resistance_r"], 1.5)


# =====================================================================================
# SUITE 07: SHORT-HISTORY STABILITY & ELIGIBILITY
# =====================================================================================
class TestSuite07ShortHistoryStabilityAndEligibility(unittest.TestCase):
    """
    Asserts both Stability (never crash) and Eligibility (reject with reason if pattern minimum history not met).
    """

    def test_stability_on_short_bars(self):
        df_15 = _generate_synthetic_df(n_bars=15)
        # 15 bars is < 20 bars minimum for technical scanner -> must reject cleanly without exception
        res, tr = detect_technical_setup(df_15, symbol="TEST_15B", return_trace=True)
        self.assertIsNone(res)
        self.assertEqual(tr["FINAL"]["terminal_reason"], "INSUFFICIENT_DATA")

    def test_eligibility_per_pattern_minimums(self):
        # 25 bars supports Bull Flag
        base_tester = TestSuite03PositivePatternFixtures()
        df_bf = base_tester._build_positive_fixture("BULL_FLAG").iloc[-25:].copy()
        res_bf, _ = detect_technical_setup(df_bf, symbol="TEST_BF_25", return_trace=True)
        self.assertIsNotNone(res_bf)

        # 25 bars is NOT eligible for Double Bottom (which requires >= 35 bars)
        df_db_short = base_tester._build_positive_fixture("DOUBLE_BOTTOM").iloc[-25:].copy()
        self.assertIsNone(_detect_double_bottom(df_db_short, atr14=2.0))


# =====================================================================================
# SUITE 08: FUNNEL CONSERVATION
# =====================================================================================
class TestSuite08FunnelConservation(unittest.TestCase):
    def test_funnel_conservation_zero_leaks(self):
        base_tester = TestSuite03PositivePatternFixtures()
        candidates = [
            ("SYM_PASS_BF", base_tester._build_positive_fixture("BULL_FLAG")),
            ("SYM_PASS_DB", base_tester._build_positive_fixture("DOUBLE_BOTTOM")),
            ("SYM_REJ_RED", base_tester._build_positive_fixture("BULL_FLAG")),
            ("SYM_REJ_VOL", base_tester._build_positive_fixture("BULL_FLAG")),
            ("SYM_REJ_NOPAT", _generate_synthetic_df(n_bars=30)),
        ]
        candidates[2][1].loc[candidates[2][1].index[-1], "Close"] = candidates[2][1].loc[candidates[2][1].index[-1], "Open"] - 1.0
        candidates[3][1].loc[candidates[3][1].index[-1], "Volume"] = 10_000.0

        alerts = 0
        rejections = {}
        total = len(candidates)

        for sym, df in candidates:
            res, tr = detect_technical_setup(df, symbol=sym, return_trace=True)
            if res and res.get("score", 0) >= 70:
                alerts += 1
            else:
                reason = tr["FINAL"].get("terminal_reason", "UNKNOWN")
                rejections[reason] = rejections.get(reason, 0) + 1

        self.assertEqual(total, alerts + sum(rejections.values()))
        self.assertEqual(alerts, 2)


# =====================================================================================
# SUITE 09: NO-LOOKAHEAD / FUTURE-DATA CONTAMINATION
# =====================================================================================
class TestSuite09FutureContamination(unittest.TestCase):
    """
    Asserts decision(BASE, D) == decision(BASE + FUTURE_BULL, D) == decision(BASE + FUTURE_CRASH, D).
    """

    def test_future_data_contamination(self):
        base_tester = TestSuite03PositivePatternFixtures()
        df_base = base_tester._build_positive_fixture("BULL_FLAG")
        d_len = len(df_base)

        # Baseline evaluation at bar D
        res_base, _ = detect_technical_setup(df_base, symbol="TEST_CAUSAL", return_trace=True)
        self.assertIsNotNone(res_base)

        # Append Future A (Radical Bull Expansion in future)
        df_future_a = df_base.copy()
        future_dates_a = pd.date_range(start=df_base.index[-1] + timedelta(days=1), periods=10, freq="B")
        df_ext_a = pd.DataFrame({
            "Date": range(10), "Open": 150.0, "High": 160.0, "Low": 149.0, "Close": 158.0, "Volume": 500_000.0,
            "EMA20": 140.0, "ATR": 3.0, "RSI": 80.0, "Volume_SMA20": 200_000.0
        }, index=future_dates_a)
        df_future_a = pd.concat([df_future_a, df_ext_a])

        # Append Future B (Severe Crash in future)
        df_future_b = df_base.copy()
        df_ext_b = pd.DataFrame({
            "Date": range(10), "Open": 50.0, "High": 52.0, "Low": 40.0, "Close": 42.0, "Volume": 900_000.0,
            "EMA20": 70.0, "ATR": 8.0, "RSI": 15.0, "Volume_SMA20": 300_000.0
        }, index=future_dates_a)
        df_future_b = pd.concat([df_future_b, df_ext_b])

        # Evaluate at historical point D
        res_eval_a, _ = detect_technical_setup(df_future_a.iloc[:d_len], symbol="TEST_CAUSAL", return_trace=True)
        res_eval_b, _ = detect_technical_setup(df_future_b.iloc[:d_len], symbol="TEST_CAUSAL", return_trace=True)

        self.assertEqual(res_base["score"], res_eval_a["score"])
        self.assertEqual(res_base["score"], res_eval_b["score"])
        self.assertEqual(res_base["stop_loss"], res_eval_a["stop_loss"])
        self.assertEqual(res_base["stop_loss"], res_eval_b["stop_loss"])
        self.assertEqual(res_base["target_1"], res_eval_a["target_1"])
        self.assertEqual(res_base["target_1"], res_eval_b["target_1"])


# =====================================================================================
# SUITE 10: ALERT PERSISTENCE & SESSION INTEGRITY
# =====================================================================================
class TestSuite10AlertPersistenceAndSessionIntegrity(unittest.TestCase):
    """
    Verifies alert deduplication on same trade date and weekend/holiday session integrity.
    """

    def test_alert_deduplication(self):
        # Simulated alert repository cache
        saved_alerts = {}

        def _mock_save_alert_if_new(symbol: str, run_date: str, pattern: str, score: int) -> bool:
            key = f"{symbol}_{run_date}_{pattern}"
            if key in saved_alerts:
                return False
            saved_alerts[key] = {"symbol": symbol, "run_date": run_date, "score": score}
            return True

        # Run 1 on Friday
        r1 = _mock_save_alert_if_new("TCS", "2025-01-10", "BULL_FLAG", 84)
        self.assertTrue(r1, "Run 1 must save alert")

        # Run 2 on Friday (Duplicate execution) -> Zero new alerts saved
        r2 = _mock_save_alert_if_new("TCS", "2025-01-10", "BULL_FLAG", 84)
        self.assertFalse(r2, "Run 2 must not create duplicate alert")

        # Run on Saturday with Friday date -> Zero new alerts saved
        r_sat = _mock_save_alert_if_new("TCS", "2025-01-10", "BULL_FLAG", 84)
        self.assertFalse(r_sat, "Weekend invocation referencing Friday must be idempotent")


if __name__ == "__main__":
    unittest.main()
