import pytest
from unittest.mock import patch, MagicMock
from datetime import date
import threading
import time
import sys
import os
import pandas as pd

# Add app to path so we can import like the app does
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app')))

from delivery_data import fetch_delivery_data
from validation.result import ValidationStatus

@patch("database.get_bhavcopy_cache")
@patch("delivery_data._get_robust_session")
def test_bhavcopy_db_cache_hit_prevents_network(mock_session, mock_get_cache):
    """Test that a DB cache hit instantly returns data without making HTTP requests."""
    # Setup mock DB cache hit
    mock_get_cache.return_value = {"RELIANCE": 50.5, "TCS": 40.2}
    
    result = fetch_delivery_data(date(2026, 7, 20))
    
    assert result == {"RELIANCE": 50.5, "TCS": 40.2}
    mock_get_cache.assert_called_once_with(date(2026, 7, 20))
    mock_session.assert_not_called()

@patch("database.get_bhavcopy_cache")
@patch("database.save_bhavcopy_cache")
@patch("delivery_data._get_robust_session")
@patch("pledge_scraper.get_scraper_api_key", return_value="dummy_key")
@patch("delivery_data.history_recorder.record_single")
@patch("delivery_data.ValidationEngine")
def test_bhavcopy_db_cache_miss_fetches_and_saves(mock_engine_cls, mock_record, mock_api_key, mock_session, mock_save_cache, mock_get_cache):
    """Test that a DB cache miss fetches from ScraperAPI and saves to DB."""
    # Mock ValidationEngine
    mock_engine = MagicMock()
    mock_dataset = MagicMock()
    mock_dataset.status = ValidationStatus.VALID
    mock_dataset.result.has_warnings = False
    mock_engine.process.return_value = mock_dataset
    mock_engine_cls.return_value = mock_engine

    # First call to get_bhavcopy_cache (outside lock) returns None
    # Second call to get_bhavcopy_cache (inside lock) returns None
    mock_get_cache.side_effect = [None, None]
    
    # Mock ScraperAPI response
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN,DELIV_QTY,DELIV_PER\n" \
                     "RELIANCE,EQ,2500,2550,2480,2530,2530,2490,1000000,250000000,19-JUL-2026,10000,INE123456789,500000,50.0\n" \
                     "TCS,EQ,3000,3100,2900,3050,3050,2950,500000,150000000,19-JUL-2026,5000,INE987654321,200000,40.0\n" \
                     + (" " * 1000) # Bypass size check
    
    mock_session_instance = MagicMock()
    mock_session_instance.get.return_value = mock_resp
    mock_session.return_value = mock_session_instance
    
    result = fetch_delivery_data(date(2026, 7, 20))
    
    # Assert network was called
    assert mock_session_instance.get.call_count == 1
    
    # Assert DB save was called
    mock_save_cache.assert_called_once()
    saved_date, saved_data = mock_save_cache.call_args[0]
    assert saved_date == date(2026, 7, 20)
    assert saved_data == {"RELIANCE": 50.0, "TCS": 40.0}
    
    # Assert correct result returned
    assert result == {"RELIANCE": 50.0, "TCS": 40.0}

@patch("database.get_bhavcopy_cache")
@patch("database.save_bhavcopy_cache")
@patch("delivery_data._get_robust_session")
@patch("pledge_scraper.get_scraper_api_key", return_value="dummy_key")
@patch("delivery_data.history_recorder.record_single")
@patch("delivery_data.ValidationEngine")
def test_bhavcopy_concurrency_double_checked_locking(mock_engine_cls, mock_record, mock_api_key, mock_session, mock_save_cache, mock_get_cache):
    """Test that concurrent calls result in only one network fetch and one DB save."""
    # Mock ValidationEngine
    mock_engine = MagicMock()
    mock_dataset = MagicMock()
    mock_dataset.status = ValidationStatus.VALID
    mock_dataset.result.has_warnings = False
    mock_engine.process.return_value = mock_dataset
    mock_engine_cls.return_value = mock_engine
    
    # We'll use a side_effect function for get_bhavcopy_cache to simulate this
    call_count = [0]
    
    def mock_get_cache_side_effect(trading_date):
        call_count[0] += 1
        if call_count[0] <= 2:
            return None # First two checks (outside lock T1, outside lock T2)
        elif call_count[0] == 3:
            return None # Inside lock T1
        else:
            return {"RELIANCE": 50.0} # Inside lock T2 (after T1 saved it)
            
    mock_get_cache.side_effect = mock_get_cache_side_effect
    
    # Mock ScraperAPI response for T1
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN,DELIV_QTY,DELIV_PER\n" \
                     "RELIANCE,EQ,2500,2550,2480,2530,2530,2490,1000000,250000000,19-JUL-2026,10000,INE123456789,500000,50.0\n" \
                     + (" " * 1000)
    
    mock_session_instance = MagicMock()
    # Add a sleep to the mock get to ensure T2 gets blocked on the lock
    def delayed_get(*args, **kwargs):
        time.sleep(0.5)
        return mock_resp
        
    mock_session_instance.get.side_effect = delayed_get
    mock_session.return_value = mock_session_instance
    
    results = []
    def worker():
        results.append(fetch_delivery_data(date(2026, 7, 20)))
        
    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    
    t1.start()
    time.sleep(0.1) # Ensure T1 grabs the lock first
    t2.start()
    
    t1.join()
    t2.join()
    
    # Both threads should get the exact same data
    assert len(results) == 2
    assert results[0] == {"RELIANCE": 50.0}
    assert results[1] == {"RELIANCE": 50.0}
    
    # Network should ONLY be called ONCE
    assert mock_session_instance.get.call_count == 1
    
    # Save should ONLY be called ONCE
    assert mock_save_cache.call_count == 1
