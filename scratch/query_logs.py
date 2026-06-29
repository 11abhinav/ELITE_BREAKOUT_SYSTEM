import os
import sys
sys.path.append(os.path.join(os.getcwd(), 'app'))
from database import get_connection
with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT created_at, module, message FROM system_logs ORDER BY created_at DESC LIMIT 10")
        for row in cur.fetchall():
            print(row)
