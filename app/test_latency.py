import time
from database import get_user_watchlist, get_all_scanners_today_trades, get_all_scanner_health
import os
import pandas as pd
from config import DATA_DIR
import sys

def test_watchlist():
    start = time.time()
    res = get_user_watchlist("DEFAULT_USER")
    dur = time.time() - start
    print(f"Watchlist load time: {dur:.4f}s for {len(res)} items")
    import json
    sz = len(json.dumps(res, default=str))
    print(f"Watchlist JSON size: {sz/1024:.2f} KB")

def test_scanner_health():
    start = time.time()
    health_rows = get_all_scanner_health()
    dur = time.time() - start
    print(f"get_all_scanner_health time: {dur:.4f}s")
    
    start = time.time()
    today_str = "2026-07-28"
    all_today_trades = get_all_scanners_today_trades(today_str)
    dur = time.time() - start
    print(f"get_all_scanners_today_trades time: {dur:.4f}s")
    
    start = time.time()
    wealth_path = os.path.join(DATA_DIR, "elite_wealth_system.parquet")
    if os.path.exists(wealth_path):
        wdf = pd.read_parquet(wealth_path)
        buy_df = wdf[wdf["Signal_Code"] == "BUY"]
        today_trades = []
        for _, wrow in buy_df.iterrows():
            pass
    dur = time.time() - start
    print(f"Wealth Engine parquet parsing time: {dur:.4f}s")

if __name__ == "__main__":
    print("Testing Watchlist API...")
    test_watchlist()
    print("\nTesting Scanner Health API...")
    test_scanner_health()
