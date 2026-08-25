import yfinance as yf
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
IST = ZoneInfo("Asia/Kolkata")

splits = yf.Ticker("TDPOWERSYS.NS").splits
print(f"Splits: {splits}")
print(f"Splits index: {splits.index}")

today_ist = datetime.now(IST).date()
entry_date_obj = datetime(2026, 8, 17).date()

try:
    print(f"Index date: {splits.index.date}")
    relevant = splits[
        (splits.index.date >= entry_date_obj) &
        (splits.index.date <= today_ist)
    ]
    print(f"Relevant splits: {relevant}")
except Exception as e:
    print(f"Error: {e}")
