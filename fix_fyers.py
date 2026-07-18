import sys

with open("app/data_providers/fyers_fetcher.py", "r") as f:
    content = f.read()

# Replace return pd.DataFrame... with MarketData
content = content.replace(
    'return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])',
    'return MarketData(None, "Fyers", None, False, False, "No data available in response")'
)
content = content.replace(
    'return pd.DataFrame(columns=["Datetime", "Open", "High", "Low", "Close", "Volume"])',
    'return MarketData(None, "Fyers", None, False, False, "No data available in response")'
)

# Fix the get_batch_ohlcv return type signature
content = content.replace(
    'def get_batch_ohlcv(self, symbols: list[str], interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None, caller: str = None) -> dict[str, pd.DataFrame]:',
    'def get_batch_ohlcv(self, symbols: list[str], interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None, caller: str = None) -> dict[str, MarketData]:'
)

with open("app/data_providers/fyers_fetcher.py", "w") as f:
    f.write(content)

print("Fixed fyers_fetcher.py")
