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
        """Tests that a 6-bar tight base with 2 resistance touches scores >= 70 and is valid."""
        df, atr = self._generate_15m_df(n_bars=6, base_price=500.0, range_atr_mult=1.1, touches=2)
        res = detect_15m_consolidation(df, atr, self.now, self.config)
        
        self.assertTrue(res.is_valid, "Tight base should qualify for 15M_BREAKOUT_WATCH")
        self.assertGreaterEqual(res.setup_score, 70, "Consolidation quality score must be >= 70")
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
        market_ctx = {"score": 5}
        
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


if __name__ == "__main__":
    unittest.main()
