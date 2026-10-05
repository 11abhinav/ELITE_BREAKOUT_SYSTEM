"""
Targeted Governance Unit Test Suite:
  1. Fiscal-Year Awareness & Annual Filing Classification Invariants
  2. Dual-Source Reconciliation with Statement Consolidation Basis Matching & Unit Normalization
"""

import unittest
from datetime import date
from typing import Dict, Any, List

from app.financial_data_integrity import (
    is_verified_annual_filing_record,
    convert_to_inr_crores,
    MonetaryUnit,
)
from app.data_providers.fundamental_models import (
    RawFinancialRecord,
    ConsolidationType,
    FundamentalStatus,
)
from app.data_providers.fundamental_source_router import FundamentalSourceRouter


class TestFiscalYearFenceAndReconciliation(unittest.TestCase):

    # =========================================================================
    # PART 1: FISCAL YEAR FENCE & ANNUAL FILING CLASSIFICATION TESTS
    # =========================================================================

    def test_01_march_annual_filing(self):
        """March 31 annual filing with explicit ANNUAL metadata -> ACCEPTED."""
        rec = {
            "period_type": "ANNUAL",
            "period_end_date": "2026-03-31",
        }
        self.assertTrue(is_verified_annual_filing_record(rec))

    def test_02_june_annual_filing_kennamet(self):
        """June 30 annual filing (e.g. KENNAMET fiscal year end) -> ACCEPTED."""
        rec = {
            "period_type": "ANNUAL",
            "period_end_date": "2026-06-30",
        }
        self.assertTrue(is_verified_annual_filing_record(rec))

    def test_03_december_annual_filing_sanofi_rain(self):
        """December 31 annual filing (e.g. SANOFI / RAIN fiscal year end) -> ACCEPTED."""
        rec = {
            "period_type": "ANNUAL",
            "period_end_date": "2025-12-31",
        }
        self.assertTrue(is_verified_annual_filing_record(rec))

    def test_04_explicit_quarterly_despite_long_duration(self):
        """Explicit QUARTERLY metadata despite duration >= 300 days -> INSTANT REJECTION."""
        rec = {
            "period_type": "QUARTERLY",
            "duration_days": 365,
            "period_start_date": "2025-04-01",
            "period_end_date": "2026-03-31",
        }
        self.assertFalse(is_verified_annual_filing_record(rec))

    def test_05_half_year_interim_filings(self):
        """H1 / H2 / HALF_YEAR interim filings -> INSTANT REJECTION."""
        for p in ("HALF_YEAR", "HALF_YEARLY", "H1", "H2", "NINE_MONTHS", "Q3"):
            rec = {
                "period_type": p,
                "period_end_date": "2025-09-30",
            }
            self.assertFalse(is_verified_annual_filing_record(rec))

    def test_06_interim_raw_fact_labels(self):
        """Ambiguous/empty period_type with quarterly/unaudited raw labels -> INSTANT REJECTION."""
        rec = {
            "period_type": "",
            "duration_days": 365,
            "Period": "3 MONTHS ENDED Q1",
            "period_end_date": "2026-06-30",
        }
        self.assertFalse(is_verified_annual_filing_record(rec))

    def test_07_ambiguous_metadata_duration_fallback(self):
        """Empty period_type with duration >=300 days and valid month-end fiscal boundary -> ACCEPTED."""
        rec = {
            "period_type": "",
            "duration_days": 365,
            "period_start_date": "2025-04-01",
            "period_end_date": "2026-03-31",
        }
        self.assertTrue(is_verified_annual_filing_record(rec))

    # =========================================================================
    # PART 2: DUAL-SOURCE RECONCILIATION & CONSOLIDATION MATCHING TESTS
    # =========================================================================

    def setUp(self):
        self.router = FundamentalSourceRouter()

    def test_08_same_period_same_basis_low_divergence_passed(self):
        """Same period + same basis + <=25% divergence -> PASSED."""
        live = [
            RawFinancialRecord(
                symbol="TESTCO",
                source="NSE_XBRL",
                period_end_date="2026-03-31",
                period_type="ANNUAL",
                consolidation=ConsolidationType.CONSOLIDATED,
                revenue=1050.0,
                net_profit=105.0,
                unit="CR",
            )
        ]
        local = [
            RawFinancialRecord(
                symbol="TESTCO",
                source="LOCAL_RAW_FILINGS",
                period_end_date="2026-03-31",
                period_type="ANNUAL",
                consolidation=ConsolidationType.CONSOLIDATED,
                revenue=1000.0,
                net_profit=100.0,
                unit="CR",
            )
        ]
        res = self.router._reconcile_upstox_and_local("TESTCO", live, local)
        self.assertNotEqual(res.overall_status, FundamentalStatus.DATA_CONFLICT)
        self.assertNotEqual(res.overall_status, FundamentalStatus.STATEMENT_MISMATCH)

    def test_09_same_period_same_basis_high_divergence_data_conflict(self):
        """Same period + same basis + >25% divergence -> DATA_CONFLICT (hard block)."""
        live = [
            RawFinancialRecord(
                symbol="TESTCO",
                source="NSE_XBRL",
                period_end_date="2026-03-31",
                period_type="ANNUAL",
                consolidation=ConsolidationType.CONSOLIDATED,
                revenue=1400.0,  # 40% divergence
                net_profit=100.0,
                unit="CR",
            )
        ]
        local = [
            RawFinancialRecord(
                symbol="TESTCO",
                source="LOCAL_RAW_FILINGS",
                period_end_date="2026-03-31",
                period_type="ANNUAL",
                consolidation=ConsolidationType.CONSOLIDATED,
                revenue=1000.0,
                net_profit=100.0,
                unit="CR",
            )
        ]
        res = self.router._reconcile_upstox_and_local("TESTCO", live, local)
        self.assertEqual(res.overall_status, FundamentalStatus.DATA_CONFLICT)

    def test_10_same_period_different_basis_statement_mismatch(self):
        """Same period + different basis (Live=CONSOLIDATED, Local=STANDALONE) -> STATEMENT_MISMATCH (hard block)."""
        live = [
            RawFinancialRecord(
                symbol="TESTCO",
                source="NSE_XBRL",
                period_end_date="2026-03-31",
                period_type="ANNUAL",
                consolidation=ConsolidationType.CONSOLIDATED,
                revenue=2500.0,
                net_profit=250.0,
                unit="CR",
            )
        ]
        local = [
            RawFinancialRecord(
                symbol="TESTCO",
                source="LOCAL_RAW_FILINGS",
                period_end_date="2026-03-31",
                period_type="ANNUAL",
                consolidation=ConsolidationType.STANDALONE,
                revenue=1000.0,
                net_profit=100.0,
                unit="CR",
            )
        ]
        res = self.router._reconcile_upstox_and_local("TESTCO", live, local)
        self.assertEqual(res.overall_status, FundamentalStatus.STATEMENT_MISMATCH)

    def test_11_unit_mismatch_crores_vs_lakhs_normalized(self):
        """Unit mismatch (Crores vs Lakhs) normalized before tolerance check -> PASSED."""
        live = [
            RawFinancialRecord(
                symbol="TESTCO",
                source="NSE_XBRL",
                period_end_date="2026-03-31",
                period_type="ANNUAL",
                consolidation=ConsolidationType.CONSOLIDATED,
                revenue=10.0,  # 10 Crores
                net_profit=1.0,
                unit="CR",
            )
        ]
        local = [
            RawFinancialRecord(
                symbol="TESTCO",
                source="LOCAL_RAW_FILINGS",
                period_end_date="2026-03-31",
                period_type="ANNUAL",
                consolidation=ConsolidationType.CONSOLIDATED,
                revenue=1000.0,  # 1000 Lakhs = 10 Crores
                net_profit=100.0,
                unit="LAKHS",
            )
        ]
        res = self.router._reconcile_upstox_and_local("TESTCO", live, local)
        self.assertNotEqual(res.overall_status, FundamentalStatus.DATA_CONFLICT)

    def test_12_standalone_and_consolidated_distinct_records(self):
        """Dual sources supplying both Standalone and Consolidated records match strictly by basis."""
        live = [
            RawFinancialRecord(
                symbol="TESTCO",
                source="NSE_XBRL",
                period_end_date="2026-03-31",
                period_type="ANNUAL",
                consolidation=ConsolidationType.CONSOLIDATED,
                revenue=2000.0,
                net_profit=200.0,
                unit="CR",
            ),
            RawFinancialRecord(
                symbol="TESTCO",
                source="NSE_XBRL",
                period_end_date="2026-03-31",
                period_type="ANNUAL",
                consolidation=ConsolidationType.STANDALONE,
                revenue=1000.0,
                net_profit=100.0,
                unit="CR",
            ),
        ]
        local = [
            RawFinancialRecord(
                symbol="TESTCO",
                source="LOCAL_RAW_FILINGS",
                period_end_date="2026-03-31",
                period_type="ANNUAL",
                consolidation=ConsolidationType.CONSOLIDATED,
                revenue=2010.0,
                net_profit=201.0,
                unit="CR",
            ),
            RawFinancialRecord(
                symbol="TESTCO",
                source="LOCAL_RAW_FILINGS",
                period_end_date="2026-03-31",
                period_type="ANNUAL",
                consolidation=ConsolidationType.STANDALONE,
                revenue=1005.0,
                net_profit=100.5,
                unit="CR",
            ),
        ]
        res = self.router._reconcile_upstox_and_local("TESTCO", live, local)
        self.assertNotEqual(res.overall_status, FundamentalStatus.DATA_CONFLICT)
        self.assertNotEqual(res.overall_status, FundamentalStatus.STATEMENT_MISMATCH)


if __name__ == "__main__":
    unittest.main()
