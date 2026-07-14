import sys
import logging
sys.path.append("app")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

from wealth_engine import get_wealth_engine_candidates
from price_cache import fetch_unified_historical
import time

print("Fetching candidates...")
df, _ = get_wealth_engine_candidates()
symbols = list(df['Symbol'].unique())
print(f"Got {len(symbols)} symbols. Example: {symbols[:5]}")

print("Calling fetch_unified_historical...")
start = time.time()
res = fetch_unified_historical(symbols, period="1y", interval="1d", requester="test_we_full")
print(f"Finished in {time.time()-start:.2f}s")
fetched = sum(1 for v in res.values() if v is not None and not v.empty)
print(f"Fetched {fetched}/{len(symbols)}")
