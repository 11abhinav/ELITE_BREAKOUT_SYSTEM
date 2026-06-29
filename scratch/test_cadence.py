import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app')))
from market_utils import is_market_open
from price_cache import get_dynamic_cadence

print("Market open:", is_market_open())
print("Cadence 1d:", get_dynamic_cadence("1d"))
