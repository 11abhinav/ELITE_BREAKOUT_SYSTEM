from app.multibagger import get_cached_fundamentals, fetch_ticker_fundamentals
from app.core_score_engine import generate_core_scores, PeerMetrics, CorePriceData
import json

cache = json.load(open('data/fundamentals_cache.json'))
cached = get_cached_fundamentals('MARUTI', cache)
f = cached if cached else fetch_ticker_fundamentals('MARUTI')

price_data = CorePriceData(
    price=100.0, sma_50=90.0, sma_200=80.0, high_52w=110.0, high_20d=105.0, 
    latest_volume=1000, volume_sma20=500, rs_nifty=0.1, rs_sector=0.05
)

try:
    real_scores = generate_core_scores(f, PeerMetrics(), price_data)
    print("BQS:", real_scores.business_quality_score)
    print("RVS:", real_scores.relative_valuation_score)
    print("TREND:", real_scores.market_structure_score)
    print("TOTAL:", real_scores.composite_investment_score)
except Exception as e:
    import traceback
    traceback.print_exc()
