import json
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
import sys
sys.path.insert(0, "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app")

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
    "target_3": 1876.32,
    "target_price": 1891.22,
    "remaining_shares": 89,
    "shares_bought": 89,
    "created_at": datetime(2026, 7, 15, 12, 26, tzinfo=IST),
    "exit_history": None
}

import yfinance as yf
ticker = yf.Ticker("LLOYDSME.NS")
hist = ticker.history(interval="5m", period="5d")
hist.index = hist.index.tz_convert(IST)
hist = hist[hist.index >= t["created_at"]]

cur_p = 1840.0

def test_process(t, hist, cur_p):
    t1 = t.get("target_1") or t.get("target_price")
    t2 = t.get("target_2") or (t1 * 1.05 if t1 else None)
    t3 = t.get("target_3") or (t1 * 1.10 if t1 else None)
    
    shares_bought = t.get("shares_bought", 0)
    
    eh = t.get("exit_history")
    existing_hist = eh if isinstance(eh, list) else json.loads(eh or "[]")
    db_events = {e.get("type") for e in existing_hist}
    
    initial_sl = t.get("initial_stop_loss")
    if not initial_sl or initial_sl == 0:
        t["stop_loss"] = t.get("stop_loss")
    else:
        t["stop_loss"] = initial_sl
    
    t["status"] = "OPEN"
    t["remaining_shares"] = shares_bought
    hist_list = []
    t["exit_history"] = "[]"
    
    ticks = []
    if hist is not None and not hist.empty:
        for ts, row in hist.iterrows():
            ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
            ticks.append((ts_str, float(row["Open"]), float(row["Low"]), float(row["High"])))
            
    if cur_p:
        now_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        ticks.append((now_str, cur_p, cur_p, cur_p))
        
    for ts_str, open_p, low, high in ticks:
        if t["status"] in ("WIN", "LOSS", "CLOSED", "REJECTED"):
            break
            
        sl = t["stop_loss"]
        status = t["status"]
        rem_shares = t["remaining_shares"]
        
        # 1. SL
        if low <= sl:
            exit_p = open_p if open_p < sl else sl
            pnl_rs_event = rem_shares * (exit_p - t["entry_price"])
            event = {"type": "SL_HIT", "price": exit_p, "shares": rem_shares, "pnl": round(pnl_rs_event, 2), "time": ts_str}
            hist_list.append(event)
            t["status"] = "WIN" if "PARTIAL" in status else "LOSS"
            t["remaining_shares"] = 0
            break
            
        # 2. T1
        if t1 and high >= t1 and "PARTIAL_WIN_1" not in status and "PARTIAL_WIN_2" not in status:
            shares_to_sell = rem_shares // 3 if t3 else rem_shares // 2
            exit_p = open_p if open_p > t1 else t1
            pnl_rs_event = shares_to_sell * (exit_p - t["entry_price"])
            event = {"type": "T1_HIT", "price": exit_p, "shares": shares_to_sell, "pnl": round(pnl_rs_event, 2), "time": ts_str}
            t["status"] = "PARTIAL_WIN_1"
            t["remaining_shares"] -= shares_to_sell
            t["stop_loss"] = t["entry_price"]
            hist_list.append(event)
            continue
            
        # 3. T2
        if t2 and high >= t2 and "PARTIAL_WIN_2" not in status:
            shares_to_sell = rem_shares // 2 if t3 else rem_shares
            exit_p = open_p if open_p > t2 else t2
            pnl_rs_event = shares_to_sell * (exit_p - t["entry_price"])
            event = {"type": "T2_HIT", "price": exit_p, "shares": shares_to_sell, "pnl": round(pnl_rs_event, 2), "time": ts_str}
            t["status"] = "PARTIAL_WIN_2"
            t["remaining_shares"] -= shares_to_sell
            t["stop_loss"] = t1
            hist_list.append(event)
            continue
            
    t["exit_history"] = json.dumps(hist_list, indent=2)
    return t

res = test_process(t, hist, cur_p)
print(res["exit_history"])
