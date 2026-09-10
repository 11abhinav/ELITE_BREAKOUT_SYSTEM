#!/usr/bin/env python3
# =============================================================================
# tests/analyze_5_scanners_synthesis.py
# CROSS-SCANNER CONFLUENCE, REGIME INTERACTION, & SCORE CALIBRATION ANALYSIS
# =============================================================================
#
# RULE 67 CHANGE-RATIONALE:
# Fulfills Section 17 & Section 18 of the Master Directive:
# 1. Multi-scanner confluence: Analyzes overlap, co-alert frequencies, and confluence edge.
# 2. Score calibration: Tests whether quality score buckets correlate monotonically with E[R].
# 3. Macro regime stability: Evaluates performance across BULL, NEUTRAL, and BEAR regimes.
# 4. Failure analysis: Examines why losing alerts failed across scanners.
# =============================================================================

from __future__ import annotations

import os
import sys
import pandas as pd
import numpy as np

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_REPORTS_DIR = os.path.join(_REPO_ROOT, "reports")

def main():
    print("=" * 100)
    print("CROSS-SCANNER SYNTHESIS: 5-SCANNER CONFLUENCE, REGIME & SCORE CALIBRATION")
    print("=" * 100)

    # 1. Load outcome CSVs
    files = {
        "EOD": os.path.join(_REPORTS_DIR, "eod_breakout_outcomes.csv"),
        "PULLBACK": os.path.join(_REPORTS_DIR, "pullback_outcomes.csv"),
        "VCP": os.path.join(_REPORTS_DIR, "vcp_outcomes.csv"),
        "MULTITF": os.path.join(_REPORTS_DIR, "multitf_v3_outcomes.csv" if os.path.exists(os.path.join(_REPORTS_DIR, "multitf_v3_outcomes.csv")) else "multitf_outcomes.csv"),
        "REVERSAL": os.path.join(_REPORTS_DIR, "reversal_v1_vs_v2_outcomes.csv"),
    }

    dfs = {}
    for name, path in files.items():
        if os.path.exists(path):
            df = pd.read_csv(path)
            dfs[name] = df
            print(f"  Loaded {name:<10}: {len(df):>5} rows from {os.path.basename(path)}")
        else:
            print(f"  WARNING: Missing {path}")

    # 2. Cross-Scanner Overlap & Confluence Analysis
    print("\n" + "-" * 80)
    print("1. MULTI-SCANNER CO-ALERT CONFLUENCE ANALYSIS (Same Symbol x Same Scan Date)")
    print("-" * 80)
    champ_map = {
        "EOD": "EOD_CHALL_G_SWEET_SPOT",
        "PULLBACK": "PULLBACK_CHALL_C_2ATR_SHELF",
        "VCP": "VCP_CHALL_G_RUNNER_EXPANSION",
        "MULTITF": "MULTITF_V3D_BULL_ONLY",
        "REVERSAL": "REVERSAL_V22C_WIDER_SL",
    }

    for partition_filter in [None, "OOS", "IS"]:
        lbl = "FULL YEAR (IS + OOS)" if partition_filter is None else f"{partition_filter} ONLY"
        print(f"\n  --- Co-Alert Overlap Matrix [{lbl}] ---")
        alert_sets = {}
        for sc, df in dfs.items():
            c_id = champ_map.get(sc, "")
            sub = df[df["variant_id"] == c_id] if "variant_id" in df.columns else df
            if partition_filter and "partition" in sub.columns:
                sub = sub[sub["partition"] == partition_filter]
            alert_sets[sc] = set(zip(sub["symbol"], sub["scan_date"]))
            print(f"    {sc:<10} Champion Unique Alerts: {len(alert_sets[sc])}")

        scanners = [s for s in dfs.keys() if len(alert_sets[s]) > 0]
        for i, s1 in enumerate(scanners):
            for s2 in scanners[i+1:]:
                overlap = alert_sets[s1].intersection(alert_sets[s2])
                pct1 = len(overlap) / len(alert_sets[s1]) * 100 if alert_sets[s1] else 0
                pct2 = len(overlap) / len(alert_sets[s2]) * 100 if alert_sets[s2] else 0
                print(f"    {s1} & {s2}: {len(overlap)} shared alerts ({pct1:.1f}% of {s1} [N={len(alert_sets[s1])}], {pct2:.1f}% of {s2} [N={len(alert_sets[s2])}])")

    # 3. Macro Regime Interaction
    print("\n" + "-" * 80)
    print("2. MACRO REGIME STABILITY BY SCANNER (OOS Partition)")
    print("-" * 80)
    print(f"  {'Scanner':<12} {'Regime':<10} {'N':>5} {'E[R]':>8} {'PF':>6} {'Win%':>7} {'+2R%':>6}")
    print("  " + "-" * 50)
    for sc, df in dfs.items():
        c_id = champ_map.get(sc, "")
        sub = df[(df["variant_id"] == c_id) & (df["partition"] == "OOS")] if "variant_id" in df.columns else df
        if sub.empty:
            continue
        for reg in ["BULL", "NEUTRAL", "BEAR"]:
            reg_df = sub[sub["regime"] == reg]
            if reg_df.empty:
                continue
            n = len(reg_df)
            er = reg_df["realized_rr"].mean()
            wins = reg_df[reg_df["realized_rr"] > 0]
            losses = reg_df[reg_df["realized_rr"] < 0]
            win_pct = len(wins) / n * 100 if n else 0
            tw = wins["realized_rr"].sum()
            tl = abs(losses["realized_rr"].sum())
            pf = (tw / tl) if tl > 0 else (99.0 if tw > 0 else 0.0)
            r2 = (reg_df["realized_rr"] >= 2.0).mean() * 100 if n else 0
            print(f"  {sc:<12} {reg:<10} {n:>5} {er:>+8.3f}R {pf:>6.2f} {win_pct:>6.1f}% {r2:>5.1f}%")

    # 4. Score Calibration: Score Bucket vs Realized Expectancy
    print("\n" + "-" * 80)
    print("3. QUALITY SCORE BUCKET CALIBRATION (OOS Partition)")
    print("-" * 80)
    print(f"  {'Scanner':<12} {'Score Bucket':<15} {'N':>5} {'E[R]':>8} {'PF':>6} {'Win%':>7}")
    print("  " + "-" * 55)
    for sc, df in dfs.items():
        c_id = champ_map.get(sc, "")
        sub = df[(df["variant_id"] == c_id) & (df["partition"] == "OOS")] if "variant_id" in df.columns else df
        if sub.empty:
            continue
        # Bucket by score: [70-79], [80-89], [90-100]
        bins = [(70, 79.9, "Score 70-79 (B)"), (80, 89.9, "Score 80-89 (A)"), (90, 105, "Score 90-100 (A+)")]
        for slo, shi, sname in bins:
            b_df = sub[(sub["score"] >= slo) & (sub["score"] <= shi)]
            if b_df.empty:
                continue
            n = len(b_df)
            er = b_df["realized_rr"].mean()
            wins = b_df[b_df["realized_rr"] > 0]
            losses = b_df[b_df["realized_rr"] < 0]
            win_pct = len(wins) / n * 100 if n else 0
            tw = wins["realized_rr"].sum()
            tl = abs(losses["realized_rr"].sum())
            pf = (tw / tl) if tl > 0 else (99.0 if tw > 0 else 0.0)
            print(f"  {sc:<12} {sname:<15} {n:>5} {er:>+8.3f}R {pf:>6.2f} {win_pct:>6.1f}%")

    # 5. Failure Cohort Diagnostics (Stop Loss Hits)
    print("\n" + "-" * 80)
    print("4. FAILURE COHORT DIAGNOSTICS (Stopped Out Trades)")
    print("-" * 80)
    print(f"  {'Scanner':<12} {'N Stopped':>10} {'SL Hit%':>8} {'Post-SL ->Entry%':>18} {'Post-SL ->T1%':>15}")
    print("  " + "-" * 68)
    for sc, df in dfs.items():
        c_id = champ_map.get(sc, "")
        sub = df[df["variant_id"] == c_id] if "variant_id" in df.columns else df
        if sub.empty:
            continue
        sl_hits = sub[sub["exit_reason"].isin(["SL_HIT", "SAME_BAR_CONFLICT_SL"])]
        n_sl = len(sl_hits)
        sl_pct = (n_sl / len(sub) * 100) if len(sub) else 0
        re = (sl_hits["post_sl_recovered_entry"].mean() * 100) if (n_sl and "post_sl_recovered_entry" in sl_hits.columns) else 0.0
        rt = (sl_hits["post_sl_recovered_t1"].mean() * 100) if (n_sl and "post_sl_recovered_t1" in sl_hits.columns) else 0.0
        print(f"  {sc:<12} {n_sl:>10} {sl_pct:>7.1f}% {re:>17.1f}% {rt:>14.1f}%")

    print("=" * 100)

if __name__ == "__main__":
    main()
