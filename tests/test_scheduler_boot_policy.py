import unittest
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch, MagicMock

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for p in (APP_DIR, ROOT_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

IST = ZoneInfo("Asia/Kolkata")

class TestSchedulerBootPolicy(unittest.TestCase):
    """
    Test suite verifying scheduler boot policy invariants:
    1. Server restart triggers boot catch-up execution across all primary scanners.
    2. BOOT CATCH-UP invokes the exact same production scanner pipeline as scheduled runs,
       differing strictly only in trigger metadata (trigger_type='NON_MARKET_BOOT', scheduler_name='NON_MARKET_BOOT').
    3. Same-day alert deduplication prevents duplicate alerts when catch-up runs after an earlier scheduled run.
    """

    def test_run_all_seven_scanners_non_market_boot_pipeline(self):
        """Verify that boot catch-up triggers all 5 primary scanners with NON_MARKET_BOOT metadata."""
        import app.main as main_mod

        calls = []

        def mock_db(*args, **kwargs): calls.append(("DAILY_BUILDER", kwargs))
        def mock_tech(*args, **kwargs): calls.append(("TECHNICAL", kwargs))
        def mock_fun(*args, **kwargs): calls.append(("FUNDAMENTAL", kwargs))
        def mock_qc(*args, **kwargs): calls.append(("QUALITY_COMPOUNDER", kwargs))
        def mock_qvr(*args, **kwargs): calls.append(("QUALITY_VALUE_RECOVERY", kwargs))

        # Synchronous thread runner to execute background batch inline during test
        def mock_thread_start(thread_self):
            if thread_self.name == "NonMarketBootBatch":
                thread_self._target(*thread_self._args, **thread_self._kwargs)

        with patch.object(main_mod, "_trigger_daily_builder", side_effect=mock_db), \
             patch.object(main_mod, "_trigger_technical", side_effect=mock_tech), \
             patch.object(main_mod, "_trigger_fundamental", side_effect=mock_fun), \
             patch.object(main_mod, "_trigger_quality_compounder_v2", side_effect=mock_qc), \
             patch.object(main_mod, "_trigger_quality_value_recovery", side_effect=mock_qvr), \
             patch("threading.Thread.start", side_effect=mock_thread_start, autospec=True), \
             patch("time.sleep", return_value=None):

            main_mod.run_all_seven_scanners_non_market_boot()

        scanners_called = [c[0] for c in calls]
        self.assertIn("DAILY_BUILDER", scanners_called)
        self.assertIn("TECHNICAL", scanners_called)
        self.assertIn("FUNDAMENTAL", scanners_called)
        self.assertIn("QUALITY_COMPOUNDER", scanners_called)
        self.assertIn("QUALITY_VALUE_RECOVERY", scanners_called)

        # Verify each trigger was called with NON_MARKET_BOOT metadata
        for name, kwargs in calls:
            self.assertEqual(kwargs.get("trigger_type"), "NON_MARKET_BOOT")
            self.assertEqual(kwargs.get("scheduler_name"), "NON_MARKET_BOOT")

    def test_same_day_alert_deduplication_contract(self):
        """Verify that save_alert_if_new deduplicates alerts for the same symbol and scanner on the same day."""
        from app.database import save_alert_if_new, init_db
        init_db()

        test_symbol = "TEST_DEDUP_SYMBOL"
        test_scanner = "QUALITY_COMPOUNDER"
        today_date = datetime.now(IST).date()

        # Mock database cursor to simulate an existing alert for same date
        mock_cursor = MagicMock()
        mock_cursor.fetchone.side_effect = [
            (999, test_symbol, 1000.0, 900.0, 1200.0, today_date, None, test_scanner, "OPEN", "QUALITY_COMPOUNDER"),  # prior_open_alert query
        ]

        class CustomMockConn:
            def cursor(self, *args, **kwargs): return mock_cursor
            def commit(self): pass
            def rollback(self): pass
            def close(self): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass

        with patch("app.database.get_connection", return_value=CustomMockConn()):
            inserted, msg, cap, shares = save_alert_if_new(
                symbol=test_symbol,
                breakout_type="QUALITY_COMPOUNDER",
                alert_time=datetime.now(IST).isoformat(),
                scanner=test_scanner,
                entry_price=1000.0,
                stop_loss=900.0,
                target_1=1200.0,
                score=90,
                bayesian_regime="BULL",
                alert_date=today_date
            )

            self.assertFalse(inserted, "Boot catch-up scan must NOT emit duplicate alerts for a symbol already alerted today!")
            self.assertTrue("blocked" in msg.lower() or "duplicate" in msg.lower() or "already" in msg.lower())

if __name__ == "__main__":
    unittest.main()
