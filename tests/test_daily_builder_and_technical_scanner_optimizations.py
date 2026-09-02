import os
import sys
import unittest
import numpy as np
import pandas as pd

# Add app directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app')))

class TestDailyBuilderAndTechnicalScannerOptimizations(unittest.TestCase):

    def test_daily_builder_vectorized_eligibility_filter(self):
        """Verify that apply_vectorized_eligibility_filter accurately filters invalid stocks."""
        from daily_builder import apply_vectorized_eligibility_filter, MIN_PRICE, MIN_MARKET_CAP, MIN_TRADED_VALUE

        # Create synthetic test universe
        data = {
            "name": ["STOCK_VALID", "STOCK_PENNY", "STOCK_MICRO", "STOCK_ILLIQUID", "STOCK_BLACK"],
            "close": [500.0, 50.0, 300.0, 200.0, 600.0],  # STOCK_PENNY fails < 100
            "market_cap_basic": [50_000_000_000, 20_000_000_000, 500_000_000, 30_000_000_000, 40_000_000_000],  # STOCK_MICRO fails < 1000Cr
            "average_volume_30d_calc": [100_000, 200_000, 100_000, 100, 100_000],  # STOCK_ILLIQUID fails traded val
        }
        df = pd.DataFrame(data)
        blacklist = {"STOCK_BLACK"}

        eligible_df, exclusions = apply_vectorized_eligibility_filter(df, blacklist)

        # STOCK_VALID must pass
        self.assertEqual(len(eligible_df), 1)
        self.assertEqual(eligible_df["name"].iloc[0], "STOCK_VALID")

        # 4 exclusions must be logged with accurate reasons
        self.assertEqual(len(exclusions), 4)
        excl_map = {e["Stock"]: e["Reason"] for e in exclusions}
        self.assertIn("Price below minimum", excl_map["STOCK_PENNY"])
        self.assertIn("Market Cap below minimum", excl_map["STOCK_MICRO"])
        self.assertIn("Low liquidity", excl_map["STOCK_ILLIQUID"])
        self.assertIn("Promoter Blacklist", excl_map["STOCK_BLACK"])

    def test_technical_scanner_warmup_and_fast_fail(self):
        """Verify indicator warm-up and fast-fail trigger gate in technical scanner."""
        from technical_scanner import detect_technical_setup, MIN_RVOL_HARD_GATE, MIN_CLV_HARD_GATE

        # Build 150-bar synthetic OHLCV data
        np.random.seed(42)
        n = 150
        dates = pd.date_range("2026-01-01", periods=n, freq="D")
        closes = np.linspace(100, 150, n)
        highs = closes + 2.0
        lows = closes - 2.0
        opens = closes - 0.5
        volumes = np.full(n, 100_000.0)

        df = pd.DataFrame({
            "Date": dates,
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": volumes,
        })

        # 1. Test fast-reject on low volume trigger candle
        df_low_vol = df.copy()
        df_low_vol.loc[df_low_vol.index[-1], "Volume"] = 50_000.0  # RVOL < 1.20
        res = detect_technical_setup(df_low_vol, "TEST_STOCK")
        self.assertIsNone(res, "Should reject trigger candle with RVOL < 1.20")

        # 2. Test fast-reject on bearish close (close <= open)
        df_bearish = df.copy()
        df_bearish.loc[df_bearish.index[-1], "Close"] = df_bearish.loc[df_bearish.index[-1], "Open"] - 1.0
        res = detect_technical_setup(df_bearish, "TEST_STOCK")
        self.assertIsNone(res, "Should reject bearish candle")

if __name__ == "__main__":
    unittest.main()
