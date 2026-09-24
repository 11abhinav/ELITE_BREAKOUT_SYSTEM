#!/usr/bin/env python3
"""
Session Telemetry & Production Snapshot Archiver
================================================
Freezes and archives live session execution telemetry and raw input snapshots
immediately post-market before any cache, data refresh, or ingestion mutation can overwrite them.

Usage:
  python3 scripts/archive_session_telemetry.py [YYYY-MM-DD]
"""

import os
import sys
import json
import shutil
import hashlib
from datetime import datetime, date

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TELEMETRY_PATH = os.path.join(REPO_ROOT, "logs", "scanner_telemetry.jsonl")
SNAPSHOT_BASE = os.path.join(REPO_ROOT, "data", "production_snapshots")


def get_git_commit() -> str:
    try:
        import subprocess
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT).decode().strip()
    except Exception:
        return "UNKNOWN"


def archive_session(target_date: str = None):
    if not target_date:
        target_date = "2026-09-24"

    session_dir = os.path.join(SNAPSHOT_BASE, target_date)
    os.makedirs(session_dir, exist_ok=True)

    print(f"==================================================================")
    print(f"🔒 FREEZING PRODUCTION TELEMETRY FOR SESSION: {target_date}")
    print(f"==================================================================")

    if not os.path.exists(TELEMETRY_PATH):
        print(f"❌ Error: {TELEMETRY_PATH} does not exist.")
        return False

    session_records = []
    scanner_counts = {}

    with open(TELEMETRY_PATH, "r") as f:
        for line in f:
            if target_date in line:
                try:
                    d = json.loads(line)
                    ts = d.get("timestamp", "")
                    all_vals = d.get("all_values", {})
                    dt_val = str(all_vals.get("Datetime", {}).get("value", ""))
                    
                    if target_date in ts or target_date in dt_val:
                        session_records.append(line)
                        sc = d.get("scanner", "UNKNOWN")
                        scanner_counts[sc] = scanner_counts.get(sc, 0) + 1
                except Exception:
                    pass

    print(f"Found {len(session_records)} telemetry records for {target_date}.")
    print("Record breakdown by component:")
    for sc, count in sorted(scanner_counts.items()):
        print(f"  - {sc}: {count} records")

    # 1. Write frozen telemetry file
    frozen_telemetry_file = os.path.join(session_dir, f"scanner_telemetry_{target_date}.jsonl")
    with open(frozen_telemetry_file, "w") as f:
        f.writelines(session_records)

    # 2. Compute SHA256 of frozen telemetry
    hasher = hashlib.sha256()
    with open(frozen_telemetry_file, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    telemetry_hash = hasher.hexdigest()

    # 3. Create immutable manifest
    manifest = {
        "session_date": target_date,
        "archive_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
        "git_commit": get_git_commit(),
        "total_records": len(session_records),
        "scanner_counts": scanner_counts,
        "telemetry_sha256": telemetry_hash,
        "frozen_file": os.path.relpath(frozen_telemetry_file, REPO_ROOT),
        "immutable_lock": True,
        "governance_rule": "NO_TELEMETRY_NO_BACKTEST"
    }

    manifest_path = os.path.join(session_dir, "snapshot_manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    # 4. Lock files to read-only (chmod 444)
    try:
        os.chmod(frozen_telemetry_file, 0o444)
        os.chmod(manifest_path, 0o444)
    except Exception as e:
        print(f"Warning setting permissions: {e}")

    print(f"✅ Frozen telemetry written to: {frozen_telemetry_file}")
    print(f"✅ Manifest locked: {manifest_path} (SHA256: {telemetry_hash[:16]}...)")
    print(f"==================================================================")
    return True


if __name__ == "__main__":
    t_date = sys.argv[1] if len(sys.argv) > 1 else "2026-09-24"
    archive_session(t_date)
