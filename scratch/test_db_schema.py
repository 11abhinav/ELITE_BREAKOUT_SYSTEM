import os
import psycopg2
from pathlib import Path

def test_schema():
    ROOT_DIR = Path(__file__).resolve().parent.parent
    env_path = ROOT_DIR / ".env"
    if env_path.exists():
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ[key.strip()] = val.strip().strip("'\"")
                    
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set!")
        return
        
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        
        # Check if schema exists
        cur.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name = 'stockupdates';")
        schema_exists = cur.fetchone()
        print(f"Schema 'stockupdates' exists: {schema_exists is not None}")
        
        # Check if watchlist table exists
        cur.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'stockupdates' AND table_name = 'watchlist';
        """)
        table_exists = cur.fetchone()
        print(f"Table 'stockupdates.watchlist' exists: {table_exists is not None}")
        
        # Print column details of stockupdates.watchlist if it exists
        if table_exists:
            cur.execute("""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_schema = 'stockupdates' AND table_name = 'watchlist';
            """)
            print("Columns:")
            for col in cur.fetchall():
                print(f"  {col[0]}: {col[1]}")
                
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_schema()
