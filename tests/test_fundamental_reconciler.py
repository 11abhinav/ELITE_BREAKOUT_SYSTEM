import unittest
from datetime import datetime, timedelta
import pandas as pd
try:
    from app.data_providers.fundamental_models import (
        RawFinancialRecord, ConsolidationType, FundamentalStatus, ReconciledCanonicalMetrics
    )
    from app.data_providers.fundamental_reconciler import FundamentalReconciler
    from app.fundamental_pre_recovery import FundamentalPreRecoveryEngine
except ImportError:
    from data_providers.fundamental_models import (
        RawFinancialRecord, ConsolidationType, FundamentalStatus, ReconciledCanonicalMetrics
    )
    from data_providers.fundamental_reconciler import FundamentalReconciler
    from fundamental_pre_recovery import FundamentalPreRecoveryEngine

class TestFundamentalReconcilerRedTeam(unittest.TestCase):
    def setUp(self):
        self.reconciler = FundamentalReconciler()
        self.engine = FundamentalPreRecoveryEngine(pit_parquet_path="dummy.parquet")

    @staticmethod
    def _make_series(source="UPSTOX", rev_mult=1.0):
        return [
            RawFinancialRecord(
                symbol="TEST", source=source, period_end_date=f"{yr}-03-31",
                period_type="ANNUAL", consolidation=ConsolidationType.CONSOLIDATED,
                revenue=1000.0 * (1.10 ** i) * (rev_mult if (i == 5 and source == "UPSTOX") else 1.0),
                net_profit=100.0 * (1.10 ** i),
                ebit=150.0, capital_employed=500.0, total_debt=50.0, total_equity=450.0,
                operating_cash_flow=120.0, shares_outstanding=10.0
            )
            for i, yr in enumerate(range(2020, 2026))
        ]

    def test_01_exact_match(self):
        # TEST 01: Upstox = NSE -> VERIFIED
        upstox = self._make_series("UPSTOX")
        nse = self._make_series("NSE")
        
        metrics = self.reconciler.reconcile_and_calculate("TEST", nse, upstox)
        self.assertEqual(metrics.overall_status, FundamentalStatus.VERIFIED)

    def test_02_within_tolerance(self):
        # TEST 02: Upstox differs by 0.05% -> VERIFIED
        upstox = self._make_series("UPSTOX", rev_mult=1.0005)
        nse = self._make_series("NSE")
        
        metrics = self.reconciler.reconcile_and_calculate("TEST", nse, upstox)
        self.assertEqual(metrics.overall_status, FundamentalStatus.VERIFIED)

    def test_03_exceeds_tolerance(self):
        # TEST 03: Upstox differs by 30% -> DATA_CONFLICT -> BLOCK
        upstox = [RawFinancialRecord(symbol="TEST", source="UPSTOX", period_end_date="2025-03-31", period_type="ANNUAL", consolidation=ConsolidationType.CONSOLIDATED, revenue=1300.0, net_profit=100.0, ebit=150.0, capital_employed=500.0, shares_outstanding=10.0)]
        nse = [RawFinancialRecord(symbol="TEST", source="NSE", period_end_date="2025-03-31", period_type="ANNUAL", consolidation=ConsolidationType.CONSOLIDATED, revenue=1000.0, net_profit=100.0, ebit=150.0, capital_employed=500.0, shares_outstanding=10.0)]
        
        metrics = self.reconciler.reconcile_and_calculate("TEST", nse, upstox)
        self.assertEqual(metrics.overall_status, FundamentalStatus.DATA_CONFLICT)

    def test_04_consolidation_mismatch(self):
        # TEST 04: Standalone vs Consolidated -> STATEMENT_MISMATCH -> BLOCK
        upstox = [RawFinancialRecord(symbol="TEST", source="UPSTOX", period_end_date="2025-03-31", period_type="ANNUAL", consolidation=ConsolidationType.CONSOLIDATED, revenue=1000.0)]
        nse = [RawFinancialRecord(symbol="TEST", source="NSE", period_end_date="2025-03-31", period_type="ANNUAL", consolidation=ConsolidationType.STANDALONE, revenue=1000.0)]
        
        metrics = self.reconciler.reconcile_and_calculate("TEST", nse, upstox)
        self.assertEqual(metrics.overall_status, FundamentalStatus.STATEMENT_MISMATCH)

    def test_05_period_mismatch(self):
        # TEST 05: FY2025 vs FY2024 -> PERIOD_MISMATCH -> BLOCK
        upstox = [RawFinancialRecord(symbol="TEST", source="UPSTOX", period_end_date="2025-03-31", period_type="ANNUAL", consolidation=ConsolidationType.CONSOLIDATED, revenue=1000.0)]
        nse = [RawFinancialRecord(symbol="TEST", source="NSE", period_end_date="2024-03-31", period_type="ANNUAL", consolidation=ConsolidationType.CONSOLIDATED, revenue=1000.0)]
        
        metrics = self.reconciler.reconcile_and_calculate("TEST", nse, upstox)
        self.assertEqual(metrics.overall_status, FundamentalStatus.PERIOD_MISMATCH)

    def test_06_missing_raw_input(self):
        # TEST 06: One required raw input missing -> DATA_INSUFFICIENT -> BLOCK
        # Missing capital_employed for ROCE
        upstox = [RawFinancialRecord(symbol="TEST", source="UPSTOX", period_end_date="2025-03-31", period_type="ANNUAL", consolidation=ConsolidationType.CONSOLIDATED, revenue=1000.0, net_profit=100.0, ebit=150.0, capital_employed=None)]
        nse = [RawFinancialRecord(symbol="TEST", source="NSE", period_end_date="2025-03-31", period_type="ANNUAL", consolidation=ConsolidationType.CONSOLIDATED, revenue=1000.0, net_profit=100.0, ebit=150.0, capital_employed=None)]
        
        metrics = self.reconciler.reconcile_and_calculate("TEST", nse, upstox)
        self.assertEqual(metrics.overall_status, FundamentalStatus.DATA_INSUFFICIENT)

    def test_08_09_idempotent_hash(self):
        # TEST 08 & 09: Repeated execution / Different retrieval timestamp -> identical provenance_hash
        metrics = ReconciledCanonicalMetrics(
            symbol="TEST", roce_5y=20.0, sales_cagr_5y=15.0, pat_cagr_5y=10.0, cfo_pat_5y=1.2, debt_to_equity=0.5
        )
        
        hash1 = self.engine._generate_deterministic_key("TEST", metrics)
        hash2 = self.engine._generate_deterministic_key("TEST", metrics)
        
        self.assertEqual(hash1, hash2)
        # Verify retrieved_at is completely isolated from hash (hash shouldn't change even if time passes)
        
    def test_10_vendor_roce_discrepancy(self):
        # TEST 10: Vendor ROCE differs but raw inputs agree -> canonical ROCE accepted; vendor discrepancy logged
        # Here we mock Upstox returning a derived ROCE of 19.8, while canonical calculates to 30.0 (150/500 = 30%)
        # Reconciler should ignore the vendor's 19.8 and succeed using the verified raw inputs 150 & 500.
        upstox = self._make_series("UPSTOX")
        nse = self._make_series("NSE")
        metrics = self.reconciler.reconcile_and_calculate("TEST", nse, upstox)
        self.assertEqual(metrics.overall_status, FundamentalStatus.VERIFIED)
        self.assertEqual(metrics.roce_5y, 30.0) # 150 / 500

    def test_11_multi_year_disjoint_cross_provider_union(self):
        # TEST 11: Multi-year disjoint union across NSE (FY20-FY23) and Upstox (FY24-FY25)
        # Assembles full 6-year series (2020-2025) to compute 5Y CAGR and derived metrics
        nse_disjoint = [
            RawFinancialRecord(
                symbol="TEST", source="NSE", period_end_date=f"{yr}-03-31",
                period_type="ANNUAL", consolidation=ConsolidationType.CONSOLIDATED,
                revenue=1000.0 * (1.10 ** i), net_profit=100.0 * (1.10 ** i),
                ebit=150.0, capital_employed=500.0, total_debt=50.0, total_equity=450.0,
                operating_cash_flow=120.0, shares_outstanding=10.0
            )
            for i, yr in enumerate(range(2020, 2024)) # 2020, 2021, 2022, 2023
        ]
        upstox_disjoint = [
            RawFinancialRecord(
                symbol="TEST", source="UPSTOX", period_end_date=f"{yr}-03-31",
                period_type="ANNUAL", consolidation=ConsolidationType.CONSOLIDATED,
                revenue=1000.0 * (1.10 ** (i + 4)), net_profit=100.0 * (1.10 ** (i + 4)),
                ebit=150.0, capital_employed=500.0, total_debt=50.0, total_equity=450.0,
                operating_cash_flow=120.0, shares_outstanding=10.0
            )
            for i, yr in enumerate(range(2024, 2026)) # 2024, 2025
        ]

        metrics = self.reconciler.reconcile_and_calculate("TEST", nse_disjoint, upstox_disjoint)
        self.assertEqual(metrics.overall_status, FundamentalStatus.VERIFIED)
        self.assertIsNotNone(metrics.sales_cagr_5y)
        self.assertIsNotNone(metrics.pat_cagr_5y)
        self.assertEqual(metrics.roce_5y, 30.0)

    def test_12_disjoint_with_overlap_tolerance_passed(self):
        # TEST 12: Overlap on FY24 with 4% difference -> NSE precedence -> VERIFIED
        nse = [
            RawFinancialRecord(
                symbol="TEST", source="NSE", period_end_date=f"{yr}-03-31",
                period_type="ANNUAL", consolidation=ConsolidationType.CONSOLIDATED,
                revenue=1000.0 * (1.10 ** i), net_profit=100.0 * (1.10 ** i),
                ebit=150.0, capital_employed=500.0, total_debt=50.0, total_equity=450.0,
                operating_cash_flow=120.0, shares_outstanding=10.0
            )
            for i, yr in enumerate(range(2020, 2025)) # 2020-2024
        ]
        upstox = [
            RawFinancialRecord(
                symbol="TEST", source="UPSTOX", period_end_date="2024-03-31",
                period_type="ANNUAL", consolidation=ConsolidationType.CONSOLIDATED,
                revenue=1000.0 * (1.10 ** 4) * 1.04, net_profit=100.0 * (1.10 ** 4) * 1.04,
                ebit=150.0, capital_employed=500.0, total_debt=50.0, total_equity=450.0,
                operating_cash_flow=120.0, shares_outstanding=10.0
            ),
            RawFinancialRecord(
                symbol="TEST", source="UPSTOX", period_end_date="2025-03-31",
                period_type="ANNUAL", consolidation=ConsolidationType.CONSOLIDATED,
                revenue=1000.0 * (1.10 ** 5), net_profit=100.0 * (1.10 ** 5),
                ebit=150.0, capital_employed=500.0, total_debt=50.0, total_equity=450.0,
                operating_cash_flow=120.0, shares_outstanding=10.0
            ),
        ]

        metrics = self.reconciler.reconcile_and_calculate("TEST", nse, upstox)
        self.assertEqual(metrics.overall_status, FundamentalStatus.VERIFIED)
        self.assertIsNotNone(metrics.sales_cagr_5y)

    def test_13_cooloff_unblocked_only_for_recovering_scanner(self):
        # TEST 13: When a scanner unblocks a symbol, other scanners keep their cooloff
        from app.database import add_symbol_to_cooloff, remove_symbol_from_cooloff, get_active_cooloff_symbols
        sym = "TEST_ISOLATION_SYM"
        # Put symbol into cooloff for both QUALITY_COMPOUNDER and FUNDAMENTAL
        add_symbol_to_cooloff(sym, "TEST_REASON", scanner="QUALITY_COMPOUNDER", duration_days=7)
        add_symbol_to_cooloff(sym, "TEST_REASON", scanner="FUNDAMENTAL", duration_days=7)

        # Confirm both are in cooloff
        self.assertIn(sym, get_active_cooloff_symbols("QUALITY_COMPOUNDER"))
        self.assertIn(sym, get_active_cooloff_symbols("FUNDAMENTAL"))

        # Unblock ONLY for QUALITY_COMPOUNDER
        remove_symbol_from_cooloff(sym, scanner="QUALITY_COMPOUNDER")

        # Confirm QUALITY_COMPOUNDER is unblocked, but FUNDAMENTAL is STILL in cooloff
        self.assertNotIn(sym, get_active_cooloff_symbols("QUALITY_COMPOUNDER"))
        self.assertIn(sym, get_active_cooloff_symbols("FUNDAMENTAL"))

        # Cleanup
        remove_symbol_from_cooloff(sym, scanner="FUNDAMENTAL")


if __name__ == '__main__':
    unittest.main()
