import sys
sys.path.append("app")
from data_provider import YFinanceFetcher
from datetime import datetime

dp = YFinanceFetcher()
symbols = ["RELIANCE", "TCS", "INFY", "HDFC", "SBI", "ITC"]
res = dp.get_batch_ohlcv(symbols, "1d", "60d", caller="test")
print(f"Returned {len(res)} symbols")
for k, v in res.items():
    df_val = v.df if (v is not None and hasattr(v, 'df')) else v
    print(f"{k}: {'None' if df_val is None else 'DataFrame with ' + str(len(df_val)) + ' rows'}")
