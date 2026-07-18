import sys
with open("app/data_providers/fyers_fetcher.py", "r") as f:
    content = f.read()
if "from data_quality import" not in content:
    content = content.replace("from data_provider import DataFetcher", "from data_provider import DataFetcher\nfrom data_quality import DataQualityValidator, MarketData\n")
    with open("app/data_providers/fyers_fetcher.py", "w") as f:
        f.write(content)
    print("Fixed FyersFetcher imports")
