import pandas as pd
import sys
import os

from app.price_provider import PriceProvider
from app.data_provider import get_fetcher
import logging
logging.basicConfig(level=logging.DEBUG)

fetcher = get_fetcher()
res = fetcher.get_batch_ohlcv(['MARSONS', 'SIKA', 'TIMEX', 'AYE'], '1d', '1mo')
print('Keys in res:', res.keys())
for k, v in res.items():
    df_val = v.df if (v is not None and hasattr(v, 'df')) else v
    if df_val is None or (hasattr(df_val, 'empty') and df_val.empty):
        print(f'{k}: Empty')
    else:
        print(f'{k}: Success, shape {df_val.shape}')
