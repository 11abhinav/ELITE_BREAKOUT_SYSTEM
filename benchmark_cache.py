import time
import os
import sys
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "app")))

from valuation_utils import normalize_id

def benchmark():
    cache_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "app", "data", "fundamentals_cache.json"))
    if not os.path.exists(cache_file):
        print(f"Cache file not found at {cache_file}")
        return

    with open(cache_file, "r") as f:
        cache_data = json.load(f)

    print(f"Loaded cache entries: {len(cache_data)}")

    symbols = list(cache_data.keys())[:748]

    # 1. OLD IMPLEMENTATION (Linear Scan O(N))
    print("\n--- Running OLD Implementation (Linear O(N) Scan) ---")
    t0 = time.perf_counter()
    old_hits = 0
    old_comparisons = 0

    for sym in symbols:
        clean_sym = sym.strip().upper()
        vars_list = [
            clean_sym, clean_sym.replace("&", "_"), clean_sym.replace("-", "_"),
            clean_sym.replace("&", "AND"), clean_sym.replace("_", "&"),
            clean_sym.replace("_", "-"), clean_sym.replace(".NS", ""), clean_sym.replace(".BO", "")
        ]
        for v in vars_list:
            norm_v = normalize_id(v)
            found = False
            for k, val in cache_data.items():
                old_comparisons += 1
                if normalize_id(k) == norm_v:
                    old_hits += 1
                    found = True
                    break
            if found:
                break

    old_dur = time.perf_counter() - t0
    print(f"OLD Duration        : {old_dur:.4f} seconds")
    print(f"OLD Total Comparisons: {old_comparisons:,}")
    print(f"OLD Hits            : {old_hits}")

    # 2. NEW IMPLEMENTATION (Memoized O(1) Index)
    print("\n--- Running NEW Implementation (Memoized O(1) Index) ---")
    t0 = time.perf_counter()
    
    t_idx0 = time.perf_counter()
    idx = {}
    for k, v in cache_data.items():
        if v and isinstance(v, dict):
            norm_k = normalize_id(k)
            if norm_k:
                idx[norm_k] = v
    idx_build_dur = (time.perf_counter() - t_idx0) * 1000.0

    new_hits = 0
    new_comparisons = 0

    for sym in symbols:
        clean_sym = sym.strip().upper()
        vars_list = [
            clean_sym, clean_sym.replace("&", "_"), clean_sym.replace("-", "_"),
            clean_sym.replace("&", "AND"), clean_sym.replace("_", "&"),
            clean_sym.replace("_", "-"), clean_sym.replace(".NS", ""), clean_sym.replace(".BO", "")
        ]
        for v in vars_list:
            norm_v = normalize_id(v)
            new_comparisons += 1
            res = idx.get(norm_v)
            if res:
                new_hits += 1
                break

    new_dur = time.perf_counter() - t0
    print(f"NEW Index Build Time: {idx_build_dur:.2f} ms (Built ONCE per cache load)")
    print(f"NEW Total Duration  : {new_dur:.6f} seconds ({new_dur*1000:.2f} ms)")
    print(f"NEW Hash Lookups    : {new_comparisons:,}")
    print(f"NEW Hits            : {new_hits}")

    speedup = old_dur / new_dur if new_dur > 0 else 0
    print(f"\n🚀 EMPIRICAL SPEEDUP FACTOR: {speedup:,.1f}x FASTER!")

if __name__ == "__main__":
    benchmark()
