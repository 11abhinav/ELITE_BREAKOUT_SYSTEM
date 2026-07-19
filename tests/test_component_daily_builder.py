import pytest
import pandas as pd
from app.daily_builder import classify_stock

def test_daily_builder_happy_path():
    """
    Test that a strong growth non-financial stock gets the 'High Growth' category.
    """
    # A perfect high growth stock: high yoy revenue, high yoy profit, high qoq, high ROE
    row = pd.Series({
        "name": "TEST",
        "sector": "Information Technology",
        "close": 100.0,
        "average_volume_30d_calc": 2000000,
        "market_cap_basic": 10000000000.0,
        "total_revenue_yoy_growth_ttm": 45.0,
        "earnings_per_share_diluted_yoy_growth_ttm": 55.0,
        "total_revenue_qoq_growth_fq": 20.0,
        "earnings_per_share_diluted_qoq_growth_fq": 25.0,
        "return_on_equity_fy": 30.0,
        "operating_margin": 25.0,
        "debt_to_equity_fq": 0.1,
    })
    
    result = classify_stock(row)
    
    assert result is not None
    assert "Stock" in result
    assert result["Stock"] == "TEST"
    assert "Wealth Compounder" in result.get("Category", "")
    assert result.get("Fundamental Score", 0) > 80

def test_daily_builder_boundary():
    """
    Test that a stock just barely missing the criteria gets a different or no category.
    """
    # Just below High Growth thresholds (e.g. yoy revenue < 20)
    row = pd.Series({
        "name": "TEST",
        "sector": "Information Technology",
        "close": 100.0,
        "average_volume_30d_calc": 2000000,
        "market_cap_basic": 10000000000.0,
        "total_revenue_yoy_growth_ttm": 19.9,
        "earnings_per_share_diluted_yoy_growth_ttm": 19.9,
        "total_revenue_qoq_growth_fq": 10.0,
        "earnings_per_share_diluted_qoq_growth_fq": 10.0,
        "return_on_equity_fy": 14.9,
        "operating_margin": 25.0,
        "debt_to_equity_fq": 0.1,
    })
    
    result = classify_stock(row)
    
    assert result is not None
    assert "Wealth Compounder" not in result.get("Category", "")
    # Depending on rules, it might be Stable Compounder if 5y growth is good, but we didn't provide 5y.
    
def test_daily_builder_failure():
    """
    Test that an exception during classification (e.g., missing critical fields causing TypeError)
    is caught and returns None (or handles it gracefully).
    """
    # This row has no data and might cause failure if not handled properly
    row = pd.Series({
        "name": None,
    })
    
    # We expect classify_stock to gracefully return None or "UNKNOWN.NS" with no cats,
    # because it has a try/except block.
    result = classify_stock(row)
    
    if result is not None:
        assert result.get("Category", "") == ""
    else:
        assert result is None
