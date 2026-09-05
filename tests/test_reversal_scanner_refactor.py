import unittest
import numpy as np
import pandas as pd
import sys
import os

# Prevent remote DB pool checkout during offline unit tests
os.environ["DATABASE_URL"] = ""

# Add app and root to path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT_DIR, "app"))

from reversal_scanner import _evaluate_candidate, _score_reversal, _check_swing_structure
from config import REVERSAL_CONFIG


def make_reversal_df(
    n_bars=200,
    base_price=600.0,
    high_52w=650.0,  # ~23% drop
    current_price=500.0,
    current_low=495.0,
    current_high=508.0,
    trough_price=470.0,
    trough_bar_ago=12,
    vol_ratio=1.60,
    rsi_now=42.0,
    rsi_trough=28.0,
    sma200_val=520.0,  # ~3.8% below SMA200 (Deep Value)
    sma50_val=550.0,   # Price below SMA50
    ema20_val=490.0,
    ema5_val=488.0,
    macd_val=2.5,
    sig_val=1.8,
    hist_rising=True,
    atr_val=8.0,
):
    """Generate realistic OHLCV dataframe with authentic reversal curve."""
    dates = pd.date_range(end=pd.Timestamp.today(), periods=n_bars, freq="B")
    
    # Base trend down from 600 to trough 470, then curl up from 470 to current_price 500
    closes = np.full(n_bars, 550.0)
    # 52W high earlier
    closes[:30] = np.linspace(600, high_52w, 30)
    # Drawdown to trough
    trough_idx = n_bars - 1 - trough_bar_ago
    closes[30:trough_idx] = np.linspace(high_52w, trough_price + 5, trough_idx - 30)
    closes[trough_idx] = trough_price + 2.0
    # Reversal recovery from trough to current price
    if trough_bar_ago > 0:
        recovery_curve = np.linspace(trough_price + 2.0, current_price, trough_bar_ago + 1)
        closes[trough_idx:] = recovery_curve

    lows = closes - 4.0
    highs = closes + 4.0
    highs[29] = high_52w
    lows[trough_idx] = trough_price

    # Set latest bar
    closes[-1] = current_price
    lows[-1] = current_low
    highs[-1] = current_high
    opens = closes - 2.0
    
    volumes = np.full(n_bars, 100_000.0)
    volumes[-1] = 100_000.0 * vol_ratio
    
    df = pd.DataFrame({
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    }, index=dates)
    
    # Technical indicator series
    df["SMA200"] = sma200_val
    df["SMA50"] = sma50_val
    df["EMA20"] = ema20_val
    df["EMA5"] = ema5_val
    df["ATR"] = atr_val
    df["Volume_Ratio"] = vol_ratio
    df["HIGH_52W"] = high_52w
    df["LOW_52W"] = trough_price
    df["SWING_LOW"] = trough_price
    df["SWING_HIGH"] = high_52w
    
    # RSI series
    rsi_series = np.full(n_bars, 50.0)
    rsi_series[trough_idx] = rsi_trough
    rsi_series[-1] = rsi_now
    if trough_bar_ago > 0:
        rsi_series[trough_idx:] = np.linspace(rsi_trough, rsi_now, trough_bar_ago + 1)
    df["RSI"] = rsi_series
    
    # MACD series
    df["MACD"] = macd_val
    df["MACD_SIGNAL"] = sig_val
    if hist_rising:
        df.iloc[-3, df.columns.get_loc("MACD")] = sig_val + 0.2
        df.iloc[-2, df.columns.get_loc("MACD")] = sig_val + 0.4
        df.iloc[-1, df.columns.get_loc("MACD")] = sig_val + 0.7
    else:
        df.iloc[-3, df.columns.get_loc("MACD")] = sig_val + 0.5
        df.iloc[-2, df.columns.get_loc("MACD")] = sig_val + 0.5
        df.iloc[-1, df.columns.get_loc("MACD")] = sig_val + 0.5
        
    return df


class TestReversalScannerRefactor(unittest.TestCase):

    def setUp(self):
        self.default_fund = {
            "roe": 15.0,
            "yoy_revenue": 10.0,
            "Category": "Wealth Compounder"
        }

    # 1. Price below SMA200 by 19% → Deep Value eligible
    def test_price_below_sma200_by_19pct_deep_value_eligible(self):
        # SMA200 = 600, Close = 486 (19.0% below SMA200), high_52w = 650 (25.2% drop)
        df = make_reversal_df(current_price=486.0, sma200_val=600.0, current_low=482.0, trough_price=460.0, vol_ratio=1.60)
        res = _evaluate_candidate("TEST_SYM", df, self.default_fund)
        self.assertTrue(res["passed"], f"Expected PASS for 19% below SMA200, got: {res.get('reject_reason')}")

    # 2. Price below SMA200 by 21% → rejected (sma200_filter)
    def test_price_below_sma200_by_21pct_rejected(self):
        # SMA200 = 600, Close = 470 (21.67% below SMA200)
        df = make_reversal_df(current_price=470.0, sma200_val=600.0, current_low=465.0, trough_price=450.0)
        res = _evaluate_candidate("TEST_SYM", df, self.default_fund)
        self.assertFalse(res["passed"])
        self.assertEqual(res["reject_code"], "sma200_filter")

    # 3. Price above SMA200 → Quality Reversal path
    def test_price_above_sma200_quality_reversal_path(self):
        # SMA200 = 480, Close = 500 (Price > SMA200), EMA20 = 495
        df = make_reversal_df(current_price=500.0, sma200_val=480.0, ema20_val=495.0, current_low=495.0, trough_price=470.0)
        res = _evaluate_candidate("TEST_SYM", df, self.default_fund)
        self.assertTrue(res["passed"], f"Expected PASS for Quality Reversal, got: {res.get('reject_reason')}")

    # 4. Price below SMA200 + ROE 11.9% → rejected (fundamental_filter)
    def test_deep_value_roe_11_9_rejected(self):
        df = make_reversal_df(current_price=490.0, sma200_val=550.0, current_low=485.0, trough_price=460.0)
        low_roe_fund = {"roe": 11.9, "yoy_revenue": 10.0, "Category": "Growth"}
        res = _evaluate_candidate("TEST_SYM", df, low_roe_fund)
        self.assertFalse(res["passed"])
        self.assertEqual(res["reject_code"], "fundamental_filter")
        self.assertIn("12.0%", res["reject_reason"])

    # 5. Price below SMA200 + ROE 12.0% → eligible
    def test_deep_value_roe_12_0_eligible(self):
        df = make_reversal_df(current_price=490.0, sma200_val=550.0, current_low=485.0, trough_price=460.0)
        pass_roe_fund = {"roe": 12.0, "yoy_revenue": 10.0, "Category": "Growth"}
        res = _evaluate_candidate("TEST_SYM", df, pass_roe_fund)
        self.assertTrue(res["passed"], f"Expected PASS for ROE 12.0%, got: {res.get('reject_reason')}")

    # 6. MACD bullish but flat histogram for 20 bars → rejected (macd_stale)
    def test_macd_flat_histogram_20_bars_rejected(self):
        # No crossover in last 10 bars and flat histogram
        df = make_reversal_df(hist_rising=False)
        res = _evaluate_candidate("TEST_SYM", df, self.default_fund)
        self.assertFalse(res["passed"])
        self.assertEqual(res["reject_code"], "macd_stale")

    # 7. MACD crossover 14 bars ago + rising histogram → eligible
    def test_macd_crossover_14_bars_ago_rising_hist_eligible(self):
        df = make_reversal_df(hist_rising=True)
        res = _evaluate_candidate("TEST_SYM", df, self.default_fund)
        self.assertTrue(res["passed"], f"Expected PASS for active rising MACD histogram, got: {res.get('reject_reason')}")

    # 8. RVOL 1.34x → rejected (low_volume)
    def test_rvol_1_34_rejected(self):
        df = make_reversal_df(vol_ratio=1.34)
        res = _evaluate_candidate("TEST_SYM", df, self.default_fund)
        self.assertFalse(res["passed"])
        self.assertEqual(res["reject_code"], "low_volume")

    # 9. RVOL 1.35x + strong structure → eligible
    def test_rvol_1_35_strong_structure_eligible(self):
        # Trough = 460, current_low = 490 (6.5% above trough -> strong structure)
        df = make_reversal_df(vol_ratio=1.35, trough_price=460.0, current_low=490.0, current_price=500.0)
        res = _evaluate_candidate("TEST_SYM", df, self.default_fund)
        self.assertTrue(res["passed"], f"Expected PASS for 1.35x with strong structure, got: {res.get('reject_reason')}")

    # 10. RVOL 1.35x + weak structure → rejected (weak_reversal_structure)
    def test_rvol_1_35_weak_structure_rejected(self):
        # Trough = 494.0, current_low = 494.5 (only 0.1% above trough -> not strong structure < 1.5%)
        # and non-rising lows
        df = make_reversal_df(vol_ratio=1.35, trough_price=494.0, current_low=494.5, current_price=500.0)
        df.iloc[-2, df.columns.get_loc("Low")] = 496.0  # prior low was higher than current low
        df.iloc[-3, df.columns.get_loc("Low")] = 497.0
        res = _evaluate_candidate("TEST_SYM", df, self.default_fund)
        self.assertFalse(res["passed"])
        self.assertEqual(res["reject_code"], "weak_reversal_structure")

    # 11. RVOL 2.1x + valid reversal → strong-volume bonus awarded in scoring
    def test_rvol_2_1_bonus_awarded(self):
        score_dict = _score_reversal(
            vol_ratio=2.1,
            drop_pct=25.0,
            current_rsi=45.0,
            past_rsi_min=28.0,
            macd_hist=1.2,
            pct_below_sma200=5.0,
            category="Wealth Compounder",
            rr_ratio=2.5,
            trend_score=20,
            atr_val=10.0,
            macd_recovery_passed=True
        )
        self.assertGreaterEqual(score_dict["score"], 60)

    # 12. Lower-low / falling knife structure → rejected
    def test_lower_low_falling_knife_rejected(self):
        # Current low drops below trough (475 < 480)
        df = make_reversal_df(current_low=475.0, trough_price=480.0)
        res = _evaluate_candidate("TEST_SYM", df, self.default_fund)
        self.assertFalse(res["passed"])
        self.assertEqual(res["reject_code"], "failed_pattern")
        self.assertIn("NEW_LOWER_LOW", res["reject_reason"])

    # 13. SMA50 above price remains eligible for Deep Value (No longer requires Close >= SMA50)
    def test_sma50_above_price_eligible_for_deep_value(self):
        # SMA50 = 550, Close = 500 (Close < SMA50 by 10%)
        df = make_reversal_df(current_price=500.0, sma50_val=550.0, sma200_val=530.0, vol_ratio=1.60)
        res = _evaluate_candidate("TEST_SYM", df, self.default_fund)
        self.assertTrue(res["passed"], f"Expected PASS when price is below SMA50 in Deep Value, got: {res.get('reject_reason')}")

    # 14. Anti-climax top still rejects
    def test_anti_climax_top_rejected(self):
        # Extreme volume (4.0x), massive 15% runup over 5 bars, with upper wick dump
        df = make_reversal_df(vol_ratio=4.0, current_high=550.0, current_price=502.0, current_low=500.0)
        # Ensure 5-bar runup
        df.iloc[-6, df.columns.get_loc("Close")] = 430.0
        res = _evaluate_candidate("TEST_SYM", df, self.default_fund)
        self.assertFalse(res["passed"])
        self.assertEqual(res["reject_code"], "climax_top")

    # 15. Risk-Reward 1.99R → reject
    def test_rr_1_99_rejected(self):
        # Force low target / high stop loss in df to fail R:R >= 2.0
        # When Close = 500, High_52W = 630 (20.6% drop), but overhead resistance cluster at 512 with risk = 15 -> R:R = 0.80 < 2.0
        df = make_reversal_df(
            current_price=500.0,
            high_52w=630.0,
            sma50_val=510.0,
            sma200_val=512.0,
            ema20_val=495.0,
            atr_val=15.0,
            trough_price=480.0,
            current_low=490.0,
        )
        df["SWING_HIGH"] = 512.0
        res = _evaluate_candidate("TEST_SYM", df, self.default_fund)
        self.assertFalse(res["passed"])
        self.assertEqual(res["reject_code"], "low_rr")

    # 16. Risk-Reward >= 2.0R → pass
    def test_rr_2_00_passed(self):
        # Healthy room to SMA200/52W high
        df = make_reversal_df(current_price=500.0, current_low=495.0, trough_price=460.0, ema20_val=490.0, sma50_val=560.0, sma200_val=580.0, atr_val=8.0)
        res = _evaluate_candidate("TEST_SYM", df, self.default_fund)
        self.assertTrue(res["passed"], f"Expected PASS for RR >= 2.0, got: {res.get('reject_reason')}")

    # 17. STRONG_BEAR regime + RVOL 1.49x → rejected (below 1.50x floor)
    def test_strong_bear_rvol_1_49_rejected(self):
        df = make_reversal_df(vol_ratio=1.49, current_price=500.0, current_low=495.0, trough_price=460.0)
        strong_bear_ctx = {"current_regime": "STRONG_BEAR", "trend": "STRONG_BEAR"}
        df["OBV_Trend"] = 1  # pass OBV so we isolate the volume floor test
        res = _evaluate_candidate("TEST_SYM", df, self.default_fund, regime_ctx=strong_bear_ctx)
        self.assertFalse(res["passed"])
        self.assertIn(res["reject_code"], ("low_volume", "regime_vol"))

    # 18. STRONG_BEAR regime + RVOL 1.50x → eligible
    def test_strong_bear_rvol_1_50_eligible(self):
        df = make_reversal_df(vol_ratio=1.50, current_price=500.0, current_low=495.0, trough_price=460.0)
        strong_bear_ctx = {"current_regime": "STRONG_BEAR", "trend": "STRONG_BEAR"}
        df["OBV_Trend"] = 1  # STRONG_BEAR requires OBV accumulation
        res = _evaluate_candidate("TEST_SYM", df, self.default_fund, regime_ctx=strong_bear_ctx)
        self.assertTrue(res["passed"], f"Expected PASS for STRONG_BEAR with RVOL 1.50x, got: {res.get('reject_reason')}")

    # 19. Quality Reversal: Higher Low + Higher High → pass
    def test_quality_reversal_hl_and_hh_passed(self):
        # SMA200 = 480 (Price > SMA200), Higher Low (495 > 470) and Higher High (508 > 504)
        df = make_reversal_df(current_price=500.0, sma200_val=480.0, ema20_val=490.0, current_low=495.0, current_high=508.0, trough_price=470.0)
        res = _evaluate_candidate("TEST_SYM", df, self.default_fund)
        self.assertTrue(res["passed"], f"Expected PASS for Quality Reversal with HL+HH, got: {res.get('reject_reason')}")

    # 20. Quality Reversal: Lower Low / Lower High structural breakdown → rejected
    def test_lower_low_lower_high_rejected(self):
        # Current Low makes new low below trough (465 < 470) and lower high (490 < 510)
        df = make_reversal_df(current_price=480.0, current_low=465.0, current_high=490.0, trough_price=470.0)
        res = _evaluate_candidate("TEST_SYM", df, self.default_fund)
        self.assertFalse(res["passed"])
        self.assertEqual(res["reject_code"], "failed_pattern")


    # 21. Weekend execution invariance (Saturday/Sunday maps to Friday trading date)
    def test_reversal_weekend_date_resolution(self):
        from market_utils import get_expected_latest_trading_date
        from datetime import datetime
        from zoneinfo import ZoneInfo
        IST = ZoneInfo("Asia/Kolkata")

        # Saturday test: 2026-08-29 -> expected Friday 2026-08-28
        sat_dt = datetime(2026, 8, 29, 12, 0, tzinfo=IST)
        trading_date = get_expected_latest_trading_date(sat_dt)
        self.assertEqual(trading_date.weekday(), 4)  # 4 = Friday
        self.assertEqual(str(trading_date), "2026-08-28")

        # Sunday test: 2026-08-30 -> expected Friday 2026-08-28
        sun_dt = datetime(2026, 8, 30, 18, 0, tzinfo=IST)
        trading_date_sun = get_expected_latest_trading_date(sun_dt)
        self.assertEqual(trading_date_sun.weekday(), 4)  # 4 = Friday
        self.assertEqual(str(trading_date_sun), "2026-08-28")


if __name__ == "__main__":
    unittest.main()


