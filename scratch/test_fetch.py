import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app')))

from data_provider import get_data
import logging

logging.basicConfig(level=logging.DEBUG)

symbol = "APOLLOHOSP"
print(f"Fetching data for {symbol}...")
df = get_data(symbol, interval="1d", lookback_days=100)
if df is not None and not df.empty:
    print(f"Success! Got {len(df)} rows.")
    print(df.tail(2))
else:
    print("Failed to get data.")
