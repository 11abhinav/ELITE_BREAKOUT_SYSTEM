import os
import sys
import time
import glob
from concurrent.futures import ThreadPoolExecutor, as_completed

# Make sure app modules can be imported
sys.path.insert(0, os.path.join(os.getcwd(), 'app'))

from price_cache import get_cached_df
from multibagger import _parse_single_symbol_price_data
from datetime import datetime

IST_ZONE = __import__('pytz').timezone('Asia/Kolkata')
ist_now = datetime.now(IST_ZONE)

stats = {
    "parquet_read_ms": 0.0,
    "parse_total_ms": 0.0,
    "indicator_compute_ms": 0.0,
    "registry_ms": 0.0,
    "total_ms": 0.0,
    "has_RSI": 0,
    "has_ATR": 0,
    "has_EMA20": 0,
    "has_SMA200": 0,
    "symbols_processed": 0
}

import threading
stats_lock = threading.Lock()

# Monkey-patch indicator_manager to track compute and registry time
import indicator_manager
original_compute = indicator_manager.manager.compute_base_indicators

def patched_compute(df, symbol):
    t0 = time.perf_counter()
    
    # We will simulate the compute but track time.
    res = original_compute(df, symbol)
    t1 = time.perf_counter()
    
    with stats_lock:
        stats["indicator_compute_ms"] += (t1 - t0) * 1000
    
    return res

indicator_manager.manager.compute_base_indicators = patched_compute

def _load_single(s):
    t0 = time.perf_counter()
    df_sym = get_cached_df(s, interval="1d", period="1y")
    t1 = time.perf_counter()
    
    if df_sym is not None and not df_sym.empty:
        cols = set(df_sym.columns)
        has_rsi = "RSI" in cols
        has_atr = "ATR" in cols
        has_ema = "EMA20" in cols
        has_sma = "SMA200" in cols
        
        parsed_spd = _parse_single_symbol_price_data(s, df_sym, ist_now, strip_forming=False)
        t2 = time.perf_counter()
        
        with stats_lock:
            stats["parquet_read_ms"] += (t1 - t0) * 1000
            stats["parse_total_ms"] += (t2 - t1) * 1000
            if has_rsi: stats["has_RSI"] += 1
            if has_atr: stats["has_ATR"] += 1
            if has_ema: stats["has_EMA20"] += 1
            if has_sma: stats["has_SMA200"] += 1
            stats["symbols_processed"] += 1
            
        return s, parsed_spd
    return s, None

def run_test():
    # Find up to 750 symbols from parquet files
    files = glob.glob(os.path.join(os.getcwd(), 'data', 'history', '1d', '*.parquet'))
    symbols = [os.path.basename(f).replace('.parquet', '') for f in files][:750]
    
    print(f"Found {len(symbols)} symbols. Starting profile with 24 threads...")
    
    start_time = time.perf_counter()
    
    with ThreadPoolExecutor(max_workers=24) as executor:
        futures = [executor.submit(_load_single, s) for s in symbols]
        for future in as_completed(futures):
            future.result()
            
    end_time = time.perf_counter()
    stats["total_ms"] = (end_time - start_time) * 1000
    
    print("\n--- PROFILING RESULTS ---")
    print(f"Total Wall-clock Time: {stats['total_ms']:.2f} ms")
    print(f"Symbols Processed: {stats['symbols_processed']}")
    print(f"Total Parquet Read (cumulative thread time): {stats['parquet_read_ms']:.2f} ms")
    print(f"Total Parse (cumulative thread time): {stats['parse_total_ms']:.2f} ms")
    print(f"  └─ of which Indicator Compute: {stats['indicator_compute_ms']:.2f} ms")
    print("\nColumns present in Parquet:")
    print(f"  RSI: {stats['has_RSI']} / {stats['symbols_processed']}")
    print(f"  ATR: {stats['has_ATR']} / {stats['symbols_processed']}")
    print(f"  EMA20: {stats['has_EMA20']} / {stats['symbols_processed']}")
    print(f"  SMA200: {stats['has_SMA200']} / {stats['symbols_processed']}")
    
if __name__ == "__main__":
    run_test()
