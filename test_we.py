import sys
import logging
sys.path.append("app")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

from price_cache import fetch_unified_historical
import time

print("Starting test...")
start = time.time()
res = fetch_unified_historical(["RELIANCE", "TCS"], period="1y", interval="1d")
print(f"Finished in {time.time()-start:.2f}s")
print(f"Keys in result: {list(res.keys())}")
for k, v in res.items():
    print(f"{k}: {'None' if v is None else str(len(v)) + ' rows'}")
