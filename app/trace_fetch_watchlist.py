import os, sys, logging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd

from price_cache import fetch_watchlist_data

test_df = pd.DataFrame({"Stock": ["ADANIPOWER"]})
res = fetch_watchlist_data(test_df, interval="1d", period="1y", requester="DEBUG_TRACE")
print(f"\nFETCH RESULT: {res}", flush=True)
if res and "ADANIPOWER" in res and res["ADANIPOWER"] is not None:
    df = res["ADANIPOWER"]
    print(f"ADANIPOWER df shape: {df.shape}, last_index: {df.index[-1] if not df.empty else 'empty'}", flush=True)
