import pytest
from unittest.mock import patch, MagicMock
import pandas as pd
import time
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../app')))

from macro_utils import (
    get_macro_regime,
    get_nifty_20d_return,
    get_nifty_6m_state,
    get_nifty_intraday_drop,
    _cache,
    MACRO_CACHE_TTL_SECONDS
)

def setup_function():
    _cache.daily_data = None
    _cache.daily_last_fetched = 0
    _cache.intraday_data = None
    _cache.intraday_last_fetched = 0

@patch('macro_utils._get_daily_nifty')
def test_get_macro_regime(mock_fetch):
    # Create mock dataframe with 250 rows to satisfy MarketRegimeEngine's len(df) >= 200 check
    dates = pd.date_range('2026-01-01', periods=250)
    data = {
        'Close': [100.0] * 249 + [110.0],
        'High': [100.0] * 249 + [110.0],
        'Low': [100.0] * 249 + [110.0],
    } # 10% return
    mock_df = pd.DataFrame(data, index=dates)
    mock_fetch.return_value = mock_df
    
    assert get_macro_regime() in ["BULL", "STRONG_BULL", "WEAK_BULL"]
    
    # Test BEAR
    data['Close'] = [100.0] * 249 + [90.0]
    data['Low'] = [100.0] * 249 + [90.0]
    mock_df = pd.DataFrame(data, index=dates)
    
    mock_fetch.return_value = mock_df
    assert get_macro_regime() in ["BEAR", "WEAK_BEAR", "STRONG_BEAR"]

@patch('macro_utils._get_daily_nifty')
def test_get_nifty_20d_return(mock_fetch):
    dates = pd.date_range('2026-01-01', periods=20)
    data = {'Close': [100.0] * 19 + [105.0]} # 5% return
    mock_df = pd.DataFrame(data, index=dates)
    mock_fetch.return_value = mock_df
    
    assert get_nifty_20d_return() == 5.0
    assert get_nifty_20d_return() == 5.0

@patch('macro_utils._get_daily_nifty')
def test_get_nifty_6m_state(mock_fetch):
    dates = pd.date_range('2026-01-01', periods=126)
    data = {'Close': [100.0] * 125 + [110.0], 'High': [100.0] * 125 + [110.0]} 
    mock_df = pd.DataFrame(data, index=dates)
    mock_fetch.return_value = mock_df
    
    ret_6m, dist_52w = get_nifty_6m_state()
    assert ret_6m == 10.0
    assert dist_52w == 0.0

@patch('macro_utils._get_intraday_nifty')
def test_get_nifty_intraday_drop(mock_fetch):
    from app.macro_utils import IST
    from datetime import datetime
    today = datetime.now(IST).strftime('%Y-%m-%d')
    dates = pd.date_range(f'{today} 09:15:00', periods=3, freq='15min')
    data = {'Open': [100.0, 99.0, 98.0], 'Close': [99.0, 98.0, 95.0]} # 5% drop from open
    mock_df = pd.DataFrame(data, index=dates)
    mock_fetch.return_value = mock_df
    
    drop = get_nifty_intraday_drop()
    assert drop == 5.0
