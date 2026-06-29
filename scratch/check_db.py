import sys, os
sys.path.append('app')
from dotenv import load_dotenv
load_dotenv()
import database
import pandas as pd

with database.get_connection() as conn:
    df = pd.read_sql("SELECT symbol, scanner, entry_date, alert_time FROM alerts WHERE scanner ILIKE '%wealth%' ORDER BY alert_time DESC LIMIT 10", conn)
    print("ALERTS table:")
    print(df)
    
    df2 = pd.read_sql("SELECT symbol, alert_date, alert_time FROM wealth_buy_alert ORDER BY alert_time DESC LIMIT 10", conn)
    print("\nWEALTH_BUY_ALERT table:")
    print(df2)
