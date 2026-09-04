import unittest
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np

from multitf.consolidation import (
    detect_15m_consolidation,
    ConsolidationResult,
    get_duration_width_limits,
    _generate_candidate_windows,
    _evaluate_dormancy,
    _compute_scores
)
from config import MULTI_TF_V2_CONFIG

IST = ZoneInfo("Asia/Kolkata")


class TestAdaptiveMTFV3Consolidation(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 4, 13, 30, tzinfo=IST)
        self.config = MULTI_TF_V2_CONFIG.copy()
        self.atr = 2.0

    def _generate_synthetic_df(self, n=30, base_high=505.0, base_low=501.0, volatile_pre_bars=0, volume=50000):
        """Generates synthetic 15m closed bars."""
        dates = [self.now - timedelta(minutes=15 * (n + volatile_pre_bars - i)) for i in range(n + volatile_pre_bars)]
        data = []
        # Optional volatile prior bars
        for _ in range(volatile_pre_bars):
            data.append({
                "Open": 490.0, "High": 520.0, "Low": 485.0, "Close": 510.0, "Volume": volume
            })
        # Consolidating bars
        for i in range(n):
            # Form higher lows and tight ceiling
            low = base_low + (i * 0.05)
            high = min(base_high, low + 2.5)
            close = (low + high) / 2.0
            data.append({
                "Open": low + 0.5, "High": high, "Low": low, "Close": close, "Volume": volume
            })
        df = pd.DataFrame(data, index=dates)
        df["session_date"] = [d.date() for d in dates]
        return df

    def test_duration_width_limits(self):
        """Dynamic limits must scale with bar count."""
        max_atr_8, max_pct_8 = get_duration_width_limits(8, self.config)
        max_atr_12, max_pct_12 = get_duration_width_limits(12, self.config)
        max_atr_20, max_pct_20 = get_duration_width_limits(20, self.config)
        max_atr_35, max_pct_35 = get_duration_width_limits(35, self.config)

        self.assertLessEqual(max_atr_8, 2.0)
        self.assertGreaterEqual(max_atr_12, 2.2)
        self.assertGreaterEqual(max_atr_20, 2.8)
        self.assertGreaterEqual(max_atr_35, 3.5)

    def test_intraday_tight_coil_discovered_despite_prior_volatility(self):
        """
        An 8-bar tight coil today must be discovered even if earlier bars were wide/volatile.
        This directly fixes the bottleneck that previously disqualified 342 of 344 stocks.
        """
        # 15 volatile bars earlier, followed by 10 tight consolidating bars
        df = self._generate_synthetic_df(n=10, base_high=505.0, base_low=501.5, volatile_pre_bars=15)
        atr = 2.0  # Range in last 10 bars is 3.5 = 1.75x ATR (< 2.5x limit)

        res = detect_15m_consolidation(df, atr, self.now, self.config, symbol="COIL_TEST")
        self.assertTrue(res.is_valid, f"Intraday tight coil must qualify, got rejection: {res.rejection_reason}")
        self.assertGreaterEqual(res.setup_score, 50)
        self.assertIn(res.winning_window_bars, [6, 8, 10])

    def test_dormancy_penalty_on_frozen_volume(self):
        """Dead flatlining stocks with zero/dormant volume should be flagged and penalized."""
        # 20 bars with volume = 10, compared to median of 50,000
        df = self._generate_synthetic_df(n=12, volume=10)
        # Create earlier history with high volume so median is large
        full_df = self._generate_synthetic_df(n=40, volume=100000)
        is_dormant, vol_ratio = _evaluate_dormancy(df, full_df, self.config)
        self.assertTrue(is_dormant, "Base with near-zero volume must be detected as dormant")

    def test_multi_touch_and_higher_lows_rewarded(self):
        """Base with multiple ceiling touches and higher lows must achieve higher setup score."""
        df = self._generate_synthetic_df(n=12, base_high=505.0, base_low=502.0)
        res = detect_15m_consolidation(df, self.atr, self.now, self.config, symbol="PRISTINE_BASE")
        self.assertTrue(res.is_valid)
        self.assertGreaterEqual(res.setup_score, 60, f"Pristine base should achieve setup_score >= 60, got {res.setup_score}")

    def test_overnight_gap_truncates_window(self):
        """Overnight gap > 2% should prevent multi-day window from spanning across gap."""
        yesterday = (self.now - timedelta(days=1)).date()
        today = self.now.date()

        dates = [datetime(yesterday.year, yesterday.month, yesterday.day, 14, 0, tzinfo=IST) + timedelta(minutes=15 * i) for i in range(6)]
        dates += [datetime(today.year, today.month, today.day, 9, 15, tzinfo=IST) + timedelta(minutes=15 * i) for i in range(8)]

        data = []
        # Yesterday closed at 500
        for _ in range(6):
            data.append({"Open": 498.0, "High": 501.0, "Low": 497.0, "Close": 500.0, "Volume": 50000})
        # Today opened with 4% gap at 520
        for _ in range(8):
            data.append({"Open": 520.0, "High": 523.0, "Low": 519.0, "Close": 521.0, "Volume": 50000})

        df = pd.DataFrame(data, index=dates)
        df["session_date"] = [d.date() for d in dates]

        windows = _generate_candidate_windows(df, self.atr, self.config)
        # Any window spanning across the gap (len > 8) must have been filtered out
        for w_df, sess_count in windows:
            self.assertEqual(sess_count, 1, "Gap > 2% must prevent window from spanning across sessions")


if __name__ == "__main__":
    unittest.main()
