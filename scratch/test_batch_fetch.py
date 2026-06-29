import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app')))

from data_provider import get_fetcher
import logging

logging.basicConfig(level=logging.DEBUG)

symbols = ["APARINDS", "CUMMINSIND", "APOLLOHOSP", "ADANIPORTS", "ADANIPOWER"]
print(f"Fetching batch data for {symbols}...")
fetcher = get_fetcher()
data_dict = fetcher.get_batch_ohlcv(symbols, interval="1d", period="100d")

for sym, df in data_dict.items():
    if df is not None and not df.empty:
        print(f"{sym}: Success! Got {len(df)} rows.")
    else:
        print(f"{sym}: Failed to get data.")
