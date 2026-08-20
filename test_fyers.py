import sys
sys.path.append('app')
from config import DATA_PROVIDER
print("DATA_PROVIDER:", DATA_PROVIDER)

from data_provider import get_fetcher
fetcher = get_fetcher()
res_data = fetcher.get_ohlcv("BPCL", interval="1d", period="1y")
df = res_data.df if (res_data is not None and hasattr(res_data, 'df')) else res_data
if df is not None and not df.empty:
    print("Fetched rows:", len(df))
    print(df.tail())
else:
    print("df is None")
