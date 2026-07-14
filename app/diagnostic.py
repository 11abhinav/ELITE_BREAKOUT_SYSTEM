from data_providers.fyers_fetcher import _fyers_circuit_breaker
from data_provider import _price_provider
import time

print(f"Fyers Circuit Breaker Open: {_fyers_circuit_breaker.is_open}")
print(f"YFinance Cooldown Until: {_price_provider.cooldown_until} (Current time: {time.time()})")
if _price_provider.cooldown_until > time.time():
    print(f"YFinance Circuit Breaker is OPEN for another {int(_price_provider.cooldown_until - time.time())} seconds!")
else:
    print("YFinance Circuit Breaker is CLOSED")
