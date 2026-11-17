"""
Unit tests for Admin Session Zero Inactivity Timeout Policy.
Verifies that:
  1. Admin and superuser sessions NEVER expire or get revoked due to inactivity.
  2. Legacy revoked admin sessions (from old 12-hour inactivity cleanup) are automatically un-revoked/healed.
  3. Non-admin user sessions continue to enforce the 12-hour inactivity policy.
  4. cleanup_stale_sessions() SQL explicitly protects admin and superuser accounts from being revoked.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo
import pytest

from app import database


def test_admin_session_never_expires_on_inactivity():
    """Verify that an admin inactive for > 48 hours is NEVER logged out or revoked."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    # Simulating an admin user whose last activity was 48 hours ago
    last_active = datetime.now(ZoneInfo("Asia/Kolkata")) - timedelta(hours=48)
    # Row: (u.is_active, s.is_revoked, s.logoff_time, s.login_time, u.role)
    mock_cur.fetchone.return_value = (True, False, last_active, last_active, 'admin')

    with patch.object(database, "get_connection") as mock_get_conn:
        mock_get_conn.return_value.__enter__.return_value = mock_conn

        is_valid = database.check_session_validity(user_id=1, session_token="admin-token-123")

        assert is_valid is True, "Admin session should remain valid despite 48h of inactivity"

        # Verify that is_revoked = TRUE was NEVER executed
        executed_sqls = [call[0][0] for call in mock_cur.execute.call_args_list]
        for sql in executed_sqls:
            assert "is_revoked = TRUE" not in sql, "Admin session must never be marked revoked"


def test_superuser_session_never_expires_on_inactivity():
    """Verify that a superuser inactive for > 7 days is NEVER logged out or revoked."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    last_active = datetime.now(ZoneInfo("Asia/Kolkata")) - timedelta(days=7)
    mock_cur.fetchone.return_value = (True, False, last_active, last_active, 'superuser')

    with patch.object(database, "get_connection") as mock_get_conn:
        mock_get_conn.return_value.__enter__.return_value = mock_conn

        is_valid = database.check_session_validity(user_id=99, session_token="super-token-999")

        assert is_valid is True, "Superuser session should remain valid despite 7 days of inactivity"


def test_admin_session_auto_unrevokes_if_previously_revoked():
    """Verify that if an active admin session was previously revoked by old cleanup, it is auto-restored."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    last_active = datetime.now(ZoneInfo("Asia/Kolkata")) - timedelta(hours=24)
    # Row has is_revoked = True (e.g. from old 12-hour inactivity cleanup)
    mock_cur.fetchone.return_value = (True, True, last_active, last_active, 'admin')

    with patch.object(database, "get_connection") as mock_get_conn:
        mock_get_conn.return_value.__enter__.return_value = mock_conn

        is_valid = database.check_session_validity(user_id=1, session_token="admin-token-123")

        assert is_valid is True, "Admin session should be auto-restored and validated"

        # Verify that un-revoke UPDATE was executed
        executed_sqls = [call[0][0] for call in mock_cur.execute.call_args_list]
        unrevoke_executed = any("is_revoked = FALSE" in sql for sql in executed_sqls)
        assert unrevoke_executed, "Expected an UPDATE setting is_revoked = FALSE for admin"


def test_non_admin_session_expires_after_12_hours():
    """Verify that regular non-admin users still expire after 12 hours of inactivity."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    # Non-admin user inactive for 13 hours
    last_active = datetime.now(ZoneInfo("Asia/Kolkata")) - timedelta(hours=13)
    mock_cur.fetchone.return_value = (True, False, last_active, last_active, 'user')

    with patch.object(database, "get_connection") as mock_get_conn:
        mock_get_conn.return_value.__enter__.return_value = mock_conn

        is_valid = database.check_session_validity(user_id=42, session_token="user-token-42")

        assert is_valid is False, "Non-admin session should expire after 12+ hours of inactivity"

        # Verify that is_revoked = TRUE was executed
        executed_sqls = [call[0][0] for call in mock_cur.execute.call_args_list]
        revoke_executed = any("is_revoked = TRUE" in sql for sql in executed_sqls)
        assert revoke_executed, "Expected an UPDATE setting is_revoked = TRUE for non-admin"


def test_cleanup_stale_sessions_sql_protects_admins():
    """Verify that cleanup_stale_sessions SQL query excludes admin and superuser roles."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    with patch.object(database, "get_connection") as mock_get_conn:
        mock_get_conn.return_value.__enter__.return_value = mock_conn

        database.cleanup_stale_sessions()

        executed_sqls = [call[0][0] for call in mock_cur.execute.call_args_list]
        revoke_sqls = [s for s in executed_sqls if "is_revoked = TRUE" in s]
        assert len(revoke_sqls) > 0, "Expected a session revocation query"
        for s in revoke_sqls:
            assert "admin" in s.lower() and "superuser" in s.lower(), \
                "Revocation query must explicitly exclude admin and superuser roles"
