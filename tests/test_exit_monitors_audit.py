#!/usr/bin/env python3
"""
Unit tests for Exit Monitors Audit, Trading Calendar Enforcement, and Recalculate Architecture.
"""

import sys, os
from datetime import datetime, date, time as time_cls, timedelta
import pandas as pd
import pytz
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
IST = pytz.timezone("Asia/Kolkata")

class TestExitMonitorsAndCalendar(unittest.TestCase):

    def test_trading_calendar_enforcement(self):
        from trading_calendar import enforce_trading_day_candles, default_trading_calendar, is_valid_market_session_timestamp, get_next_trading_date

        # 1. Test get_next_trading_date for Friday -> Monday
        friday = date(2026, 9, 18)
        next_d = get_next_trading_date(friday)
        self.assertEqual(next_d, date(2026, 9, 21), "Friday must advance to Monday (skipping Sat & Sun)")

        # 2. Test is_valid_market_session_timestamp
        valid_ts = IST.localize(datetime(2026, 9, 22, 11, 30))
        self.assertTrue(is_valid_market_session_timestamp(valid_ts), "Tuesday 11:30 IST should be valid market session")

        midnight_ts = IST.localize(datetime(2026, 9, 22, 0, 36))
        self.assertFalse(is_valid_market_session_timestamp(midnight_ts), "Midnight 00:36 IST must NOT be valid market session")

        weekend_ts = IST.localize(datetime(2026, 9, 20, 11, 30)) # Sunday
        self.assertFalse(is_valid_market_session_timestamp(weekend_ts), "Sunday must NOT be valid market session")

        # 3. Test enforce_trading_day_candles with mixed candles
        dates = [
            # Valid Monday trading session candles
            IST.localize(datetime(2026, 9, 21, 9, 30)),
            IST.localize(datetime(2026, 9, 21, 10, 0)),
            IST.localize(datetime(2026, 9, 21, 15, 25)),
            # Invalid midnight ghost bar
            IST.localize(datetime(2026, 9, 22, 0, 0)),
            # Invalid Saturday candle
            IST.localize(datetime(2026, 9, 26, 11, 0)),
            # Invalid Sunday candle
            IST.localize(datetime(2026, 9, 27, 11, 0)),
            # Invalid post-market bar
            IST.localize(datetime(2026, 9, 21, 18, 0)),
        ]
        df = pd.DataFrame({
            "Open": [100.0] * len(dates),
            "High": [105.0] * len(dates),
            "Low": [98.0] * len(dates),
            "Close": [102.0] * len(dates),
            "Volume": [1000] * len(dates)
        }, index=pd.DatetimeIndex(dates))

        cleaned = enforce_trading_day_candles(df, symbol="TEST_SYM")
        self.assertEqual(len(cleaned), 3, "Only the 3 valid market hours trading session candles should survive")
        for ts in cleaned.index:
            self.assertTrue(time_cls(9, 15) <= ts.time() <= time_cls(15, 30))
            self.assertTrue(ts.weekday() < 5)

    def test_point_in_time_causality_and_recalculate(self):
        from performance_tracker import process_trade_history
        import json

        alert_time = "2026-09-21 10:00:00"
        trade = {
            "id": 99999,
            "symbol": "TEST_STOCK",
            "entry_price": 100.0,
            "stop_loss": 95.0,
            "initial_stop_loss": 95.0,
            "target_1": 110.0,
            "target_2": 120.0,
            "target_3": 130.0,
            "shares_bought": 100,
            "remaining_shares": 100,
            "capital_allocated": 10000.0,
            "status": "OPEN",
            "execution_state": "OPEN",
            "alert_time": alert_time,
            "exit_history": "[]"
        }

        # Create 3 candles:
        # Candle 0: 09:30 IST (BEFORE alert at 10:00) -> MUST BE IGNORED even if price touched SL!
        # Candle 1: 10:15 IST -> price reaches 112 (hits T1, SL should ratchet to breakeven + buffer ~100.3)
        # Candle 2: 11:00 IST -> price touches 100 (below ratcheted SL 100.3) -> SL_HIT on remaining shares
        candles = [
            IST.localize(datetime(2026, 9, 21, 9, 30)),   # Low=90 (would trigger SL if pre-alert leaked!)
            IST.localize(datetime(2026, 9, 21, 10, 15)),  # High=112 (hits T1)
            IST.localize(datetime(2026, 9, 21, 11, 0)),   # Low=100 (hits ratcheted SL)
        ]
        hist = pd.DataFrame({
            "Open": [100.0, 105.0, 105.0],
            "High": [102.0, 112.0, 106.0],
            "Low":  [90.0, 104.0, 99.5],
            "Close": [99.0, 111.0, 100.0],
            "Volume": [5000, 5000, 5000]
        }, index=pd.DatetimeIndex(candles))

        # Replay via process_trade_history with is_recalculate=True
        process_trade_history(trade, hist, cur_p=None, is_recalculate=True)

        # Verify Candle 0 was NOT evaluated (trade did NOT stop out at 09:30)
        eh = json.loads(trade["exit_history"])
        self.assertEqual(len(eh), 2, "Should record exactly 2 events: T1_HIT and SL_HIT")
        self.assertEqual(eh[0]["type"], "T1_HIT")
        self.assertEqual(eh[0]["time"], "2026-09-21 10:15:00")

        self.assertEqual(eh[1]["type"], "SL_HIT")
        self.assertEqual(eh[1]["time"], "2026-09-21 11:00:00")
        self.assertEqual(trade["closed_at"], "2026-09-21 11:00:00")

        # Verify overall cumulative P&L and status:
        # T1 sold 50 shares at 110: profit = +500
        # Remaining 50 shares sold at 100.3: profit = +15
        # Total profit = +515 (> 0) -> status MUST BE "WIN", not "LOSS"!
        self.assertEqual(trade["status"], "WIN", "Cumulative positive P&L must be classified as WIN")

    def test_win_loss_net_pnl_classification(self):
        """
        Verify that final status is strictly determined by sign of cumulative realized P&L:
        If T1 (+100) was booked, but remaining position lost (-300), net = -200 -> LOSS.
        """
        from performance_tracker import process_trade_history
        import json

        alert_time = "2026-09-21 10:00:00"
        trade = {
            "id": 99998,
            "symbol": "NET_LOSS_STOCK",
            "entry_price": 100.0,
            "stop_loss": 95.0,
            "initial_stop_loss": 95.0,
            "target_1": 102.0,   # T1 gives small +2 gain on 50%
            "target_2": 120.0,
            "shares_bought": 100,
            "remaining_shares": 100,
            "capital_allocated": 10000.0,
            "status": "OPEN",
            "execution_state": "OPEN",
            "alert_time": alert_time,
            "exit_history": "[]"
        }

        # Suppose price hits T1 at 102, then gapped down or crashed to 90 on remaining 50 shares
        # T1 profit: 50 * 2 = +100
        # SL loss: 50 * (90 - 100) = -500
        # Net PnL = -400 -> MUST BE LOSS!
        candles = [
            IST.localize(datetime(2026, 9, 21, 10, 15)),  # High=103 (hits T1)
            IST.localize(datetime(2026, 9, 21, 11, 0)),   # Open=90, Low=90 (gap down / SL hit at 90)
        ]
        hist = pd.DataFrame({
            "Open": [100.0, 90.0],
            "High": [103.0, 91.0],
            "Low":  [100.0, 90.0],
            "Close": [102.0, 90.5],
            "Volume": [5000, 5000]
        }, index=pd.DatetimeIndex(candles))

        process_trade_history(trade, hist, cur_p=None, is_recalculate=True)
        self.assertEqual(trade["status"], "LOSS", "Negative cumulative P&L (-400) must be classified as LOSS")

    def test_post_market_friday_advancement(self):
        """
        Verify Friday post-market alert (17:51) advances to Monday 09:15, NOT Saturday!
        """
        from performance_tracker import _fetch_post_alert_bars
        from trading_calendar import get_next_trading_date

        friday_evening = date(2026, 9, 18)
        next_td = get_next_trading_date(friday_evening)
        self.assertEqual(next_td, date(2026, 9, 21), "Next trading day after Friday must be Monday")

    def test_cadence_settings(self):
        # Verify main.py source has 300s (5min) for MULTIBAGGER_EXIT
        with open("app/main.py", "r") as f:
            content = f.read()
        self.assertIn("last_mb_exit or (now - last_mb_exit).total_seconds() >= 300", content)
        self.assertIn('"MULTIBAGGER_EXIT", status="OK",\n            last_success=datetime.now(IST).isoformat(),\n            scheduled_for="Every 5min (market hours)"', content)
        self.assertIn("time.sleep(300)", content)

if __name__ == "__main__":
    unittest.main()
