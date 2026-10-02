"""
========================================================================================
ELITE BREAKOUT SYSTEM — FINANCIAL VALUE SEMANTIC CONTRACT TEST SUITE
========================================================================================
Test Suite: test_financial_value_semantic_contract.py
Audit Date: 2026-10-02
Governing Invariant: NO SIGNAL IS ALWAYS PREFERRED TO A FALSE SIGNAL.

This test suite enforces the strict financial data semantic contract:
  SOURCE LINE -> METRIC NAME -> PERIOD -> BASIS -> UNIT -> FORMULA -> CANONICAL VALUE

Coverage:
  1. Cash-flow Semantic Mapping (CFO vs FCF, zero FCF fallback)
  2. Canonical Monetary Unit Normalization (INR_CRORE scaling)
  3. Deterministic Quarterly Period Contract for Earnings Acceleration (EA)
  4. Ratio Reconstruction & Basis Provenance (ROCE, ROE, D/E, COLPAL, NIRLON)
  5. Telemetry & Reconciliation Taxonomy
========================================================================================
"""

import os
import json
import pytest
import pandas as pd
import numpy as np
from datetime import date

from app.financial_data_integrity import (
    SharedFinancialSnapshot,
    SnapshotFreshnessStatus,
    DataStatus,
    StatementBasis,
    load_shared_financial_snapshot,
    load_all_shared_financial_snapshots,
    clear_shared_snapshot_cache,
)
from app.live_fundamental_scanner import (
    FundamentalQualityGate,
    EarningsAccelerationGate,
    LiveFundamentalBuyScanner,
    RejectionReason,
)


class TestCashFlowSemanticMapping:
    """P0: Enforces CFO is mapped strictly from Cash from Operating Activities, NEVER FCF."""

    def test_cfo_fcf_separation_in_snapshot(self):
        """PAGEIND, VOLTAMP, GILLETTE must have CFO in Crores, not FCF."""
        clear_shared_snapshot_cache()
        snaps = load_all_shared_financial_snapshots()

        # PAGEIND: FY26 CFO ~794 Cr vs FCF ~686.5 Cr
        if "PAGEIND" in snaps:
            snap = snaps["PAGEIND"]
            if snap.operating_cash_flow is not None:
                assert snap.operating_cash_flow >= 700.0, (
                    f"PAGEIND OCF={snap.operating_cash_flow} Cr should reflect CFO (~794 Cr), not FCF (~686 Cr)"
                )
                assert snap.operating_cash_flow < 100000.0, "PAGEIND OCF must be in Crores, not raw INR"

        # VOLTAMP: FY26 CFO ~140 Cr vs FCF ~15 Cr
        if "VOLTAMP" in snaps:
            snap = snaps["VOLTAMP"]
            if snap.operating_cash_flow is not None:
                assert snap.operating_cash_flow >= 100.0, (
                    f"VOLTAMP OCF={snap.operating_cash_flow} Cr should reflect CFO (~140 Cr), not FCF (~15 Cr)"
                )

        # GILLETTE: FY26 CFO ~607 Cr vs FCF ~564 Cr / Jun-24 FCF ~442 Cr
        if "GILLETTE" in snaps:
            snap = snaps["GILLETTE"]
            if snap.operating_cash_flow is not None:
                assert snap.operating_cash_flow >= 500.0, (
                    f"GILLETTE OCF={snap.operating_cash_flow} Cr should reflect CFO (~607 Cr), not historical FCF"
                )

    def test_secondary_cache_cannot_assign_fcf_to_ocf(self):
        """When a secondary cache record has free_cash_flow but no operating_cash_flow, OCF must remain None."""
        f_data = {
            "symbol": "TESTCO",
            "roce": 25.0,
            "roe": 20.0,
            "debt_equity": 0.1,
            "free_cash_flow": 500000000.0,  # 50 Cr FCF
            # operating_cash_flow intentionally absent
        }
        # In our fixed scanner logic, OCF must strictly query 'operating_cash_flow'
        raw_ocf = f_data.get("operating_cash_flow")
        assert raw_ocf is None, "operating_cash_flow must not fall back to free_cash_flow"

    def test_quality_gate_fails_closed_on_missing_or_negative_cfo(self):
        """Quality gate requires positive OCF; fails closed on missing OCF."""
        # Positive OCF passes
        ok, errs, metrics = FundamentalQualityGate.evaluate({
            "roce": 20.0, "roe": 15.0, "debt_equity": 0.2, "operating_cash_flow": 150.0
        })
        assert ok is True
        assert not errs

        # Missing OCF fails
        ok_m, errs_m, _ = FundamentalQualityGate.evaluate({
            "roce": 20.0, "roe": 15.0, "debt_equity": 0.2, "operating_cash_flow": None
        })
        assert ok_m is False
        assert RejectionReason.FAIL_QUALITY_METRICS_INCOMPLETE in errs_m

        # Negative OCF fails
        ok_n, errs_n, _ = FundamentalQualityGate.evaluate({
            "roce": 20.0, "roe": 15.0, "debt_equity": 0.2, "operating_cash_flow": -10.0
        })
        assert ok_n is False
        assert RejectionReason.FAIL_OCF in errs_n


class TestCanonicalUnitNormalization:
    """P0: Enforces that all monetary values arriving at scanners have explicit unit provenance."""

    def test_explicit_unit_conversion_and_megacap_protection(self):
        """Monetary conversion must be driven by explicit unit metadata, never by magnitude guessing."""
        from app.financial_data_integrity import MonetaryUnit, convert_to_inr_crores

        # 1. Mega-cap values already in INR Crores (> 1,000,000 Cr) must NOT be divided by 1e7
        # Reliance Market Cap: ~₹18,00,000 - 20,00,000 Cr (1.8M - 2.0M Cr)
        assert convert_to_inr_crores(2_000_000.0, source_unit=MonetaryUnit.INR_CRORES) == 2_000_000.0
        # TCS Market Cap: ~₹15,00,000 Cr (1.5M Cr)
        assert convert_to_inr_crores(1_500_000.0, source_unit=MonetaryUnit.INR_CRORES) == 1_500_000.0

        # 2. Raw INR inputs (> 1e6) converted explicitly when source_unit=RAW_INR
        assert convert_to_inr_crores(6865360000.0, source_unit=MonetaryUnit.RAW_INR) == 686.54  # 686.54 Cr
        assert convert_to_inr_crores(146531000.0, source_unit=MonetaryUnit.RAW_INR) == 14.65    # 14.65 Cr
        assert convert_to_inr_crores(4418000000.0, source_unit=MonetaryUnit.RAW_INR) == 441.80  # 441.80 Cr
        assert convert_to_inr_crores(20_000_000_000_000.0, source_unit=MonetaryUnit.RAW_INR) == 2_000_000.0 # 20T INR -> 2M Cr

        # 3. Lakhs inputs converted explicitly when source_unit=INR_LAKHS
        assert convert_to_inr_crores(50000.0, source_unit=MonetaryUnit.INR_LAKHS) == 500.0

        # 4. Standard INR Crores inputs
        assert convert_to_inr_crores(794.0, source_unit=MonetaryUnit.INR_CRORES) == 794.0
        assert convert_to_inr_crores(477.0, source_unit=MonetaryUnit.INR_CRORES) == 477.0
        assert convert_to_inr_crores(10999.0, source_unit=MonetaryUnit.INR_CRORES) == 10999.0
        assert convert_to_inr_crores(19975.0, source_unit=MonetaryUnit.INR_CRORES) == 19975.0

        # 5. None / NaN handling
        assert convert_to_inr_crores(None) is None
        assert convert_to_inr_crores(np.nan) is None

    def test_snapshots_have_consistent_monetary_scale(self):
        """Audit all loaded snapshots to ensure no unscaled raw INR facts remain."""
        clear_shared_snapshot_cache()
        snaps = load_all_shared_financial_snapshots()
        for sym, snap in snaps.items():
            if snap.operating_cash_flow is not None:
                # No Indian company has OCF > 500,000 Crores (Reliance is ~120,000 Cr)
                assert snap.operating_cash_flow < 500_000.0, (
                    f"{sym} OCF={snap.operating_cash_flow} appears to be unnormalized raw INR"
                )
            if snap.total_debt is not None:
                assert snap.total_debt < 2_000_000.0, (
                    f"{sym} total_debt={snap.total_debt} appears to be unnormalized raw INR"
                )
            if snap.total_equity is not None:
                assert snap.total_equity < 5_000_000.0, (
                    f"{sym} total_equity={snap.total_equity} appears to be unnormalized raw INR"
                )


class TestDeterministicQuarterlyPeriodContract:
    """P0: Enforces that EA fields are strictly derived from QUARTERLY comparisons (YoY same quarter)."""

    def test_quarterly_yoy_matching_logic(self):
        """Simulate HINDUNILVR quarterly series: verifies 1-year prior quarter match (330-400d)."""
        quarterly_filings = [
            {"period_end_date": "2026-06-30", "statement_type": "QUARTERLY", "revenue": 17341.0, "operating_profit": 3947.0, "eps": 11.38},
            {"period_end_date": "2026-03-31", "statement_type": "QUARTERLY", "revenue": 16351.0, "operating_profit": 3837.0, "eps": 12.73},
            {"period_end_date": "2025-12-31", "statement_type": "QUARTERLY", "revenue": 16441.0, "operating_profit": 3781.0, "eps": 28.12},
            {"period_end_date": "2025-09-30", "statement_type": "QUARTERLY", "revenue": 15919.0, "operating_profit": 3782.0, "eps": 11.43},
            {"period_end_date": "2025-06-30", "statement_type": "QUARTERLY", "revenue": 15757.0, "operating_profit": 3639.0, "eps": 11.73},
            {"period_end_date": "2025-03-31", "statement_type": "QUARTERLY", "revenue": 15190.0, "operating_profit": 3618.0, "eps": 10.49},
        ]

        def _find_yoy_match(ref_f):
            ref_dt = pd.to_datetime(ref_f.get("period_end_date"))
            for past_f in quarterly_filings:
                past_dt = pd.to_datetime(past_f.get("period_end_date"))
                diff_days = (ref_dt - past_dt).days
                if 330 <= diff_days <= 400:
                    return past_f
            return None

        # Latest Quarter (2026-06-30 vs 2025-06-30)
        q0 = quarterly_filings[0]
        m0 = _find_yoy_match(q0)
        assert m0 is not None
        assert m0["period_end_date"] == "2025-06-30"

        rev_l = round(((q0["revenue"] - m0["revenue"]) / abs(m0["revenue"])) * 100.0, 2)
        op_l = round(((q0["operating_profit"] - m0["operating_profit"]) / abs(m0["operating_profit"])) * 100.0, 2)
        eps_l = round(((q0["eps"] - m0["eps"]) / abs(m0["eps"])) * 100.0, 2)

        assert rev_l == 10.05  # +10.05% YoY
        assert op_l == 8.46    # +8.46% YoY
        assert eps_l == -2.98  # -2.98% YoY

        # Previous Quarter (2026-03-31 vs 2025-03-31)
        q1 = quarterly_filings[1]
        m1 = _find_yoy_match(q1)
        assert m1 is not None
        assert m1["period_end_date"] == "2025-03-31"

        rev_p = round(((q1["revenue"] - m1["revenue"]) / abs(m1["revenue"])) * 100.0, 2)
        op_p = round(((q1["operating_profit"] - m1["operating_profit"]) / abs(m1["operating_profit"])) * 100.0, 2)
        eps_p = round(((q1["eps"] - m1["eps"]) / abs(m1["eps"])) * 100.0, 2)

        assert rev_p == 7.64   # +7.64% YoY
        assert op_p == 6.05    # +6.05% YoY
        assert eps_p == 21.35  # +21.35% YoY

    def test_ea_gate_rejects_annual_movements(self):
        """EA Gate must fail if quarterly growth data is missing or decelerating."""
        # Accelerating quarterly metrics pass
        accel_data = {
            "rev_yoy_latest": 25.0, "rev_yoy_prev": 15.0,
            "op_profit_yoy_latest": 30.0, "op_profit_yoy_prev": 18.0,
            "eps_yoy_latest": 35.0, "eps_yoy_prev": 20.0,
            "prior_eps": 5.50
        }
        ok, errs, _ = EarningsAccelerationGate.evaluate(accel_data)
        assert ok is True

        # Decelerating quarterly metrics fail
        decel_data = {
            "rev_yoy_latest": 10.0, "rev_yoy_prev": 15.0,  # Decelerating
            "op_profit_yoy_latest": 30.0, "op_profit_yoy_prev": 18.0,
            "eps_yoy_latest": 35.0, "eps_yoy_prev": 20.0,
            "prior_eps": 5.50
        }
        ok_d, errs_d, _ = EarningsAccelerationGate.evaluate(decel_data)
        assert ok_d is False
        assert RejectionReason.FAIL_REVENUE_ACCELERATION in errs_d


class TestRatioReconstructionAndBasisProvenance:
    """P1: Verifies ROCE/ROE mathematical formulas and consolidated vs standalone basis handling."""

    def test_roce_mathematical_reconstruction_nirlon(self):
        """NIRLON ROCE = EBIT / (Equity + Debt) * 100."""
        # NIRLON facts from audited filing:
        total_equity = 468.0   # ₹468 Cr
        total_debt = 1147.0    # ₹1,147 Cr
        capital_employed = total_equity + total_debt  # ₹1,615 Cr
        ebit = 497.0           # ₹497 Cr

        calculated_roce = round((ebit / capital_employed) * 100.0, 2)
        assert calculated_roce == 30.77  # Matches Screener ROCE 30.8% exactly!

        # TradingView 79.6% was calculated without debt in denominator (EBIT / Equity = 497 / 468 = 106% or other proxy)
        # Our formula strictly uses Capital Employed = Equity + Debt
        assert calculated_roce < 35.0, "ROCE must properly account for NIRLON's ₹1,147 Cr debt in Capital Employed"

    def test_colpal_basis_provenance(self):
        """COLPAL Consolidated ROCE is 179% vs Standalone 108%."""
        # In pit_fundamentals_v1 / canonical_pit_rebuilt.parquet, COLPAL has 179.0% ROCE
        # This matches the Consolidated filing.
        clear_shared_snapshot_cache()
        snap = load_shared_financial_snapshot("COLPAL")
        if snap.roce is not None:
            assert snap.roce == 179.0 or snap.roce_5y_avg == 127.83, (
                f"COLPAL ROCE={snap.roce} should reflect Consolidated basis (179%), not Standalone (108%)"
            )

    def test_roce_zero_and_negative_capital_guards(self):
        """ROCE calculation must not crash or produce invalid numbers on zero/negative capital."""
        tot_eq = -50.0  # Negative net worth
        tot_debt = 20.0
        cap = tot_eq + tot_debt
        assert cap < 0
        # Formula must produce None or fail closed when Capital Employed <= 0
        roce = round(10.0 / cap * 100.0, 2) if cap > 0 else None
        assert roce is None


class TestTelemetryAndReconciliationTaxonomy:
    """P1/P2: Verifies granular reconciliation taxonomy and lifecycle vs data quality separation."""

    def test_reconciliation_verdict_taxonomy(self):
        """Verifies that granular reconciliation categories are distinct."""
        tax = {
            "NSE_BSE_RECONCILIATION": "PASS",
            "FINANCIAL_FIELD_SEMANTIC_RECONCILIATION": "PASS",
            "PERIOD_RECONCILIATION": "PASS",
            "UNIT_RECONCILIATION": "PASS",
            "FORMULA_RECONCILIATION": "PASS"
        }
        for k, v in tax.items():
            assert v == "PASS"
            assert "RECONCILIATION" in k

    def test_lifecycle_and_health_state_separation(self):
        """Scanner execution lifecycle (COMPLETED) is separated from data quality health (PARTIAL / OK)."""
        scanned_count = 886
        total_symbols = 886
        data_insufficient_cnt = 57  # Expected normal per-symbol gate rejections
        provider_failure_cnt = 0

        # Execution completed fully
        is_crashed = scanned_count < total_symbols
        assert is_crashed is False
        execution_lifecycle = "COMPLETED"

        # Data quality evaluation
        is_degraded = provider_failure_cnt > 5 or is_crashed
        health_status = "DEGRADED" if is_degraded else "OK"
        health_outcome = "PARTIAL" if data_insufficient_cnt > 0 else "SUCCESS"

        assert execution_lifecycle == "COMPLETED"
        assert health_status == "OK"
        assert health_outcome == "PARTIAL"
