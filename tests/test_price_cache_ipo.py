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
    
    # 1. Setup mock data (80 days of data)
    today = datetime.now()
    dates = [today - timedelta(days=i) for i in range(80)]
    dates.reverse()
    df = pd.DataFrame({"Date": dates, "Close": [100]*80})
    df.set_index("Date", inplace=True)
    
    req_period = "1y" # expects 300 days * 0.65 = 195 days
    
    # Test 1: Before full fetch (no earliest_date recorded)
    is_long = _is_cache_long_enough(df, req_period, sym)
    assert is_long is False, "Expected cache to NOT be long enough before FULL fetch"
    
    # Simulate the FULL fetch recording the earliest date
    earliest_dt = dates[0].date().isoformat()
    with open(earliest_path, "w") as f:
        json.dump({sym: earliest_dt}, f)
        
    # Test 2: After FULL fetch (earliest_date matches)
    is_long_2 = _is_cache_long_enough(df, req_period, sym)
    assert is_long_2 is True, "Expected cache to be long enough after FULL fetch recorded earliest date"
