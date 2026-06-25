import pytest
import pandas as pd
from datetime import date
from app.wealth_engine import calculate_100_point_score, compute_tax_hold_bonus

def test_calculate_100_point_score():
    # Construct a fake fundamental row
    row = pd.Series({
        'ROCE': 25.0,
        'ROE': 20.0,
        'debt_to_equity': 0.1,
        'profit_growth_3y': 15.0,
        'sales_growth_3y': 12.0,
        'opm': 18.0,
        'fii_holding': 10.0,
        'dii_holding': 15.0
    })
    
    score = calculate_100_point_score(row)
    assert isinstance(score, int)
    assert 0 <= score <= 100

def test_compute_tax_hold_bonus():
    # Long term (> 1 yr)
    entry = date(2025, 1, 1)
    # Mocking today as a fixed date by passing the unrealized PnL
    
    # Very high PnL, no tax drag => bonus should be 0
    res = compute_tax_hold_bonus(entry_date=entry, unrealized_pnl_pct=150.0)
    assert isinstance(res, dict)
    
    # Minor profit, high tax drag
    res = compute_tax_hold_bonus(entry_date=date.today(), unrealized_pnl_pct=5.0)
    assert "reason" in res
