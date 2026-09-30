#!/usr/bin/env python3
"""
scripts/post_ingestion_freeze_and_report.py
===========================================
Runs automatically after PIT ingestion completes.
1. Waits for ingest PID to exit
2. Runs 14-gate certification
3. Freezes dataset (SHA256 manifest)
4. Writes MORNING_SUMMARY_REPORT.md
"""
import os, sys, time, json, sqlite3, hashlib, subprocess, logging
from datetime import datetime
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout),
                               logging.FileHandler("/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/post_ingestion_watcher.log","a")])
logger = logging.getLogger(__name__)

REPO   = "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM"
PIT_DB = f"{REPO}/data/pit_fundamentals_v1/pit_fundamentals_v1.db"
PIT_DIR= f"{REPO}/data/pit_fundamentals_v1"
REPORT_DIR = f"{REPO}/reports/pead_fundamental_v1"
os.makedirs(REPORT_DIR, exist_ok=True)

INGEST_PID = 58236

def sha256(path):
    h = hashlib.sha256()
    with open(path,"rb") as f:
        for chunk in iter(lambda: f.read(65536), b""): h.update(chunk)
    return h.hexdigest()

def pid_alive(pid):
    try: os.kill(pid, 0); return True
    except: return False

logger.info(f"Watcher started — monitoring PID {INGEST_PID}")

# Wait for ingestion to finish
while pid_alive(INGEST_PID):
    time.sleep(30)
    logger.info(f"  PID {INGEST_PID} still running...")

logger.info(f"PID {INGEST_PID} exited — ingestion complete.")
time.sleep(5)  # let file handles flush

# Run certification
logger.info("Running certification audit...")
result = subprocess.run(
    [f"{REPO}/.venv/bin/python3", f"{REPO}/scripts/certify_pit_quarterly_dataset.py"],
    cwd=REPO, capture_output=True, text=True
)
cert_passed = result.returncode == 0
logger.info(f"Certification exit code: {result.returncode}")
logger.info(result.stdout[-3000:] if result.stdout else "(no stdout)")

# Read certification verdict
verdict_path = f"{PIT_DIR}/pit_certification_verdict.json"
verdict = {}
if os.path.exists(verdict_path):
    with open(verdict_path) as f: verdict = json.load(f)

# Freeze SHA256
db_sha = sha256(PIT_DB) if os.path.exists(PIT_DB) else "UNAVAILABLE"

# Read manifest
manifest_path = f"{PIT_DIR}/ingestion_manifest.json"
manifest = {}
if os.path.exists(manifest_path):
    with open(manifest_path) as f: manifest = json.load(f)

# DB stats
con = sqlite3.connect(PIT_DB)
stats_df = pd.read_sql("""
    SELECT statement_type,
           COUNT(*) as rows,
           COUNT(DISTINCT symbol) as symbols,
           SUM(CASE WHEN eps IS NOT NULL THEN 1 ELSE 0 END) as has_eps,
           MIN(period_end_date) as earliest,
           MAX(period_end_date) as latest
    FROM pit_fundamentals_v1 GROUP BY statement_type
""", con)
total_rows  = pd.read_sql("SELECT COUNT(*) as n FROM pit_fundamentals_v1", con).iloc[0]["n"]
q_rows      = pd.read_sql("SELECT COUNT(*) as n FROM pit_fundamentals_v1 WHERE statement_type='QUARTERLY'", con).iloc[0]["n"]
q_syms      = pd.read_sql("SELECT COUNT(DISTINCT symbol) as n FROM pit_fundamentals_v1 WHERE statement_type='QUARTERLY'", con).iloc[0]["n"]
earliest_q  = pd.read_sql("SELECT MIN(period_end_date) as d FROM pit_fundamentals_v1 WHERE statement_type='QUARTERLY'", con).iloc[0]["d"]
failed_syms = manifest.get("failed_symbols", [])
con.close()

cert_verdict = verdict.get("verdict","UNKNOWN")
gates_passed = verdict.get("gates_passed", "?")
gates_total  = verdict.get("total_gates", 14)
pead_blocked = verdict.get("pead_backtest_blocked", True)

# Write frozen manifest
frozen = {
    "freeze_timestamp_ist": datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
    "freeze_timestamp_utc": datetime.utcnow().isoformat(),
    "db_sha256_frozen": db_sha,
    "total_rows": int(total_rows),
    "quarterly_rows": int(q_rows),
    "quarterly_symbols": int(q_syms),
    "earliest_quarterly_period": str(earliest_q),
    "certification_verdict": cert_verdict,
    "gates_passed": f"{gates_passed}/{gates_total}",
    "pead_backtest_blocked": pead_blocked,
    "failed_symbols_count": len(failed_syms),
    "failed_symbols": failed_syms,
    "timestamp_basis": "LODR_STATUTORY_DEADLINE_CONSERVATIVE",
    "next_step": "python3 scripts/run_pead_fundamental_v1_one_shot.py" if not pead_blocked else "BLOCKED — fix certification failures first",
}
frozen_path = f"{PIT_DIR}/FROZEN_DATASET_MANIFEST.json"
with open(frozen_path,"w") as f: json.dump(frozen, f, indent=2)
logger.info(f"Frozen manifest: {frozen_path}")

# Write morning report
icon = "✅" if cert_passed else "❌"
lines = [
    "# PEAD_FUNDAMENTAL_V1 — Morning Summary Report",
    "",
    f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}",
    f"**Ingestion PID:** {INGEST_PID} (completed)",
    "",
    "---",
    "",
    f"## 1. Ingestion Result",
    "",
    f"| Metric | Value |",
    f"|--------|-------|",
    f"| Total rows ingested | {int(total_rows):,} |",
    f"| QUARTERLY rows | {int(q_rows):,} |",
    f"| Symbols with QUARTERLY data | {int(q_syms)} |",
    f"| Earliest quarterly period | {earliest_q} |",
    f"| Failed symbols | {len(failed_syms)} |",
    f"| Timestamp basis | `LODR_STATUTORY_DEADLINE_CONSERVATIVE` |",
    "",
    "```",
    stats_df.to_string(index=False),
    "```",
    "",
    f"## 2. Certification Result",
    "",
    f"**Verdict: {icon} {cert_verdict}** | Gates: {gates_passed}/{gates_total}",
    "",
]

if verdict.get("gates"):
    lines += ["| Gate | Name | Status | Detail |", "|------|------|--------|--------|"]
    for g in verdict["gates"]:
        ico = "✅" if g["status"]=="PASS" else "❌"
        lines.append(f"| {g['gate']} | {g['name']} | {ico} {g['status']} | {g['detail']} |")
    lines.append("")

lines += [
    f"## 3. Dataset Freeze",
    "",
    f"| Item | Value |",
    f"|------|-------|",
    f"| DB SHA256 (frozen) | `{db_sha[:32]}...` |",
    f"| Frozen manifest | `data/pit_fundamentals_v1/FROZEN_DATASET_MANIFEST.json` |",
    "",
    "## 4. Next Step",
    "",
]

if not pead_blocked:
    lines += [
        "> [!IMPORTANT]",
        "> All certification gates passed. Dataset is frozen.",
        "> **Run:** `python3 scripts/run_pead_fundamental_v1_one_shot.py`",
        "> This will execute the complete PEAD_FUNDAMENTAL_V1 one-shot backtest.",
    ]
else:
    lines += [
        "> [!CAUTION]",
        f"> PEAD backtest is BLOCKED. Fix the {int(gates_total)-int(gates_passed)} failed certification gate(s) first.",
        "> Check `reports/pead_fundamental_v1/PIT_CERTIFICATION_REPORT.md` for details.",
    ]

if failed_syms:
    lines += ["", f"## 5. Failed Symbols ({len(failed_syms)})", "", "```", str(failed_syms), "```"]

report_path = f"{REPORT_DIR}/MORNING_SUMMARY_REPORT.md"
with open(report_path,"w") as f: f.write("\n".join(lines)+"\n")
logger.info(f"Morning report: {report_path}")
logger.info(f"=== DONE === Verdict: {cert_verdict} | PEAD blocked: {pead_blocked}")
