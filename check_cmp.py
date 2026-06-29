import os
import pandas as pd
from datetime import datetime
import yfinance as yf

symbols = ["WABAG.NS", "UNITDSPR.NS", "RRKABEL.NS", "NETWEB.NS", "HUDCO.NS"]
DATA_DIR = "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/data"

print("--- Actual yfinance prices ---")
for sym in symbols:
    try:
        t = yf.Ticker(sym)
        print(f"{sym}: {t.fast_info.last_price}")
    except Exception as e:
        print(f"{sym}: Error {e}")

print("\n--- Parquet file check ---")
for sym in ["WABAG", "UNITDSPR", "RRKABEL", "NETWEB", "HUDCO"]:
    for interval in ["1m", "5m", "15m", "30m", "1h", "1d"]:
        path = os.path.join(DATA_DIR, "history", interval, f"{sym}.parquet")
        if os.path.exists(path):
            mtime = os.path.getmtime(path)
            dt = datetime.fromtimestamp(mtime)
            df = pd.read_parquet(path)
            last_close = df['Close'].iloc[-1] if not df.empty else None
            last_time = df.index[-1] if not df.empty else None
            print(f"{sym} {interval}: mtime={dt}, close={last_close}, last_time={last_time}")
