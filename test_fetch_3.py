import pandas as pd
import sys
import os

from app.data_provider import get_fetcher

# Mock DB calls
import app.database as db
db.upsert_data_fetch_health = lambda *args, **kwargs: None
db.delete_data_fetch_health = lambda *args, **kwargs: None
db.upsert_fetch_error = lambda *args, **kwargs: None
db.delete_fetch_error_on_success = lambda *args, **kwargs: None

fetcher = get_fetcher()
res = fetcher.get_batch_ohlcv(['MARSONS', 'SIKA', 'TIMEX', 'AYE'], '1d', '1mo')
print('Keys in res:', res.keys())
for k, v in res.items():
    df_val = v.df if (v is not None and hasattr(v, 'df')) else v
    if df_val is None or (hasattr(df_val, 'empty') and df_val.empty):
        print(f'{k}: Empty')
    else:
        print(f'{k}: Success, shape {df_val.shape}')
