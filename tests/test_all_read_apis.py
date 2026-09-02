import os
import sys
import unittest
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TestAllReadAPIs")

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

class TestAllReadAPIs(unittest.TestCase):

    def test_all_read_endpoints(self):
        import dashboard_server
        from dashboard_server import app

        dashboard_server._session_cache[(1, 'test_token')] = (True, 9999999999)

        read_endpoints = [
            "/health",
            "/version",
            "/api/version",
            "/api/csrf_token",
            "/api/push/vapid_public_key",
            "/api/user_info",
            "/api/notifications",
            "/api/viewers",
            "/api/capital_info",
            "/api/todays_alerts",
            "/api/breakout_watchlist",
            "/api/multibagger/watchlist",
            "/api/multibagger/watchlist?page=1&per_page=20",
            "/api/wealth",
            "/api/indices",
            "/api/all_tickers",
            "/api/near_misses?days=7",
            "/api/admin/near_misses?days=7",
            "/api/near_misses?days=7&page=1&per_page=20",
            "/api/v2/master_summary",
            "/api/v2/master_alerts",
            "/api/v2/stocks_to_watch",
            "/api/v2/investment_watch",
            "/api/v2/portfolio_actions",
            "/api/v2/confluence_breakdown",
            "/api/v2/scanner_health",
            "/api/v2/universe_health",
            "/api/v2/universe_data?tier=ELITE",
            "/api/v2/universe_data?tier=NEAR_QUALIFIED",
            "/api/v2/universe_data?tier=EXCLUDED",
            "/api/admin/users/search?q=&status=all",
            "/api/admin/db/tables_summary",
            "/api/scanner_health",
            "/api/validation_health",
            "/api/data_fetch_health",
            "/api/fetch_errors",
            "/api/fetch_errors/grouped_by_scanner",
            "/api/system_logs",
            "/api/admin/pledge_worker/mode",
            "/api/scanner_execution_history?scanner=ALL&system_version=ALL&page=1&per_page=20",
            "/admin/pending_users",
            "/api/v1/user_watchlist",
            "/api/v1/symbols/master_list",
            "/api/v1/symbols/suggest?q=TAT",
            "/api/v1/analytics/outcomes/advanced",
        ]

        failed = 0
        errors = []

        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 1
                sess['username'] = 'admin'
                sess['role'] = 'admin'
                sess['is_admin'] = True
                sess['session_token'] = 'test_token'

            for endpoint in read_endpoints:
                try:
                    res = client.get(endpoint)
                    status = res.status_code
                    if status != 200:
                        failed += 1
                        errors.append(f"{endpoint} -> HTTP {status}")
                except Exception as e:
                    failed += 1
                    errors.append(f"{endpoint} -> {type(e).__name__}: {str(e)}")

        self.assertEqual(failed, 0, f"Endpoints failed to return HTTP 200: {errors}")

if __name__ == "__main__":
    unittest.main()
