# =====================================================================================
# tests/test_single_active_scanner_guard.py
# Tests for Single Active Scanner Global Lock & Reentrant Distributed Lock.
# =====================================================================================

import pytest
import threading
from unittest.mock import MagicMock, patch
from database import is_scanner_actively_running
from lock_utils import ProcessLock

def test_is_scanner_actively_running():
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = ("run-12345",)
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    with patch("database.get_connection") as mock_get_conn:
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        active = is_scanner_actively_running("MULTIBAGGER")
        assert active is True
        assert mock_cur.execute.called

def test_process_lock_reentrant_same_thread():
    with patch.dict("os.environ", {"DATABASE_URL": ""}, clear=False):
        lock1 = ProcessLock("test_reentrant_lock")
        lock2 = ProcessLock("test_reentrant_lock")
        
        assert lock1 is lock2  # Singleton check

        acquired1 = lock1.acquire(blocking=True)
        assert acquired1 is True

        # Same thread acquiring second time must NOT deadlock (Reentrant check)
        acquired2 = lock2.acquire(blocking=True)
        assert acquired2 is True

        lock2.release()
        lock1.release()
        assert lock1.locked() is False

def test_process_lock_blocks_different_thread():
    with patch.dict("os.environ", {"DATABASE_URL": ""}, clear=False):
        lock1 = ProcessLock("test_multi_thread")
        assert lock1.acquire(blocking=True) is True

        thread_result = []
        def second_thread():
            lock2 = ProcessLock("test_multi_thread")
            res = lock2.acquire(blocking=False)
            thread_result.append(res)

        t = threading.Thread(target=second_thread)
        t.start()
        t.join()

        assert thread_result == [False]  # Blocked across threads!
        lock1.release()

