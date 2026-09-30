#!/usr/bin/env python3
"""
scripts/certify_pit_quarterly_dataset.py
========================================
POST-INGESTION CERTIFICATION AUDIT FOR PEAD_FUNDAMENTAL_V1 PIT DATASET

This script runs 14 certification checks before the PEAD backtest is allowed to start.
It writes a certification report to reports/pead_fundamental_v1/PIT_CERTIFICATION_REPORT.md

CERTIFICATION GATES (all must pass):
  [01] QUARTERLY row count > 0
  [02] QUARTERLY coverage by symbol (>= 500 symbols)
  [03] QUARTERLY coverage by period (earliest <= 2013-03-31)
  [04] EPS coverage for QUARTERLY rows (>= 85%)
  [05] Revenue coverage for QUARTERLY rows (>= 90%)
  [06] Operating profit coverage for QUARTERLY rows (>= 85%)
  [07] ANNUAL: OCF coverage (>= 60% of industrial symbols)
  [08] ANNUAL: ROCE coverage (>= 70% of industrial symbols)
  [09] ANNUAL: ROE coverage (>= 70% of industrial symbols)
  [10] Timestamp basis is LODR_STATUTORY_DEADLINE_CONSERVATIVE for all rows
  [11] PIT chronology: conservative_availability_timestamp > period_end_date for all rows
  [12] Duplicate/revision audit: no duplicate (symbol, period_end_date, statement_type, revision_number)
  [13] Missing-quarter audit: per-symbol quarterly gaps (report gaps > 3 consecutive)
  [14] SHA256 consistency with ingestion_manifest.json

VERDICT:
  ALL_PASS  → PEAD backtest may proceed (freeze dataset first)
  ANY_FAIL  → PEAD backtest BLOCKED
"""

from __future__ import annotations
import os, sys, json, sqlite3, hashlib, logging
from datetime import datetime, date, timedelta
from typing import Dict, List, Any
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

REPO_ROOT    = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR     = os.path.join(REPO_ROOT, "data")
PIT_DIR      = os.path.join(DATA_DIR, "pit_fundamentals_v1")
PIT_DB_PATH  = os.path.join(PIT_DIR, "pit_fundamentals_v1.db")
MANIFEST_PATH= os.path.join(PIT_DIR, "ingestion_manifest.json")
REPORT_DIR   = os.path.join(REPO_ROOT, "reports", "pead_fundamental_v1")
os.makedirs(REPORT_DIR, exist_ok=True)

# Financial sector symbols to exclude from industrial gate checks
FINANCIAL_SECTOR_TERMS = {
    "bank","nbc","hfc","insurance","amfi","amc","finance","financial",
    "capital","credit","leasing","invest"
}

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path,"rb") as f:
        for chunk in iter(lambda: f.read(65536), b""): h.update(chunk)
    return h.hexdigest()

def main():
    logger.info("="*70)
    logger.info("PEAD_FUNDAMENTAL_V1 — PIT Dataset Certification Audit")
    logger.info("="*70)

    if not os.path.exists(PIT_DB_PATH):
        logger.error(f"FATAL: PIT DB not found at {PIT_DB_PATH}")
        logger.error("Run: python3 scripts/ingest_pit_quarterly_and_annual.py first.")
        sys.exit(1)

    con = sqlite3.connect(PIT_DB_PATH)
    df_all = pd.read_sql("SELECT * FROM pit_fundamentals_v1", con)
    con.close()

    df_q = df_all[df_all["statement_type"] == "QUARTERLY"].copy()
    df_a = df_all[df_all["statement_type"].isin(["ANNUAL","ANNUAL_FINANCIAL_SECTOR"])].copy()

    results: List[Dict] = []
    all_pass = True

    def gate(num, name, passed, detail=""):
        nonlocal all_pass
        status = "PASS" if passed else "FAIL"
        if not passed: all_pass = False
        results.append({"gate": f"[{num:02d}]", "name": name, "status": status, "detail": detail})
        icon = "✅" if passed else "❌"
        logger.info(f"  {icon} [{num:02d}] {name}: {status} | {detail}")
        return passed

    logger.info(f"\nTotal rows: {len(df_all)} | QUARTERLY: {len(df_q)} | ANNUAL: {len(df_a)}")
    logger.info("")

    # [01] QUARTERLY row count
    q_count = len(df_q)
    gate(1, "QUARTERLY row count > 0", q_count > 0,
         f"{q_count} QUARTERLY rows found")

    # [02] QUARTERLY symbol coverage
    q_syms = df_q["symbol"].nunique()
    gate(2, "QUARTERLY symbols >= 500", q_syms >= 500,
         f"{q_syms} symbols have at least one QUARTERLY row")

    # [03] QUARTERLY earliest period
    if q_count > 0:
        earliest = pd.to_datetime(df_q["period_end_date"]).min().date()
        gate(3, "QUARTERLY earliest period <= 2013-03-31",
             earliest <= date(2013, 3, 31),
             f"Earliest period_end_date = {earliest}")
    else:
        gate(3, "QUARTERLY earliest period <= 2013-03-31", False, "No quarterly rows")

    # [04] EPS coverage for QUARTERLY
    if q_count > 0:
        eps_pct = df_q["eps"].notna().mean() * 100
        gate(4, "QUARTERLY EPS coverage >= 85%", eps_pct >= 85.0,
             f"{eps_pct:.1f}% of quarterly rows have non-null EPS")
    else:
        gate(4, "QUARTERLY EPS coverage >= 85%", False, "No quarterly rows")

    # [05] Revenue coverage
    if q_count > 0:
        rev_pct = df_q["revenue"].notna().mean() * 100
        gate(5, "QUARTERLY Revenue coverage >= 90%", rev_pct >= 90.0,
             f"{rev_pct:.1f}% of quarterly rows have non-null revenue")
    else:
        gate(5, "QUARTERLY Revenue coverage >= 90%", False, "No quarterly rows")

    # [06] Operating profit coverage
    if q_count > 0:
        op_pct = df_q["operating_profit"].notna().mean() * 100
        gate(6, "QUARTERLY Operating Profit coverage >= 85%", op_pct >= 85.0,
             f"{op_pct:.1f}% of quarterly rows have non-null operating_profit")
    else:
        gate(6, "QUARTERLY Operating Profit coverage >= 85%", False, "No quarterly rows")

    # [07] ANNUAL OCF coverage (industrial only)
    df_a_ind = df_a[df_a["statement_type"] == "ANNUAL"]
    if len(df_a_ind) > 0:
        ocf_pct = df_a_ind["operating_cash_flow"].notna().mean() * 100
        gate(7, "ANNUAL OCF coverage >= 60% (industrial)", ocf_pct >= 60.0,
             f"{ocf_pct:.1f}% of annual industrial rows have OCF")
    else:
        gate(7, "ANNUAL OCF coverage >= 60% (industrial)", False, "No annual industrial rows")

    # [08] ANNUAL ROCE coverage
    if len(df_a_ind) > 0:
        roce_pct = df_a_ind["roce"].notna().mean() * 100
        gate(8, "ANNUAL ROCE coverage >= 70% (industrial)", roce_pct >= 70.0,
             f"{roce_pct:.1f}% of annual industrial rows have ROCE")
    else:
        gate(8, "ANNUAL ROCE coverage >= 70% (industrial)", False, "No annual industrial rows")

    # [09] ANNUAL ROE coverage
    if len(df_a_ind) > 0:
        roe_pct = df_a_ind["roe"].notna().mean() * 100
        gate(9, "ANNUAL ROE coverage >= 70% (industrial)", roe_pct >= 70.0,
             f"{roe_pct:.1f}% of annual industrial rows have ROE")
    else:
        gate(9, "ANNUAL ROE coverage >= 70% (industrial)", False, "No annual industrial rows")

    # [10] Timestamp basis
    if "timestamp_basis" in df_all.columns:
        basis_ok = (df_all["timestamp_basis"] == "LODR_STATUTORY_DEADLINE_CONSERVATIVE").all()
        wrong = int((df_all["timestamp_basis"] != "LODR_STATUTORY_DEADLINE_CONSERVATIVE").sum())
        gate(10, "All rows have LODR_STATUTORY_DEADLINE_CONSERVATIVE basis",
             basis_ok, f"{wrong} rows with wrong basis" if not basis_ok else "All rows correctly tagged")
    else:
        gate(10, "All rows have LODR_STATUTORY_DEADLINE_CONSERVATIVE basis", False,
             "timestamp_basis column missing")

    # [11] PIT chronology: conservative_ts > period_end_date
    df_chron = df_all.copy()
    df_chron["ped"] = pd.to_datetime(df_chron["period_end_date"])
    df_chron["cts"] = pd.to_datetime(df_chron["conservative_availability_timestamp"])
    chrono_fail = (df_chron["cts"] <= df_chron["ped"]).sum()
    gate(11, "PIT chronology: conservative_ts > period_end_date",
         chrono_fail == 0,
         f"{chrono_fail} rows where conservative_ts <= period_end_date (PIT violation)")

    # [12] Duplicate audit
    dup_count = df_all.duplicated(
        subset=["symbol","period_end_date","statement_type","revision_number"]
    ).sum()
    gate(12, "No duplicate (symbol, period_end_date, statement_type, revision_number)",
         dup_count == 0,
         f"{dup_count} duplicate rows found")

    # [13] Missing-quarter audit: per-symbol gap analysis
    if q_count > 0:
        df_q2 = df_q.copy()
        df_q2["ped"] = pd.to_datetime(df_q2["period_end_date"])
        df_q2 = df_q2.sort_values(["symbol","ped"])
        max_gap_q = 0
        symbols_with_large_gap = []
        for sym, grp in df_q2.groupby("symbol"):
            peds = sorted(grp["ped"].tolist())
            for j in range(1, len(peds)):
                gap_days = (peds[j] - peds[j-1]).days
                gap_quarters = round(gap_days / 90)
                if gap_quarters > max_gap_q:
                    max_gap_q = gap_quarters
                if gap_quarters > 3:
                    symbols_with_large_gap.append(f"{sym}({peds[j-1].date()}->{peds[j].date()})")
        gate(13, "No symbol with quarterly gap > 3 consecutive quarters",
             len(symbols_with_large_gap) == 0,
             f"Max gap = {max_gap_q}Q | {len(symbols_with_large_gap)} symbols with gap>3Q" +
             (f" (sample: {symbols_with_large_gap[:3]})" if symbols_with_large_gap else ""))
    else:
        gate(13, "No symbol with quarterly gap > 3 consecutive quarters", False, "No quarterly rows")

    # [14] SHA256 consistency
    if os.path.exists(MANIFEST_PATH):
        with open(MANIFEST_PATH) as f:
            manifest = json.load(f)
        stored_sha = manifest.get("db_sha256","")
        if stored_sha:
            actual_sha = sha256_file(PIT_DB_PATH)
            gate(14, "DB SHA256 matches ingestion manifest",
                 actual_sha == stored_sha,
                 f"Manifest SHA={stored_sha[:16]}... | Actual={actual_sha[:16]}...")
        else:
            gate(14, "DB SHA256 matches ingestion manifest", False, "No SHA256 in manifest")
    else:
        gate(14, "DB SHA256 matches ingestion manifest", False, "ingestion_manifest.json not found")

    # ── Summary report ────────────────────────────────────────────────────────
    passed = sum(1 for r in results if r["status"]=="PASS")
    failed = sum(1 for r in results if r["status"]=="FAIL")
    verdict = "ALL_PASS — PEAD backtest may proceed" if all_pass else f"FAIL — PEAD BACKTEST BLOCKED ({failed} gates failed)"

    logger.info("")
    logger.info("="*70)
    logger.info(f"CERTIFICATION VERDICT: {verdict}")
    logger.info(f"Gates passed: {passed}/{len(results)}")
    logger.info("="*70)

    # ── Write markdown report ─────────────────────────────────────────────────
    report_lines = [
        "# PEAD_FUNDAMENTAL_V1 — PIT Dataset Certification Report",
        "",
        f"**Run timestamp:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}",
        f"**DB path:** `{PIT_DB_PATH}`",
        f"**Total rows:** {len(df_all)} | QUARTERLY: {q_count} | ANNUAL: {len(df_a)}",
        "",
        "## Gate Results",
        "",
        "| Gate | Name | Status | Detail |",
        "|------|------|--------|--------|",
    ]
    for r in results:
        icon = "✅" if r["status"]=="PASS" else "❌"
        report_lines.append(f"| {r['gate']} | {r['name']} | {icon} {r['status']} | {r['detail']} |")

    report_lines += [
        "",
        "## Coverage by Statement Type",
        "",
        "```",
    ]
    try:
        con = sqlite3.connect(PIT_DB_PATH)
        cov_df = pd.read_sql("""
            SELECT statement_type,
                COUNT(*) as total_rows, COUNT(DISTINCT symbol) as symbols,
                SUM(CASE WHEN eps IS NOT NULL THEN 1 ELSE 0 END) as has_eps,
                SUM(CASE WHEN revenue IS NOT NULL THEN 1 ELSE 0 END) as has_revenue,
                MIN(period_end_date) as earliest, MAX(period_end_date) as latest
            FROM pit_fundamentals_v1 GROUP BY statement_type
        """, con)
        con.close()
        report_lines.append(cov_df.to_string(index=False))
    except: report_lines.append("Coverage query failed")
    report_lines += ["```", ""]

    report_lines += [
        "## DATA PROVENANCE SECTION",
        "",
        f"- **Provider:** Screener.in (HTML scrape)",
        f"- **Timestamp basis:** `LODR_STATUTORY_DEADLINE_CONSERVATIVE`",
        f"- **actual_publication_timestamp:** Set to SEBI LODR statutory deadline (T+45d Q1-Q3, T+60d Q4/Annual) at 23:59:59 IST",
        f"- **Provenance limitation:** True board-meeting timestamps NOT recoverable from Screener",
        f"- **PIT safety:** Conservative (data appears at deadline, never before statutory requirement — safe direction for causality)",
        f"- **PEAD engine rule:** Must use `conservative_availability_timestamp` for pre-event gating",
        "",
        "## Certification Verdict",
        "",
        f"**{verdict}**",
        "",
        f"Gates passed: **{passed}/{len(results)}**",
        "",
    ]
    if not all_pass:
        report_lines.append("> [!CAUTION]")
        report_lines.append("> PEAD_FUNDAMENTAL_V1 backtest is BLOCKED until all certification gates pass.")
    else:
        report_lines.append("> [!IMPORTANT]")
        report_lines.append("> Before running the PEAD backtest: freeze the dataset (record SHA256 in run manifest) and do not modify the DB afterwards.")

    report_path = os.path.join(REPORT_DIR, "PIT_CERTIFICATION_REPORT.md")
    with open(report_path, "w") as f:
        f.write("\n".join(report_lines) + "\n")
    logger.info(f"Certification report written: {report_path}")

    # Write machine-readable verdict
    verdict_json = {
        "certification_run_utc": datetime.utcnow().isoformat(),
        "total_gates": len(results),
        "gates_passed": passed,
        "gates_failed": failed,
        "verdict": "ALL_PASS" if all_pass else "FAIL",
        "pead_backtest_blocked": not all_pass,
        "gates": results,
    }
    vpath = os.path.join(PIT_DIR, "pit_certification_verdict.json")
    with open(vpath, "w") as f: json.dump(verdict_json, f, indent=2)
    logger.info(f"Verdict JSON: {vpath}")

    sys.exit(0 if all_pass else 1)

if __name__ == "__main__":
    main()
