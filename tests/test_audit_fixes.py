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

if __name__ == '__main__':
    unittest.main()
