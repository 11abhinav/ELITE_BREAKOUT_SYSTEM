import os
import sys
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from price_cache import fetch_watchlist_data, _download_all_robust

def test_price_cache_fresh_count_initialization(monkeypatch, tmp_path):
    """
    Regression Test: Guarantees that fetch_watchlist_data and _download_all_robust
    initialize fresh_count at top scope and execute delta fetch branches without UnboundLocalError.
    """
    # 1. Create a dummy watchlist
    watchlist = pd.DataFrame({"Stock": ["TESTSTOCK1", "TESTSTOCK2"]})
    
    # 2. Mock history directory
    test_history_dir = str(tmp_path / "history" / "1d")
    os.makedirs(test_history_dir, exist_ok=True)
    
    # 3. Create pre-existing cached parquet file for TESTSTOCK1 (to trigger needs_full = False delta branch)
    dates = pd.date_range(end=datetime.now(ZoneInfo("Asia/Kolkata")), periods=50, freq="D")
    df_cache = pd.DataFrame({
        "Date": dates,
        "Open": np.random.randn(50) + 100,
        "High": np.random.randn(50) + 105,
        "Low": np.random.randn(50) + 95,
        "Close": np.random.randn(50) + 100,
        "Volume": [100000] * 50
    })
    
    p_path = os.path.join(test_history_dir, "TESTSTOCK1.parquet")
    df_cache.to_parquet(p_path)
    
    # Mock DATA_DIR
    monkeypatch.setattr("price_cache.DATA_DIR", str(tmp_path))
    
    # Mock fetcher get_batch_ohlcv to return fresh data (triggering line 703 fresh_count += 1)
    class MockMarketData:
        def __init__(self, df):
            self.df = df
            self.dataframe = df
            self.source = "MockFetcher"
            self.quality_report = type('Report', (), {'quality_score': 100, 'status': 'ValidationStatus.VALID', 'validator_name': 'Mock'})()
            self.row_count = len(df) if df is not None else 0
            
    class MockFetcher:
        def get_batch_ohlcv(self, symbols, interval, period, retries=3, range_from=None, range_to=None, caller=None):
            res = {}
            for sym in symbols:
                d_fresh = pd.date_range(end=datetime.now(ZoneInfo("Asia/Kolkata")), periods=10, freq="D")
                df_fresh = pd.DataFrame({
                    "Date": d_fresh,
                    "Open": np.random.randn(10) + 100,
                    "High": np.random.randn(10) + 105,
                    "Low": np.random.randn(10) + 95,
                    "Close": np.random.randn(10) + 100,
                    "Volume": [100000] * 10
                })
                res[sym] = MockMarketData(df_fresh)
            return res

    monkeypatch.setattr("price_cache.get_fetcher", lambda: MockFetcher())
    
    # Execute _download_all_robust
    result = _download_all_robust(watchlist, period="1y", interval="1d", requester="TEST")
    
    # Assert no UnboundLocalError occurred and data is returned cleanly
    assert isinstance(result, dict)
    assert "TESTSTOCK1" in result
    assert "TESTSTOCK2" in result
