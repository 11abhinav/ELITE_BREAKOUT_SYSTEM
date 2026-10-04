"""
tests/test_bse_corporate_provider.py
====================================
Integration & unit tests for BseCorporateProvider:
  1. BSE_AVAILABLE: Successfully extracts semantically valid RawFinancialRecords
  2. BSE_NO_DATA: Scrip code queried successfully but no records returned
  3. BSE_HTTP_ERROR: Network timeout, 403, or 5xx handling
  4. BSE_SYMBOL_NOT_FOUND: Scrip code cannot be resolved for unmapped symbol
  5. BSE_PARSE_FAILURE: HTTP 200 raw data returned, but 0 semantically valid records extracted
     (Enforces P0 Invariant: HTTP 200 + raw rows > 0 + usable == 0 -> BSE_PARSE_FAILURE)
"""

from unittest.mock import MagicMock, patch
import pytest
import requests

from app.data_providers.bse_corporate_provider import BseCorporateProvider
from app.data_providers.fundamental_models import ConsolidationType


def test_bse_symbol_not_found():
    """Unmapped symbol with no scrip code resolution produces BSE_SYMBOL_NOT_FOUND."""
    provider = BseCorporateProvider()
    records = provider.fetch_raw_financials("COMPLETELY_UNKNOWN_XYZ_SYM")

    assert len(records) == 0
    assert provider.last_status.get("COMPLETELY_UNKNOWN_XYZ_SYM") == "BSE_SYMBOL_NOT_FOUND"
    assert provider.last_raw_count.get("COMPLETELY_UNKNOWN_XYZ_SYM") == 0
    assert provider.last_usable_count.get("COMPLETELY_UNKNOWN_XYZ_SYM") == 0


def test_bse_available_success():
    """Valid BSE corporate response returns semantically validated records and BSE_AVAILABLE."""
    provider = BseCorporateProvider()
    mock_response_data = [
        {
            "PeriodEnded": "2024-03-31",
            "Period": "Annual",
            "Consolidated": "Consolidated",
            "TotIncome": "1250.50",
            "NetProfit": "180.25",
            "OperatingProfit": "220.00",
            "CFO": "210.00",
            "TotalDebt": "50.00",
            "TotalEquity": "800.00",
            "Unit": "cr",
        },
        {
            "PeriodEnded": "2023-03-31",
            "Period": "Annual",
            "Consolidated": "Consolidated",
            "TotIncome": "1100.00",
            "NetProfit": "150.00",
            "OperatingProfit": "190.00",
            "CFO": "175.00",
            "TotalDebt": "60.00",
            "TotalEquity": "700.00",
            "Unit": "cr",
        }
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_response_data

    with patch.object(provider.session, "get", return_value=mock_resp):
        records = provider.fetch_raw_financials("ADOR")

    assert len(records) == 2
    assert provider.last_status.get("ADOR") == "BSE_AVAILABLE"
    assert provider.last_raw_count.get("ADOR") == 2
    assert provider.last_usable_count.get("ADOR") == 2
    assert records[0].source == "BSE_CORPORATE"
    assert records[0].revenue == 1250.50
    assert records[0].net_profit == 180.25
    assert records[0].capital_employed == 850.00
    assert records[0].consolidation == ConsolidationType.CONSOLIDATED


def test_bse_no_data():
    """Empty list from BSE API produces BSE_NO_DATA."""
    provider = BseCorporateProvider()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = []

    with patch.object(provider.session, "get", return_value=mock_resp):
        records = provider.fetch_raw_financials("ADOR")

    assert len(records) == 0
    assert provider.last_status.get("ADOR") == "BSE_NO_DATA"
    assert provider.last_raw_count.get("ADOR") == 0
    assert provider.last_usable_count.get("ADOR") == 0


def test_bse_http_error():
    """Network connection timeout or 500 error produces BSE_HTTP_ERROR."""
    provider = BseCorporateProvider()

    with patch.object(provider.session, "get", side_effect=requests.RequestException("Connection timed out")):
        records = provider.fetch_raw_financials("ADOR")

    assert len(records) == 0
    assert provider.last_status.get("ADOR") == "BSE_HTTP_ERROR"
    assert "Connection timed out" in str(provider.last_error.get("ADOR"))


def test_bse_feed_access_required():
    """HTTP 401 or 403 produces BSE_FEED_ACCESS_REQUIRED, not generic HTTP error."""
    provider = BseCorporateProvider()
    mock_resp = MagicMock()
    mock_resp.status_code = 403

    with patch.object(provider.session, "get", return_value=mock_resp):
        records = provider.fetch_raw_financials("ADOR")

    assert len(records) == 0
    assert provider.last_status.get("ADOR") == "BSE_FEED_ACCESS_REQUIRED"
    assert provider.bse_auth_or_403_blocks == 2  # C and S flags attempted



def test_bse_parse_failure_p0_invariant():
    """
    P0 INVARIANT: HTTP 200 + raw rows > 0 + usable == 0 -> BSE_PARSE_FAILURE.
    If raw data contains rows with invalid dates, non-numeric fields, or negative revenue,
    the provider must classify as BSE_PARSE_FAILURE (never BSE_NO_DATA).
    """
    provider = BseCorporateProvider()
    corrupted_data = [
        {
            "PeriodEnded": "FUTURE_DATE_INVALID",
            "Period": "Annual",
            "TotIncome": "-500.00",  # Negative revenue violates semantic check
            "NetProfit": "NaN",
        }
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = corrupted_data

    with patch.object(provider.session, "get", return_value=mock_resp):
        records = provider.fetch_raw_financials("ADOR")

    assert len(records) == 0
    assert provider.last_raw_count.get("ADOR") == 1
    assert provider.last_usable_count.get("ADOR") == 0
    assert provider.last_status.get("ADOR") == "BSE_PARSE_FAILURE"
