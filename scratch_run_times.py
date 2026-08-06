import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "app"))
from database import get_connection

try:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    scanner_name, 
                    COUNT(*) as runs,
                    AVG(duration_seconds) as avg_duration_sec,
                    MIN(duration_seconds) as min_duration_sec,
                    MAX(duration_seconds) as max_duration_sec
                FROM scanner_execution_history
                WHERE start_time >= NOW() - INTERVAL '7 days'
                AND duration_seconds IS NOT NULL
                GROUP BY scanner_name
                ORDER BY avg_duration_sec DESC;
            """)
            results = cur.fetchall()
            
            print(f"{'Scanner':<20} | {'Runs (7d)':<10} | {'Avg Run Time':<15} | {'Min':<10} | {'Max':<10}")
            print("-" * 75)
            for row in results:
                name = row[0]
                runs = row[1]
                avg_s = float(row[2])
                min_s = float(row[3])
                max_s = float(row[4])
                print(f"{name:<20} | {runs:<10} | {avg_s:>6.1f} sec       | {min_s:>6.1f} s   | {max_s:>6.1f} s")
                
except Exception as e:
    print(f"Error connecting to db: {e}")
