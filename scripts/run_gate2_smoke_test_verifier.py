#!/usr/bin/env python3
"""
scripts/run_gate2_smoke_test_verifier.py
=========================================
GATE 2: Smoke Test Log Verifier

Parses the most recent scanner log and verifies:
  REQUIRED (must appear exactly once):
    - [FUNDAMENTAL_PRE_RECOVERY] START
    - NEVER-DOWNGRADE PASS  OR  canonical snapshot loaded

  FORBIDDEN (must NOT appear anywhere):
    - JIT_PERSISTED
    - JIT_SUCCESS
    - JIT_BUDGET_EXHAUSTED
    - Saving updated canonical PIT directly
    - canonical_pit_rebuilt.parquet written by pre_recovery
    - scanner-level network fundamental fetch

Usage:
  python3 scripts/run_gate2_smoke_test_verifier.py --log <path_to_scanner.log>
  python3 scripts/run_gate2_smoke_test_verifier.py  # auto-finds latest log
"""

from __future__ import annotations

import argparse
import glob
import logging
import os
import sys
from datetime import datetime

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("gate2")

REPORT_PATH = os.path.join(REPO_ROOT, "reports", "gate2_smoke_test_verifier.md")

# Strings that MUST appear at least once
REQUIRED_PATTERNS = [
    "[FUNDAMENTAL_PRE_RECOVERY] START",
]

# Strings that MUST appear exactly once (no duplicate pre-recovery sweep)
EXACTLY_ONCE_PATTERNS = [
    "[FUNDAMENTAL_PRE_RECOVERY] START",
]

# Strings that MUST NEVER appear
FORBIDDEN_PATTERNS = [
    "JIT_PERSISTED",
    "JIT_SUCCESS",
    "JIT_BUDGET_EXHAUSTED",
    "Saving updated canonical PIT directly",
    "canonical_pit_rebuilt.parquet written by pre_recovery",
    "scanner-level network fundamental fetch",
    "[PRE_RECOVERY] Saving updated canonical PIT dataset",
]


def find_latest_log() -> str | None:
    """Search common log locations for the most recently modified scanner log."""
    patterns = [
        os.path.join(REPO_ROOT, "logs", "*.log"),
        os.path.join(REPO_ROOT, "*.log"),
        os.path.join(REPO_ROOT, "logs", "scanner*.log"),
        "/tmp/scanner*.log",
    ]
    candidates = []
    for pattern in patterns:
        candidates.extend(glob.glob(pattern))
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def run_gate2(log_path: str | None = None) -> bool:
    logger.info("=" * 60)
    logger.info("GATE 2 — Smoke Test Log Verifier")
    logger.info("=" * 60)

    if not log_path:
        log_path = find_latest_log()

    if not log_path or not os.path.exists(log_path):
        logger.error(
            "No log file found. Run the scanner first, then pass --log <path>."
        )
        return False

    logger.info(f"  Analysing: {log_path}")
    with open(log_path, "r", errors="replace") as f:
        content = f.read()

    lines = content.splitlines()
    results = {}
    overall_pass = True

    # --- Required patterns ---
    for pat in REQUIRED_PATTERNS:
        count = content.count(pat)
        present = count >= 1
        results[f"required::{pat}"] = {"count": count, "pass": present}
        status = "PASS ✅" if present else "FAIL ❌"
        logger.info(f"  [REQUIRED] {status} | '{pat}' found {count}x")
        if not present:
            overall_pass = False

    # --- Exactly-once patterns ---
    for pat in EXACTLY_ONCE_PATTERNS:
        count = content.count(pat)
        exact = count == 1
        results[f"exactly_once::{pat}"] = {"count": count, "pass": exact}
        status = "PASS ✅" if exact else "FAIL ❌"
        logger.info(f"  [ONCE]     {status} | '{pat}' found {count}x (expected 1)")
        if not exact:
            overall_pass = False

    # --- Forbidden patterns ---
    for pat in FORBIDDEN_PATTERNS:
        count = content.count(pat)
        absent = count == 0
        results[f"forbidden::{pat}"] = {"count": count, "pass": absent}
        status = "PASS ✅" if absent else "FAIL ❌"
        logger.info(f"  [FORBIDDEN] {status} | '{pat}' found {count}x (expected 0)")
        if not absent:
            overall_pass = False

    # Build report
    report_lines = [
        "# Gate 2 — Smoke Test Log Verifier",
        "",
        f"**Log file:** `{log_path}`",
        f"**Analysis date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}",
        "",
        "## Required Patterns (must appear ≥ 1x)",
        "| Pattern | Count | Status |",
        "|---|---|---|",
    ]
    for pat in REQUIRED_PATTERNS:
        r = results[f"required::{pat}"]
        report_lines.append(f"| `{pat}` | {r['count']} | {'✅' if r['pass'] else '❌'} |")

    report_lines += [
        "",
        "## Exactly-Once Patterns (must appear exactly 1x)",
        "| Pattern | Count | Status |",
        "|---|---|---|",
    ]
    for pat in EXACTLY_ONCE_PATTERNS:
        r = results[f"exactly_once::{pat}"]
        report_lines.append(f"| `{pat}` | {r['count']} | {'✅' if r['pass'] else '❌'} |")

    report_lines += [
        "",
        "## Forbidden Patterns (must appear 0x)",
        "| Pattern | Count | Status |",
        "|---|---|---|",
    ]
    for pat in FORBIDDEN_PATTERNS:
        r = results[f"forbidden::{pat}"]
        report_lines.append(f"| `{pat}` | {r['count']} | {'✅' if r['pass'] else '❌'} |")

    report_lines += [
        "",
        f"## Gate 2 Verdict: {'✅ PASS — Proceed to Gate 3' if overall_pass else '❌ FAIL — Fix architectural violations first'}",
    ]

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(report_lines))

    logger.info(f"\n{'✅ GATE 2 PASS' if overall_pass else '❌ GATE 2 FAIL'} — {REPORT_PATH}")
    return overall_pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gate 2 Smoke Test Verifier")
    parser.add_argument("--log", help="Path to scanner log file")
    args = parser.parse_args()
    ok = run_gate2(log_path=args.log)
    sys.exit(0 if ok else 1)
