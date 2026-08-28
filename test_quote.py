import sys, os
sys.path.insert(0, os.path.abspath('app'))
from market_data.providers.upstox_provider import UpstoxProvider
p = UpstoxProvider()
res = p.get_quotes(["TATAMOTORS"])
print("Quotes for TATAMOTORS:", res)
