import os
import pytest
import pandas as pd
from unittest.mock import patch

from app.price_cache import fetch_unified_historical
import app.config as config

@pytest.fixture
def temp_cache_dir(tmp_path):
    """Overrides the cache directory to use a temporary pytest directory."""
    with patch('app.price_cache.DATA_DIR', str(tmp_path)):
        with patch('app.config.DATA_DIR', str(tmp_path)):
            yield str(tmp_path)

def test_scenario_yahoo_returns_one_row(temp_cache_dir):
    """
    E2E Scenario:
    1. System has healthy cached data (10 rows).
    2. A scan is triggered. Yahoo returns only 1 row (corrupted/partial response).
    3. We assert:
       - Validation fails.
       - Cache is NOT overwritten (retained).
       - System falls back to returning the historical cache (so scanner continues).
    """
    symbol = "TEST.NS"
    interval = "1d"
    
    # 1. Establish healthy cache (10 rows)
    history_dir = os.path.join(temp_cache_dir, "history", interval)
    os.makedirs(history_dir, exist_ok=True)
    
    healthy_df = pd.DataFrame({
        "Date": pd.date_range("2023-01-01", periods=10, freq="D"),
        "Open": [100.0] * 10,
        "High": [105.0] * 10,
        "Low": [95.0] * 10,
        "Close": [102.0] * 10,
        "Volume": [1000] * 10
    })
    healthy_df.set_index("Date", inplace=True)
    
    cache_path = os.path.join(history_dir, f"{symbol}.parquet")
    healthy_df.to_parquet(cache_path)
    
    # Verify cache is established
    assert os.path.exists(cache_path)
    
    # 2. Mock the external API fetch to return only 1 row, with missing/NaN values to simulate corrupted data (quality score < 50)
    one_row_df = pd.DataFrame({
        "Date": pd.to_datetime(["2023-01-11"]),
        "Open": [float('nan')], "High": [105.0], "Low": [95.0], "Close": [102.0], "Volume": [1000]
    })
    one_row_df.set_index("Date", inplace=True)
    
    # Patch yfinance directly to block all network calls and return our one_row_df
    with patch('yfinance.download', return_value=one_row_df) as mock_fetch:
        with patch('yfinance.Ticker'):
            # Patch the SmartFetcher so it forces YAHOO instead of Fyers
            with patch('app.data_provider.AutoSwitchingFetcher._should_use_fyers', return_value=False):
                # Patch _is_cache_up_to_date to always return False, forcing a fetch
                with patch('app.price_cache._is_cache_up_to_date', return_value=False):
                    with patch('app.price_cache._is_cache_long_enough', return_value=False):
                        # Mock database and fetch status to prevent errors during test
                        os.environ["DATABASE_URL"] = "postgresql://mock"
                        with patch('app.database.init_db', return_value=None):
                            with patch('app.database.upsert_data_fetch_health', return_value=None):
                                with patch('app.data_fetch_status.mark_failure', return_value=None):
                                    with patch('app.database.upload_history_bundle_to_db', return_value=None):
                                        with patch('app.database.restore_history_bundle_from_db', return_value=None):
                                            import logging
                                            logging.getLogger().setLevel(logging.DEBUG)
                                            
                                            # Force validation engine to return poor quality to trigger POOR_QUALITY_SCORE fallback
                                            from app.validation.report import DataQualityReport
                                            mock_report = DataQualityReport(
                                                is_valid=False,
                                                quality_score=0,
                                                critical_failures=("Simulated failure",),
                                                warnings=("Simulated poor quality",),
                                                status="ERROR",
                                                row_count=1
                                            )
                                            with patch('app.validation.engine.ValidationEngine.validate', return_value=mock_report):
                                                # Request data via price_cache just like eod_scanner does
                                                results = fetch_unified_historical([symbol], period="5d", interval="1d")
                                                print("RESULTS DICTIONARY:", results)
                                                df = results.get(symbol)
                                                print("DF IS:", df)
                                                assert df is not None
                                                assert len(df) == 10
                                                assert df.index[-1] == pd.to_datetime("2023-01-10")
                                            
                                            # Verify the cache file on disk was NOT mutated by the bad fetch
                                            cached_df_after = pd.read_parquet(cache_path)
                                            assert len(cached_df_after) == 10
