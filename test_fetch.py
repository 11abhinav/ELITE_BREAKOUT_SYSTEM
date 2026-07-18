import sys
sys.path.append('app')
from data_provider import get_fetcher
import pandas as pd

fetcher = get_fetcher()
df = fetcher.get_ohlcv("BPCL", "1d", "1y")
print("BPCL length:", len(df) if df is not None else "None")
if df is not None and len(df) > 0:
    print(df.tail(2))

df2 = fetcher.get_ohlcv("HAVELLS", "1d", "1y")
print("HAVELLS length:", len(df2) if df2 is not None else "None")
if df2 is not None and len(df2) > 0:
    print(df2.tail(2))
