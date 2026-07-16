import os
import sys
import pandas as pd
from zoneinfo import ZoneInfo
from datetime import datetime
import json

# Add app to path
sys.path.insert(0, "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app")

from data_provider import DataFetcher
from performance_tracker import process_trade_history, _fetch_post_alert_bars

IST = ZoneInfo("Asia/Kolkata")

t = {
    "id": 686,
    "symbol": "LLOYDSME",
    "status": "OPEN",
    "entry_price": 1837.9,
    "stop_loss": 1828.54,
    "initial_stop_loss": 1828.54,
    "target_1": 1855.94,
    "target_2": 1861.42,
    "target_3": 1874.1,
    "shares_bought": 100,
    "remaining_shares": 100,
    "exit_history": "[]",
    "created_at": datetime.strptime("2026-07-15 12:26:00", "%Y-%m-%d %H:%M:%S")
}

def mock_update_partial_exit(alert_id, new_status, new_sl, shares_sold, remaining_shares, pnl_rs, exit_event):
    eh = t.get("exit_history", "[]")
    if not eh: eh = "[]"
    import json
    lst = json.loads(eh)
    lst.append(exit_event)
    t["exit_history"] = json.dumps(lst)
    t["stop_loss"] = new_sl
    t["status"] = new_status
    t["remaining_shares"] = remaining_shares

def mock_update_alert_outcome(alert_id, status, exit_price, pnl_pct, pnl_rs, closed_at, exit_signal):
    pass

import performance_tracker
performance_tracker.update_partial_exit = mock_update_partial_exit
performance_tracker.update_alert_outcome = mock_update_alert_outcome

# Fetch history
hist = _fetch_post_alert_bars(t["symbol"], t["created_at"])
print("HIST HEAD:", hist.head() if hist is not None else None)
print("HIST TAIL:", hist.tail() if hist is not None else None)

# Run logic
# Force cur_p to trigger SL so we can see the timestamp!
process_trade_history(t, hist, cur_p=1800)
print("FINAL EXIT HISTORY:", t.get("exit_history"))
