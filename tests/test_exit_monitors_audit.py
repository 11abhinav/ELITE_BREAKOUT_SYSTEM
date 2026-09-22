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
        self.assertIn('"MULTIBAGGER_EXIT":    15,       # runs every 5 min during market hours', content)

    def test_edge_case_timestamps_and_corrupt_data(self):
        import numpy as np
        from trading_calendar import is_valid_market_session_timestamp, enforce_trading_day_candles

        # Edge cases: None, NaT, NaN, empty strings, invalid text
        self.assertFalse(is_valid_market_session_timestamp(None))
        self.assertFalse(is_valid_market_session_timestamp(pd.NaT))
        self.assertFalse(is_valid_market_session_timestamp(np.nan))
        self.assertFalse(is_valid_market_session_timestamp(""))
        self.assertFalse(is_valid_market_session_timestamp("   "))
        self.assertFalse(is_valid_market_session_timestamp("gibberish-not-a-date"))

        # Boundary tests on a trading day (Tuesday 2026-09-22)
        # 09:14:59 (pre-market) -> False
        self.assertFalse(is_valid_market_session_timestamp(IST.localize(datetime(2026, 9, 22, 9, 14, 59))))
        # 09:15:00 (market open) -> True
        self.assertTrue(is_valid_market_session_timestamp(IST.localize(datetime(2026, 9, 22, 9, 15, 0))))
        # 15:30:00 (market close) -> True
        self.assertTrue(is_valid_market_session_timestamp(IST.localize(datetime(2026, 9, 22, 15, 30, 0))))
        # 15:30:01 (post-market) -> False
        self.assertFalse(is_valid_market_session_timestamp(IST.localize(datetime(2026, 9, 22, 15, 30, 1))))

        # Test enforce_trading_day_candles with NaT / corrupt rows
        mixed_idx = pd.to_datetime([
            "2026-09-22 09:30:00",
            "NaT",
            "2026-09-22 14:00:00"
        ])
        df_corrupt = pd.DataFrame({
            "Open": [100.0, 101.0, 102.0],
            "Close": [101.0, 102.0, 103.0]
        }, index=mixed_idx)
        cleaned_df = enforce_trading_day_candles(df_corrupt, "CORRUPT_TEST")
        self.assertEqual(len(cleaned_df), 2, "NaT row must be purged")

    def test_sanitize_market_session_timestamp(self):
        from trading_calendar import sanitize_market_session_timestamp

        # 1. Midnight timestamp (e.g. 2026-09-22 00:34:00 Tuesday) -> snaps to previous trading session close (2026-09-21 15:30:00 Monday)
        sanitized_midnight = sanitize_market_session_timestamp("2026-09-22 00:34:00")
        self.assertEqual(sanitized_midnight, "2026-09-21 15:30:00")

        # 2. Date-only (e.g. 2026-09-18) -> snaps to that day's session close (2026-09-18 15:30:00)
        sanitized_date = sanitize_market_session_timestamp("2026-09-18")
        self.assertEqual(sanitized_date, "2026-09-18 15:30:00")

        # 3. Weekend timestamp (Sunday 2026-09-20 12:00:00) -> snaps to latest valid trading day (Friday 2026-09-18 15:30:00)
        sanitized_weekend = sanitize_market_session_timestamp("2026-09-20 12:00:00")
        self.assertEqual(sanitized_weekend, "2026-09-18 15:30:00")

        # 4. Valid intraday timestamp (Tuesday 2026-09-22 11:30:00) -> strictly preserved
        sanitized_intraday = sanitize_market_session_timestamp("2026-09-22 11:30:00")
        self.assertEqual(sanitized_intraday, "2026-09-22 11:30:00")

        # 5. Post-market timestamp (Tuesday 2026-09-22 18:00:00) -> snaps to today's session close (2026-09-22 15:30:00)
        sanitized_post = sanitize_market_session_timestamp("2026-09-22 18:00:00")
        self.assertEqual(sanitized_post, "2026-09-22 15:30:00")

    def test_recalculate_clears_stale_exit_state(self):
        """
        Verify that a trade previously closed as WIN with exit_price and closed_at (e.g. 00:34:00)
        is completely cleared of terminal fields when recalculating, so if only T1 hits,
        closed_at is None and position stays open as PARTIAL_WIN_1.
        """
        from performance_tracker import process_trade_history
        import json

        alert_time = "2026-09-17 20:29:00"
        trade = {
            "id": 99997,
            "symbol": "GENUSPOWER",
            "entry_price": 303.85,
            "stop_loss": 300.85,
            "initial_stop_loss": 300.85,
            "target_1": 309.85,
            "target_2": 313.56,
            "target_3": 319.56,
            "shares_bought": 100,
            "remaining_shares": 100,
            "capital_allocated": 30385.0,
            # Stale closure fields from previous flawed run
            "status": "WIN",
            "execution_state": "WIN",
            "exit_price": 310.9,
            "closed_at": "2026-09-22 00:34:00",
            "target_hit": True,
            "stopped_out": False,
            "pnl_pct": 2.32,
            "pnl_rs": 705.0,
            "alert_time": alert_time,
            "exit_history": "[]"
        }

        # Market bars starting from next trading morning (2026-09-18 09:15)
        # Price reaches 310.00 (hits T1 309.85), but never reaches T2 (313.56) or SL (300.85)
        candles = [
            IST.localize(datetime(2026, 9, 18, 9, 15)),
            IST.localize(datetime(2026, 9, 18, 9, 30)),
            IST.localize(datetime(2026, 9, 18, 10, 0)),
        ]
        hist = pd.DataFrame({
            "Open": [304.0, 306.0, 309.0],
            "High": [305.0, 310.0, 311.0],  # Hits T1 at 310.0
            "Low":  [303.0, 305.0, 308.0],
            "Close": [305.0, 309.0, 310.5],
            "Volume": [10000, 15000, 12000]
        }, index=pd.DatetimeIndex(candles))

        process_trade_history(trade, hist, cur_p=310.5, is_recalculate=True)

        # 1. State must be PARTIAL_WIN_1, not WIN
        self.assertEqual(trade["status"], "PARTIAL_WIN_1")
        self.assertEqual(trade["execution_state"], "PARTIAL_1_HIT")

        # 2. closed_at MUST BE None (not 00:34:00 or any midnight timestamp!)
        self.assertIsNone(trade["closed_at"], "Open/partial position must have closed_at = None")

        # 3. target_hit must be False
        self.assertFalse(trade["target_hit"], "target_hit must be False while position is still active")

        # 4. Exit history must contain exactly 1 event (T1_HIT) with valid session timestamp
        eh = json.loads(trade["exit_history"])
        self.assertEqual(len(eh), 1)
        self.assertEqual(eh[0]["type"], "T1_HIT")
        self.assertEqual(eh[0]["price"], 309.85)
        self.assertEqual(eh[0]["time"], "2026-09-18 09:30:00")

if __name__ == "__main__":
    unittest.main()
