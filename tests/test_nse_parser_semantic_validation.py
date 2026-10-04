"""
tests/test_nse_parser_semantic_validation.py
============================================
P0 Semantic Correctness Tests for Financial Statement Parsers:
Enforces:
  usable_count > 0 != valid_data
A record is usable only after semantic validation confirms:
  1. Valid ISO date in the past (rejects future dates)
  2. Valid statement type (ANNUAL / QUARTERLY)
  3. Consolidated vs Standalone declaration
  4. Non-negative top-line revenue
  5. Finite numeric values (rejects NaN, Inf, corrupted strings)
  6. Valid reporting currency & units
"""

from datetime import date, timedelta
import pytest
from app.data_providers.fundamental_models import ConsolidationType, RawFinancialRecord
from app.data_providers.nse_xbrl_provider import validate_semantic_record


def test_semantic_validation_valid_record():
    """A well-formed financial statement record passes semantic validation."""
    valid_rec = RawFinancialRecord(
        symbol="TCS",
        source="NSE_XBRL",
        period_end_date="2024-03-31",
        period_type="ANNUAL",
        consolidation=ConsolidationType.CONSOLIDATED,
        revenue=240893.0,
        net_profit=46099.0,
        operating_cash_flow=44318.0,
        total_debt=7800.0,
        total_equity=90500.0,
        capital_employed=98300.0,
        eps=126.5,
        unit="cr",
        currency="INR",
    )
    is_valid, reason = validate_semantic_record(valid_rec)
    assert is_valid is True
    assert reason is None


def test_semantic_validation_rejects_future_period_end():
    """Records with future period_end_date must be rejected as invalid data."""
    future_date = (date.today() + timedelta(days=400)).isoformat()
    future_rec = RawFinancialRecord(
        symbol="TCS",
        source="NSE_XBRL",
        period_end_date=future_date,
        period_type="ANNUAL",
        consolidation=ConsolidationType.CONSOLIDATED,
        revenue=1000.0,
        net_profit=100.0,
        unit="cr",
        currency="INR",
    )
    is_valid, reason = validate_semantic_record(future_rec)
    assert is_valid is False
    assert "FUTURE_PERIOD_END" in reason


def test_semantic_validation_rejects_negative_revenue():
    """Gross / operating revenue cannot be negative."""
    neg_rec = RawFinancialRecord(
        symbol="XYZ",
        source="NSE_XBRL",
        period_end_date="2024-03-31",
        period_type="ANNUAL",
        consolidation=ConsolidationType.CONSOLIDATED,
        revenue=-150.0,
        net_profit=10.0,
        unit="cr",
        currency="INR",
    )
    is_valid, reason = validate_semantic_record(neg_rec)
    assert is_valid is False
    assert "NEGATIVE_REVENUE" in reason


def test_semantic_validation_rejects_invalid_statement_type():
    """Statement type must be ANNUAL or QUARTERLY."""
    bad_type_rec = RawFinancialRecord(
        symbol="XYZ",
        source="NSE_XBRL",
        period_end_date="2024-03-31",
        period_type="UNKNOWN_TYPE_XYZ",
        consolidation=ConsolidationType.CONSOLIDATED,
        revenue=500.0,
        net_profit=50.0,
        unit="cr",
        currency="INR",
    )
    is_valid, reason = validate_semantic_record(bad_type_rec)
    assert is_valid is False
    assert "INVALID_STATEMENT_TYPE" in reason


def test_semantic_validation_rejects_non_finite_values():
    """Values such as float('inf') or float('nan') must be rejected."""
    inf_rec = RawFinancialRecord(
        symbol="XYZ",
        source="NSE_XBRL",
        period_end_date="2024-03-31",
        period_type="ANNUAL",
        consolidation=ConsolidationType.CONSOLIDATED,
        revenue=float("inf"),
        net_profit=50.0,
        unit="cr",
        currency="INR",
    )
    is_valid, reason = validate_semantic_record(inf_rec)
    assert is_valid is False
    assert "NAN_OR_INF_METRIC_DETECTED" in reason


def test_semantic_validation_rejects_missing_unit_or_currency():
    """Unit and currency must be declared and valid."""
    bad_curr_rec = RawFinancialRecord(
        symbol="XYZ",
        source="NSE_XBRL",
        period_end_date="2024-03-31",
        period_type="ANNUAL",
        consolidation=ConsolidationType.CONSOLIDATED,
        revenue=500.0,
        net_profit=50.0,
        unit="",
        currency="",
    )
    is_valid, reason = validate_semantic_record(bad_curr_rec)
    assert is_valid is False
    assert ("UNSUPPORTED_CURRENCY" in reason or "UNKNOWN_FINANCIAL_UNIT" in reason)
