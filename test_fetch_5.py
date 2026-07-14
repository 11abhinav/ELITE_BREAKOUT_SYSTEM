import pandas as pd
from app.price_cache import _download_all_robust

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
logger = logging.getLogger("app.price_cache")
logger.setLevel(logging.INFO)

wl = pd.DataFrame({'Stock': ['SIKA', 'AYE']})
_download_all_robust(wl, '1mo', '1d', requester='TEST')
