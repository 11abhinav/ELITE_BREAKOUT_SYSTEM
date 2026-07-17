import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))
import intraday
from intraday import strip_forming_candle, IST

# Simulate a DataFrame with a 09:15 15m candle
dt = datetime(2026, 7, 16, 9, 15, tzinfo=IST)
df = pd.DataFrame({"Close": [100]}, index=[dt])

print("Now is 09:20")
now = datetime(2026, 7, 16, 9, 20, tzinfo=IST)
stripped = strip_forming_candle(df, 15, now)
print(f"Stripped length: {len(stripped)}")

print("Now is 09:30")
now = datetime(2026, 7, 16, 9, 30, tzinfo=IST)
stripped = strip_forming_candle(df, 15, now)
print(f"Stripped length: {len(stripped)}")

print("Now is 09:31")
now = datetime(2026, 7, 16, 9, 31, tzinfo=IST)
stripped = strip_forming_candle(df, 15, now)
print(f"Stripped length: {len(stripped)}")
