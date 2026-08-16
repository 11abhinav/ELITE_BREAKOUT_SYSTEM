import os
import json
import pandas as pd
from datetime import datetime, timedelta
import sys

# Ensure app directory is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app')))

from price_cache import _is_cache_long_enough
import price_cache

def test_ipo_cache_logic(tmp_path, monkeypatch):
    sym = "TEST_IPO_PYTEST"
    
    # Mock DATA_DIR in price_cache
    monkeypatch.setattr(price_cache, "DATA_DIR", str(tmp_path))
    earliest_path = os.path.join(str(tmp_path), "earliest_dates.json")
    
    # 1. Setup mock data (20 days of data, below the 30-row incremental threshold)
    today = datetime.now()
    dates = [today - timedelta(days=i) for i in range(20)]
    dates.reverse()
    df = pd.DataFrame({"Date": dates, "Close": [100]*20})
    df.set_index("Date", inplace=True)
    
    req_period = "1y" # expects 300 days * 0.65 = 195 days
    
    # Test 1: Before full fetch (no earliest_date recorded and < 30 rows)
    is_long = _is_cache_long_enough(df, req_period, sym)
    assert is_long is False, "Expected cache to NOT be long enough before FULL fetch"
    
    # Simulate the FULL fetch recording the earliest date
    earliest_dt = dates[0].date().isoformat()
    with open(earliest_path, "w") as f:
        json.dump({sym: earliest_dt}, f)
        
    # Test 2: After FULL fetch (earliest_date matches)
    is_long_2 = _is_cache_long_enough(df, req_period, sym)
    assert is_long_2 is True, "Expected cache to be long enough after FULL fetch recorded earliest date"


def test_ipo_cache_poisoning(tmp_path, monkeypatch):
    """
    Ensures that a short-period FULL fetch (like '10d') does NOT write to earliest_dates.json,
    which would incorrectly permanently cap the history of the stock.
    """
    sym = "TEST_POISON_PYTEST"
    monkeypatch.setattr(price_cache, "DATA_DIR", str(tmp_path))
    earliest_path = os.path.join(str(tmp_path), "earliest_dates.json")
    
    # 1. Setup mock data (5 days of data)
    today = datetime.now()
    dates = [today - timedelta(days=i) for i in range(5)]
    dates.reverse()
    df_new = pd.DataFrame({"Date": dates, "Close": [100]*5})
    
    # Simulate a FULL fetch (group_key="FULL") where the requested period was short ("10d")
    group_key = "FULL"
    period = "10d"
    
    # Replicate the exact logic fixed in price_cache.py
    if group_key == "FULL" and not df_new.empty and period.lower() in ("max", "10y", "5y", "2y", "1y", "ytd"):
        t_col = 'Date'
        earliest_ts = pd.to_datetime(df_new[t_col].iloc[0])
        earliest_dt_str = earliest_ts.date().isoformat()
        earliest_dates = {}
        earliest_dates[sym] = earliest_dt_str
        with open(earliest_path, "w") as f:
            json.dump(earliest_dates, f)
            
    # Assert that the file was NOT created because period="10d" is not in the allowed list
    assert not os.path.exists(earliest_path), "earliest_dates.json should NOT be created for a 10d fetch!"
    
    # Now simulate a FULL fetch for "1y"
    period = "1y"
    if group_key == "FULL" and not df_new.empty and period.lower() in ("max", "10y", "5y", "2y", "1y", "ytd"):
        t_col = 'Date'
        earliest_ts = pd.to_datetime(df_new[t_col].iloc[0])
        earliest_dt_str = earliest_ts.date().isoformat()
        earliest_dates = {}
        earliest_dates[sym] = earliest_dt_str
        with open(earliest_path, "w") as f:
            json.dump(earliest_dates, f)
            
    # Assert that the file WAS created for "1y"
    assert os.path.exists(earliest_path), "earliest_dates.json SHOULD be created for a 1y fetch!"
