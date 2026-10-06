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


def test_get_quarantined_symbols_filters_active_absences():
    """
    Verify that get_quarantined_symbols returns symbols under active quarantine
    (CONFIRMED_NO_DATA_ANYWHERE, NOT_REPORTED, DATA_UNAVAILABLE) and excludes transient provider errors.
    """
    from app.pit_recovery_cache import PitRecoveryStatusStore
    import tempfile
    import os

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = os.path.join(tmpdir, "test_recovery_status.parquet")
        store = PitRecoveryStatusStore(parquet_path=tmp_path)
        # Add confirmed data unavailable symbol
        store.record_unavailability(
            symbol="BAD_CO",
            provider="ALL",
            status="UNAVAILABLE",
            reason="CONFIRMED_NO_DATA_ANYWHERE",
            scanner_family="FUNDAMENTAL",
        )
        # Add not reported filing symbol
        store.record_unavailability(
            symbol="GAP_CO",
            provider="ALL",
            status="UNAVAILABLE",
            reason="NOT_REPORTED",
            scanner_family="ALL",
        )
        # Add transient failure that should NOT be quarantined
        store.record_unavailability(
            symbol="RETRY_CO",
            provider="UPSTOX",
            status="PROVIDER_ERROR",
            reason="PROVIDER_FAILURE",
            scanner_family="FUNDAMENTAL",
        )

        q_symbols = store.get_quarantined_symbols(scanner_family="FUNDAMENTAL")
        assert "BAD_CO" in q_symbols
        assert "GAP_CO" in q_symbols
        assert "RETRY_CO" not in q_symbols


def test_fresh_feed_pre_buy_pass_to_db_insertion():
    """
    PROVES THE REMAINING PROOF GAP:
    Qualified Recovery Candidate -> Fresh Exchange Feed (within 24h SLA) -> Pre-BUY Gate PASS -> DB Insertion.
    Verifies that when market data feed SLA is valid, qualified recovery stocks cleanly pass
    the Pre-BUY Data Integrity Gate and persist as active BUY alerts in the database.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from unittest.mock import patch
    from app.financial_data_integrity import (
        BUYEvidenceBundle,
        pre_buy_data_integrity_gate,
        DataStatus,
        FieldProvenance,
    )
    from app.database import save_v2_candidate_alert

    ist = ZoneInfo("Asia/Kolkata")
    now_iso = datetime.now(ist).isoformat()

    # 1. Qualified candidate bundle with complete field provenance
    fin_metrics = {
        "roce": FieldProvenance(
            symbol="TEST_RECOVERY", scanner="QUALITY_VALUE_RECOVERY", field="roce",
            value_used=18.5, unit="PERCENT", period_end="2026-03-31", period_type="ANNUAL",
            basis="CONSOLIDATED", source_used="EXCHANGE_FILINGS", validation_status="PASSED"
        ),
        "debt_to_equity": FieldProvenance(
            symbol="TEST_RECOVERY", scanner="QUALITY_VALUE_RECOVERY", field="debt_to_equity",
            value_used=0.15, unit="RATIO", period_end="2026-03-31", period_type="ANNUAL",
            basis="CONSOLIDATED", source_used="EXCHANGE_FILINGS", validation_status="PASSED"
        ),
        "cfo_pat_ratio": FieldProvenance(
            symbol="TEST_RECOVERY", scanner="QUALITY_VALUE_RECOVERY", field="cfo_pat_ratio",
            value_used=0.95, unit="RATIO", period_end="2026-03-31", period_type="ANNUAL",
            basis="CONSOLIDATED", source_used="EXCHANGE_FILINGS", validation_status="PASSED"
        ),
    }

    c_bundle = BUYEvidenceBundle(
        scan_run_id="RECOVERY_TEST_RUN_001",
        scanner="QUALITY_VALUE_RECOVERY",
        symbol="TEST_RECOVERY",
        cmp=500.0,
        strategy_score=90.0,
        gate_results={"QUALITY": True, "VALUATION": True},
        financial_metrics=fin_metrics,
        data_integrity_status=DataStatus.VALID,
        financial_provenance_complete=True,
        pit_valid=True,
        period_integrity=True,
        basis_integrity=True,
        unit_integrity=True,
        required_metrics_complete=True,
    )

    # 2. Fresh exchange feed watermark (checked 10 minutes ago, well within 24h SLA)
    fresh_watermark = {
        "symbol": "TEST_RECOVERY",
        "valid": True,
        "latest_exchange_period_end": "2026-03-31",
        "latest_exchange_filing_timestamp": "2026-03-31T20:00:00+05:30",
        "nse_last_checked": now_iso,
        "bse_last_checked": now_iso,
        "failure_reason": None,
    }

    with patch("app.financial_data_integrity.get_multi_source_exchange_watermark", return_value=fresh_watermark):
        # Evaluate Pre-BUY gate under fresh feed
        c_verdict = pre_buy_data_integrity_gate(c_bundle)
        assert c_verdict.ok is True, f"Expected Pre-BUY gate to PASS with fresh feed, but got: {c_verdict.reason}"

        # Persist alert to database
        cand = {
            "symbol": "TEST_RECOVERY",
            "entry_price": 500.0,
            "current_price": 500.0,
            "tier": "TIER1",
            "ranking_score": 90.0,
            "signal_date": datetime.now(ist).strftime("%Y-%m-%d"),
            "scanner": "QUALITY_VALUE_RECOVERY",
            "breakout_type": "QUALITY_VALUE_RECOVERY",
            "metrics": {"drawdown_pct": 45.0, "roce": 18.5, "d_e": 0.15, "val_compression_ratio": 0.65},
            "context": {"price_source": "UPSTOX"},
        }

        ok, msg = save_v2_candidate_alert(cand)
        assert ok is True, f"Failed to persist candidate alert: {msg}"
        assert "TEST_RECOVERY" in msg or "INSERTED" in msg
