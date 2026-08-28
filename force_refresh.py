import sys
import os
import logging
logging.basicConfig(level=logging.INFO)

sys.path.append(os.path.join(os.path.dirname(__file__), "app"))
import pandas as pd
from app.fundamentals_cache import refresh_fundamentals_tiered
from app.valuation_utils import fetch_full_universe_for_valuation

if __name__ == "__main__":
    df = fetch_full_universe_for_valuation()
    refresh_fundamentals_tiered(df)
