import unittest
import sys
import os

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app'))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from zero_alert_diagnostic import classify_zero_alert_run, SingleTerminalTracker, StageWaterfallTracker


class TestZeroAlertDiagnosticLifecycle(unittest.TestCase):
    """
    Validates institutional zero-alert classification hierarchy:
    1. DATA/ENGINE FAILURE
    2. PERSISTENCE / LIFECYCLE FAILURE (including PREARM setup discrepancy and MONITOR overnight load failure)
    3. DEEP FUNNEL COLLAPSE (SUSPICIOUS_ZERO)
    4. NO VIABLE STRUCTURES (LEGITIMATE_ZERO)
    5. PREARM / OUTSIDE_WINDOW (LEGITIMATE_ZERO with verified persistence)
    """

    def test_data_or_engine_failure(self):
        """Coverage < 75% triggers DATA_OR_ENGINE_FAILURE regardless of mode."""
        res = classify_zero_alert_run(
            scanner_name="TEST_SCANNER",
            universe_size=100,
            valid_data_count=50, # 50% coverage
            initial_setups_count=0,
            finalist_candidates_count=0,
            alerts_generated=0,
            execution_mode="PREARM"
        )
        self.assertEqual(res["classification"], "DATA_OR_ENGINE_FAILURE")
        self.assertEqual(res["severity"], "CRITICAL")

    def test_finalists_existed_triggers_critical_zero(self):
        """Finalists existed but 0 alerts persisted triggers CRITICAL_ZERO."""
        res = classify_zero_alert_run(
            scanner_name="MULTIBAGGER",
            universe_size=748,
            valid_data_count=748,
            initial_setups_count=10,
            finalist_candidates_count=3,
            alerts_generated=0,
            execution_mode="LIVE"
        )
        self.assertEqual(res["classification"], "CRITICAL_ZERO")
        self.assertEqual(res["severity"], "CRITICAL")
        self.assertIn("3 candidates reached final risk/persistence gate", res["explanation"])

    def test_direct_persistence_failure_triggers_critical_zero(self):
        """Direct database write failures trigger CRITICAL_ZERO."""
        res = classify_zero_alert_run(
            scanner_name="EOD",
            universe_size=748,
            valid_data_count=748,
            initial_setups_count=5,
            finalist_candidates_count=0,
            alerts_generated=0,
            persistence_failures_count=2,
            execution_mode="LIVE"
        )
        self.assertEqual(res["classification"], "CRITICAL_ZERO")
        self.assertEqual(res["severity"], "CRITICAL")

    def test_prearm_armed_vs_db_discrepancy_triggers_critical_zero(self):
        """
        User scenario: 100 candidates armed in PREARM, but 0 persisted to DB.
        Must NOT be classified as LEGITIMATE_ZERO; must be CRITICAL_ZERO.
        """
        # Case A: candidates_persisted_count == 0
        res_a = classify_zero_alert_run(
            scanner_name="MULTI_TF",
            universe_size=748,
            valid_data_count=748,
            initial_setups_count=100,
            finalist_candidates_count=0,
            alerts_generated=0,
            execution_mode="PREARM",
            candidates_persisted_count=0
        )
        self.assertEqual(res_a["classification"], "CRITICAL_ZERO")
        self.assertEqual(res_a["severity"], "CRITICAL")
        self.assertIn("0 records persisted to watchlist database", res_a["explanation"])

        # Case B: lifecycle_summary shows total_in_watchlist == 0
        lifecycle_empty = {"total_in_watchlist": 0, "active_substates": 0, "live_monitor_eligible": 0}
        res_b = classify_zero_alert_run(
            scanner_name="MULTI_TF",
            universe_size=748,
            valid_data_count=748,
            initial_setups_count=100,
            finalist_candidates_count=0,
            alerts_generated=0,
            execution_mode="PREARM",
            lifecycle_summary=lifecycle_empty
        )
        self.assertEqual(res_b["classification"], "CRITICAL_ZERO")
        self.assertEqual(res_b["severity"], "CRITICAL")

    def test_monitor_overnight_survival_load_discrepancy_triggers_critical_zero(self):
        """
        User scenario: 30 candidates survived overnight & monitor-eligible, but 0 loaded into live monitor.
        Must NOT be classified as LEGITIMATE_ZERO; must be CRITICAL_ZERO.
        """
        lifecycle_survived = {
            "total_in_watchlist": 45,
            "active_substates": 30,
            "in_cooldown": 5,
            "invalidated": 10,
            "live_monitor_eligible": 30
        }
        res = classify_zero_alert_run(
            scanner_name="MULTI_TF_5M",
            universe_size=0, # 0 loaded
            valid_data_count=0,
            initial_setups_count=0,
            finalist_candidates_count=0,
            alerts_generated=0,
            execution_mode="MONITOR",
            lifecycle_summary=lifecycle_survived
        )
        self.assertEqual(res["classification"], "CRITICAL_ZERO")
        self.assertEqual(res["severity"], "CRITICAL")
        self.assertIn("30 candidates survived overnight and are live-monitor eligible, but 0 were loaded", res["explanation"])

    def test_deep_funnel_collapse_suspicious_zero(self):
        """Candidates penetrated past stage 2 into scoring/risk, but eliminated before alerts."""
        waterfall = [
            {"stage": "UNIVERSE", "entered": 748, "passed": 236, "eliminated": 512, "attrition_pct": 68.5},
            {"stage": "BREAKOUT_STRUCTURE", "entered": 236, "passed": 56, "eliminated": 180, "attrition_pct": 76.3},
            {"stage": "QUALITY_AND_RISK", "entered": 56, "passed": 0, "eliminated": 56, "attrition_pct": 100.0}
        ]
        res = classify_zero_alert_run(
            scanner_name="EOD",
            universe_size=748,
            valid_data_count=748,
            initial_setups_count=236,
            finalist_candidates_count=0,
            alerts_generated=0,
            near_miss_count=4,
            regime="BEAR",
            execution_mode="LIVE",
            stage_waterfall=waterfall
        )
        self.assertEqual(res["classification"], "SUSPICIOUS_ZERO")
        self.assertEqual(res["severity"], "WARNING")
        self.assertIn("QUALITY_AND_RISK", res["explanation"])

    def test_clean_legitimate_zero_no_structures(self):
        """Clean legitimate zero when 0 structures form in market."""
        res = classify_zero_alert_run(
            scanner_name="REVERSAL",
            universe_size=748,
            valid_data_count=748,
            initial_setups_count=0,
            finalist_candidates_count=0,
            alerts_generated=0,
            near_miss_count=0,
            regime="BEAR",
            execution_mode="LIVE"
        )
        self.assertEqual(res["classification"], "LEGITIMATE_ZERO")
        self.assertEqual(res["severity"], "INFO")
        self.assertIn("Clean legitimate zero", res["explanation"])

    def test_clean_prearm_with_persisted_candidates(self):
        """PREARM mode with verified DB persistence properly classified as LEGITIMATE_ZERO with context."""
        lifecycle_healthy = {
            "total_in_watchlist": 25,
            "active_substates": 20,
            "in_cooldown": 2,
            "invalidated": 3,
            "live_monitor_eligible": 20
        }
        res = classify_zero_alert_run(
            scanner_name="MULTI_TF",
            universe_size=748,
            valid_data_count=748,
            initial_setups_count=20,
            finalist_candidates_count=0,
            alerts_generated=0,
            execution_mode="PREARM",
            candidates_persisted_count=25,
            lifecycle_summary=lifecycle_healthy
        )
        self.assertEqual(res["classification"], "LEGITIMATE_ZERO")
        self.assertEqual(res["severity"], "INFO")
        self.assertIn("25 verified in DB", res["explanation"])

    def test_single_terminal_conservation_invariant(self):
        """Guarantees sum(terminal outcomes) == universe_size with delta = 0."""
        symbols = [f"SYM_{i}" for i in range(100)]
        tracker = SingleTerminalTracker(universe=symbols, scanner_name="TEST")
        for sym in symbols[:30]:
            tracker.record_terminal(sym, "GATE_1_FAIL")
        for sym in symbols[30:50]:
            tracker.record_terminal(sym, "GATE_2_FAIL")
        for sym in symbols[50:60]:
            tracker.record_terminal(sym, "ALERT_GENERATED")
        # Sweep remainder (remaining 40)
        tracker.record_untracked_remainder()

        summary = tracker.get_summary()
        self.assertEqual(summary["total_universe"], 100)
        self.assertEqual(summary["sum_terminal"], 100)
        self.assertEqual(summary["conservation_delta"], 0)
        self.assertEqual(summary["terminal_counts"]["UNTRACKED_DROP"], 40)


if __name__ == "__main__":
    unittest.main()
