"""
Tests for:
1. Upstox Fundamentals Provider balance sheet parsing (category list format as returned by Upstox API v2 for UTLSOLAR).
2. QualityValueRecoveryScanner 1D price history integration and logging.
"""

import pytest
from typing import Dict, Any, List
import pandas as pd
from unittest.mock import MagicMock, patch

from app.data_providers.upstox_fundamentals_provider import (
    UpstoxFundamentalsProvider,
    RawFinancialRecord,
    ConsolidationType,
)
from app.live_fundamental_scanner import QualityValueRecoveryScanner


def test_upstox_balance_sheet_category_list_parsing():
    """
    Verify that Upstox API v2 balance sheet format (category list with 'history')
    properly populates total_debt, total_equity, and capital_employed on RawFinancialRecord.
    This resolves the UTLSOLAR debt mapping failure.
    """
    provider = UpstoxFundamentalsProvider()

    # Sample existing income records
    income_records = [
        RawFinancialRecord(
            symbol="UTLSOLAR",
            source="UPSTOX_API",
            period_end_date="2025-03-31",
            period_type="ANNUAL",
            consolidation=ConsolidationType.CONSOLIDATED,
            revenue=1000.0,
            net_profit=100.0,
            ebit=150.0,
            eps=10.0,
            unit="crore",
            currency="INR",
        )
    ]

    # Real Upstox v2 API payload schema for /balance-sheet
    mock_bs_response = {
        "status": "success",
        "data": {
            "type": "consolidated",
            "time_period": "yearly",
            "balance_sheet": [
                {
                    "category": "Total Assets",
                    "history": [{"period": "31-Mar-2025", "value": 500.0}],
                },
                {
                    "category": "Total Liabilities",
                    "history": [{"period": "31-Mar-2025", "value": 200.0}],
                },
                {
                    "category": "Total Debt",
                    "history": [{"period": "31-Mar-2025", "value": 50.0}],
                },
                {
                    "category": "Shareholders Equity",
                    "history": [{"period": "31-Mar-2025", "value": 300.0}],
                },
                {
                    "category": "Cash & Cash Equivalents",
                    "history": [{"period": "31-Mar-2025", "value": 40.0}],
                },
            ],
        },
    }

    with patch.object(provider, "_get", return_value=mock_bs_response):
        enriched = provider._parse_balance_sheets("INE000000000", "UTLSOLAR", income_records)

    assert len(enriched) == 1
    rec = enriched[0]
    assert rec.total_debt == 50.0, f"Expected total_debt=50.0, got {rec.total_debt}"
    assert rec.total_equity == 300.0, f"Expected total_equity=300.0, got {rec.total_equity}"
    assert rec.capital_employed == 300.0, f"Expected capital_employed=300.0, got {rec.capital_employed}"


def test_quality_value_recovery_uses_df_px_drawdown():
    """
    Verify that evaluate_symbol_recovery uses df_px 504-day high to calculate drawdown
    and passes candidates with >=30% drawdown and compressed valuation.
    """
    # Create 504 daily bars where peak was 1000 and current is 650 (35% drawdown)
    dates = pd.date_range(end="2026-10-06", periods=504, freq="D")
    df_px = pd.DataFrame({
        "open": [900.0] * 504,
        "high": [1000.0] + [700.0] * 503,
        "low": [600.0] * 504,
        "close": [700.0] * 503 + [650.0],
        "volume": [10000] * 504,
    }, index=dates)

    stock_row = {
        "symbol": "DISLOCATED_CO",
        "industry": "Capital Goods",
        "roce_5y_avg": 18.0,
        "roce": 18.0,
        "debt_to_equity": 0.20,
        "cfo_pat_ratio": 0.90,
        "current_ev_ebitda": 10.0,
        "ev_ebitda_3y_median": 15.0,  # 33% compression (10/15 <= 0.80)
        "current_price": 650.0,
    }

    # Evaluate with df_px
    passed, rejection_reasons, metrics = QualityValueRecoveryScanner.evaluate_symbol_recovery(
        sym="DISLOCATED_CO",
        row=stock_row,
        cmp_price=650.0,
        df_px=df_px,
    )

    assert passed is True, f"Expected PASS, but got reasons: {rejection_reasons}"
    assert metrics["drawdown_pct"] == 35.0
    assert metrics["val_compression_ratio"] <= 0.80
    assert "NO_DRAWDOWN_DISLOCATION_FAIL" not in rejection_reasons
