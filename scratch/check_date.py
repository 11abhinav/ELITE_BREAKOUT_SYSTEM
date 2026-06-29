import sys, os
sys.path.append('app')
from dotenv import load_dotenv
load_dotenv()
import database
import pandas as pd

with database.get_connection() as conn:
    df = pd.read_sql("SELECT symbol, entry_date, alert_time FROM alerts ORDER BY alert_time DESC LIMIT 10", conn)
    print(df)
