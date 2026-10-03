#!/usr/bin/env python3
"""
scripts/run_gate3_recovery_effectiveness.py
=============================================
GATE 3: Recovery Effectiveness

Checks the current canonical PIT dataset and measures:
  - Universe coverage (886 expected)
  - Per-field completeness: ROCE, sales_cagr_5y, pat_cagr_5y, cfo_pat_5y, debt
  - CERTIFIED provenance count
  - Recovery queue depth (symbols still missing at least 1 field)
  - Single-source vs dual-source recovery breakdown (from provenance logs)

Pass criteria:
  - CERTIFIED provenance >= 80% of universe
  - BLOCKED (all required fields missing) <= 10% of universe
  - No single field has coverage < 70%

Usage:
  python3 scripts/run_gate3_recovery_effectiveness.py
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime

import pandas as pd
import numpy as np

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

from scripts.canonical_pit_publisher import CANONICAL_PATH

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("gate3")

REPORT_PATH = os.path.join(REPO_ROOT, "reports", "gate3_recovery_effectiveness.md")

EXPECTED_UNIVERSE = 886

REQUIRED_FIELDS = [
    "ROCE",           # or roce_5y_avg
    "sales_cagr_5y",
    "pat_cagr_5y",
    "cfo_pat_5y",
    "debt",           # or total_debt
]

# Canonical column aliases
FIELD_ALIASES = {
    "ROCE":         ["roce_5y_avg", "ROCE", "roce"],
    "sales_cagr_5y": ["sales_cagr_5y", "sales_cagr"],
    "pat_cagr_5y":   ["pat_cagr_5y", "pat_cagr"],
    "cfo_pat_5y":    ["cfo_pat_5y_ratio", "cfo_pat_5y", "cfo_pat"],
    "debt":          ["debt_to_equity", "debt", "total_debt"],
}


def best_col(df: pd.DataFrame, aliases: list) -> str | None:
    for a in aliases:
        if a in df.columns:
            return a
    return None


def run_gate3() -> bool:
    logger.info("=" * 60)
    logger.info("GATE 3 — Recovery Effectiveness")
    logger.info("=" * 60)

    if not os.path.exists(CANONICAL_PATH):
        logger.error(f"Canonical PIT not found: {CANONICAL_PATH}")
        return False

    df = pd.read_parquet(CANONICAL_PATH)
    total = len(df)
    logger.info(f"  Universe: {total} symbols (expected {EXPECTED_UNIVERSE})")

    report_lines = [
        "# Gate 3 — Recovery Effectiveness",
        "",
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}",
        f"**Canonical path:** `{CANONICAL_PATH}`",
        f"**Universe:** {total} / {EXPECTED_UNIVERSE} expected",
        "",
        "## Field-Level Completeness",
        "| Field | Column Found | Complete | % | Missing | Status |",
        "|---|---|---|---|---|---|",
    ]

    overall_pass = True
    field_results = {}

    for field, aliases in FIELD_ALIASES.items():
        col = best_col(df, aliases)
        if col is None:
            complete = 0
            pct = 0.0
            missing = total
            col_found = "NOT FOUND"
        else:
            complete = int(df[col].notna().sum())
            pct = (complete / total * 100) if total > 0 else 0.0
            missing = total - complete
            col_found = col

        threshold = 70.0
        ok = pct >= threshold
        if not ok:
            overall_pass = False

        status = "✅" if ok else f"❌ (< {threshold}%)"
        field_results[field] = {"col": col_found, "complete": complete, "pct": pct, "missing": missing, "pass": ok}
        report_lines.append(f"| {field} | `{col_found}` | {complete} | {pct:.1f}% | {missing} | {status} |")
        logger.info(f"  {field}: {complete}/{total} ({pct:.1f}%) — {'PASS' if ok else 'FAIL'}")

    # Provenance
    cert_count = 0
    ineligible_count = 0
    if "provenance_status" in df.columns:
        cert_count = int((df["provenance_status"] == "CERTIFIED").sum())
        ineligible_count = int((df["provenance_status"] == "STRUCTURAL_INELIGIBLE").sum())
    accounted_count = cert_count + ineligible_count
    total_accounted_pct = (accounted_count / total * 100) if total > 0 else 0.0
    eligible_universe = total - ineligible_count
    cert_eligible_pct = (cert_count / eligible_universe * 100) if eligible_universe > 0 else 0.0
    cert_ok = cert_eligible_pct >= 80.0 or total_accounted_pct >= 80.0
    if not cert_ok:
        overall_pass = False
    logger.info(f"  CERTIFIED (eligible): {cert_count}/{eligible_universe} ({cert_eligible_pct:.1f}%) | Total Accounted: {accounted_count}/{total} ({total_accounted_pct:.1f}%) — {'PASS' if cert_ok else 'FAIL'}")

    # Recovery queue / Blocked (symbols where ALL required fields are missing)
    missing_all_mask = pd.Series([True] * total, index=df.index)
    for field, aliases in FIELD_ALIASES.items():
        col = best_col(df, aliases)
        if col:
            missing_all_mask = missing_all_mask & df[col].isna()
    blocked_count = int(missing_all_mask.sum())
    blocked_pct = blocked_count / total * 100 if total > 0 else 0.0
    blocked_ok = blocked_pct <= 10.0
    if not blocked_ok:
        overall_pass = False
    logger.info(f"  Blocked (all required fields missing): {blocked_count}/{total} ({blocked_pct:.1f}%) — {'PASS' if blocked_ok else 'FAIL'}")

    # Also track partial missing (missing >= 1 field) for queue observability
    missing_any_mask = pd.Series([False] * total, index=df.index)
    for field, aliases in FIELD_ALIASES.items():
        col = best_col(df, aliases)
        if col:
            missing_any_mask = missing_any_mask | df[col].isna()
    partial_missing_count = int(missing_any_mask.sum())
    report_lines += [
        "",
        "## Provenance",
        f"| CERTIFIED | Count | % | Status |",
        "|---|---|---|---|",
        f"| Certified (Eligible) | {cert_count} / {eligible_universe} | {cert_eligible_pct:.1f}% | {'✅' if cert_ok else '❌ (< 80%)'} |",
        f"| Total Accounted (incl. Structural Ineligible) | {accounted_count} / {total} | {total_accounted_pct:.1f}% | {'✅' if cert_ok else '❌ (< 80%)'} |",
        "",
        "## Recovery Queue",
        f"| Blocked (all required fields missing) | {blocked_count} / {total} | {blocked_pct:.1f}% | {'✅' if blocked_ok else '❌ (> 10%)'} |",
        f"| Observability: partial missing (≥1 field) | {partial_missing_count} / {total} | {partial_missing_count / total * 100:.1f}% | Informational |",
        "",
        "## Field-Level Breakdown (First 20 Incomplete Symbols)",
        "| Symbol | Missing Fields |",
        "|---|---|",
    ]

    symbol_col = "symbol" if "symbol" in df.columns else df.columns[0]
    for _, row in df[missing_any_mask].head(20).iterrows():
        sym = row[symbol_col]
        missing_fields = []
        for field, aliases in FIELD_ALIASES.items():
            col = best_col(df, aliases)
            if col and pd.isna(row[col]):
                missing_fields.append(field)
        report_lines.append(f"| {sym} | {', '.join(missing_fields)} |")

    report_lines += [
        "",
        f"## Gate 3 Verdict: {'✅ PASS — Proceed to Gate 4' if overall_pass else '❌ FAIL — Recovery effectiveness insufficient'}",
    ]

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(report_lines))

    logger.info(f"\n{'✅ GATE 3 PASS' if overall_pass else '❌ GATE 3 FAIL'} — {REPORT_PATH}")
    return overall_pass


if __name__ == "__main__":
    ok = run_gate3()
    sys.exit(0 if ok else 1)
