# =====================================================================================
# app/test_adaptive_maturity_framework.py
# VERIFICATION SUITE FOR ADAPTIVE MATURITY & HISTORY-TIERED SCANNER FRAMEWORK
# =====================================================================================
import unittest
import numpy as np
import pandas as pd
from datetime import datetime

from reversal_scanner import _evaluate_candidate, REVERSAL_MIN_BARS
from pullback_pipeline import detect_pullback_setup
from multibagger import StockPriceData, entry_confirmed
from technical_scanner import detect_technical_setup


def _generate_candles(n_bars: int = 50, start_price: float = 100.0) -> pd.DataFrame:
    """Generates synthetic daily OHLCV dataframe on business days with standard indicator columns."""
    dates = pd.date_range(start="2025-01-01", periods=n_bars, freq="B")
    data = []
    p = start_price
    for _ in range(n_bars):
        o = p
        h = p + 1.5
        l = p - 1.0
        c = p + 0.5
        v = 150_000.0
        data.append({"Date": _, "Open": o, "High": h, "Low": l, "Close": c, "Volume": v})
        p = c
    df = pd.DataFrame(data, index=dates)
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["ATR"] = 2.0
    df["RSI"] = 52.0
    df["Volume_SMA20"] = 150_000.0
    if n_bars >= 50:
        df["SMA50"] = df["Close"].rolling(50).mean()
    else:
        df["SMA50"] = np.nan
    if n_bars >= 200:
        df["SMA200"] = df["Close"].rolling(200).mean()
    else:
        df["SMA200"] = np.nan
    return df


class TestAdaptiveMaturityFramework(unittest.TestCase):
    """
    Verifies that short-history IPOs (35-49 bars) and Recent Listings (50-199 bars)
    are evaluated with replacement maturity-aware indicators rather than failing
    on missing SMA200.
    """

    def test_reversal_short_history_ipo(self):
        # 40-bar FRESH_IPO candidate (e.g. Marsons archetype with short history)
        df_ipo = _generate_candles(n_bars=40, start_price=100.0)
        df_ipo.loc[df_ipo.index[10], "High"] = 120.0
        df_ipo.loc[df_ipo.index[25], "Low"] = 90.0
        df_ipo.loc[df_ipo.index[25], "RSI"] = 28.0
        df_ipo.loc[df_ipo.index[-1], "Open"] = 98.0
        df_ipo.loc[df_ipo.index[-1], "Low"] = 97.5
        df_ipo.loc[df_ipo.index[-1], "Close"] = 100.0
        df_ipo.loc[df_ipo.index[-1], "High"] = 101.0
        df_ipo.loc[df_ipo.index[-1], "RSI"] = 42.0
        df_ipo.loc[df_ipo.index[-1], "Volume"] = 350_000.0

        res = _evaluate_candidate(
            symbol="TEST_IPO_40B",
            df=df_ipo,
            fund_data={"Category": "Growth"},
            regime_ctx={"trend": "NEUTRAL"}
        )
        self.assertNotEqual(res.get("reject_code"), "no_data", f"Rejected as no_data: {res.get('reject_reason')}")
        ctx = res.get("context", {})
        if ctx:
            self.assertEqual(ctx.get("history_class"), "FRESH_IPO")
            self.assertEqual(ctx.get("trend_validation_mode"), "EMA20")

    def test_reversal_recent_listing_100_bars(self):
        # 100-bar RECENT_LISTING candidate
        df_rec = _generate_candles(n_bars=100, start_price=100.0)
        df_rec.loc[df_rec.index[20], "High"] = 130.0
        df_rec.loc[df_rec.index[70], "Low"] = 95.0
        df_rec.loc[df_rec.index[70], "RSI"] = 26.0
        df_rec.loc[df_rec.index[-1], "Open"] = 104.0
        df_rec.loc[df_rec.index[-1], "Low"] = 103.5
        df_rec.loc[df_rec.index[-1], "Close"] = 106.0
        df_rec.loc[df_rec.index[-1], "High"] = 107.0
        df_rec.loc[df_rec.index[-1], "RSI"] = 45.0
        df_rec.loc[df_rec.index[-1], "Volume"] = 350_000.0

        res = _evaluate_candidate(
            symbol="TEST_REC_100B",
            df=df_rec,
            fund_data={"Category": "Quality"},
            regime_ctx={"trend": "BULLISH"}
        )
        self.assertNotEqual(res.get("reject_code"), "no_data")
        ctx = res.get("context", {})
        if ctx:
            self.assertEqual(ctx.get("history_class"), "RECENT_LISTING")
            self.assertEqual(ctx.get("trend_validation_mode"), "SMA50_EMA20")

    def test_pullback_history_tiered_uptrend(self):
        # 1. FRESH_IPO (30 bars): Evaluates on EMA20
        df_30 = _generate_candles(n_bars=30, start_price=100.0)
        res_30 = detect_pullback_setup("TEST_PB_30B", df_30)
        self.assertEqual(res_30.get("history_class"), "FRESH_IPO")
        self.assertEqual(res_30.get("trend_validation_mode"), "EMA20")

        # 2. RECENT_LISTING (80 bars): Evaluates on SMA50 + EMA20
        df_80 = _generate_candles(n_bars=80, start_price=100.0)
        res_80 = detect_pullback_setup("TEST_PB_80B", df_80)
        self.assertEqual(res_80.get("history_class"), "RECENT_LISTING")
        self.assertEqual(res_80.get("trend_validation_mode"), "SMA50_EMA20")

    def test_multibagger_adaptive_entry(self):
        spd_ipo = StockPriceData(
            symbol="IPO_TEST",
            price=100.0,
            change_pct=1.5,
            low_52w=80.0,
            high_52w=110.0,
            turnover_20d=5_000_000.0,
            sma_20=98.0,
            sma_50=0.0,
            sma_200=0.0,
            high_20d=105.0,
            high_60d=105.0,
            mom_3m=10.0,
            mom_6m=10.0,
            atr_14=3.0,
            ema_20=99.0,
            latest_volume=300_000.0,
            volume_sma20=100_000.0,
            close_yesterday=98.5,
            sma_200_yesterday=0.0,
            today_open=98.5,
            today_close=100.0
        )
        passed, reason = entry_confirmed(spd_ipo)
        self.assertTrue(passed, f"Multibagger IPO entry failed with reason: {reason}")


class TestLifecycleTransitionsAndBoundaries(unittest.TestCase):
    """
    Suite 11: Tier-Equivalence & Boundary Transition Tests.
    Tests boundary discontinuities:
      14 -> 15 bars: Rejected -> FRESH_IPO (LOW confidence)
      19 -> 20 bars: LOW -> STANDARD confidence
      34 -> 35 bars: Reversal Ineligible -> Reversal Eligible
      49 -> 50 bars: FRESH_IPO -> RECENT_LISTING
      199 -> 200 bars: RECENT_LISTING -> MATURE
    """

    def test_technical_scanner_boundaries(self):
        # 14 bars: Rejected
        df_14 = _generate_candles(n_bars=14)
        res_14, tr_14 = detect_technical_setup(df_14, symbol="T14", return_trace=True)
        self.assertIsNone(res_14)
        self.assertEqual(tr_14["FINAL"]["terminal_reason"], "INSUFFICIENT_BARS")

        # 20 bars: FRESH_IPO with STANDARD confidence
        df_20 = _generate_candles(n_bars=20)
        res_20, tr_20 = detect_technical_setup(df_20, symbol="T20", return_trace=True)
        self.assertEqual(tr_20["01_DATA_VALIDATION"]["history_class"], "FRESH_IPO")
        self.assertEqual(tr_20["01_DATA_VALIDATION"]["history_confidence"], "STANDARD")
        self.assertEqual(tr_20["01_DATA_VALIDATION"]["trend_validation_mode"], "EMA20")

        # 50 bars: RECENT_LISTING
        df_50 = _generate_candles(n_bars=50)
        res_50, tr_50 = detect_technical_setup(df_50, symbol="T50", return_trace=True)
        self.assertEqual(tr_50["01_DATA_VALIDATION"]["history_class"], "RECENT_LISTING")
        self.assertEqual(tr_50["01_DATA_VALIDATION"]["trend_validation_mode"], "SMA50_EMA20")

        # 200 bars: MATURE
        df_200 = _generate_candles(n_bars=200)
        res_200, tr_200 = detect_technical_setup(df_200, symbol="T200", return_trace=True)
        self.assertEqual(tr_200["01_DATA_VALIDATION"]["history_class"], "MATURE")
        self.assertEqual(tr_200["01_DATA_VALIDATION"]["trend_validation_mode"], "SMA200")

    def test_reversal_scanner_34_vs_35_boundary(self):
        # 34 bars: Rejected for Reversal (requires >= 35)
        df_34 = _generate_candles(n_bars=34)
        res_34 = _evaluate_candidate("R34", df_34)
        self.assertEqual(res_34.get("reject_code"), "no_data")
        self.assertIn("34 < 35 minimum", res_34.get("reject_reason", ""))

        # 35 bars: Valid historical floor for Reversal
        df_35 = _generate_candles(n_bars=35)
        res_35 = _evaluate_candidate("R35", df_35)
        self.assertNotEqual(res_35.get("reject_code"), "no_data")


if __name__ == "__main__":
    unittest.main()
