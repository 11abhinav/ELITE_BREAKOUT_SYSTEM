import pytest
from app.database import (
    stop_scanner,
    resume_scanner,
    is_scanner_stopped,
    pause_all_scanners,
    resume_all_scanners,
    normalize_scanner_name
)

def test_normalize_scanner_name():
    assert normalize_scanner_name("EOD") == "EOD"
    assert normalize_scanner_name("eod") == "EOD"
    assert normalize_scanner_name("DAILY_BUILDER") == "DAILY_BUILDER"
    assert normalize_scanner_name("Wealth Engine") == "Wealth Engine"
    assert normalize_scanner_name("multibagger") == "MULTIBAGGER"

def test_stop_and_resume_scanner_db_calls(mocker):
    mock_upsert = mocker.patch("app.database.upsert_scanner_health")
    mock_conn = mocker.patch("app.database.get_connection")
    mock_cur = mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value

    # Stop scanner
    stop_scanner("EOD")
    mock_upsert.assert_called_with("EOD", status="PAUSED", error_msg="Paused by Admin")

    # Resume scanner
    resume_scanner("EOD")
    mock_upsert.assert_called_with("EOD", status="IDLE", error_msg=None)

    # Check is_scanner_stopped logic when status is STOPPED or PAUSED
    mock_cur.fetchone.return_value = ("PAUSED",)
    assert is_scanner_stopped("EOD") is True

    mock_cur.fetchone.return_value = ("STOPPED",)
    assert is_scanner_stopped("EOD") is True

    mock_cur.fetchone.return_value = ("OK",)
    assert is_scanner_stopped("EOD") is False

    mock_cur.fetchone.return_value = None
    assert is_scanner_stopped("EOD") is False

def test_pause_and_resume_all_scanners(mocker):
    mock_upsert = mocker.patch("app.database.upsert_scanner_health")

    pause_all_scanners()
    assert mock_upsert.call_count >= 5

    mock_upsert.reset_mock()
    resume_all_scanners()
    assert mock_upsert.call_count >= 5
