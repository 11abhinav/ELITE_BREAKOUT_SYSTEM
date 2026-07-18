import sys
sys.path.append('app')
from config import DATA_PROVIDER
print("DATA_PROVIDER:", DATA_PROVIDER)

from data_provider import get_fetcher
fetcher = get_fetcher()
df = fetcher.get_ohlcv("BPCL", interval="1d", period="1y")
if df is not None:
    print("Fetched rows:", len(df))
    print(df.tail())
else:
    print("df is None")
