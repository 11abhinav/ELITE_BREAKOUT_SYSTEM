import sys
sys.path.append('app')
from data_provider import get_fetcher
import pandas as pd

fetcher = get_fetcher()
res = fetcher.get_ohlcv("BPCL", "1d", "1y")
df = res.df if (res is not None and hasattr(res, 'df')) else res
print("BPCL length:", len(df) if (df is not None and hasattr(df, '__len__')) else "None")
if df is not None and hasattr(df, 'tail') and len(df) > 0:
    print(df.tail(2))

res2 = fetcher.get_ohlcv("HAVELLS", "1d", "1y")
df2 = res2.df if (res2 is not None and hasattr(res2, 'df')) else res2
print("HAVELLS length:", len(df2) if (df2 is not None and hasattr(df2, '__len__')) else "None")
if df2 is not None and hasattr(df2, 'tail') and len(df2) > 0:
    print(df2.tail(2))
