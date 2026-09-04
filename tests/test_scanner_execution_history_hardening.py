import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app')))

class TestScannerExecutionHistoryHardening(unittest.TestCase):

    def test_normalize_scanner_name_mappings(self):
        from database import normalize_scanner_name
        self.assertEqual(normalize_scanner_name("WEALTH_EXIT"), "WEALTH_EXIT")
        self.assertEqual(normalize_scanner_name("wealth_exit"), "WEALTH_EXIT")
        self.assertEqual(normalize_scanner_name("WEALTH_INTRADAY"), "WEALTH_EXIT")
        self.assertEqual(normalize_scanner_name("MULTI_TF_5M"), "MULTI_TF_5M")
        self.assertEqual(normalize_scanner_name("multitf_5m"), "MULTI_TF_5M")
        self.assertEqual(normalize_scanner_name("MULTIBAGGER_EXIT"), "MULTIBAGGER_EXIT")
        self.assertEqual(normalize_scanner_name("WEALTH_ENGINE"), "Wealth Engine")
        self.assertEqual(normalize_scanner_name("MULTI_TF"), "MULTI_TF")

    def test_start_scanner_execution_run_skipped_duplicate_bypasses_concurrency(self):
        from database import start_scanner_execution_run
        # Even if is_scanner_actively_running returns True, SKIPPED_DUPLICATE initial_status must not raise
        with patch("database.is_scanner_actively_running", return_value=True), \
             patch("database.get_connection") as mock_conn:
            mock_cur = MagicMock()
            mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = mock_cur
            
            ctx = start_scanner_execution_run(
                scanner_name="MULTI_TF",
                trigger_type="SCHEDULED",
                initial_status="SKIPPED_DUPLICATE"
            )
            self.assertIsNotNone(ctx)
            self.assertEqual(ctx.scanner_name, "MULTI_TF")

    def test_record_skipped_execution_run_creates_record(self):
        from database import record_skipped_execution_run
        with patch("database.get_connection") as mock_conn:
            mock_cur = MagicMock()
            mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = mock_cur
            
            run_id = record_skipped_execution_run(
                scanner_name="EOD",
                trigger_type="SCHEDULED",
                stop_reason="Scanner lock held (previous run active)"
            )
            self.assertIsNotNone(run_id)
            self.assertTrue(mock_cur.execute.called)
            # Verify SQL query inserted SKIPPED_DUPLICATE
            sql_arg = mock_cur.execute.call_args[0][0]
            self.assertIn("SKIPPED_DUPLICATE", sql_arg)

if __name__ == "__main__":
    unittest.main()
