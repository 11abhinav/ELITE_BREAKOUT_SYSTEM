import json
from multibagger import get_cached_fundamentals
from core_score_engine import CoreFundamentals

cache = json.load(open('../data/fundamentals_cache.json'))
data = cache['RELIANCE']
print(data.keys())
try:
    f = CoreFundamentals(**{k: v for k, v in data.items() if k != "fetched_at"})
    print("SUCCESS")
except Exception as e:
    import traceback
    traceback.print_exc()
