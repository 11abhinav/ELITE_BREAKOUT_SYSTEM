import os
import sys
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from price_cache import fetch_watchlist_data
from delivery_data import fetch_bhavcopy, process_delivery_data

# Ensure we have today's Bhavcopy in the registry
today_ist = datetime.now(ZoneInfo('Asia/Kolkata')).date()
print(f"Testing for date: {today_ist}")

# Fetch bhavcopy manually just in case
b_csv = fetch_bhavcopy(today_ist)
if b_csv:
    process_delivery_data(b_csv, today_ist)
else:
    print("Failed to fetch Bhavcopy for today. It might be a weekend or holiday, or NSE hasn't published it.")

# Fetch data for a couple of symbols
watchlist = pd.DataFrame({"Stock": ["RELIANCE", "TCS", "INFY"]})
data = fetch_watchlist_data(watchlist, period="1mo", interval="1d", requester="test")

for sym in watchlist["Stock"]:
    df = data.get(sym)
    if df is not None and not df.empty:
        last_dt = df.index[-1] if isinstance(df.index, pd.DatetimeIndex) else pd.to_datetime(df['Date'].iloc[-1])
        print(f"{sym} - Last Date: {last_dt.date()} | is_stale: {df.attrs.get('is_stale', False)}")
    else:
        print(f"{sym} - No data")

