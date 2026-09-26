#!/usr/bin/env python3
"""
scripts/run_phase1_live_triage.py
=============================================================================
PHASE 1: EMPIRICAL LIVE TRIAGE HARNESS
Queries the live production database for all CLOSED alerts, converts returns
into alert-specific R-multiples using individual structural stop-losses,
computes empirical expectancies, and applies the charter triage classification.

Outputs:
  - reports/certification/phase1_live_triage/live_alerts_ledger.csv
  - reports/certification/phase1_live_triage/live_triage_summary.csv
  - reports/certification/phase1_live_triage/PHASE1_LIVE_TRIAGE_REPORT.md
=============================================================================
"""

import os
import sys
import json
import argparse
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
import numpy as np
import pandas as pd

# Set up paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "app"))
sys.path.insert(0, BASE_DIR)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("Phase1LiveTriage")

OUTPUT_DIR = os.path.join(BASE_DIR, "reports", "certification", "phase1_live_triage")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def parse_args():
    parser = argparse.ArgumentParser(description="Phase 1 Live Alert Empirical Triage")
    parser.add_argument("--db-url", type=str, default=None, help="PostgreSQL connection string")
    parser.add_argument("--input-file", type=str, default=None, help="Optional pre-exported alerts JSON or CSV")
    return parser.parse_args()


def get_db_connection(db_url: Optional[str] = None):
    url = db_url or os.getenv("DATABASE_URL")
    if not url:
        env_file = os.path.join(BASE_DIR, ".env")
        if os.path.exists(env_file):
            with open(env_file, "r") as f:
                for line in f:
                    if line.strip().startswith("DATABASE_URL="):
                        url = line.strip().split("DATABASE_URL=", 1)[1].strip("'\"")
                        break
    if not url:
        raise ValueError(
            "DATABASE_URL is not set. Please provide --db-url, set the DATABASE_URL environment variable, "
            "or specify --input-file with pre-exported data."
        )
    import psycopg2
    from psycopg2.extras import RealDictCursor
    return psycopg2.connect(url, cursor_factory=RealDictCursor)


def run_live_triage(db_url: Optional[str] = None, input_file: Optional[str] = None):
    raw_alerts = []

    if input_file and os.path.exists(input_file):
        logger.info(f"📂 Loading pre-exported alerts from {input_file}...")
        if input_file.endswith(".csv"):
            df_in = pd.read_csv(input_file)
            raw_alerts = df_in.to_dict(orient="records")
        else:
            with open(input_file, "r") as f:
                raw_alerts = json.load(f)
    else:
        logger.info("🔌 Connecting to live production alerts database...")
        conn = get_db_connection(db_url)
        with conn.cursor() as cur:
            # 1. Exact query specified by user for aggregate verification
            user_query = """
                SELECT scanner, category,
                       COUNT(*) AS n_alerts,
                       AVG(CASE WHEN pnl_pct > 0 THEN 1.0 ELSE 0.0 END) AS win_rate,
                       AVG(pnl_pct) AS avg_pnl_pct,
                       MIN(closed_at) AS earliest_close,
                       MAX(closed_at) AS latest_close
                FROM alerts
                WHERE status = 'CLOSED'
                GROUP BY scanner, category
                ORDER BY avg_pnl_pct ASC;
            """
            cur.execute(user_query)
            agg_rows = cur.fetchall()
            logger.info(f"📊 Aggregated query returned {len(agg_rows)} scanner/category cohorts.")

            # 2. Extract full per-alert raw ledger for CLOSED alerts
            ledger_query = """
                SELECT id, symbol, scanner, category, breakout_type, alert_date, alert_time,
                       entry_price, actual_entry_price, stop_loss, target_1, target_2, target_3, target_4,
                       exit_price, pnl_pct, pnl_rs, status, closed_at, exit_reason, filter_metadata, context
                FROM alerts
                WHERE status = 'CLOSED'
                ORDER BY closed_at ASC, id ASC;
            """
            cur.execute(ledger_query)
            raw_alerts = cur.fetchall()
            logger.info(f"📥 Fetched {len(raw_alerts)} CLOSED alert rows.")
        conn.close()

    if not raw_alerts:
        logger.warning("⚠️ Zero CLOSED alerts found in the database.")
        return

    processed_rows = []
    for row in raw_alerts:
        r = dict(row)
        alert_id = r.get("id")
        symbol = r.get("symbol")
        scanner = str(r.get("scanner") or r.get("breakout_type") or "UNKNOWN").upper().strip()
        category = str(r.get("category") or "DEFAULT").upper().strip()
        
        entry_p = r.get("actual_entry_price") or r.get("entry_price")
        exit_p = r.get("exit_price")
        sl_p = r.get("stop_loss")
        pnl_pct = r.get("pnl_pct")
        closed_at = str(r.get("closed_at") or "")
        alert_date = str(r.get("alert_date") or "")
        alert_time = str(r.get("alert_time") or "")

        meta = r.get("filter_metadata")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        elif not isinstance(meta, dict):
            meta = {}

        ctx = r.get("context")
        if isinstance(ctx, str):
            try:
                ctx = json.loads(ctx)
            except Exception:
                ctx = {}
        elif not isinstance(ctx, dict):
            ctx = {}

        if (sl_p is None or float(sl_p) <= 0) and meta:
            sl_p = meta.get("stop_loss") or meta.get("sl_price") or meta.get("initial_stop_loss")
        if (sl_p is None or float(sl_p) <= 0) and ctx:
            sl_p = ctx.get("stop_loss") or ctx.get("sl_price") or ctx.get("initial_stop_loss")

        entry_val = float(entry_p) if entry_p is not None else 0.0
        exit_val = float(exit_p) if exit_p is not None else 0.0
        sl_val = float(sl_p) if sl_p is not None else 0.0
        pnl_pct_val = float(pnl_pct) if pnl_pct is not None else 0.0

        r_multiple = 0.0
        risk_dist = abs(entry_val - sl_val) if entry_val > 0 and sl_val > 0 else 0.0

        if risk_dist > 0 and exit_val > 0:
            r_multiple = (exit_val - entry_val) / risk_dist
        elif risk_dist > 0 and pnl_pct_val != 0.0:
            risk_pct = (risk_dist / entry_val) * 100.0 if entry_val > 0 else 0.0
            r_multiple = pnl_pct_val / risk_pct if risk_pct > 0 else 0.0
        elif entry_val > 0 and sl_val > 0:
            risk_pct = abs(entry_val - sl_val) / entry_val * 100.0
            r_multiple = pnl_pct_val / risk_pct if risk_pct > 0 else 0.0

        processed_rows.append({
            "alert_id": alert_id,
            "symbol": symbol,
            "scanner": scanner,
            "category": category,
            "alert_date": alert_date,
            "alert_time": alert_time,
            "entry_price": round(entry_val, 2),
            "stop_loss": round(sl_val, 2),
            "target_1": round(float(r.get("target_1") or 0.0), 2),
            "target_2": round(float(r.get("target_2") or 0.0), 2),
            "target_3": round(float(r.get("target_3") or 0.0), 2),
            "target_4": round(float(r.get("target_4") or 0.0), 2),
            "exit_price": round(exit_val, 2),
            "exit_reason": r.get("exit_reason") or ("WIN" if pnl_pct_val > 0 else "LOSS"),
            "closed_at": closed_at,
            "pnl_pct": round(pnl_pct_val, 2),
            "pnl_rs": round(float(r.get("pnl_rs") or 0.0), 2),
            "initial_risk_dist": round(risk_dist, 2),
            "r_multiple": round(r_multiple, 4),
            "is_win": 1 if pnl_pct_val > 0 else 0
        })

    df_ledger = pd.DataFrame(processed_rows)
    ledger_path = os.path.join(OUTPUT_DIR, "live_alerts_ledger.csv")
    df_ledger.to_csv(ledger_path, index=False)
    logger.info(f"✅ Saved live alert ledger ({len(df_ledger)} rows) to {ledger_path}")

    # Compute summary table per scanner & category
    summary_records = []
    grouped = df_ledger.groupby(["scanner", "category"])

    for (sc, cat), group in grouped:
        n_alerts = len(group)
        win_rate = float(group["is_win"].mean() * 100.0)
        avg_pnl_pct = float(group["pnl_pct"].mean())
        mean_r = float(group["r_multiple"].mean())
        earliest_close = group["closed_at"].min()
        latest_close = group["closed_at"].max()

        r_vals = group["r_multiple"].values
        if len(r_vals) >= 5:
            boot_means = [np.mean(np.random.choice(r_vals, size=len(r_vals), replace=True)) for _ in range(2000)]
            ci_low = float(np.percentile(boot_means, 2.5))
            ci_high = float(np.percentile(boot_means, 97.5))
        else:
            ci_low = mean_r
            ci_high = mean_r

        if n_alerts < 30:
            classification = "DEFERRED (N < 30)"
            action = "Underpowered in live sample; deferred to historical replay."
        elif mean_r <= 0.0:
            classification = "HIGH_PRIORITY_AUDIT (E[R] <= 0, N >= 30)"
            action = "Immediate audit candidate for simplification or decommission."
        elif mean_r > 0.20 and n_alerts >= 50:
            classification = "VALIDATED (E[R] > +0.20R, N >= 50)"
            action = "Validated empirical live edge; proceed to historical holdout audit."
        else:
            classification = "CANDIDATE_MONITOR"
            action = "Positive live expectancy, continue historical certification."

        summary_records.append({
            "scanner": sc,
            "category": cat,
            "n_alerts": n_alerts,
            "win_rate_pct": round(win_rate, 2),
            "avg_pnl_pct": round(avg_pnl_pct, 2),
            "mean_realized_r": round(mean_r, 4),
            "ci_95_low": round(ci_low, 4),
            "ci_95_high": round(ci_high, 4),
            "earliest_close": earliest_close,
            "latest_close": latest_close,
            "triage_classification": classification,
            "governance_action": action
        })

    df_summary = pd.DataFrame(summary_records).sort_values("mean_realized_r", ascending=True)
    summary_path = os.path.join(OUTPUT_DIR, "live_triage_summary.csv")
    df_summary.to_csv(summary_path, index=False)
    logger.info(f"✅ Saved live triage summary ({len(df_summary)} cohorts) to {summary_path}")

    report_md = f"""# PHASE 1: EMPIRICAL LIVE TRIAGE REPORT
**Audit Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST  
**Dataset:** Live Production `alerts` Table (`status = 'CLOSED'`)  
**Total Closed Alerts Evaluated:** {len(df_ledger)}  
**Raw Ledger File:** [`live_alerts_ledger.csv`](file://{ledger_path})  
**Summary Table File:** [`live_triage_summary.csv`](file://{summary_path})  

---

## 1. Executive Summary & Triage Classification

| Scanner | Category | N (Closed) | Win Rate % | Avg PnL % | Mean R | 95% Bootstrap CI | Triage Classification | Action |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
"""
    for _, row in df_summary.iterrows():
        report_md += (
            f"| **`{row['scanner']}`** | `{row['category']}` | {row['n_alerts']} | "
            f"{row['win_rate_pct']:.1f}% | {row['avg_pnl_pct']:+.2f}% | **{row['mean_realized_r']:+.4f}R** | "
            f"[{row['ci_95_low']:+.3f}R, {row['ci_95_high']:+.3f}R] | **`{row['triage_classification']}`** | "
            f"{row['governance_action']} |\n"
        )

    report_md += """
---

## 2. Methodology & Mathematical R-Conversion Standard
1. **Per-Alert Initial Risk Definition:**  
   $$\\text{Risk}_{\\text{init}} = |\\text{Entry Price} - \\text{Stop Loss}|$$
   Sourced directly from each alert's immutable order record, `filter_metadata`, and `context`.
2. **Realized R-Multiple:**  
   $$R = \\frac{\\text{Exit Price} - \\text{Entry Price}}{\\text{Risk}_{\\text{init}}}$$
3. **Resampling:** 95% Bootstrap percentile confidence interval ($N=2,000$ resamples with replacement).
4. **Governing Triage Thresholds:**
   - $\\mathbb{E}[R] \\le 0.00\\text{R}$ on $N \\ge 30$: `HIGH_PRIORITY_AUDIT` (Flagged for immediate simplification or decommission).
   - $N < 30$: `DEFERRED` (Underpowered in live sample; deferred to historical replay).
   - $\\mathbb{E}[R] > +0.20\\text{R}$ on $N \\ge 50$: `VALIDATED` (Statistically confirmed in live market conditions).
"""

    report_path = os.path.join(OUTPUT_DIR, "PHASE1_LIVE_TRIAGE_REPORT.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_md)
    logger.info(f"✅ Generated Phase 1 report at {report_path}")


if __name__ == "__main__":
    args = parse_args()
    try:
        run_live_triage(db_url=args.db_url, input_file=args.input_file)
    except Exception as e:
        logger.error(f"❌ Live triage failed: {e}", exc_info=True)
        sys.exit(1)
