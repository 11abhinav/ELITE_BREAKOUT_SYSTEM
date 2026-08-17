# =====================================================================================
# tests/test_single_active_scanner_guard.py
# Tests for Single Active Scanner Global Lock & Duplicate Run Prevention Guard.
# =====================================================================================

import pytest
from unittest.mock import MagicMock, patch
from database import is_scanner_actively_running, cleanup_stale_scanner_runs

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
        sql = mock_cur.execute.call_args_list[-1][0][0]
        assert "LOWER(scanner_name) = LOWER(%s)" in sql

def test_cleanup_stale_scanner_runs():
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    with patch("database.get_connection") as mock_get_conn:
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        cleanup_stale_scanner_runs(max_stale_minutes=5)
        assert mock_cur.execute.called
        sql = mock_cur.execute.call_args[0][0]
        assert "TIMED_OUT" in sql
        assert "Stale heartbeat threshold exceeded" in sql
