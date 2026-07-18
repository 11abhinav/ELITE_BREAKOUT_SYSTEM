import sys
import os
sys.path.insert(0, os.path.abspath('app'))
from data_provider import get_fetcher
from price_cache import _download_all_robust

fetcher = get_fetcher()

print("Testing get_ohlcv...")
md = fetcher.get_ohlcv("RELIANCE", "1d", "1y")
print(f"Result for RELIANCE: valid={md.quality_report.is_valid if md.quality_report else False}, rows={len(md.dataframe) if md.dataframe is not None else 0}")

print("Testing get_batch_ohlcv...")
res = fetcher.get_batch_ohlcv(["TCS", "BPCL"], "1d", "1y")
for sym, md in res.items():
    print(f"Result for {sym}: valid={md.quality_report.is_valid if md.quality_report else False}, rows={len(md.dataframe) if md.dataframe is not None else 0}")

