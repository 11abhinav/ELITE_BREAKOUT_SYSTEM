"""
tests/test_push_subscriptions.py
================================
Unit tests verifying push subscription database queries and operations.
Ensures zero NameError, UnboundLocalError, or cursor fetch regressions.
"""

import pytest
from unittest.mock import patch, MagicMock

from app.database import (
    get_all_push_subscriptions,
    save_push_subscription,
    remove_push_subscription,
)


def test_get_all_push_subscriptions_empty():
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    with patch("app.database.get_connection") as mock_get_conn:
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        subs = get_all_push_subscriptions()
        assert subs == []
        mock_cursor.execute.assert_called_once_with(
            "SELECT user_id, endpoint, p256dh, auth FROM push_subscriptions"
        )
        mock_cursor.fetchall.assert_called_once()


def test_get_all_push_subscriptions_tuple_rows():
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [
        (1, "https://push.example.com/sub1", "key_p256dh_1", "key_auth_1"),
        (2, "https://push.example.com/sub2", "key_p256dh_2", "key_auth_2"),
    ]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    with patch("app.database.get_connection") as mock_get_conn:
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        subs = get_all_push_subscriptions()
        assert len(subs) == 2
        assert subs[0] == {
            "user_id": 1,
            "endpoint": "https://push.example.com/sub1",
            "p256dh": "key_p256dh_1",
            "auth": "key_auth_1",
        }
        assert subs[1]["user_id"] == 2


def test_get_all_push_subscriptions_dict_rows():
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [
        {"user_id": 10, "endpoint": "https://push.example.com/sub10", "p256dh": "key10", "auth": "auth10"}
    ]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    with patch("app.database.get_connection") as mock_get_conn:
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        subs = get_all_push_subscriptions()
        assert len(subs) == 1
        assert subs[0]["endpoint"] == "https://push.example.com/sub10"


def test_remove_push_subscription():
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    with patch("app.database.get_connection") as mock_get_conn:
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        ok = remove_push_subscription("https://push.example.com/sub_stale")
        assert ok is True
        mock_cursor.execute.assert_called_once()
        mock_conn.commit.assert_called_once()
