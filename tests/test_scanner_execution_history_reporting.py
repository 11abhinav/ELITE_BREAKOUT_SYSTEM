#!/usr/bin/env python3
"""
tests/test_scanner_execution_history_reporting.py
=================================================
AUDIT BATTERY: Scanner Execution History Data Reporting Across All Scanners

Verifies:
  1. ScannerRunContext accurately tracks and exposes structured data quality fields:
     - data_insufficient_count
     - data_missing_count
     - provider_failure_count
     - summary_notes
     - metrics_json
  2. evaluate_quality_status evaluates PARTIAL when data_insufficient > 0 or data_missing > 0,
     preventing misleading "0% Stale" / "NORMAL" labels from masking data gaps.
  3. complete_scanner_execution_run handles both ScannerRunContext objects and keyword arguments
     (e.g., V2 final calls with run_id, total_scanned, quality_status, summary_notes, metrics_json).
  4. All scanners (FUNDAMENTAL, V2_FINAL, EOD, REVERSAL, PULLBACK, MULTI_TF, MULTIBAGGER, WEALTH_ENGINE)
     record their true data quality reports in execution history.
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
APP_DIR = os.path.join(BASE_DIR, "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from scanner_run_context import ScannerRunContext
from database import complete_scanner_execution_run


class TestScannerExecutionHistoryReporting(unittest.TestCase):

    def test_scanner_run_context_fields_and_setters(self):
        """Validates that ScannerRunContext initializes and updates all structured data counters."""
        ctx = ScannerRunContext(scanner_name="FUNDAMENTAL", trigger_type="SCHEDULED")
        self.assertEqual(ctx.data_insufficient_count, 0)
        self.assertEqual(ctx.data_missing_count, 0)
        self.assertEqual(ctx.provider_failure_count, 0)
        self.assertIsNone(ctx.summary_notes)
        self.assertEqual(ctx.metrics_json, {})

        ctx.set_total_stocks(886)
        ctx.fresh_count = 735
        ctx.stale_count = 0
        ctx.incomplete_count = 151
        ctx.set_data_counts(insufficient=151, missing=0, provider_failure=0)
        ctx.set_summary_notes("Approved=886 | DataInsuff=151 | DataMissing=0")
        ctx.set_metrics_json({"approved": 886, "data_insufficient": 151})

        self.assertEqual(ctx.data_insufficient_count, 151)
        self.assertEqual(ctx.data_missing_count, 0)
        self.assertEqual(ctx.provider_failure_count, 0)
        self.assertEqual(ctx.summary_notes, "Approved=886 | DataInsuff=151 | DataMissing=0")
        self.assertEqual(ctx.metrics_json["approved"], 886)

        d = ctx.to_dict()
        self.assertEqual(d["data_insufficient_count"], 151)
        self.assertEqual(d["data_missing_count"], 0)
        self.assertEqual(d["provider_failure_count"], 0)
        self.assertEqual(d["summary_notes"], "Approved=886 | DataInsuff=151 | DataMissing=0")
        self.assertEqual(d["metrics_json"]["data_insufficient"], 151)

    def test_quality_status_derivation(self):
        """Validates quality_status classifies PARTIAL when data is insufficient or missing, not NORMAL."""
        # 1. Zero stale, but 151 insufficient -> MUST BE PARTIAL
        ctx = ScannerRunContext(scanner_name="FUNDAMENTAL")
        ctx.set_total_stocks(886)
        ctx.fresh_count = 735
        ctx.stale_count = 0
        ctx.set_data_counts(insufficient=151, missing=0, provider_failure=0)
        self.assertEqual(ctx.compute_stale_ratio(), 0.0)
        self.assertEqual(ctx.evaluate_quality_status(), "PARTIAL")

        # 2. Zero stale, missing data -> MUST BE PARTIAL
        ctx_miss = ScannerRunContext(scanner_name="EOD")
        ctx_miss.set_total_stocks(500)
        ctx_miss.fresh_count = 480
        ctx_miss.stale_count = 0
        ctx_miss.set_data_counts(insufficient=0, missing=20, provider_failure=0)
        self.assertEqual(ctx_miss.evaluate_quality_status(), "PARTIAL")

        # 3. Provider failure -> MUST BE PARTIAL
        ctx_fail = ScannerRunContext(scanner_name="AI_WORKER")
        ctx_fail.set_total_stocks(50)
        ctx_fail.fresh_count = 45
        ctx_fail.stale_count = 0
        ctx_fail.set_data_counts(insufficient=0, missing=0, provider_failure=5)
        self.assertEqual(ctx_fail.evaluate_quality_status(), "PARTIAL")

        # 4. Perfectly clean run -> NORMAL
        ctx_clean = ScannerRunContext(scanner_name="FUNDAMENTAL")
        ctx_clean.set_total_stocks(886)
        ctx_clean.fresh_count = 886
        ctx_clean.stale_count = 0
        ctx_clean.set_data_counts(insufficient=0, missing=0, provider_failure=0)
        self.assertEqual(ctx_clean.evaluate_quality_status(), "NORMAL")

        # 5. Stale ratio exceeding threshold -> DEGRADED
        ctx_stale = ScannerRunContext(scanner_name="EOD")
        ctx_stale.set_total_stocks(100)
        ctx_stale.fresh_count = 70
        ctx_stale.stale_count = 30  # 30% > 25% threshold
        self.assertEqual(ctx_stale.evaluate_quality_status(), "DEGRADED")

    def test_complete_scanner_execution_run_with_ctx(self):
        """Verifies complete_scanner_execution_run updates DB with structured counters from ctx."""
        ctx = ScannerRunContext(scanner_name="FUNDAMENTAL")
        ctx.set_total_stocks(886)
        ctx.fresh_count = 735
        ctx.stale_count = 0
        ctx.incomplete_count = 151
        ctx.data_insufficient_count = 151
        ctx.data_missing_count = 0
        ctx.provider_failure_count = 0
        ctx.summary_notes = "Approved=886 | DataInsuff=151"
        ctx.metrics_json = {"approved": 886, "data_insufficient": 151}

        # Mock connection and cursor
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ("FUNDAMENTAL",)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        with patch("database.get_connection") as mock_get_conn:
            mock_get_conn.return_value.__enter__.return_value = mock_conn
            complete_scanner_execution_run(ctx)

            self.assertTrue(mock_cursor.execute.called)
            sql, params = mock_cursor.execute.call_args[0]
            self.assertIn("UPDATE scanner_execution_history", sql)
            self.assertIn("data_insufficient_count = %s", sql)
            self.assertIn("data_missing_count = %s", sql)
            self.assertIn("provider_failure_count = %s", sql)
            self.assertIn("summary_notes = %s", sql)
            self.assertIn("metrics_json = %s::jsonb", sql)

            # Check parameter values
            # params format: (lifecycle_status, quality_status, total_stocks, fresh, stale, incomplete,
            #                 insuff, missing, fail, summary_notes, metrics_json_str, stale_ratio, ...)
            self.assertEqual(params[0], "COMPLETED")
            self.assertEqual(params[1], "PARTIAL")  # Quality status
            self.assertEqual(params[2], 886)        # total_stocks
            self.assertEqual(params[3], 735)        # fresh_count
            self.assertEqual(params[4], 0)          # stale_count
            self.assertEqual(params[5], 151)        # incomplete_count
            self.assertEqual(params[6], 151)        # data_insufficient_count
            self.assertEqual(params[7], 0)          # data_missing_count
            self.assertEqual(params[8], 0)          # provider_failure_count
            self.assertEqual(params[9], "Approved=886 | DataInsuff=151")
            self.assertIn('"approved": 886', params[10])

    def test_complete_scanner_execution_run_with_kwargs(self):
        """Verifies complete_scanner_execution_run works seamlessly when invoked with kwargs like V2."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ("QUALITY_COMPOUNDER_VALUE_V2_FINAL",)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        with patch("database.get_connection") as mock_get_conn:
            mock_get_conn.return_value.__enter__.return_value = mock_conn
            complete_scanner_execution_run(
                run_id="run_v2_12345678",
                total_scanned=886,
                total_stocks=886,
                candidate_count=3,
                quality_status="DATA_BLOCKED",
                data_insufficient_count=52,
                data_missing_count=90,
                provider_failure_count=0,
                summary_notes="Approved=886 | DataBlocked=142 (Non_PIT:90, IncompleteQuality:52)",
                metrics_json={"total_scanned": 886, "candidate_count": 3}
            )

            self.assertTrue(mock_cursor.execute.called)
            sql, params = mock_cursor.execute.call_args[0]
            self.assertEqual(params[0], "COMPLETED")
            self.assertEqual(params[1], "DATA_BLOCKED")
            self.assertEqual(params[2], 886)
            self.assertEqual(params[6], 52)         # data_insufficient_count
            self.assertEqual(params[7], 90)         # data_missing_count
            self.assertEqual(params[8], 0)          # provider_failure_count
            self.assertEqual(params[9], "Approved=886 | DataBlocked=142 (Non_PIT:90, IncompleteQuality:52)")
            self.assertIn('"candidate_count": 3', params[10])
            self.assertEqual(params[12], 3)         # alerts_generated from candidate_count
            self.assertEqual(params[19], "run_v2_12345678")


if __name__ == "__main__":
    unittest.main()
