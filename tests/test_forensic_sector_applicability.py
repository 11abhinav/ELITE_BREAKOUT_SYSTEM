"""
Unit Test Suite for Sector-Aware Forensic Risk Engine:
1. Bank (AUBANK / generic bank) with low/synthetic CFO/PAT is NOT rejected solely for that metric.
2. Industrial company with genuinely bad CFO/PAT (< 0.60) is strictly rejected.
3. NBFC / Financial institution follows financial sector framework and qualifies for growth mode.
4. Universal forensic red flags (>= 2) trigger hard REJECT regardless of sector.
5. Financial institution with severe negative ROA (< 0.0%) is rejected.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from forensic_engine import ForensicEngine, ForensicRiskTier


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


if __name__ == "__main__":
    unittest.main()
