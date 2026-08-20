#!/usr/bin/env python3
"""
[VERSION: REGRESSION_SNAPSHOT_v1.0]
Stage 2 Audit — Regression Snapshot Capture Script
Captures a baseline of scanner/system behavior BEFORE any code changes.
Run this before and after every Stage 2 fix to validate no behavioral regression.

Usage:
    python3 scripts/capture_regression_snapshot.py --label before_fix_xyz
    python3 scripts/capture_regression_snapshot.py --label after_fix_xyz
    python3 scripts/compare_snapshots.py before_fix_xyz after_fix_xyz

Output:
    artifacts/snapshots/<label>_<timestamp>.json
"""

import sys
import os
import json
import time
import hashlib
import argparse
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

# Allow running from project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "app"))

IST = ZoneInfo("Asia/Kolkata")
SNAPSHOT_DIR = os.path.join(PROJECT_ROOT, "artifacts", "snapshots")
os.makedirs(SNAPSHOT_DIR, exist_ok=True)


def get_git_info() -> dict:
    """Capture current git commit and branch."""
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT, text=True
        ).strip()
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=PROJECT_ROOT, text=True
        ).strip()
        return {"commit": commit, "branch": branch}
    except Exception as e:
        return {"commit": "unknown", "branch": "unknown", "error": str(e)}


def get_test_results() -> dict:
    """Run pytest and capture pass/fail counts."""
    if not os.path.exists(os.path.join(PROJECT_ROOT, "tests")):
        return {"status": "NO_TESTS_DIRECTORY", "passed": 0, "failed": 0, "total": 0}
    start = time.perf_counter()
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no", "--no-header"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=600
        )
        elapsed = time.perf_counter() - start
        output = result.stdout + result.stderr

        # Parse counts
        import re
        m = re.search(r"(\d+) passed", output)
        passed = int(m.group(1)) if m else 0
        m = re.search(r"(\d+) failed", output)
        failed = int(m.group(1)) if m else 0
        m = re.search(r"(\d+) skipped", output)
        skipped = int(m.group(1)) if m else 0

        return {
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "total": passed + failed + skipped,
            "elapsed_seconds": round(elapsed, 2),
            "returncode": result.returncode
        }
    except subprocess.TimeoutExpired:
        return {"error": "pytest timeout after 600s"}
    except Exception as e:
        return {"error": str(e)}


def get_config_hash() -> str:
    """Hash key config values to detect config drift between snapshots."""
    try:
        import config
        key_values = {
            k: str(getattr(config, k, None))
            for k in [
                "PROVIDER_ROUTING_POLICY", "QUALITY_SCORE_WEIGHTS",
                "PRICE_CACHE_TTL_SECONDS", "BATCH_DOWNLOAD_SIZE",
                "QUALITY_VALIDATOR_VERSION"
            ]
        }
        raw = json.dumps(key_values, sort_keys=True)
        return hashlib.md5(raw.encode()).hexdigest()
    except Exception as e:
        return f"error:{e}"


def get_db_alert_snapshot() -> dict:
    """Capture current alert counts and top scoring symbols from DB."""
    try:
        import database
        conn = database.get_connection()
        if not conn:
            return {"error": "no_db_connection"}

        with conn.cursor() as cur:
            # Total alert count
            cur.execute("SELECT COUNT(*) FROM alerts WHERE status='OPEN'")
            open_count = cur.fetchone()[0]

            # Alert count per scanner
            cur.execute("""
                SELECT scanner, COUNT(*) as cnt
                FROM alerts
                WHERE status='OPEN'
                GROUP BY scanner
                ORDER BY cnt DESC
                LIMIT 10
            """)
            per_scanner = {row[0]: row[1] for row in cur.fetchall()}

            # Top 10 symbols by score
            cur.execute("""
                SELECT symbol, technical_score, scanner
                FROM alerts
                WHERE status='OPEN'
                ORDER BY technical_score DESC NULLS LAST
                LIMIT 10
            """)
            top_symbols = [
                {"symbol": r[0], "score": r[1], "scanner": r[2]}
                for r in cur.fetchall()
            ]

            # Recent alert count (last 24h)
            cur.execute("""
                SELECT COUNT(*) FROM alerts
                WHERE created_at >= NOW() - INTERVAL '24 hours'
            """)
            last_24h = cur.fetchone()[0]

        conn.close()
        return {
            "open_alerts": open_count,
            "last_24h_alerts": last_24h,
            "per_scanner": per_scanner,
            "top_10_symbols": top_symbols
        }
    except Exception as e:
        return {"error": str(e)}


def get_file_hashes() -> dict:
    """Hash key production files to detect unexpected changes."""
    key_files = [
        "app/eod_scanner.py",
        "app/reversal_scanner.py",
        "app/multi_tf_scanner.py",
        "app/data_providers/fyers_fetcher.py",
        "app/market_data/providers/upstox_provider.py",
        "app/price_cache.py",
        "app/config.py",
        "app/technical_indicators.py",
        "app/scoring_engine.py",
    ]
    hashes = {}
    for rel_path in key_files:
        full = os.path.join(PROJECT_ROOT, rel_path)
        try:
            with open(full, "rb") as f:
                hashes[rel_path] = hashlib.md5(f.read()).hexdigest()
        except FileNotFoundError:
            hashes[rel_path] = "missing"
    return hashes


def get_module_line_counts() -> dict:
    """Count lines in key modules — quick proxy for code drift."""
    files = [
        "app/eod_scanner.py", "app/reversal_scanner.py",
        "app/multi_tf_scanner.py", "app/data_providers/fyers_fetcher.py",
        "app/market_data/providers/upstox_provider.py",
        "app/price_cache.py", "app/config.py",
    ]
    counts = {}
    for rel in files:
        full = os.path.join(PROJECT_ROOT, rel)
        try:
            with open(full) as f:
                counts[rel] = sum(1 for _ in f)
        except FileNotFoundError:
            counts[rel] = -1
    return counts


def main():
    parser = argparse.ArgumentParser(description="Capture regression snapshot")
    parser.add_argument("--label", default="snapshot", help="Snapshot label (e.g. before_session_fix)")
    parser.add_argument("--skip-tests", action="store_true", help="Skip running pytest (faster)")
    args = parser.parse_args()

    ts = datetime.now(IST).strftime("%Y%m%d_%H%M%S")
    label = args.label.replace(" ", "_")
    filename = f"{label}_{ts}.json"
    filepath = os.path.join(SNAPSHOT_DIR, filename)

    print(f"\n📸 Capturing regression snapshot: {label}")
    print(f"   Output: {filepath}\n")

    snapshot = {
        "label": label,
        "timestamp": datetime.now(IST).isoformat(),
        "version_tag": "REGRESSION_SNAPSHOT_v1.0",
        "git": get_git_info(),
    }

    print("  [1/5] Hashing key files...")
    snapshot["file_hashes"] = get_file_hashes()

    print("  [2/5] Counting module lines...")
    snapshot["module_line_counts"] = get_module_line_counts()

    print("  [3/5] Hashing config state...")
    snapshot["config_hash"] = get_config_hash()

    print("  [4/5] Reading DB alert snapshot...")
    snapshot["db_alerts"] = get_db_alert_snapshot()

    if not args.skip_tests:
        print("  [5/5] Running test suite (this may take ~5 min)...")
        snapshot["test_results"] = get_test_results()
    else:
        snapshot["test_results"] = {"skipped": True}

    with open(filepath, "w") as f:
        json.dump(snapshot, f, indent=2, default=str)

    print(f"\n✅ Snapshot saved: {filepath}")
    if "test_results" in snapshot and not snapshot["test_results"].get("skipped"):
        tr = snapshot["test_results"]
        print(f"   Tests: {tr.get('passed',0)} passed / {tr.get('failed',0)} failed / {tr.get('skipped',0)} skipped")
    print(f"   Git: {snapshot['git'].get('commit')} on {snapshot['git'].get('branch')}")
    print(f"   Open alerts in DB: {snapshot['db_alerts'].get('open_alerts','N/A')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
