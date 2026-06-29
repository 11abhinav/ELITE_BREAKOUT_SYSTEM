import os
import psycopg2
from pathlib import Path

def check_results():
    ROOT_DIR = Path(__file__).resolve().parent.parent
    env_path = ROOT_DIR / ".env"
    
    # Load from our found .env path if available
    db_url = "postgresql://postgres:PxrzlXmmgnjjavEwJZRiTuAdtoeIdzAT@thomas.proxy.rlwy.net:41764/railway"
    
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        
        # Check stockupdates.prices table
        cur.execute("SELECT COUNT(*) FROM stockupdates.prices;")
        prices_count = cur.fetchone()[0]
        print(f"Total entries in stockupdates.prices: {prices_count}")
        
        # Print first 10 entries from stockupdates.prices
        cur.execute("SELECT symbol, latest_price, fundamental_score, quality_score, value_score FROM stockupdates.prices LIMIT 10;")
        print("\nFirst 10 entries in stockupdates.prices:")
        for row in cur.fetchall():
            print(f"  Symbol: {row[0]}, Price: {row[1]}, Fundamental Score: {row[2]}, Quality Score: {row[3]}, Value Score: {row[4]}")
            
        # Check stockupdates.watchlist table
        cur.execute("SELECT COUNT(*) FROM stockupdates.watchlist;")
        watchlist_count = cur.fetchone()[0]
        print(f"\nTotal entries in stockupdates.watchlist: {watchlist_count}")
        
        # Check telegram queue
        cur.execute("SELECT id, symbol, SUBSTRING(message_text, 1, 60) FROM telegram_queue ORDER BY created_at DESC LIMIT 5;")
        print("\nLast 5 items in telegram_queue:")
        for row in cur.fetchall():
            print(f"  ID: {row[0]}, Symbol: {row[1]}, Message: {row[2]}...")
            
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_results()
