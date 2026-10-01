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
from typing import List, Dict, Tuple, Any

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
                cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name = %s", (tbl,))
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

        alerts_subquery = "SELECT id FROM alerts WHERE COALESCE(NULLIF(scanner, ''), 'UNKNOWN') NOT IN %s"

        # 2. alerts table
        cur.execute("SELECT COUNT(*) FROM alerts WHERE COALESCE(NULLIF(scanner, ''), 'UNKNOWN') NOT IN %s", (RETAINED_SCANNERS,))
        stats["alerts"] = cur.fetchone()[0]

        # 3. candidates table
        cur.execute("SELECT COUNT(*) FROM candidates WHERE COALESCE(NULLIF(scanner, ''), 'UNKNOWN') NOT IN %s", (RETAINED_SCANNERS,))
        stats["candidates"] = cur.fetchone()[0]

        # 4. rejected_alerts table
        cur.execute("SELECT COUNT(*) FROM rejected_alerts WHERE COALESCE(NULLIF(scanner, ''), 'UNKNOWN') NOT IN %s", (RETAINED_SCANNERS,))
        stats["rejected_alerts"] = cur.fetchone()[0]

        # 5. Dependent child records of alerts to delete
        try:
            cur.execute(f"SELECT COUNT(*) FROM trade_audit_log WHERE alert_id IN ({alerts_subquery})", (RETAINED_SCANNERS,))
            stats["trade_audit_log"] = cur.fetchone()[0]
        except Exception:
            stats["trade_audit_log"] = 0

        try:
            cur.execute(f"SELECT COUNT(*) FROM alert_outcomes WHERE alert_id IN ({alerts_subquery})", (RETAINED_SCANNERS,))
            stats["alert_outcomes"] = cur.fetchone()[0]
        except Exception:
            stats["alert_outcomes"] = 0

        try:
            cur.execute(f"SELECT COUNT(*) FROM alert_events WHERE alert_id IN ({alerts_subquery})", (RETAINED_SCANNERS,))
            stats["alert_events"] = cur.fetchone()[0]
        except Exception:
            stats["alert_events"] = 0

        try:
            cur.execute(f"SELECT COUNT(*) FROM telegram_queue WHERE alert_id IN ({alerts_subquery})", (RETAINED_SCANNERS,))
            stats["telegram_queue"] = cur.fetchone()[0]
        except Exception:
            stats["telegram_queue"] = 0

        try:
            cur.execute(f"SELECT COUNT(*) FROM global_notifications WHERE alert_id IN ({alerts_subquery})", (RETAINED_SCANNERS,))
            stats["global_notifications"] = cur.fetchone()[0]
        except Exception:
            stats["global_notifications"] = 0

        # 6. scanner_execution_history (column: scanner_name)
        cur.execute("SELECT COUNT(*) FROM scanner_execution_history WHERE COALESCE(NULLIF(scanner_name, ''), 'UNKNOWN') NOT IN %s", (ALL_ALLOWED_ENTITIES,))
        stats["scanner_execution_history"] = cur.fetchone()[0]

        # 7. scanner_health (column: scanner_name)
        cur.execute("SELECT COUNT(*) FROM scanner_health WHERE COALESCE(NULLIF(scanner_name, ''), 'UNKNOWN') NOT IN %s", (ALL_ALLOWED_ENTITIES,))
        stats["scanner_health"] = cur.fetchone()[0]

        # 8. scanner_control (column: scanner_name)
        try:
            cur.execute("SELECT COUNT(*) FROM scanner_control WHERE COALESCE(NULLIF(scanner_name, ''), 'UNKNOWN') NOT IN %s", (RETAINED_SCANNERS,))
            stats["scanner_control"] = cur.fetchone()[0]
        except Exception:
            stats["scanner_control"] = 0

        # 9. scan_failures (column: scanner_name)
        try:
            cur.execute("SELECT COUNT(*) FROM scan_failures WHERE COALESCE(NULLIF(scanner_name, ''), 'UNKNOWN') NOT IN %s", (RETAINED_SCANNERS,))
            stats["scan_failures"] = cur.fetchone()[0]
        except Exception:
            stats["scan_failures"] = 0

        # 10. funnel_telemetry (column: scanner)
        try:
            cur.execute("SELECT COUNT(*) FROM funnel_telemetry WHERE COALESCE(NULLIF(scanner, ''), 'UNKNOWN') NOT IN %s", (RETAINED_SCANNERS,))
            stats["funnel_telemetry"] = cur.fetchone()[0]
        except Exception:
            stats["funnel_telemetry"] = 0

        # 11. scanner_evaluation_log (column: scanner)
        try:
            cur.execute("SELECT COUNT(*) FROM scanner_evaluation_log WHERE COALESCE(NULLIF(scanner, ''), 'UNKNOWN') NOT IN %s", (RETAINED_SCANNERS,))
            stats["scanner_evaluation_log"] = cur.fetchone()[0]
        except Exception:
            stats["scanner_evaluation_log"] = 0

        # 12. near_misses (column: scanner)
        try:
            cur.execute("SELECT COUNT(*) FROM near_misses WHERE COALESCE(NULLIF(scanner, ''), 'UNKNOWN') NOT IN %s", (RETAINED_SCANNERS,))
            stats["near_misses"] = cur.fetchone()[0]
        except Exception:
            stats["near_misses"] = 0

        if not dry_run:
            print("🚀 Executing deletions...")
            # 1. Delete dependent child alert records first
            try:
                cur.execute(f"DELETE FROM trade_audit_log WHERE alert_id IN ({alerts_subquery})", (RETAINED_SCANNERS,))
            except Exception as e:
                print(f"  [WARN] Failed to delete from trade_audit_log: {e}")

            try:
                cur.execute(f"DELETE FROM alert_outcomes WHERE alert_id IN ({alerts_subquery})", (RETAINED_SCANNERS,))
            except Exception as e:
                print(f"  [WARN] Failed to delete from alert_outcomes: {e}")

            try:
                cur.execute(f"DELETE FROM alert_events WHERE alert_id IN ({alerts_subquery})", (RETAINED_SCANNERS,))
            except Exception as e:
                print(f"  [WARN] Failed to delete from alert_events: {e}")

            try:
                cur.execute(f"DELETE FROM telegram_queue WHERE alert_id IN ({alerts_subquery})", (RETAINED_SCANNERS,))
            except Exception as e:
                print(f"  [WARN] Failed to delete from telegram_queue: {e}")

            try:
                cur.execute(f"DELETE FROM global_notifications WHERE alert_id IN ({alerts_subquery})", (RETAINED_SCANNERS,))
            except Exception as e:
                print(f"  [WARN] Failed to delete from global_notifications: {e}")

            # 2. Delete alerts
            cur.execute("DELETE FROM alerts WHERE COALESCE(NULLIF(scanner, ''), 'UNKNOWN') NOT IN %s", (RETAINED_SCANNERS,))

            # 3. Delete candidates
            cur.execute("DELETE FROM candidates WHERE COALESCE(NULLIF(scanner, ''), 'UNKNOWN') NOT IN %s", (RETAINED_SCANNERS,))

            # 4. Delete rejected_alerts
            cur.execute("DELETE FROM rejected_alerts WHERE COALESCE(NULLIF(scanner, ''), 'UNKNOWN') NOT IN %s", (RETAINED_SCANNERS,))

            # 5. Delete scanner_execution_history
            cur.execute("DELETE FROM scanner_execution_history WHERE COALESCE(NULLIF(scanner_name, ''), 'UNKNOWN') NOT IN %s", (ALL_ALLOWED_ENTITIES,))

            # 6. Delete scanner_health
            cur.execute("DELETE FROM scanner_health WHERE COALESCE(NULLIF(scanner_name, ''), 'UNKNOWN') NOT IN %s", (ALL_ALLOWED_ENTITIES,))

            # 7. Delete scanner_control
            try:
                cur.execute("DELETE FROM scanner_control WHERE COALESCE(NULLIF(scanner_name, ''), 'UNKNOWN') NOT IN %s", (RETAINED_SCANNERS,))
            except Exception:
                pass

            # 8. Delete scan_failures
            try:
                cur.execute("DELETE FROM scan_failures WHERE COALESCE(NULLIF(scanner_name, ''), 'UNKNOWN') NOT IN %s", (RETAINED_SCANNERS,))
            except Exception:
                pass

            # 9. Delete funnel_telemetry
            try:
                cur.execute("DELETE FROM funnel_telemetry WHERE COALESCE(NULLIF(scanner, ''), 'UNKNOWN') NOT IN %s", (RETAINED_SCANNERS,))
            except Exception:
                pass

            # 10. Delete scanner_evaluation_log
            try:
                cur.execute("DELETE FROM scanner_evaluation_log WHERE COALESCE(NULLIF(scanner, ''), 'UNKNOWN') NOT IN %s", (RETAINED_SCANNERS,))
            except Exception:
                pass

            # 11. Delete near_misses
            try:
                cur.execute("DELETE FROM near_misses WHERE COALESCE(NULLIF(scanner, ''), 'UNKNOWN') NOT IN %s", (RETAINED_SCANNERS,))
            except Exception:
                pass

            # 12. Clear system_state performance_data cache
            try:
                cur.execute("DELETE FROM system_state WHERE key = 'performance_data'")
            except Exception:
                pass

            conn.commit()
            print("✅ All deletions committed successfully.")

            # Trigger background performance rebuild to refresh UI cache
            try:
                from performance_tracker import trigger_performance_rebuild
                trigger_performance_rebuild(force=True)
            except Exception as pe:
                print(f"  [INFO] trigger_performance_rebuild notice: {pe}")

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
