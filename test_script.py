from unittest.mock import patch
import pandas as pd
from datetime import datetime
from stock_analyzer import analyze_symbol

dates = pd.date_range(end=datetime.now().strftime("%Y-%m-%d"), periods=60, freq='B')
prices = [50.0 + i * 0.5 for i in range(60)]
df = pd.DataFrame({
    "Open": [p - 0.5 for p in prices],
    "High": [p + 1.0 for p in prices],
    "Low": [p - 1.0 for p in prices],
    "Close": prices,
    "Volume": [300000] * 60
}, index=dates)

with patch('stock_analyzer.validate_nse_bse_ticker', return_value={"is_valid": True, "symbol": "PENNYSTOCK"}), \
     patch('stock_analyzer.fetch_watchlist_data') as mock_fetch, \
     patch('stock_analyzer.compute_nifty_rs_rating') as mock_rs, \
     patch('stock_analyzer.get_fundamentals') as mock_fund:
    mock_fetch.return_value = {"PENNYSTOCK": df}
    mock_rs.return_value = {"PENNYSTOCK": 40.0}
    mock_fund.return_value = {"company_name": "Penny Stock Ltd", "sector": "SMALL"}
    res = analyze_symbol("PENNYSTOCK")
    print(res.get("funnel", {}).get("daily_builder", {}))
