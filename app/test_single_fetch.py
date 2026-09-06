import os, sys
app_dir = "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app"
if app_dir not in sys.path:
    sys.path.insert(0, app_dir)

from price_cache import fetch_unified_historical

res = fetch_unified_historical(["ADANIPOWER"], period="1y", interval="1d", requester="TEST")
print(f"FETCH KEYS: {list(res.keys()) if res else 'None'}", flush=True)
if res and "ADANIPOWER" in res:
    df = res["ADANIPOWER"]
    print(f"ADANIPOWER df: {df.shape if df is not None else 'None'}", flush=True)
    if df is not None and not df.empty:
        print(f"ADANIPOWER last row: {df.iloc[-1]}", flush=True)
