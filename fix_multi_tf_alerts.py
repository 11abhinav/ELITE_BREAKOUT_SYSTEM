import sys
import os

# Add app to path so we can import database
sys.path.append(os.path.join(os.path.dirname(__file__), "app"))

from database import get_connection

def fix_multi_tf_references():
    print("Connecting to database...")
    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. Update alerts table
            print("Updating alerts table...")
            cur.execute("""
                UPDATE alerts
                SET scanner = 'MULTI_TF'
                WHERE scanner IN ('multi_tf_scanner', 'Multi-TF Ladder', 'Multi-TF', 'multi_tf');
            """)
            print(f"Rows updated in alerts: {cur.rowcount}")

            # 2. Update rejected_alerts table
            print("Updating rejected_alerts table...")
            cur.execute("""
                UPDATE rejected_alerts
                SET scanner = 'MULTI_TF'
                WHERE scanner IN ('multi_tf_scanner', 'Multi-TF Ladder', 'Multi-TF', 'multi_tf');
            """)
            print(f"Rows updated in rejected_alerts: {cur.rowcount}")

            # 3. Update candidates table (if scanner column exists)
            print("Updating candidates table...")
            try:
                cur.execute("""
                    UPDATE candidates
                    SET scanner = 'MULTI_TF'
                    WHERE scanner IN ('multi_tf_scanner', 'Multi-TF Ladder', 'Multi-TF', 'multi_tf');
                """)
                print(f"Rows updated in candidates: {cur.rowcount}")
            except Exception as e:
                print(f"Could not update candidates (column might not exist): {e}")
                conn.rollback()

            # 4. Update scanner_health table
            print("Updating scanner_health table...")
            cur.execute("""
                UPDATE scanner_health
                SET scanner_name = 'MULTI_TF'
                WHERE scanner_name IN ('multi_tf_scanner', 'Multi-TF Ladder', 'Multi-TF', 'multi_tf');
            """)
            print(f"Rows updated in scanner_health: {cur.rowcount}")

            # 5. Update fetch_errors table
            print("Updating fetch_errors table...")
            cur.execute("""
                UPDATE fetch_errors
                SET scanner = 'MULTI_TF'
                WHERE scanner IN ('multi_tf_scanner', 'Multi-TF Ladder', 'Multi-TF', 'multi_tf');
            """)
            print(f"Rows updated in fetch_errors: {cur.rowcount}")

            # 6. Delete old scanner_health rows if they exist alongside MULTI_TF
            # The previous update might fail if there's a unique constraint on scanner_name,
            # or it might create duplicates. The primary key in scanner_health is usually scanner_name.
            # If so, the update would have raised a UniqueViolation.
            
            # 7. Update signal column in alerts to replace 'Multi-TF' with 'MULTI_TF'
            print("Updating signals column in alerts...")
            cur.execute("""
                UPDATE alerts
                SET signals = REPLACE(signals, 'Multi-TF', 'MULTI_TF')
                WHERE signals LIKE '%Multi-TF%';
            """)
            print(f"Rows updated in alerts (signals): {cur.rowcount}")

        conn.commit()
    print("Done!")

if __name__ == "__main__":
    fix_multi_tf_references()
