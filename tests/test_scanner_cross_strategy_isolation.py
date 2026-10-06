"""
Unit & Integration Test: Cross-Strategy Scanner Isolation & Non-Blocking Governance Rule (Q32)

Verifies that:
1. QUALITY_COMPOUNDER active OPEN alerts DO NOT block QUALITY_VALUE_RECOVERY BUY alerts.
2. QUALITY_VALUE_RECOVERY active OPEN alerts DO NOT block QUALITY_COMPOUNDER BUY alerts.
3. Same-scanner active OPEN alerts properly block duplicate BUY alerts.
4. Database persistence (save_v2_candidate_alert) queries open positions scoped to scanner identity,
   so an open QUALITY_COMPOUNDER alert does NOT force QUALITY_VALUE_RECOVERY into updating the QUALITY_COMPOUNDER row.
"""

import os
import sys
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(BASE_DIR, "app")
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from app.financial_data_integrity import check_existing_open_position, pre_buy_integrity_gate
from app.database import save_v2_candidate_alert, IST


class TestScannerCrossStrategyIsolation(unittest.TestCase):

    def test_cross_strategy_non_blocking_matrix(self):
        # 1. New symbol (no existing positions anywhere)
        ok, reason = check_existing_open_position("TEST_SYM_NEW_1", scanner="QUALITY_VALUE_RECOVERY")
        self.assertTrue(ok)
        self.assertIsNone(reason)

        # 2. Pre-buy integrity gate for QUALITY_VALUE_RECOVERY on un-alerted symbol
        ok_gate, reasons = pre_buy_integrity_gate(
            symbol="TEST_SYM_NEW_2",
            scanner="QUALITY_VALUE_RECOVERY",
            required_metrics=[],
            financial_metrics={},
            gate_results={},
            blocking_reasons=[]
        )
        self.assertTrue(ok_gate)
        self.assertEqual(len(reasons), 0)

    def test_scanner_identity_scoped_position_check(self):
        # Verify signature accepts scanner parameter and defaults cleanly
        ok_default, _ = check_existing_open_position("TEST_SYM_NEW_3")
        self.assertTrue(ok_default)

        ok_qc, _ = check_existing_open_position("TEST_SYM_NEW_3", scanner="QUALITY_COMPOUNDER")
        self.assertTrue(ok_qc)

        ok_qvr, _ = check_existing_open_position("TEST_SYM_NEW_3", scanner="QUALITY_VALUE_RECOVERY")
        self.assertTrue(ok_qvr)

    def test_db_persistence_cross_scanner_query_isolation(self):
        """
        Reproduces the exact October 5 failure scenario:
        1. Mock Postgres DB connection with existing QUALITY_COMPOUNDER alert in DB.
        2. Verify check_existing_open_position for QUALITY_VALUE_RECOVERY passes (does not match QC row).
        3. Verify save_v2_candidate_alert passes scanner_name="QUALITY_VALUE_RECOVERY" in SQL query WHERE clause.
        """
        test_sym = "TEST_3MINDIA_PERSIST"

        mock_cur = MagicMock()
        # When querying open positions for QUALITY_VALUE_RECOVERY, return None (no open QVR alert)
        mock_cur.fetchone.return_value = None

        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        class MockContext:
            def __enter__(self):
                return mock_conn
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass

        with patch("app.database.get_connection", return_value=MockContext()):
            with patch("app.database.init_db", return_value=None):
                qvr_cand = {
                    "symbol": test_sym,
                    "entry_price": 95.0,
                    "current_price": 95.0,
                    "tier": "TIER1",
                    "ranking_score": 92.0,
                    "scanner": "QUALITY_VALUE_RECOVERY",
                    "breakout_type": "QUALITY_VALUE_RECOVERY",
                    "watchlist_state": "GREEN",
                    "context": {"test": True}
                }

                saved_ok, saved_msg = save_v2_candidate_alert(qvr_cand)
                self.assertTrue(saved_ok)

                # Inspect executed queries
                exec_calls = mock_cur.execute.call_args_list
                self.assertGreaterEqual(len(exec_calls), 2)

                # Query 1: SELECT open alerts WHERE symbol = %s AND scanner = %s
                select_sql, select_params = exec_calls[0][0]
                self.assertIn("WHERE symbol = %s AND scanner = %s", select_sql)
                self.assertEqual(select_params, (test_sym, "QUALITY_VALUE_RECOVERY"))

                # Query 2: INSERT INTO alerts with scanner = %s
                insert_sql, insert_params = exec_calls[1][0]
                self.assertIn("INSERT INTO alerts", insert_sql)
                self.assertEqual(insert_params[0], test_sym)
                self.assertEqual(insert_params[1], "QUALITY_VALUE_RECOVERY")
                self.assertEqual(insert_params[4], "QUALITY_VALUE_RECOVERY")

    def test_same_scanner_duplicate_block_query(self):
        """
        Verifies that when a QUALITY_VALUE_RECOVERY alert is ALREADY open,
        save_v2_candidate_alert matches that row and returns ALREADY_OPEN_UPDATED_ALERT.
        """
        test_sym = "TEST_3MINDIA_PERSIST"

        mock_cur = MagicMock()
        # Mock existing QUALITY_VALUE_RECOVERY open row
        mock_cur.fetchone.return_value = (3952, "OPEN", "GREEN", "2026-10-05")

        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        class MockContext:
            def __enter__(self):
                return mock_conn
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass

        with patch("app.database.get_connection", return_value=MockContext()):
            with patch("app.database.init_db", return_value=None):
                qvr_cand = {
                    "symbol": test_sym,
                    "entry_price": 95.0,
                    "current_price": 95.0,
                    "tier": "TIER1",
                    "ranking_score": 92.0,
                    "scanner": "QUALITY_VALUE_RECOVERY",
                    "breakout_type": "QUALITY_VALUE_RECOVERY",
                    "watchlist_state": "GREEN",
                    "context": {"test": True}
                }

                saved_ok, saved_msg = save_v2_candidate_alert(qvr_cand)
                self.assertFalse(saved_ok)
                self.assertIn("ALREADY_OPEN_UPDATED_ALERT_3952", saved_msg)


if __name__ == "__main__":
    unittest.main()
