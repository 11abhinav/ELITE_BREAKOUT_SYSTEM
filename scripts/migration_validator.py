#!/usr/bin/env python3

"""
ELITE BREAKOUT SYSTEM - MIGRATION VALIDATOR
Compares the row counts of all core tables between the OLD and NEW databases
to ensure zero data loss during migration.
"""

import os
import sys
import psycopg2
from psycopg2 import OperationalError

TABLES_TO_VERIFY = [
    # Config
    "symbol_mappings",
    "system_state",
    "bayesian_model_updates",
    "users",
    "push_subscriptions",
    "global_notifications",
    "telegram_queue",
    
    # Transactional
    "alerts",
    "candidates",
    "system_logs",
    "wealth_score_history",
    "wealth_buy_alert",
    "trade_audit_log",
    "validation_history",
    "capital_history",
    "manual_portfolio"
]

def get_row_count(conn, table_name):
    try:
        with conn.cursor() as cursor:
            # Check if table exists
            cursor.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s);", (table_name,))
            exists = cursor.fetchone()[0]
            if not exists:
                return -1 # Table does not exist
            
            cursor.execute(f"SELECT COUNT(*) FROM {table_name};")
            return cursor.fetchone()[0]
    except Exception as e:
        print(f"Error reading {table_name}: {e}")
        return -2

def main():
    old_url = os.environ.get("OLD_DATABASE_URL")
    new_url = os.environ.get("NEW_DATABASE_URL")

    if not old_url or not new_url:
        print("ERROR: OLD_DATABASE_URL and NEW_DATABASE_URL environment variables must be set.")
        print("Example usage:")
        print("OLD_DATABASE_URL='...' NEW_DATABASE_URL='...' python3 migration_validator.py")
        sys.exit(1)

    print("Connecting to databases...")
    try:
        old_conn = psycopg2.connect(old_url)
        new_conn = psycopg2.connect(new_url)
    except OperationalError as e:
        print(f"Failed to connect to database: {e}")
        sys.exit(1)

    print("\nStarting Row Count Verification...\n")
    print(f"{'TABLE NAME':<30} | {'OLD DB COUNT':<15} | {'NEW DB COUNT':<15} | {'STATUS'}")
    print("-" * 80)

    all_match = True

    for table in TABLES_TO_VERIFY:
        old_count = get_row_count(old_conn, table)
        new_count = get_row_count(new_conn, table)
        
        old_display = str(old_count) if old_count >= 0 else ("MISSING" if old_count == -1 else "ERROR")
        new_display = str(new_count) if new_count >= 0 else ("MISSING" if new_count == -1 else "ERROR")

        if old_count == -1 and new_count == -1:
            status = "N/A (Ignored)"
        elif old_count == new_count:
            status = "✅ MATCH"
        else:
            status = "❌ MISMATCH"
            all_match = False

        print(f"{table:<30} | {old_display:<15} | {new_display:<15} | {status}")

    print("-" * 80)
    
    if all_match:
        print("\n✅ SUCCESS: All tables matched perfectly. Migration validated successfully.")
    else:
        print("\n❌ FAILURE: Row count mismatches detected! Do NOT switch production traffic.")
        sys.exit(1)

if __name__ == "__main__":
    main()
