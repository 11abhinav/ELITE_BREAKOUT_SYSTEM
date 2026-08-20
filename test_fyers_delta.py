import sys
sys.path.append('app')
from data_provider import get_fetcher
fetcher = get_fetcher()

# Test DELTA fetch via Fyers (if Fyers is active)
print("Using fetcher:", type(fetcher).__name__)

res = fetcher.get_ohlcv("BPCL", interval="1d", period="1y", range_from="2026-07-13", range_to="2026-07-19")
df = res.df if (res is not None and hasattr(res, 'df')) else res
if df is not None and hasattr(df, '__len__'):
    print("Fetched delta rows:", len(df))
    print(df)
else:
    print("df is None")
