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

    def test_process_lock_context_manager(self):
        """Verify ProcessLockImpl supports the context manager protocol ('with lock:')."""
        from lock_utils import ProcessLock
        lock = ProcessLock("unit_test_ctx_lock")
        self.assertFalse(lock.locked())
        with lock:
            self.assertTrue(lock.locked())
        self.assertFalse(lock.locked())

    def test_trading_calendar_is_trading_day_types(self):
        """Verify TradingCalendar.is_trading_day accepts string, date, datetime, and Timestamp."""
        from trading_calendar import default_trading_calendar
        # Monday 2026-09-21 is a trading day
        self.assertTrue(default_trading_calendar.is_trading_day("2026-09-21"))
        self.assertTrue(default_trading_calendar.is_trading_day(date(2026, 9, 21)))
        self.assertTrue(default_trading_calendar.is_trading_day(datetime(2026, 9, 21, 10, 30)))
        self.assertTrue(default_trading_calendar.is_trading_day(pd.Timestamp("2026-09-21 10:30:00")))

        # Sunday 2026-09-20 is a weekend
        self.assertFalse(default_trading_calendar.is_trading_day("2026-09-20"))
        self.assertFalse(default_trading_calendar.is_trading_day(date(2026, 9, 20)))
        self.assertFalse(default_trading_calendar.is_trading_day(datetime(2026, 9, 20, 10, 30)))
        self.assertFalse(default_trading_calendar.is_trading_day(pd.Timestamp("2026-09-20 10:30:00")))

    def test_enforce_trading_day_candles_daily_vs_intraday(self):
        """
        Verify enforce_trading_day_candles does NOT purge valid daily (1d) candles
        even when timestamps are 00:00:00 or 05:30:00, but DOES purge midnight ghost
        bars in intraday datasets.
        """
        from trading_calendar import enforce_trading_day_candles

        # 1. Daily DataFrame (1 bar per date, 10 trading days, midnight timestamps 00:00:00)
        daily_dates = [
            "2026-09-01 00:00:00", "2026-09-02 00:00:00", "2026-09-03 00:00:00",
            "2026-09-04 00:00:00", "2026-09-07 00:00:00", "2026-09-08 00:00:00",
            "2026-09-09 00:00:00", "2026-09-10 00:00:00", "2026-09-11 00:00:00",
            "2026-09-21 09:55:00"  # Live bar appended
        ]
        df_daily = pd.DataFrame({
            "Date": daily_dates,
            "Open": [100.0 + i for i in range(len(daily_dates))],
            "High": [105.0 + i for i in range(len(daily_dates))],
            "Low": [95.0 + i for i in range(len(daily_dates))],
            "Close": [102.0 + i for i in range(len(daily_dates))],
            "Volume": [1000 * (i + 1) for i in range(len(daily_dates))]
        })
        cleaned_daily = enforce_trading_day_candles(df_daily, "TEST_DAILY")
        self.assertEqual(len(cleaned_daily), len(daily_dates), "Daily bars must NOT be purged as off-hours")

        # 2. Intraday DataFrame (Multiple bars per date, has 00:00:00 ghost bar + market hour bars)
        intraday_dates = [
            "2026-09-21 00:00:00",  # Midnight ghost bar -> MUST be purged
            "2026-09-21 09:15:00",
            "2026-09-21 09:30:00",
            "2026-09-21 10:00:00",
            "2026-09-21 15:30:00",
            "2026-09-21 16:00:00",  # Post-market bar -> MUST be purged
        ]
        df_intraday = pd.DataFrame({
            "Date": intraday_dates,
            "Open": [200.0] * len(intraday_dates),
            "High": [205.0] * len(intraday_dates),
            "Low": [195.0] * len(intraday_dates),
            "Close": [202.0] * len(intraday_dates),
            "Volume": [500] * len(intraday_dates)
        })
        cleaned_intraday = enforce_trading_day_candles(df_intraday, "TEST_INTRADAY")
        self.assertEqual(len(cleaned_intraday), 4, "Midnight and post-market bars in intraday data must be purged")

    def test_corporate_actions_get_bulk_split_factor_kwargs(self):
        """Verify get_bulk_split_factor accepts both entry_date and entry_d."""
        from corporate_actions import get_bulk_split_factor
        # Calling with entry_date
        f1 = get_bulk_split_factor("RELIANCE", entry_date=date(2025, 1, 1))
        # Calling with entry_d alias
        f2 = get_bulk_split_factor("RELIANCE", entry_d=date(2025, 1, 1))
        self.assertEqual(f1, f2)
        self.assertIsInstance(f1, float)

    def test_diagnostics_startup_check(self):
        """Verify startup diagnostics run without throwing exceptions."""
        from diagnostics import run_startup_diagnostics
        res = run_startup_diagnostics()
        self.assertTrue(res.get("storage_ok"), "Storage check should succeed")
        self.assertTrue(res.get("calendar_ok"), "Calendar check should succeed")

    def test_market_and_legacy_entry_mode_auto_heal_and_sl_exit(self):
        """Verify that MARKET and LEGACY_UNKNOWN trades in PENDING_ENTRY state (e.g. RVNL) auto-heal to OPEN and exit on SL hit."""
        from performance_tracker import process_trade_history

        # 1. Simulate RVNL alert: MARKET entry, PENDING_ENTRY execution_state
        trade_rvnl = {
            "id": 88888,
            "symbol": "RVNL",
            "scanner": "SHORT_COVERING_5M",
            "entry_mode": "MARKET",
            "execution_state": "PENDING_ENTRY",
            "entry_price": 212.42,
            "stop_loss": 211.29,
            "initial_stop_loss": 211.29,
            "target_1": 216.0,
            "target_2": 220.0,
            "target_3": 225.0,
            "shares_bought": 100,
            "remaining_shares": 100,
            "capital_allocated": 21242.0,
            "status": "OPEN",
            "alert_time": "2026-09-22 11:45:00",
            "exit_history": "[]"
        }

        # Create candle where price drops to 210.50 (breaching SL 211.29)
        candles = [
            IST.localize(datetime(2026, 9, 22, 11, 50)),
        ]
        df_rvnl = pd.DataFrame({
            "Open": [212.0],
            "High": [212.5],
            "Low":  [210.5],  # SL breached!
            "Close": [210.8],
            "Volume": [100000]
        }, index=pd.DatetimeIndex(candles))

        process_trade_history(trade_rvnl, hist=df_rvnl, cur_p=210.50, is_recalculate=False)

        # Assert RVNL is NOT rejected in limbo; it must transition to SL_HIT / LOSS
        self.assertEqual(trade_rvnl["execution_state"], "SL_HIT", "RVNL must transition from PENDING_ENTRY to SL_HIT")
        self.assertEqual(trade_rvnl["status"], "LOSS", "RVNL must close with status LOSS")
        self.assertTrue(trade_rvnl["stopped_out"], "RVNL stopped_out must be True")
        self.assertIsNotNone(trade_rvnl["closed_at"], "RVNL closed_at must be populated")
        self.assertEqual(trade_rvnl["remaining_shares"], 0, "Remaining shares must be 0")

        # 2. Simulate Legacy Alert with LEGACY_UNKNOWN entry_mode in PENDING_ENTRY state
        trade_legacy = {
            "id": 88889,
            "symbol": "TATASTEEL",
            "scanner": "BREAKOUT",
            "entry_mode": "LEGACY_UNKNOWN",
            "execution_state": "PENDING_ENTRY",
            "entry_price": 150.0,
            "stop_loss": 145.0,
            "initial_stop_loss": 145.0,
            "target_1": 160.0,
            "shares_bought": 50,
            "remaining_shares": 50,
            "capital_allocated": 7500.0,
            "status": "OPEN",
            "alert_time": "2026-09-22 10:00:00",
            "exit_history": "[]"
        }
        df_legacy = pd.DataFrame({
            "Open": [149.0],
            "High": [151.0],
            "Low":  [144.0],  # Breaches SL 145.0
            "Close": [144.5],
            "Volume": [50000]
        }, index=pd.DatetimeIndex([IST.localize(datetime(2026, 9, 22, 10, 15))]))

        process_trade_history(trade_legacy, hist=df_legacy, cur_p=144.50, is_recalculate=False)

        self.assertEqual(trade_legacy["execution_state"], "SL_HIT", "Legacy alert must transition to SL_HIT")
        self.assertEqual(trade_legacy["status"], "LOSS", "Legacy alert must close with status LOSS")
        self.assertTrue(trade_legacy["stopped_out"], "Legacy alert stopped_out must be True")

    def test_small_quantity_share_division_and_full_close(self):
        """Verify that a 4-share trade correctly scales 1 at T1, 1 at T2, and remaining 2 at T3, and cleanly closes."""
        from performance_tracker import process_trade_history, _calc_shares_to_sell
        import json

        # 1. Direct unit test of _calc_shares_to_sell
        cfg = [20, 30, 50]
        s1 = _calc_shares_to_sell(shares_bought=4, rem_shares=4, target_idx=0, exit_config=cfg, has_next_target=True, has_future_target_after_next=True)
        self.assertEqual(s1, 1, "T1 must sell 1 share (reserving 2 for T2 and T3)")
        s2 = _calc_shares_to_sell(shares_bought=4, rem_shares=3, target_idx=1, exit_config=cfg, has_next_target=True, has_future_target_after_next=False)
        self.assertEqual(s2, 1, "T2 must sell 1 share (reserving 1 for T3)")
        s3 = _calc_shares_to_sell(shares_bought=4, rem_shares=2, target_idx=2, exit_config=cfg, has_next_target=False)
        self.assertEqual(s3, 2, "T3 must sell all remaining 2 shares")

        # 1 share total
        s_single = _calc_shares_to_sell(shares_bought=1, rem_shares=1, target_idx=0, exit_config=cfg, has_next_target=True, has_future_target_after_next=True)
        self.assertEqual(s_single, 1, "Single share trade must sell 1 share at T1")

        # 2. End-to-end replay test for GENUSPOWER setup: 4 shares, Entry: 303.85, T1: 309.85, T2: 313.56, T3: 319.56
        trade_genus = {
            "id": 99991,
            "symbol": "GENUSPOWER",
            "scanner": "MULTI_TF",
            "entry_mode": "MARKET",
            "execution_state": "OPEN",
            "entry_price": 303.85,
            "actual_entry_price": 303.85,
            "stop_loss": 300.85,
            "initial_stop_loss": 300.85,
            "target_1": 309.85,
            "target_2": 313.56,
            "target_3": 319.56,
            "shares_bought": 4,
            "remaining_shares": 4,
            "capital_allocated": 1215.4,
            "status": "OPEN",
            "alert_time": "2026-09-17 20:29:00",
            "exit_history": "[]"
        }

        # First candle breaches T1 (high=310.9) but not T2
        df_t1 = pd.DataFrame({
            "Open": [305.0],
            "High": [310.9],  # T1 breached
            "Low":  [304.0],
            "Close": [310.0],
            "Volume": [20000]
        }, index=pd.DatetimeIndex([IST.localize(datetime(2026, 9, 22, 14, 45))]))

        process_trade_history(trade_genus, hist=df_t1, cur_p=310.9, is_recalculate=True)

        self.assertEqual(trade_genus["status"], "PARTIAL_WIN_1", "Position must be PARTIAL_WIN_1 after T1 hit")
        self.assertEqual(trade_genus["remaining_shares"], 3, "Position must have 3 shares remaining after T1")
        eh1 = json.loads(trade_genus["exit_history"])
        self.assertEqual(len(eh1), 1, "Must have exactly 1 exit event for T1")
        self.assertEqual(eh1[0]["type"], "T1_HIT")
        self.assertEqual(eh1[0]["shares"], 1, "Must have sold exactly 1 share at T1")

        # Second candle reaches T2 (high=314.0)
        df_t2 = pd.DataFrame({
            "Open": [305.0, 310.0],
            "High": [310.9, 314.0],  # Candle 1 hits T1 (309.85), Candle 2 hits T2 (313.56)
            "Low":  [304.0, 310.0],  # Low 310.0 stays above SL
            "Close": [310.0, 313.8],
            "Volume": [20000, 25000]
        }, index=pd.DatetimeIndex([
            IST.localize(datetime(2026, 9, 22, 14, 45)),
            IST.localize(datetime(2026, 9, 22, 14, 50))
        ]))

        process_trade_history(trade_genus, hist=df_t2, cur_p=313.8, is_recalculate=True)
        self.assertEqual(trade_genus["status"], "PARTIAL_WIN_2", "Position must be PARTIAL_WIN_2 after T2 hit")
        self.assertEqual(trade_genus["remaining_shares"], 2, "Position must have 2 shares remaining after T2")
        eh2 = json.loads(trade_genus["exit_history"])
        self.assertEqual(len(eh2), 2, "Must have exactly 2 exit events (T1_HIT and T2_HIT)")
        self.assertEqual(eh2[1]["type"], "T2_HIT")
        self.assertEqual(eh2[1]["shares"], 1, "Must have sold exactly 1 share at T2")

        # Third candle reaches T3 (high=320.0) -> Full close as WIN!
        df_t3 = pd.DataFrame({
            "Open": [305.0, 310.0, 314.0],
            "High": [310.9, 314.0, 320.0],  # Candle 3 hits T3 (319.56)
            "Low":  [304.0, 310.0, 314.0],
            "Close": [310.0, 313.8, 319.8],
            "Volume": [20000, 25000, 30000]
        }, index=pd.DatetimeIndex([
            IST.localize(datetime(2026, 9, 22, 14, 45)),
            IST.localize(datetime(2026, 9, 22, 14, 50)),
            IST.localize(datetime(2026, 9, 22, 14, 55))
        ]))

        process_trade_history(trade_genus, hist=df_t3, cur_p=319.8, is_recalculate=True)
        self.assertEqual(trade_genus["status"], "WIN", "Position must close cleanly as WIN after all shares sold at T3")
        self.assertEqual(trade_genus["remaining_shares"], 0, "Position must have 0 remaining shares")
        self.assertTrue(trade_genus["target_hit"], "target_hit must be True")
        self.assertIsNotNone(trade_genus["closed_at"], "closed_at must be populated")
        eh3 = json.loads(trade_genus["exit_history"])
        self.assertEqual(len(eh3), 3, "Must have 3 exit events total")
        self.assertEqual(eh3[2]["shares"], 2, "Must have sold final 2 shares at T3")

    def test_single_share_trade_closes_as_win_on_t1(self):
        """Verify that a 1-share trade cleanly and immediately closes as WIN when T1 is hit."""
        from performance_tracker import process_trade_history

        trade_single = {
            "id": 99992,
            "symbol": "ONE_SHARE_CO",
            "scanner": "MULTI_TF",
            "entry_mode": "MARKET",
            "execution_state": "OPEN",
            "entry_price": 100.0,
            "actual_entry_price": 100.0,
            "stop_loss": 95.0,
            "initial_stop_loss": 95.0,
            "target_1": 105.0,
            "target_2": 110.0,
            "target_3": 115.0,
            "shares_bought": 1,
            "remaining_shares": 1,
            "capital_allocated": 100.0,
            "status": "OPEN",
            "alert_time": "2026-09-22 10:00:00",
            "exit_history": "[]"
        }

        df = pd.DataFrame({
            "Open": [102.0],
            "High": [106.0],  # T1 hit
            "Low":  [101.0],
            "Close": [105.5],
            "Volume": [1000]
        }, index=pd.DatetimeIndex([IST.localize(datetime(2026, 9, 22, 10, 15))]))

        process_trade_history(trade_single, hist=df, cur_p=105.5, is_recalculate=True)
        self.assertEqual(trade_single["status"], "WIN", "Single share trade must close as WIN, not remain OPEN or PARTIAL_WIN")
        self.assertEqual(trade_single["remaining_shares"], 0, "Remaining shares must be 0")
        self.assertTrue(trade_single["target_hit"], "target_hit must be True")
        self.assertIsNotNone(trade_single["closed_at"])

    def test_exit_history_deduplication_defense_in_depth(self):
        """Verify that existing duplicate events in DB exit_history are sanitized and never duplicated."""
        from performance_tracker import process_trade_history
        import json

        # Simulate corrupt DB state with duplicate T1_HIT events (like in the bug)
        corrupted_hist = [
            {"type": "T1_HIT", "price": 309.85, "shares": 1, "pnl": 6.0, "time": "2026-09-22 14:45:00"},
            {"type": "T1_HIT", "price": 311.15, "shares": 1, "pnl": 7.3, "time": "2026-09-22 14:47:00"}
        ]
        trade = {
            "id": 99993,
            "symbol": "GENUSPOWER",
            "scanner": "MULTI_TF",
            "entry_mode": "MARKET",
            "execution_state": "PARTIAL_1_HIT",
            "entry_price": 303.85,
            "actual_entry_price": 303.85,
            "stop_loss": 304.76,
            "initial_stop_loss": 300.85,
            "target_1": 309.85,
            "target_2": 313.56,
            "target_3": 319.56,
            "shares_bought": 4,
            "remaining_shares": 3,
            "capital_allocated": 1215.4,
            "status": "PARTIAL_WIN_1",
            "alert_time": "2026-09-17 20:29:00",
            "exit_history": json.dumps(corrupted_hist)
        }

        # Run live exit monitor (is_recalculate=False) with current price between T1 and T2
        df = pd.DataFrame({
            "Open": [310.0],
            "High": [311.5],
            "Low":  [309.5],
            "Close": [311.0],
            "Volume": [15000]
        }, index=pd.DatetimeIndex([IST.localize(datetime(2026, 9, 22, 14, 55))]))

        process_trade_history(trade, hist=df, cur_p=311.0, is_recalculate=False)

        # After processing, history must be deduplicated
        eh = json.loads(trade["exit_history"]) if isinstance(trade["exit_history"], str) else trade["exit_history"]
        t1_events = [e for e in eh if e.get("type") == "T1_HIT"]
        self.assertEqual(len(t1_events), 1, "Duplicate T1_HIT events must be deduplicated to exactly 1")
        self.assertEqual(trade["remaining_shares"], 3, "Remaining shares must correctly be 3 (4 bought - 1 sold at T1)")
        self.assertEqual(trade["status"], "PARTIAL_WIN_1")

if __name__ == "__main__":
    unittest.main()
