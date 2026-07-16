import os
import sys
import pandas as pd

# Add app to path
sys.path.insert(0, "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app")
from dotenv import load_dotenv
env_file = "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/.env"
if os.path.exists(env_file):
    with open(env_file) as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ[k] = v.strip("'\"")

from data_provider import DataFetcher
fetcher = DataFetcher()
df = pd.DataFrame({"Stock": ["LLOYDSME"]})
res = fetcher.fetch_watchlist_data(df, interval="5m", period="5d", requester="test")
hist = res.get("LLOYDSME")

if hist is not None:
    print(hist.to_string())
else:
    print("hist is None")
