import unittest
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app')))

from technical_scanner import (
    _find_swing_pivots,
    _detect_double_bottom,
    _detect_cup_and_handle,
    _detect_ascending_triangle,
    _detect_bull_pennant,
    _detect_higher_low_reversal,
    _detect_v_reversal,
    detect_technical_setup,
)

IST = ZoneInfo("Asia/Kolkata")


class TestTechnicalScannerPatternAudit(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 2, 18, 15, tzinfo=IST)

    def _make_daily_df(self, n=50, base_price=500.0, trend="FLAT"):
        dates = [self.now - timedelta(days=(n - i)) for i in range(n)]
        rows = []
        p = base_price
        for i in range(n):
            if trend == "UP":
                p += 1.5
            elif trend == "DOWN":
                p -= 1.5
            o = p - 0.5
            h = p + 2.0
            l = p - 2.0
            c = p + 0.5
            v = 100000.0
            rows.append({
                "Date": dates[i], "Open": o, "High": h, "Low": l, "Close": c, "Volume": v
            })
        df = pd.DataFrame(rows)
        df.set_index("Date", inplace=True)
        return df

    def test_find_swing_pivots_rejects_1bar_noise(self):
        """A single bar dip or rise surrounded by flat bars must not be a 2-bar swing pivot."""
        highs = np.array([100, 100, 100, 102, 100, 100, 100], dtype=float)
        lows = np.array([98, 98, 98, 96, 98, 98, 98], dtype=float)
        peaks, troughs = _find_swing_pivots(highs, lows, lookback=2)
        # Peak at idx 3 is higher than idx 1, 2, 4, 5 -> recognized
        self.assertEqual(peaks, [3])
        # Trough at idx 3 is lower than idx 1, 2, 4, 5 -> recognized
        self.assertEqual(troughs, [3])

        # But a 1-bar blip with adjacent equal/lower bars must NOT qualify
        highs_noisy = np.array([100, 102, 101, 102, 100], dtype=float)
        lows_noisy = np.array([98, 96, 97, 96, 98], dtype=float)
        peaks_n, troughs_n = _find_swing_pivots(highs_noisy, lows_noisy, lookback=2)
        self.assertEqual(peaks_n, [])
        self.assertEqual(troughs_n, [])

    def test_double_bottom_rejected_when_no_prior_downtrend(self):
        """A stock in an uptrend that pauses with two equal lows must NOT be labeled a Double Bottom."""
        df = self._make_daily_df(n=60, base_price=500.0, trend="UP")
        # Artificially create two equal lows 12 bars apart near the peak
        # T1 at bar 40 (low 550), Neckline at bar 46 (high 575), T2 at bar 52 (low 550)
        # But pre-T1 price came UP from 500 -> 550 (no prior downtrend!)
        atr14 = 5.0
        df.iloc[40, df.columns.get_loc("Low")] = 550.0
        df.iloc[46, df.columns.get_loc("High")] = 575.0
        df.iloc[52, df.columns.get_loc("Low")] = 550.0
        # Today breakout above 575
        df.iloc[-1, df.columns.get_loc("Close")] = 578.0
        df.iloc[-1, df.columns.get_loc("Open")] = 574.0

        res = _detect_double_bottom(df, atr14)
        self.assertIsNone(res, "Double bottom must be rejected if there is no preceding markdown")

    def test_double_bottom_rejected_if_stale_breakout(self):
        """If breakout occurred 5 days ago and yesterday closed way above neckline, reject as stale."""
        # 1. Prior downtrend from 600 -> 500 (-16.6%)
        # 2. T1 at 500, Neckline at 530, T2 at 501 (14 bars apart)
        # 3. But yesterday closed at 545 (> 530 * 1.025)
        dates = [self.now - timedelta(days=(60 - i)) for i in range(60)]
        data = []
        for i in range(60):
            if i < 20:
                p = 600.0 - (i * 5.0)  # Down from 600 to 500
            elif i == 20:
                p = 500.0  # T1
            elif i < 28:
                p = 500.0 + ((i - 20) * 3.75)  # Rally to 530
            elif i == 28:
                p = 530.0  # Neckline
            elif i < 36:
                p = 530.0 - ((i - 28) * 3.6)  # Drop to 501
            elif i == 36:
                p = 501.0  # T2
            elif i < 58:
                p = 501.0 + ((i - 36) * 1.5)  # Slow rise
            else:
                p = 550.0  # Already broken out days ago!
            data.append({"Date": dates[i], "Open": p, "High": p + 1.0, "Low": p - 1.0, "Close": p, "Volume": 100000.0})

        df = pd.DataFrame(data).set_index("Date")
        res = _detect_double_bottom(df, 5.0)
        self.assertIsNone(res, "Stale double bottom breakout where price already ran must be rejected")

    def test_genuine_double_bottom_qualifies(self):
        """Pristine W-reversal with prior downtrend, 14-bar trough spacing, and fresh breakout today."""
        dates = [self.now - timedelta(days=(60 - i)) for i in range(60)]
        data = []
        for i in range(60):
            if i < 20:
                p = 600.0 - (i * 5.0)  # Prior downtrend: 600 -> 500 (-16.7%)
            elif i == 20:
                p = 500.0  # Trough 1
            elif i < 27:
                p = 500.0 + ((i - 20) * 4.3)
            elif i == 27:
                p = 530.0  # Neckline (+6.0% height)
            elif i < 35:
                p = 530.0 - ((i - 27) * 3.6)
            elif i == 35:
                p = 501.0  # Trough 2 (diff 0.2%, 15 bars from T1)
            elif i < 58:
                p = 501.0 + ((i - 35) * 1.25)  # Climbing towards neckline
            elif i == 58:
                p = 529.0  # Yesterday: Testing neckline (below 530)
            else:
                p = 533.0  # TODAY: Fresh Breakout above neckline 530!

            o = p - 0.5
            c = p + 0.5 if i == 59 else p
            h = max(o, c) + 1.0
            l = min(o, c) - 1.0
            data.append({"Date": dates[i], "Open": o, "High": h, "Low": l, "Close": c, "Volume": 150000.0})

        df = pd.DataFrame(data).set_index("Date")
        res = _detect_double_bottom(df, 5.0)
        self.assertIsNotNone(res, "Genuine W-bottom reversal with fresh breakout must qualify")
        self.assertEqual(res["pattern"], "DOUBLE_BOTTOM")
        self.assertGreaterEqual(res["prior_drop_pct"], 7.0)
        self.assertGreaterEqual(res["trough_bars"], 8)
        self.assertLessEqual(res["trough_diff_pct"], 2.5)

    def test_ascending_triangle_strict_ascending_lows(self):
        """Ascending triangle must have strictly ascending multi-bar swing lows."""
        dates = [self.now - timedelta(days=(40 - i)) for i in range(40)]
        data = []
        res_ceiling = 500.0
        # Flat ceiling at 500, Lows rising: 470 -> 480 -> 490 -> Breakout today 503
        for i in range(40):
            if i in (10, 22, 34):
                h = res_ceiling
                l = 485.0
                c = 495.0
                o = 490.0
            elif i == 5:
                l = 470.0  # Trough 1
                h = 480.0
                c = 475.0
                o = 472.0
            elif i == 17:
                l = 480.0  # Trough 2 (Higher Low)
                h = 490.0
                c = 485.0
                o = 482.0
            elif i == 29:
                l = 490.0  # Trough 3 (Higher Low)
                h = 498.0
                c = 495.0
                o = 492.0
            elif i == 38:
                l = 494.0
                h = 499.0
                c = 498.0
                o = 495.0
            elif i == 39:
                # Today fresh breakout!
                l = 498.0
                h = 504.0
                c = 503.0
                o = 499.0
            else:
                l = 485.0
                h = 495.0
                c = 490.0
                o = 488.0
            data.append({"Date": dates[i], "Open": o, "High": h, "Low": l, "Close": c, "Volume": 120000.0})

        df = pd.DataFrame(data).set_index("Date")
        res = _detect_ascending_triangle(df, 5.0)
        self.assertIsNotNone(res, "Ascending triangle with flat ceiling and rising lows must qualify")
        self.assertEqual(res["pattern"], "ASCENDING_TRIANGLE")


if __name__ == "__main__":
    unittest.main()
