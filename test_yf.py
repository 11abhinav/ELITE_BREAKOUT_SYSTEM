import sys
sys.path.append("app")
from data_provider import DataProvider
from datetime import datetime

dp = DataProvider()
symbols = ["RELIANCE", "TCS", "INFY", "HDFC", "SBI", "ITC"]
res = dp.yfinance_fetcher.get_batch_ohlcv(symbols, "1d", "60d", caller="test")
print(f"Returned {len(res)} symbols")
for k, v in res.items():
    print(f"{k}: {'None' if v is None else 'DataFrame with ' + str(len(v)) + ' rows'}")
