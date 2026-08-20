import pandas as pd
from app.price_cache import _download_all_robust
from app.data_provider import get_fetcher

import app.database as db
db.upsert_data_fetch_health = lambda *args, **kwargs: None
db.delete_data_fetch_health = lambda *args, **kwargs: None
db.upsert_fetch_error = lambda *args, **kwargs: None
db.delete_fetch_error_on_success = lambda *args, **kwargs: None

import app.data_fetch_status as dfs
dfs.mark_success = lambda *args, **kwargs: None
dfs.mark_failure = lambda *args, **kwargs: None

import logging
logging.basicConfig(level=logging.INFO)

# Let's mock get_fetcher directly to print arguments
original_get_fetcher = get_fetcher
def mock_get_fetcher():
    fetcher = original_get_fetcher()
    original_get_batch = fetcher.get_batch_ohlcv
    
    def mock_get_batch(symbols, interval, period, retries=3, range_from=None, range_to=None, caller=None):
        print(f"CALLED get_batch_ohlcv:")
        print(f"symbols: {symbols}")
        print(f"range_from: {range_from}, range_to: {range_to}")
        res = original_get_batch(symbols, interval, period, retries, range_from, range_to, caller)
        for k, v in res.items():
            df_val = v.df if (v is not None and hasattr(v, 'df')) else v
            if df_val is None or getattr(df_val, 'empty', True):
                print(f"RESULT {k}: Empty")
            else:
                print(f"RESULT {k}: Success {getattr(df_val, 'shape', len(df_val))}")
        return res
    
    fetcher.get_batch_ohlcv = mock_get_batch
    return fetcher

import app.price_cache
app.price_cache.get_fetcher = mock_get_fetcher

wl = pd.DataFrame({'Stock': ['SIKA', 'AYE']})
_download_all_robust(wl, '1mo', '1d', requester='TEST')
