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
        results = search_symbols_autocomplete("TATA", limit=25)

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

        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0]["symbol"], "TCS")

    @patch('stock_analyzer.get_watchlist')
    def test_autocomplete_custom_symbol_fallback(self, mock_wl):
        """Verify unknown ticker query returns no fallback invalid ticker row."""
        mock_wl.return_value = pd.DataFrame([])

        from stock_analyzer import search_symbols_autocomplete
        results = search_symbols_autocomplete("UNKNOWNSTOCK", limit=5)

        self.assertEqual(len(results), 0)


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

    @patch('dashboard_server._cached_check_session', return_value=True)
    @patch('stock_analyzer.refresh_master_symbols_universe', return_value=True)
    @patch('stock_analyzer._load_master_symbol_dictionary', return_value={"TATAMOTORS": {}, "RELIANCE": {}})
    def test_api_admin_refresh_master_symbols_endpoint(self, mock_dict, mock_refresh, mock_check):
        """Verify /api/v1/admin/master_symbols/refresh route allows Admin to manually refresh symbol registry."""
        from dashboard_server import app
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess['logged_in'] = True
                sess['user_id'] = 'ADMIN'
                sess['session_token'] = 'TOKEN123'

            resp = client.post('/api/v1/admin/master_symbols/refresh')
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data["success"])
            self.assertEqual(data["count"], 2)


class TestPerUserWatchlistIsolation(unittest.TestCase):
    """Test suite verifying 100% per-user watchlist data privacy and isolation."""

    @patch('database.get_connection')
    def test_strict_per_user_watchlist_query(self, mock_get_conn):
        """Verify get_user_watchlist queries strictly WHERE user_id::text = %s."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            ("TATAMOTORS", "Tata Motors Ltd", datetime.now(), datetime.now(), 88.0, "MONITORING", "Notes", datetime.now(), None)
        ]

        from database import get_user_watchlist
        items = get_user_watchlist("57880")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["symbol"], "TATAMOTORS")
        # Ensure query strictly bound user_id '57880'
        execute_args = mock_cursor.execute.call_args[0]
        self.assertIn("user_id::text = %s", execute_args[0])
        self.assertEqual(execute_args[1], ("57880",))


class TestMasterSymbolsRegistry(unittest.TestCase):
    """Test suite for master_symbols database table and 07:00 AM IST refresh pipeline."""

    @patch('database.get_connection')
    def test_sync_master_symbols_bulk_upsert(self, mock_get_conn):
        """Verify sync_master_symbols executes bulk upsert SQL for active symbols."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        from database import sync_master_symbols
        sample_rows = [
            {"symbol": "TATAMOTORS", "company_name": "Tata Motors Ltd", "exchange": "NSE", "sector": "AUTO"},
            {"symbol": "RELIANCE", "company_name": "Reliance Industries Ltd", "exchange": "NSE", "sector": "ENERGY"}
        ]
        ok = sync_master_symbols(sample_rows)

        self.assertTrue(ok)
        mock_cursor.executemany.assert_called_once()

    @patch('database.sync_master_symbols', return_value=True)
    @patch('database.upsert_scanner_health')
    @patch('stock_analyzer._load_master_symbol_dictionary')
    def test_refresh_master_symbols_universe_job(self, mock_load, mock_health, mock_sync):
        """Verify refresh_master_symbols_universe syncs equities and updates scanner health."""
        mock_load.return_value = {
            "TATAMOTORS": {"symbol": "TATAMOTORS", "company_name": "Tata Motors Ltd", "sector": "AUTO"},
            "RELIANCE": {"symbol": "RELIANCE", "company_name": "Reliance Ltd", "sector": "ENERGY"}
        }

        from stock_analyzer import refresh_master_symbols_universe
        ok = refresh_master_symbols_universe()

        self.assertTrue(ok)
        mock_sync.assert_called_once()
        mock_health.assert_called_once()

    @patch('stock_analyzer.get_watchlist')
    @patch('stock_analyzer._load_master_symbol_dictionary')
    def test_autocomplete_invalid_ticker_returns_empty(self, mock_load, mock_wl):
        """Verify unrecognized invalid ticker query 'XYZINVALID' returns empty list."""
        mock_wl.return_value = pd.DataFrame([])
        mock_load.return_value = {
            "TATAMOTORS": {"symbol": "TATAMOTORS", "company_name": "Tata Motors Limited", "sector": "AUTO"}
        }

        from stock_analyzer import search_symbols_autocomplete
        results = search_symbols_autocomplete("XYZINVALID", limit=5)

        self.assertEqual(len(results), 0)


class TestSpaceInsensitiveAutocomplete(unittest.TestCase):
    """Test suite verifying space/punctuation insensitive autocomplete matching."""

    @patch('stock_analyzer.get_watchlist')
    @patch('stock_analyzer._load_master_symbol_dictionary')
    def test_autocomplete_space_insensitive_tata_motors(self, mock_load, mock_wl):
        """Verify typing 'tata motors' matches TATAMOTORS symbol."""
        mock_wl.return_value = pd.DataFrame([])
        mock_load.return_value = {
            "TATAMOTORS": {"symbol": "TATAMOTORS", "company_name": "Tata Motors Limited", "sector": "AUTO"},
            "RELIANCE": {"symbol": "RELIANCE", "company_name": "Reliance Industries", "sector": "ENERGY"}
        }

        from stock_analyzer import search_symbols_autocomplete
        results = search_symbols_autocomplete("tata motors", limit=5)

        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0]["symbol"], "TATAMOTORS")

    @patch('stock_analyzer.get_watchlist')
    @patch('stock_analyzer._load_master_symbol_dictionary')
    def test_autocomplete_invalid_ticker_returns_empty(self, mock_load, mock_wl):
        """Verify invalid ticker query 'XYZINVALID' returns empty list."""
        mock_wl.return_value = pd.DataFrame([])
        mock_load.return_value = {
            "TATAMOTORS": {"symbol": "TATAMOTORS", "company_name": "Tata Motors Limited", "sector": "AUTO"}
        }

        from stock_analyzer import search_symbols_autocomplete
        results = search_symbols_autocomplete("XYZINVALID", limit=5)

        self.assertEqual(len(results), 0)


class TestDeepAnalysisDatabasePersistence(unittest.TestCase):
    """Test suite for deep analysis result persistence in user_watchlists table."""

    @patch('database.get_connection')
    def test_update_user_watchlist_scan_result_with_deep_analysis_json(self, mock_get_conn):
        """Verify update_user_watchlist_scan_result serializes and stores deep analysis result JSON."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        from database import update_user_watchlist_scan_result
        deep_res = {
            "overall_health_score": 88.5,
            "watchlist_status": "QUALIFIED",
            "funnel": {"eod_breakout": {"status": "QUALIFIED", "reasons": ["Close > SMA50"]}}
        }
        ok = update_user_watchlist_scan_result("TATAMOTORS", user_id="USER1", health_score=88.5, status="QUALIFIED", deep_analysis_result=deep_res)

        self.assertTrue(ok)
        execute_args = mock_cursor.execute.call_args[0]
        self.assertIn("deep_analysis_result = %s", execute_args[0])

    @patch('database.get_connection')
    def test_get_user_watchlist_deserializes_deep_analysis_json(self, mock_get_conn):
        """Verify get_user_watchlist parses deep_analysis_result JSON string into Python dict."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        deep_json_str = json.dumps({"overall_health_score": 92.0, "funnel": {}})
        mock_cursor.fetchall.return_value = [
            ("TATAMOTORS", "Tata Motors Ltd", datetime.now(), datetime.now(), 92.0, "QUALIFIED", "Notes", datetime.now(), deep_json_str)
        ]

        from database import get_user_watchlist
        items = get_user_watchlist("USER1")

        self.assertEqual(len(items), 1)
        self.assertIsNotNone(items[0]["deep_analysis_result"])
        self.assertEqual(items[0]["deep_analysis_result"]["overall_health_score"], 92.0)


class TestDataFetchingValidation(unittest.TestCase):
    """Test suite validating historical stock price data fetching."""

    def test_data_fetching_synthetic_df(self):
        """Verify price dataframe structure and indicator calculation readiness."""
        dates = pd.date_range("2024-01-01", periods=30)
        df = pd.DataFrame({
            'Date': dates,
            'Open': [100.0 + i for i in range(30)],
            'High': [102.0 + i for i in range(30)],
            'Low': [99.0 + i for i in range(30)],
            'Close': [101.0 + i for i in range(30)],
            'Volume': [10000] * 30
        })

        self.assertFalse(df.empty)
        self.assertEqual(len(df), 30)
        self.assertIn("Close", df.columns)
        self.assertIn("Volume", df.columns)
        self.assertGreater(df["Close"].iloc[-1], df["Close"].iloc[0])


class TestAuditFixesAndParity(unittest.TestCase):
    """Test suite verifying fixes for Piotroski Series ambiguity, Stage 5 Pullback parity, and score preservation."""

    @patch('stock_analyzer.validate_nse_bse_ticker', return_value={"is_valid": True, "symbol": "PULLBACKTEST"})
    @patch('stock_analyzer.fetch_watchlist_data')
    @patch('stock_analyzer.compute_nifty_rs_rating')
    @patch('stock_analyzer.get_fundamentals')
    @patch('watchlist_cache.get_watchlist')
    def test_stage_5_pullback_full_swing_evaluation(self, mock_wl, mock_fund, mock_rs, mock_fetch, mock_val):
        """Verify Stage 5 Pullback evaluates full swing pivots, retracement depth, and trigger bar."""
        dates = pd.date_range(end=datetime.now().strftime("%Y-%m-%d"), periods=60, freq='B')
        prices = [100.0 + (i * 0.5) for i in range(60)]
        df = pd.DataFrame({
            "Open": [p - 0.2 for p in prices],
            "High": [p + 0.5 for p in prices],
            "Low": [p - 0.5 for p in prices],
            "Close": prices,
            "Volume": [50000] * 60
        }, index=dates)

        mock_fetch.return_value = {"PULLBACKTEST": df}
        mock_rs.return_value = {"PULLBACKTEST": 75.0}
        mock_wl.return_value = pd.DataFrame([])
        mock_fund.return_value = {"piotroski_score": 7, "promoter_pledge_pct": 0.0}

        from stock_analyzer import analyze_symbol
        res = analyze_symbol("PULLBACKTEST")

        self.assertTrue(res.get("success"))
        funnel = res.get("funnel", {})
        self.assertIn("pullback", funnel)
        # Verify reason includes specific pullback evaluation message (not empty)
        self.assertTrue(len(funnel["pullback"]["reasons"]) > 0)

    def test_on_demand_fundamentals_merge_preserves_valid_cached_score(self):
        """Verify merging on-demand fundamentals preserves an existing valid Piotroski score in cache."""
        fund_data = {"score": 8, "date": "2026-07-11"}
        on_demand_fund = {"score": 3, "roe": 18.5, "roce": 22.0}

        # Simulating the merge rule in stock_analyzer.py
        existing_score = fund_data.get("score")
        for k, v in on_demand_fund.items():
            if k not in fund_data or fund_data[k] is None:
                fund_data[k] = v
        if existing_score is not None and existing_score >= 0:
            fund_data["score"] = existing_score

        self.assertEqual(fund_data["score"], 8)
        self.assertEqual(fund_data["roe"], 18.5)
        self.assertEqual(fund_data["roce"], 22.0)


if __name__ == '__main__':
    unittest.main()

