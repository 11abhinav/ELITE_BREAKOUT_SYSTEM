import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app')))

from data_providers.unified_fetcher import fetcher
from data_providers.fyers_fetcher import FyersFetcher
from data_providers.provider_selector import selector
from data_provider import YFinanceFetcher


class TestExternalApiDataValidation:
    """Comprehensive test suite validating external API data fetching and symbol routing."""

    def test_fyers_symbol_normalization_for_indices_and_equities(self):
        """Validate FyersFetcher symbol normalization for equities and index tickers."""
        fyers = FyersFetcher()
        assert fyers._normalize_symbol("TATAMOTORS") == "NSE:TATAMOTORS-EQ"
        assert fyers._normalize_symbol("TATAMOTORS.NS") == "NSE:TATAMOTORS-EQ"
        assert fyers._normalize_symbol("RELIANCE.BO") == "BSE:RELIANCE-EQ"
        assert fyers._normalize_symbol("^NSEI") == "NSE:NIFTY50-INDEX"
        assert fyers._normalize_symbol("NIFTY 50") == "NSE:NIFTY50-INDEX"
        assert fyers._normalize_symbol("BANKNIFTY") == "NSE:NIFTYBANK-INDEX"
        assert fyers._normalize_symbol("SENSEX") == "BSE:SENSEX-INDEX"

    def test_yfinance_symbol_normalization(self):
        """Validate YFinanceFetcher symbol normalization."""
        yf_fetcher = YFinanceFetcher()
        assert yf_fetcher._normalize_symbol("RELIANCE") == "RELIANCE.NS"
        assert yf_fetcher._normalize_symbol("RELIANCE.NS") == "RELIANCE.NS"
        assert yf_fetcher._normalize_symbol("^NSEI") == "^NSEI"
        assert yf_fetcher._normalize_symbol("^BSESN") == "^BSESN"

    @patch("yfinance.download")
    def test_unified_fetcher_index_ticker_mapping_in_yahoo_fallback(self, mock_yf_download):
        """
        Verify that UnifiedFetcher.fetch_live_quotes correctly maps index names
        ('NIFTY 50', 'BANKNIFTY', 'SENSEX') to valid YFinance tickers ('^NSEI', '^NSEBANK', '^BSESN')
        instead of generating invalid delisted tickers ('NIFTY 50.NS', 'BANKNIFTY.NS').
        """
        # Create a mock multi-index DataFrame mimicking YFinance download output for indices
        dates = pd.date_range("2026-07-26", periods=2)
        columns = pd.MultiIndex.from_tuples([
            ("^NSEI", "Close"), ("^NSEBANK", "Close"), ("^BSESN", "Close")
        ])
        mock_df = pd.DataFrame([[24500.0, 52000.0, 80000.0], [24600.0, 52200.0, 80300.0]], index=dates, columns=columns)
        mock_yf_download.return_value = mock_df

        # Mock Fyers to fail so fallback to Yahoo is triggered
        with patch.object(fetcher.fyers, "get_ohlcv", return_value=None):
            with patch("fyers_auth.get_fyers_client", return_value=None):
                quotes = fetcher.fetch_live_quotes(["NIFTY 50", "BANKNIFTY", "SENSEX"], consumer="test_consumer")

        # Verify yf.download was called with mapped index symbols
        assert mock_yf_download.called
        download_args = mock_yf_download.call_args[0][0]
        assert "^NSEI" in download_args
        assert "^NSEBANK" in download_args
        assert "^BSESN" in download_args
        assert "NIFTY 50.NS" not in download_args

        # Verify results returned prices for original requested keys
        assert "NIFTY 50" in quotes
        assert quotes["NIFTY 50"]["v"]["cmd"]["c"] == 24600.0
        assert "BANKNIFTY" in quotes
        assert quotes["BANKNIFTY"]["v"]["cmd"]["c"] == 52200.0
        assert "SENSEX" in quotes
        assert quotes["SENSEX"]["v"]["cmd"]["c"] == 80300.0

    def test_provider_selector_routing_policies(self):
        """Validate ProviderSelector routing policies for daily, intraday, and live data."""
        assert selector.get_providers("price_1d", fetch_type="historical") == ["yahoo", "fyers", "bse"]
        assert selector.get_providers("price_15m", fetch_type="historical") == ["fyers", "yahoo", "bse"]
        assert selector.get_providers("live_quotes", fetch_type="live_quotes") == ["fyers", "yahoo", "bse"]

    @patch("yfinance.download")
    def test_live_quotes_equity_fallback_to_yahoo(self, mock_yf_download):
        """Verify equity ticker live quotes fallback to Yahoo appending .NS."""
        dates = pd.date_range("2026-07-26", periods=2)
        columns = pd.MultiIndex.from_tuples([
            ("TATAMOTORS.NS", "Close"), ("RELIANCE.NS", "Close")
        ])
        mock_df = pd.DataFrame([[1000.0, 3000.0], [1020.0, 3050.0]], index=dates, columns=columns)
        mock_yf_download.return_value = mock_df

        with patch("fyers_auth.get_fyers_client", return_value=None):
            quotes = fetcher.fetch_live_quotes(["TATAMOTORS", "RELIANCE"], consumer="test_equity")

        assert "TATAMOTORS" in quotes
        assert quotes["TATAMOTORS"]["v"]["cmd"]["c"] == 1020.0
        assert "RELIANCE" in quotes
        assert quotes["RELIANCE"]["v"]["cmd"]["c"] == 3050.0
