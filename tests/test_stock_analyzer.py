# =====================================================================================
# tests/test_stock_analyzer.py
# MODULAR TEST CLASSES FOR STOCK ANALYZER, DIAGNOSTIC ENGINE & PERSONAL WATCHLIST
# =====================================================================================

import unittest
from unittest.mock import patch, MagicMock
import pandas as pd
from datetime import datetime
import json


class TestStockAnalyzerAutocomplete(unittest.TestCase):
    """Test suite for real-time symbol and company autocomplete search."""

    @patch('stock_analyzer.get_watchlist')
    def test_autocomplete_symbol_prefix_match(self, mock_wl):
        """Verify autocomplete search matches symbol prefix correctly."""
        mock_wl.return_value = pd.DataFrame([
            {"Stock": "TATAMOTORS", "Company": "Tata Motors Ltd", "Sector": "AUTO", "Category": "LARGE"},
            {"Stock": "TATASTEEL", "Company": "Tata Steel Ltd", "Sector": "METALS", "Category": "LARGE"},
            {"Stock": "RELIANCE", "Company": "Reliance Industries", "Sector": "ENERGY", "Category": "LARGE"}
        ])

        from stock_analyzer import search_symbols_autocomplete
        results = search_symbols_autocomplete("TATA", limit=10)

        self.assertGreaterEqual(len(results), 2)
        symbols = [r["symbol"] for r in results]
        self.assertIn("TATAMOTORS", symbols)
        self.assertIn("TATASTEEL", symbols)

    @patch('stock_analyzer.get_watchlist')
    def test_autocomplete_company_name_match(self, mock_wl):
        """Verify autocomplete matches substring within company name."""
        mock_wl.return_value = pd.DataFrame([
            {"Stock": "INFY", "Company": "Infosys Limited", "Sector": "IT", "Category": "LARGE"},
            {"Stock": "TCS", "Company": "Tata Consultancy Services", "Sector": "IT", "Category": "LARGE"}
        ])

        from stock_analyzer import search_symbols_autocomplete
        results = search_symbols_autocomplete("Consultancy", limit=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["symbol"], "TCS")

    @patch('stock_analyzer.get_watchlist')
    def test_autocomplete_custom_symbol_fallback(self, mock_wl):
        """Verify unknown ticker query generates clean custom fallback suggestion."""
        mock_wl.return_value = pd.DataFrame([])

        from stock_analyzer import search_symbols_autocomplete
        results = search_symbols_autocomplete("UNKNOWNSTOCK", limit=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["symbol"], "UNKNOWNSTOCK")
        self.assertEqual(results[0]["category"], "CUSTOM")


class TestStockAnalyzerDiagnosticEngine(unittest.TestCase):
    """Test suite for 7-stage quantitative scanner funnel and health score engine."""

    def test_validate_nse_bse_ticker_invalid_format(self):
        """Verify ticker validator rejects invalid characters and symbols."""
        from stock_analyzer import validate_nse_bse_ticker
        res = validate_nse_bse_ticker("INVALID@SYMBOL#123")
        self.assertFalse(res["is_valid"])
        self.assertIn("Invalid ticker format", res["error"])

    @patch('stock_analyzer.get_watchlist')
    @patch('stock_analyzer.get_connection')
    @patch('stock_analyzer.fetch_watchlist_data')
    def test_validate_nse_bse_ticker_unrecognized_symbol(self, mock_fetch, mock_conn, mock_wl):
        """Verify ticker validator rejects unrecognized tickers not on NSE/BSE."""
        mock_wl.return_value = pd.DataFrame([])
        mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.fetchone.return_value = None
        mock_fetch.return_value = {}

        from stock_analyzer import validate_nse_bse_ticker
        res = validate_nse_bse_ticker("NONEXISTENT999")
        self.assertFalse(res["is_valid"])
        self.assertIn("NOT a recognized NSE/BSE ticker symbol", res["error"])

    @patch('stock_analyzer.validate_nse_bse_ticker')
    def test_analyze_symbol_invalid_ticker_rejection(self, mock_val):
        """Verify analyze_symbol returns is_invalid_ticker True when ticker is invalid."""
        mock_val.return_value = {"is_valid": False, "error": "Invalid ticker"}

        from stock_analyzer import analyze_symbol
        res = analyze_symbol("FAKETICKER")
        self.assertFalse(res["success"])
        self.assertTrue(res.get("is_invalid_ticker"))

    @patch('stock_analyzer.validate_nse_bse_ticker', return_value={"is_valid": True, "symbol": "SHORTBARS"})
    @patch('stock_analyzer.fetch_watchlist_data')
    def test_analyze_symbol_insufficient_data_error(self, mock_fetch, mock_val):
        """Verify analyze_symbol returns structured error when historical data is missing."""
        mock_fetch.return_value = {"SHORTBARS": pd.DataFrame()}

        from stock_analyzer import analyze_symbol
        res = analyze_symbol("SHORTBARS")

        self.assertFalse(res.get("success"))
        self.assertIn("error", res)

    @patch('stock_analyzer.validate_nse_bse_ticker', return_value={"is_valid": True, "symbol": "PENNYSTOCK"})
    @patch('stock_analyzer.fetch_watchlist_data')
    @patch('stock_analyzer.compute_nifty_rs_rating')
    @patch('stock_analyzer.get_fundamentals')
    def test_analyze_symbol_daily_builder_price_floor_deficit(self, mock_fund, mock_rs, mock_fetch, mock_val):
        """Verify price floor < ₹100 generates explicit deficit warning."""
        dates = pd.date_range(end=datetime.now().strftime("%Y-%m-%d"), periods=60, freq='B')
        prices = [50.0 + i * 0.5 for i in range(60)] # Max price 79.5 < 100.0
        df = pd.DataFrame({
            "Open": [p - 0.5 for p in prices],
            "High": [p + 1.0 for p in prices],
            "Low": [p - 1.0 for p in prices],
            "Close": prices,
            "Volume": [100000] * 60
        }, index=dates)

        mock_fetch.return_value = {"PENNYSTOCK": df}
        mock_rs.return_value = {"PENNYSTOCK": 40.0}
        mock_fund.return_value = {"company_name": "Penny Stock Ltd", "sector": "SMALL"}

        from stock_analyzer import analyze_symbol
        res = analyze_symbol("PENNYSTOCK")

        self.assertTrue(res.get("success"))
        funnel = res.get("funnel", {})
        self.assertEqual(funnel["daily_builder"]["status"], "NO")

        deficits_text = " ".join(res.get("deficits", []))
        self.assertIn("Price Floor Deficit", deficits_text)

    @patch('stock_analyzer.validate_nse_bse_ticker', return_value={"is_valid": True, "symbol": "TATAMOTORS"})
    @patch('stock_analyzer.fetch_watchlist_data')
    @patch('stock_analyzer.compute_nifty_rs_rating')
    @patch('stock_analyzer.get_fundamentals')
    @patch('watchlist_cache.get_watchlist')
    def test_analyze_symbol_full_7_stage_funnel(self, mock_wl, mock_fund, mock_rs, mock_fetch, mock_val):
        """Verify analyze_symbol evaluates across all 7 pipeline stages."""
        dates = pd.date_range(end=datetime.now().strftime("%Y-%m-%d"), periods=60, freq='B')
        prices = [100.0 + i * 1.5 for i in range(60)]
        df = pd.DataFrame({
            "Open": [p - 1.0 for p in prices],
            "High": [p + 2.0 for p in prices],
            "Low": [p - 2.0 for p in prices],
            "Close": prices,
            "Volume": [100000 if i < 59 else 600000 for i in range(60)]
        }, index=dates)

        mock_fetch.return_value = {"TATAMOTORS": df}
        mock_rs.return_value = {"TATAMOTORS": 88.0}
        
        mock_wl.return_value = pd.DataFrame([{
            "Stock": "TATAMOTORS",
            "Company": "Tata Motors Ltd",
            "Sector": "AUTO",
            "ROCE %": 24.5,
            "ROE %": 19.0,
            "Debt/Equity": 0.25
        }])
        
        mock_fund.return_value = {
            "piotroski_score": 8,
            "promoter_pledge_pct": 0.0
        }

        from stock_analyzer import analyze_symbol
        res = analyze_symbol("TATAMOTORS")

        self.assertTrue(res.get("success"))
        funnel = res.get("funnel", {})
        self.assertEqual(len(funnel), 7)
        self.assertIn("daily_builder", funnel)
        self.assertIn("eod_breakout", funnel)
        self.assertIn("multi_tf", funnel)
        self.assertIn("reversal", funnel)
        self.assertIn("pullback", funnel)
        self.assertIn("wealth_engine", funnel)
        self.assertIn("multibagger", funnel)

        self.assertGreater(res.get("overall_health_score"), 70.0)


class TestManualAlertPromotion(unittest.TestCase):
    """Test suite for 1-click manual alert promotion to active alerts table."""

    @patch('stock_analyzer.analyze_symbol')
    @patch('stock_analyzer.compute_sl_and_target')
    @patch('stock_analyzer.save_alert_if_new')
    @patch('telegram_engine.send_telegram_message')
    def test_create_manual_alert_success(self, mock_tg, mock_save, mock_sl, mock_analyze):
        """Verify successful promotion of analyzed setup into an active BUY alert."""
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
        mock_save.return_value = (True, "Alert Created", 202, None)

        from stock_analyzer import create_manual_alert_from_analysis
        res = create_manual_alert_from_analysis("TATAMOTORS", scanner_type="EOD", user_id="ADMIN")

        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("alert_id"), 202)
        self.assertEqual(res.get("symbol"), "TATAMOTORS")
        self.assertEqual(res.get("entry_price"), 500.0)

    @patch('stock_analyzer.analyze_symbol')
    @patch('stock_analyzer.compute_sl_and_target')
    def test_create_manual_alert_sl_rejection(self, mock_sl, mock_analyze):
        """Verify rejection response when risk manager rejects SL/Target parameters."""
        mock_analyze.return_value = {"success": True, "close_price": 500.0}
        mock_sl.return_value = {"is_rejected": True, "rejection_reason": "Stop Loss exceeds 10% maximum risk limit"}

        from stock_analyzer import create_manual_alert_from_analysis
        res = create_manual_alert_from_analysis("HIGHVOLATILITY", scanner_type="EOD")

        self.assertFalse(res.get("success"))
        self.assertIn("Stop Loss exceeds", res.get("error"))


class TestUserWatchlistRepository(unittest.TestCase):
    """Test suite for database helper operations on user_watchlists table."""

    @patch('database.get_connection')
    def test_add_to_user_watchlist(self, mock_get_conn):
        """Verify adding a symbol to personal watchlist."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        from database import add_to_user_watchlist
        ok = add_to_user_watchlist("RELIANCE", company_name="Reliance Industries", user_id="USER1", health_score=85.0)

        self.assertTrue(ok)
        mock_cursor.execute.assert_called()

    @patch('database.get_connection')
    def test_get_user_watchlist(self, mock_get_conn):
        """Verify fetching user personal watchlist items."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            ("RELIANCE", "Reliance Industries", datetime.now(), datetime.now(), 85.0, "MONITORING", "Notes")
        ]

        from database import get_user_watchlist
        items = get_user_watchlist("USER1")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["symbol"], "RELIANCE")

    @patch('database.get_connection')
    def test_remove_from_user_watchlist(self, mock_get_conn):
        """Verify removing a symbol from personal watchlist."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        from database import remove_from_user_watchlist
        ok = remove_from_user_watchlist("RELIANCE", user_id="USER1")

        self.assertTrue(ok)


class TestStockAnalyzerApiEndpoints(unittest.TestCase):
    """Test suite for Flask REST API endpoints serving Analyse Your Watchlist feature."""

    @patch('dashboard_server._cached_check_session', return_value=True)
    @patch('stock_analyzer.search_symbols_autocomplete')
    def test_api_symbols_suggest_endpoint(self, mock_suggest, mock_check):
        """Verify /api/v1/symbols/suggest route returns JSON list."""
        mock_suggest.return_value = [{"symbol": "TATAMOTORS", "company_name": "Tata Motors Ltd"}]

        from dashboard_server import app
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess['logged_in'] = True
                sess['user_id'] = 'TEST_USER'
                sess['session_token'] = 'TOKEN123'

            resp = client.get('/api/v1/symbols/suggest?q=TATA')
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["symbol"], "TATAMOTORS")

    @patch('dashboard_server._cached_check_session', return_value=True)
    @patch('stock_analyzer.analyze_symbol')
    def test_api_analyze_stock_endpoint(self, mock_analyze, mock_check):
        """Verify /api/v1/analyze_stock route returns diagnostic analysis object."""
        mock_analyze.return_value = {"success": True, "symbol": "TATAMOTORS", "overall_health_score": 85.0}

        from dashboard_server import app
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess['logged_in'] = True
                sess['user_id'] = 'TEST_USER'
                sess['session_token'] = 'TOKEN123'

            resp = client.get('/api/v1/analyze_stock?symbol=TATAMOTORS')
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data["success"])
            self.assertEqual(data["overall_health_score"], 85.0)


if __name__ == '__main__':
    unittest.main()
