"""
Unit Test Suite for Sector-Aware Forensic Risk Engine & Hardening:
1. Bank (AUBANK / generic bank) with low/synthetic CFO/PAT is NOT rejected solely for that metric.
2. Industrial company with genuinely bad CFO/PAT (< 0.60) is strictly rejected.
3. NBFC / Financial institution follows financial sector framework and qualifies for growth mode.
4. Universal forensic red flags (>= 2) trigger hard REJECT regardless of sector.
5. Financial institution with severe negative ROA (< 0.0%) is rejected.
6. Stale data regression test: Financial stock with legacy cfo_pat=0.50 evaluates to SECTOR_EXEMPT, not REJECT.
7. Non-financial with cfo_pat=0.45 strictly rejected.
8. Universe checklist runtime sector routing guard raises RuntimeError when financial path is routed to nonfin junk gates.
9. Canonical sector classification consistency across ForensicEngine, daily_builder, and fundamentals_cache.
10. Known financial subsectors (NBFC, Insurance, AMC) are explicitly SECTOR_EXEMPT.
11. Unknown / Unclassified entities do NOT get automatic exemption (CFO/PAT applies).
12. Sector/Path disagreement deterministic resolution.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from forensic_engine import ForensicEngine, ForensicRiskTier
from universe_checklist import UniverseChecklist, _add_nonfin_junk_gates


class TestForensicSectorApplicability(unittest.TestCase):

    def test_01_bank_cfo_pat_exemption(self):
        """Verify banks/financials are exempt from industrial CFO/PAT gate."""
        aubank_fund = {
            "symbol": "AUBANK",
            "Path": "Financial",
            "Sector": "Finance",
            "categories": ["Financial Recovery"],
            "roe": 14.22,
            "roa": 1.60,
            "cfo_pat": 0.50,         # Synthetic or volatile cash flow from deposits/lending
            "cfo_pat_3y": 0.50,
            "revenue_cagr_3y": 0.1496,
            "forensic_flags": 0
        }
        res = ForensicEngine.evaluate_symbol(aubank_fund)
        
        self.assertNotEqual(res["forensic_risk_tier"], ForensicRiskTier.REJECT,
                            "Bank must NOT be rejected due to industrial CFO/PAT metric")
        self.assertEqual(res["forensic_risk_tier"], ForensicRiskTier.LOW)
        self.assertEqual(res["forensic_details"]["cfo_pat_status"], "SECTOR_EXEMPT")
        self.assertEqual(res["forensic_details"]["is_financial"], True)

    def test_02_industrial_bad_cfo_pat_rejected(self):
        """Verify non-financial industrial company with CFO/PAT < 0.60 is strictly REJECTED."""
        industrial_bad_cfo = {
            "symbol": "WEAK_EARNINGS_LTD",
            "Path": "Non-Financial",
            "Sector": "Capital Goods",
            "categories": ["Wealth Compounder"],
            "roe": 18.0,
            "roce": 20.0,
            "cfo_pat": 0.42,         # Genuinely poor earnings quality
            "cfo_pat_3y": 0.42,
            "revenue_cagr_3y": 0.15,
            "forensic_flags": 0
        }
        res = ForensicEngine.evaluate_symbol(industrial_bad_cfo)
        
        self.assertEqual(res["forensic_risk_tier"], ForensicRiskTier.REJECT,
                         "Non-financial company with CFO/PAT < 0.60 MUST be rejected")
        self.assertEqual(res["forensic_details"]["cfo_pat_status"], "HARD_REJECT")
        self.assertIn("0.42", res["forensic_details"]["reason"])

    def test_03_nbfc_growth_mode_and_low_risk(self):
        """Verify NBFC follows financial framework and properly qualifies for Growth Mode."""
        nbfc_fund = {
            "symbol": "FAST_NBFC",
            "Path": "Financial",
            "Sector": "Financial Services",
            "categories": ["Fast Growing Financial"],
            "roe": 18.5,
            "roa": 2.4,
            "revenue_cagr_3y": 0.22,
            "forensic_flags": 0
        }
        res = ForensicEngine.evaluate_symbol(nbfc_fund)
        
        self.assertEqual(res["forensic_risk_tier"], ForensicRiskTier.LOW)
        self.assertTrue(res["growth_investment_mode"], "Strong NBFC must qualify for Growth Investment Mode")
        self.assertGreaterEqual(res["growth_investment_score"], 60)
        self.assertEqual(res["forensic_details"]["is_financial"], True)

    def test_04_universal_forensic_red_flags_rejection(self):
        """Verify forensic_flags >= 2 triggers hard REJECT across both sectors."""
        # Financial with auditor flags
        fin_bad_flags = {
            "symbol": "DODGY_BANK",
            "Path": "Financial",
            "Sector": "Banking",
            "roe": 16.0,
            "roa": 1.5,
            "forensic_flags": 2
        }
        res_fin = ForensicEngine.evaluate_symbol(fin_bad_flags)
        self.assertEqual(res_fin["forensic_risk_tier"], ForensicRiskTier.REJECT)
        self.assertIn("Forensic red flags", res_fin["forensic_details"]["reason"])

        # Non-Financial with auditor flags
        nonfin_bad_flags = {
            "symbol": "DODGY_IND",
            "Path": "Non-Financial",
            "Sector": "Automobile",
            "roe": 16.0,
            "roce": 18.0,
            "cfo_pat_3y": 0.95,
            "forensic_flags": 3
        }
        res_nonfin = ForensicEngine.evaluate_symbol(nonfin_bad_flags)
        self.assertEqual(res_nonfin["forensic_risk_tier"], ForensicRiskTier.REJECT)
        self.assertIn("Forensic red flags", res_nonfin["forensic_details"]["reason"])

    def test_05_financial_negative_roa_rejection(self):
        """Verify financial institution with negative ROA is rejected due to severe asset erosion."""
        failing_bank = {
            "symbol": "FAILING_BANK",
            "Path": "Financial",
            "Sector": "Finance",
            "roe": -12.0,
            "roa": -1.8,             # Severe capital destruction
            "forensic_flags": 0
        }
        res = ForensicEngine.evaluate_symbol(failing_bank)
        self.assertEqual(res["forensic_risk_tier"], ForensicRiskTier.REJECT)
        self.assertIn("Negative ROA", res["forensic_details"]["reason"])

    def test_06_stale_data_financial_with_low_cfo_pat_is_sector_exempt(self):
        """Stale Data Regression: A financial stock with legacy/stale cfo_pat=0.50 is SECTOR_EXEMPT, not REJECT."""
        stale_fin_row = {
            "symbol": "AUBANK",
            "Sector": "Finance",
            "cfo_pat": 0.50,
            "cfo_pat_3y": 0.50,
            "roe": 14.22,
            "roa": 1.60,
            "revenue_cagr_3y": 0.15,
            "forensic_flags": 0
        }
        res = ForensicEngine.evaluate_symbol(stale_fin_row)
        self.assertNotEqual(res["forensic_risk_tier"], ForensicRiskTier.REJECT,
                            "Stale financial row must NOT be rejected by forensic engine")
        self.assertEqual(res["forensic_risk_tier"], ForensicRiskTier.LOW)
        self.assertEqual(res["forensic_details"]["cfo_pat_status"], "SECTOR_EXEMPT")

    def test_07_non_financial_with_0_45_cfo_pat_is_rejected(self):
        """Verify non-financial with cfo_pat=0.45 is strictly REJECTED."""
        nonfin_bad = {
            "symbol": "POOR_CASHFLOW_IND",
            "Sector": "Capital Goods",
            "cfo_pat": 0.45,
            "cfo_pat_3y": 0.45,
            "roe": 15.0,
            "roce": 16.0,
            "revenue_cagr_3y": 0.12,
            "forensic_flags": 0
        }
        res = ForensicEngine.evaluate_symbol(nonfin_bad)
        self.assertEqual(res["forensic_risk_tier"], ForensicRiskTier.REJECT)
        self.assertEqual(res["forensic_details"]["cfo_pat_status"], "HARD_REJECT")

    def test_08_universe_checklist_routing_guard_raises_runtime_error(self):
        """Verify _add_nonfin_junk_gates raises RuntimeError for financial paths."""
        cl = UniverseChecklist(symbol="TEST_FIN", path="BANK")
        for fin_path in ("BANK", "NBFC_HFC", "INSURANCE", "AMC"):
            with self.assertRaises(RuntimeError) as ctx:
                _add_nonfin_junk_gates(cl, {}, roe=15.0, path=fin_path)
            self.assertIn("sector routing violation", str(ctx.exception))

    def test_09_canonical_sector_classification_consistency(self):
        """Verify ForensicEngine correctly identifies all canonical financial variations."""
        fin_cases = [
            {"Path": "Financial"},
            {"path": "bank"},
            {"financial_sub_path": "NBFC_HFC"},
            {"Sector": "Finance"},
            {"sector": "Financial Services"},
            {"sector": "Banks"},
            {"Industry": "Major Banks"},
            {"industry": "Life/Health Insurance"},
            {"categories": ["Financial Recovery"]},
        ]
        for case in fin_cases:
            self.assertTrue(ForensicEngine.is_financial_institution(case), f"Failed for case: {case}")

        nonfin_cases = [
            {"Path": "Non-Financial", "Sector": "Technology", "Industry": "Software"},
            {"Sector": "Capital Goods", "Industry": "Machinery"},
            {"Sector": "Health Technology", "categories": ["Wealth Compounder"]},
        ]
        for case in nonfin_cases:
            self.assertFalse(ForensicEngine.is_financial_institution(case), f"Failed for case: {case}")

    def test_10_known_financial_subsectors_are_exempt(self):
        """Verify known financial subsectors (NBFC, Insurance, AMC) receive SECTOR_EXEMPT."""
        subsectors = [
            {"symbol": "NBFC_CO", "financial_sub_path": "NBFC_HFC", "Sector": "Financial Services", "cfo_pat": 0.30, "roa": 2.0},
            {"symbol": "INS_CO", "financial_sub_path": "INSURANCE", "Sector": "Insurance", "cfo_pat": 0.25, "roa": 1.8},
            {"symbol": "AMC_CO", "financial_sub_path": "AMC", "Sector": "Financial Services", "cfo_pat": 0.40, "roa": 3.5},
        ]
        for sub in subsectors:
            res = ForensicEngine.evaluate_symbol(sub)
            self.assertEqual(res["forensic_risk_tier"], ForensicRiskTier.LOW, f"Failed for {sub['symbol']}")
            self.assertEqual(res["forensic_details"]["cfo_pat_status"], "SECTOR_EXEMPT")

    def test_11_unclassified_unknown_does_not_get_automatic_exemption(self):
        """Verify unclassified/unknown sector does NOT get automatic financial exemption."""
        unclassified_stock = {
            "symbol": "UNKNOWN_CORP",
            "Path": "FINANCIAL_UNCLASSIFIED",
            "Sector": "Unknown Sector",
            "cfo_pat": 0.45,
            "cfo_pat_3y": 0.45,
            "roe": 12.0,
            "forensic_flags": 0
        }
        res = ForensicEngine.evaluate_symbol(unclassified_stock)
        self.assertEqual(res["forensic_risk_tier"], ForensicRiskTier.REJECT,
                         "Unclassified entity must not bypass CFO/PAT gate")
        self.assertEqual(res["forensic_details"]["cfo_pat_status"], "HARD_REJECT")

    def test_12_sector_path_disagreement_deterministic_handling(self):
        """Verify deterministic handling when path and sector disagree."""
        # Case A: path says BANK but sector says Industrial -> resolves by explicit path as KNOWN_FINANCIAL
        case_a = {
            "symbol": "CONFLICT_A",
            "Path": "BANK",
            "Sector": "Capital Goods",
            "cfo_pat": 0.35,
            "roa": 1.5,
            "forensic_flags": 0
        }
        res_a = ForensicEngine.evaluate_symbol(case_a)
        self.assertEqual(res_a["forensic_risk_tier"], ForensicRiskTier.LOW)
        self.assertEqual(res_a["forensic_details"]["cfo_pat_status"], "SECTOR_EXEMPT")

        # Case B: path says Non-Financial but sector says Finance -> resolves by explicit path as NON_FINANCIAL
        case_b = {
            "symbol": "CONFLICT_B",
            "Path": "Non-Financial",
            "Sector": "Finance",
            "cfo_pat": 0.35,
            "cfo_pat_3y": 0.35,
            "forensic_flags": 0
        }
        res_b = ForensicEngine.evaluate_symbol(case_b)
        self.assertEqual(res_b["forensic_risk_tier"], ForensicRiskTier.REJECT)
        self.assertEqual(res_b["forensic_details"]["cfo_pat_status"], "HARD_REJECT")


if __name__ == "__main__":
    unittest.main()
