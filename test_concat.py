import sys
sys.path.append('app')
import pandas as pd
import os
from config import DATA_DIR
from data_provider import get_fetcher
from datetime import datetime
from zoneinfo import ZoneInfo
IST = ZoneInfo("Asia/Kolkata")

history_dir = os.path.join(DATA_DIR, "history", "1d")
bpcl_path = os.path.join(history_dir, "BPCL.parquet")

cached_df = pd.read_parquet(bpcl_path)
print("Cached columns:", cached_df.columns)
print("Cached index:", cached_df.index.name)

fetcher = get_fetcher()
# simulate DELTA fetch
new_df = fetcher.get_ohlcv("BPCL", interval="1d", period="1y", range_from="2026-07-13", range_to="2026-07-19")
if new_df is not None:
    print("New columns:", new_df.columns)
    print("New index:", new_df.index.name)
    
    # Normalize New DF exactly like in price_cache
    time_col = 'Date' if 'Date' in new_df.columns else ('Datetime' if 'Datetime' in new_df.columns else None)
    if time_col:
        new_df[time_col] = pd.to_datetime(new_df[time_col])
        if new_df[time_col].dt.tz is None:
            new_df[time_col] = new_df[time_col].dt.tz_localize('Asia/Kolkata')
        else:
            new_df[time_col] = new_df[time_col].dt.tz_convert('Asia/Kolkata')
    elif not new_df.index.empty:
        new_df.index = pd.to_datetime(new_df.index)
        if new_df.index.tz is None:
            new_df.index = new_df.index.tz_localize('Asia/Kolkata')
        else:
            new_df.index = new_df.index.tz_convert('Asia/Kolkata')

    # Normalize Cached DF exactly like in price_cache
    c_time_col = 'Date' if 'Date' in cached_df.columns else ('Datetime' if 'Datetime' in cached_df.columns else None)
    if c_time_col:
        cached_df[c_time_col] = pd.to_datetime(cached_df[c_time_col])
        if cached_df[c_time_col].dt.tz is None:
            cached_df[c_time_col] = cached_df[c_time_col].dt.tz_localize('Asia/Kolkata')
        else:
            cached_df[c_time_col] = cached_df[c_time_col].dt.tz_convert('Asia/Kolkata')
    elif not cached_df.index.empty:
        cached_df.index = pd.to_datetime(cached_df.index)
        if cached_df.index.tz is None:
            cached_df.index = cached_df.index.tz_localize('Asia/Kolkata')
        else:
            cached_df.index = cached_df.index.tz_convert('Asia/Kolkata')

    combined = pd.concat([cached_df, new_df])
    
    time_col_comb = 'Date' if 'Date' in combined.columns else ('Datetime' if 'Datetime' in combined.columns else None)
    if time_col_comb:
        combined = combined.drop_duplicates(subset=[time_col_comb], keep='last')
    else:
        combined = combined[~combined.index.duplicated(keep='last')]
        
    combined = combined.sort_index() if time_col_comb is None else combined.sort_values(time_col_comb)
    print("Combined length:", len(combined))
else:
    print("new_df is None")
