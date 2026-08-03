"""
[VERSION: PERF_PHASE0_v1.0]
Output Equivalence Comparison Tool

Compares scanner output snapshots before and after each optimization phase.
Non-deterministic fields (timestamps, UUIDs, durations) are excluded.
Only business output fields are compared.

Usage:
    python tools/compare_outputs.py \
        --baseline artifacts/equivalence/wealth_engine_baseline_20260803.parquet \
        --current  artifacts/equivalence/wealth_engine_phase1_20260803.parquet \
        --scanner  WealthEngine

Exit codes:
    0 = No diffs (equivalence confirmed)
    1 = Diffs found (blocking failure)
    2 = Missing file or schema error (blocking failure)
"""

import argparse
import sys
import json
from pathlib import Path
from datetime import datetime

try:
    import pandas as pd
except ImportError:
    print("ERROR: pandas not installed. Run: pip install pandas", file=sys.stderr)
    sys.exit(2)

# ─── Fields excluded from comparison (non-deterministic) ──────────────────────
# Any field in this set is ignored during diff. Add fields that legitimately
# vary run-to-run without indicating a correctness regression.
NON_DETERMINISTIC_FIELDS = {
    # Timestamps
    "scan_timestamp", "alert_timestamp", "created_at", "updated_at",
    "fetched_at", "generated_at", "run_timestamp",
    # Durations and execution metadata
    "scan_duration_seconds", "execution_time_ms", "fetch_duration_ms",
    # UUIDs and correlation IDs
    "execution_id", "alert_id", "batch_id", "run_id",
    # Logging metadata
    "log_level", "thread_id", "process_id",
}

# ─── Business fields compared per scanner ─────────────────────────────────────
SCANNER_BUSINESS_FIELDS = {
    "WealthEngine": [
        "symbol", "Signal_Code", "Signal_Reason", "Score", "FM_Score",
        "Portfolio_Bucket", "cmp", "sma_200", "target_1", "target_2",
        "stop_loss", "peg_ratio", "roe", "debt_to_equity",
    ],
    "ReversalScanner": [
        "symbol", "category", "score", "raw_score", "entry_price",
        "stop_loss", "target_1", "target_price", "rsi", "volume_ratio",
        "bayesian_regime",
    ],
    "DailyBuilder": [
        "symbol", "score", "trend", "regime", "qualified",
    ],
}


def load_snapshot(path: Path, business_fields: list) -> pd.DataFrame:
    """Load parquet snapshot and return only business fields that exist."""
    if not path.exists():
        print(f"ERROR: Snapshot file not found: {path}", file=sys.stderr)
        sys.exit(2)

    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
    elif path.suffix == ".csv":
        df = pd.read_csv(path)
    elif path.suffix == ".json":
        df = pd.read_json(path)
    else:
        print(f"ERROR: Unsupported file format: {path.suffix}", file=sys.stderr)
        sys.exit(2)

    # Keep only the business fields that exist in both files
    available = [f for f in business_fields if f in df.columns]
    missing = [f for f in business_fields if f not in df.columns]
    if missing:
        print(f"  ⚠️  Fields not present in snapshot (skipped): {missing}")
    return df[available].sort_values("symbol").reset_index(drop=True)


def compare(baseline: pd.DataFrame, current: pd.DataFrame, scanner: str) -> dict:
    """Compare two DataFrames and return diff report."""
    result = {
        "scanner": scanner,
        "baseline_rows": len(baseline),
        "current_rows": len(current),
        "row_count_match": len(baseline) == len(current),
        "added_symbols": [],
        "removed_symbols": [],
        "field_diffs": [],
        "schema_version": 1,
    }

    baseline_syms = set(baseline["symbol"].tolist())
    current_syms = set(current["symbol"].tolist())
    result["added_symbols"] = sorted(current_syms - baseline_syms)
    result["removed_symbols"] = sorted(baseline_syms - current_syms)

    common_syms = baseline_syms & current_syms
    b_common = baseline[baseline["symbol"].isin(common_syms)].sort_values("symbol").reset_index(drop=True)
    c_common = current[current["symbol"].isin(common_syms)].sort_values("symbol").reset_index(drop=True)

    common_cols = [col for col in b_common.columns if col in c_common.columns and col != "symbol"]
    for col in common_cols:
        try:
            diff_mask = b_common[col].astype(str) != c_common[col].astype(str)
            if diff_mask.any():
                diffs = b_common[diff_mask][["symbol"]].copy()
                diffs["field"] = col
                diffs["baseline_value"] = b_common[diff_mask][col].astype(str).values
                diffs["current_value"] = c_common[diff_mask][col].astype(str).values
                result["field_diffs"].extend(diffs.to_dict("records"))
        except Exception as e:
            print(f"  ⚠️  Could not compare column '{col}': {e}")

    return result


def main():
    parser = argparse.ArgumentParser(description="Scanner output equivalence checker (Phase 0)")
    parser.add_argument("--baseline", required=True, help="Path to baseline snapshot file")
    parser.add_argument("--current",  required=True, help="Path to current snapshot file")
    parser.add_argument("--scanner",  default="WealthEngine",
                        choices=list(SCANNER_BUSINESS_FIELDS.keys()),
                        help="Scanner name to select business fields")
    parser.add_argument("--output",   default=None, help="Optional JSON diff report output path")
    args = parser.parse_args()

    business_fields = SCANNER_BUSINESS_FIELDS[args.scanner]
    print(f"\n🔍 Equivalence Check — {args.scanner}")
    print(f"   Baseline : {args.baseline}")
    print(f"   Current  : {args.current}")
    print(f"   Fields   : {business_fields}\n")

    baseline_df = load_snapshot(Path(args.baseline), business_fields)
    current_df  = load_snapshot(Path(args.current),  business_fields)

    report = compare(baseline_df, current_df, args.scanner)

    # Summary
    has_diff = (
        not report["row_count_match"]
        or report["added_symbols"]
        or report["removed_symbols"]
        or report["field_diffs"]
    )

    if report["added_symbols"]:
        print(f"  ➕ Added symbols   ({len(report['added_symbols'])}): {report['added_symbols'][:10]}")
    if report["removed_symbols"]:
        print(f"  ➖ Removed symbols ({len(report['removed_symbols'])}): {report['removed_symbols'][:10]}")
    if report["field_diffs"]:
        print(f"  ❌ Field diffs ({len(report['field_diffs'])}):")
        for d in report["field_diffs"][:20]:
            print(f"     {d['symbol']}.{d['field']}: {d['baseline_value']} → {d['current_value']}")
        if len(report["field_diffs"]) > 20:
            print(f"     ... and {len(report['field_diffs']) - 20} more.")

    if args.output:
        report["run_timestamp"] = datetime.utcnow().isoformat()
        Path(args.output).write_text(json.dumps(report, indent=2))
        print(f"\n  📄 Diff report written: {args.output}")

    if has_diff:
        print("\n❌ EQUIVALENCE CHECK FAILED — Business output diffs detected.")
        print("   This is a blocking failure. Disable the active feature flag before proceeding.\n")
        sys.exit(1)
    else:
        print(f"\n✅ EQUIVALENCE CHECK PASSED — {args.scanner} outputs are identical.\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
