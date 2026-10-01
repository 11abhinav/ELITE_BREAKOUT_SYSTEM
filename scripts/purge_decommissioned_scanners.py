"""
scripts/purge_decommissioned_scanners.py
========================================
Purges all data belonging to decommissioned scanners across all PostgreSQL tables.
Retains strictly the THREE ACTIVE SCANNERS:
  1. DAILY_BUILDER
  2. TECHNICAL
  3. QUALITY_COMPOUNDER_VALUE_V2_FINAL
Along with core system workers/daemons (SystemScheduler, AI Worker, Pledge Worker, etc.).

Supports:
  --dry-run : Report rows that will be deleted without modifying the database.
  --execute : Apply deletions inside an ACID transaction.
"""

import os
import sys
import argparse
import psycopg2
from typing import List, Dict, Tuple

# Active scanners that MUST be preserved
RETAINED_SCANNERS = (
    "DAILY_BUILDER",
    "TECHNICAL",
    "QUALITY_COMPOUNDER_VALUE_V2_FINAL",
)

# System workers & external providers to retain in scanner_health and execution_history
RETAINED_SYSTEM_ENTITIES = (
    "SystemScheduler",
    "AI Worker",
    "Pledge Worker",
    "WATCHLIST_BUILDER",
    "PERFORMANCE_TRACKER",
    "MASTER_SYMBOLS",
    "FYERS_PROVIDER",
    "External:upstox",
    "External:fyers",
    "External:performance_tracker",
    "External:yfinance",
)

ALL_ALLOWED_ENTITIES = RETAINED_SCANNERS + RETAINED_SYSTEM_ENTITIES

# Dedicated obsolete scanner tables
OBSOLETE_TABLES = [
    "accumulation_alerts",
    "accumulation_candidates",
    "accumulation_health",
    "accumulation_runs",
    "accumulation_telemetry",
    "mtf_v2_watchlist",
    "wealth_score_history",
    "wealth_buy_alert",
    "breakout_watchlist",
]


def purge_decommissioned_data(conn, dry_run: bool = True) -> Dict[str, Any]:
    stats = {}
    with conn.cursor() as cur:
        # 1. Obsolete tables row counts / drop
        stats["obsolete_tables"] = {}
        for tbl in OBSOLETE_TABLES:
            try:
                cur.execute(f"SELECT COUNT(*) FROM information_schema.tables WHERE table_name = %s", (tbl,))
                exists = cur.fetchone()[0] > 0
                if exists:
                    cur.execute(f"SELECT COUNT(*) FROM {tbl}")
                    cnt = cur.fetchone()[0]
                    stats["obsolete_tables"][tbl] = cnt
                    if not dry_run:
                        cur.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")
                        print(f"  [DROP TABLE] Dropped obsolete table: {tbl} ({cnt} rows)")
                else:
                    stats["obsolete_tables"][tbl] = "NOT_FOUND"
            except Exception as e:
                stats["obsolete_tables"][tbl] = f"ERROR: {e}"

        # 2. alerts table
        cur.execute("SELECT COUNT(*) FROM alerts WHERE scanner NOT IN %s AND scanner_name NOT IN %s", (RETAINED_SCANNERS, RETAINED_SCANNERS))
        alerts_to_delete = cur.fetchone()[0]
        stats["alerts"] = alerts_to_delete

        # 3. candidates table
        cur.execute("SELECT COUNT(*) FROM candidates WHERE scanner NOT IN %s", (RETAINED_SCANNERS,))
        candidates_to_delete = cur.fetchone()[0]
        stats["candidates"] = candidates_to_delete

        # 4. rejected_alerts table
        cur.execute("SELECT COUNT(*) FROM rejected_alerts WHERE scanner NOT IN %s", (RETAINED_SCANNERS,))
        rejected_to_delete = cur.fetchone()[0]
        stats["rejected_alerts"] = rejected_to_delete

        # 5. alert_outcomes and alert_events (child records of alerts to delete)
        cur.execute("""
            SELECT COUNT(*) FROM alert_outcomes
            WHERE alert_id IN (
                SELECT id FROM alerts WHERE scanner NOT IN %s AND scanner_name NOT IN %s
            )
        """, (RETAINED_SCANNERS, RETAINED_SCANNERS))
        outcomes_to_delete = cur.fetchone()[0]
        stats["alert_outcomes"] = outcomes_to_delete

        cur.execute("""
            SELECT COUNT(*) FROM alert_events
            WHERE alert_id IN (
                SELECT id FROM alerts WHERE scanner NOT IN %s AND scanner_name NOT IN %s
            )
        """, (RETAINED_SCANNERS, RETAINED_SCANNERS))
        events_to_delete = cur.fetchone()[0]
        stats["alert_events"] = events_to_delete

        # 6. scanner_execution_history
        cur.execute("SELECT COUNT(*) FROM scanner_execution_history WHERE scanner_name NOT IN %s", (ALL_ALLOWED_ENTITIES,))
        exec_hist_to_delete = cur.fetchone()[0]
        stats["scanner_execution_history"] = exec_hist_to_delete

        # 7. scanner_health
        cur.execute("SELECT COUNT(*) FROM scanner_health WHERE scanner_name NOT IN %s", (ALL_ALLOWED_ENTITIES,))
        health_to_delete = cur.fetchone()[0]
        stats["scanner_health"] = health_to_delete

        # 8. scanner_control
        cur.execute("SELECT COUNT(*) FROM scanner_control WHERE scanner_name NOT IN %s", (RETAINED_SCANNERS,))
        control_to_delete = cur.fetchone()[0]
        stats["scanner_control"] = control_to_delete

        # 9. scan_failures
        cur.execute("SELECT COUNT(*) FROM scan_failures WHERE scanner_name NOT IN %s", (RETAINED_SCANNERS,))
        failures_to_delete = cur.fetchone()[0]
        stats["scan_failures"] = failures_to_delete

        # 10. funnel_telemetry
        cur.execute("SELECT COUNT(*) FROM funnel_telemetry WHERE scanner_name NOT IN %s", (RETAINED_SCANNERS,))
        telemetry_to_delete = cur.fetchone()[0]
        stats["funnel_telemetry"] = telemetry_to_delete

        # 11. scanner_evaluation_log
        cur.execute("SELECT COUNT(*) FROM scanner_evaluation_log WHERE scanner_name NOT IN %s", (RETAINED_SCANNERS,))
        eval_log_to_delete = cur.fetchone()[0]
        stats["scanner_evaluation_log"] = eval_log_to_delete

        # 12. near_misses
        cur.execute("SELECT COUNT(*) FROM near_misses WHERE scanner_name NOT IN %s", (RETAINED_SCANNERS,))
        near_misses_to_delete = cur.fetchone()[0]
        stats["near_misses"] = near_misses_to_delete

        if not dry_run:
            print("🚀 Executing deletions...")
            # Delete child alert records first
            cur.execute("""
                DELETE FROM alert_outcomes
                WHERE alert_id IN (
                    SELECT id FROM alerts WHERE scanner NOT IN %s AND scanner_name NOT IN %s
                )
            """, (RETAINED_SCANNERS, RETAINED_SCANNERS))

            cur.execute("""
                DELETE FROM alert_events
                WHERE alert_id IN (
                    SELECT id FROM alerts WHERE scanner NOT IN %s AND scanner_name NOT IN %s
                )
            """, (RETAINED_SCANNERS, RETAINED_SCANNERS))

            # Delete alerts
            cur.execute("DELETE FROM alerts WHERE scanner NOT IN %s AND scanner_name NOT IN %s", (RETAINED_SCANNERS, RETAINED_SCANNERS))

            # Delete candidates
            cur.execute("DELETE FROM candidates WHERE scanner NOT IN %s", (RETAINED_SCANNERS,))

            # Delete rejected_alerts
            cur.execute("DELETE FROM rejected_alerts WHERE scanner NOT IN %s", (RETAINED_SCANNERS,))

            # Delete scanner_execution_history
            cur.execute("DELETE FROM scanner_execution_history WHERE scanner_name NOT IN %s", (ALL_ALLOWED_ENTITIES,))

            # Delete scanner_health
            cur.execute("DELETE FROM scanner_health WHERE scanner_name NOT IN %s", (ALL_ALLOWED_ENTITIES,))

            # Delete scanner_control
            cur.execute("DELETE FROM scanner_control WHERE scanner_name NOT IN %s", (RETAINED_SCANNERS,))

            # Delete scan_failures
            cur.execute("DELETE FROM scan_failures WHERE scanner_name NOT IN %s", (RETAINED_SCANNERS,))

            # Delete funnel_telemetry
            cur.execute("DELETE FROM funnel_telemetry WHERE scanner_name NOT IN %s", (RETAINED_SCANNERS,))

            # Delete scanner_evaluation_log
            cur.execute("DELETE FROM scanner_evaluation_log WHERE scanner_name NOT IN %s", (RETAINED_SCANNERS,))

            # Delete near_misses
            cur.execute("DELETE FROM near_misses WHERE scanner_name NOT IN %s", (RETAINED_SCANNERS,))

            conn.commit()
            print("✅ All deletions committed successfully.")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Purge decommissioned scanner data from PostgreSQL.")
    parser.add_argument("--execute", action="store_true", help="Execute actual deletion (default is dry-run)")
    args = parser.parse_args()

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("❌ ERROR: DATABASE_URL environment variable is not set.")
        sys.exit(1)

    dry_run = not args.execute
    mode_str = "DRY-RUN (No changes applied)" if dry_run else "EXECUTE (Permanently deleting data)"
    print(f"\n=======================================================")
    print(f"🧹 PURGE DECOMMISSIONED SCANNERS — {mode_str}")
    print(f"=======================================================")
    print(f"Retained Scanners: {RETAINED_SCANNERS}")
    print(f"Retained System Workers: {RETAINED_SYSTEM_ENTITIES}\n")

    conn = psycopg2.connect(db_url, options="-c timezone=Asia/Kolkata")
    try:
        stats = purge_decommissioned_data(conn, dry_run=dry_run)
        print("\nSummary of records:")
        for k, v in stats.items():
            print(f"  • {k}: {v}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
