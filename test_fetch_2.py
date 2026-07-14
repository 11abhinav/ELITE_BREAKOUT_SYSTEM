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
    if v is None or (hasattr(v, 'empty') and v.empty):
        print(f'{k}: Empty')
    else:
        print(f'{k}: Success, shape {v.shape}')
