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
    mocker.patch("app.database.push_service", create=True)
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
    context_json_str = params[16]
    
    context_dict = json.loads(context_json_str)
    assert context_dict["peg"] is None
    assert context_dict["inf_val"] is None
    assert context_dict["nested"]["val"] is None
    assert context_dict["valid"] == 42.0

# =====================================================================================
# [VERSION: CONCALL_CACHE_QUERY_SAFETY_v1.0]
# Tests to ensure ai_concall_cache_v3 queries NEVER regress to fragile TEXT timestamp
# casting (e.g. SUBSTRING(created_at, 1, 26)::TIMESTAMP which breaks on 5-digit microseconds).
# Root cause: commit d6bf25c1 introduced SUBSTRING hack that broke on rows like
# '2026-06-14 12:41:10.76633+' (5 microsecond digits, + at position 26).
# Fix: column migrated to TIMESTAMPTZ. Queries must use direct >= comparison.
# =====================================================================================

def test_has_valid_concall_cache_returns_true_when_row_exists(mocker):
    """has_valid_concall_cache must return True when a non-error row exists for symbol."""
    from app.database import has_valid_concall_cache
    mock_conn = mocker.patch("app.database.get_connection")
    mock_cur = mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    mock_cur.fetchone.return_value = (1,)  # Row found

    result = has_valid_concall_cache("TESTSTOCK")

    assert result is True
    sql = mock_cur.execute.call_args[0][0]
    # Must NOT use fragile SUBSTRING or TEXT casting — query must be simple
    assert "SUBSTRING" not in sql, "REGRESSION: SUBSTRING cast detected — breaks on 5-digit microseconds"
    assert "::TIMESTAMP" not in sql, "REGRESSION: ::TIMESTAMP cast detected — use TIMESTAMPTZ column directly"
    assert "ai_concall_cache_v3" in sql
    assert "error" in sql


def test_has_valid_concall_cache_returns_false_when_no_row(mocker):
    """has_valid_concall_cache must return False when no non-error row exists."""
    from app.database import has_valid_concall_cache
    mock_conn = mocker.patch("app.database.get_connection")
    mock_cur = mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    mock_cur.fetchone.return_value = None  # No row

    result = has_valid_concall_cache("UNKNOWNSYMBOL")
    assert result is False


def test_has_error_concall_cache_query_uses_timestamptz_comparison(mocker):
    """has_error_concall_cache_within_24h must NOT use SUBSTRING or TEXT casting.
    The query must use direct TIMESTAMPTZ >= comparison after column migration."""
    from app.database import has_error_concall_cache_within_24h
    mock_conn = mocker.patch("app.database.get_connection")
    mock_cur = mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    mock_cur.fetchone.return_value = (1,)

    result = has_error_concall_cache_within_24h("TESTSTOCK")

    assert result is True
    sql = mock_cur.execute.call_args[0][0]
    # [VERSION: CONCALL_CACHE_QUERY_SAFETY_v1.0] Guard against SUBSTRING regression
    assert "SUBSTRING" not in sql, (
        "REGRESSION DETECTED: SUBSTRING(created_at, 1, 26)::TIMESTAMP breaks on rows with "
        "5-digit microseconds (e.g. '2026-06-14 12:41:10.76633+'). Column is now TIMESTAMPTZ — "
        "use 'created_at >= NOW() - INTERVAL ...' directly."
    )
    assert "regexp_replace" not in sql, (
        "REGRESSION DETECTED: regexp_replace timestamp hack detected. Column is TIMESTAMPTZ — "
        "use direct comparison."
    )
    assert "created_at" in sql
    assert "INTERVAL" in sql


def test_get_recent_concall_analysis_returns_data_when_found(mocker):
    """get_recent_concall_analysis must return analysis_data dict when row found,
    and query must NOT use fragile SUBSTRING/TEXT casting."""
    from app.database import get_recent_concall_analysis
    mock_conn = mocker.patch("app.database.get_connection")
    mock_cur = mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    expected = {"management_confidence": 8, "summary": "Strong growth"}
    mock_cur.fetchone.return_value = (expected,)

    result = get_recent_concall_analysis("TESTSTOCK", max_age_days=60)

    assert result == expected
    sql = mock_cur.execute.call_args[0][0]
    # [VERSION: CONCALL_CACHE_QUERY_SAFETY_v1.0] Permanently guard clean query
    assert "SUBSTRING" not in sql, (
        "REGRESSION: SUBSTRING cast found. This breaks when microseconds are 5 digits. "
        "created_at is now TIMESTAMPTZ — use direct >= comparison."
    )
    assert "::TIMESTAMP" not in sql, (
        "REGRESSION: ::TIMESTAMP cast found. Use TIMESTAMPTZ column directly."
    )
    assert "created_at >=" in sql or "created_at>=" in sql, (
        "Query must use direct TIMESTAMPTZ comparison: 'created_at >= NOW() - INTERVAL ...'"
    )


def test_get_recent_concall_analysis_returns_none_when_not_found(mocker):
    """get_recent_concall_analysis must return None when no recent cache exists."""
    from app.database import get_recent_concall_analysis
    mock_conn = mocker.patch("app.database.get_connection")
    mock_cur = mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    mock_cur.fetchone.return_value = None

    result = get_recent_concall_analysis("NOSUCHSYMBOL", max_age_days=60)
    assert result is None

def test_upsert_scanner_health_first_insert(mocker):
    mock_conn = mocker.patch("app.database.get_connection")
    mock_cur = mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    
    # Simulate first insert with last_success and error_msg
    upsert_scanner_health(
        scanner_name="NEW_SCANNER", 
        status="OK", 
        last_success="2026-07-24T12:00:00",
        error_msg="Status: Progress message"
    )
    
    execute_calls = mock_cur.execute.call_args_list
    assert len(execute_calls) > 0
    
    target_sql = None
    target_params = None
    for call in execute_calls:
        sql = call[0][0]
        if "INSERT INTO scanner_health" in sql:
            target_sql = sql
            target_params = call[0][1]
            break
            
    assert target_sql is not None, "Failed to find INSERT query"
    
    # Assert columns are dynamically added
    assert "last_success" in target_sql
    assert "error_msg" in target_sql
    
    # Assert values are dynamically added
    assert "2026-07-24T12:00:00" in target_params
    assert "Status: Progress message" in target_params
    
def test_upsert_scanner_health_update(mocker):
    mock_conn = mocker.patch("app.database.get_connection")
    mock_cur = mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    
    # Simulate update with last_success and error_msg
    upsert_scanner_health(
        scanner_name="EXISTING_SCANNER", 
        status="OK", 
        last_success="2026-07-24T15:00:00",
        error_msg="Status: Updated progress"
    )
    
    execute_calls = mock_cur.execute.call_args_list
    
    target_sql = None
    target_params = None
    for call in execute_calls:
        sql = call[0][0]
        if "UPDATE" in sql and "SET" in sql:
            target_sql = sql
            target_params = call[0][1]
            break
            
    assert target_sql is not None, "Failed to find UPDATE query"
    
    # The ON CONFLICT DO UPDATE SET should contain our parameters
    assert "last_success = %s" in target_sql
    assert "error_msg = %s" in target_sql
    assert "2026-07-24T15:00:00" in target_params
    assert "Status: Updated progress" in target_params
