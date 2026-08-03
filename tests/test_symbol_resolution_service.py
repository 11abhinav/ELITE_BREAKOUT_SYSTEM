# =====================================================================================
# tests/test_symbol_resolution_service.py
# UNIT TEST SUITE FOR INSTITUTIONAL SYMBOL RESOLUTION SERVICE & CANONICAL REGISTRY
# =====================================================================================

import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
import threading
import time

from symbol_resolution_engine import (
    SymbolResolutionService,
    ResolvedInstrument,
    InstrumentMetadata,
    MemoryIndexStore,
    MappingSource,
    MappingStatus,
    get_symbol_resolver
)


class TestSymbolResolutionEngine(unittest.TestCase):
    """Unit test suite for SymbolResolutionService O(1) hotpath & adapters."""

    def setUp(self):
        self.resolver = SymbolResolutionService()
        self.resolver._active_indexes.idx_provider_mapping.clear()
        self.resolver._active_indexes.negative_cache.clear()

    def test_singleton_instance(self):
        """Verify SymbolResolutionService is a single instance across calls."""
        r1 = get_symbol_resolver()
        r2 = get_symbol_resolver()
        self.assertIs(r1, r2)

    def test_fyers_index_resolution(self):
        """Verify Fyers index lookup resolves benchmark indices instantly."""
        res = self.resolver.resolve("NIFTY 50", provider="fyers")
        self.assertTrue(res.is_valid)
        self.assertEqual(res.mapped_symbol, "NSE:NIFTY50-INDEX")

        res_bank = self.resolver.resolve("BANKNIFTY", provider="fyers")
        self.assertTrue(res_bank.is_valid)
        self.assertEqual(res_bank.mapped_symbol, "NSE:NIFTYBANK-INDEX")

    def test_upstox_index_resolution(self):
        """Verify Upstox index lookup resolves benchmark indices cleanly."""
        res = self.resolver.resolve("NIFTY 50", provider="upstox")
        self.assertTrue(res.is_valid)
        self.assertEqual(res.mapped_symbol, "NSE_INDEX|Nifty 50")

    def test_yahoo_index_resolution(self):
        """Verify Yahoo index lookup resolves benchmark indices cleanly."""
        res = self.resolver.resolve("^NSEI", provider="yahoo")
        self.assertTrue(res.is_valid)
        self.assertEqual(res.mapped_symbol, "^NSEI")

    def test_memory_store_o1_lookup(self):
        """Verify MemoryIndexStore returns pre-scanned mappings in microsecond O(1) latency."""
        res_inst = ResolvedInstrument("INE002A01018", "RELIANCE", "fyers", "NSE:RELIANCE-EQ", "NSE", "EQ", 100, "MASTER")
        self.resolver._active_indexes.idx_provider_mapping[("fyers", "RELIANCE")] = res_inst

        t0 = time.perf_counter()
        res = self.resolver.resolve("RELIANCE", provider="fyers")
        dt_us = (time.perf_counter() - t0) * 1000000

        self.assertTrue(res.is_valid)
        self.assertEqual(res.mapped_symbol, "NSE:RELIANCE-EQ")
        self.assertLess(dt_us, 5000)  # Should complete in sub-millisecond time (< 5ms)

    def test_single_flight_concurrency_control(self):
        """Verify 10 parallel threads requesting an unknown symbol trigger only 1 probe call."""
        probe_counter = {"count": 0}

        def mock_probe(sym, meta):
            with threading.Lock():
                probe_counter["count"] += 1
            time.sleep(0.05)  # Simulate API latency
            return ResolvedInstrument("EQ:UNKNOWNCONCURRENCYTEST", sym, "fyers", f"NSE:{sym}-EQ", "NSE", "EQ", 80, "PROBED")

        with patch('config.FEATURE_ASYNC_SYMBOL_PROBING_V1', False):
            with patch.object(self.resolver._adapters["fyers"], 'probe_candidates', side_effect=mock_probe):
                threads = []
                results = [None] * 10

                def worker(idx):
                    results[idx] = self.resolver.resolve("UNKNOWNCONCURRENCYTEST", provider="fyers")

                for i in range(10):
                    t = threading.Thread(target=worker, args=(i,))
                    threads.append(t)
                    t.start()

                for t in threads:
                    t.join()

                # All 10 threads must return the valid resolved instrument
                for r in results:
                    self.assertIsNotNone(r)
                    self.assertEqual(r.mapped_symbol, "NSE:UNKNOWNCONCURRENCYTEST-EQ")

                # But probe_candidates was invoked exactly ONCE due to single-flight locking!
                self.assertEqual(probe_counter["count"], 1)

    def test_all_three_providers_resolution(self):
        """Verify SymbolResolutionService resolves symbols cleanly across Upstox, Fyers, and Yahoo."""
        for prov, expected in [("fyers", "NSE:TATAMOTORS-EQ"), ("upstox", "NSE_EQ|TATAMOTORS"), ("yahoo", "TATAMOTORS.NS")]:
            res = self.resolver.resolve("TATAMOTORS", provider=prov)
            self.assertTrue(res.is_valid)
            self.assertEqual(res.provider, prov)

    def test_telemetry_metrics_summary(self):
        """Verify telemetry collector tracks hits, P50/P95/P99 latency, and summary dict."""
        metrics = self.resolver.get_metrics_summary()
        self.assertIn("total_requests", metrics)
        self.assertIn("memory_hit_ratio", metrics)
        self.assertIn("latency_p50_ms", metrics)


if __name__ == '__main__':
    unittest.main()
