import os
import sys
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from database import init_db
from price_cache import fetch_watchlist_data
from delivery_data import acquire_bhavcopy

init_db()

today_ist = datetime.now(ZoneInfo('Asia/Kolkata')).date()
print(f"Testing for date: {today_ist}")

# Fetch bhavcopy
final_dict, df = acquire_bhavcopy(today_ist)
if final_dict:
    print("Bhavcopy fetched and registered.")
else:
    print("Failed to fetch Bhavcopy.")

watchlist = pd.DataFrame({"Stock": ["RELIANCE", "TCS"]})
data = fetch_watchlist_data(watchlist, period="5d", interval="1d", requester="test")

for sym in watchlist["Stock"]:
    df = data.get(sym)
    if df is not None and not df.empty:
        last_dt = df.index[-1] if isinstance(df.index, pd.DatetimeIndex) else pd.to_datetime(df['Date'].iloc[-1])
        print(f"{sym} - Last Date: {last_dt.date()} | is_stale: {df.attrs.get('is_stale', False)}")
        print(df.tail(2))
    else:
        print(f"{sym} - No data")

