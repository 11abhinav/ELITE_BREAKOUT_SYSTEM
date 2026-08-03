"""
[VERSION: PERF_PHASE0_v1.0]
Performance Regression Gate — CI Tool

Compares per-stage timings from a current run against a committed baseline.
Fails (exit 1) if any stage regresses more than the configured threshold (default: 5%).

Usage:
    python tools/compare_perf.py \
        --baseline artifacts/profiling/perf_Phase0_Baseline_<date>.json \
        --current  perf_current.json \
        --threshold 5

Exit codes:
    0 = All stages within threshold (gate passed)
    1 = One or more regressions exceed threshold (gate failed)
    2 = Missing file or schema mismatch
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime

EXPECTED_SCHEMA_VERSION = 1


def load_report(path: Path) -> dict:
    if not path.exists():
        print(f"ERROR: Report file not found: {path}", file=sys.stderr)
        sys.exit(2)
    try:
        with open(path) as f:
            report = json.load(f)
    except Exception as e:
        print(f"ERROR: Failed to parse {path}: {e}", file=sys.stderr)
        sys.exit(2)

    version = report.get("schema_version")
    if version != EXPECTED_SCHEMA_VERSION:
        print(f"ERROR: Schema version mismatch. Expected {EXPECTED_SCHEMA_VERSION}, got {version}.", file=sys.stderr)
        sys.exit(2)
    return report


def compare_perf(baseline: dict, current: dict, threshold_pct: float) -> dict:
    """
    Compare per-stage total_ms. Returns a comparison report.
    Regression = current stage is slower than baseline by > threshold_pct%.
    """
    baseline_stages = baseline.get("stages", {})
    current_stages  = current.get("stages", {})

    result = {
        "schema_version": EXPECTED_SCHEMA_VERSION,
        "threshold_pct": threshold_pct,
        "baseline_phase": baseline.get("phase"),
        "current_phase": current.get("phase"),
        "baseline_total_ms": baseline.get("total_scan_wall_clock_ms"),
        "current_total_ms": current.get("total_scan_wall_clock_ms"),
        "stages": {},
        "regressions": [],
        "improvements": [],
        "new_stages": [],
        "removed_stages": [],
    }

    all_stages = set(baseline_stages) | set(current_stages)
    for stage in sorted(all_stages):
        b = baseline_stages.get(stage)
        c = current_stages.get(stage)

        if b is None:
            result["new_stages"].append(stage)
            continue
        if c is None:
            result["removed_stages"].append(stage)
            continue

        b_ms = b.get("total_ms") or 0
        c_ms = c.get("total_ms") or 0
        delta_ms = c_ms - b_ms
        delta_pct = ((c_ms - b_ms) / b_ms * 100) if b_ms > 0 else 0

        stage_result = {
            "baseline_total_ms": round(b_ms, 1),
            "current_total_ms":  round(c_ms, 1),
            "delta_ms":  round(delta_ms, 1),
            "delta_pct": round(delta_pct, 1),
            "regression": delta_pct > threshold_pct,
            "improvement": delta_pct < -threshold_pct,
        }
        result["stages"][stage] = stage_result

        if stage_result["regression"]:
            result["regressions"].append({"stage": stage, "delta_pct": round(delta_pct, 1)})
        elif stage_result["improvement"]:
            result["improvements"].append({"stage": stage, "delta_pct": round(delta_pct, 1)})

    # Overall wall-clock check
    b_total = baseline.get("total_scan_wall_clock_ms") or 0
    c_total = current.get("total_scan_wall_clock_ms") or 0
    if b_total > 0:
        total_delta_pct = (c_total - b_total) / b_total * 100
        result["total_delta_pct"] = round(total_delta_pct, 1)
        if total_delta_pct > threshold_pct:
            result["regressions"].append({"stage": "TOTAL_SCAN", "delta_pct": round(total_delta_pct, 1)})

    return result


def main():
    parser = argparse.ArgumentParser(description="Performance regression gate (CI)")
    parser.add_argument("--baseline",  required=True, help="Baseline perf JSON path")
    parser.add_argument("--current",   required=True, help="Current perf JSON path")
    parser.add_argument("--threshold", type=float, default=5.0,
                        help="Regression threshold %% (default: 5.0)")
    parser.add_argument("--output",    default=None, help="Optional JSON gate report output path")
    args = parser.parse_args()

    baseline = load_report(Path(args.baseline))
    current  = load_report(Path(args.current))

    print(f"\n📊 Performance Gate — threshold: {args.threshold}%")
    print(f"   Baseline : {args.baseline}  [{baseline.get('phase')} / {baseline.get('run_type')}]")
    print(f"   Current  : {args.current}  [{current.get('phase')} / {current.get('run_type')}]\n")

    report = compare_perf(baseline, current, args.threshold)

    # Print stage table
    print(f"  {'Stage':<45} {'Baseline':>12} {'Current':>12} {'Δms':>10} {'Δ%':>8}  Status")
    print(f"  {'-'*95}")
    for stage, s in report["stages"].items():
        status = "❌ REGRESSED" if s["regression"] else ("✅ IMPROVED" if s["improvement"] else "  OK")
        print(f"  {stage:<45} {s['baseline_total_ms']:>10.1f}ms {s['current_total_ms']:>10.1f}ms "
              f"{s['delta_ms']:>+9.1f} {s['delta_pct']:>+7.1f}%  {status}")

    if report.get("new_stages"):
        print(f"\n  ➕ New stages: {report['new_stages']}")
    if report.get("removed_stages"):
        print(f"  ➖ Removed stages: {report['removed_stages']}")

    if report["improvements"]:
        print(f"\n  ✅ Improvements ({len(report['improvements'])}):")
        for imp in report["improvements"]:
            print(f"     {imp['stage']}: {imp['delta_pct']:+.1f}%")

    if args.output:
        report["run_timestamp"] = datetime.utcnow().isoformat()
        Path(args.output).write_text(json.dumps(report, indent=2))
        print(f"\n  📄 Gate report written: {args.output}")

    if report["regressions"]:
        print(f"\n❌ PERFORMANCE GATE FAILED — {len(report['regressions'])} regression(s) exceed {args.threshold}% threshold:")
        for reg in report["regressions"]:
            print(f"   {reg['stage']}: {reg['delta_pct']:+.1f}%")
        print()
        sys.exit(1)
    else:
        print(f"\n✅ PERFORMANCE GATE PASSED — All stages within {args.threshold}% threshold.\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
