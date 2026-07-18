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
    # [VERSION: UNIT_TEST_CALL_ARGS_FIX_v1.0] Check first call's symbols list in call_args_list
    ns_symbols_called = mock_fetch_batch.call_args_list[0][0][0]
    assert set(ns_symbols_called) == {'FIVESTAR.NS', 'RELIANCE.NS'}
    
    # Ensure BOTH original 'FIVESTAR' and 'FIVESTAR.NS' are present in the output
    assert 'FIVESTAR' in result
    assert 'FIVESTAR.NS' in result
    
    # Ensure the dataframe was properly mapped to both
    assert result['FIVESTAR'] is not None
    assert result['FIVESTAR.NS'] is not None
    assert len(result['FIVESTAR']) == 2
    assert result['FIVESTAR'].iloc[-1]['Close'] == 105

    # RELIANCE should be ProviderResult.EMPTY_DATA or None based on the dict get
    from core_enums import ProviderResult
    assert 'RELIANCE' not in result or result['RELIANCE'] is None or result['RELIANCE'] == ProviderResult.EMPTY_DATA

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

def test_bse_persistent_mapping_fallback(mocker):
    from data_provider import YFinanceFetcher
    from bse_mapping_utils import load_bse_mappings
    import bse_mapping_utils
    import bse_mapping_utils
    
    # Mock DB storage for mappings
    fake_mappings = {}
    
    def fake_load():
        return fake_mappings
        
    def fake_save(orig, mapped):
        fake_mappings[orig.strip().upper()] = mapped.strip().upper()
        
    mocker.patch("bse_mapping_utils.load_bse_mappings", side_effect=fake_load)
    mocker.patch("bse_mapping_utils.save_bse_mapping", side_effect=fake_save)
    bse_mapping_utils._bse_mappings_cache = fake_mappings
    fetcher = YFinanceFetcher()
    
    # Mock _get_ohlcv_raw:
    # First call (NSE query) returns empty/None
    # Second call (BSE fallback query) returns data
    dummy_df = pd.DataFrame({'Close': [500]})
    mocker.patch.object(fetcher, "_get_ohlcv_raw", side_effect=[None, dummy_df])
    
    df = fetcher.get_ohlcv("YASHHV", "1d", "1y")
    
    # Should successfully return the BSE df
    assert df is not None
    assert df.iloc[0]['Close'] == 500
    
    # Verify that the mapping is saved to our temp file
    mappings = load_bse_mappings()
    assert mappings.get("YASHHV") == "YASHHV.BO"
    
    # Now, calling normalize_symbol on YASHHV should directly return YASHHV.BO
    assert fetcher._normalize_symbol("YASHHV") == "YASHHV.BO"
    assert fetcher._normalize_symbol("YASHHV.NS") == "YASHHV.BO"
    bse_mapping_utils._bse_mappings_cache = None

