#!/usr/bin/env python3
"""
DELETE BAD V2 ALERTS — SAFETY SCRIPT
=====================================
Deletes the ~99 QUALITY_COMPOUNDER_VALUE_V2_FINAL alerts generated on 2026-09-29
that were produced using synthetic fallback metric values (Sales CAGR=10.0%,
PAT CAGR=10.5%, CFO/PAT=1.0, D/E=0.2).

Run with:
    DRY_RUN=1  python3 scripts/delete_bad_v2_alerts.py   <- preview only (default)
    DRY_RUN=0  python3 scripts/delete_bad_v2_alerts.py   <- actually deletes

Requires DATABASE_URL env var to be set.
"""

import os
import sys

# -- resolve paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR    = os.path.join(os.path.dirname(SCRIPT_DIR), "app")
ROOT_DIR   = os.path.dirname(SCRIPT_DIR)
for p in (APP_DIR, ROOT_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import psycopg2
from psycopg2.extras import RealDictCursor

# -- config
DRY_RUN        = os.getenv("DRY_RUN", "1") != "0"   # default: safe preview
DATABASE_URL   = os.getenv("DATABASE_URL")
TARGET_SCANNER = "QUALITY_COMPOUNDER_VALUE_V2_FINAL"
# The bad run happened on 2026-09-29 IST.
TARGET_DATE    = "2026-09-29"

if not DATABASE_URL:
    print("ERROR: DATABASE_URL env var is not set. Cannot connect to the database.")
    sys.exit(1)

# -- connect
conn = psycopg2.connect(DATABASE_URL, options="-c timezone=Asia/Kolkata")
conn.autocommit = False

try:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:

        # STEP 1: preview
        cur.execute("""
            SELECT id, symbol, alert_date, alert_time, scanner, status,
                   entry_price, scanner_run_id
            FROM   alerts
            WHERE  scanner   = %s
              AND  alert_date = %s
            ORDER  BY id
        """, (TARGET_SCANNER, TARGET_DATE))
        rows = cur.fetchall()

        print(f"\n{'='*70}")
        print(f"  PREVIEW -- alerts to be deleted")
        print(f"  Scanner : {TARGET_SCANNER}")
        print(f"  Date    : {TARGET_DATE}")
        print(f"  Count   : {len(rows)}")
        print(f"{'='*70}")
        for r in rows:
            print(f"  id={r['id']:>6}  {r['symbol']:<16}  status={r['status']:<10}  "
                  f"entry={r['entry_price']}  run={r['scanner_run_id']}")
        print(f"{'='*70}\n")

        if not rows:
            print("Nothing to delete -- no matching rows found.")
            sys.exit(0)

        if DRY_RUN:
            print("DRY_RUN=1 --> No rows deleted. Re-run with DRY_RUN=0 to delete.")
            sys.exit(0)

        # STEP 2: require explicit confirmation
        answer = input(f"Type YES to permanently delete {len(rows)} alerts: ").strip()
        if answer != "YES":
            print("Aborted -- nothing deleted.")
            sys.exit(0)

        # STEP 3: delete
        cur.execute("""
            DELETE FROM alerts
            WHERE  scanner   = %s
              AND  alert_date = %s
        """, (TARGET_SCANNER, TARGET_DATE))
        deleted = cur.rowcount
        conn.commit()
        print(f"\n[OK] Deleted {deleted} rows from 'alerts' table.")
        print(   "     scanner_run_id records and candidates table are unaffected.")
        print(   "     Run the scanner again to produce clean alerts.")

except Exception as e:
    conn.rollback()
    print(f"\n[ERROR] Rolled back. {e}")
    raise
finally:
    conn.close()
