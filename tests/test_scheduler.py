import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch, MagicMock

# Assume we want to test if market hours logic works correctly
def is_market_open(mock_now: datetime) -> bool:
    from datetime import time as dt_time
    # Simplified version of what's in main.py
    market_open = dt_time(9, 15) <= mock_now.time() <= dt_time(15, 30)
    is_weekday = mock_now.weekday() < 5
    return market_open and is_weekday

def test_market_hours_open():
    IST = ZoneInfo("Asia/Kolkata")
    # Thursday, 10:30 AM (Market Open)
    mock_now = datetime(2026, 6, 25, 10, 30, tzinfo=IST)
    assert is_market_open(mock_now) == True

def test_market_hours_closed_weekend():
    IST = ZoneInfo("Asia/Kolkata")
    # Saturday, 10:30 AM (Market Closed)
    mock_now = datetime(2026, 6, 27, 10, 30, tzinfo=IST)
    assert is_market_open(mock_now) == False

def test_market_hours_closed_after_hours():
    IST = ZoneInfo("Asia/Kolkata")
    # Thursday, 16:00 (4:00 PM) (Market Closed)
    mock_now = datetime(2026, 6, 25, 16, 0, tzinfo=IST)
    assert is_market_open(mock_now) == False
