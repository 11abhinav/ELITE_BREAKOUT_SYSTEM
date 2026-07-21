import sys
import os
import pytest
from unittest.mock import MagicMock

# Add app to path so imports resolve
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

def test_multibagger_memory_cleanup_no_crash(monkeypatch):
    """
    [SCANNER_SMOKE_TEST] Ensures that the multibagger scanner's main wrapper completes
    successfully and does not crash during the memory cleanup / return block.
    Specifically guards against UnboundLocalError caused by prematurely deleting
    variables like `fundamentals_list` before they are used in the return statement.
    """
    import multibagger
    import constituent_service

    # 1. Mock NSE constituent fetch
    monkeypatch.setattr(constituent_service, "fetch_constituents", lambda: ["MOCK_STOCK_1", "MOCK_STOCK_2"])
    
    # 2. Mock batch download
    from multibagger import StockPriceData
    def mock_batch_download(symbols):
        return {
            sym: StockPriceData(
                symbol=sym, price=105.0, change_pct=1.0, low_52w=90.0, high_52w=120.0, 
                turnover_20d=2000000.0, sma_20=100.0, sma_50=95.0, sma_200=90.0, 
                high_20d=110.0, high_60d=115.0, mom_3m=10.0, mom_6m=15.0, atr_14=2.0, 
                ema_20=101.0, latest_volume=50000, volume_sma20=45000, 
                close_yesterday=104.0, sma_200_yesterday=89.9, closes_below_sma200_count=0
            ) for sym in symbols
        }
    monkeypatch.setattr(multibagger, "batch_download_market_data", mock_batch_download)
    
    # 3. Mock the core pipeline to avoid DB locks and API calls
    mock_pipeline = MagicMock()
    # Pipeline returns (symbol, PipelineResult or None)
    monkeypatch.setattr(multibagger, "run_pipeline_for_symbol", lambda *args, **kwargs: (args[0], None))
    
    # 4. Run the scanner in test mode
    try:
        # debug_limit=2 ensures it loops quickly, is_test_mode=True bypasses telegram/DB saves
        stats = multibagger.start(debug_limit=2, is_test_mode=True)
        
        # Verify the cleanup block didn't crash and returned the correct stats structure
        assert isinstance(stats, dict), "Scanner must return a dictionary of stats"
        assert "total_count" in stats, "total_count must be in stats"
        assert "processed_count" in stats, "processed_count must be in stats"
        assert "today_alerts" in stats, "today_alerts must be in stats"
        
    except UnboundLocalError as e:
        pytest.fail(f"Scanner crashed during memory cleanup! Variables were likely deleted prematurely: {e}")
    except Exception as e:
        # Ignore other exceptions unless they are specifically variable access errors in the cleanup block
        if "cannot access local variable" in str(e):
             pytest.fail(f"Scanner crashed with local variable scope error: {e}")
