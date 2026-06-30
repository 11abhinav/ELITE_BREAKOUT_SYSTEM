import json
import logging
from multibagger import get_cached_fundamentals
from core_score_engine import PeerMetrics, generate_core_scores, CorePriceData

def debug_scores():
    with open('data/fundamentals_cache.json', 'r') as f:
        cache = json.load(f)
        
    sym = 'RELIANCE'
    f_data = get_cached_fundamentals(sym, cache)
    print(f"Fundamentals for {sym}:", f_data)
    
    price_data = CorePriceData(
        price=100.0, sma_50=90.0, sma_200=80.0, high_52w=110.0, high_20d=105.0, 
        latest_volume=1000, volume_sma20=500, rs_nifty=0.1, rs_sector=0.05
    )
    
    real_scores = generate_core_scores(f_data, PeerMetrics(), price_data)
    print("BQS:", real_scores.business_quality_score)
    print("RVS:", real_scores.relative_valuation_score)
    print("TREND:", real_scores.market_structure_score)
    print("TOTAL:", real_scores.composite_investment_score)
    print("WARNINGS:", real_scores.warnings)

if __name__ == '__main__':
    debug_scores()
