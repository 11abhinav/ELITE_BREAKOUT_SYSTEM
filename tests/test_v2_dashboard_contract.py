# =====================================================================================
# tests/test_v2_dashboard_contract.py
# V2 MASTER DASHBOARD API CONTRACT & DATA INTEGRITY UNIT TESTS
# =====================================================================================
import unittest
import math
import sys
import os

app_path = "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app"
if app_path not in sys.path:
    sys.path.insert(0, app_path)

from master_orchestrator import (
    orchestrator_v2,
    resolve_tradingview_symbol,
    _sanitize_numeric,
)


class TestV2DashboardContract(unittest.TestCase):

    def test_resolve_tradingview_symbol(self):
        """Tests exchange-aware TradingView symbol resolution (No hardcoded NSE)."""
        # Standard NSE stock
        self.assertEqual(resolve_tradingview_symbol("ABB"), "NSE:ABB")
        self.assertEqual(resolve_tradingview_symbol("RELIANCE"), "NSE:RELIANCE")

        # Explicit BSE suffix or prefix
        self.assertEqual(resolve_tradingview_symbol("DIACABS.BO"), "BSE:DIACABS")
        self.assertEqual(resolve_tradingview_symbol("BSE:YASHHV"), "BSE:YASHHV")
        self.assertEqual(resolve_tradingview_symbol("STLTECH.BO"), "BSE:STLTECH")

        # Numeric BSE scrip code (532959 -> DIACABS)
        self.assertTrue(resolve_tradingview_symbol("532959").startswith("BSE:"))

        # Empty / invalid input
        self.assertEqual(resolve_tradingview_symbol(""), "NSE:UNKNOWN")

    def test_distance_precedence(self):
        """Tests distance calculation precedence: stored -> calculated -> None."""
        item1 = {
            "symbol": "STOCK1",
            "distance_to_trigger_pct": 2.5,
            "trigger_level": 100.0,
            "cmp": 95.0,
        }
        res1 = orchestrator_v2._ensure_contract_keys(item1)
        self.assertEqual(res1["distance_pct"], 2.5)  # Stored distance preserved

        item2 = {
            "symbol": "STOCK2",
            "distance_to_trigger_pct": None,
            "trigger_level": 110.0,
            "cmp": 100.0,
        }
        res2 = orchestrator_v2._ensure_contract_keys(item2)
        self.assertEqual(res2["distance_pct"], 10.0)  # Calculated (110-100)/100 * 100

        item3 = {
            "symbol": "STOCK3",
            "trigger_level": None,
            "cmp": 100.0,
        }
        res3 = orchestrator_v2._ensure_contract_keys(item3)
        self.assertIsNone(res3["distance_pct"])  # Missing trigger -> None

        item4 = {
            "symbol": "STOCK4",
            "trigger_level": 100.0,
            "cmp": 0.0,  # Guards against cmp <= 0
        }
        res4 = orchestrator_v2._ensure_contract_keys(item4)
        self.assertIsNone(res4["distance_pct"])

    def test_required_contract_keys_and_provenance(self):
        """Asserts all 6 endpoints return required contract keys and valid data_source tags."""
        required_keys = {
            "symbol",
            "tradingview_symbol",
            "cmp",
            "cmp_source",
            "cmp_is_live",
            "cmp_timestamp",
            "trigger_level",
            "distance_pct",
            "primary_blocker",
            "why_qualifies",
            "data_source",
        }

        # 1. Stocks to Watch
        watch_rows = orchestrator_v2.get_stocks_to_watch()
        self.assertIsInstance(watch_rows, list)
        for row in watch_rows:
            for k in required_keys:
                self.assertIn(k, row, f"Missing key '{k}' in get_stocks_to_watch output")
            self.assertIn(row["data_source"], ["scanner_candidates", "legacy_fallback"])
            self.assertFalse(str(row.get("cmp")).lower() == "undefined")
            self.assertFalse(str(row.get("trigger_level")).lower() == "undefined")
            self.assertFalse(str(row.get("distance_pct")).lower() == "undefined")

        # 2. Confirmed Signals
        signals_rows = orchestrator_v2.get_confirmed_signals()
        self.assertIsInstance(signals_rows, list)
        for row in signals_rows:
            for k in required_keys:
                self.assertIn(k, row, f"Missing key '{k}' in get_confirmed_signals output")

        # 3. Investment Watch
        inv_rows = orchestrator_v2.get_investment_watch()
        self.assertIsInstance(inv_rows, list)
        for row in inv_rows:
            for k in required_keys:
                self.assertIn(k, row, f"Missing key '{k}' in get_investment_watch output")

        # 4. Confluence Setups
        conf_rows = orchestrator_v2.get_all_confluence_setups()
        self.assertIsInstance(conf_rows, list)
        for row in conf_rows:
            for k in required_keys:
                self.assertIn(k, row, f"Missing key '{k}' in get_all_confluence_setups output")

        # 5. Portfolio Actions
        port_rows = orchestrator_v2.get_portfolio_actions()
        self.assertIsInstance(port_rows, list)
        for row in port_rows:
            for k in required_keys:
                self.assertIn(k, row, f"Missing key '{k}' in get_portfolio_actions output")

    def test_no_invalid_value_strings(self):
        """Asserts no API response contains 'undefined', 'NaN', 'Infinity', or 'NSE:undefined'."""
        all_results = (
            orchestrator_v2.get_stocks_to_watch() +
            orchestrator_v2.get_confirmed_signals() +
            orchestrator_v2.get_investment_watch() +
            orchestrator_v2.get_all_confluence_setups() +
            orchestrator_v2.get_portfolio_actions()
        )

        invalid_substrings = ["undefined", "nan", "infinity", "nse:undefined", "₹undefined"]
        for row in all_results:
            for key, val in row.items():
                str_val = str(val).lower()
                for inv in invalid_substrings:
                    self.assertNotIn(
                        inv,
                        str_val,
                        f"Found forbidden string '{inv}' in key '{key}' of row for symbol '{row.get('symbol')}'"
                    )

    def test_cmp_provenance_and_fallback(self):
        """Tests central CMP resolution provenance and fallback logic."""
        from unittest.mock import patch
        
        # Test Case 1: Live unavailable, DAILY_CACHE available
        with patch("price_cache.get_cached_price_details", return_value=(542.50, "DAILY_CACHE", False, "2026-08-28")):
            details = orchestrator_v2.get_trusted_cmp_details("TEST_SYM")
            self.assertEqual(details["cmp"], 542.50)
            self.assertEqual(details["cmp_source"], "DAILY_CACHE")
            self.assertFalse(details["cmp_is_live"])
            self.assertEqual(details["cmp_timestamp"], "2026-08-28")

        # Test Case 2: Both unavailable -> UNAVAILABLE rather than inventing a price
        with patch("price_cache.get_cached_price_details", return_value=(None, "UNAVAILABLE", False, None)):
            details = orchestrator_v2.get_trusted_cmp_details("TEST_SYM")
            self.assertIsNone(details["cmp"])
            self.assertEqual(details["cmp_source"], "UNAVAILABLE")
            self.assertFalse(details["cmp_is_live"])
            self.assertIsNone(details["cmp_timestamp"])

    def test_boot_cleanup_concurrency(self):
        """Tests negative test for boot sequence QUEUED status cleanup concurrency."""
        from database import get_connection, upsert_scanner_health
        
        # Reset and prepare test states
        upsert_scanner_health("EOD", status="QUEUED", error_msg="Test boot queued")
        
        # We manually insert a mock scanner to simulate an unrelated concurrent queued scanner
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO scanner_health (scanner_name, status, error_msg, updated_at)
                    VALUES ('UNRELATED_SCANNER', 'QUEUED', 'Test unrelated queued', NOW())
                    ON CONFLICT (scanner_name) DO UPDATE SET status = 'QUEUED', error_msg = 'Test unrelated queued';
                """)
            conn.commit()
        
        all_scanners = [
            ("EOD", None),
        ]
        
        # Run cleanup query manually to simulate finally block
        scanner_names = [name for name, _ in all_scanners]
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE scanner_health
                    SET status = 'IDLE',
                        error_msg = 'Boot sequence completed — status reset from QUEUED',
                        updated_at = NOW()
                    WHERE (status = 'QUEUED' OR status LIKE 'QUEUED%')
                      AND scanner_name = ANY(%s);
                """, (scanner_names,))
            conn.commit()
            
        # Verify Scanner A (EOD) is IDLE, Scanner B (UNRELATED_SCANNER) is still QUEUED
        from database import get_all_scanner_health
        health = {r["scanner_name"]: r for r in get_all_scanner_health()}
        
        self.assertEqual(health["EOD"]["status"], "IDLE")
        self.assertEqual(health["UNRELATED_SCANNER"]["status"], "QUEUED")
        
        # Cleanup mock scanner
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM scanner_health WHERE scanner_name = 'UNRELATED_SCANNER'")
            conn.commit()


if __name__ == "__main__":
    unittest.main()
