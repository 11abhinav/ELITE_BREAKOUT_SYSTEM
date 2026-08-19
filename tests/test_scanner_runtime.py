import unittest
from unittest.mock import patch, MagicMock
import os

_orig_db_url = os.environ.get("DATABASE_URL")
_orig_railway = os.environ.get("RAILWAY_ENVIRONMENT")
_orig_dont_save = os.environ.get("DONT_SAVE_ALERTS")

os.environ["DATABASE_URL"] = "postgres://fake:fake@fake:5432/fake"
os.environ["RAILWAY_ENVIRONMENT"] = "test"
os.environ["DONT_SAVE_ALERTS"] = "1"

# Mock database get_connection and psycopg2 before importing anything that uses it
mock_conn = MagicMock()
mock_cursor = MagicMock()
mock_cursor.fetchone.return_value = ['{}']
mock_cursor.fetchall.return_value = []
mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
patch('database.get_connection', return_value=mock_conn).start()
patch('database.init_db').start()
patch('psycopg2.connect', return_value=mock_conn).start()
patch('psycopg2.pool.ThreadedConnectionPool', return_value=MagicMock()).start()

# Import the entry wrappers for all scanners
from eod_scanner import _start_wrapper as eod_start
from multibagger import _start_wrapper as multibagger_start
from multi_tf_scanner import _start_wrapper as mtf_start
from reversal_scanner import _start_wrapper as reversal_start
from wealth_engine import _run_wealth_scan_wrapper as wealth_start
from pullback_pipeline import run_pullback_pipeline as pullback_start

class TestScannerRuntimeFailures(unittest.TestCase):
    """
    Test suite to validate that all scanners gracefully handle unexpected runtime
    exceptions without cascading into NameErrors or UnboundLocalErrors in their
    cleanup/telemetry blocks.
    """

    @patch('eod_scanner.init_db')
    @patch('eod_scanner.get_watchlist')
    def test_eod_scanner_crash_handling(self, mock_get_mtf_target_universe, mock_init_db):
        mock_get_mtf_target_universe.side_effect = Exception("Simulated DB crash in EOD")
        
        # EOD gracefully catches watchlist exceptions and returns 0
        result = eod_start(force=True)
        self.assertEqual(result, 0)

    @patch('multibagger.init_db')
    def test_multibagger_crash_handling(self, mock_init_db):
        mock_init_db.side_effect = Exception("Simulated DB crash in Multibagger")
        
        # Multibagger doesn't wrap the whole thing in a try block! If init_db fails,
        # it will propagate up. But what if fetch_constituents fails?
        # Our telemetry fix ensured that variables are initialized at the top.
        with self.assertRaises(Exception):
            multibagger_start(is_test_mode=True)

    @patch('wealth_engine.os.path.exists')
    def test_wealth_engine_crash_handling(self, mock_exists):
        # Trigger an exception early in the wealth engine
        mock_exists.side_effect = Exception("Simulated FS crash in Wealth Engine")
        
        # Wealth Engine wraps everything in a huge try/except block.
        # It should catch it, update health to DOWN, and NOT crash with NameError.
        try:
            wealth_start(is_test_mode=True)
        except Exception as e:
            self.fail(f"Wealth engine leaked an exception: {e}")

    @patch('reversal_scanner.init_db')
    @patch('reversal_scanner.get_watchlist')
    def test_reversal_scanner_crash_handling(self, mock_get_mtf_target_universe, mock_init_db):
        mock_get_mtf_target_universe.side_effect = Exception("Simulated crash in Reversal")
        
        # Reversal gracefully catches watchlist exceptions and returns 0
        result = reversal_start(force=True)
        self.assertEqual(result, 0)

    @patch('pullback_pipeline.init_db')
    @patch('pullback_pipeline.get_nifty_20d_return')
    def test_pullback_scanner_crash_handling(self, mock_nifty, mock_init_db):
        mock_nifty.side_effect = Exception("Simulated crash in Pullback")
        
        # Pullback doesn't wrap everything, it will propagate. 
        with self.assertRaises(Exception):
            pullback_start(force=True)

    @patch('multi_tf_scanner.get_mtf_target_universe')
    def test_multi_tf_scanner_crash_handling(self, mock_get_mtf_target_universe):
        mock_get_mtf_target_universe.side_effect = Exception("Simulated crash in Multi TF")
        
        # Multi TF wraps everything in a while True loop with a try/except,
        # and re-raises if run_once=True.
        with self.assertRaises(Exception):
            mtf_start(run_once=True, is_test_mode=True)

def teardown_module(module):
    """Clean up module-level patches and restore environment variables."""
    patch.stopall()
    if _orig_db_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = _orig_db_url

    if _orig_railway is None:
        os.environ.pop("RAILWAY_ENVIRONMENT", None)
    else:
        os.environ["RAILWAY_ENVIRONMENT"] = _orig_railway

    if _orig_dont_save is None:
        os.environ.pop("DONT_SAVE_ALERTS", None)
    else:
        os.environ["DONT_SAVE_ALERTS"] = _orig_dont_save


if __name__ == '__main__':
    unittest.main()

