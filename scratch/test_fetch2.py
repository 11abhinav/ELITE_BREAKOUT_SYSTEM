import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app')))

from data_provider import get_fetcher
import logging

logging.basicConfig(level=logging.DEBUG)

symbol = "APOLLOHOSP"
print(f"Fetching data for {symbol}...")
fetcher = get_fetcher()
df = fetcher.get_ohlcv(symbol, interval="1d", period="100d")
if df is not None and not df.empty:
    print(f"Success! Got {len(df)} rows.")
    print(df.tail(2))
else:
    print("Failed to get data.")
