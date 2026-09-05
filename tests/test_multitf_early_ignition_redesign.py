import unittest
import sys
import os
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app')))

from multitf.consolidation import ConsolidationResult
from multitf.pressure import PressureResult, evaluate_5m_pressure, compute_ignition_score
from multitf.breakout_strength import (
    BreakoutStrengthResult,
    classify_alert_severity,
    evaluate_trade_eligibility
)
from multitf.state import MtfSubstate, to_canonical
from sl_target_helper import compute_sl_and_target
from config import MULTI_TF_V2_CONFIG

IST = ZoneInfo("Asia/Kolkata")


class TestMultiTFEarlyIgnitionRedesign(unittest.TestCase):
    def setUp(self):
        self.config = MULTI_TF_V2_CONFIG.copy()
        self.now = datetime(2026, 9, 2, 11, 30, tzinfo=IST)

    # ─────────────────────────────────────────────────────────────────────────
    # 1. IGNITION SCORE CALCULATION (0-100) & ARMED_PRE_BREAKOUT
    # ─────────────────────────────────────────────────────────────────────────
    def test_ignition_score_ready_qualifies(self):
        """High-quality base with compression, tests, proximity, and pressure achieves >= 75 ignition score."""
        cons = ConsolidationResult(
            symbol="TEST",
            is_valid=True,
            box_high=500.0,
            box_low=480.0,
            setup_score=80,
            compression_ratio=0.70,  # <= 0.75 -> 25 pts
            resistance_test_count=3, # >= 3 -> 20 pts
            has_higher_lows=True
        )
        press = PressureResult(
            is_attempt=True,         # attempt -> 20 pts
            live_position=0.85,
            volume_ratio=1.30        # >= 1.25 -> 10 pts
        )
        ctx_1h = {"score": 6}        # >= 5 -> 10 pts
        dist_atr = 0.12              # <= 0.15 -> 15 pts

        # Total points: 25 + 20 + 15 + 20 + 10 + 10 = 100
        res = compute_ignition_score(cons, press, dist_atr, ctx_1h, self.config)
        self.assertGreaterEqual(res["ignition_score"], 75)
        self.assertTrue(res["is_ignition_ready"])
        self.assertEqual(res["score_breakdown"]["compression"], 25)
        self.assertEqual(res["score_breakdown"]["resistance_tests"], 20)
        self.assertEqual(res["score_breakdown"]["distance"], 15)
        self.assertEqual(res["score_breakdown"]["pressure"], 20)
        self.assertEqual(res["score_breakdown"]["volume_trend"], 10)
        self.assertEqual(res["score_breakdown"]["context_1h"], 10)

    def test_ignition_score_fails_when_sitting_dead_or_far(self):
        """Stock sitting far (> 0.40 ATR) or with dead volume and no tests fails ignition readiness."""
        cons = ConsolidationResult(
            symbol="TEST",
            is_valid=True,
            box_high=500.0,
            box_low=480.0,
            setup_score=76,
            compression_ratio=1.0,
            resistance_test_count=1, # 1 test fails tests >= 2 requirement
            has_higher_lows=False
        )
        press = PressureResult(
            is_attempt=False,
            live_position=0.40,
            volume_ratio=0.80
        )
        ctx_1h = {"score": -2}       # negative 1H context
        dist_atr = 0.55              # > 0.40 ATR limit

        res = compute_ignition_score(cons, press, dist_atr, ctx_1h, self.config)
        self.assertLess(res["ignition_score"], 75)
        self.assertFalse(res["is_ignition_ready"])

    # ─────────────────────────────────────────────────────────────────────────
    # 2. PRE-BREAKOUT CONTRACT VS EARLY-BREAKOUT CONTRACT
    # ─────────────────────────────────────────────────────────────────────────
    def test_pre_breakout_contract_does_not_require_5m_close_above_resistance(self):
        """
        Pre-breakout contract uses PLANNED entry and PLANNED SL to verify tradeability,
        without requiring realized 5m close above resistance.
        """
        box_high = 500.0
        box_low = 490.0
        atr_5m = 2.0
        planned_entry = box_high + (0.05 * atr_5m) # 500.10
        planned_sl = box_low - (0.10 * atr_5m)     # 489.80

        # Create 1H ticker with higher overhead resistance at 525.0
        df_1h = pd.DataFrame([{"LOOKBACK_SWING_HIGH": 525.0, "R1": 530.0}])

        # Planned trade projected metrics
        proj = compute_sl_and_target(
            entry_price=planned_entry,
            atr=atr_5m,
            ticker=df_1h,
            mode="MULTI_TF_V2",
            box_low=box_low
        )

        self.assertFalse(proj["is_rejected"])
        self.assertGreaterEqual(proj["rr_ratio"], 1.5)
        self.assertEqual(proj["entry_price"], planned_entry)
        self.assertEqual(to_canonical(MtfSubstate.ARMED_PRE_BREAKOUT), "CANDIDATE")

    # ─────────────────────────────────────────────────────────────────────────
    # 3. T0 VS T1 TARGET ARCHITECTURE WITH EXPLICIT PROVENANCE
    # ─────────────────────────────────────────────────────────────────────────
    def test_tight_t0_obstacle_does_not_reject_trade_when_t1_satisfies_rr(self):
        """
        Nearest structural resistance (T0) offering only 0.4R scale-out does NOT reject
        the trade when next structural target (T1) offers >= 1.5R.
        """
        entry = 100.0
        box_low = 95.0
        atr = 2.0
        # Level 102.0 provides (102-100)/5.2 = 0.38R (T0 obstacle)
        # Level 112.0 provides (112-100)/5.2 = 2.31R (T1 trade target)
        ticker_df = pd.DataFrame([{"LOOKBACK_SWING_HIGH": 102.0, "R1": 112.0}])

        res = compute_sl_and_target(
            entry_price=entry,
            atr=atr,
            ticker=ticker_df,
            mode="MULTI_TF_V2",
            box_low=box_low
        )

        self.assertFalse(res["is_rejected"], "Trade should not be rejected by tight T0 obstacle")
        self.assertEqual(res["target_0"], 102.0)
        self.assertAlmostEqual(res["t0_rr_ratio"], 0.38, places=2)
        self.assertEqual(res["target_1"], 112.0)
        self.assertGreaterEqual(res["rr_ratio"], 1.5)
        self.assertEqual(res["t1_source"], "STRUCTURAL")

    def test_t1_measured_move_fallback_when_no_distant_structural_resistance(self):
        """
        When there are no structural levels delivering >= 1.5R (e.g. blue sky),
        T1 falls back to a 2.0R measured move with t1_source='MEASURED_MOVE'.
        """
        entry = 100.0
        box_low = 95.0
        atr = 2.0
        ticker_df = pd.DataFrame([{"LOOKBACK_SWING_HIGH": 103.0}]) # only 103 exists (< 1.5R)

        res = compute_sl_and_target(
            entry_price=entry,
            atr=atr,
            ticker=ticker_df,
            mode="MULTI_TF_V2",
            box_low=box_low
        )

        self.assertFalse(res["is_rejected"])
        self.assertEqual(res["target_0"], 103.0)
        self.assertEqual(res["t1_source"], "MEASURED_MOVE")
        self.assertEqual(res["target_basis"], "2R_Measured_Move")
        self.assertGreaterEqual(res["rr_ratio"], 2.0)

    # ─────────────────────────────────────────────────────────────────────────
    # 4. DECOUPLED SEVERITY CLASSIFICATION VS TRADE ELIGIBILITY CONTRACT
    # ─────────────────────────────────────────────────────────────────────────
    def test_bear_quality_floor_permits_good_and_blocks_weak(self):
        """
        In BEAR market:
        - Setup meeting Base >= 75, Breakout >= 68, RVOL >= 1.30, Confluence >= 80, RR >= 1.5
          is classified as GOOD and approved as ELIGIBLE.
        - Setup failing these thresholds is rejected with specific reason code.
        """
        # 1. Valid BEAR Setup
        cons = ConsolidationResult(symbol="TEST", is_valid=True, setup_score=76, has_higher_lows=True, resistance_test_count=2)
        brk = BreakoutStrengthResult(breakout_score=69, volume_ratio=1.34)
        sev = classify_alert_severity(cons, brk, self.config, market_status="BEAR")
        self.assertEqual(sev, "GOOD")

        eligible, reason = evaluate_trade_eligibility(
            base_score=76,
            breakout_score=69,
            volume_ratio=1.34,
            confluence_score=83,
            rr_ratio=1.8,
            market_status="BEAR",
            config=self.config
        )
        self.assertTrue(eligible)
        self.assertEqual(reason, "ELIGIBLE")

        # 2. Setup with high severity classification but failing R:R (< 1.5R) is NOT ELIGIBLE
        cons_super = ConsolidationResult(symbol="TEST", is_valid=True, setup_score=82, has_higher_lows=True, resistance_test_count=3)
        brk_super = BreakoutStrengthResult(breakout_score=81, volume_ratio=1.90)
        sev_super = classify_alert_severity(cons_super, brk_super, self.config, market_status="BEAR")
        self.assertEqual(sev_super, "SUPER")

        eligible_bad_rr, reason_bad_rr = evaluate_trade_eligibility(
            base_score=82,
            breakout_score=81,
            volume_ratio=1.90,
            confluence_score=85,
            rr_ratio=0.9, # Bad R:R
            market_status="BEAR",
            config=self.config
        )
        self.assertFalse(eligible_bad_rr)
        self.assertEqual(reason_bad_rr, "RR_T1_FAIL")

    # ─────────────────────────────────────────────────────────────────────────
    # 5. HEALTHY PENETRATION VS INSTITUTIONAL THRUST VS BLOW-OFF EXHAUSTION
    # ─────────────────────────────────────────────────────────────────────────
    def test_healthy_penetration_up_to_1_20_atr(self):
        """5m close penetrating up to 1.20 ATR is accepted as healthy expansion without overextension."""
        box_high = 500.0
        atr_15m = 5.0
        atr_5m = 2.0
        # Penetration = 5.0 points = 1.0 ATR_15M (<= 1.20)
        close_price = box_high + 5.0

        dates = [self.now - timedelta(minutes=5 * (5 - i)) for i in range(5)]
        rows = [
            {"Date": dates[i], "Open": 498.0, "High": 499.5, "Low": 497.0, "Close": 498.5, "Volume": 5000.0}
            for i in range(4)
        ]
        # Breakout candle: closes at 505.0
        rows.append({
            "Date": dates[4],
            "Open": 499.0, "High": 505.5, "Low": 498.5, "Close": close_price, "Volume": 8000.0 # 1.6x vol
        })
        df_5m = pd.DataFrame(rows)
        df_5m.set_index("Date", inplace=True)

        res = evaluate_5m_pressure(
            live_candle=None,
            df_5m_closed=df_5m,
            box_high=box_high,
            atr_5m=atr_5m,
            ist_now=self.now,
            config=self.config,
            atr_15m=atr_15m
        )

        self.assertTrue(res.is_confirmed)
        self.assertFalse(res.is_overextended, "1.0 ATR penetration must not be flagged as overextended")

    def test_institutional_thrust_above_1_20_atr_approved_if_velocity_controlled(self):
        """
        Penetration > 1.20 ATR with RVOL >= 1.75, strong close >= 0.75, and velocity <= 0.25
        is recognized as Institutional Thrust, not exhaustion.
        """
        box_high = 500.0
        atr_15m = 5.0
        atr_5m = 3.0
        # Penetration = 6.5 points = 1.30 ATR_15M (> 1.20)
        close_price = box_high + 6.5 # 506.5

        dates = [self.now - timedelta(minutes=5 * (5 - i)) for i in range(5)]
        rows = [
            {"Date": dates[i], "Open": 498.0, "High": 499.5, "Low": 497.0, "Close": 498.5, "Volume": 5000.0}
            for i in range(4)
        ]
        # Institutional thrust bar: candle range = 7.5, velocity = (7.5 / 3.0) / 5 = 0.50?
        # Let's make candle range = 3.0 so velocity = (3.0 / 3.0) / 5 = 0.20 <= 0.25 max envelope
        rows.append({
            "Date": dates[4],
            "Open": 504.0, "High": 507.0, "Low": 504.0, "Close": 506.5, "Volume": 10000.0 # 2.0x vol (>= 1.75)
        })
        df_5m = pd.DataFrame(rows)
        df_5m.set_index("Date", inplace=True)

        res = evaluate_5m_pressure(
            live_candle=None,
            df_5m_closed=df_5m,
            box_high=box_high,
            atr_5m=atr_5m,
            ist_now=self.now,
            config=self.config,
            atr_15m=atr_15m
        )

        self.assertTrue(res.is_confirmed)
        self.assertFalse(res.is_overextended, "Controlled velocity thrust with RVOL >= 1.75 and close >= 0.75 is approved")

    def test_blowoff_exhaustion_above_1_20_atr_rejected(self):
        """Penetration > 1.20 ATR with abnormal velocity or weak close is rejected as exhaustion."""
        box_high = 500.0
        atr_15m = 5.0
        atr_5m = 2.0
        # Penetration = 7.0 points = 1.40 ATR_15M (> 1.20)
        # Candle range = 8.0 -> velocity = (8.0 / 2.0) / 5 = 0.80 >> 0.25 (blow-off exhaustion)
        dates = [self.now - timedelta(minutes=5 * (5 - i)) for i in range(5)]
        rows = [
            {"Date": dates[i], "Open": 498.0, "High": 499.5, "Low": 497.0, "Close": 498.5, "Volume": 5000.0}
            for i in range(4)
        ]
        rows.append({
            "Date": dates[4],
            "Open": 499.0, "High": 507.0, "Low": 499.0, "Close": 507.0, "Volume": 6000.0 # RVOL only 1.2x (< 1.75)
        })
        df_5m = pd.DataFrame(rows)
        df_5m.set_index("Date", inplace=True)

        res = evaluate_5m_pressure(
            live_candle=None,
            df_5m_closed=df_5m,
            box_high=box_high,
            atr_5m=atr_5m,
            ist_now=self.now,
            config=self.config,
            atr_15m=atr_15m
        )

        self.assertTrue(res.is_overextended, "Abnormal blow-off candle must be flagged as overextended/exhaustion")

    # ─────────────────────────────────────────────────────────────────────────
    # 6. TWO-SESSION CUTOFF & HARD 15:00 BLACKOUT
    # ─────────────────────────────────────────────────────────────────────────
    def test_late_session_quality_gate(self):
        """Between 14:15 and 15:00 IST, stricter thresholds are enforced."""
        # Setup with Base=72, Breakout=66 qualifies in normal session, but fails late session
        normal_ok, _ = evaluate_trade_eligibility(
            base_score=72,
            breakout_score=66,
            volume_ratio=1.28,
            confluence_score=70,
            rr_ratio=1.6,
            market_status="NORMAL",
            config=self.config,
            is_late_session=False
        )
        self.assertTrue(normal_ok)

        late_fail, reason = evaluate_trade_eligibility(
            base_score=72, # < 75
            breakout_score=66, # < 75
            volume_ratio=1.28, # < 1.50
            confluence_score=70, # < 82
            rr_ratio=1.6,
            market_status="NORMAL",
            config=self.config,
            is_late_session=True
        )
        self.assertFalse(late_fail)
        self.assertEqual(reason, "LATE_SESSION_BASE_FAIL")


if __name__ == "__main__":
    unittest.main()
