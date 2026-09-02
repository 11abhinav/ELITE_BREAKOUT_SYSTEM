import sys
import os
import re
import unittest

# Add app directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app')))

class TestHealthScannerScheduleConsistency(unittest.TestCase):

    def test_health_scanner_schedule_consistency(self):
        """Verify that Accumulation Scanner schedule is synchronized across all layers."""
        app_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app'))
        main_py = os.path.join(app_dir, 'main.py')
        database_py = os.path.join(app_dir, 'database.py')
        admin_html = os.path.join(app_dir, 'admin_dashboard.html')

        with open(main_py, 'r') as f:
            main_content = f.read()

        with open(database_py, 'r') as f:
            db_content = f.read()

        with open(admin_html, 'r') as f:
            admin_content = f.read()

        # 1. Verify Bhavcopy readiness start is 18:30 IST
        self.assertIn("18:30", main_content)
        # 2. Verify Accumulation scanner trigger is 18:35 IST
        self.assertIn("18:35", main_content)
        
        # 3. Verify Health Scanner timing display #1 (database schedule_map)
        self.assertIn('"ACCUMULATION": "Daily 18:35 IST (Post-Bhavcopy / Verified Evening Batch)"', db_content)

        # 4. Verify Health Scanner timing display #2 (admin dashboard SCANNER_CONFIG / descriptions)
        self.assertIn("'ACCUMULATION': { label: 'Accumulation Scanner', desc: 'Post-Bhavcopy Delivery Scan · Daily 18:35 IST' }", admin_content)

        # 5. Verify no stale 16:15 references exist in active scanner schedules
        self.assertNotIn("16:15", main_content)
        self.assertNotIn("16:15", db_content)
        self.assertNotIn("16:15", admin_content)

if __name__ == '__main__':
    unittest.main()
