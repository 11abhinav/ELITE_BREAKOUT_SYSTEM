#!/usr/bin/env python3
"""
Read-Only Live Proof Data Exporter for V5.30 Audit.
STRICTLY READ-ONLY: Never modifies any tables, rows, or database files.
"""

import os
import sys
import sqlite3
import hashlib
import csv
import json
import datetime

DB_PATH = "data/shadow_telemetry.db"

def main():
    if not os.path.exists(DB_PATH):
        print(f"Error: Database file not found at {DB_PATH}")
        sys.exit(1)

    # 1. Compute SHA-256
    sha256_hash = hashlib.sha256()
    with open(DB_PATH, "rb") as f:
        for byte_block in iter(lambda: f.read(65536), b""):
            sha256_hash.update(byte_block)
    db_sha256 = sha256_hash.hexdigest()

    with open("v530_database_sha256.txt", "w") as f:
        f.write(f"Database: {os.path.abspath(DB_PATH)}\n")
        f.write(f"SHA-256: {db_sha256}\n")
        f.write(f"Export Timestamp: {datetime.datetime.now().isoformat()}\n")

    # 2. Open read-only SQLite connection
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # 3. Export Schema Information
    cur.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name")
    tables_schema = cur.fetchall()

    schema_lines = []
    schema_lines.append(f"# SQLite Database Schema Export for {DB_PATH}")
    schema_lines.append(f"# SHA-256: {db_sha256}")
    schema_lines.append(f"# Timestamp: {datetime.datetime.now().isoformat()}\n")

    table_names = []
    table_counts = {}

    for row in tables_schema:
        name = row["name"]
        sql = row["sql"]
        if name == "sqlite_sequence":
            continue
        table_names.append(name)
        schema_lines.append(f"============================================================")
        schema_lines.append(f"TABLE: {name}")
        schema_lines.append(f"============================================================")
        schema_lines.append(sql if sql else "N/A")
        schema_lines.append("\n-- PRAGMA table_info:")
        cur.execute(f"PRAGMA table_info({name})")
        cols = cur.fetchall()
        for col in cols:
            schema_lines.append(f"  cid={col['cid']}, name={col['name']}, type={col['type']}, notnull={col['notnull']}, dflt_value={col['dflt_value']}, pk={col['pk']}")
        schema_lines.append("\n")

        cur.execute(f"SELECT count(*) FROM {name}")
        table_counts[name] = cur.fetchone()[0]

    with open("v530_schema.txt", "w") as f:
        f.write("\n".join(schema_lines))

    # 4. Export Table Counts
    count_lines = [
        f"Database: {os.path.abspath(DB_PATH)}",
        f"SHA-256: {db_sha256}",
        f"Timestamp: {datetime.datetime.now().isoformat()}",
        "------------------------------------------------------------",
        "TABLE NAME                                | ROW COUNT",
        "------------------------------------------------------------"
    ]
    for tbl, cnt in table_counts.items():
        count_lines.append(f"{tbl.ljust(41)} | {str(cnt).rjust(9)}")
    count_lines.append("------------------------------------------------------------")

    with open("v530_table_counts.txt", "w") as f:
        f.write("\n".join(count_lines))

    # 5. Export Primary Disagreement Table (ALL rows, preserving numeric precision)
    cur.execute("SELECT * FROM v530_vs_v529_disagreement_telemetry ORDER BY id ASC")
    disagreements = cur.fetchall()
    
    if disagreements:
        headers = disagreements[0].keys()
        with open("v530_live_proof_disagreements.csv", "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for r in disagreements:
                writer.writerow(dict(r))
    else:
        with open("v530_live_proof_disagreements.csv", "w", newline="") as f:
            f.write("# No records found\n")

    # 6. Export Related Telemetry (Alerts and Triggers joined)
    # Joining v530 alerts with trigger details and v529 counterparts for complete reconstruction
    query_related = """
    SELECT 
        a530.id AS v530_alert_id,
        a530.config_version_id AS v530_config_id,
        a530.symbol,
        a530.exchange_session_date,
        a530.decision_timestamp,
        a530.source_commit,
        a530.nifty_regime,
        a530.sector,
        a530.archetype,
        a530.clv,
        a530.extension_r,
        a530.volume_retention_ratio,
        a530.runway_atr,
        a530.vwap_relationship,
        a530.compression_days,
        a530.base_tightness,
        a530.wick_pct,
        a530.rs_3d_momentum,
        a530.fresh_score_exp,
        a530.structure_score,
        a530.timing_score,
        a530.model_g_score AS v530_model_g_score,
        a530.veto_regime_divergence AS v530_regime_veto,
        a530.is_vetoed AS v530_is_vetoed,
        a530.regime_capacity_limit,
        a530.shadow_rank AS v530_shadow_rank,
        a530.allocated_r AS v530_allocated_r,
        a530.trigger_status AS v530_trigger_status,
        t530.eligibility_45m_timestamp AS v530_45m_timestamp,
        t530.final_trigger_state AS v530_final_trigger_state,
        a530.entry_price AS v530_entry_price,
        a530.stop_loss AS v530_stop_loss,
        a530.target_price AS v530_target_price,
        a530.realized_r AS v530_realized_r,
        a530.exit_reason AS v530_exit_reason,
        a530.outcome_classification AS v530_outcome_classification,
        a529.shadow_rank AS v529_shadow_rank,
        a529.allocated_r AS v529_allocated_r,
        a529.trigger_status AS v529_trigger_status,
        t529.eligibility_30m_timestamp AS v529_30m_timestamp,
        t529.final_trigger_state AS v529_final_trigger_state,
        a529.realized_r AS v529_realized_r,
        a529.exit_reason AS v529_exit_reason
    FROM v530_shadow_alert_telemetry a530
    LEFT JOIN v530_shadow_trigger_telemetry t530 
        ON a530.symbol = t530.symbol AND a530.decision_timestamp = t530.decision_timestamp
    LEFT JOIN v529_shadow_alert_telemetry a529 
        ON a530.symbol = a529.symbol AND a530.decision_timestamp = a529.decision_timestamp
    LEFT JOIN v529_shadow_trigger_telemetry t529
        ON a529.symbol = t529.symbol AND a529.decision_timestamp = t529.decision_timestamp
    ORDER BY a530.id ASC
    """
    cur.execute(query_related)
    related_records = cur.fetchall()

    if related_records:
        rel_headers = related_records[0].keys()
        with open("v530_related_telemetry.csv", "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rel_headers)
            writer.writeheader()
            for r in related_records:
                writer.writerow(dict(r))

    # 7. Compute Summary Statistics directly from raw query
    cur.execute("SELECT count(*) FROM v530_vs_v529_disagreement_telemetry WHERE is_disagreement = 1 AND paired_delta_r IS NOT NULL")
    res_count = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM v530_vs_v529_disagreement_telemetry WHERE is_disagreement = 1 AND paired_delta_r IS NULL")
    unres_count = cur.fetchone()[0]

    cur.execute("SELECT MIN(decision_timestamp), MAX(decision_timestamp) FROM v530_vs_v529_disagreement_telemetry")
    min_ts, max_ts = cur.fetchone()

    con.close()

    # Print summary output exactly as requested
    print("=========================================================================")
    print("           V5.30 READ-ONLY LIVE PROOF DATA EXPORT COMPLETE               ")
    print("=========================================================================")
    print(f"database path:               {os.path.abspath(DB_PATH)}")
    print(f"database SHA-256:            {db_sha256}")
    print(f"table names:                 {', '.join(table_names)}")
    print("row counts:")
    for tbl, cnt in table_counts.items():
        print(f"  - {tbl}: {cnt}")
    print(f"resolved disagreement count: {res_count}")
    print(f"unresolved disagreement count: {unres_count}")
    print(f"earliest event timestamp:    {min_ts}")
    print(f"latest event timestamp:      {max_ts}")
    print("=========================================================================")

if __name__ == "__main__":
    main()
