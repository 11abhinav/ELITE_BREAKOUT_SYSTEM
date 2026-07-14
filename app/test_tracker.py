import sys
import json
import pandas as pd
from datetime import datetime

import performance_tracker

logs = []

def mock_update_partial_exit(alert_id, new_status, new_sl, shares_sold, remaining_shares, pnl_rs, exit_event):
    logs.append(f"PARTIAL EXIT | Status: {new_status} | New SL: {new_sl} | Sold: {shares_sold} | Rem: {remaining_shares} | PNL: {pnl_rs} | Event: {exit_event}")

def mock_update_alert_outcome(alert_id, status, exit_price, pnl_pct, closed_at, pnl_rs, exit_signal=None):
    logs.append(f"OUTCOME | Status: {status} | Exit: {exit_price} | Pnl%: {pnl_pct} | PnlRs: {pnl_rs}")

performance_tracker.update_partial_exit = mock_update_partial_exit
performance_tracker.update_alert_outcome = mock_update_alert_outcome

print("=== TEST 1: Legacy Alert (target_price only) ===")
logs.clear()
legacy_alert = {
    "id": 1,
    "symbol": "TEST1",
    "entry_price": 100,
    "stop_loss": 90,
    "target_price": 120,
    "status": "OPEN",
    "shares_bought": 100,
    "remaining_shares": 100,
    "capital_allocated": 10000,
    "exit_history": "[]"
}
# Hit the target exactly at 120
performance_tracker.process_trade_history(legacy_alert, None, cur_p=125)
for l in logs: print(l)

print("\n=== TEST 2: V2 Alert (T1, T2, T3) - T1 Hit ===")
logs.clear()
v2_alert = {
    "id": 2,
    "symbol": "TEST2",
    "entry_price": 100,
    "stop_loss": 90,
    "target_1": 110,
    "target_2": 120,
    "target_3": 130,
    "status": "OPEN",
    "shares_bought": 100,
    "remaining_shares": 100,
    "capital_allocated": 10000,
    "exit_history": "[]"
}
# Hit T1
performance_tracker.process_trade_history(v2_alert, None, cur_p=112)
for l in logs: print(l)

print("\n=== TEST 3: V2 Alert (T1, T2, T3) - T2 Hit from PARTIAL_WIN_1 ===")
logs.clear()
v2_alert_t1 = {
    "id": 2,
    "symbol": "TEST2",
    "entry_price": 100,
    "stop_loss": 100,
    "target_1": 110,
    "target_2": 120,
    "target_3": 130,
    "status": "PARTIAL_WIN_1",
    "shares_bought": 100,
    "remaining_shares": 75,
    "capital_allocated": 10000,
    "exit_history": '[{"type": "T1_HIT", "price": 112, "shares": 25, "pnl": 300, "time": "2026-07-14 10:00:00"}]'
}
# Hit T2
performance_tracker.process_trade_history(v2_alert_t1, None, cur_p=122)
for l in logs: print(l)

print("\n=== TEST 4: V2 Alert (T1, T2, T3) - SL Hit from PARTIAL_WIN_1 ===")
logs.clear()
# Hit trailing SL
performance_tracker.process_trade_history(v2_alert_t1, None, cur_p=95)
for l in logs: print(l)
