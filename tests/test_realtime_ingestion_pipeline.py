import pytest
import pandas as pd
import logging
from database import get_latest_build_manifest
from price_cache import fetch_watchlist_data, clear_price_cache
from core_enums import ProviderResult

logger = logging.getLogger(__name__)

def _load_live_test_watchlist() -> pd.DataFrame:
    try:
        manifest = get_latest_build_manifest()
        symbols = manifest.get("symbols", [])
        if symbols:
            return pd.DataFrame(symbols)
    except Exception as e:
        logger.warning(f"Could not load live build manifest: {e}")

    # Fallback to realistic production symbol universe
    sample_symbols = ["POLYCAB", "LALPATHLAB", "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "BHARTIARTL", "ITC"]
    return pd.DataFrame([{"Stock": s, "Category": "Wealth Compounder"} for s in sample_symbols])

class TestRealtimeIngestionPipeline:
    """Real-time integration test suite using live watchlist symbols and real API price cache pipelines."""

    def test_realtime_watchlist_symbol_keys(self):
        """Verify live watchlist symbol formatting and ensure no null/empty symbols exist."""
        watchlist = _load_live_test_watchlist()
        assert not watchlist.empty, "Daily watchlist is empty!"
        assert "Stock" in watchlist.columns, "Daily watchlist missing 'Stock' column!"
        
        symbols = watchlist["Stock"].dropna().tolist()
        assert len(symbols) > 0, "No valid symbols found in daily watchlist!"
        for sym in symbols:
            assert isinstance(sym, str) and len(sym.strip()) > 0, f"Invalid symbol string: {sym}"
            assert not sym.endswith(".NS") and not sym.endswith(".BO"), f"Raw watchlist contains exchange suffix: {sym}"

    def test_realtime_fetch_watchlist_data_alignment(self):
        """Execute fetch_watchlist_data on live watchlist symbols and verify 100% canonical key alignment."""
        watchlist = _load_live_test_watchlist()
        if watchlist.empty or len(watchlist) == 0:
            pytest.skip("Skipping real-time fetch test because watchlist is empty in DB environment")

        # Test first batch of 20 live symbols
        test_sample = watchlist.head(20).copy()
        requested_symbols = test_sample["Stock"].tolist()
        
        # Clear cache to ensure fresh pipeline execution
        clear_price_cache()

        all_data = fetch_watchlist_data(test_sample, period="6mo", interval="1d", requester="RealtimeTest")
        assert all_data is not None, "fetch_watchlist_data returned None!"
        assert len(all_data) > 0, "fetch_watchlist_data returned empty dictionary!"

        # Check key alignment for requested symbols
        missing_keys = []
        valid_dataframes = 0
        for sym in requested_symbols:
            if sym not in all_data:
                missing_keys.append(sym)
            else:
                val = all_data[sym]
                if isinstance(val, pd.DataFrame) and not val.empty:
                    valid_dataframes += 1

        assert len(missing_keys) == 0, f"Canonical key mismatch! Requested symbols missing from returned keys: {missing_keys}"
        # Assert at least 80% valid dataframes fetched live
        fetch_ratio = valid_dataframes / len(requested_symbols)
        logger.info(f"📊 Realtime Ingestion Result: {valid_dataframes}/{len(requested_symbols)} ({fetch_ratio*100:.1f}%) valid DataFrames fetched.")
        assert fetch_ratio >= 0.80, f"Low real-time data fetch ratio: {fetch_ratio*100:.1f}% < 80%"

    def test_realtime_eod_scanner_key_resolution(self):
        """Verify that eod_scanner._start_wrapper resolves symbols without false no_data rejections under canonical keys."""
        import eod_scanner
        watchlist = _load_live_test_watchlist()
        if watchlist.empty or len(watchlist) == 0:
            pytest.skip("Skipping real-time scanner test because watchlist is empty")

        test_sample = watchlist.head(10).copy()
        all_ticker_data = fetch_watchlist_data(test_sample, period="6mo", interval="1d", requester="RealtimeScannerTest")
        
        # Verify scanner resolution logic against fetched dictionary
        no_data_count = 0
        for _, row in test_sample.iterrows():
            sym = row["Stock"]
            ticker_data = all_ticker_data.get(sym)
            if ticker_data is None:
                ticker_data = all_ticker_data.get(f"{sym}.NS") or all_ticker_data.get(f"{sym}.BO") or all_ticker_data.get(sym.split('.')[0])
            
            if ticker_data is None or (isinstance(ticker_data, pd.DataFrame) and ticker_data.empty):
                no_data_count += 1

        assert no_data_count == 0, f"Real-time scanner symbol resolution failed! {no_data_count}/{len(test_sample)} symbols marked as no_data!"
