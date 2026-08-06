import os
import sys
import time

sys.path.append(os.path.join(os.getcwd(), 'app'))
from database import get_connection

try:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_fetch_errors_acknowledged ON fetch_errors(is_acknowledged);")
            print("Added index on fetch_errors(is_acknowledged)")
            
            cur.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_system_logs_acknowledged ON system_logs(is_acknowledged);")
            print("Added index on system_logs(is_acknowledged)")
            
            cur.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_system_logs_msg_mod ON system_logs(message, module);")
            print("Added index on system_logs(message, module)")
            
        conn.commit()
except Exception as e:
    print(f"Error: {e}")
