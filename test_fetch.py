import sys, os
sys.path.insert(0, 'app')
from multibagger import fetch_ticker_fundamentals
for sym in ["POLYCAB", "MAHSEAMLES", "NAM-INDIA"]:
    print(f"\nFetching {sym}...")
    try:
        fund = fetch_ticker_fundamentals(sym)
        if fund:
            print(f"total_equity: {fund.get('total_equity')}")
            print(f"market_cap: {fund.get('market_cap')}")
            print(f"data_freshness: {fund.get('data_freshness')}")
        else:
            print("Returned None")
    except Exception as e:
        print(f"Error: {e}")
