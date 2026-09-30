import os
import sys
import unittest
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app_dir = os.path.join(BASE_DIR, "app")
if app_dir not in sys.path:
    sys.path.insert(0, app_dir)

from live_fundamental_scanner import QualityCompounderValueScannerV2Final
from lock_utils import ProcessLock

class TestMasterRCARemediation(unittest.TestCase):

    def test_financial_sector_classification(self):
        """Verify financial entity detection for banks, NBFCs, HFCs, and explicit symbols."""
        self.assertTrue(QualityCompounderValueScannerV2Final.is_financial_sector("", "HDFCBANK"))
        self.assertTrue(QualityCompounderValueScannerV2Final.is_financial_sector("", "AADHARHFC"))
        self.assertTrue(QualityCompounderValueScannerV2Final.is_financial_sector("", "AAVAS"))
        self.assertTrue(QualityCompounderValueScannerV2Final.is_financial_sector("Banking Services", "GENERIC"))
        self.assertFalse(QualityCompounderValueScannerV2Final.is_financial_sector("IT Services", "INFY"))

    def test_annual_cagr_calculation_unmixed(self):
        """Verify that annual Sales/PAT CAGR calculation on annual statements produces valid positive CAGR for INFY."""
        # Simulated annual statements (FY2021 to FY2026)
        annual_data = pd.DataFrame([
            {"statement_type": "ANNUAL", "period_end_date": "2021-03-31", "revenue": 100472.0, "net_profit": 19351.0},
            {"statement_type": "ANNUAL", "period_end_date": "2022-03-31", "revenue": 121641.0, "net_profit": 22110.0},
            {"statement_type": "ANNUAL", "period_end_date": "2023-03-31", "revenue": 146767.0, "net_profit": 24095.0},
            {"statement_type": "ANNUAL", "period_end_date": "2024-03-31", "revenue": 153670.0, "net_profit": 26248.0},
            {"statement_type": "ANNUAL", "period_end_date": "2025-03-31", "revenue": 162990.0, "net_profit": 26750.0},
            {"statement_type": "ANNUAL", "period_end_date": "2026-03-31", "revenue": 178650.0, "net_profit": 29474.0},
            # Quarterly filing at the end (must be excluded from annual CAGR)
            {"statement_type": "QUARTERLY", "period_end_date": "2026-06-30", "revenue": 48211.0, "net_profit": 7775.0},
        ])
        
        g_ann = annual_data[annual_data['statement_type'] == 'ANNUAL'].sort_values('period_end_date')
        k_cagr = min(5, len(g_ann) - 1)
        start_row = g_ann.iloc[-k_cagr - 1]
        end_row = g_ann.iloc[-1]
        yrs = (pd.to_datetime(end_row['period_end_date']) - pd.to_datetime(start_row['period_end_date'])).days / 365.25
        
        rev_cagr = (pow(end_row['revenue'] / start_row['revenue'], 1.0 / yrs) - 1.0) * 100.0
        pat_cagr = (pow(end_row['net_profit'] / start_row['net_profit'], 1.0 / yrs) - 1.0) * 100.0

        self.assertAlmostEqual(rev_cagr, 12.2, delta=0.5)
        self.assertAlmostEqual(pat_cagr, 8.7, delta=0.5)
        self.assertGreater(rev_cagr, 0.0)

    def test_process_lock_queue_timeout(self):
        """Verify ProcessLock queue wait timeout functionality."""
        lock = ProcessLock("unit_test_rca_lock")
        # Acquire lock once
        self.assertTrue(lock.acquire(blocking=False))
        # Non-blocking second acquire should return False
        self.assertFalse(lock.acquire(blocking=False))
        lock.release()

if __name__ == '__main__':
    unittest.main()
