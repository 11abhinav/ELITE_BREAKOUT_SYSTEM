import os
import pandas as pd
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed

DATA_DIR = "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/data"
symbols = ["WABAG", "UNITDSPR", "RRKABEL", "NETWEB", "HUDCO"]

prices = {}
def fetch_cmp(sym):
    try:
        yf_sym = sym if sym.endswith(".NS") else f"{sym}.NS"
        t = yf.Ticker(yf_sym)
        price = float(t.fast_info.last_price)
        if pd.isna(price):
            raise ValueError("NaN price")
        return sym, price
    except Exception as e:
        print(f"Fallback for {sym}: {e}")
        try:
            sym_clean = sym.replace(':', '_')
            latest_mtime = 0
            best_file = None
            for interval in ["1m", "5m", "15m", "30m", "1h", "1d"]:
                file_path = os.path.join(DATA_DIR, "history", interval, f"{sym_clean}.parquet")
                if os.path.exists(file_path):
                    mtime = os.path.getmtime(file_path)
                    if mtime > latest_mtime:
                        latest_mtime = mtime
                        best_file = file_path
            if best_file:
                df = pd.read_parquet(best_file)
                if not df.empty and "Close" in df.columns:
                    df_valid = df.dropna(subset=["Close"])
                    if not df_valid.empty:
                        return sym, float(df_valid["Close"].iloc[-1])
        except Exception:
            pass
        return sym, None

with ThreadPoolExecutor(max_workers=10) as executor:
    futures = {executor.submit(fetch_cmp, sym): sym for sym in symbols}
    for future in as_completed(futures):
        sym, price = future.result()
        if price is not None:
            prices[sym] = price
            
print(prices)
