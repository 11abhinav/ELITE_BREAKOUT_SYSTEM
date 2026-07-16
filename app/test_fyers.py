import os
import sys
import pandas as pd

env_file = "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/.env"
if os.path.exists(env_file):
    with open(env_file) as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ[k] = v.strip("'\"")

sys.path.insert(0, "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app")

from data_providers.fyers_fetcher import FyersFetcher
from datetime import datetime, timedelta

fetcher = FyersFetcher()
# We don't have the token so it might fail, but let's try
hist = fetcher.get_ohlcv("LLOYDSME", "5m", "5d")
if hist is not None:
    print(hist.head())
    print(hist.tail())
else:
    print("Fyers fetch failed, as expected without valid token.")
