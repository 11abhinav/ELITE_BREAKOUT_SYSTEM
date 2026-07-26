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
    
    # Fetch fundamentals for NSDL (NSE symbol that falls back to BSE)
    res = fetch_single_piotroski("NSDL")
    
    assert res is not None
    assert res.get("score") is not None
    assert res.get("failed") is not True
    
    # RCA FIX (2026-07-17): BSE mapping SHOULD be saved for ALL symbols (not just
    # numeric ones) when a fallback to .BO succeeds. This prevents redundant Yahoo
    # API hits and rate-limit consumption on every future fundamental sweep.
    # The OLD assertion "mappings.get('NSDL') is None" was wrong — it validated the
    # broken isdigit() restriction that we intentionally removed.
    mappings = bse_mapping_utils.load_bse_mappings()
    assert mappings.get("NSDL") == "NSDL.BO", (
        "BSE fallback mapping should be saved for ALL symbols (alphabetical included) "
        "to prevent repeated API hits on future fundamental sweeps."
    )
    
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
    
    # Check that BSE mapping WAS recorded for the numeric BSE symbol too
    mappings = bse_mapping_utils.load_bse_mappings()
    assert mappings.get("500180") == "500180.BO"
    
    bse_mapping_utils._bse_mappings_cache = None


def test_compute_piotroski_multi_row_duplicate_indexing():
    """Verify compute_piotroski handles duplicate row index names without raising Pandas Series ambiguity exceptions."""
    from fundamentals_cache import compute_piotroski

    # Create a DataFrame with duplicate index rows (e.g. from concats or yfinance duplicates)
    fin_df = pd.DataFrame(
        [[100, 80], [100, 80]],
        columns=["2025", "2024"],
        index=["Net Income", "Net Income"]  # Duplicate index!
    )
    bs_df = pd.DataFrame(
        [[500, 450], [500, 450]],
        columns=["2025", "2024"],
        index=["Total Assets", "Total Assets"]  # Duplicate index!
    )

    info = {"operatingCashflow": 150, "grossMargins": 0.45, "prevGrossMargins": 0.40, "currentRatio": 1.5, "previousCurrentRatio": 1.4}

    score = compute_piotroski(info, fin_df, balance_sheet=bs_df)
    assert score >= 0, f"Expected non-negative Piotroski score, got {score}"
    assert score >= 4, f"Expected valid positive Piotroski score, got {score}"


def test_compute_piotroski_separate_fin_bs_dataframes():
    """Verify passing fin and bs DataFrames separately computes valid Piotroski score even when date columns differ."""
    from fundamentals_cache import compute_piotroski

    fin = pd.DataFrame(
        [[200, 150], [1000, 800]],
        columns=["2025-03-31", "2024-03-31"],
        index=["Net Income", "Total Revenue"]
    )
    bs = pd.DataFrame(
        [[1200, 1000], [50, 60], [100, 100]],
        columns=["2025-03-31", "2024-03-31"],
        index=["Total Assets", "Long Term Debt", "Ordinary Shares Number"]
    )

    info = {
        "operatingCashflow": 250,
        "grossMargins": 0.50,
        "prevGrossMargins": 0.45,
        "currentRatio": 2.0,
        "previousCurrentRatio": 1.8
    }

    score = compute_piotroski(info, fin, balance_sheet=bs)
    assert score >= 8, f"Expected Piotroski score >= 8, got {score}"

