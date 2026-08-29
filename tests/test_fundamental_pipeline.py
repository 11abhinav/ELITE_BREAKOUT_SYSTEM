"""
test_fundamental_pipeline.py
=============================
Automated test suite for the Production-Grade Fundamental Hydration Pipeline (v3.0).
"""

import sys
import os
import time
import math
import unittest
from datetime import datetime

sys.path.insert(0, os.path.abspath("app"))

from fundamental_pipeline import (
    valid_number,
    is_field_semantically_valid,
    is_field_fresh,
    _is_provider_healthy,
    _trip_circuit_breaker,
    _provider_health,
    get_unified_fundamentals,
    _build_response_contract
)


class TestFundamentalPipeline(unittest.TestCase):

    def test_valid_number(self):
        self.assertTrue(valid_number(100.5))
        self.assertTrue(valid_number(0))
        self.assertTrue(valid_number(-50.2))
        self.assertFalse(valid_number(None))
        self.assertFalse(valid_number(math.nan))
        self.assertFalse(valid_number(math.inf))
        self.assertFalse(valid_number("invalid"))

    def test_semantic_sanity_validation(self):
        self.assertTrue(is_field_semantically_valid("total_equity", 5000.0))
        self.assertFalse(is_field_semantically_valid("total_equity", -100.0))
        self.assertTrue(is_field_semantically_valid("total_debt", 0.0))

        # Accept negative financial statements as valid reported numbers
        self.assertTrue(is_field_semantically_valid("net_profit", -2500.0))
        self.assertTrue(is_field_semantically_valid("operating_cash_flow", -1200.0))

    def test_circuit_breaker_cooldown_with_jitter(self):
        _provider_health["SCREENER"] = {"state": "HEALTHY", "cooldown_until": 0.0}
        self.assertTrue(_is_provider_healthy("SCREENER"))

        _trip_circuit_breaker("SCREENER", base_cooldown_seconds=10.0, reason="HTTP 429 Test")
        self.assertFalse(_is_provider_healthy("SCREENER"))

        _provider_health["SCREENER"]["cooldown_until"] = time.time() - 1.0
        self.assertTrue(_is_provider_healthy("SCREENER"))

    def test_usable_hydration_contract(self):
        fields_complete = {
            "total_equity": {"value": 100, "source": "SCREENER", "quality": "REPORTED"},
            "total_debt": {"value": 50, "source": "SCREENER", "quality": "REPORTED"},
            "net_profit": {"value": 20, "source": "SCREENER", "quality": "REPORTED"},
            "operating_cash_flow": {"value": 25, "source": "SCREENER", "quality": "REPORTED"},
            "pe": {"value": 15, "source": "SCREENER", "quality": "REPORTED"},
            "pb": {"value": 2, "source": "SCREENER", "quality": "REPORTED"},
            "roe": {"value": 18, "source": "SCREENER", "quality": "REPORTED"},
            "roce": {"value": 22, "source": "SCREENER", "quality": "REPORTED"}
        }
        res_comp = _build_response_contract("TEST1", fields_complete, [1], 10.0)
        self.assertEqual(res_comp["hydration"]["status"], "COMPLETE")
        self.assertTrue(res_comp["hydration"]["usable"])

        fields_partial = {
            "total_equity": {"value": 100, "source": "SCREENER", "quality": "REPORTED"},
            "total_debt": {"value": 50, "source": "SCREENER", "quality": "REPORTED"},
            "net_profit": {"value": 20, "source": "SCREENER", "quality": "REPORTED"},
            "operating_cash_flow": {"value": 25, "source": "SCREENER", "quality": "REPORTED"}
        }
        res_part = _build_response_contract("TEST2", fields_partial, [1], 10.0)
        self.assertEqual(res_part["hydration"]["status"], "PARTIAL_BUT_USABLE")
        self.assertTrue(res_part["hydration"]["usable"])

    def test_single_flight_deduplication(self):
        import threading
        results = []

        def worker():
            res = get_unified_fundamentals("RELIANCE")
            results.append(res)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 4)
        for r in results:
            self.assertEqual(r["symbol"], "RELIANCE")


if __name__ == "__main__":
    unittest.main()
