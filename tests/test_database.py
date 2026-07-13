import pytest
import sqlite3
from app.database import classify_error_severity, upsert_scanner_health, init_db

def test_classify_error_severity():
    # IGNORABLE cases
    assert classify_error_severity("yfinance timeout") == "IGNORABLE"
    assert classify_error_severity("no data found") == "IGNORABLE"
    assert classify_error_severity("stock not found in Fyers") == "IGNORABLE"
    
    # CRITICAL cases
    assert classify_error_severity("database locked") == "CRITICAL"
    assert classify_error_severity("syntax error") == "CRITICAL"
    assert classify_error_severity(None) == None

def test_upsert_scanner_health_ok_status(mocker):
    # Mock get_connection to avoid hitting real DB during testing
    mock_conn = mocker.patch("app.database.get_connection")
    mock_cur = mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    
    upsert_scanner_health(scanner_name="TEST_SCANNER", status="OK")
    
    execute_calls = mock_cur.execute.call_args_list
    assert len(execute_calls) > 0
    
    target_sql = None
    target_params = None
    for call in execute_calls:
        sql = call[0][0]
        if "INSERT INTO scanner_health" in sql or "UPDATE scanner_health" in sql:
            target_sql = sql
            target_params = call[0][1] if len(call[0]) > 1 else []
            break
            
    assert target_sql is not None, "Failed to find scanner_health insert/update query"
    assert "status = %s" in target_sql
    assert "OK" in target_params
    assert "last_success = %s" in target_sql

def test_init_db_creates_tables(mocker):
    # Reset the global state to ensure init_db actually runs
    import app.database
    app.database._DB_INITIALIZED = False
    
    mock_conn = mocker.patch("app.database.get_connection")
    mock_cur = mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    
    init_db()
    
    # Extract all executed SQL queries
    execute_calls = mock_cur.execute.call_args_list
    queries = [call[0][0].lower() for call in execute_calls]
    
    # Verify core tables are created
    assert any("create table if not exists alerts" in q for q in queries)
    assert any("create table if not exists scanner_health" in q for q in queries)
    assert any("create table if not exists breakout_watchlist" in q for q in queries)

def test_save_alert_with_nan_sanitization(mocker):
    import json
    from app.database import save_alert_if_new
    
    mock_conn = mocker.patch("app.database.get_connection")
    mock_cur = mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    mock_cur.fetchone.return_value = None  # No duplicates
    mock_cur.rowcount = 1
    
    mocker.patch("app.database.DONT_SAVE_ALERTS", False)
    mocker.patch("portfolio_engine.calculate_trade_allocation", return_value=(10000.0, 100))
    # Mock live_prices.get_live_prices if needed
    try:
        mocker.patch("live_prices.get_live_prices", return_value={})
    except Exception:
        pass


    
    context_with_nan = {
        "peg": float("nan"),
        "inf_val": float("inf"),
        "nested": {"val": float("nan")},
        "valid": 42.0
    }
    
    save_alert_if_new(
        symbol="TESTSTOCK",
        breakout_type="resistance",
        alert_time="2026-07-13 15:00:00",
        scanner="TEST_SCAN",
        category="CAT",
        entry_price=100.0,
        stop_loss=90.0,
        target_price=120.0,
        signals="sig",
        score=85,
        rsi=60.0,
        volume_ratio=2.0,
        context=context_with_nan
    )
    
    # Assert query execution parameters
    execute_calls = mock_cur.execute.call_args_list
    insert_call = None
    for call in execute_calls:
        sql = call[0][0]
        if "INSERT INTO alerts" in sql:
            insert_call = call
            break
            
    assert insert_call is not None
    params = insert_call[0][1]
    context_json_str = params[12]
    
    context_dict = json.loads(context_json_str)
    assert context_dict["peg"] is None
    assert context_dict["inf_val"] is None
    assert context_dict["nested"]["val"] is None
    assert context_dict["valid"] == 42.0

