import sys
sys.path.append('app')
from data_provider import get_fetcher
fetcher = get_fetcher()

# Test DELTA fetch via Fyers (if Fyers is active)
print("Using fetcher:", type(fetcher).__name__)

df = fetcher.get_ohlcv("BPCL", interval="1d", period="1y", range_from="2026-07-13", range_to="2026-07-19")
if df is not None:
    print("Fetched delta rows:", len(df))
    print(df)
else:
    print("df is None")
