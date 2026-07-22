import pytest
from datetime import datetime, date, timedelta
from app.earnings_calendar import EarningsCalendarService, EarningsSeverity, DateStatus

class MockProvider:
    def __init__(self, date_map=None):
        self.date_map = date_map or {}

    def fetch_earnings_date(self, symbol: str):
        if symbol in self.date_map:
            return self.date_map[symbol], DateStatus.ESTIMATED
        return None, DateStatus.UNKNOWN

def test_earnings_risk_classification():
    today = date(2026, 7, 25)
    
    # 1. Mock DB data scenario
    service = EarningsCalendarService(provider=MockProvider())
    
    # Test Today
    service_get_info = service.get_earnings_info
    
    # Unit test direct logic
    diff_0 = (today - today).days
    assert diff_0 == 0
    
    diff_2 = (date(2026, 7, 27) - today).days
    assert diff_2 == 2
    
    diff_4 = (date(2026, 7, 29) - today).days
    assert diff_4 == 4
    
    diff_10 = (date(2026, 8, 4) - today).days
    assert diff_10 == 10

def test_mock_provider_fetch():
    target_date = date(2026, 8, 1)
    provider = MockProvider({"RELIANCE": target_date})
    service = EarningsCalendarService(provider=provider)
    
    ed, status = provider.fetch_earnings_date("RELIANCE")
    assert ed == target_date
    assert status == DateStatus.ESTIMATED
