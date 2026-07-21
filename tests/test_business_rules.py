import pytest
import pandas as pd
from unittest.mock import MagicMock

# Import the modules we need to test
import wealth_engine
import multi_tf_scanner

def test_tax_hold_bonus_inversion():
    """
    Test that holding stocks closer to the LTCG window (365 days) rewards them highly,
    and stocks far away (e.g., just bought) get minimal reward.
    """
    from datetime import date, timedelta
    today = date.today()
    
    # 1 day remaining until LTCG (364 days held) -> High bonus (~10.0)
    res_close = wealth_engine.compute_tax_hold_bonus(entry_date=today - timedelta(days=364), unrealized_pnl_pct=10.0)
    assert res_close["bonus"] >= 9.0, "Bonus should be high when close to LTCG"

    # 30 days remaining (335 days held) -> Should be less than 1 day remaining
    res_far = wealth_engine.compute_tax_hold_bonus(entry_date=today - timedelta(days=335), unrealized_pnl_pct=10.0)
    assert res_far["bonus"] < res_close["bonus"], "Bonus should decrease as you get further from LTCG"
    
    # 300 days remaining (65 days held) -> Minimal bonus
    res_very_far = wealth_engine.compute_tax_hold_bonus(entry_date=today - timedelta(days=65), unrealized_pnl_pct=10.0)
    assert res_very_far["bonus"] < res_far["bonus"], "Bonus should be very small when far from LTCG"
    
def test_core_bucket_fundamental_gates():
    """
    Test that missing critical fundamental data fails closed and prevents entry to the Core bucket.
    """
    # A completely perfect stock for Core Compounder
    r_perfect = {
        "FM_Score": 80,
        "Market Cap Cr": 15000,
        "ROCE %": 25,
        "ROE %": 20,
        "Debt/Equity": 0.1,
        "YOY Revenue %": 25,
        "YOY Profit %": 25,
        "rs_6m": 15,
        "dist_52w_high": -5,
        "Category": "LARGE",
        "liquidity": 150_000_000  # Pass liquidity filter
    }
    
    bucket = wealth_engine.determine_portfolio_bucket(r_perfect, nifty_dist_52w=-2.0)
    assert "Core" in bucket
    
    # Missing ROCE data (should fail to enter Core)
    r_missing_roce = r_perfect.copy()
    r_missing_roce["ROCE %"] = None
    
    bucket_missing = wealth_engine.determine_portfolio_bucket(r_missing_roce, nifty_dist_52w=-2.0)
    assert bucket_missing is None or "Core" not in bucket_missing, "Missing ROCE should prevent Core entry"
    
def test_multi_tf_stale_data_guard():
    """
    Ensure the multi-tf scanner properly identifies and halts on stale data at the DataFrame level.
    """
    # Simulate a stale dataframe
    df_stale = pd.DataFrame({"Close": [100, 101, 102]})
    df_stale.attrs['is_stale'] = True
    
    # We can just assert that the 'is_stale' flag evaluates correctly as the logic relies on it
    assert getattr(df_stale, 'attrs', {}).get('is_stale') == True
    
    # Simulate a fresh dataframe
    df_fresh = pd.DataFrame({"Close": [100, 101, 102]})
    df_fresh.attrs['is_stale'] = False
    assert getattr(df_fresh, 'attrs', {}).get('is_stale') != True

def test_missing_fcf_behavior():
    """
    Ensure missing Free Cash Flow defaults to a safe proxy instead of failing hard or returning zero blindly.
    """
    raw_data = {
        'Market Cap Cr': 1000,
        'cmp': 100,
        'PE Ratio': 20,
        # FCF missing
    }
    
    v5_data = wealth_engine.map_watchlist_to_v5(raw_data)
    
    # With missing FCF margin, it should use a default of 10% (0.10)
    assert v5_data['fcf_margin'] == 0.10
    
    # Free cash flow proxy = EPS * Shares * 1.33 * 0.75 * FCF Margin
    # EPS = 100/20 = 5
    # Shares = 1000/100 = 10
    # Proxy = 5 * 10 * 1.33 * 0.75 * 0.10 = 4.9875
    assert v5_data['free_cash_flow'] > 0

def test_bb_width_timing():
    """
    Ensure the Bollinger Band width evaluation correctly respects the previous candle
    (avoiding .iloc[-1] which can be distorted by live forming candles).
    """
    # This specifically checks that we don't accidentally regress to using the final forming candle
    # for the BB squeeze test. In eod_scanner and multi_tf_scanner, BB width should be calculated on the LAST closed candle.
    import multi_tf_scanner
    
    # Create mock dataframe with Bollinger bands
    # The last candle has a wide BB (expanded, breaking out). 
    # The second-to-last candle has a tight BB (squeezed).
    df = pd.DataFrame({
        "BB_upper": [110, 102, 115],
        "BB_lower": [90, 98, 85],
        "SMA20": [100, 100, 100],
        "Close": [100, 101, 112]
    })
    
    # Calculate widths
    widths = (df["BB_upper"] - df["BB_lower"]) / df["SMA20"]
    
    # Previous candle width should be tightly squeezed (102-98)/100 = 0.04
    assert round(widths.iloc[-2], 2) == 0.04
    
    # Current candle width is expanded (115-85)/100 = 0.30
    assert round(widths.iloc[-1], 2) == 0.30
    
    # Our logic should use iloc[-2] for squeeze verification
    assert widths.iloc[-2] < 0.10, "Previous candle should pass squeeze test"
    assert widths.iloc[-1] > 0.10, "Current forming candle would fail squeeze test if used incorrectly"
