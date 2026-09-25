import unittest
import numpy as np
import pandas as pd
from app.multitf.consolidation import prepare_15m_context, detect_15m_consolidation


class TestConsolidationNaTHandling(unittest.TestCase):
    """
    Test suite verifying prepare_15m_context handles NaT timestamps and NaN values
    without raising 'cannot convert float NaN to integer'.
    """

    def setUp(self):
        self.config = {
            "MIN_CONSOLIDATION_BARS": 6,
            "GAP_PCT_THRESHOLD": 0.020,
            "GAP_ATR_MULT": 2.0,
        }

    def test_prepare_15m_context_with_nat_index(self):
        """Verify prepare_15m_context strips NaT timestamps in DatetimeIndex without error."""
        dates = pd.date_range("2026-09-25 09:15", periods=20, freq="15min")
        dates_list = list(dates)
        dates_list[-1] = pd.NaT  # Corrupt trailing timestamp

        df = pd.DataFrame({
            "Open": np.linspace(100, 105, 20),
            "High": np.linspace(101, 106, 20),
            "Low": np.linspace(99, 104, 20),
            "Close": np.linspace(100.5, 105.5, 20),
            "Volume": np.full(20, 1000.0)
        }, index=pd.DatetimeIndex(dates_list))

        ctx = prepare_15m_context(df, atr_15m=2.0, config=self.config, symbol="TEST_NAT")
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx.symbol, "TEST_NAT")
        self.assertFalse(np.isnan(ctx.minutes_of_session[-1]))

    def test_prepare_15m_context_with_nat_datetime_column(self):
        """Verify prepare_15m_context handles NaT values in Datetime column."""
        dates = list(pd.date_range("2026-09-25 09:15", periods=15, freq="15min"))
        dates[5] = pd.NaT  # Middle NaT

        df = pd.DataFrame({
            "Datetime": dates,
            "Open": np.linspace(100, 105, 15),
            "High": np.linspace(101, 106, 15),
            "Low": np.linspace(99, 104, 15),
            "Close": np.linspace(100.5, 105.5, 15),
            "Volume": np.full(15, 5000.0)
        })

        ctx = prepare_15m_context(df, atr_15m=1.5, config=self.config, symbol="TEST_NAT_COL")
        self.assertIsNotNone(ctx)
        self.assertEqual(len(ctx.close), 14)  # 15 - 1 NaT = 14

    def test_prepare_15m_context_with_nan_volume(self):
        """Verify prepare_15m_context handles NaN volume gracefully with nanmedian."""
        dates = pd.date_range("2026-09-25 09:15", periods=20, freq="15min")
        vols = np.full(20, 10000.0)
        vols[-1] = np.nan
        vols[0] = np.nan

        df = pd.DataFrame({
            "Open": np.linspace(100, 105, 20),
            "High": np.linspace(101, 106, 20),
            "Low": np.linspace(99, 104, 20),
            "Close": np.linspace(100.5, 105.5, 20),
            "Volume": vols
        }, index=dates)

        ctx = prepare_15m_context(df, atr_15m=2.0, config=self.config, symbol="TEST_NAN_VOL")
        self.assertIsNotNone(ctx)
        self.assertFalse(np.isnan(ctx.volume_baseline))
        self.assertFalse(np.isnan(ctx.tod_baseline))


if __name__ == "__main__":
    unittest.main()
