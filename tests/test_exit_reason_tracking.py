import pytest
import json
from database import init_db, save_alert_if_new, update_alert_outcome, get_all_alerts, get_todays_alerts, get_all_scanners_today_trades

def test_exit_signal_persistence_and_query():
    """Verify that update_alert_outcome persists exit_signal and database queries expose it."""
    init_db()
    
    symbol = "REASON_TEST_STOCK"
    alert_date = "2026-08-23"
    alert_time = "2026-08-23 10:00:00"
    
    # 1. Insert alert
    alert_id = save_alert_if_new(
        symbol=symbol,
        breakout_type="EOD|Breakout|50DMA|EOD_V1",
        alert_time=alert_time,
        scanner="EOD",
        category="STRONG_UPTREND",
        entry_price=100.0,
        stop_loss=90.0,
        target_price=120.0
    )
    
    assert alert_id is not None, "Failed to insert test alert"
    
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
    todays_alerts = get_todays_alerts(alert_date)
    matched_today = [a for a in todays_alerts if a["id"] == alert_id]
    assert len(matched_today) == 1, "Failed to find test alert in get_todays_alerts"
    assert matched_today[0].get("exit_signal") == exit_reason, f"Expected '{exit_reason}', got '{matched_today[0].get('exit_signal')}'"
    
    # 5. Verify get_all_scanners_today_trades includes exit_signal
    scanner_trades = get_all_scanners_today_trades(alert_date)
    eod_trades = scanner_trades.get("EOD", [])
    matched_scanner = [t for t in eod_trades if t["symbol"] == symbol]
    assert len(matched_scanner) >= 1, "Failed to find test trade in get_all_scanners_today_trades"
    assert matched_scanner[0].get("exit_signal") == exit_reason, f"Expected '{exit_reason}', got '{matched_scanner[0].get('exit_signal')}'"
