import sys
import os

# Add the app directory to the path so we can import from app.database
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app')))

from database import get_connection

def update_constraint():
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE scanner_health DROP CONSTRAINT IF EXISTS chk_scanner_status;")
                cur.execute("ALTER TABLE scanner_health ADD CONSTRAINT chk_scanner_status CHECK (status IN ('OK', 'DOWN', 'IDLE', 'RUNNING', 'DEGRADED')) NOT VALID;")
            conn.commit()
            print("Successfully updated chk_scanner_status constraint.")
    except Exception as e:
        print(f"Error updating constraint: {e}")

if __name__ == "__main__":
    update_constraint()
