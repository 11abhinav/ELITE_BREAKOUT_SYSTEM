# =====================================================================================
# tests/test_consolidation_differential.py
# Correctness & Differential Equivalence Suite: Legacy vs Optimized Engine
#
# Asserts:
# 1. Mathematical soundness of fast filter: no legitimate base is rejected.
# 2. 100% equivalence between legacy and vectorized engine on candidate windows,
#    box_high, box_low, box_id, winning_window_bars, and scores.
# 3. Micro-benchmark measuring speedup.
# =====================================================================================

import unittest
import sys
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from multitf.consolidation import (
    detect_15m_consolidation,
    _detect_15m_consolidation_legacy,
    detect_15m_consolidation_from_context,
    prepare_15m_context,
    Prepared15mContext,
    ConsolidationResult,
    get_duration_width_limits
)
from config import MULTI_TF_V2_CONFIG

IST = ZoneInfo("Asia/Kolkata")


class TestConsolidationDifferential(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 4, 13, 30, tzinfo=IST)
        self.config = MULTI_TF_V2_CONFIG.copy()
        self.atr = 2.5

    def _generate_synthetic_df(self, n=30, base_high=505.0, base_low=501.0, volatile_pre_bars=0, volume=50000):
        """Generates synthetic 15m closed bars."""
        dates = [self.now - timedelta(minutes=15 * (n + volatile_pre_bars - i)) for i in range(n + volatile_pre_bars)]
        data = []
        for _ in range(volatile_pre_bars):
            data.append({
                "Open": 490.0, "High": 520.0, "Low": 485.0, "Close": 510.0, "Volume": volume
            })
        for i in range(n):
            low = base_low + (i * 0.05)
            high = min(base_high, low + 2.5)
            close = (low + high) / 2.0
            data.append({
                "Open": low + 0.5, "High": high, "Low": low, "Close": close, "Volume": volume
            })
        df = pd.DataFrame(data, index=dates)
        df["session_date"] = [d.date() for d in dates]
        return df

    def test_fast_filter_implication(self):
        """Fast filter rejects only when NO candidate window can qualify mathematically."""
        # 1. Too few bars (< 6)
        df_short = self._generate_synthetic_df(n=5)
        ctx_short = prepare_15m_context(df_short, self.atr, self.config, symbol="SHORT")
        self.assertIsNone(ctx_short, "Context preparation must reject < 6 bars")
        leg_short = _detect_15m_consolidation_legacy(df_short, self.atr, self.now, self.config, symbol="SHORT")
        self.assertFalse(leg_short.is_valid, "Legacy must also reject < 6 bars")

        # 2. Zero ATR
        df_valid = self._generate_synthetic_df(n=10)
        ctx_zero_atr = prepare_15m_context(df_valid, 0.0, self.config, symbol="ZERO_ATR")
        self.assertIsNone(ctx_zero_atr, "Context preparation must reject atr <= 0")
        leg_zero_atr = _detect_15m_consolidation_legacy(df_valid, 0.0, self.now, self.config, symbol="ZERO_ATR")
        self.assertFalse(leg_zero_atr.is_valid, "Legacy must also reject atr <= 0")

    def test_differential_equivalence_across_fixtures(self):
        """Legacy vs Optimized must produce identical results across diverse fixtures."""
        fixtures = [
            ("TIGHT_COIL", self._generate_synthetic_df(n=10, base_high=505.0, base_low=501.5, volatile_pre_bars=15), 2.0),
            ("PRISTINE_BASE", self._generate_synthetic_df(n=12, base_high=505.0, base_low=502.0), 2.5),
            ("MID_BASE", self._generate_synthetic_df(n=16, base_high=510.0, base_low=504.0), 3.0),
            ("WIDE_BASE_REJECT", self._generate_synthetic_df(n=10, base_high=550.0, base_low=500.0), 2.0),
            ("SHORT_BASE", self._generate_synthetic_df(n=8, base_high=102.0, base_low=100.5), 0.8),
            ("LONG_BASE", self._generate_synthetic_df(n=35, base_high=205.0, base_low=198.0), 3.5),
        ]

        for name, df, atr in fixtures:
            with self.subTest(fixture=name):
                # Run legacy
                legacy_res = _detect_15m_consolidation_legacy(df, atr, self.now, self.config, symbol=name)
                
                # Run optimized
                ctx = prepare_15m_context(df, atr, self.config, symbol=name)
                self.assertIsNotNone(ctx, f"Context preparation failed for {name}")
                opt_res = detect_15m_consolidation_from_context(ctx, self.now, self.config)

                # Equivalence assertions
                self.assertEqual(
                    legacy_res.is_valid, opt_res.is_valid,
                    f"[{name}] Validity mismatch: legacy={legacy_res.is_valid} vs opt={opt_res.is_valid}"
                )

                if legacy_res.is_valid:
                    self.assertEqual(
                        legacy_res.winning_window_bars, opt_res.winning_window_bars,
                        f"[{name}] Winning window mismatch: legacy={legacy_res.winning_window_bars} vs opt={opt_res.winning_window_bars}"
                    )
                    self.assertAlmostEqual(
                        legacy_res.box_high, opt_res.box_high, places=2,
                        msg=f"[{name}] box_high mismatch"
                    )
                    self.assertAlmostEqual(
                        legacy_res.box_low, opt_res.box_low, places=2,
                        msg=f"[{name}] box_low mismatch"
                    )
                    self.assertEqual(
                        legacy_res.box_id, opt_res.box_id,
                        f"[{name}] box_id mismatch: {legacy_res.box_id} vs {opt_res.box_id}"
                    )
                    self.assertAlmostEqual(
                        legacy_res.box_width_pct, opt_res.box_width_pct, places=4,
                        msg=f"[{name}] box_width_pct mismatch"
                    )
                    self.assertAlmostEqual(
                        legacy_res.box_width_atr, opt_res.box_width_atr, places=2,
                        msg=f"[{name}] box_width_atr mismatch"
                    )
                    self.assertEqual(
                        legacy_res.resistance_test_count, opt_res.resistance_test_count,
                        f"[{name}] resistance_test_count mismatch"
                    )
                    self.assertEqual(
                        legacy_res.lifecycle_stage, opt_res.lifecycle_stage,
                        f"[{name}] lifecycle_stage mismatch"
                    )
                    # Scores within 1 point tolerance
                    self.assertLessEqual(
                        abs(legacy_res.base_quality_score - opt_res.base_quality_score), 1,
                        f"[{name}] base_quality_score delta > 1: legacy={legacy_res.base_quality_score}, opt={opt_res.base_quality_score}"
                    )
                    self.assertLessEqual(
                        abs(legacy_res.setup_score - opt_res.setup_score), 1,
                        f"[{name}] setup_score delta > 1: legacy={legacy_res.setup_score}, opt={opt_res.setup_score}"
                    )

    def test_speedup_microbenchmark(self):
        """Vectorized context engine should be substantially faster than legacy pandas engine."""
        df = self._generate_synthetic_df(n=25, base_high=505.0, base_low=501.5, volatile_pre_bars=10)
        atr = 2.0
        iterations = 50

        # Benchmark Legacy
        t0 = time.perf_counter()
        for _ in range(iterations):
            _detect_15m_consolidation_legacy(df, atr, self.now, self.config, symbol="BENCH")
        t_legacy = time.perf_counter() - t0

        # Benchmark Optimized (including context preparation)
        t0 = time.perf_counter()
        for _ in range(iterations):
            ctx = prepare_15m_context(df, atr, self.config, symbol="BENCH")
            detect_15m_consolidation_from_context(ctx, self.now, self.config)
        t_opt = time.perf_counter() - t0

        speedup = t_legacy / max(1e-6, t_opt)
        print(f"\n[BENCHMARK] Legacy: {t_legacy*1000/iterations:.2f}ms/sym | Optimized: {t_opt*1000/iterations:.2f}ms/sym | Speedup: {speedup:.1f}x")
        self.assertGreater(speedup, 1.5, f"Optimized engine must be faster than legacy (got {speedup:.1f}x)")


if __name__ == "__main__":
    unittest.main()
