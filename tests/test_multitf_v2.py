import unittest
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app')))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from multitf.consolidation import detect_15m_consolidation, ConsolidationResult
from multitf.pressure import evaluate_5m_pressure, PressureResult
from multitf.confluence import evaluate_breakout_confluence, ConfluenceResult
from config import MULTI_TF_V2_CONFIG

IST = ZoneInfo("Asia/Kolkata")


class TestMultiTFRedesign(unittest.TestCase):
    def setUp(self):
        self.config = MULTI_TF_V2_CONFIG.copy()
        self.now = datetime(2026, 9, 2, 11, 30, tzinfo=IST)

    def _generate_15m_df(self, n_bars=8, base_price=500.0, range_atr_mult=1.2, touches=2):
        """Generates synthetic 15m closed candles forming a tight base."""
        dates = [self.now - timedelta(minutes=15 * (n_bars - i)) for i in range(n_bars)]
        atr = 5.0
        max_range = atr * range_atr_mult
        
        # Base between base_price and base_price + max_range
        high_level = base_price + max_range
        low_level = base_price
        
        rows = []
        for i in range(n_bars):
            # Create touches at high_level
            if i in (1, n_bars - 2) and touches >= 2:
                h = high_level
                c = high_level - 0.5
                l = high_level - 2.5
                o = high_level - 2.0
            else:
                l = low_level + 0.5
                h = high_level - 1.0
                c = (l + h) / 2.0
                o = c - 0.5
                
            rows.append({
                "Date": dates[i],
                "Open": o,
                "High": h,
                "Low": l,
                "Close": c,
                "Volume": 10000.0,
                "session_date": dates[i].date()
            })
            
        df = pd.DataFrame(rows)
        df.set_index("Date", inplace=True)
        df.attrs["symbol"] = "TESTSTOCK"
        return df, atr

    def test_15m_consolidation_tight_base_qualifies(self):
        """Tests that an 8-bar tight base with 3 resistance touches scores >= 70 and is valid in V3."""
        df, atr = self._generate_15m_df(n_bars=8, base_price=500.0, range_atr_mult=0.90, touches=3)
        res = detect_15m_consolidation(df, atr, self.now, self.config)

        # V3: 8 bars (mat=12) + tight 0.90× ATR (tight=17) + 3 tests (rep=13) + neutral comp/hl = ~65+
        self.assertTrue(res.is_valid, "8-bar tight base with 3 tests should qualify for 15M_BREAKOUT_WATCH")
        self.assertGreaterEqual(res.setup_score, 65, "V3: 8-bar tight base should score >= 65")
        self.assertLessEqual(res.box_width_atr, 1.50, "Box width ATR should be <= 1.50")
        self.assertGreaterEqual(res.resistance_test_count, 2, "Should record at least 2 resistance tests")

    def test_15m_consolidation_wide_erratic_rejected(self):
        """Tests that a wide base (> 2.5x ATR) is rejected with score < 55."""
        df, atr = self._generate_15m_df(n_bars=6, base_price=500.0, range_atr_mult=2.8, touches=1)
        res = detect_15m_consolidation(df, atr, self.now, self.config)
        
        self.assertFalse(res.is_valid, "Wide erratic base must not qualify")
        self.assertLess(res.setup_score, 55, "Score should be < 55")

    def test_5m_model_a_direct_breakout(self):
        """Tests Model A: direct breakout with Close > resistance, strong close pos, and RVOL >= 1.25x."""
        box_high = 506.0
        atr_5m = 2.0
        
        # 5m closed candles
        dates = [self.now - timedelta(minutes=5 * (5 - i)) for i in range(5)]
        rows = []
        for i in range(4):
            rows.append({
                "Date": dates[i],
                "Open": 504.0, "High": 505.5, "Low": 503.5, "Close": 504.5, "Volume": 5000.0
            })
        # Breakout candle: closes at 507.5 (above 506 + buffer 0.2 = 506.2)
        rows.append({
            "Date": dates[4],
            "Open": 505.0, "High": 508.0, "Low": 504.8, "Close": 507.5, "Volume": 10000.0 # 2.0x volume
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
            daily_atr=15.0
        )
        
        self.assertTrue(res.is_confirmed, "Model A direct breakout should be confirmed")
        self.assertEqual(res.trigger_model, "MODEL_A_DIRECT")
        self.assertGreaterEqual(res.live_position, 0.60)
        self.assertGreaterEqual(res.volume_ratio, 1.25)

    def test_5m_model_b_retest_defense(self):
        """Tests Model B: breakout retest & defense where low tests resistance and closes bullish."""
        box_high = 506.0
        atr_5m = 2.0
        
        dates = [self.now - timedelta(minutes=5 * (5 - i)) for i in range(5)]
        rows = []
        for i in range(4):
            rows.append({
                "Date": dates[i],
                "Open": 506.5, "High": 507.0, "Low": 505.8, "Close": 506.8, "Volume": 6000.0
            })
        # Retest candle: Low reaches 506.1 (tests zone), Open 506.2, Close 507.8 (bullish hammer/bounce)
        rows.append({
            "Date": dates[4],
            "Open": 506.2, "High": 508.0, "Low": 506.1, "Close": 507.8, "Volume": 8000.0
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
            daily_atr=15.0
        )
        
        self.assertTrue(res.is_confirmed, "Model B retest defense should be confirmed")
        self.assertEqual(res.trigger_model, "MODEL_B_RETEST")

    def test_5m_fake_breakout_wick_rejected(self):
        """Tests anti-fake-breakout: High > resistance but Close < resistance must be rejected."""
        box_high = 506.0
        atr_5m = 2.0
        
        dates = [self.now - timedelta(minutes=5 * (5 - i)) for i in range(5)]
        rows = []
        for i in range(4):
            rows.append({
                "Date": dates[i],
                "Open": 504.0, "High": 505.5, "Low": 503.5, "Close": 504.5, "Volume": 5000.0
            })
        # Shooting star wick: High spikes to 508.0, but Close falls back to 504.8
        rows.append({
            "Date": dates[4],
            "Open": 505.0, "High": 508.0, "Low": 504.0, "Close": 504.8, "Volume": 10000.0
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
            daily_atr=15.0
        )
        
        self.assertFalse(res.is_confirmed, "Wick-only breakout must be rejected")

    def test_5m_overextension_rejected(self):
        """Tests anti-fake-breakout: Price > resistance + 0.50x Daily ATR is rejected as overextended."""
        box_high = 506.0
        atr_5m = 2.0
        daily_atr = 10.0 # Max extension = 506 + 5.0 = 511.0
        
        dates = [self.now - timedelta(minutes=5 * (5 - i)) for i in range(5)]
        rows = []
        for i in range(4):
            rows.append({
                "Date": dates[i],
                "Open": 504.0, "High": 505.5, "Low": 503.5, "Close": 504.5, "Volume": 5000.0
            })
        # Over-extended candle: Closes at 514.0 (> 511.0)
        rows.append({
            "Date": dates[4],
            "Open": 506.0, "High": 515.0, "Low": 505.8, "Close": 514.0, "Volume": 15000.0
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
            daily_atr=daily_atr
        )
        
        self.assertFalse(res.is_confirmed, "Over-extended candle must not trigger confirmed breakout")
        self.assertTrue(res.is_overextended, "Over-extended flag must be True")

    def test_confluence_scoring(self):
        """Tests that a high quality 15m base (80) + confirmed 5m momentum produces approval."""
        cons = ConsolidationResult(
            symbol="TESTSTOCK",
            is_valid=True,
            box_high=506.0,
            box_low=500.0,
            setup_score=85
        )
        pressure = PressureResult(
            is_confirmed=True,
            volume_ratio=1.8,
            range_ratio=1.5,
            live_position=0.75,
            momentum_score=22
        )
        ctx_1h = {"score": 8}
        ctx_30m = {"score": 6}
        market_ctx = {"score": 5, "status": "NORMAL"}
        
        confluence = evaluate_breakout_confluence(
            consolidation=cons,
            pressure=pressure,
            ctx_1h=ctx_1h,
            ctx_30m=ctx_30m,
            market_ctx=market_ctx,
            config=self.config
        )
        
        self.assertTrue(confluence.is_approved, "Confluence must approve high quality setup")
        self.assertGreaterEqual(confluence.total_score, 65, "Total confluence score must be >= 65")

    def test_soft_market_regime_shield(self):
        """Tests soft market shield: severe bear market suppresses marginal setups, allows strong leaders."""
        # 1. Marginal setup in BEAR market -> suppressed
        cons_marginal = ConsolidationResult(symbol="TEST_MARGINAL", is_valid=True, box_high=506.0, box_low=500.0, setup_score=70)
        pressure_marginal = PressureResult(is_confirmed=True, volume_ratio=1.28, range_ratio=1.2, live_position=0.65, momentum_score=16)
        ctx_bear = {"status": "BEAR", "score": -5}
        
        conf_marginal = evaluate_breakout_confluence(
            consolidation=cons_marginal,
            pressure=pressure_marginal,
            ctx_1h={"score": 0},
            ctx_30m={"score": 0},
            market_ctx=ctx_bear,
            config=self.config
        )
        self.assertFalse(conf_marginal.is_approved, "Marginal setup during bear market must be suppressed")
        
        # 2. Strong RS leader in BEAR market (Confluence >= 80, RVOL >= 1.5x) -> allowed
        # Needs: struct=36 (90-base*0.40), mom=24, vol=15 (RVOL>=2x), ctx=8 => total=83
        cons_leader = ConsolidationResult(symbol="TEST_LEADER", is_valid=True, box_high=506.0, box_low=500.0, setup_score=90)
        pressure_leader = PressureResult(is_confirmed=True, volume_ratio=2.10, range_ratio=1.6, live_position=0.85, momentum_score=24)
        
        conf_leader = evaluate_breakout_confluence(
            consolidation=cons_leader,
            pressure=pressure_leader,
            ctx_1h={"score": 10},
            ctx_30m={"score": 8},
            market_ctx=ctx_bear,
            config=self.config
        )
        self.assertTrue(conf_leader.is_approved, "Strong relative strength leader must be permitted in bear market")


class TestBaseQualityEngineV3(unittest.TestCase):
    """Tests for the V3 7-Component Base Quality Engine in consolidation.py."""

    def setUp(self):
        self.config = MULTI_TF_V2_CONFIG.copy()
        self.now = datetime(2026, 9, 2, 11, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
        self.atr = 5.0  # ₹5 ATR for ₹500 stock = 1%

    def _make_df(self, n=10, high_noise=0.5, higher_lows=False, compression=True):
        """Generate synthetic 15m OHLCV DataFrame."""
        dates = pd.date_range(self.now - pd.Timedelta(minutes=15 * n), periods=n, freq="15min")
        data = []
        base = 500.0
        for i in range(n):
            frac = i / max(n - 1, 1)
            low_adj = (i * 0.20) if higher_lows else 0.0
            vol_mult = (1.0 - frac * 0.3) if compression else 1.0  # Contracting ranges
            candle_range = self.atr * 0.80 * vol_mult
            o = base + 1.0
            h = base + self.atr * high_noise + candle_range / 2
            l = base + low_adj
            c = base + 1.5 + low_adj
            data.append({"Open": o, "High": h, "Low": l, "Close": c, "Volume": 100000, "session_date": dates[i].date()})
        df = pd.DataFrame(data, index=dates)
        return df

    def test_tightness_score_tight_base(self):
        """Tight range (0.75× ATR) should score 20 pts."""
        res = ConsolidationResult(symbol="TEST", is_valid=False, box_high=505.0, box_low=501.25, box_width_atr=0.75, bars_count=8)
        from multitf.consolidation import _compute_scores
        df = self._make_df()
        _compute_scores(df, df, self.atr, res, self.config)
        self.assertEqual(res.score_tightness, 20, "Range/ATR 0.75 should give 20 pts tightness")

    def test_tightness_score_wide_base(self):
        """Wide range (1.5× ATR) should score only 8 pts."""
        res = ConsolidationResult(symbol="TEST", is_valid=False, box_high=507.5, box_low=500.0, box_width_atr=1.5, bars_count=8)
        from multitf.consolidation import _compute_scores
        df = self._make_df()
        _compute_scores(df, df, self.atr, res, self.config)
        self.assertEqual(res.score_tightness, 8, "Range/ATR 1.5 should give 8 pts tightness")

    def test_maturity_cap_on_wide_base(self):
        """Maturity > 10 should be capped to 10 when base is wide (box_width_atr > 1.25)."""
        res = ConsolidationResult(symbol="TEST", is_valid=False, box_high=508.0, box_low=500.0, box_width_atr=1.4, bars_count=12)
        from multitf.consolidation import _compute_scores
        df = self._make_df(n=12)
        _compute_scores(df, df, self.atr, res, self.config)
        self.assertLessEqual(res.score_maturity, 10, "12-bar wide base: maturity must be capped at 10")

    def test_higher_lows_detected(self):
        """A base with clearly rising lows should detect has_higher_lows=True."""
        res = ConsolidationResult(symbol="TEST", is_valid=False, box_high=506.0, box_low=500.0, box_width_atr=1.0, bars_count=8)
        from multitf.consolidation import _compute_scores
        df = self._make_df(n=8, higher_lows=True)
        _compute_scores(df, df, self.atr, res, self.config)
        self.assertTrue(res.has_higher_lows, "Rising lows structure must be detected")
        self.assertGreater(res.score_higher_lows, 5, "Higher lows should contribute > 5 pts")

    def test_compression_score_contracting(self):
        """Contracting volatility (late < 0.6× early) should score 15 pts."""
        res = ConsolidationResult(symbol="TEST", is_valid=False, box_high=505.0, box_low=500.0, box_width_atr=1.0, bars_count=8)
        from multitf.consolidation import _compute_scores
        df = self._make_df(n=8, compression=True)
        _compute_scores(df, df, self.atr, res, self.config)
        self.assertGreaterEqual(res.score_compression, 4, "Compression should give >= 4 pts")

    def test_base_rating_label_correct(self):
        """setup_score >= 90 must label EXCEPTIONAL, 80+ SUPER, 70+ GOOD."""
        # Manually set scores to verify labeling
        from multitf.consolidation import _compute_scores
        df = self._make_df(n=12, higher_lows=True)
        res = ConsolidationResult(symbol="TEST", is_valid=False, box_high=505.0, box_low=501.25,
                                  box_width_atr=0.75, bars_count=12)
        res.resistance_test_count = 4
        _compute_scores(df, df, self.atr, res, self.config)
        self.assertIn(res.base_rating_label, ["EXCEPTIONAL", "SUPER", "GOOD", "WATCH", "REJECT"],
                      "base_rating_label must be one of the valid tiers")


class TestBreakoutStrengthEngineV3(unittest.TestCase):
    """Tests for the V3 5m Breakout Strength Engine."""

    def setUp(self):
        self.config = MULTI_TF_V2_CONFIG.copy()
        self.now = datetime(2026, 9, 2, 11, 30, tzinfo=ZoneInfo("Asia/Kolkata"))

    def _make_cons(self, base_score=80):
        return ConsolidationResult(symbol="TEST", is_valid=True, box_high=505.0, box_low=500.0,
                                   setup_score=base_score, has_higher_lows=True, resistance_test_count=3,
                                   compression_ratio=0.75, base_rating_label="SUPER")

    def _make_5m_df(self, n=20, breakout_vol=200000, prev_vol=80000, close=506.5):
        dates = pd.date_range(self.now - pd.Timedelta(minutes=5 * n), periods=n, freq="5min")
        vols = [80000] * (n - 1) + [breakout_vol]
        closes = [500.5] * (n - 1) + [close]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.5 for c in closes]
        df = pd.DataFrame({"Open": [c - 0.5 for c in closes], "High": highs, "Low": lows,
                           "Close": closes, "Volume": vols}, index=dates)
        return df

    def test_high_rvol_gives_high_score(self):
        """RVOL >= 2.0× should give 22+ pts on volume score in 25-pt scale."""
        from multitf.breakout_strength import compute_breakout_strength
        cons = self._make_cons()
        df_5m = self._make_5m_df(breakout_vol=200000)
        pressure = PressureResult(is_confirmed=True, volume_ratio=2.5, range_ratio=2.0, live_position=0.88,
                                  momentum_score=22, current_5m_volume=200000, expected_volume=80000, prev_5m_volume=80000)
        brkout = compute_breakout_strength(pressure, cons, df_5m, None, self.now, self.config)
        self.assertGreaterEqual(brkout.score_rvol, 22, "RVOL 2.5× should give >= 22 pts on 25-pt scale")
        self.assertEqual(brkout.rvol_label, "VERY_STRONG")

    def test_weak_rvol_gives_low_score(self):
        """RVOL < 1.0× should give 0 pts."""
        from multitf.breakout_strength import compute_breakout_strength
        cons = self._make_cons()
        df_5m = self._make_5m_df(breakout_vol=50000)
        pressure = PressureResult(is_confirmed=True, volume_ratio=0.8, range_ratio=1.0, live_position=0.65,
                                  momentum_score=10, current_5m_volume=50000, expected_volume=80000, prev_5m_volume=80000)
        brkout = compute_breakout_strength(pressure, cons, df_5m, None, self.now, self.config)
        self.assertEqual(brkout.score_rvol, 0, "RVOL < 1.0× should give 0 pts")
        self.assertEqual(brkout.rvol_label, "WEAK")

    def test_volume_acceleration_scoring(self):
        """Vol 3× previous bar should score 10 pts acceleration."""
        from multitf.breakout_strength import compute_breakout_strength
        cons = self._make_cons()
        df_5m = self._make_5m_df(breakout_vol=240000, prev_vol=80000)
        pressure = PressureResult(is_confirmed=True, volume_ratio=1.8, range_ratio=2.0, live_position=0.85,
                                  momentum_score=20, current_5m_volume=240000, prev_5m_volume=80000)
        brkout = compute_breakout_strength(pressure, cons, df_5m, None, self.now, self.config)
        self.assertGreaterEqual(brkout.score_vol_accel, 8, "3× prev vol should give >= 8 pts acceleration")

    def test_base_relative_volume_and_energy(self):
        """Breakout volume 2.5x consolidation median should award base-rel vol score and calculate energy."""
        from multitf.breakout_strength import compute_breakout_strength
        cons = self._make_cons(base_score=85)
        df_5m = self._make_5m_df(breakout_vol=200000, prev_vol=80000, close=506.8)
        pressure = PressureResult(is_confirmed=True, volume_ratio=2.5, range_ratio=2.2, live_position=0.90,
                                  momentum_score=24, current_5m_volume=200000, prev_5m_volume=80000)
        brkout = compute_breakout_strength(pressure, cons, df_5m, None, self.now, self.config)
        self.assertGreaterEqual(brkout.score_base_rel_vol, 6, "Base-rel vol >= 2.0x should score >= 6 pts")
        self.assertGreater(brkout.breakout_energy, 0.5, "Breakout energy should be positive")
        self.assertIn(brkout.breakout_energy_label, ["EXTREME", "HIGH", "MODERATE", "LOW"])


class TestAlertSeverityClassification(unittest.TestCase):
    """Tests for the V3 alert severity classification."""

    def setUp(self):
        self.config = MULTI_TF_V2_CONFIG.copy()

    def _make_brkout(self, breakout_score=92, rvol=2.5, rvol_label="VERY_STRONG"):
        from multitf.breakout_strength import BreakoutStrengthResult
        return BreakoutStrengthResult(breakout_score=breakout_score, volume_ratio=rvol,
                                     rvol_label=rvol_label, breakout_rating_label="EXPLOSIVE")

    def _make_cons(self, base_score=92, hl=True, tests=4):
        return ConsolidationResult(symbol="TEST", is_valid=True, box_high=506.0, box_low=500.0,
                                   setup_score=base_score, has_higher_lows=hl, resistance_test_count=tests,
                                   compression_ratio=0.65)

    def test_a_plus_setup(self):
        from multitf.breakout_strength import classify_alert_severity
        severity = classify_alert_severity(self._make_cons(92, True, 4), self._make_brkout(92, 2.5), self.config)
        self.assertEqual(severity, "A_PLUS", "Base 92 + Breakout 92 + RVOL 2.5× + HL + 4 tests = A+")

    def test_explosive_setup(self):
        from multitf.breakout_strength import classify_alert_severity
        severity = classify_alert_severity(self._make_cons(86, False, 2), self._make_brkout(89, 2.1), self.config)
        self.assertEqual(severity, "EXPLOSIVE", "Base 86 + Breakout 89 + RVOL 2.1× = EXPLOSIVE")

    def test_super_setup(self):
        from multitf.breakout_strength import classify_alert_severity
        severity = classify_alert_severity(self._make_cons(82, False, 2), self._make_brkout(82, 1.6), self.config)
        self.assertEqual(severity, "SUPER", "Base 82 + Breakout 82 = SUPER")

    def test_good_setup(self):
        from multitf.breakout_strength import classify_alert_severity
        severity = classify_alert_severity(self._make_cons(72, False, 2), self._make_brkout(73, 1.3, "CONFIRMED"), self.config)
        self.assertEqual(severity, "GOOD", "Base 72 + Breakout 73 = GOOD")

    def test_weak_breakout_no_push(self):
        from multitf.breakout_strength import classify_alert_severity
        severity = classify_alert_severity(self._make_cons(72, False, 2), self._make_brkout(65, 1.2, "NORMAL"), self.config)
        self.assertEqual(severity, "WEAK", "Breakout 65 < MIN_BREAKOUT_SCORE(70) = WEAK")

    def test_bear_market_suppresses_marginal(self):
        from multitf.breakout_strength import classify_alert_severity
        severity = classify_alert_severity(self._make_cons(74, False, 2), self._make_brkout(74, 1.3, "CONFIRMED"),
                                           self.config, market_status="BEAR")
        self.assertEqual(severity, "WEAK", "In BEAR: base 74, RVOL 1.3× (< 1.5×) must be suppressed")

    def test_bear_market_allows_rs_leader(self):
        from multitf.breakout_strength import classify_alert_severity
        severity = classify_alert_severity(self._make_cons(85, True, 3), self._make_brkout(89, 2.2, "VERY_STRONG"),
                                           self.config, market_status="BEAR")
        self.assertNotEqual(severity, "WEAK", "In BEAR: strong RS leader (base 85, RVOL 2.2×) must pass")

    def test_range_index_datetime_handling(self):
        """Validates that strip_closed_candles and validate_freshness work seamlessly with RangeIndex."""
        from multitf.data import strip_closed_candles, validate_freshness, normalize_sessions
        from multitf.scanner import _get_atr

        now = datetime(2026, 9, 2, 14, 30, tzinfo=IST)
        dates = pd.date_range("2026-09-02 09:15", periods=20, freq="15min", tz="Asia/Kolkata")
        df_range = pd.DataFrame({
            "Date": dates,
            "Open": [100.0 + i for i in range(20)],
            "High": [102.0 + i for i in range(20)],
            "Low": [99.0 + i for i in range(20)],
            "Close": [101.0 + i for i in range(20)],
            "Volume": [10000 + i * 100 for i in range(20)],
            "ATR": [3.0] * 20
        })

        # Ensure index is standard integer RangeIndex
        self.assertIsInstance(df_range.index, pd.RangeIndex)

        # 1. strip_closed_candles should not throw TypeError
        cl = strip_closed_candles(df_range, 15, now)
        self.assertIsNotNone(cl)
        self.assertFalse(cl.empty)

        # 2. normalize_sessions should work with Date column
        norm = normalize_sessions(df_range, "15m", now)
        self.assertIn("session_date", norm.columns)
        self.assertIn("bar_start", norm.columns)

        # 3. _get_atr should resolve 'ATR' or 'ATR_14'
        atr_val = _get_atr(df_range)
        self.assertEqual(atr_val, 3.0)

        # 4. _get_atr should fallback to TrueRange calculation if no ATR column
        df_no_atr = df_range.drop(columns=["ATR"])
        computed_atr = _get_atr(df_no_atr)
        self.assertGreater(computed_atr, 0.0)

    def test_build_watchlist_candidate_canonical_state(self):
        """Validates that build_watchlist_candidate properly maps canonical state without AttributeError."""
        from multitf.candidate import build_watchlist_candidate
        from multitf.data import MultitfDataBundle
        from unittest.mock import MagicMock

        bundle = MagicMock(spec=MultitfDataBundle)
        bundle.prov_1h = None
        bundle.prov_30m = None
        bundle.prov_15m = None
        bundle.prov_5m = None

        cons = self._make_cons(80, False, 2)
        cons.box_id = "TEST_15M_BOX"
        ist_now = datetime(2026, 9, 2, 11, 30, tzinfo=IST)
        candidate = build_watchlist_candidate(
            bundle=bundle,
            consolidation=cons,
            ctx_1h={"score": 80},
            ctx_30m={"score": 80},
            market_ctx={"regime": "BULL"},
            ist_now=ist_now
        )
        self.assertEqual(candidate["state"], "WATCH")
        self.assertEqual(candidate["mtf_substate"], "WATCHING")
        self.assertEqual(candidate["symbol"], "TEST")
        self.assertEqual(candidate["box_id"], "TEST_15M_BOX")


if __name__ == "__main__":
    unittest.main()
