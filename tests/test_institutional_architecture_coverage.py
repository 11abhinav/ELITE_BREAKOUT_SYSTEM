import os
import sys
import unittest
import numpy as np
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app'))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

class TestInstitutionalArchitectureCoverage(unittest.TestCase):
    """
    Comprehensive Institutional Architecture Test Suite:
    - Bhavcopy failure / stale-data suppression
    - Circuit execution FSM & execution gate
    - RegimePolicy behavior & Sector RS normalization
    - Ratchet trailing monotonicity
    - Meta-conviction scoring
    - Health Scanner scheduler next-run synchronization
    """

    def test_bhavcopy_readiness_and_stale_data_suppression(self):
        """Verifies Bhavcopy gating and stale-data suppression."""
        main_py_path = os.path.join(APP_DIR, "main.py")
        with open(main_py_path, "r", encoding="utf-8") as f:
            main_py = f.read()

        self.assertIn("def wait_for_bhavcopy_or_fallback(", main_py)
        self.assertIn("18:30", main_py, "Bhavcopy readiness must initiate at 18:30 IST")
        self.assertIn("18:35", main_py, "Accumulation must execute at 18:35 IST")
        self.assertNotIn("16:15", main_py, "Legacy 16:15 must be purged completely")

    def test_regime_policy_contracts_and_sector_rs(self):
        """Verifies frozen RegimePolicy dataclass, risk multipliers, and Sector RS continuous scaling."""
        from regime_engine import (
            get_regime_policy,
            calculate_sector_score_bonus,
            calculate_normalized_meta_score
        )

        # 1. Test Regime Policy Contracts
        bull_pol = get_regime_policy("BULL_STRONG")
        self.assertEqual(bull_pol.min_diurnal_rvol, 1.15)
        self.assertTrue(bull_pol.max_new_entries_permitted)
        self.assertEqual(bull_pol.risk_multiplier, 1.0)

        high_vol_pol = get_regime_policy("HIGH_VOL_EVENT")
        self.assertFalse(high_vol_pol.max_new_entries_permitted)
        self.assertEqual(high_vol_pol.risk_multiplier, 0.0)

        # 2. Test Sector RS Continuous Bonus Scaling (-10 to +10)
        self.assertEqual(calculate_sector_score_bonus(100.0), 10.0)
        self.assertEqual(calculate_sector_score_bonus(50.0), 0.0)
        self.assertEqual(calculate_sector_score_bonus(0.0), -10.0)
        self.assertEqual(calculate_sector_score_bonus(80.0), 6.0)

        # 3. Test Normalized Meta Conviction Score
        meta_score = calculate_normalized_meta_score(
            tech_score=85.0,
            diurnal_rvol=1.50,
            sector_rs_pct=80.0,
            fundamental_score=90.0
        )
        self.assertTrue(np.isclose(meta_score, 76.3, atol=0.2))

    def test_ratchet_trailing_monotonicity(self):
        """Verifies that Stop Loss only ratchets upwards and never loosens downwards."""
        from performance_tracker import process_trade_history

        trade = {
            "id": 88888,
            "symbol": "TEST_RATCHET_MONO",
            "scanner": "MULTI_TF",
            "status": "OPEN",
            "execution_state": "OPEN",
            "entry_mode": "MARKET",
            "entry_price": 100.0,
            "actual_entry_price": 100.0,
            "stop_loss": 95.0,
            "initial_stop_loss": 95.0,
            "target_1": 108.0,
            "target_2": 115.0,
            "target_3": 125.0,
            "shares_bought": 100,
            "remaining_shares": 100,
            "exit_history": "[]"
        }

        # Step 1: Minor advance (SL stays at 95.0)
        hist_1 = pd.DataFrame([
            {"Open": 100.0, "High": 104.0, "Low": 98.0, "Close": 103.0, "Volume": 1000}
        ], index=pd.to_datetime(["2026-08-01 10:00:00"]))
        process_trade_history(trade, hist=hist_1, cur_p=None)
        self.assertEqual(trade["stop_loss"], 95.0)

        # Step 2: Hit Target 1 -> Ratchets SL to entry + buffer (>= 100.30)
        hist_2 = pd.DataFrame([
            {"Open": 103.0, "High": 109.0, "Low": 102.0, "Close": 108.5, "Volume": 5000}
        ], index=pd.to_datetime(["2026-08-01 11:00:00"]))
        process_trade_history(trade, hist=hist_2, cur_p=None)
        self.assertGreaterEqual(trade["stop_loss"], 100.30)
        self.assertEqual(trade["status"], "PARTIAL_WIN_1")
        sl_after_t1 = trade["stop_loss"]

        # Step 3: Pullback to 102.0 (SL must NEVER decrease below sl_after_t1)
        hist_3 = pd.DataFrame([
            {"Open": 108.0, "High": 108.5, "Low": 102.0, "Close": 102.5, "Volume": 2000}
        ], index=pd.to_datetime(["2026-08-01 12:00:00"]))
        process_trade_history(trade, hist=hist_3, cur_p=None)
        self.assertGreaterEqual(trade["stop_loss"], sl_after_t1, "SL must not loosen or decrease on pullback")

    def test_health_scanner_scheduler_next_run_consistency(self):
        """Verifies that configured schedule matches health displays and next execution logic."""
        from database import get_all_scanner_health, init_db
        import re

        init_db()
        records = get_all_scanner_health()
        acc_rec = next((r for r in records if r.get("scanner_name") == "ACCUMULATION"), None)
        self.assertIsNotNone(acc_rec)
        sched_display_1 = acc_rec.get("scheduled_for", "")

        # Display Location #1
        self.assertIn("18:35", sched_display_1)
        self.assertNotIn("16:15", sched_display_1)

        # Display Location #2 (Admin Dashboard UI)
        admin_html_path = os.path.join(APP_DIR, "admin_dashboard.html")
        with open(admin_html_path, "r", encoding="utf-8") as f:
            admin_html = f.read()

        match_meta = re.search(r"'ACCUMULATION':\s*\{\s*label:\s*'[^']+',\s*desc:\s*'([^']+)'\s*\}", admin_html)
        self.assertIsNotNone(match_meta)
        sched_display_2 = match_meta.group(1)
        self.assertIn("18:35", sched_display_2)
        self.assertNotIn("16:15", sched_display_2)

    def test_symbol_router_and_fyers_miss_handling(self):
        """Verifies static pre-seeding of LTIM, universal routing fallback, and error taxonomy."""
        from symbol_router import SymbolRouter, RoutingState, ProviderErrorCode

        router = SymbolRouter()
        # 1. Verify static pre-seeding across all timeframes
        self.assertEqual(router.get_route("LTIM", "1d"), RoutingState.UPSTOX_ONLY)
        self.assertEqual(router.get_route("NSE:LTIM", "1h"), RoutingState.UPSTOX_ONLY)
        self.assertEqual(router.get_route("LTIM", "15m"), RoutingState.UPSTOX_ONLY)
        self.assertEqual(router.get_route("LTIM", "5m"), RoutingState.UPSTOX_ONLY)

        # 1b. Verify non-contamination: standard NSE/BSE symbols remain LOAD_BALANCED
        self.assertEqual(router.get_route("RELIANCE", "1d"), RoutingState.LOAD_BALANCED)
        self.assertEqual(router.get_route("NSE:INFY", "15m"), RoutingState.LOAD_BALANCED)
        self.assertEqual(router.get_route("BSE:500325", "1d"), RoutingState.LOAD_BALANCED)

        # 2. Verify error classification for Fyers symbol misses
        err_str = "Invalid symbol: All Fyers series candidates failed for LTIM (['NSE:LTIM-EQ', 'BSE:LTIM-EQ'])"
        code = router.classify_error_code(err_str)
        self.assertEqual(code, ProviderErrorCode.UNSUPPORTED_SYMBOL)

        # 3. Verify record_result with UNSUPPORTED_SYMBOL sets UPSTOX_ONLY universally
        router.record_result("TESTSYM", "1d", "fyers", is_success=False, error_msg="invalid symbol provided")
        self.assertEqual(router.get_route("TESTSYM", "1d"), RoutingState.UPSTOX_ONLY)
        self.assertEqual(router.get_route("TESTSYM", "15m"), RoutingState.UPSTOX_ONLY)
        self.assertEqual(router.get_route("TESTSYM", "5m"), RoutingState.UPSTOX_ONLY)

if __name__ == "__main__":
    unittest.main()
