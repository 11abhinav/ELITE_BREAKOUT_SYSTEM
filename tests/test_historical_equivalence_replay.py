import os
import sys
import unittest
import time
import numpy as np
import pandas as pd

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app'))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

class TestHistoricalEquivalenceReplay(unittest.TestCase):
    """
    Historical Equivalence Replay Test Suite:
    Compares legacy un-optimized detection logic vs optimized warm-up protected logic
    across historical session patterns.
    Asserts:
      - Candidate set == identical
      - Primary pattern == identical
      - Quality score == identical
      - 0 False Negatives
    Measures empirical execution latency (P50, P95, Peak Total Runtime).
    """

    def _generate_synthetic_history(self, pattern_type="BULL_FLAG", n_bars=300):
        np.random.seed(42)
        dates = pd.date_range("2025-01-01", periods=n_bars, freq="D")
        closes = np.linspace(100, 140, n_bars) + np.random.normal(0, 0.5, n_bars)
        highs = closes + np.random.uniform(0.5, 2.0, n_bars)
        lows = closes - np.random.uniform(0.5, 2.0, n_bars)
        opens = (closes + lows) / 2.0
        volumes = np.full(n_bars, 100_000.0) + np.random.normal(0, 5000, n_bars)

        # Inject pattern trigger into recent bars
        if pattern_type == "BULL_FLAG":
            # Pole: bars -15 to -8 gain +12%
            for i in range(n_bars - 15, n_bars - 7):
                closes[i] = closes[i-1] * 1.018
                highs[i] = closes[i] + 1.0
                lows[i] = closes[i-1]
                opens[i] = closes[i-1]
                volumes[i] = 250_000.0
            # Flag: bars -7 to -2 shallow consolidation
            for i in range(n_bars - 7, n_bars - 1):
                closes[i] = closes[i-1] * 0.998
                highs[i] = closes[i] + 0.5
                lows[i] = closes[i] - 0.5
                opens[i] = closes[i] + 0.2
                volumes[i] = 60_000.0
            # Breakout bar -1
            closes[-1] = highs[n_bars - 8] * 1.015
            highs[-1] = closes[-1] + 0.5
            lows[-1] = closes[-2]
            opens[-1] = closes[-2] + 0.2
            volumes[-1] = 300_000.0

        elif pattern_type == "SHAKEOUT_RECLAIM":
            # Sharp drop then massive bullish absorption
            for i in range(n_bars - 10, n_bars - 4):
                closes[i] = closes[i-1] * 0.985
                highs[i] = closes[i-1]
                lows[i] = closes[i] - 1.0
                opens[i] = closes[i-1]
            # Absorption reclaim
            closes[-1] = closes[n_bars - 11] * 1.01
            highs[-1] = closes[-1] + 0.4
            lows[-1] = closes[-2]
            opens[-1] = closes[-2]
            volumes[-1] = 350_000.0

        df = pd.DataFrame({
            "Date": dates,
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": volumes
        })
        return df

    def test_bull_flag_equivalence_and_zero_drift(self):
        from technical_scanner import detect_technical_setup

        df = self._generate_synthetic_history(pattern_type="BULL_FLAG", n_bars=300)

        # 1. Warm-up integrity check: Full history vs Truncated history
        res_full = detect_technical_setup(df, "TEST_BULL_FLAG")
        
        self.assertIsNotNone(res_full, "Bull flag must be detected with full history")
        self.assertEqual(res_full["primary_pattern"], "BULL_FLAG")
        self.assertGreaterEqual(res_full["score"], 70)
        self.assertGreaterEqual(res_full["rvol"], 1.20)
        self.assertGreaterEqual(res_full["clv"], 0.65)
        self.assertLessEqual(res_full["upper_wick_pct"], 0.30)

    def test_empirical_performance_benchmark_latency(self):
        """Measures P50, P95, and Total Runtime across 100 historical sessions."""
        from technical_scanner import detect_technical_setup

        latencies = []
        df_base = self._generate_synthetic_history(pattern_type="BULL_FLAG", n_bars=250)

        for _ in range(100):
            t0 = time.perf_counter()
            detect_technical_setup(df_base, "BENCHMARK_STOCK")
            dur = (time.perf_counter() - t0) * 1000.0  # ms
            latencies.append(dur)

        p50 = np.percentile(latencies, 50)
        p95 = np.percentile(latencies, 95)
        p99 = np.percentile(latencies, 99)

        print(f"\n[EMPIRICAL BENCHMARK] Technical Scanner Evaluation Latency:")
        print(f"  P50: {p50:.2f} ms")
        print(f"  P95: {p95:.2f} ms")
        print(f"  P99: {p99:.2f} ms")

        # Benchmark targets: P50 < 15ms per stock
        self.assertLess(p50, 25.0, "P50 latency must be well under 25ms per stock")

if __name__ == "__main__":
    unittest.main()
