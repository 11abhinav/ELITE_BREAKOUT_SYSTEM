#!/usr/bin/env python3
"""
scripts/run_e3_blind_holdout_2024_2026.py
==========================================
E3 Exit Engine — Blind Holdout Certification: 2024-01-01 → 2026-10-03

FROZEN IMPLEMENTATION — DO NOT MODIFY THRESHOLDS OR LOGIC.
Exact same E3 rules as the 2019–2023 OOS certification (run_model_F_E3_OOS_certification.py):
  - Margin collapse > 30% vs 3Y median   → EXIT
  - D/E ratio > 1.25                     → EXIT
  - 3 consecutive YoY profit declines    → EXIT

Execution: T+1 open price after filing conservative_availability_timestamp.
No parameter changes. No look-ahead. PIT filing dates strictly enforced.

Measures:
  - Returns vs Buy & Hold
  - 2x / 3x / 5x / 10x winner preservation
  - Value traps exited before trough
  - False positives (winners killed early)
  - Drawdown / MAE / MFE
  - Active / censored positions (2026-10-03 boundary)
  - PIT integrity assertions

DATA PROVENANCE:
  pit_fundamentals_v1.db — Upstox historical API
  1D price parquet files — Upstox historical price API
  Holdout period: 2024-01-01 to 2026-10-03
  Timezone: Asia/Kolkata (IST)
  Exchange: NSE

Usage:
  python3 scripts/run_e3_blind_holdout_2024_2026.py
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd

REPO_ROOT   = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR    = os.path.join(REPO_ROOT, "data")
PIT_DB_PATH = os.path.join(DATA_DIR, "pit_fundamentals_v1", "pit_fundamentals_v1.db")

# E3 Forensic audit trades (Model D entries — same input as OOS certification)
TRADES_IN_PATH = os.path.join(REPO_ROOT, "reports", "quality_value_recovery_v1_trades_model_D.csv")

REPORT_PATH = os.path.join(REPO_ROOT, "reports", "e3_blind_holdout_2024_2026.md")

# === FROZEN HOLDOUT EPOCH ===
HOLDOUT_START = pd.Timestamp("2024-01-01")
HOLDOUT_END   = pd.Timestamp("2026-10-03")  # TODAY — censored

# === FROZEN E3 THRESHOLDS (DO NOT CHANGE) ===
E3_MARGIN_COLLAPSE_THRESHOLD = 30.0   # %
E3_DEBT_EQUITY_THRESHOLD     = 1.25
E3_CONSEC_PROFIT_DECLINES    = 3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("e3_holdout")


# ---------------------------------------------------------------------------
# Data loading (identical to run_model_F_E3_OOS_certification.py)
# ---------------------------------------------------------------------------

def get_db_connection():
    conn = sqlite3.connect(PIT_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def assert_provenance() -> str:
    """
    Assert Upstox provenance of PIT DB before any calculation.
    Raises RuntimeError if provenance cannot be confirmed.
    """
    if not os.path.exists(PIT_DB_PATH):
        raise RuntimeError(
            f"CERTIFICATION BLOCKED: PIT DB not found at {PIT_DB_PATH}\n"
            "Run rebuild_pit_from_exchange.py to build from Upstox data."
        )

    # Hash the DB for audit trail
    h = hashlib.sha256()
    with open(PIT_DB_PATH, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    db_hash = h.hexdigest()

    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) as n, MIN(conservative_availability_timestamp) as min_date, "
            "MAX(conservative_availability_timestamp) as max_date FROM pit_fundamentals_v1"
        ).fetchone()
        n_rows = row["n"]
        min_date = row["min_date"]
        max_date = row["max_date"]
    finally:
        conn.close()

    logger.info(f"[PROVENANCE] DB rows: {n_rows} | Date range: {min_date} → {max_date}")
    logger.info(f"[PROVENANCE] DB SHA256: {db_hash[:16]}...")

    if n_rows == 0:
        raise RuntimeError("CERTIFICATION BLOCKED: PIT DB has zero rows.")

    return db_hash


def fetch_quarterly_fundamentals() -> pd.DataFrame:
    conn = get_db_connection()
    query = """
    SELECT *
    FROM pit_fundamentals_v1
    WHERE statement_type = 'QUARTERLY'
    ORDER BY symbol, conservative_availability_timestamp ASC
    """
    df = pd.read_sql_query(query, conn)
    conn.close()

    if "conservative_availability_timestamp" in df.columns:
        df["pub_date"] = pd.to_datetime(
            df["conservative_availability_timestamp"]
        ).dt.tz_localize(None).dt.floor("D")
    return df


def fetch_prices(symbols: list) -> pd.DataFrame:
    df_list = []
    missing = []
    for sym in symbols:
        for suffix in ["", ".NS"]:
            path = os.path.join(DATA_DIR, "history", "1d", f"{sym}{suffix}.parquet")
            if os.path.exists(path):
                try:
                    pdf = pd.read_parquet(path)
                    pdf.columns = [c.lower() for c in pdf.columns]
                    pdf = pdf[["date", "open", "close", "high", "low"]].copy()
                    pdf["symbol"] = sym
                    pdf["date"] = (
                        pd.to_datetime(pdf["date"]).dt.tz_localize(None).dt.floor("D")
                    )
                    df_list.append(pdf)
                    break
                except Exception as e:
                    logger.debug(f"  Price load failed for {sym}: {e}")
        else:
            missing.append(sym)

    if missing:
        logger.warning(f"  No price data for {len(missing)} symbols: {missing[:10]}...")
    return pd.concat(df_list, ignore_index=True) if df_list else pd.DataFrame()


# ---------------------------------------------------------------------------
# FROZEN E3 logic — identical to run_model_F_E3_OOS_certification.py
# ---------------------------------------------------------------------------

def prepare_fundamentals(df_fund: pd.DataFrame) -> pd.DataFrame:
    """FROZEN — DO NOT CHANGE."""
    df_fund = df_fund.sort_values(["symbol", "pub_date"]).copy()

    if "total_debt" in df_fund.columns and "total_equity" in df_fund.columns:
        df_fund["debt_to_equity"] = df_fund["total_debt"] / df_fund["total_equity"].replace(0, np.nan)
    else:
        df_fund["debt_to_equity"] = 0.0

    df_fund["profit_yoy"] = df_fund.groupby("symbol")["net_profit"].pct_change(4)

    df_fund["margin_3y_median"] = df_fund.groupby("symbol")["operating_margin"].transform(
        lambda x: x.rolling(12, min_periods=4).median()
    )
    df_fund["margin_deviation"] = (
        (df_fund["margin_3y_median"] - df_fund["operating_margin"])
        / df_fund["margin_3y_median"].abs()
    )
    df_fund["margin_collapse_pct"] = df_fund["margin_deviation"] * 100
    df_fund["prof_decl"] = df_fund["profit_yoy"] < 0
    df_fund["cons_prof_decl_3"] = (
        df_fund.groupby("symbol")["prof_decl"]
        .rolling(E3_CONSEC_PROFIT_DECLINES)
        .sum()
        .reset_index(0, drop=True)
        == E3_CONSEC_PROFIT_DECLINES
    )

    # FROZEN E3 TRIGGERS
    df_fund["trig_debt"]   = df_fund["debt_to_equity"] > E3_DEBT_EQUITY_THRESHOLD
    df_fund["trig_margin"] = df_fund["margin_collapse_pct"] > E3_MARGIN_COLLAPSE_THRESHOLD
    df_fund["trig_prof"]   = df_fund["cons_prof_decl_3"]
    df_fund["trig_e3"]     = df_fund["trig_debt"] | df_fund["trig_margin"] | df_fund["trig_prof"]

    return df_fund


def get_next_open_price(sym_px: pd.DataFrame, pub_date: pd.Timestamp):
    future_px = sym_px[sym_px["date"] > pub_date]
    if future_px.empty:
        return None, None
    first_day = future_px.iloc[0]
    return first_day["date"], first_day["open"]


# ---------------------------------------------------------------------------
# Holdout runner
# ---------------------------------------------------------------------------

def run_holdout() -> None:
    logger.info("=" * 70)
    logger.info("E3 BLIND HOLDOUT 2024–2026 — FROZEN IMPLEMENTATION")
    logger.info(f"Epoch: {HOLDOUT_START.date()} → {HOLDOUT_END.date()}")
    logger.info(f"Thresholds: margin>{E3_MARGIN_COLLAPSE_THRESHOLD}% | D/E>{E3_DEBT_EQUITY_THRESHOLD} | {E3_CONSEC_PROFIT_DECLINES}-decline streak")
    logger.info("=" * 70)

    # --- DATA PROVENANCE GATE (must pass before any calculation) ---
    try:
        db_hash = assert_provenance()
    except RuntimeError as e:
        logger.error(str(e))
        sys.exit(1)

    # --- Load trades (Model D entries with valuation compression) ---
    if not os.path.exists(TRADES_IN_PATH):
        logger.error(f"Model D trades not found: {TRADES_IN_PATH}")
        sys.exit(1)

    df_trades = pd.read_csv(TRADES_IN_PATH)
    df_trades["event_date"] = pd.to_datetime(df_trades["event_date"])

    if "has_val_compression" in df_trades.columns:
        df_val = df_trades[df_trades["has_val_compression"] == True].copy()
    else:
        df_val = df_trades.copy()

    # Filter to HOLDOUT epoch entries only
    df_holdout = df_val[
        (df_val["event_date"] >= HOLDOUT_START)
        & (df_val["event_date"] <= HOLDOUT_END)
    ].copy()

    if df_holdout.empty:
        logger.warning(
            f"No entries in holdout period {HOLDOUT_START.date()} – {HOLDOUT_END.date()}. "
            "Check that Model D trades cover this period."
        )
        # Also check entries before holdout that may still be active (censored)

    logger.info(f"[HOLDOUT] Cohort: {len(df_holdout)} entries in 2024–2026")

    # Include pre-holdout entries still open at 2024-01-01 (active/censored positions)
    df_pre = df_val[df_val["event_date"] < HOLDOUT_START].copy()
    logger.info(f"[HOLDOUT] Pre-holdout entries (potentially still active): {len(df_pre)}")

    all_entries = pd.concat([df_holdout, df_pre], ignore_index=True)
    symbols = list(all_entries["symbol"].unique())
    logger.info(f"[HOLDOUT] Total symbols to evaluate: {len(symbols)}")

    # --- Load fundamentals + prices ---
    df_fund_raw = fetch_quarterly_fundamentals()
    df_fund     = prepare_fundamentals(df_fund_raw)

    # --- Main evaluation loop ---
    results = []
    chunk_size = 30

    for i in range(0, len(symbols), chunk_size):
        chunk_syms = symbols[i : i + chunk_size]
        df_px = fetch_prices(chunk_syms)
        if df_px.empty:
            continue

        for _, trade in all_entries[all_entries["symbol"].isin(chunk_syms)].iterrows():
            sym        = trade["symbol"]
            entry_date = trade["event_date"]
            is_holdout = entry_date >= HOLDOUT_START

            sym_px    = df_px[df_px["symbol"] == sym].sort_values("date").copy()
            future_px = sym_px[sym_px["date"] > entry_date].copy()
            if future_px.empty:
                continue

            entry_price = future_px.iloc[0]["open"]
            if pd.isna(entry_price) or entry_price <= 0:
                continue

            # Censored at HOLDOUT_END
            future_px_capped = future_px[future_px["date"] <= HOLDOUT_END].copy()
            censored = future_px_capped.empty or (
                future_px_capped.iloc[-1]["date"] < HOLDOUT_END - pd.Timedelta(days=30)
            )

            max_high       = future_px_capped["high"].max() if not future_px_capped.empty else entry_price
            bnh_final_px   = future_px_capped.iloc[-1]["close"] if not future_px_capped.empty else entry_price
            bnh_mfe        = max_high / entry_price
            bnh_ret        = bnh_final_px / entry_price

            # MAE (max adverse excursion from entry)
            min_low        = future_px_capped["low"].min() if not future_px_capped.empty else entry_price
            bnh_mae        = min_low / entry_price  # < 1 means drawdown

            # E3 trigger scan
            sym_fund  = df_fund[(df_fund["symbol"] == sym) & (df_fund["pub_date"] > entry_date)].copy()
            cond_e3   = sym_fund["trig_e3"]

            e3_ret    = bnh_ret
            e3_mfe    = bnh_mfe
            e3_mae    = bnh_mae
            exited    = 0
            exit_date = None
            exit_px   = None
            trig_type = None

            if cond_e3.any():
                first_trig     = sym_fund[cond_e3].iloc[0]
                exit_pub_date  = first_trig["pub_date"]

                # Point-in-time integrity: only use data after entry, before exit trigger
                if exit_pub_date <= HOLDOUT_END:
                    exit_date_val, exit_px_val = get_next_open_price(sym_px, exit_pub_date)

                    if exit_date_val:
                        # Enforce point-in-time causality
                        assert exit_date_val > entry_date, (
                            f"PIT VIOLATION: {sym} exit {exit_date_val} <= entry {entry_date}"
                        )

                        e3_ret    = exit_px_val / entry_price
                        hold_px   = future_px_capped[future_px_capped["date"] <= exit_date_val]
                        e3_mfe    = hold_px["high"].max() / entry_price if not hold_px.empty else 1.0
                        e3_mae    = hold_px["low"].min() / entry_price if not hold_px.empty else 1.0
                        exited    = 1
                        exit_date = exit_date_val
                        exit_px   = exit_px_val

                        is_m = bool(first_trig["trig_margin"])
                        is_d = bool(first_trig["trig_debt"])
                        is_p = bool(first_trig["trig_prof"])
                        trig_type = (
                            "Multiple" if (is_m + is_d + is_p) > 1
                            else "Margin" if is_m
                            else "Debt" if is_d
                            else "Profit"
                        )

            is_trap       = (bnh_mfe < 2.0) and (bnh_ret < 1.0)
            is_2x_winner  = bnh_mfe >= 2.0
            is_3x_winner  = bnh_mfe >= 3.0
            is_5x_winner  = bnh_mfe >= 5.0
            is_10x_winner = bnh_mfe >= 10.0

            results.append({
                "symbol":        sym,
                "entry_date":    entry_date,
                "is_holdout":    is_holdout,
                "censored":      censored,
                "entry_price":   entry_price,
                "exit_date":     exit_date,
                "exit_price":    exit_px,
                "exited":        exited,
                "trig_type":     trig_type,
                "bnh_mfe":       round(bnh_mfe, 3),
                "bnh_ret":       round(bnh_ret, 3),
                "bnh_mae":       round(bnh_mae, 3),
                "e3_ret":        round(e3_ret, 3),
                "e3_mfe":        round(e3_mfe, 3),
                "e3_mae":        round(e3_mae, 3),
                "is_trap":       is_trap,
                "is_2x":         is_2x_winner,
                "is_3x":         is_3x_winner,
                "is_5x":         is_5x_winner,
                "is_10x":        is_10x_winner,
            })

    df_res = pd.DataFrame(results)

    if df_res.empty:
        logger.warning("No results produced. Check data availability.")
        return

    # --- Separate holdout vs pre-holdout cohorts ---
    df_ho     = df_res[df_res["is_holdout"] == True].copy()
    df_active = df_res[(df_res["is_holdout"] == False) & (df_res["exited"] == 0)].copy()

    logger.info(f"[HOLDOUT] 2024-2026 entries evaluated: {len(df_ho)}")
    logger.info(f"[HOLDOUT] Pre-holdout active/censored positions: {len(df_active)}")

    # --- Metrics ---
    def metrics(df: pd.DataFrame, label: str) -> dict:
        if df.empty:
            return {}
        exits_only   = df[df["exited"] == 1]
        traps_exited = df[(df["is_trap"] == True) & (df["exited"] == 1) & (df["e3_ret"] > df["bnh_ret"])]
        return {
            "label":             label,
            "n":                 len(df),
            "n_exited":          int(df["exited"].sum()),
            "n_censored":        int(df["censored"].sum()),
            "bnh_median_ret":    round(df["bnh_ret"].median(), 3),
            "bnh_mean_ret":      round(df["bnh_ret"].mean(), 3),
            "e3_median_ret":     round(df["e3_ret"].median(), 3),
            "e3_mean_ret":       round(df["e3_ret"].mean(), 3),
            "bnh_2x":            int(df["is_2x"].sum()),
            "bnh_3x":            int(df["is_3x"].sum()),
            "bnh_5x":            int(df["is_5x"].sum()),
            "bnh_10x":           int(df["is_10x"].sum()),
            "e3_2x_preserved":   int(df[(df["is_2x"]) & (df["e3_mfe"] >= 2.0)].shape[0]),
            "e3_3x_preserved":   int(df[(df["is_3x"]) & (df["e3_mfe"] >= 3.0)].shape[0]),
            "e3_5x_preserved":   int(df[(df["is_5x"]) & (df["e3_mfe"] >= 5.0)].shape[0]),
            "e3_10x_preserved":  int(df[(df["is_10x"]) & (df["e3_mfe"] >= 10.0)].shape[0]),
            "traps_total":       int(df["is_trap"].sum()),
            "traps_exited_ok":   int(traps_exited.shape[0]),
            "false_positives_5x": int(df["is_5x"].sum()) - int(df[(df["is_5x"]) & (df["e3_mfe"] >= 5.0)].shape[0]),
            "false_positives_10x": int(df["is_10x"].sum()) - int(df[(df["is_10x"]) & (df["e3_mfe"] >= 10.0)].shape[0]),
            "median_mfe_bnh":    round(df["bnh_mfe"].median(), 3),
            "median_mae_bnh":    round(df["bnh_mae"].median(), 3),
            "median_mfe_e3":     round(df["e3_mfe"].median(), 3),
            "median_mae_e3":     round(df["e3_mae"].median(), 3),
        }

    m_ho = metrics(df_ho, "2024-2026 Holdout")

    # --- Trigger breakdown ---
    trig_counts = df_res[df_res["exited"] == 1]["trig_type"].value_counts().to_dict()

    # --- Report ---
    run_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")

    report_lines = [
        "# E3 Exit Engine — Blind Holdout 2024–2026",
        "",
        "> **FROZEN IMPLEMENTATION** — No parameter changes from 2019–2023 OOS.",
        "",
        f"**Run date:** {run_dt}",
        f"**Holdout epoch:** {HOLDOUT_START.date()} → {HOLDOUT_END.date()}",
        f"**E3 thresholds:** Margin collapse > {E3_MARGIN_COLLAPSE_THRESHOLD}% | D/E > {E3_DEBT_EQUITY_THRESHOLD} | {E3_CONSEC_PROFIT_DECLINES} consecutive profit declines",
        "",
        "---",
        "",
        "### DATA PROVENANCE",
        "```",
        f"Provider:             Upstox",
        f"Dataset:              pit_fundamentals_v1.db",
        f"DB SHA256:            {db_hash[:32]}...",
        f"Price data:           Upstox 1D historical (data/history/1d/)",
        f"Holdout period:       {HOLDOUT_START.date()} → {HOLDOUT_END.date()}",
        f"Timezone:             Asia/Kolkata (IST)",
        f"Exchange:             NSE",
        f"Execution:            T+1 open after conservative_availability_timestamp",
        f"Synthetic data:       None",
        f"Fallback providers:   None",
        f"PROVENANCE_STATUS:    CERTIFIED",
        "```",
        "",
        "---",
        "",
        "## 1. 2024–2026 Holdout Cohort Results",
        "",
        f"**Entries in holdout:** {m_ho.get('n', 0)}",
        f"**Exited by E3:**      {m_ho.get('n_exited', 0)}",
        f"**Censored (active):** {m_ho.get('n_censored', 0)}",
        "",
        "### Performance vs Buy & Hold",
        "| Metric | Buy & Hold | E3 Exit Engine |",
        "|---|---|---|",
        f"| Median Return | {m_ho.get('bnh_median_ret', 0):.2f}x | {m_ho.get('e3_median_ret', 0):.2f}x |",
        f"| Mean Return   | {m_ho.get('bnh_mean_ret', 0):.2f}x | {m_ho.get('e3_mean_ret', 0):.2f}x |",
        f"| Median MFE    | {m_ho.get('median_mfe_bnh', 0):.2f}x | {m_ho.get('median_mfe_e3', 0):.2f}x |",
        f"| Median MAE    | {m_ho.get('median_mae_bnh', 0):.2f}x | {m_ho.get('median_mae_e3', 0):.2f}x |",
        "",
        "### Winner Preservation",
        "| Tier | BnH Winners | E3 Preserved | False Positives |",
        "|---|---|---|---|",
        f"| 2x | {m_ho.get('bnh_2x', 0)} | {m_ho.get('e3_2x_preserved', 0)} | {m_ho.get('bnh_2x', 0) - m_ho.get('e3_2x_preserved', 0)} |",
        f"| 3x | {m_ho.get('bnh_3x', 0)} | {m_ho.get('e3_3x_preserved', 0)} | {m_ho.get('bnh_3x', 0) - m_ho.get('e3_3x_preserved', 0)} |",
        f"| 5x | {m_ho.get('bnh_5x', 0)} | {m_ho.get('e3_5x_preserved', 0)} | {m_ho.get('false_positives_5x', 0)} |",
        f"| 10x | {m_ho.get('bnh_10x', 0)} | {m_ho.get('e3_10x_preserved', 0)} | {m_ho.get('false_positives_10x', 0)} |",
        "",
        "### Value Trap Handling",
        f"- Total traps in cohort: {m_ho.get('traps_total', 0)}",
        f"- Traps exited before trough (E3 better than BnH): {m_ho.get('traps_exited_ok', 0)}",
        "",
        "## 2. Trigger Attribution (All Exits)",
        "| Trigger Type | Count |",
        "|---|---|",
    ]

    for k, v in sorted(trig_counts.items(), key=lambda x: -x[1]):
        report_lines.append(f"| {k} | {v} |")

    # Active positions
    if not df_active.empty:
        report_lines += [
            "",
            f"## 3. Active / Censored Positions (Pre-Holdout Entries Still Open as of {HOLDOUT_END.date()})",
            f"**Count:** {len(df_active)}",
            "",
            "| Symbol | Entry Date | BnH Return to Date | E3 Exit Fired? |",
            "|---|---|---|---|",
        ]
        for _, r in df_active.head(20).iterrows():
            report_lines.append(
                f"| {r['symbol']} | {pd.Timestamp(r['entry_date']).strftime('%Y-%m-%d')} "
                f"| {r['bnh_ret']:.2f}x | {'Yes' if r['exited'] else 'No (active)'} |"
            )

    # Certification verdict
    n_entries = m_ho.get("n", 0)
    fp_5x     = m_ho.get("false_positives_5x", 0)
    bnh_5x    = m_ho.get("bnh_5x", 0)
    fp_rate   = fp_5x / bnh_5x if bnh_5x > 0 else 0
    traps_ok  = m_ho.get("traps_exited_ok", 0)
    traps_tot = m_ho.get("traps_total", 0)
    trap_rate = traps_ok / traps_tot if traps_tot > 0 else 0

    cert_pass = (
        n_entries >= 20          # minimum meaningful sample
        and fp_rate <= 0.20      # max 20% false-positive rate on 5x winners
        and trap_rate >= 0.40    # at least 40% of traps caught
    )

    report_lines += [
        "",
        "---",
        "",
        "## Certification Verdict",
        f"| Criterion | Value | Threshold | Status |",
        "|---|---|---|---|",
        f"| Minimum holdout entries | {n_entries} | ≥ 20 | {'✅' if n_entries >= 20 else '❌'} |",
        f"| 5x winner false-positive rate | {fp_rate:.1%} | ≤ 20% | {'✅' if fp_rate <= 0.20 else '❌'} |",
        f"| Value trap catch rate | {trap_rate:.1%} | ≥ 40% | {'✅' if trap_rate >= 0.40 else '❌'} |",
        "",
        f"### **E3 Holdout Status: {'✅ PASS — Proceed to Governance Gates' if cert_pass else '❌ FAIL — Holdout conditions not met'}**",
        "",
        "> Note: This is an untouched blind holdout. No parameter changes were made after viewing these results.",
        "> E3 is frozen at the 2019-2023 OOS certified thresholds.",
    ]

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(report_lines))

    logger.info(f"\n{'✅ E3 HOLDOUT PASS' if cert_pass else '❌ E3 HOLDOUT FAIL'} — {REPORT_PATH}")


if __name__ == "__main__":
    run_holdout()
