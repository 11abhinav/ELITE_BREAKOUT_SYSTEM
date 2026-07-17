import pytest
import pandas as pd
import json
import os
from unittest.mock import MagicMock

def test_fundamentals_cache_bse_fallback(mocker):
    # Setup imports inside the test to follow workspace rules
    import bse_mapping_utils
    from fundamentals_cache import fetch_single_piotroski, load_cache
    
    # Mock DB storage for mappings
    fake_mappings = {}
    
    def fake_load():
        return fake_mappings
        
    def fake_save(orig, mapped):
        fake_mappings[orig.strip().upper()] = mapped.strip().upper()
        
    mocker.patch("bse_mapping_utils.load_bse_mappings", side_effect=fake_load)
    mocker.patch("bse_mapping_utils.save_bse_mapping", side_effect=fake_save)
    bse_mapping_utils._bse_mappings_cache = fake_mappings
    
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
    
    # Fetch fundamentals for NSDL (NSE symbol)
    res = fetch_single_piotroski("NSDL")
    
    assert res is not None
    assert res.get("score") is not None
    assert res.get("failed") is not True
    
    # Check that BSE mapping was NOT saved for the standard NSE symbol
    mappings = bse_mapping_utils.load_bse_mappings()
    assert mappings.get("NSDL") is None
    
    # Fetch fundamentals for a numeric BSE symbol (500180) to verify it gets mapped
    # Mock yf.Ticker to return empty financials for 500180.NS but valid for 500180.BO
    def mock_ticker_init_bse(sym):
        if sym.endswith(".NS"):
            return mock_ticker_ns
        else:
            return mock_ticker_bo
            
    mocker.patch("yfinance.Ticker", side_effect=mock_ticker_init_bse)
    
    res_bse = fetch_single_piotroski("500180")
    assert res_bse is not None
    assert res_bse.get("score") is not None
    
    # Check that BSE mapping WAS recorded for the numeric BSE symbol
    mappings = bse_mapping_utils.load_bse_mappings()
    assert mappings.get("500180") == "500180.BO"
    
    bse_mapping_utils._bse_mappings_cache = None
