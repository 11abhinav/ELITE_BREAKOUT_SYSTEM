"""
tests/test_nse_pledge_pipeline.py
=================================
Unit and integration tests for the official NSE promoter pledge ingestion pipeline.
Covers:
  1. CSV schema validation & parsing
  2. Company-to-symbol normalization
  3. Saturday 02:00 AM - 10:00 AM IST window enforcement
  4. Database backward compatibility (get_pledge_map integration)
"""

import os
import sys
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

# Add app to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from nse_pledge_fetcher import (
    parse_nse_pledged_csv,
    _normalize_company_name,
    EXPECTED_CORE_HEADERS,
)
from pledge_worker import is_pledge_active_window, get_pledge_window_desc

IST = ZoneInfo("Asia/Kolkata")


class TestNSEPledgePipeline(unittest.TestCase):

    def test_active_window_schedule(self):
        """Validates that worker window is strictly Saturday 02:00 AM to 10:00 AM IST."""
        # 1. Saturday 03:30 AM IST -> In Window
        sat_0330 = datetime(2026, 9, 5, 3, 30, tzinfo=IST) # 2026-09-05 is Saturday
        self.assertTrue(is_pledge_active_window(sat_0330))

        # 2. Saturday 02:00 AM IST -> In Window (start bound)
        sat_0200 = datetime(2026, 9, 5, 2, 0, tzinfo=IST)
        self.assertTrue(is_pledge_active_window(sat_0200))

        # 3. Saturday 09:59 AM IST -> In Window (end bound)
        sat_0959 = datetime(2026, 9, 5, 9, 59, tzinfo=IST)
        self.assertTrue(is_pledge_active_window(sat_0959))

        # 4. Saturday 01:59 AM IST -> Outside Window
        sat_0159 = datetime(2026, 9, 5, 1, 59, tzinfo=IST)
        self.assertFalse(is_pledge_active_window(sat_0159))

        # 5. Saturday 10:00 AM IST -> Outside Window
        sat_1000 = datetime(2026, 9, 5, 10, 0, tzinfo=IST)
        self.assertFalse(is_pledge_active_window(sat_1000))

        # 6. Friday 03:30 AM IST -> Outside Window (Weekday)
        fri_0330 = datetime(2026, 9, 4, 3, 30, tzinfo=IST)
        self.assertFalse(is_pledge_active_window(fri_0330))

        # 7. Sunday 03:30 AM IST -> Outside Window
        sun_0330 = datetime(2026, 9, 6, 3, 30, tzinfo=IST)
        self.assertFalse(is_pledge_active_window(sun_0330))

        # Window description
        self.assertIn("02:00 - 10:00", get_pledge_window_desc())

    def test_company_name_normalization(self):
        """Verifies robust fuzzy name normalization across entity suffixes."""
        n1 = _normalize_company_name("20 Microns Limited")
        n2 = _normalize_company_name("20 Microns Ltd.")
        n3 = _normalize_company_name("20 Microns Ltd")
        self.assertEqual(n1, "20MICRONS")
        self.assertEqual(n2, "20MICRONS")
        self.assertEqual(n3, "20MICRONS")

        tata1 = _normalize_company_name("Tata Steel Limited")
        tata2 = _normalize_company_name("TATA STEEL LTD")
        self.assertEqual(tata1, "TATASTEEL")
        self.assertEqual(tata2, "TATASTEEL")

    def test_parse_nse_pledged_csv_structure(self):
        """Verifies accurate CSV parsing and metric extraction."""
        synthetic_csv = (
            '"NAME OF COMPANY","TOTAL NO. OF ISSUED SHARES A+B+C","TOTAL PROMOTER HOLDING NO. OF SHARES (A)",'
            '"TOTAL PROMOTER HOLDING % A /(A+B+C)","TOTAL PUBLIC HOLDING B",'
            '"PROMOTER SHARES ENCUMBERED AS OF LAST QUARTER NO. OF SHARES (X)",'
            '"PROMOTER SHARES ENCUMBERED AS OF LAST QUARTER % OF PROMOTER SHARES (X/A)",'
            '"PROMOTER SHARES ENCUMBERED AS OF LAST QUARTER % OF TOTAL SHARES [X/(A+B+C)]",'
            '"PROMOTER SHARES ENCUMBERED AS OF LAST QUARTER VALUES(RS.CR.)=NO. OF SHARES ENCUMBERED [X] * LAST AVAILABLE CLOSING PRICE OF THE SCRIP",'
            '"DISCLOSURE MADE BY PROMOTERS","NO. OF SHARES PLEDGED IN THE DEPOSITORY SYSTEM NO. OF SHARES PLEDGED",'
            '"NO. OF SHARES PLEDGED IN THE DEPOSITORY SYSTEM TOTAL NO. OF DEMAT SHARES","(%) PLEDGE / DEMAT",'
            '"Values(Rs. Cr.)","BROADCAST DATE"\n'
            '"20 Microns Limited","35286502","15893364","45.04","19393138","0","0.00","0.00","0",'
            '"04-Sep-2026 16:31:29","1560873","35280179","4.42","31.438","04-Sep-2026 16:31:29"\n'
            '"A2Z Infra Engineering Limited","177522358","49560983","27.92","127961375","49402301","99.68","27.83",'
            '"66.347","04-Sep-2026 16:31:38","56473105","177517541","31.81","75.843","04-Sep-2026 16:31:38"\n'
        )

        res = parse_nse_pledged_csv(synthetic_csv)
        self.assertEqual(res["total_rows"], 2)
        self.assertEqual(res["matched_count"], 2)
        records_by_sym = {r["symbol"]: r for r in res["records"]}

        self.assertIn("20MICRONS", records_by_sym)
        self.assertEqual(records_by_sym["20MICRONS"]["pledge_pct"], 0.0)
        self.assertEqual(records_by_sym["20MICRONS"]["promoter_shares"], 15893364)
        self.assertEqual(records_by_sym["20MICRONS"]["source"], "NSE")

        self.assertIn("A2ZINFRA", records_by_sym)
        self.assertEqual(records_by_sym["A2ZINFRA"]["pledge_pct"], 99.68)
        self.assertEqual(records_by_sym["A2ZINFRA"]["pledged_shares"], 49402301)
        self.assertEqual(records_by_sym["A2ZINFRA"]["depository_pledge_demat_pct"], 31.81)

    def test_schema_mismatch_raises(self):
        """Verifies that altered CSV headers trigger NSE_SCHEMA_CHANGED exception."""
        invalid_csv = '"WRONG_COL_1","WRONG_COL_2"\n"val1","val2"\n'
        with self.assertRaises(ValueError) as ctx:
            parse_nse_pledged_csv(invalid_csv)
        self.assertIn("NSE_SCHEMA_CHANGED", str(ctx.exception))

    def test_bulk_upsert_dummy_connection(self):
        """Verifies upsert_bulk_pledge_records executes without errors under dummy/local connection."""
        from database import upsert_bulk_pledge_records
        records = [{
            "symbol": "TESTSYM",
            "pledge_pct": 5.5,
            "pledged_shares": 50000,
            "promoter_shares": 1000000,
            "total_shares": 2000000,
            "depository_pledged_shares": 50000,
            "promoter_holding_pct": 50.0,
            "depository_pledge_demat_pct": 2.5,
            "source": "NSE",
            "as_of_date": datetime.now(IST).date(),
            "snapshot_id": "test_snap_123"
        }]
        meta = {
            "snapshot_id": "test_snap_123",
            "snapshot_date": datetime.now(IST).date(),
            "total_rows": 1,
            "matched_count": 1
        }
        res = upsert_bulk_pledge_records(records, meta)
        self.assertEqual(res, 1)

    def test_run_pledge_worker_sync_flow(self):
        """Verifies run_pledge_worker_sync executes cleanly with mocked fetcher."""
        from unittest.mock import patch
        from pledge_worker import run_pledge_worker_sync

        mock_payload = {
            "snapshot_id": "snap_test_456",
            "snapshot_date": datetime.now(IST).date(),
            "total_rows": 1,
            "matched_count": 1,
            "records": [{
                "symbol": "INFY",
                "pledge_pct": 0.0,
                "promoter_holding_pct": 14.7,
                "pledged_shares": 0,
                "promoter_shares": 50000000,
                "total_shares": 400000000,
                "depository_pledged_shares": 0,
                "depository_pledge_demat_pct": 0.0,
                "source": "NSE",
                "as_of_date": datetime.now(IST).date(),
                "snapshot_id": "snap_test_456"
            }],
            "unmapped_companies": []
        }

        with patch("pledge_worker.fetch_and_parse_nse_pledged_data", return_value=(mock_payload, None)):
            res = run_pledge_worker_sync(force=True)
            self.assertEqual(res["status"], "SUCCESS")
            self.assertEqual(res["matched_count"], 1)


if __name__ == "__main__":
    unittest.main()

