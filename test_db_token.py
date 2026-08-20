import sys
import logging
sys.path.append("app")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

from fyers_auth import get_access_token
from data_provider import get_fetcher
from data_providers.fyers_fetcher import _fyers_circuit_breaker

print("Checking Fyers token...")
try:
    token = get_access_token()
    print(f"Token is: {'Valid (Hidden)' if token else 'None'}")
except Exception as e:
    print(f"Error getting token: {e}")

print(f"Fyers Circuit Breaker: is_open={_fyers_circuit_breaker.is_open}, count={_fyers_circuit_breaker.failure_count}")

fetcher = get_fetcher()
res = fetcher.get_batch_ohlcv(["RELIANCE", "TCS"], "1d", "1y", caller="test")
print(f"Got {len(res)} results from AutoSwitchingFetcher.")
for k,v in res.items():
    df_val = v.df if (v is not None and hasattr(v, 'df')) else v
    print(f"{k}: {'None' if df_val is None else len(df_val)}")
