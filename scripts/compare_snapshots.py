#!/usr/bin/env python3
"""
[VERSION: REGRESSION_COMPARE_v1.0]
Stage 2 Audit — Snapshot Comparison Script
Diffs two regression snapshots and flags any behavioral regressions.

Usage:
    python3 scripts/compare_snapshots.py before_fix_xyz after_fix_xyz
    python3 scripts/compare_snapshots.py artifacts/snapshots/before_*.json artifacts/snapshots/after_*.json
"""

import sys
import os
import json
import glob
import argparse
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOT_DIR = os.path.join(PROJECT_ROOT, "artifacts", "snapshots")


def load_snapshot(label_or_path: str) -> dict:
    """Load snapshot by label prefix or direct path."""
    if os.path.isfile(label_or_path):
        with open(label_or_path) as f:
            return json.load(f)
    # Search by label prefix
    matches = sorted(glob.glob(os.path.join(SNAPSHOT_DIR, f"{label_or_path}*.json")))
    if not matches:
        raise FileNotFoundError(f"No snapshot found for label: {label_or_path}")
    with open(matches[-1]) as f:  # latest match
        return json.load(f)


def compare_snapshots(before: dict, after: dict) -> list:
    """Return a list of (severity, category, description) tuples."""
    findings = []

    # 1. Test suite regression
    bt = before.get("test_results", {})
    at = after.get("test_results", {})
    if not bt.get("skipped") and not at.get("skipped"):
        if at.get("failed", 0) > bt.get("failed", 0):
            delta = at["failed"] - bt["failed"]
            findings.append(("🔴 REGRESSION", "Tests", f"{delta} new test failure(s): {at['failed']} total failed"))
        elif at.get("passed", 0) < bt.get("passed", 0):
            delta = bt["passed"] - at["passed"]
            findings.append(("🟡 WARNING", "Tests", f"{delta} fewer tests passing ({at['passed']} vs {bt['passed']})"))
        else:
            findings.append(("✅ OK", "Tests", f"No regression. {at.get('passed',0)} passing."))

    # 2. File changes
    bh = before.get("file_hashes", {})
    ah = after.get("file_hashes", {})
    changed = [f for f in set(bh) | set(ah) if bh.get(f) != ah.get(f)]
    if changed:
        for f in changed:
            if bh.get(f) == "missing":
                findings.append(("ℹ️  INFO", "Files", f"NEW file: {f}"))
            elif ah.get(f) == "missing":
                findings.append(("🟡 WARNING", "Files", f"DELETED file: {f}"))
            else:
                findings.append(("ℹ️  CHANGED", "Files", f"Modified: {f}"))
    else:
        findings.append(("✅ OK", "Files", "No key files changed."))

    # 3. Config drift
    bc = before.get("config_hash")
    ac = after.get("config_hash")
    if bc != ac:
        findings.append(("🟡 WARNING", "Config", f"Config state changed: {bc} → {ac}"))
    else:
        findings.append(("✅ OK", "Config", "Config unchanged."))

    # 4. Alert count changes
    bd = before.get("db_alerts", {})
    ad = after.get("db_alerts", {})
    if "error" not in bd and "error" not in ad:
        delta_open = ad.get("open_alerts", 0) - bd.get("open_alerts", 0)
        if delta_open < -10:
            findings.append(("🔴 REGRESSION", "Alerts", f"Open alert count dropped by {abs(delta_open)} — possible scanner suppression"))
        elif delta_open > 100:
            findings.append(("🟡 WARNING", "Alerts", f"Open alert count surged by {delta_open} — check for false positives"))
        else:
            findings.append(("✅ OK", "Alerts", f"Open alerts: {bd.get('open_alerts')} → {ad.get('open_alerts')} (Δ{delta_open:+d})"))

        # Top symbols stability
        before_syms = {s["symbol"] for s in bd.get("top_10_symbols", [])}
        after_syms = {s["symbol"] for s in ad.get("top_10_symbols", [])}
        if before_syms and after_syms:
            dropped = before_syms - after_syms
            appeared = after_syms - before_syms
            if dropped:
                findings.append(("🟡 WARNING", "Alerts", f"Top symbols dropped: {', '.join(dropped)}"))
            if appeared:
                findings.append(("ℹ️  INFO", "Alerts", f"New top symbols: {', '.join(appeared)}"))

    # 5. Line count changes (detect accidental mass deletion)
    bl = before.get("module_line_counts", {})
    al = after.get("module_line_counts", {})
    for f in set(bl) | set(al):
        bc_lines = bl.get(f, 0)
        ac_lines = al.get(f, 0)
        if bc_lines > 0 and ac_lines > 0:
            change_pct = abs(ac_lines - bc_lines) / bc_lines * 100
            if change_pct > 20:
                findings.append(("🔴 REGRESSION", "Files", f"{f}: Line count changed {bc_lines} → {ac_lines} ({change_pct:.0f}%) — possible mass edit"))

    return findings


def main():
    parser = argparse.ArgumentParser(description="Compare two regression snapshots")
    parser.add_argument("before", help="Before snapshot label or path")
    parser.add_argument("after", help="After snapshot label or path")
    parser.add_argument("--output", help="Write report to file")
    args = parser.parse_args()

    try:
        before = load_snapshot(args.before)
        after = load_snapshot(args.after)
    except FileNotFoundError as e:
        print(f"❌ {e}")
        return 1

    print(f"\n📊 REGRESSION COMPARISON REPORT")
    print(f"   Before : {before['label']} @ {before['timestamp'][:19]} (git:{before['git']['commit']})")
    print(f"   After  : {after['label']} @ {after['timestamp'][:19]} (git:{after['git']['commit']})")
    print(f"   Version: REGRESSION_COMPARE_v1.0\n")

    findings = compare_snapshots(before, after)

    regressions = [f for f in findings if "REGRESSION" in f[0]]
    warnings = [f for f in findings if "WARNING" in f[0]]

    for sev, cat, desc in findings:
        print(f"  {sev:<18} [{cat:<10}] {desc}")

    print(f"\nSummary: {len(regressions)} regression(s), {len(warnings)} warning(s)")

    report_lines = [
        f"# Regression Comparison Report",
        f"Before: {before['label']} ({before['git']['commit']})",
        f"After:  {after['label']} ({after['git']['commit']})",
        f"",
    ]
    for sev, cat, desc in findings:
        report_lines.append(f"- {sev} [{cat}] {desc}")

    if args.output:
        with open(args.output, "w") as f:
            f.write("\n".join(report_lines))
        print(f"\n  Report written to: {args.output}")

    return 1 if regressions else 0


if __name__ == "__main__":
    sys.exit(main())
