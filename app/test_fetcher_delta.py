import os, sys, logging
logging.basicConfig(level=logging.DEBUG)

app_dir = "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app"
if app_dir not in sys.path:
    sys.path.insert(0, app_dir)

from data_provider import get_fetcher

fetcher = get_fetcher()
print(f"Fetcher instance: {fetcher}", flush=True)

res = fetcher.get_batch_ohlcv(["ADANIPOWER"], interval="1d", period="1y", range_from="2026-08-25", range_to="2026-09-06", caller="TEST")
print(f"Result keys: {list(res.keys()) if res else 'None'}", flush=True)
if res and "ADANIPOWER" in res:
    md = res["ADANIPOWER"]
    print(f"MD: dataframe={md.dataframe.shape if md.dataframe is not None else None}, error={md.error}, source={md.source}", flush=True)
