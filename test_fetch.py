import pandas as pd
import sys
import os

from app.price_cache import _download_all_robust

# Mock DB calls
import app.database as db
db.upsert_data_fetch_health = lambda *args, **kwargs: None
db.delete_data_fetch_health = lambda *args, **kwargs: None
db.upsert_fetch_error = lambda *args, **kwargs: None
db.delete_fetch_error_on_success = lambda *args, **kwargs: None

import app.data_fetch_status as dfs
dfs.mark_success = lambda *args, **kwargs: None
dfs.mark_failure = lambda *args, **kwargs: None

wl = pd.DataFrame({'Stock': ['MARSONS', 'SIKA', 'TIMEX', 'AYE']})
all_data = _download_all_robust(wl, '1mo', '1d', requester='TEST')
print('Keys in all_data:', all_data.keys())
for k, v in all_data.items():
    if v is None or (hasattr(v, 'empty') and v.empty):
        print(f'{k}: Empty')
    else:
        print(f'{k}: Success, shape {v.shape}')
