import os, sys, traceback

app_dir = "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app"
if app_dir not in sys.path:
    sys.path.insert(0, app_dir)

from data_providers.unified_fetcher import UnifiedFetcher
from data_provider import YFinanceFetcher

print("--- TESTING YFinanceFetcher DIRECTLY FOR ADANIPOWER.NS ---", flush=True)
try:
    yff = YFinanceFetcher()
    res = yff.get_batch_ohlcv(["ADANIPOWER.NS", "CYIENT.NS", "RTNPOWER.NS"], interval="1d", period="1y")
    print(f"YFinance result keys: {list(res.keys()) if res else None}", flush=True)
    for k, v in res.items():
        if v and hasattr(v, "dataframe") and v.dataframe is not None:
            print(f"{k}: shape={v.dataframe.shape}, last_index={v.dataframe.index[-1]}", flush=True)
        else:
            print(f"{k}: None or empty", flush=True)
except Exception as e:
    print(f"YFinance error: {e}", flush=True)
    traceback.print_exc()

print("\n--- TESTING UnifiedFetcher FOR ADANIPOWER ---", flush=True)
try:
    uf = UnifiedFetcher()
    df_uf = uf.fetch_historical("ADANIPOWER", interval="1d", period="1y", consumer="trace_test")
    print(f"UnifiedFetcher result: {df_uf.shape if df_uf is not None else None}", flush=True)
    if df_uf is not None and not df_uf.empty:
        print(f"UnifiedFetcher last date: {df_uf.index[-1] if hasattr(df_uf.index, 'date') else df_uf.iloc[-1]}", flush=True)
except Exception as e:
    print(f"UnifiedFetcher error: {e}", flush=True)
    traceback.print_exc()
