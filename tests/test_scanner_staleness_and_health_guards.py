import unittest
import os
import pandas as pd
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.market_utils import validate_batch_staleness
from app.short_covering.oi_data_service import oi_data_service
from app.scanner_contract import ScannerExecutionContract

IST = ZoneInfo("Asia/Kolkata")

class TestScannerStalenessAndHealthGuards(unittest.TestCase):

    def test_staleness_blocker_threshold(self):
        """Verify >= 25% stale data triggers a hard blocker, but < 25% passes."""
        # 1. Below 25% (e.g. 10/100 = 10%)
        res_pass = validate_batch_staleness(stale_count=10, total_count=100, scanner_name="TEST_SCANNER", max_stale_pct=25.0)
        self.assertFalse(res_pass["is_blocked"])
        self.assertEqual(res_pass["stale_pct"], 10.0)

        # 2. Exactly 25% (25/100 = 25%) -> Hard Blocker
        res_block_exact = validate_batch_staleness(stale_count=25, total_count=100, scanner_name="TEST_SCANNER", max_stale_pct=25.0)
        self.assertTrue(res_block_exact["is_blocked"])
        self.assertEqual(res_block_exact["stale_pct"], 25.0)

        # 3. Above 25% (30/100 = 30%) -> Hard Blocker
        res_block_high = validate_batch_staleness(stale_count=30, total_count=100, scanner_name="TEST_SCANNER", max_stale_pct=25.0)
        self.assertTrue(res_block_high["is_blocked"])
        self.assertEqual(res_block_high["stale_pct"], 30.0)

    def test_scanner_contract_staleness_block(self):
        """Verify ScannerExecutionContract fails with DOWN status and admin notification on high staleness."""
        contract = ScannerExecutionContract("TEST_MULTIBAGGER", total_symbols=100)
        stale_list = [f"STALE_{i}" for i in range(30)] # 30% stale
        result = contract.complete(missing_symbols=[], stale_symbols=stale_list, processed_count=70)
        self.assertFalse(result.success)
        self.assertEqual(result.status, "DOWN")
        self.assertIn("STALE DATA BLOCKER", result.error_msg)

    def test_zero_synthetic_data_in_oi_service(self):
        """Verify oi_data_service returns None when real data is missing, and NEVER generates fake random numbers."""
        # Querying an invalid symbol that definitely has no real market parquet
        fake_sym = "COMPLETELY_NON_EXISTENT_SYMBOL_XYZ_123"
        res_eod = oi_data_service.get_daily_oi_history(fake_sym, lookback_days=5, as_of=date(2026, 9, 11))
        self.assertIsNone(res_eod, "Must return None when genuine real market data is missing — zero synthetic data invariant!")

        res_5m = oi_data_service.get_intraday_5m_data(fake_sym, target_date=date(2026, 9, 11))
        self.assertIsNone(res_5m, "Must return None when genuine intraday parquet data is missing — zero synthetic data invariant!")

if __name__ == "__main__":
    unittest.main()
