import sys
import os
import json
import logging
from pprint import pprint

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../app')))

from core.multibagger_pipeline import run_pipeline_for_symbol
from multibagger import fetch_ticker_fundamentals, batch_download_market_data

logging.basicConfig(level=logging.INFO)

SYMBOLS = [
    "TCS", "ASIANPAINT", "TITAN", # Compounders
    "HDFCBANK", "ICICIBANK", "BAJFINANCE", # Financials
    "TATASTEEL", "JSWSTEEL", "SIEMENS", "ABB", # Cyclicals / Capital Goods
    "SUNPHARMA", "NESTLEIND", # Pharma / FMCG
    "YESBANK", "IDEA" # Poor quality / Turnaround
]

def main():
    print("--- 🚀 V5 GOLDEN DATASET VALIDATION ---")
    
    # 1. Fetch Fundamentals
    print(f"Fetching fundamentals for {len(SYMBOLS)} symbols...")
    fundamentals = {}
    for sym in SYMBOLS:
        print(f"  -> {sym}")
        fund = fetch_ticker_fundamentals(sym)
        if fund:
            fundamentals[sym] = fund
            
    # 2. Fetch Technicals
    print("\nFetching technicals...")
    valid_symbols = list(fundamentals.keys())
    technicals_map = batch_download_market_data(valid_symbols)
    
    results = {}
    
    for sym in valid_symbols:
        fund = fundamentals[sym]
        tech = technicals_map.get(sym)
        if not tech:
            continue
            
        # Convert StockPriceData to dictionary format expected by pipeline
        tech_dict = {
            "price": tech.price,
            "sma_50": tech.sma_50,
            "sma_200": tech.sma_200,
            "rs_rating": tech.mom_3m * 100, # crude proxy
            "relative_volume_10d": tech.latest_volume / tech.volume_sma20 if tech.volume_sma20 > 0 else 1.0,
            "pct_from_52w_high": (tech.price - tech.high_52w) / tech.high_52w if tech.high_52w > 0 else 0.0,
            "ema_20": tech.ema_20,
            "atr": tech.atr_14
        }
        
        # 3. Run Pipeline
        decision = run_pipeline_for_symbol(sym, fund, tech_dict)
        results[sym] = decision
        
        print(f"\n[{sym}] - {decision.classification}")
        print(f"  Score: {decision.composite_score:.1f} | Raw: {decision.raw_composite_score:.1f} | Conf: {decision.confidence:.1f}%")
        print(f"  FV: {decision.valuation.fair_value:.1f} | BuyZone: {decision.buy_zone.buy_zone_low:.1f}-{decision.buy_zone.buy_zone_high:.1f}")
        if decision.is_invalidated:
            print(f"  🚨 INVALIDATED: {decision.invalidation_reason}")

if __name__ == "__main__":
    main()
