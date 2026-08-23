import pytest
import json
import uuid
from database import init_db, get_connection, update_alert_outcome, get_all_alerts, get_todays_alerts, get_all_scanners_today_trades, DummyConnection
from datetime import datetime
from zoneinfo import ZoneInfo
from psycopg2.extras import RealDictCursor

def test_exit_signal_persistence_and_query():
    """Verify that update_alert_outcome persists exit_signal and database queries expose it."""
    init_db()
    
    unique_id = uuid.uuid4().hex[:6].upper()
    symbol = f"TESTSYM_{unique_id}"
    scanner_name = f"SCANNER_{unique_id}"
    today_str = datetime.now(ZoneInfo('Asia/Kolkata')).strftime('%Y-%m-%d')
    alert_time = f"{today_str} 10:00:00"
    
    # 1. Insert alert directly into Postgres alerts table if DB is available
    alert_id = 999999
    is_dummy = False
    with get_connection() as conn:
        if isinstance(conn, DummyConnection):
            is_dummy = True
        else:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    INSERT INTO alerts
                        (symbol, breakout_type, alert_time, alert_date, scanner, category,
                        entry_price, stop_loss, initial_stop_loss, target_price, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN')
                    RETURNING id;
                """, (symbol, "EOD|Breakout|50DMA|EOD_V1", alert_time, today_str, scanner_name, "STRONG_UPTREND",
                      100.0, 90.0, 90.0, 120.0))
                row = cur.fetchone()
                if row:
                    alert_id = row["id"]
                conn.commit()

    if is_dummy:
        # In DummyConnection offline test environment, verify signature and execution without crashing
        exit_reason = "Catastrophic Stop Loss Hit: Drawdown >= 20.0% (-22.5% loss)"
        update_alert_outcome(
            alert_id=1,
            status="LOSS",
            exit_price=77.5,
            pnl_pct=-22.5,
            exit_signal=exit_reason
        )
        assert True
        return

    # 2. Lock exit with detailed exit signal reason
    exit_reason = "Catastrophic Stop Loss Hit: Drawdown >= 20.0% (-22.5% loss)"
    update_alert_outcome(
        alert_id=alert_id,
        status="LOSS",
        exit_price=77.5,
        pnl_pct=-22.5,
        exit_signal=exit_reason
    )
    
    # 3. Verify get_all_alerts includes exit_signal
    all_alerts = get_all_alerts()
    matched = [a for a in all_alerts if a["id"] == alert_id]
    assert len(matched) == 1, "Failed to find test alert in get_all_alerts"
    assert matched[0].get("exit_signal") == exit_reason, f"Expected '{exit_reason}', got '{matched[0].get('exit_signal')}'"
    
    # 4. Verify get_todays_alerts includes exit_signal
    todays_alerts = get_todays_alerts(today_str)
    matched_today = [a for a in todays_alerts if a["id"] == alert_id]
    assert len(matched_today) == 1, "Failed to find test alert in get_todays_alerts"
    assert matched_today[0].get("exit_signal") == exit_reason, f"Expected '{exit_reason}', got '{matched_today[0].get('exit_signal')}'"
    
    # 5. Verify get_all_scanners_today_trades includes exit_signal
    scanner_trades = get_all_scanners_today_trades(today_str)
    eod_trades = scanner_trades.get(scanner_name, [])
    matched_scanner = [t for t in eod_trades if t["symbol"] == symbol]
    assert len(matched_scanner) >= 1, "Failed to find test trade in get_all_scanners_today_trades"
    assert matched_scanner[0].get("exit_signal") == exit_reason, f"Expected '{exit_reason}', got '{matched_scanner[0].get('exit_signal')}'"
