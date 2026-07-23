import sys
sys.path.append("/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app")
import pandas as pd
import os
from config import DATA_DIR
from price_cache import _is_cache_up_to_date, _is_cache_long_enough

history_dir = os.path.join(DATA_DIR, "historical_data")
sym = "RELIANCE.NS"
cache_file = os.path.join(history_dir, f"{sym}_1d.parquet")

if os.path.exists(cache_file):
    df = pd.read_parquet(cache_file)
    last_ts = pd.to_datetime(df['Date'].iloc[-1])
    if last_ts.tzinfo is None:
        from zoneinfo import ZoneInfo
        last_ts = last_ts.tz_localize(ZoneInfo("Asia/Kolkata"))
    
    print(f"File exists: {cache_file}")
    print(f"Rows: {len(df)}")
    print(f"Last TS: {last_ts}")
    up_to_date = _is_cache_up_to_date(last_ts, "1d")
    long_enough = _is_cache_long_enough(df, "1y", sym)
    print(f"Up to date: {up_to_date}")
    print(f"Long enough: {long_enough}")
else:
    print(f"File not found: {cache_file}")
