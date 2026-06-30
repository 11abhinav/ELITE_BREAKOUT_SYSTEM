import json
import traceback
from app.database import get_connection

try:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT scanner_name, status, last_success, error_msg FROM scanner_health WHERE scanner_name IN ('EOD', 'REVERSAL', 'DAILY_BUILDER');")
            rows = cur.fetchall()
            for r in rows:
                print(r)
except Exception as e:
    traceback.print_exc()
