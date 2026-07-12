import unittest
import pandas as pd
from unittest.mock import patch, MagicMock

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from daily_builder import _classify_nonfin, _classify_fin, _score_nonfin, _score_fin, MIN_PROMOTER_MCAP

class TestDailyBuilderFixes(unittest.TestCase):

    def setUp(self):
        # Construct mock row representing a healthy compounder with missing QoQ data
        self.mock_nonfin_row = pd.Series({
            "name": "RELIANCE",
            "sector": "Energy",
            "close": 2400.0,
            "average_volume_30d_calc": 1000000.0,
            "market_cap_basic": 16000000000000.0, # 16 Lac Cr
            "return_on_equity_fy": 16.0,
            "operating_margin": 12.0,
            "debt_to_equity_fq": 0.4,
            "return_on_assets_fq": 1.5,
            "return_on_invested_capital_fq": 14.0,
            "earnings_per_share_basic_ttm": 120.0,
            "gross_profit_yoy_growth_ttm": 12.0,
            "gross_profit_qoq_growth_fq": None, # Missing QoQ
            "earnings_per_share_diluted_yoy_growth_ttm": 15.0,
            "earnings_per_share_diluted_qoq_growth_fq": None, # Missing QoQ
            "total_revenue_yoy_growth_ttm": 11.0,
            "total_revenue_qoq_growth_fq": None, # Missing QoQ
            "net_income_yoy_growth_ttm": 14.0,
            "net_income_qoq_growth_fq": None, # Missing QoQ
            "price_earnings_ttm": 25.0,
            "price_book_ratio": 2.5,
            "total_revenue_5y_growth": 12.0,
            "earnings_per_share_basic_5y_growth": 14.0,
            "free_cash_flow_margin_ttm": 8.0,
            "dividend_yield_recent": 0.5,
            "float_shares_outstanding": 3000000000.0, # Float = 3B shares
            "total_shares_outstanding_fundamental": 6600000000.0, # Total = 6.6B (Insiders/Promoters = 3.6B)
        })

        # Construct a healthy financial row with missing QoQ data
        self.mock_fin_row = pd.Series({
            "name": "HDFCBANK",
            "sector": "Banks",
            "close": 1500.0,
            "average_volume_30d_calc": 2000000.0,
            "market_cap_basic": 11000000000000.0, # 11 Lac Cr
            "return_on_equity_fy": 17.0,
            "return_on_assets_fq": 2.1,
            "operating_margin": None, # OPM is naturally None for Banks in TV
            "debt_to_equity_fq": 0.0, # Banks debt is cash deposits, typically represented as 0 in fundamental D/E
            "return_on_invested_capital_fq": 12.0,
            "earnings_per_share_basic_ttm": 80.0,
            "gross_profit_yoy_growth_ttm": 14.0,
            "gross_profit_qoq_growth_fq": None,
            "earnings_per_share_diluted_yoy_growth_ttm": 18.0,
            "earnings_per_share_diluted_qoq_growth_fq": None,
            "total_revenue_yoy_growth_ttm": 15.0,
            "total_revenue_qoq_growth_fq": None,
            "net_income_yoy_growth_ttm": 18.0,
            "net_income_qoq_growth_fq": None,
            "price_earnings_ttm": 18.0,
            "price_book_ratio": 2.8,
            "total_revenue_5y_growth": 14.0,
            "earnings_per_share_basic_5y_growth": 16.0,
            "free_cash_flow_margin_ttm": 12.0,
            "dividend_yield_recent": 1.2,
            "float_shares_outstanding": 2000000000.0,
            "total_shares_outstanding_fundamental": 7300000000.0,
        })

    @patch('fundamentals_cache.get_fundamentals')
    def test_missing_qoq_does_not_exclude_nonfin(self, mock_get_fundamentals):
        """Test that missing QoQ data does not hard-exclude non-financial stocks."""
        mock_get_fundamentals.return_value = {
            "cfo_pat_ratio": 0.8,
            "retention_ratio": 0.7,
            "insider_hold": 0.55,
            "forensic_flags": 0
        }
        res = _classify_nonfin(self.mock_nonfin_row, "RELIANCE")
        
        self.assertIsNotNone(res)
        self.assertIn("Wealth Compounder", res["Category"])
        self.assertEqual(res["Stock"], "RELIANCE")
        self.assertIsNone(res["QOQ Revenue %"]) # Assert that None QoQ is accepted and handled
        print("✓ Test passed: Non-financial path handles missing QoQ data gracefully.")

    @patch('fundamentals_cache.get_fundamentals')
    def test_missing_qoq_does_not_exclude_fin(self, mock_get_fundamentals):
        """Test that missing QoQ data does not hard-exclude financial stocks."""
        mock_get_fundamentals.return_value = {
            "insider_hold": 0.60,
            "forensic_flags": 0
        }
        res = _classify_fin(self.mock_fin_row, "HDFCBANK")
        
        self.assertIsNotNone(res)
        self.assertIn("Top Bank/NBFC", res["Category"])
        self.assertEqual(res["Stock"], "HDFCBANK")
        self.assertIsNone(res["QOQ Profit %"])
        print("✓ Test passed: Financial path handles missing QoQ data gracefully.")

    @patch('fundamentals_cache.get_fundamentals')
    def test_promoter_mcap_junk_gate_blocks_shell(self, mock_get_fundamentals):
        """Test that shell companies with promoter market caps under ₹500 Cr are blocked."""
        mock_get_fundamentals.return_value = {
            "cfo_pat_ratio": 0.8,
            "retention_ratio": 0.7,
            "insider_hold": 0.55,
            "forensic_flags": 0
        }
        
        # Modify row to mimic small shell company: Market Cap = ₹800 Cr, but float is 98% (only 2% promoter holding)
        # Promoter MCAP = 2% of ₹800 Cr = ₹16 Cr (which is under the ₹500 Cr threshold)
        shell_row = self.mock_nonfin_row.copy()
        shell_row["market_cap_basic"] = 8_000_000_000.0  # ₹800 Cr
        shell_row["float_shares_outstanding"] = 98_000_000.0
        shell_row["total_shares_outstanding_fundamental"] = 100_000_000.0
        
        res = _classify_nonfin(shell_row, "SHELLSTK")
        self.assertIsNone(res)  # Should return None (skipped/excluded)
        print("✓ Test passed: Shell companies are successfully blocked by the Promoter Market Cap junk gate.")

    def test_financial_path_score_deduplication(self):
        """Test that financial path score does not double-count yoy_margin."""
        # Test base score calculation directly
        # If we pass yoy_margin = True, we should verify it does not receive +5 points a second time.
        score = _score_fin(
            yoy_rev=20.0, yoy_profit=30.0, qoq_rev=10.0, qoq_profit=10.0,
            roe=16.0, roa=1.2, yoy_margin=True, fin_mature=False, fin_compounder=False
        )
        
        # Verify the calculation:
        # yoy_profit >= 25 -> 20 pts
        # yoy_rev >= 15 -> 10 pts
        # yoy_margin (line 711) -> 15 pts
        # roe >= 15 -> 10 pts
        # roa >= 1.0 -> 5 pts
        # yoy_margin (line 719 - REMOVED) -> 0 pts
        # Total expected score: 20 + 10 + 15 + 10 + 5 = 60
        self.assertEqual(score, 60)
        print("✓ Test passed: Financial path scoring does not double-count YoY margin expansion.")

    def test_yfinance_symbol_normalization(self):
        """Test that YFinanceFetcher correctly resolves both NSE and BSE symbols."""
        from data_provider import YFinanceFetcher
        fetcher = YFinanceFetcher()
        
        # Test NSE cases
        self.assertEqual(fetcher._normalize_symbol("RELIANCE"), "RELIANCE.NS")
        self.assertEqual(fetcher._normalize_symbol("TCS.NS"), "TCS.NS")
        self.assertEqual(fetcher._normalize_symbol("NSE:INFY"), "INFY.NS")
        
        # Test BSE cases
        self.assertEqual(fetcher._normalize_symbol("500209"), "500209.BO")
        self.assertEqual(fetcher._normalize_symbol("BSE:500209"), "500209.BO")
        self.assertEqual(fetcher._normalize_symbol("RELIANCE.BO"), "RELIANCE.BO")
        
        # Test ampersand fixes
        self.assertEqual(fetcher._normalize_symbol("M_M"), "M&M.NS")
        self.assertEqual(fetcher._normalize_symbol("M-M"), "M&M.NS")
        
        # Test index case
        self.assertEqual(fetcher._normalize_symbol("^NSEI"), "^NSEI")
        print("✓ Test passed: YFinanceFetcher symbol normalization correctly handles NSE, BSE, indices, and ampersands.")

    def test_service_sector_altman_z_score(self):
        """Test that Altman Z-score utilizes service sector solvent thresholds (1.10) for IT/Service companies."""
        from multibagger import passes_multibagger_quality_gate
        
        # Service stock template: Altman Z is 1.5 (which is below 1.8 manufacturing threshold, but above 1.10 service threshold)
        service_stock = {
            "is_financial": False,
            "sector": "Technology",
            "roce": 0.25,
            "revenue_cagr_3y": 0.15,
            "operating_margin_ttm": 0.20,
            "fcf_margin": 0.15,
            "cfo_pat_ratio": 0.90,
            "debt_equity": 0.10,
            "interest_coverage_ratio": 15.0,
            "altman_z": 1.5,  # Grey zone for service, but solvent
            "promoter_pledge_pct": 0.0,
            "auditor_flags": False
        }
        
        passed, reason = passes_multibagger_quality_gate(service_stock)
        self.assertTrue(passed, f"Service stock should pass Altman Z gate: {reason}")
        
        # Manufacturing stock template: Altman Z is 1.5 (which is below 1.8 manufacturing threshold)
        mfg_stock = service_stock.copy()
        mfg_stock["sector"] = "Basic Materials"  # Manufacturing/Asset-heavy
        
        passed, reason = passes_multibagger_quality_gate(mfg_stock)
        self.assertFalse(passed, "Manufacturing stock should fail Altman Z gate when Z < 1.8")
        print("✓ Test passed: Altman Z-score correctly uses service sector solvent thresholds vs manufacturing.")

    def test_reversal_macd_normalization(self):
        """Test that MACD momentum scoring is normalized by close price to prevent large-cap bias."""
        from reversal_scanner import _score_reversal
        
        # High priced stock (price = 10,000) with MACD hist = 5.0 (5 bps = 0.05% of price). Expecting moderate score.
        score_high = _score_reversal(
            vol_ratio=2.0, drop_pct=30.0, current_rsi=35.0, past_10_rsi_min=20.0,
            macd_hist=5.0, pct_below_sma200=5.0, category="Wealth Compounder", rr_ratio=3.0,
            above_sma50=True, above_sma200=True, obv_trend=1, delivery_pct=40.0,
            close_price=10000.0
        )
        
        # Low priced stock (price = 100) with MACD hist = 0.05 (5 bps = 0.05% of price). Should receive identical score!
        score_low = _score_reversal(
            vol_ratio=2.0, drop_pct=30.0, current_rsi=35.0, past_10_rsi_min=20.0,
            macd_hist=0.05, pct_below_sma200=5.0, category="Wealth Compounder", rr_ratio=3.0,
            above_sma50=True, above_sma200=True, obv_trend=1, delivery_pct=40.0,
            close_price=100.0
        )
        
        self.assertEqual(score_high, score_low, f"Large cap and small cap with identical relative MACD should score equally ({score_high} vs {score_low})")
        print("✓ Test passed: MACD scoring correctly normalizes by close price to eliminate large-cap bias.")

    def test_reversal_soft_sma50_score(self):
        """Test that soft SMA50 pass awards 10 trend points instead of 0."""
        from reversal_scanner import _score_reversal
        
        score_soft = _score_reversal(
            vol_ratio=2.0, drop_pct=30.0, current_rsi=35.0, past_10_rsi_min=20.0,
            macd_hist=1.0, pct_below_sma200=5.0, category="Wealth Compounder", rr_ratio=3.0,
            above_sma50=False, above_sma200=False, obv_trend=1, delivery_pct=40.0,
            close_price=100.0
        )
        
        score_no_sma = _score_reversal(
            vol_ratio=2.0, drop_pct=30.0, current_rsi=35.0, past_10_rsi_min=20.0,
            macd_hist=1.0, pct_below_sma200=5.0, category="Wealth Compounder", rr_ratio=3.0,
            above_sma50=None, above_sma200=False, obv_trend=1, delivery_pct=40.0,
            close_price=100.0
        )
        
        # Soft pass (above_sma50=False) should score 10 points higher than no SMA pass (above_sma50=None)
        self.assertEqual(score_soft - score_no_sma, 10)
        print("✓ Test passed: Soft SMA50 pass correctly awards 10 trend score points.")

if __name__ == '__main__':
    unittest.main()
