import json
from datetime import datetime
IST = __import__('zoneinfo').ZoneInfo("Asia/Kolkata")
with open("data/fundamentals_cache.json") as f:
    data = json.load(f)

for k, v in list(data.items())[:10]:
    if v:
        entry_date = datetime.strptime(v["date"], "%Y-%m-%d").date()
        days_old = (datetime.now(IST).date() - entry_date).days
        print(f"{k}: date={v['date']}, days_old={days_old}")
    else:
        print(f"{k}: null")
