import unittest
from unittest.mock import patch, MagicMock
import pandas as pd
from datetime import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../app')))

from multi_tf_scanner import strip_forming_candle
from surveillance import get_live_blacklist, _blacklist_lock
from database import check_session_validity

class TestAuditFixes(unittest.TestCase):
    
    # ── V3 Issue 1: Multi-TF Scanner Forming Candle Bug ──
    def test_strip_forming_candle_with_datetime_index(self):
        """Test that strip_forming_candle correctly reads from the dataframe index."""
        # Create a df with localized timezone in index
        now = pd.Timestamp.now(tz='Asia/Kolkata')
        idx = [now - pd.Timedelta(minutes=90), now - pd.Timedelta(minutes=30)]
        df = pd.DataFrame({"Close": [100, 105]}, index=idx)
        
        # The last candle (now - 30m) is still forming in a 60m timeframe
        stripped_df = strip_forming_candle(df, tf_minutes=60, ist_now=now)
        
        # Should strip the last row
        self.assertEqual(len(stripped_df), 1)
        self.assertEqual(stripped_df.iloc[0]["Close"], 100)
        
    def test_strip_forming_candle_completed(self):
        """Test that strip_forming_candle leaves completed candles alone."""
        now = pd.Timestamp.now(tz='Asia/Kolkata')
        # Both candles are completed (older than 60 mins)
        idx = [now - pd.Timedelta(minutes=150), now - pd.Timedelta(minutes=90)]
        df = pd.DataFrame({"Close": [100, 105]}, index=idx)
        
        stripped_df = strip_forming_candle(df, tf_minutes=60, ist_now=now)
        
        # Should keep both rows
        self.assertEqual(len(stripped_df), 2)

    # ── V3 Issue 2: Surveillance Thundering Herd ──
    def test_surveillance_lock_exists(self):
        """Test that get_live_blacklist uses the threading lock properly."""
        # We can't easily simulate a race condition in a unit test, but we can verify the lock exists
        self.assertIsNotNone(_blacklist_lock)
        self.assertTrue(hasattr(_blacklist_lock, "acquire"))
        self.assertTrue(hasattr(_blacklist_lock, "release"))

    # ── V4 Issue 1: Zombie Sessions ──
    @patch('database.get_connection')
    def test_check_session_validity_active(self, mock_get_conn):
        """Test session validation for an active user with correct token."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        
        # DB returns: is_active=True, session_token='abc'
        mock_cursor.fetchone.return_value = (True, 'abc')
        
        is_valid = check_session_validity(1, 'abc')
        self.assertTrue(is_valid)

    @patch('database.get_connection')
    def test_check_session_validity_revoked(self, mock_get_conn):
        """Test session validation for an inactive user."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        
        # DB returns: is_active=False, session_token='abc'
        mock_cursor.fetchone.return_value = (False, 'abc')
        
        is_valid = check_session_validity(1, 'abc')
        self.assertFalse(is_valid)

    # ── V8.2 Audit Remediation Regression Tests ──

    @patch('psycopg2.connect')
    def test_process_lock_returns_false_on_db_exception(self, mock_connect):
        """Verify ProcessLock.acquire() returns False (not True) when DB connection raises OperationalError."""
        import psycopg2
        mock_connect.side_effect = psycopg2.OperationalError("Connection timeout")
        
        from lock_utils import ProcessLock
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://user:pass@localhost:5432/db"}):
            lock = ProcessLock("test_lock_audit_fix")
            acquired = lock.acquire(blocking=False)
            self.assertFalse(acquired, "ProcessLock MUST return False when DB advisory lock connection fails")

    def test_unified_fetcher_duplicate_symbols_discard(self):
        """Verify UnifiedFetcher.fetch_live_quotes handles duplicate input symbols without raising KeyError."""
        from data_providers.unified_fetcher import UnifiedFetcher
        fetcher = UnifiedFetcher()
        
        # Mock fyers and yfinance quote responses
        with patch.object(fetcher, 'fyers') as mock_fyers:
            mock_fyers._normalize_symbol.side_effect = lambda s: f"NSE:{s}-EQ"
            with patch('fyers_auth.get_fyers_client') as mock_get_client:
                mock_client = MagicMock()
                mock_get_client.return_value = mock_client
                mock_client.quotes.return_value = {
                    "s": "ok",
                    "d": [
                        {"s": "ok", "n": "NSE:RELIANCE-EQ", "v": {"lp": 2500.0}}
                    ]
                }
                # Input contains duplicate "RELIANCE"
                results = fetcher.fetch_live_quotes(["RELIANCE", "RELIANCE"], consumer="TEST")
                self.assertIn("RELIANCE", results)
                self.assertEqual(results["RELIANCE"]["v"]["cmd"]["c"], 2500.0)

    def test_unified_fetcher_multiindex_extraction(self):
        """Verify MultiIndex column extraction works for both (symbol, field) and (field, symbol) level ordering."""
        import pandas as pd
        from data_providers.unified_fetcher import UnifiedFetcher
        fetcher = UnifiedFetcher()
        
        # Level order 1: (Field, Symbol) -> ('Close', 'RELIANCE.NS')
        cols1 = pd.MultiIndex.from_tuples([('Close', 'RELIANCE.NS'), ('Open', 'RELIANCE.NS')])
        df1 = pd.DataFrame([[2500.0, 2480.0]], columns=cols1)
        
        # Level order 2: (Symbol, Field) -> ('RELIANCE.NS', 'Close')
        cols2 = pd.MultiIndex.from_tuples([('RELIANCE.NS', 'Close'), ('RELIANCE.NS', 'Open')])
        df2 = pd.DataFrame([[2500.0, 2480.0]], columns=cols2)
        
        with patch('yfinance.download') as mock_download:
            mock_download.return_value = df1
            with patch('yf_rate_limiter.acquire'), patch('yf_rate_limiter.release'):
                with patch.object(fetcher.selector, 'get_providers', return_value=['yahoo']):
                    res1 = fetcher.fetch_live_quotes(["RELIANCE"], consumer="TEST")
                    self.assertEqual(res1.get("RELIANCE", {}).get("v", {}).get("cmd", {}).get("c"), 2500.0)
                    
            mock_download.return_value = df2
            with patch('yf_rate_limiter.acquire'), patch('yf_rate_limiter.release'):
                with patch.object(fetcher.selector, 'get_providers', return_value=['yahoo']):
                    res2 = fetcher.fetch_live_quotes(["RELIANCE"], consumer="TEST")
                    self.assertEqual(res2.get("RELIANCE", {}).get("v", {}).get("cmd", {}).get("c"), 2500.0)

    def test_indicator_manager_history_levels(self):
        """Verify IndicatorManager computes indicators dynamically based on exact history length boundaries (14, 20, 50, 200)."""
        import numpy as np
        from indicator_manager import IndicatorManager
        mgr = IndicatorManager()
        
        # Helper to generate test df
        def make_df(n):
            dates = pd.date_range("2026-01-01", periods=n, freq="D")
            prices = 100.0 + np.arange(n) * 0.5
            return pd.DataFrame({"Open": prices, "High": prices+1, "Low": prices-1, "Close": prices}, index=dates)

        # 10 bars -> All indicators None
        b10 = mgr.compute_base_indicators(make_df(10), "TEST10")
        self.assertIsNone(b10.atr_14)
        self.assertIsNone(b10.ema_20)

        # 15 bars -> ATR14 & RSI14 set; EMA20, SMA50, SMA200 None
        b15 = mgr.compute_base_indicators(make_df(15), "TEST15")
        self.assertIsNotNone(b15.atr_14)
        self.assertIsNotNone(b15.rsi_14)
        self.assertIsNone(b15.ema_20)
        self.assertIsNone(b15.sma_50)

        # 25 bars -> EMA20 & SMA20 set; SMA50 & SMA200 None
        b25 = mgr.compute_base_indicators(make_df(25), "TEST25")
        self.assertIsNotNone(b25.ema_20)
        self.assertIsNotNone(b25.sma_20)
        self.assertIsNone(b25.ema_50)

        # 60 bars -> EMA50 & SMA50 set; SMA200 None
        b60 = mgr.compute_base_indicators(make_df(60), "TEST60")
        self.assertIsNotNone(b60.ema_50)
        self.assertIsNotNone(b60.sma_50)
        self.assertIsNone(b60.sma_200)

        # 210 bars -> ALL indicators set including SMA200
        b210 = mgr.compute_base_indicators(make_df(210), "TEST210")
        self.assertIsNotNone(b210.sma_200)

    @patch('delivery_data.requests.Session')
    def test_delivery_data_series_prioritization(self, mock_session_cls):
        """Verify delivery data fetcher prioritizes EQ series over BE series when a symbol appears under both."""
        import datetime
        from delivery_data import fetch_delivery_data
        from validation.result import ValidationStatus
        
        # Build sample CSV with all required Bhavcopy columns
        header = "SYMBOL,SERIES,DELIV_PER,DATE1,OPEN_PRICE,HIGH_PRICE,LOW_PRICE,CLOSE_PRICE,LAST_PRICE,PREV_CLOSE,TTL_TRD_QNTY,TURNOVER_LACS,NO_OF_TRADES\n"
        target_rows = (
            "RELIANCE,BE,95.0,24-07-2026,2500,2550,2490,2540,2540,2500,10000,250,100\n"  # BE series row appears first
            "RELIANCE,EQ,45.2,24-07-2026,2500,2550,2490,2540,2540,2500,100000,2500,1000\n" # EQ series row appears second
        )
        dummy_rows = "".join([f"DUMMY{i},EQ,50.0,24-07-2026,100,105,99,102,102,100,1000,10,50\n" for i in range(25)])
        csv_data = header + target_rows + dummy_rows
        
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = csv_data
        
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        mock_session_cls.return_value.__enter__.return_value = mock_session
        
        mock_val_res = MagicMock()
        mock_val_res.status = ValidationStatus.VALID
        mock_val_res.score = 100
        mock_val_res.result.has_warnings = False
        mock_val_res.result.warnings = []
        
        with patch('pledge_scraper.get_scraper_api_key', return_value='test_key'), \
             patch('database.get_bhavcopy_cache', return_value=None), \
             patch('database.save_bhavcopy_cache'), \
             patch('delivery_data.ValidationEngine.process', return_value=mock_val_res):
            res = fetch_delivery_data(datetime.date(2026, 7, 24), skip_db_save=True)
            self.assertEqual(res.get("RELIANCE"), 45.2, "EQ series delivery percentage (45.2%) MUST be prioritized over BE series (95.0%)")

    @patch('database.get_connection')
    def test_update_shadow_alert_outcome(self, mock_get_conn):
        """Verify update_shadow_alert_outcome updates counterfactual shadow columns without altering main status."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        
        # Row 1: shadow_status is currently 'SHADOW_OPEN'
        mock_cursor.fetchone.return_value = ('SHADOW_OPEN',)
        
        from database import update_shadow_alert_outcome
        update_shadow_alert_outcome(101, 'SHADOW_WIN', 125.0, 15.5)
        
        # Verify SQL query updated shadow columns
        mock_cursor.execute.assert_called()
        calls = mock_cursor.execute.call_args_list
        update_call = calls[-1]
        self.assertIn("UPDATE alerts", update_call[0][0])
        self.assertIn("shadow_status = %s", update_call[0][0])
        self.assertEqual(update_call[0][1][0], 'SHADOW_WIN')
        self.assertEqual(update_call[0][1][1], 125.0)
        self.assertEqual(update_call[0][1][2], 15.5)

    @patch('database.get_connection')
    def test_get_all_failed_reversal_cooldown_symbols(self, mock_get_conn):
        """Verify get_all_failed_reversal_cooldown_symbols includes pnl_pct in CTE selection without SQL error."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        
        mock_cursor.fetchall.return_value = [
            ('TATASTEEL', datetime.now().date(), 'SL_HIT')
        ]
        
        from database import get_all_failed_reversal_cooldown_symbols
        res = get_all_failed_reversal_cooldown_symbols(cooldown_days=30)
        
        self.assertIn('TATASTEEL', res)
        # Verify query includes a.pnl_pct in LatestAlerts CTE
        query_sql = mock_cursor.execute.call_args[0][0]
        self.assertIn("a.pnl_pct", query_sql)
        self.assertIn("pnl_pct IS NOT NULL AND pnl_pct < 0", query_sql)

    def test_multibagger_conviction_tier_classification(self):
        """Verify classify_conviction correctly assigns Prime, High Quality, or Watchlist tiers."""
        from multibagger import classify_conviction
        # Prime Multibagger: composite >= 75, cqs >= 65, pas >= 50, trend >= 10, f_score >= 7
        tier1, score1 = classify_conviction(cqs=70.0, pas=60.0, trend=15.0, composite=80.0, f_score=7)
        self.assertEqual(tier1, "🚀 Prime Multibagger")

        # High Quality: composite >= 65, cqs >= 60, trend >= 10
        tier2, score2 = classify_conviction(cqs=62.0, pas=40.0, trend=12.0, composite=68.0, f_score=5)
        self.assertEqual(tier2, "💎 High Quality")

        # Watchlist: composite >= 50 (does not trigger active BUY alert)
        tier3, score3 = classify_conviction(cqs=52.0, pas=30.0, trend=5.0, composite=55.0, f_score=4)
        self.assertEqual(tier3, "🟡 Watchlist")

if __name__ == '__main__':
    unittest.main()
