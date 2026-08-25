import sys, os
sys.path.insert(0, 'app')
from multibagger import load_cache
cache = load_cache()
for sym in ["POLYCAB", "MAHSEAMLES", "NAM-INDIA"]:
    fund = cache.get(sym)
    if fund:
        print(f"--- {sym} ---")
        print(f"total_equity: {fund.get('total_equity')}")
        print(f"market_cap: {fund.get('market_cap')}")
        print(f"data_freshness: {fund.get('data_freshness')}")
    else:
        print(f"{sym} not found in cache")
