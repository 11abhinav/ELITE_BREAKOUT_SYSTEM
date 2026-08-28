# tests/test_dashboard_semantic_fallbacks.py
import unittest
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../app")))

from master_orchestrator import MasterOrchestratorV2

class TestDashboardSemanticFallbacks(unittest.TestCase):

    def test_portfolio_actions_fallback_semantic_truth(self):
        """
        Semantic Negative Test:
        When wealth_buy_alert returns NO rows (fallback mode),
        the endpoint must NEVER return action='BUY'.
        All rows must be explicitly marked with is_fallback=True and action='WATCHLIST_BASELINE'.
        """
        orchestrator = MasterOrchestratorV2()
        
        def mock_run_query(query, params=None):
            if "wealth_buy_alert" in query:
                return [] # No live buy alerts today
            if "daily_watchlist_v2" in query:
                return [
                    {
                        "symbol": "RELIANCE",
                        "action": "WATCHLIST_BASELINE",
                        "target_position_pct": 5.0,
                        "current_position_pct": 0.0,
                        "sector": "ELITE_COMPOUNDER",
                        "valuation_status": "HIGH",
                        "cmp": 2850.0,
                        "notes": "Liquid Elite Compounder",
                        "entry_signal": "Passed Quality Checklist"
                    }
                ]
            return []

        orchestrator._run_query = mock_run_query
        
        actions = orchestrator.get_portfolio_actions()
        
        self.assertTrue(len(actions) > 0, "Fallback should return baseline compounders")
        for item in actions:
            self.assertTrue(item.get("is_fallback"), "Fallback items must be explicitly marked is_fallback=True")
            self.assertEqual(item.get("action"), "WATCHLIST_BASELINE", "Fallback action must NEVER be 'BUY'")
            self.assertNotEqual(item.get("action"), "BUY", "CRITICAL SAFETY FAIL: Fallback item masquerading as BUY signal")
            self.assertEqual(item.get("data_source"), "daily_watchlist_v2_fallback")

    def test_confluence_setups_fallback_semantic_truth(self):
        """
        Semantic Negative Test:
        When live alerts return NO rows, confluence fallback must filter candidates by
        quality_score >= 70.0 and state IN ('CANDIDATE', 'ARMED', 'DEVELOPING').
        All records must be marked as is_fallback=True and setup_type='BASELINE_CONFLUENCE'.
        """
        orchestrator = MasterOrchestratorV2()

        def mock_run_query(query, params=None):
            if "FROM alerts" in query:
                return []
            if "FROM scanner_candidates" in query:
                return [
                    {
                        "symbol": "TCS",
                        "scanner": "EOD",
                        "state": "ARMED",
                        "quality_score": 85.0,
                        "cmp": 3900.0
                    }
                ]
            return []

        orchestrator._run_query = mock_run_query
        confluence = orchestrator.get_all_confluence_setups()

        self.assertTrue(len(confluence) > 0)
        for item in confluence:
            self.assertTrue(item.get("is_fallback"))
            self.assertEqual(item.get("setup_type"), "BASELINE_CONFLUENCE")
            self.assertEqual(item.get("confluence_tier"), "OBSERVATION CONFLUENCE")
            self.assertEqual(item.get("data_source"), "scanner_candidates_fallback")

if __name__ == '__main__':
    unittest.main()
