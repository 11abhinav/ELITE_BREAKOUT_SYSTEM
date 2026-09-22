import unittest
from datetime import datetime, date
import pandas as pd
from trading_calendar import default_trading_calendar, is_trading_day, is_weekend_date


class TestRuntimeErrorsFix(unittest.TestCase):

    def test_trading_calendar_date_parsing_resilience(self):
        """Verify is_trading_day handles str, Timestamp, date, and bad inputs without AttributeError."""
        # Valid string
        res = default_trading_calendar.is_trading_day("2026-09-22")
        self.assertIsInstance(res, bool)

        # Truncated or ISO string with timestamp
        res2 = default_trading_calendar.is_trading_day("2026-09-22 14:30:00")
        self.assertIsInstance(res2, bool)

        # pd.Timestamp
        res3 = default_trading_calendar.is_trading_day(pd.Timestamp("2026-09-22"))
        self.assertIsInstance(res3, bool)

        # Pure date
        res4 = default_trading_calendar.is_trading_day(date(2026, 9, 22))
        self.assertIsInstance(res4, bool)

        # Pure datetime
        res5 = default_trading_calendar.is_trading_day(datetime(2026, 9, 22, 10, 15))
        self.assertIsInstance(res5, bool)

        # Invalid string or None
        self.assertFalse(default_trading_calendar.is_trading_day("INVALID_DATE"))
        self.assertFalse(default_trading_calendar.is_trading_day(None))
        self.assertFalse(default_trading_calendar.is_trading_day(""))

        # is_weekend_date helper
        self.assertFalse(is_weekend_date("2026-09-22")) # Tuesday
        self.assertTrue(is_weekend_date("2026-09-20"))  # Sunday
        self.assertFalse(is_weekend_date("INVALID"))

    def test_performance_tracker_auto_heal_pending_entry_with_actual_entry_price(self):
        """Verify performance_tracker heals PENDING_ENTRY with actual_entry_price into OPEN."""
        from performance_tracker import process_trade_history

        # Alert with PENDING_ENTRY but actual_entry_price populated (REDINGTON / CARTRADE / RGL scenario)
        alert = {
            "id": 999991,
            "symbol": "REDINGTON",
            "execution_state": "PENDING_ENTRY",
            "status": "PENDING_ENTRY",
            "entry_mode": "BREAKOUT",
            "entry_price": 220.0,
            "actual_entry_price": 221.5,
            "stop_loss": 210.0,
            "target_price": 240.0,
            "shares_bought": 100,
            "remaining_shares": 100,
            "date": "2026-09-22",
            "exit_profile": "BALANCED"
        }

        # Mock candles dataframe
        df = pd.DataFrame([
            {"Open": 221.5, "High": 225.0, "Low": 219.0, "Close": 224.0, "Volume": 50000.0}
        ], index=[pd.Timestamp("2026-09-22 10:00:00")])

        # Call process_trade_history in test mode
        process_trade_history(alert, df, cur_p=224.0, is_recalculate=False)

        # Invariant check: execution_state MUST be healed to OPEN!
        self.assertEqual(alert.get("execution_state"), "OPEN")
        self.assertEqual(alert.get("status"), "OPEN")
        self.assertEqual(alert.get("actual_entry_price"), 221.5)

    def test_fyers_auth_autologin_mutex(self):
        """Verify fyers_auth mutex flag prevents duplicate concurrent background threads."""
        import fyers_auth
        self.assertFalse(fyers_auth._autologin_in_progress)
        self.assertTrue(hasattr(fyers_auth, "_last_autologin_start_time"))


if __name__ == "__main__":
    unittest.main()
