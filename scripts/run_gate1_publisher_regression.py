#!/usr/bin/env python3
"""
scripts/run_gate1_publisher_regression.py
==========================================
GATE 1: Publisher Regression Tests

Tests:
  - Negative: Degraded candidate (822/886 → 465/884) MUST be BLOCKED.
              Canonical SHA256 MUST be unchanged after the attempt.
  - Positive: Improved/valid candidate MUST be PUBLISHED.

Usage:
  python3 scripts/run_gate1_publisher_regression.py
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile

import shutil
import pandas as pd
import numpy as np

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

from scripts.canonical_pit_publisher import (
    CANONICAL_PATH,
    MASTER_V2_PATH,
    META_FILE,
    publish_canonical_pit,
    _sha256,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("gate1")

REPORT_PATH = os.path.join(REPO_ROOT, "reports", "gate1_publisher_regression.md")


def synthesise_degraded_candidate(df_canonical: pd.DataFrame) -> str:
    """
    Build a degraded candidate that mimics the 822/886 → 465/884 failure:
      - Drop ~33% of symbols (simulates the lost symbol_set)
      - Null-out EV/EBITDA + ROCE for remainder
      - Reduce CERTIFIED provenance count
    """
    df = df_canonical.copy()
    n_drop = max(1, len(df) // 3)
    drop_idx = df.sample(n=n_drop, random_state=42).index
    df = df.drop(drop_idx).reset_index(drop=True)

    for col in ["current_ev_ebitda", "ev_ebitda_3y_median", "current_pe", "roce_5y_avg"]:
        if col in df.columns:
            null_idx = df.sample(frac=0.4, random_state=7).index
            df.loc[null_idx, col] = np.nan

    if "provenance_status" in df.columns:
        bad_idx = df.sample(frac=0.3, random_state=13).index
        df.loc[bad_idx, "provenance_status"] = "STALE"

    fd, path = tempfile.mkstemp(suffix="_degraded_candidate.parquet")
    os.close(fd)
    df.to_parquet(path, index=False)
    return path


def synthesise_improved_candidate(df_canonical: pd.DataFrame) -> str:
    """
    Build a candidate strictly better than current canonical.
    All symbols retained. All financial columns filled. All provenance CERTIFIED.
    """
    df = df_canonical.copy()

    fill_map = {
        "current_ev_ebitda":    15.0,
        "ev_ebitda_3y_median":  14.0,
        "current_pe":           22.0,
        "pe_3y_median":         20.0,
        "revenue":              1000.0,
        "ebitda":               200.0,
        "net_profit":           100.0,
        "operating_cash_flow":  90.0,
        "total_debt":           50.0,
        "cash_and_equivalents": 80.0,
        "shares_outstanding_m": 100.0,
        "roce_5y_avg":          18.0,
        "annual_filing_count":  5.0,
    }
    for col, val in fill_map.items():
        if col in df.columns:
            df[col] = df[col].fillna(val)
        else:
            df[col] = val

    if "provenance_status" in df.columns:
        df["provenance_status"] = "CERTIFIED"
    if "pit_freshness_status" in df.columns:
        df["pit_freshness_status"] = df["pit_freshness_status"].replace("DATA_STALE", "VALID")
    if "filing_gap_detected" in df.columns:
        df["filing_gap_detected"] = False

    fd, path = tempfile.mkstemp(suffix="_improved_candidate.parquet")
    os.close(fd)
    df.to_parquet(path, index=False)
    return path


def run_gate1() -> bool:
    logger.info("=" * 60)
    logger.info("GATE 1 — Publisher Regression Tests")
    logger.info("=" * 60)

    if not os.path.exists(CANONICAL_PATH):
        logger.error(f"Canonical PIT not found: {CANONICAL_PATH}")
        return False

    df_canonical = pd.read_parquet(CANONICAL_PATH)
    canonical_sha = _sha256(CANONICAL_PATH)
    logger.info(f"  Canonical: {len(df_canonical)} symbols | SHA256: {canonical_sha[:16]}...")

    results = {}

    # --- TEST 1A: Negative ---
    logger.info("\n[1A] Negative: degraded candidate must be BLOCKED...")
    deg_path = synthesise_degraded_candidate(df_canonical)
    try:
        r = publish_canonical_pit(deg_path, reason="GATE1_REGRESSION_NEGATIVE")
        sha_after = _sha256(CANONICAL_PATH)
        sha_unchanged = sha_after == canonical_sha
        decision = r.get("publication_decision", "UNKNOWN")
        gate1a = (decision == "BLOCKED") and sha_unchanged
        results.update({
            "1A_decision": decision,
            "1A_sha_unchanged": sha_unchanged,
            "1A_gate_reasons": r.get("gate_reasons", []),
            "1A_pass": gate1a,
        })
        logger.info(f"  [1A] {'PASS ✅' if gate1a else 'FAIL ❌'} | decision={decision} | sha_unchanged={sha_unchanged}")
    finally:
        os.unlink(deg_path)

    # --- TEST 1B: Positive ---
    logger.info("\n[1B] Positive: improved candidate must be PUBLISHED...")
    imp_path = synthesise_improved_candidate(df_canonical)
    can_backup = CANONICAL_PATH + ".gate1_backup"
    v2_backup = MASTER_V2_PATH + ".gate1_backup"
    meta_backup = META_FILE + ".gate1_backup"

    shutil.copy2(CANONICAL_PATH, can_backup)
    if os.path.exists(MASTER_V2_PATH):
        shutil.copy2(MASTER_V2_PATH, v2_backup)
    if os.path.exists(META_FILE):
        shutil.copy2(META_FILE, meta_backup)

    try:
        r = publish_canonical_pit(imp_path, reason="GATE1_REGRESSION_POSITIVE")
        decision = r.get("publication_decision", "UNKNOWN")
        gate1b = decision == "PUBLISHED"
        results.update({"1B_decision": decision, "1B_pass": gate1b})
        logger.info(f"  [1B] {'PASS ✅' if gate1b else 'FAIL ❌'} | decision={decision}")
    finally:
        os.unlink(imp_path)
        # Restore real canonical data unconditionally
        if os.path.exists(can_backup):
            shutil.copy2(can_backup, CANONICAL_PATH)
            os.unlink(can_backup)
        if os.path.exists(v2_backup):
            shutil.copy2(v2_backup, MASTER_V2_PATH)
            os.unlink(v2_backup)
        if os.path.exists(meta_backup):
            shutil.copy2(meta_backup, META_FILE)
            os.unlink(meta_backup)
        # Verify canonical hash restored
        current_sha = _sha256(CANONICAL_PATH)
        assert current_sha == canonical_sha, f"Canonical hash mismatch after restore: {current_sha} != {canonical_sha}"
        logger.info(f"  [1B] Restored canonical files; SHA256 verified: {current_sha[:16]}...")

    gate1_pass = results.get("1A_pass", False) and results.get("1B_pass", False)

    report_lines = [
        "# Gate 1 — Publisher Regression",
        "",
        f"**Canonical symbols:** {len(df_canonical)}",
        f"**Canonical SHA256:** `{canonical_sha[:16]}...`",
        "",
        "## Test 1A — Negative (Degraded Candidate)",
        f"- Decision: `{results['1A_decision']}`",
        f"- Canonical SHA unchanged: `{results['1A_sha_unchanged']}`",
        f"- Gate violations: {results['1A_gate_reasons']}",
        f"- **Result: {'PASS ✅' if results['1A_pass'] else 'FAIL ❌'}**",
        "",
        "## Test 1B — Positive (Improved Candidate)",
        f"- Decision: `{results['1B_decision']}`",
        f"- **Result: {'PASS ✅' if results['1B_pass'] else 'FAIL ❌'}**",
        "",
        f"## Gate 1 Verdict: {'✅ PASS — Proceed to Gate 2' if gate1_pass else '❌ FAIL — Do NOT proceed'}",
    ]

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(report_lines))

    logger.info(f"\n{'✅ GATE 1 PASS' if gate1_pass else '❌ GATE 1 FAIL'} — {REPORT_PATH}")
    return gate1_pass


if __name__ == "__main__":
    ok = run_gate1()
    sys.exit(0 if ok else 1)
