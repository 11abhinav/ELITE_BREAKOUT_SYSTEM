#!/usr/bin/env python3
# =============================================================================
# scripts/generate_multibagger_v57_certification_report.py
# V5.7 Multibagger Scale Institutional Report Generator
# =============================================================================

import os
import sys
import numpy as np
import pandas as pd

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_CSV_PATH = os.path.join(_REPO_ROOT, "reports", "multibagger_v57_scale_outcomes.csv")

def bootstrap_ci(r_series, n_bootstrap=2000, ci=95):
    if len(r_series) < 5:
        return (0.0, 0.0)
    boot_means = [np.mean(np.random.choice(r_series, size=len(r_series), replace=True)) for _ in range(n_bootstrap)]
    lower = np.percentile(boot_means, (100 - ci) / 2.0)
    upper = np.percentile(boot_means, 100 - (100 - ci) / 2.0)
    return (round(lower, 3), round(upper, 3))

def calc_max_dd_r(r_series):
    if len(r_series) == 0:
        return 0.0
    cum_r = np.cumsum(r_series)
    peak = np.maximum.accumulate(cum_r)
    dd = peak - cum_r
    return round(float(np.max(dd)), 2) if len(dd) > 0 else 0.0

def compute_mbag_metrics(df_subset):
    n = len(df_subset)
    if n == 0:
        return {
            "n": 0, "win_pct": 0.0, "er": 0.0, "pf": 0.0,
            "ci_low": 0.0, "ci_high": 0.0, "max_dd_r": 0.0,
            "avg_win": 0.0, "avg_loss": 0.0, "avg_bars": 0.0,
            "pct_5r": 0.0, "pct_3r": 0.0, "max_r": 0.0,
            "bear_er": "N/A", "neut_er": "N/A", "bull_er": "N/A",
            "bear_n": 0, "neut_n": 0, "bull_n": 0
        }
    r = df_subset["r_multiple"].values
    wins = r[r > 0]
    losses = r[r < 0]
    win_pct = (len(wins) / n) * 100.0
    er = np.mean(r)
    tot_win = np.sum(wins)
    tot_loss = abs(np.sum(losses))
    pf = tot_win / tot_loss if tot_loss > 0 else (9.99 if tot_win > 0 else 0.0)
    ci_low, ci_high = bootstrap_ci(r)
    max_dd = calc_max_dd_r(r)
    avg_win = np.mean(wins) if len(wins) > 0 else 0.0
    avg_loss = np.mean(losses) if len(losses) > 0 else 0.0
    avg_bars = np.mean(df_subset["bars_held"].values)

    pct_5r = (np.sum(r >= 4.4) / n) * 100.0
    pct_3r = (np.sum(r >= 2.9) / n) * 100.0
    max_r = float(np.max(r)) if len(r) > 0 else 0.0

    bear_df = df_subset[df_subset["regime"] == "BEAR"]
    neut_df = df_subset[df_subset["regime"] == "NEUTRAL"]
    bull_df = df_subset[df_subset["regime"] == "BULL"]

    bear_er = f"{np.mean(bear_df['r_multiple'].values):+.3f}R" if len(bear_df) > 0 else "N/A"
    neut_er = f"{np.mean(neut_df['r_multiple'].values):+.3f}R" if len(neut_df) > 0 else "N/A"
    bull_er = f"{np.mean(bull_df['r_multiple'].values):+.3f}R" if len(bull_df) > 0 else "N/A"

    return {
        "n": n,
        "win_pct": round(win_pct, 1),
        "er": round(er, 3),
        "pf": round(pf, 2),
        "ci_low": ci_low,
        "ci_high": ci_high,
        "max_dd_r": max_dd,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "avg_bars": round(avg_bars, 1),
        "pct_5r": round(pct_5r, 1),
        "pct_3r": round(pct_3r, 1),
        "max_r": round(max_r, 2),
        "bear_er": bear_er,
        "neut_er": neut_er,
        "bull_er": bull_er,
        "bear_n": len(bear_df),
        "neut_n": len(neut_df),
        "bull_n": len(bull_df)
    }

def main():
    if not os.path.exists(_CSV_PATH):
        print(f"Error: {_CSV_PATH} not found.")
        sys.exit(1)

    df = pd.read_csv(_CSV_PATH)
    variants = df["variant_id"].unique()

    print("\n" + "="*135)
    print(f"{'VARIANT ID':<34} | {'PART':<7} | {'N':>4} | {'WIN%':>6} | {'E[R]':>7} | {'PF':>5} | {'5R+%':>5} | {'95% CI':>14} | {'MAX DD':>7} | {'NEUT':>8} | {'BULL':>8}")
    print("="*135)

    summary_rows = []

    for v in variants:
        v_df = df[df["variant_id"] == v]
        for part in ["DEV", "VAL", "HOLDOUT"]:
            p_df = v_df[v_df["partition"] == part]
            m = compute_mbag_metrics(p_df)
            ci_str = f"[{m['ci_low']:+.2f}, {m['ci_high']:+.2f}]"
            print(f"{v:<34} | {part:<7} | {m['n']:>4} | {m['win_pct']:>5.1f}% | {m['er']:>+6.3f}R | {m['pf']:>5.2f} | {m['pct_5r']:>4.1f}% | {ci_str:>14} | {m['max_dd_r']:>6.1f}R | {m['neut_er']:>8} | {m['bull_er']:>8}")
            summary_rows.append({
                "variant_id": v,
                "partition": part,
                **m
            })
        print("-" * 135)

    sum_df = pd.DataFrame(summary_rows)
    sum_df.to_csv(os.path.join(_REPO_ROOT, "reports", "multibagger_v57_summary_matrix.csv"), index=False)
    print(f"\nSummary matrix saved to reports/multibagger_v57_summary_matrix.csv")

if __name__ == "__main__":
    main()
