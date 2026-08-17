# =====================================================================================
# tests/test_single_active_scanner_guard.py
# Tests for Single Active Scanner Global Lock & Duplicate Run Prevention Guard.
# =====================================================================================

import pytest
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
        sql = mock_cur.execute.call_args[0][0]
        assert "LOWER(scanner_name) = LOWER(%s)" in sql

def test_process_lock_accepts_timeout_kwarg():
    lock = ProcessLock("test_lock_kwarg")
    assert lock.acquire(blocking=False, timeout=1.0) is True
    lock.release()
