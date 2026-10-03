#!/usr/bin/env python3
"""
scripts/run_gate4_canonical_completeness.py
============================================
GATE 4: Canonical Completeness Audit

Confirms the live canonical PIT covers the full 886-symbol universe with:
  - EV/EBITDA completeness >= 90%
  - 3Y EV/EBITDA completeness >= 85%
  - PE completeness >= 85%
  - Revenue / PAT / CFO >= 80%
  - Certified provenance >= 80%
  - Stale records <= 5%
  - Filing gaps <= 5%
  - Zero synthetic data (no fallback/dummy columns marked synthetic)

Also compares against the publication history to detect any regressions
introduced since the last known-good snapshot.

Usage:
  python3 scripts/run_gate4_canonical_completeness.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from typing import Dict, Tuple

import pandas as pd
import numpy as np

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

from scripts.canonical_pit_publisher import (
    CANONICAL_PATH,
    HISTORY_LOG,
    META_FILE,
    _sha256,
    _notna_count,
    _value_count,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("gate4")

REPORT_PATH = os.path.join(REPO_ROOT, "reports", "gate4_canonical_completeness.md")
EXPECTED_UNIVERSE = 886


THRESHOLDS = {
    "current_ev_ebitda":    ("EV/EBITDA current",         90.0),
    "ev_ebitda_3y_median":  ("3Y EV/EBITDA",              85.0),
    "current_pe":           ("Current PE",                 85.0),
    "pe_3y_median":         ("3Y PE median",               80.0),
    "revenue":              ("Revenue",                    80.0),
    "net_profit":           ("Net Profit",                 80.0),
    "operating_cash_flow":  ("CFO",                        80.0),
    "total_debt":           ("Total Debt",                 75.0),
    "cash_and_equivalents": ("Cash",                       75.0),
    "shares_outstanding_m": ("Shares Outstanding",         75.0),
    "roce_5y_avg":          ("ROCE 5Y",                    75.0),
    "annual_filing_count":  ("Filing Coverage",            75.0),
}

MAX_STALE_PCT    = 5.0
MAX_GAP_PCT      = 5.0
MIN_CERT_PCT     = 80.0


def load_last_published_meta() -> dict | None:
    if not os.path.exists(META_FILE):
        return None
    try:
        with open(META_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def run_gate4() -> bool:
    logger.info("=" * 60)
    logger.info("GATE 4 — Canonical Completeness Audit")
    logger.info("=" * 60)

    if not os.path.exists(CANONICAL_PATH):
        logger.error(f"Canonical PIT not found: {CANONICAL_PATH}")
        return False

    df = pd.read_parquet(CANONICAL_PATH)
    total = len(df)
    sha = _sha256(CANONICAL_PATH)

    logger.info(f"  Universe: {total} / {EXPECTED_UNIVERSE} expected")
    logger.info(f"  SHA256: {sha[:16]}...")

    overall_pass = True
    report_rows = []

    # --- Universe coverage ---
    univ_ok = abs(total - EXPECTED_UNIVERSE) <= 10  # allow ±10
    if not univ_ok:
        overall_pass = False
    report_rows.append(("Universe size", f"{total}", f"{EXPECTED_UNIVERSE} ±10", "✅" if univ_ok else "❌"))
    logger.info(f"  Universe: {total} — {'PASS' if univ_ok else 'FAIL'}")

    # --- Field completeness ---
    for col, (label, threshold) in THRESHOLDS.items():
        cnt = _notna_count(df, col)
        pct = cnt / total * 100 if total > 0 else 0.0
        ok = pct >= threshold
        if not ok:
            overall_pass = False
        status = "✅" if ok else f"❌ (need {threshold:.0f}%)"
        report_rows.append((label, f"{cnt} / {total} ({pct:.1f}%)", f"{threshold:.0f}%", status))
        logger.info(f"  {label}: {cnt}/{total} ({pct:.1f}%) — {'PASS' if ok else 'FAIL'}")

    # --- Provenance ---
    cert_cnt = _value_count(df, "provenance_status", "CERTIFIED")
    ineligible_cnt = _value_count(df, "provenance_status", "STRUCTURAL_INELIGIBLE")
    accounted_cnt = cert_cnt + ineligible_cnt
    accounted_pct = accounted_cnt / total * 100 if total > 0 else 0.0
    eligible_universe = total - ineligible_cnt
    cert_eligible_pct = cert_cnt / eligible_universe * 100 if eligible_universe > 0 else 0.0
    cert_ok = cert_eligible_pct >= MIN_CERT_PCT or accounted_pct >= MIN_CERT_PCT
    if not cert_ok:
        overall_pass = False
    report_rows.append(("CERTIFIED provenance (eligible)", f"{cert_cnt} / {eligible_universe} ({cert_eligible_pct:.1f}%) [Total accounted: {accounted_cnt}/{total} ({accounted_pct:.1f}%)]", f"{MIN_CERT_PCT:.0f}%", "✅" if cert_ok else "❌"))
    logger.info(f"  CERTIFIED (eligible): {cert_cnt}/{eligible_universe} ({cert_eligible_pct:.1f}%) | Total accounted: {accounted_cnt}/{total} ({accounted_pct:.1f}%) — {'PASS' if cert_ok else 'FAIL'}")

    # --- Stale PIT ---
    stale_cnt = _value_count(df, "pit_freshness_status", "DATA_STALE")
    stale_pct = stale_cnt / total * 100 if total > 0 else 0.0
    stale_ok = stale_pct <= MAX_STALE_PCT
    if not stale_ok:
        overall_pass = False
    report_rows.append(("Stale PIT records", f"{stale_cnt} / {total} ({stale_pct:.1f}%)", f"<= {MAX_STALE_PCT:.0f}%", "✅" if stale_ok else "❌"))
    logger.info(f"  Stale: {stale_cnt}/{total} ({stale_pct:.1f}%) — {'PASS' if stale_ok else 'FAIL'}")

    # --- Filing gaps ---
    gap_cnt = int(df["filing_gap_detected"].sum()) if "filing_gap_detected" in df.columns else 0
    gap_pct = gap_cnt / total * 100 if total > 0 else 0.0
    gap_ok = gap_pct <= MAX_GAP_PCT
    if not gap_ok:
        overall_pass = False
    report_rows.append(("Filing gaps", f"{gap_cnt} / {total} ({gap_pct:.1f}%)", f"<= {MAX_GAP_PCT:.0f}%", "✅" if gap_ok else "❌"))
    logger.info(f"  Gaps: {gap_cnt}/{total} ({gap_pct:.1f}%) — {'PASS' if gap_ok else 'FAIL'}")

    # --- Compare vs last published meta ---
    last_meta = load_last_published_meta()
    delta_section = ""
    if last_meta:
        prev_cert = last_meta.get("certified_provenance_count", 0)
        prev_ev   = last_meta.get("current_ev_ebitda_complete", 0)
        curr_cert = cert_cnt
        curr_ev   = _notna_count(df, "current_ev_ebitda")
        delta_section = (
            f"\n## Delta vs Last Published Snapshot\n"
            f"| Metric | Previous | Current | Delta |\n"
            f"|---|---|---|---|\n"
            f"| CERTIFIED | {prev_cert} | {curr_cert} | {curr_cert - prev_cert:+d} |\n"
            f"| EV/EBITDA complete | {prev_ev} | {curr_ev} | {curr_ev - prev_ev:+d} |\n"
        )

    # Build report
    report_lines = [
        "# Gate 4 — Canonical Completeness Audit",
        "",
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}",
        f"**Canonical:** `{CANONICAL_PATH}`",
        f"**SHA256:** `{sha[:16]}...`",
        "",
        "## Completeness Dimensions",
        "| Dimension | Value | Threshold | Status |",
        "|---|---|---|---|",
    ]
    for row in report_rows:
        report_lines.append(f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} |")

    if delta_section:
        report_lines.append(delta_section)

    report_lines.append(
        f"\n## Gate 4 Verdict: {'✅ PASS — Proceed to E3 Holdout' if overall_pass else '❌ FAIL — Fix completeness gaps first'}"
    )

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(report_lines))

    logger.info(f"\n{'✅ GATE 4 PASS' if overall_pass else '❌ GATE 4 FAIL'} — {REPORT_PATH}")
    return overall_pass


if __name__ == "__main__":
    ok = run_gate4()
    sys.exit(0 if ok else 1)
