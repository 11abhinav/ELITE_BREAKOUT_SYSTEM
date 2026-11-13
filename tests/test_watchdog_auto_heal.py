import unittest
from unittest.mock import MagicMock, patch
import time

class TestWatchdogAutoHeal(unittest.TestCase):
    def test_auto_heal_stuck_runs_definition(self):
        from database import auto_heal_stuck_scanner_runs, SCANNER_MAX_RUNTIME_MINUTES
        self.assertIn("PERFORMANCE_TRACKER", SCANNER_MAX_RUNTIME_MINUTES)
        self.assertLessEqual(SCANNER_MAX_RUNTIME_MINUTES["PERFORMANCE_TRACKER"], 15.0)
        self.assertIn("TECHNICAL", SCANNER_MAX_RUNTIME_MINUTES)
        self.assertEqual(SCANNER_MAX_RUNTIME_MINUTES["TECHNICAL"], 60.0)

    def test_scanner_run_context_safety_fuse_configuration(self):
        from scanner_run_context import SCANNER_MAX_EXECUTION_SECONDS, ScannerRunContext
        self.assertIn("PERFORMANCE_TRACKER", SCANNER_MAX_EXECUTION_SECONDS)
        self.assertLessEqual(SCANNER_MAX_EXECUTION_SECONDS["PERFORMANCE_TRACKER"], 900)
        self.assertIn("TECHNICAL", SCANNER_MAX_EXECUTION_SECONDS)
        self.assertEqual(SCANNER_MAX_EXECUTION_SECONDS["TECHNICAL"], 3600)

    @patch("psycopg2.extras.execute_values")
    @patch("database.get_connection")
    def test_bulk_update_alert_current_prices(self, mock_conn, mock_exec_val):
        from database import bulk_update_alert_current_prices
        mock_cur = MagicMock()
        mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = mock_cur
        
        updates = [(1, 100.5), (2, 250.75)]
        bulk_update_alert_current_prices(updates)
        self.assertTrue(mock_exec_val.called)
        self.assertTrue(mock_conn.return_value.__enter__.return_value.commit.called)

    @patch("database.get_connection")
    @patch("database.insert_notification")
    def test_auto_heal_terminates_stuck_performance_tracker(self, mock_notif, mock_conn):
        from database import auto_heal_stuck_scanner_runs
        
        # Mock cursor returning a stuck PERFORMANCE_TRACKER run (duration 25 mins > 10 min ceiling)
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            {
                "run_id": "test_stuck_run_123456",
                "scanner_name": "PERFORMANCE_TRACKER",
                "started_at": "2026-11-12 15:05:36",
                "heartbeat_at": "2026-11-12 15:30:00",
                "lifecycle_status": "RUNNING",
                "duration_min": 25.0,
                "silent_min": 0.5
            }
        ]
        mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = mock_cur
        
        terminated = auto_heal_stuck_scanner_runs()
        self.assertEqual(len(terminated), 1)
        self.assertEqual(terminated[0]["run_id"], "test_stuck_run_123456")
        self.assertIn("Exceeded maximum allowed runtime", terminated[0]["reason"])
        
        # Verify DB updates executed
        self.assertTrue(mock_cur.execute.called)
        # Verify in-app admin notification called
        mock_notif.assert_called_once()
        self.assertIn("PERFORMANCE_TRACKER", mock_notif.call_args[0][1])

    @patch("database.is_scanner_stopped", return_value=False)
    @patch("database.start_scanner_execution_run")
    @patch("database.complete_scanner_execution_run")
    @patch("database.upsert_scanner_health")
    @patch("performance_tracker.build_performance_data")
    def test_trigger_performance_tracker_completes_run_ctx(
        self, mock_build, mock_upsert, mock_complete, mock_start, mock_stopped
    ):
        from main import _trigger_performance_tracker
        mock_ctx = MagicMock()
        mock_start.return_value = mock_ctx
        
        res = _trigger_performance_tracker()
        self.assertEqual(res["total_count"], 1)
        mock_build.assert_called_once_with(force_live_fetch=True, run_ctx=mock_ctx)
        mock_complete.assert_called_once_with(mock_ctx)
        self.assertTrue(any(call[1].get("status") == "OK" for call in mock_upsert.call_args_list))

if __name__ == "__main__":
    unittest.main()

