import sys, os
sys.path.append('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app')
from database import get_connection
import pandas as pd

with get_connection() as conn:
    df = pd.read_sql("SELECT scanner_name, date, total_count, processed_count, outcome, error_msg, provider_stats FROM scanner_health ORDER BY date DESC LIMIT 20", conn)
    print(df.to_string())
