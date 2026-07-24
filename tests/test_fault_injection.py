import pytest
import pandas as pd
import requests
import psycopg2
from unittest.mock import patch, MagicMock
from datetime import date

from validation.result import ValidationStatus
from delivery_data import fetch_delivery_data
from price_cache import _download_all_robust
import data_provider

@pytest.fixture(autouse=True)
def clear_price_provider_cache():
    data_provider._price_provider.cache.clear()
    yield

@pytest.fixture
def mock_db_connection():
    with patch('validation.history.get_connection') as mock_val_conn:
        with patch('database.get_connection') as mock_db_conn:
            yield (mock_val_conn, mock_db_conn)

@pytest.fixture
def mock_scraper_api_key():
    with patch('pledge_scraper.get_scraper_api_key', return_value="fake_api_key"):
        yield

@pytest.fixture
def empty_delivery_cache():
    import delivery_data
    from data_registry import registry
    delivery_data._delivery_cache = None
    delivery_data._delivery_cache_date = None
    registry._datasets.clear()
    with patch('database.get_bhavcopy_cache', return_value=None):
        yield

# ==============================================================================
# PHASE 2: PROVIDER FAILURES
# ==============================================================================

def test_bhavcopy_network_timeout(empty_delivery_cache, mock_scraper_api_key):
    """Simulates a network timeout during Bhavcopy ingestion."""
    with patch('requests.Session.get') as mock_get:
        mock_get.side_effect = requests.exceptions.Timeout("Connection timed out")
        
        with patch('delivery_data.mark_failure') as mock_mark_failure:
            result = fetch_delivery_data(date(2026, 7, 19))
            
            assert result == {}
            # Assert failure is correctly marked
            mock_mark_failure.assert_called_with('nse_bhavcopy', 'Connection timed out')

def test_bhavcopy_html_response(empty_delivery_cache, mock_scraper_api_key):
    """Simulates a Cloudflare block page (HTML instead of CSV) during Bhavcopy ingestion."""
    with patch('requests.Session.get') as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body><h1>Access Denied</h1></body></html>"
        mock_get.return_value = mock_resp
        
        with patch('delivery_data.history_recorder.record_single') as mock_record:
            result = fetch_delivery_data(date(2026, 7, 19))
            
            # Since it's a parse error, it fails gracefully
            assert result == {}
            # Because it throws a pandas parsing error, it does not reach validation,
            # so record_single is not called. This tests parse error resilience.
            mock_record.assert_not_called()

def test_bhavcopy_truncated_response(empty_delivery_cache, mock_scraper_api_key):
    """Simulates an extremely small CSV file (truncated) bypassing size checks."""
    csv_content = "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN,DELIV_QTY,DELIV_PER\n"
    csv_content += "RELIANCE,EQ,2500,2550,2480,2530,2530,2490,1000000,250000000,19-JUL-2026,10000,INE123456789,500000,50.0\n"
    # To bypass `len(raw_data) < 1000` we pad it with spaces
    csv_content += " " * 1000 
    
    with patch('requests.Session.get') as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = csv_content
        mock_get.return_value = mock_resp
        
        with patch('delivery_data.history_recorder.record_single') as mock_record:
            result = fetch_delivery_data(date(2026, 7, 19))
            
            # It should fail HIS001 (Symbol count dropped by > 10%)
            assert result == {}
            
            # Record should still be called with INVALID dataset
            assert mock_record.called
            args, kwargs = mock_record.call_args
            validated_dataset = args[1]
            assert validated_dataset.status == ValidationStatus.INVALID
            
            # Assert failure code HIS001 is present
            assert any("HIS001" in f for f in validated_dataset.result.critical_failures)

# ==============================================================================
# PHASE 3: DATA CORRUPTION FAILURES
# ==============================================================================

def test_bhavcopy_missing_mandatory_columns(empty_delivery_cache, mock_scraper_api_key):
    """Simulates a Bhavcopy payload missing the mandatory SYMBOL column."""
    csv_content = "SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN,DELIV_QTY,DELIV_PER\n"
    csv_content += "EQ,2500,2550,2480,2530,2530,2490,1000000,250000000,19-JUL-2026,10000,INE123456789,500000,50.0\n"
    csv_content += " " * 1000 
    
    with patch('requests.Session.get') as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = csv_content
        mock_get.return_value = mock_resp
        
        with patch('delivery_data.history_recorder.record_single') as mock_record:
            result = fetch_delivery_data(date(2026, 7, 19))
            
            # It should fail SCH001 (Missing required columns)
            assert result == {}
            
            assert mock_record.called
            args, kwargs = mock_record.call_args
            validated_dataset = args[1]
            assert validated_dataset.status == ValidationStatus.INVALID
            
            assert any("SCH001" in f for f in validated_dataset.result.critical_failures)

def test_price_ingestion_corrupted_ohlc(tmp_path):
    """Simulates fetching corrupted price data where HIGH < LOW and negative prices exist."""
    watchlist = pd.DataFrame({"Stock": ["TESTSYM"]})
    
    # Create corrupted df
    corrupted_df = pd.DataFrame({
        "Datetime": pd.date_range("2026-07-01", periods=5),
        "Open": [100.0, 101.0, 102.0, 103.0, 104.0],
        "High": [90.0, 95.0, 96.0, 97.0, 98.0], # High is lower than Open/Low!
        "Low": [110.0, 111.0, 112.0, 113.0, 114.0],
        "Close": [105.0, 106.0, 107.0, 108.0, -10.0], # Negative close
        "Volume": [1000, 2000, 1500, 3000, 1000]
    }).set_index("Datetime")
    
    with patch('yfinance.download') as mock_yf_download:
        mock_yf_download.return_value = corrupted_df
        
        with patch('price_cache.DATA_DIR', str(tmp_path)):
            with patch('price_cache.history_recorder.record_batch') as mock_record_batch:
                data = _download_all_robust(watchlist, period="1mo", interval="1d")
                
                # Verify that the cache fallback occurred or the corrupted data was processed properly.
                # Since the data is INVALID, it should NOT return it as valid fresh data.
                # The data contains negative prices (BUS001) and HIGH < LOW (BUS002).
                # Therefore, the cache state machine sets is_stale=True (if cache existed) or drops it (None).
                assert "TESTSYM" not in data or data["TESTSYM"] is None or getattr(data["TESTSYM"], "attrs", {}).get('is_stale', False)
                
                assert mock_record_batch.called
                args, kwargs = mock_record_batch.call_args
                results_list = args[1]
                assert len(results_list) == 1
                
                report = results_list[0]
                assert report.status == ValidationStatus.INVALID
                
                failure_strings = list(report.result.critical_failures)
                assert any("BUS002" in f for f in failure_strings) # Corrupted OHLCV data

# ==============================================================================
# PHASE 4: DATABASE FAILURES
# ==============================================================================

def test_database_unavailable_resilience(tmp_path):
    """Simulates a database outage during price ingestion, asserting the system stays available."""
    watchlist = pd.DataFrame({"Stock": ["TESTSYM"]})
    
    valid_df = pd.DataFrame({
        "Datetime": pd.date_range("2026-07-01", periods=50),
        "Open": [100.0] * 50,
        "High": [105.0] * 50,
        "Low": [95.0] * 50,
        "Close": [102.0] * 50,
        "Volume": [1000] * 50
    }).set_index("Datetime")
    
    with patch('yfinance.download') as mock_yf_download:
        mock_yf_download.return_value = valid_df
        
        # Patch the database connections to fail
        with patch('validation.history.get_connection') as mock_get_conn:
            with patch('database.get_connection') as mock_db_conn:
                mock_get_conn.side_effect = psycopg2.OperationalError("Database is offline")
                mock_db_conn.side_effect = psycopg2.OperationalError("Database is offline")
                
                with patch('price_cache.DATA_DIR', str(tmp_path)):
                    # Run ingestion
                    # The crucial assertion here is that it DOES NOT RAISE an exception
                    data = _download_all_robust(watchlist, period="1mo", interval="1d")
                    
                    # And the data is still successfully cached and returned
                    assert "TESTSYM" in data
                    assert data["TESTSYM"] is not None
                    assert not data["TESTSYM"].empty
                    
                    # Verify meta file was saved successfully despite database outage
                    import os, json
                    meta_path = os.path.join(str(tmp_path), "history", "1d", "TESTSYM.meta.json")
                    assert os.path.exists(meta_path)
                    with open(meta_path, "r") as f:
                        meta = json.load(f)
                    assert meta["validation_status"] == "ValidationStatus.VALID"
