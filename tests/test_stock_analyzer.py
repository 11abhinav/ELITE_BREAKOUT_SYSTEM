# =====================================================================================
# tests/test_stock_analyzer.py
# UNIT TEST SUITE FOR STOCK ANALYZER & PERSONAL WATCHLIST ENGINE
# =====================================================================================

import unittest
from unittest.mock import patch, MagicMock
import pandas as pd
from datetime import datetime


class TestStockAnalyzer(unittest.TestCase):

    @patch('stock_analyzer.get_watchlist')
    def test_search_symbols_autocomplete(self, mock_wl):
        """Verify autocomplete search matches symbol prefix and company names."""
        mock_df = pd.DataFrame([
            {"Stock": "TATAMOTORS", "Company": "Tata Motors Ltd", "Sector": "AUTO", "Category": "LARGE"},
            {"Stock": "TATASTEEL", "Company": "Tata Steel Ltd", "Sector": "METALS", "Category": "LARGE"},
            {"Stock": "RELIANCE", "Company": "Reliance Industries", "Sector": "ENERGY", "Category": "LARGE"}
        ])
        mock_wl.return_value = mock_df

        from stock_analyzer import search_symbols_autocomplete
        results = search_symbols_autocomplete("TATA", limit=10)

        self.assertGreaterEqual(len(results), 2)
        symbols = [r["symbol"] for r in results]
        self.assertIn("TATAMOTORS", symbols)
        self.assertIn("TATASTEEL", symbols)

    @patch('stock_analyzer.fetch_watchlist_data')
    @patch('stock_analyzer.compute_nifty_rs_rating')
    @patch('stock_analyzer.get_fundamentals')
    def test_analyze_symbol_full_funnel(self, mock_fund, mock_rs, mock_fetch):
        """Verify analyze_symbol generates valid health score, deficit list, and 7-stage funnel structure."""
        today_str = datetime.now().strftime("%Y-%m-%d")
        dates = pd.date_range(end=today_str, periods=60, freq='B')

        # Create sample price DataFrame with breakout close & volume expansion
        prices = [100.0 + i * 1.5 for i in range(60)]
        df = pd.DataFrame({
            "Open": [p - 1.0 for p in prices],
            "High": [p + 2.0 for p in prices],
            "Low": [p - 2.0 for p in prices],
            "Close": prices,
            "Volume": [100000 if i < 59 else 500000 for i in range(60)] # 5x volume surge on last bar
        }, index=dates)

        mock_fetch.return_value = {"TATAMOTORS": df}
        mock_rs.return_value = {"TATAMOTORS": 85.0}

        mock_fund.return_value = {
            "company_name": "Tata Motors Ltd",
            "sector": "AUTO",
            "roce": 22.5,
            "roe": 18.0,
            "debt_to_equity": 0.35,
            "piotroski_score": 8,
            "promoter_pledge_pct": 2.5
        }

        from stock_analyzer import analyze_symbol
        res = analyze_symbol("TATAMOTORS")

        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("symbol"), "TATAMOTORS")
        self.assertIn("overall_health_score", res)
        self.assertGreater(res.get("overall_health_score"), 60.0)

        funnel = res.get("funnel", {})
        self.assertIn("daily_builder", funnel)
        self.assertEqual(funnel["daily_builder"]["status"], "YES")
        self.assertIn("eod_breakout", funnel)
        self.assertIn("multibagger", funnel)

        deficits = res.get("deficits", [])
        self.assertIsInstance(deficits, list)
        self.assertGreaterEqual(len(deficits), 1)

    @patch('stock_analyzer.analyze_symbol')
    @patch('stock_analyzer.compute_sl_and_target')
    @patch('stock_analyzer.save_alert_if_new')
    @patch('telegram_engine.send_telegram_message')
    def test_create_manual_alert_from_analysis(self, mock_tg, mock_save, mock_sl, mock_analyze):
        """Verify 1-click manual alert promotion saves alert and triggers notifications."""
        mock_analyze.return_value = {
            "success": True,
            "close_price": 500.0,
            "overall_health_score": 88.5,
            "rs_percentile": 90.0,
            "sector": "AUTO",
            "funnel": {}
        }
        mock_sl.return_value = {
            "is_rejected": False,
            "stop_loss": 475.0,
            "target_1": 530.0,
            "target_2": 560.0,
            "target_3": 600.0
        }
        mock_save.return_value = (True, "Alert Created", 101, None)

        from stock_analyzer import create_manual_alert_from_analysis
        res = create_manual_alert_from_analysis("TATAMOTORS", scanner_type="EOD", user_id="ADMIN")

        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("alert_id"), 101)
        self.assertEqual(res.get("symbol"), "TATAMOTORS")
        self.assertEqual(res.get("entry_price"), 500.0)

    @patch('database.get_connection')
    def test_user_watchlist_db_operations(self, mock_get_conn):
        """Verify database helper functions for user personal watchlist."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        mock_cursor.fetchall.return_value = [
            ("TATAMOTORS", "Tata Motors Ltd", datetime.now(), datetime.now(), 82.5, "QUALIFIED", "Watchlist Note")
        ]

        from database import add_to_user_watchlist, get_user_watchlist, remove_from_user_watchlist
        ok_add = add_to_user_watchlist("TATAMOTORS", company_name="Tata Motors Ltd", user_id="ADMIN", health_score=82.5)
        self.assertTrue(ok_add)

        items = get_user_watchlist("ADMIN")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["symbol"], "TATAMOTORS")
        self.assertEqual(items[0]["last_health_score"], 82.5)

        ok_rem = remove_from_user_watchlist("TATAMOTORS", user_id="ADMIN")
        self.assertTrue(ok_rem)


if __name__ == '__main__':
    unittest.main()
