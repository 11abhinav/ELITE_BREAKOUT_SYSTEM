import pytest
import pandas as pd
import json
import os
from unittest.mock import MagicMock

def test_fundamentals_cache_bse_fallback(mocker):
    # Setup imports inside the test to follow workspace rules
    import bse_mapping_utils
    from fundamentals_cache import fetch_single_piotroski, load_cache
    
    # Use temporary file to avoid altering live mappings
    temp_mapping_file = "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/data/temp_bse_mappings.json"
    mocker.patch("bse_mapping_utils._BSE_MAPPING_FILE", temp_mapping_file)
    
    # Ensure starting clean
    if os.path.exists(temp_mapping_file):
        os.remove(temp_mapping_file)
    bse_mapping_utils._bse_mappings_cache = None
    
    # Mock Ticker returns empty financials for NSDL.NS (first check)
    # but valid financials for NSDL.BO (second check fallback)
    mock_ticker_ns = MagicMock()
    mock_ticker_ns.financials = pd.DataFrame()
    mock_ticker_ns.balance_sheet = pd.DataFrame()
    
    mock_ticker_bo = MagicMock()
    mock_ticker_bo.info = {"operatingCashflow": 1000, "grossMargins": 0.4}
    mock_ticker_bo.financials = pd.DataFrame([[100, 80]], columns=["2025", "2024"], index=["Net Income"])
    mock_ticker_bo.balance_sheet = pd.DataFrame([[500, 450]], columns=["2025", "2024"], index=["Total Assets"])
    
    # Mock yf.Ticker to return mock_ticker_ns for NSDL.NS and mock_ticker_bo for NSDL.BO
    def mock_ticker_init(sym):
        if sym.endswith(".NS"):
            return mock_ticker_ns
        else:
            return mock_ticker_bo
            
    mocker.patch("yfinance.Ticker", side_effect=mock_ticker_init)
    
    # Mock yf_acquire and yf_release to avoid locking / rate limits during test
    mocker.patch("fundamentals_cache.yf_acquire")
    mocker.patch("fundamentals_cache.yf_release")
    
    # Fetch fundamentals for NSDL
    res = fetch_single_piotroski("NSDL")
    
    assert res is not None
    assert res.get("score") is not None
    assert res.get("failed") is not True
    
    # Check that BSE mapping was recorded
    mappings = bse_mapping_utils.load_bse_mappings()
    assert mappings.get("NSDL") == "NSDL.BO"
    
    # Clean up temp file
    if os.path.exists(temp_mapping_file):
        os.remove(temp_mapping_file)
    bse_mapping_utils._bse_mappings_cache = None
