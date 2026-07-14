import sys
import logging
sys.path.append("app")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

from data_providers.fyers_fetcher import FyersFetcher, _fyers_circuit_breaker
import time

print(f"Initial Circuit Breaker State: is_open={_fyers_circuit_breaker.is_open}, count={_fyers_circuit_breaker.failure_count}")

fetcher = FyersFetcher()
symbols = ["RELIANCE", "TCS"]
print(f"Testing Fyers API directly for {symbols}...")
try:
    res = fetcher.get_batch_ohlcv(symbols, "1d", "1y", caller="test")
    if not res:
        print("Fyers returned empty/None result.")
    else:
        for k, v in res.items():
            print(f"{k}: {'None' if v is None else str(len(v)) + ' rows'}")
except Exception as e:
    print(f"Exception during fetch: {e}")

print(f"Final Circuit Breaker State: is_open={_fyers_circuit_breaker.is_open}, count={_fyers_circuit_breaker.failure_count}")
