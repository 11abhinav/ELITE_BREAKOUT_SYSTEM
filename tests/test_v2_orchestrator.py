import unittest
import time
from master_orchestrator import orchestrator_v2, resolve_tradingview_symbol, _sanitize_numeric

class TestMasterOrchestratorV2Performance(unittest.TestCase):

    def setUp(self):
        orchestrator_v2.invalidate_cache()

    def test_tradingview_symbol_resolution(self):
        self.assertEqual(resolve_tradingview_symbol("RELIANCE"), "NSE:RELIANCE")
        self.assertEqual(resolve_tradingview_symbol("BSE:532959"), "BSE:532959")
        self.assertEqual(resolve_tradingview_symbol("532959"), "BSE:532959")
        self.assertEqual(resolve_tradingview_symbol("DIACABS.BO"), "BSE:DIACABS")

    def test_sanitize_numeric(self):
        self.assertEqual(_sanitize_numeric(100.55), 100.55)
        self.assertEqual(_sanitize_numeric("250.75"), 250.75)
        self.assertIsNone(_sanitize_numeric(float('nan')))
        self.assertIsNone(_sanitize_numeric(float('inf')))
        self.assertIsNone(_sanitize_numeric("invalid"))

    def test_ensure_contract_keys_fast_path(self):
        sample = {
            "symbol": "TCS",
            "cmp": 3850.50,
            "trigger_level": 3900.0,
            "distance_pct": 1.29,
            "entry_price": 3800.0,
            "stop_loss": 3700.0,
            "target_1": 4000.0
        }
        t0 = time.perf_counter()
        for _ in range(100):
            res = orchestrator_v2._ensure_contract_keys(dict(sample), data_source="test")
        t_elapsed = time.perf_counter() - t0
        self.assertLess(t_elapsed, 0.1, "100 contract key resolutions took > 100ms")
        self.assertEqual(res["symbol"], "TCS")
        self.assertEqual(res["tradingview_symbol"], "NSE:TCS")
        self.assertEqual(res["cmp"], 3850.50)
        self.assertEqual(res["cmp_source"], "RECORDED_PRICE")
        self.assertIsNotNone(res["distance_pct"])
        self.assertIsNotNone(res["primary_blocker"])
        self.assertIsNotNone(res["why_qualifies"])

    def test_v2_endpoints_return_types(self):
        summary = orchestrator_v2.get_master_summary()
        self.assertIsInstance(summary, dict)
        self.assertIn("engines", summary)
        self.assertIn("status", summary)

        signals = orchestrator_v2.get_confirmed_signals()
        self.assertIsInstance(signals, list)

        watch = orchestrator_v2.get_stocks_to_watch()
        self.assertIsInstance(watch, list)

        inv = orchestrator_v2.get_investment_watch()
        self.assertIsInstance(inv, list)

        actions = orchestrator_v2.get_portfolio_actions()
        self.assertIsInstance(actions, list)

        confluence = orchestrator_v2.get_all_confluence_setups()
        self.assertIsInstance(confluence, list)

        health = orchestrator_v2.get_scanner_health()
        self.assertIsInstance(health, list)

if __name__ == "__main__":
    unittest.main()
