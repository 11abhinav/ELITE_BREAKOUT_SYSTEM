from app.database import get_connection
import pandas as pd
with get_connection() as conn:
    df = pd.read_sql("SELECT * FROM scanner_health WHERE scanner_name IN ('EOD', 'REVERSAL', 'DAILY_BUILDER');", conn)
    print(df.to_string())
