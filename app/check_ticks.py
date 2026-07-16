import json
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from database import get_connection
from price_cache import fetch_watchlist_data

IST = ZoneInfo("Asia/Kolkata")

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM alerts WHERE id = 686")
        columns = [desc[0] for desc in cur.description]
        row = cur.fetchone()
        t = dict(zip(columns, row))

print(f"Alert 686 time: {t['alert_time']}")

df_req = pd.DataFrame({"Stock": ["LLOYDSME"]})
data = fetch_watchlist_data(df_req, period="5d", interval="5m")
hist = data.get("LLOYDSME")

if hist is not None and not hist.empty:
    date_col = next((c for c in ["Datetime", "Date", "index"] if c in hist.columns), None)
    if date_col is not None:
        hist[date_col] = pd.to_datetime(hist[date_col])
        hist = hist.set_index(date_col)

    idx = hist.index
    if idx.tz is None:
        idx = idx.tz_localize("Asia/Kolkata")
    else:
        idx = idx.tz_convert("Asia/Kolkata")
    hist.index = idx
    
    # filter
    alert_dt = datetime.fromisoformat(t['alert_time'].replace("Z", "+00:00"))
    if alert_dt.tzinfo is None:
        alert_dt = alert_dt.replace(tzinfo=IST)
    else:
        alert_dt = alert_dt.astimezone(IST)
        
    hist = hist[hist.index >= alert_dt].copy()
    print("Ticks:")
    for ts, row in hist.iterrows():
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
        print(ts_str, float(row["Open"]), float(row["Low"]), float(row["High"]))
else:
    print("No history fetched")
