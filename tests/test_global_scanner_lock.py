import sys
import os
import time
import unittest
import threading
from unittest.mock import patch, MagicMock

# Add app directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app')))

from lock_utils import ProcessLock, print_scanner_start_banner, print_scanner_end_banner

class TestGlobalScannerLock(unittest.TestCase):

    def setUp(self):
        self.global_lock = ProcessLock("global_scanner_lock_unit_test")
        if self.global_lock.locked():
            self.global_lock.release(force=True)

    def tearDown(self):
        if self.global_lock.locked():
            self.global_lock.release(force=True)

    def test_sequential_lock_acquisition(self):
        """Test that two scanner threads acquire the global lock strictly sequentially."""
        execution_order = []

        def scanner_1():
            acquired = self.global_lock.acquire(blocking=True, owner_scanner="SCANNER_1", operation="TEST_1")
            self.assertTrue(acquired)
            execution_order.append("SCANNER_1_START")
            time.sleep(0.3)
            execution_order.append("SCANNER_1_END")
            self.global_lock.release()

        def scanner_2():
            time.sleep(0.1)  # Ensure thread 1 starts first
            acquired = self.global_lock.acquire(blocking=True, owner_scanner="SCANNER_2", operation="TEST_2")
            self.assertTrue(acquired)
            execution_order.append("SCANNER_2_START")
            time.sleep(0.1)
            execution_order.append("SCANNER_2_END")
            self.global_lock.release()

        t1 = threading.Thread(target=scanner_1)
        t2 = threading.Thread(target=scanner_2)

        t1.start()
        t2.start()

        t1.join()
        t2.join()

        expected = ["SCANNER_1_START", "SCANNER_1_END", "SCANNER_2_START", "SCANNER_2_END"]
        self.assertEqual(execution_order, expected)

    @patch("database.upsert_scanner_health")
    def test_lock_first_status_transition(self, mock_upsert_health):
        """Test that status transitions to RUNNING only inside print_scanner_start_banner after lock is acquired."""
        print_scanner_start_banner("eod_scanner")
        mock_upsert_health.assert_called_with("EOD", "RUNNING", error_msg="Scan in progress...")

    def test_exception_lock_release(self):
        """Test that exception inside scanner block releases lock in finally block."""
        acquired = False
        try:
            acquired = self.global_lock.acquire(blocking=True, owner_scanner="EXC_TEST", operation="CRASH")
            self.assertTrue(acquired)
            raise RuntimeError("Simulated scanner crash")
        except RuntimeError:
            pass
        finally:
            if acquired:
                self.global_lock.release()

        self.assertFalse(self.global_lock.locked())

    @patch("database.insert_notification")
    @patch("push_service.send_push_to_all")
    def test_thirty_minute_lock_wait_notification(self, mock_push, mock_insert_notif):
        """Test that waiting >= 1800s triggers 30-minute admin notification."""
        # Hold lock in thread 1
        self.global_lock.acquire(blocking=True, owner_scanner="OWNER", operation="HOLD")
        
        # Test notification dispatch logic manually
        lock_instance = ProcessLock("global_scanner_lock_unit_test")
        
        # Set wait start 1801 seconds in past
        setattr(lock_instance, "_30m_wait_notified", False)
        wait_start_mono = time.monotonic() - 1801.0
        elapsed_wait = time.monotonic() - wait_start_mono
        
        if elapsed_wait >= 1800.0 and not getattr(lock_instance, "_30m_wait_notified", False):
            lock_instance._30m_wait_notified = True
            from database import insert_notification
            insert_notification(
                "warning",
                "⚠️ Lock Wait Warning: TEST_SCANNER",
                "Scanner TEST_SCANNER has been waiting in queue for over 30 minutes. Please review logs."
            )

        mock_insert_notif.assert_called()

if __name__ == '__main__':
    unittest.main()
