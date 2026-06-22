import psycopg2
from psycopg2.extras import RealDictCursor
import os
from dotenv import load_dotenv

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    print("No DATABASE_URL found.")
    exit(1)

print(f"Connecting to DB...")
try:
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = True
    with conn.cursor() as cur:
        print("Dropping old constraint...")
        cur.execute("ALTER TABLE alerts DROP CONSTRAINT IF EXISTS chk_alerts_status;")
        print("Adding new constraint...")
        cur.execute("ALTER TABLE alerts ADD CONSTRAINT chk_alerts_status CHECK (status IN ('OPEN', 'WIN', 'LOSS', 'CLOSED', 'REJECTED')) NOT VALID;")
        print("Done!")
except Exception as e:
    print(f"Error: {e}")
