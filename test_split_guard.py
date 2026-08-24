import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

import pandas as pd
from unittest.mock import patch, MagicMock
from wealth_engine import evaluate_open_positions

def mock_yfinance_ticker(splits_dict=None):
    if splits_dict is None:
        splits_dict = {}
    
    mock_splits = pd.Series(splits_dict)
    if not mock_splits.empty:
        mock_splits.index = pd.to_datetime(mock_splits.index)
        
    mock_ticker = MagicMock()
    mock_ticker.splits = mock_splits
    return mock_ticker

def run_scenario(name, r_dict, is_test_mode=True, splits_dict=None):
    r = pd.Series(r_dict)
    df = pd.DataFrame([r])
    
    # We need to ensure has_genuine_prev_close logic evaluates correctly inside evaluate_open_positions
    # evaluate_open_positions expects certain columns in the dataframe for _generate_exit_signal
    
    with patch('yfinance.Ticker', return_value=mock_yfinance_ticker(splits_dict)):
        result_df = evaluate_open_positions(df, portfolio_dict={})
        result_code = result_df.iloc[0].get('Exit_Code')
        
    print(f"Scenario: {name:<25} | Expected: {r.get('expected')} | Actual: {result_code}")
    assert result_code == r.get('expected'), f"Failed {name}: got {result_code}"

def test_all():
    print("Running Wealth Engine Exit Logic Tests...\n")
    
    # Common base row to satisfy is_live_valid and has_genuine_prev_close
    # For a drawdown of >=20%, is_live_valid must be True, which means:
    # not used_fallback_data, data_quality != STALE, abs(cmp-prev_close)/prev_close <= 0.50
    # BUT wait, evaluate_open_positions has its own logic for is_live_valid which uses these keys.
    base = {
        "used_fallback_data": False,
        "data_quality": "LIVE",
        "macro_regime": "BULL",
        "FM_Score": 90.0,
        "RS_Rating": 90.0
    }
    
    # 1. Normal -21% move (SELL)
    r1 = base.copy(); r1.update({"Stock": "NORMAL21", "entry_price": 100.0, "cmp": 79.0, "prev_close": 85.0, "entry_date": "2026-08-01", "expected": "SELL"})
    run_scenario("Normal -21% move", r1)
    
    # 2. Normal -30% move (SELL)
    r2 = base.copy(); r2.update({"Stock": "NORMAL30", "entry_price": 100.0, "cmp": 70.0, "prev_close": 82.0, "entry_date": "2026-08-01", "expected": "SELL"})
    run_scenario("Normal -30% move", r2)
    
    # 3. ~50% split move (SPLIT_ADJUSTED, no SELL)
    r3 = base.copy(); r3.update({"Stock": "SPLIT50", "entry_price": 1500.0, "cmp": 750.0, "prev_close": 750.0, "entry_date": "2026-08-01", "expected": "SPLIT_ADJUSTED"})
    run_scenario("~50% split move", r3, splits_dict={"2026-08-10": 2.0})
    
    # 4. 1:3 split (SPLIT_ADJUSTED, no SELL)
    r4 = base.copy(); r4.update({"Stock": "SPLIT3", "entry_price": 3000.0, "cmp": 1000.0, "prev_close": 1000.0, "entry_date": "2026-08-01", "expected": "SPLIT_ADJUSTED"})
    run_scenario("1:3 split", r4, splits_dict={"2026-08-15": 3.0})
    
    # 5. Missing prev_close (DATA_STALE, no SELL)
    r5 = base.copy(); r5.update({"Stock": "MISSPREV", "entry_price": 100.0, "cmp": 90.0, "prev_close": None, "entry_date": "2026-08-01", "expected": "DATA_STALE"})
    run_scenario("Missing prev_close", r5)
    
    # 6. Invalid CMP (DATA_STALE, no SELL)
    r6 = base.copy(); r6.update({"Stock": "INVCMP", "entry_price": 100.0, "cmp": -5.0, "prev_close": 100.0, "entry_date": "2026-08-01", "expected": "DATA_STALE"})
    run_scenario("Invalid CMP", r6)
    
    print("\n✅ All scenarios passed successfully!")

if __name__ == "__main__":
    test_all()
