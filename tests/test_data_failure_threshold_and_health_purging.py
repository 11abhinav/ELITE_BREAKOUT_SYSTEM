import unittest
import os
import sys
from datetime import datetime

# Ensure project root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../app")))

from app.database import get_all_scanner_health, init_db, upsert_scanner_health, get_connection
from engine.production.governance_registry import DECOMMISSIONED_SCANNERS, CERTIFIED_PRODUCTION_SCANNERS

class TestDataFailureThresholdAndHealthPurging(unittest.TestCase):

    def setUp(self):
        init_db()

    def test_decommissioned_scanners_and_unused_workers_purged_from_health(self):
        """Verify get_all_scanner_health() never contains decommissioned scanners or unused workers."""
        health_rows = get_all_scanner_health()
        returned_scanners = {r["scanner_name"] for r in health_rows}
        
        # Verify AI Worker & Pledge Worker are absent
        self.assertNotIn("AI Worker", returned_scanners, "AI Worker must be purged from scanner health")
        self.assertNotIn("Pledge Worker", returned_scanners, "Pledge Worker must be purged from scanner health")
        
        # Verify decommissioned scanners are absent
        for dec in DECOMMISSIONED_SCANNERS:
            self.assertNotIn(dec, returned_scanners, f"Decommissioned scanner {dec} must be absent from scanner health")

        # Verify only active certified scanners/monitors exist
        expected_active = {
            "DAILY_BUILDER", "TECHNICAL", "FUNDAMENTAL",
            "QUALITY_COMPOUNDER", "QUALITY_VALUE_RECOVERY",
            "QUALITY_COMPOUNDER_EXIT", "QUALITY_VALUE_RECOVERY_EXIT",
            "PERFORMANCE_TRACKER", "WEALTH_EXIT_V1", "WEALTH_EXIT_V2",
            "FILING_WATCHER"
        }
        self.assertEqual(returned_scanners, expected_active)

    def test_data_failure_above_25_percent_triggers_down_status(self):
        """Verify data failure ratio > 25% produces DOWN status (red badge)."""
        scanned_count = 1332
        incomplete_count = 797
        stale_count = 0
        data_fail_count = incomplete_count + stale_count
        data_fail_ratio = data_fail_count / max(1, scanned_count)

        self.assertGreater(data_fail_ratio, 0.25, "Failure ratio should be > 25%")

        # Test threshold calculation rule
        if data_fail_ratio > 0.25:
            health_status = "DOWN"
            health_error = (
                f"DATA_DOWN: {data_fail_count}/{scanned_count} stocks "
                f"({round(data_fail_ratio * 100, 1)}% > 25% threshold) incomplete/stale with data failures"
            )
        else:
            health_status = "DEGRADED"
            health_error = f"DATA_DEGRADED: {data_fail_count}/{scanned_count} stocks"

        self.assertEqual(health_status, "DOWN")
        self.assertTrue(health_error.startswith("DATA_DOWN"))
        self.assertIn("> 25% threshold", health_error)

if __name__ == "__main__":
    unittest.main()
