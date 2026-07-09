import pytest
import pandas as pd
from unittest.mock import patch, MagicMock

# Make sure to import the module after setting up paths or directly from app
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app')))

from app.data_provider import YFinanceFetcher

def test_normalize_symbol():
    fetcher = YFinanceFetcher()
    # Should append .NS if not present
    assert fetcher._normalize_symbol("FIVESTAR") == "FIVESTAR.NS"
    # Should not append .NS if already present
    assert fetcher._normalize_symbol("FIVESTAR.NS") == "FIVESTAR.NS"
    # Should convert special ampersand names correctly
    assert fetcher._normalize_symbol("M_M") == "M&M.NS"
    
    # Should convert general underscores to hyphens and append .NS
    assert fetcher._normalize_symbol("NAM_INDIA") == "NAM-INDIA.NS"
    # Should ignore indices
    assert fetcher._normalize_symbol("^NSEI") == "^NSEI"

@patch('app.data_provider._price_provider.fetch_batch')
def test_get_batch_ohlcv_duplicate_mapping(mock_fetch_batch):
    """
    Test that when get_batch_ohlcv is called with multiple symbols that normalize 
    to the same symbol (e.g., 'FIVESTAR' and 'FIVESTAR.NS'), the output contains 
    the data for ALL requested original symbols.
    """
    fetcher = YFinanceFetcher()
    
    # Create a dummy dataframe
    dummy_df = pd.DataFrame({'Close': [100, 105], 'Open': [99, 100]})
    
    # Mock fetch_batch to return the dummy dataframe for 'FIVESTAR.NS'
    mock_fetch_batch.return_value = {
        'FIVESTAR.NS': dummy_df
    }
    
    # Call with both the raw symbol and the .NS symbol
    symbols = ['FIVESTAR', 'FIVESTAR.NS', 'RELIANCE']
    
    result = fetcher.get_batch_ohlcv(symbols, interval='1d', period='2d')
    
    # Verify that mock was called with correct deduplicated symbols
    # 'RELIANCE' will be normalized to 'RELIANCE.NS'
    args, kwargs = mock_fetch_batch.call_args
    ns_symbols_called = args[0]
    assert set(ns_symbols_called) == {'FIVESTAR.NS', 'RELIANCE.NS'}
    
    # Ensure BOTH original 'FIVESTAR' and 'FIVESTAR.NS' are present in the output
    assert 'FIVESTAR' in result
    assert 'FIVESTAR.NS' in result
    
    # Ensure the dataframe was properly mapped to both
    assert result['FIVESTAR'] is not None
    assert result['FIVESTAR.NS'] is not None
    assert len(result['FIVESTAR']) == 2
    assert result['FIVESTAR'].iloc[-1]['Close'] == 105
    
    # RELIANCE should be None or not present since the mock didn't return it,
    # but the method should set it to None gracefully based on the dict get
    # Actually fetch_batch doesn't return RELIANCE.NS, so fetched.get returns None
    assert 'RELIANCE' not in result or result['RELIANCE'] is None

@patch('app.data_provider._price_provider.fetch_batch')
def test_get_batch_ohlcv_preserves_single_symbol(mock_fetch_batch):
    """
    Test that calling with a single symbol correctly maps back to the original symbol.
    """
    fetcher = YFinanceFetcher()
    dummy_df = pd.DataFrame({'Close': [100]})
    mock_fetch_batch.return_value = {'HDFCBANK.NS': dummy_df}
    
    result = fetcher.get_batch_ohlcv(['HDFCBANK'], interval='1d', period='1d')
    
    assert 'HDFCBANK' in result
    assert result['HDFCBANK'] is not None
    assert 'HDFCBANK.NS' not in result
