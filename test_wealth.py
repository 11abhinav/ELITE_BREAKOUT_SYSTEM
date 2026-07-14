import os
import sys
sys.path.insert(0, os.path.abspath('app'))
from app.price_cache import fetch_unified_historical
import pandas as pd

# Try to load the actual watchlist
WATCHLIST_PATH = "data/watchlist.parquet"
if os.path.exists(WATCHLIST_PATH):
    df = pd.read_parquet(WATCHLIST_PATH)
    symbols = list(set(df["Stock"].astype(str).tolist()))[:10] # test with 10 symbols
else:
    symbols = ["INFY.NS", "TCS.NS", "AAYUSHBULL.NS"]

print(f"Testing fetch for {len(symbols)} symbols: {symbols}")
all_data = fetch_unified_historical(symbols, period="1mo", interval="1d")
fetched_count = sum(1 for v in all_data.values() if v is not None and not v.empty)
print(f"Fetched {fetched_count}/{len(symbols)}")
for sym, df in all_data.items():
    if df is not None:
        print(f"{sym}: {len(df)} rows")
    else:
        print(f"{sym}: None")
