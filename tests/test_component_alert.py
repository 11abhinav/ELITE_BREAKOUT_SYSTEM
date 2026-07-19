import pytest
from unittest.mock import patch, MagicMock
from app.message_formatter import format_alert
from app.database import save_alert_if_new

def test_alert_formatting():
    """
    Test that the alert message formatter correctly processes an alert dictionary
    and includes essential details like PEGs, Moat, and Trade targets.
    """
    alert_dict = {
        "symbol": "TEST.NS",
        "score": 96,
        "category": "Wealth Compounder",
        "breakout_signals": "MULTI_TF Ladder",
        "price": 105.0,
        "volume_ratio": 2.5,
        "peg": 0.8,
        "yoy_rev": 25.0,
        "yoy_profit": 35.0,
        "roe": 22.0,
        "open": 100.0,
        "day_high": 110.0,
        "entry_price": 106.0,
        "stop_loss": 98.0,
        "target_1": 120.0,
        "target_2": 135.0
    }

    message = format_alert(alert_dict, scanner="MULTI_TF")
    
    assert message is not None
    assert "TEST.NS" in message
    assert "ELITE" in message # Score >= 95
    assert "DEEP VALUE" in message # PEG < 1.0
    assert "25.0%" in message # YoY Rev
    assert "35.0%" in message # YoY Profit
    assert "98.0" in message # Stop Loss
    assert "120.0" in message # Target 1

@patch("app.database.check_recent_alert")
@patch("portfolio_engine.calculate_trade_allocation")
@patch("app.database.get_connection")
def test_alert_deduplication(mock_get_connection, mock_calculate, mock_check_recent):
    """
    Test that save_alert_if_new uses ON CONFLICT DO NOTHING to prevent duplicate alerts
    for the same symbol, breakout type, and scanner on the same day.
    """
    # Setup mock connection and cursor
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_get_connection.return_value.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_calculate.return_value = (50000.0, 50)
    mock_check_recent.return_value = False

    # Simulate that the insert affects 0 rows (duplicate)
    mock_cursor.fetchone.return_value = None
    mock_cursor.rowcount = 0

    inserted, reason, cap, shares = save_alert_if_new(
        symbol="TEST.NS",
        breakout_type="MULTI_TF",
        alert_time="2026-07-19 10:00:00+05:30",
        scanner="MULTI_TF",
        category="Wealth Compounder",
        entry_price=106.0,
        stop_loss=98.0,
        target_1=120.0,
        score=96
    )

    # Verify deduplication was handled
    assert inserted is False
    assert reason == "DB CONFLICT (Duplicate)"

    # Verify the executed SQL contains the conflict clause
    executed_sql = mock_cursor.execute.call_args[0][0]
    assert "ON CONFLICT" in executed_sql
    assert "DO NOTHING" in executed_sql

@patch("app.database.check_recent_alert")
@patch("portfolio_engine.calculate_trade_allocation")
@patch("app.database.get_connection")
def test_alert_save_success(mock_get_connection, mock_calculate, mock_check_recent):
    """
    Test that save_alert_if_new successfully saves an alert when not a duplicate.
    """
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_get_connection.return_value.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_calculate.return_value = (50000.0, 50)
    mock_check_recent.return_value = False

    # Simulate that the insert affects 1 row (success)
    mock_cursor.fetchone.return_value = None
    mock_cursor.rowcount = 1

    inserted, reason, cap, shares = save_alert_if_new(
        symbol="TEST.NS",
        breakout_type="MULTI_TF",
        alert_time="2026-07-19 10:00:00+05:30",
        scanner="MULTI_TF",
        category="Wealth Compounder",
        entry_price=106.0,
        stop_loss=98.0,
        target_1=120.0,
        score=96
    )

    assert inserted is True
    assert reason == "Inserted"
