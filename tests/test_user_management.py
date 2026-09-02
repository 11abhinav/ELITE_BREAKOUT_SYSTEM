import os
import sys
import unittest
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TestUserManagement")

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

class TestUserManagement(unittest.TestCase):

    def test_user_management_backend(self):
        from database import search_users, update_user_role, update_user_account_status
        from dashboard_server import app

        # 1. Test database search_users
        users = search_users("", "all")
        if users:
            u0 = users[0]
            self.assertIn("name", u0, "User object must include 'name'")
            self.assertIn("username", u0, "User object must include 'username'")
            self.assertIn("role", u0, "User object must include 'role'")
            self.assertIn("is_active", u0, "User object must include 'is_active'")

        # 2. Test Flask test_client
        import dashboard_server
        dashboard_server._session_cache[(1, 'test_token')] = (True, 9999999999)

        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 1
                sess['username'] = 'admin'
                sess['role'] = 'admin'
                sess['is_admin'] = True
                sess['session_token'] = 'test_token'

            r_info = client.get('/api/user_info')
            self.assertEqual(r_info.status_code, 200)
            data_info = r_info.get_json()
            self.assertEqual(data_info.get("username"), "admin")

            r_search = client.get('/api/admin/users/search?q=&status=all')
            self.assertEqual(r_search.status_code, 200)
            data_search = r_search.get_json()
            self.assertIn("users", data_search)

            r_role = client.post('/api/admin/users/update_role', json={"user_id": 1, "role": "admin"})
            self.assertEqual(r_role.status_code, 200)

if __name__ == "__main__":
    unittest.main()
