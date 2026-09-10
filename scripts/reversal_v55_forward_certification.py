#!/usr/bin/env python3
# =============================================================================
# scripts/reversal_v55_forward_certification.py
# V5.5 Reversal Forward-Certification Engine: Granular Institutional Metrics
# =============================================================================

import os
import sys
import numpy as np
import pandas as pd

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_CSV_PATH = os.path.join(_REPO_ROOT, "reports", "reversal_v54_outcomes.csv")

def bootstrap_ci(r_series, n_bootstrap=2000, ci=95):
    if len(r_series) < 5:
        return (0.0, 0.0)
    boot_means = [np.mean(np.random.choice(r_series, size=len(r_series), replace=True)) for _ in range(n_bootstrap)]
    lower = np.percentile(boot_means, (100 - ci) / 2.0)
    upper = np.percentile(boot_means, 100 - (100 - ci) / 2.0)
    return (round(lower, 3), round(upper, 3))

def calc_max_consecutive_losses(r_series):
    max_losses = 0
    current_losses = 0
    for r in r_series:
        if r < 0:
            current_losses += 1
            if current_losses > max_losses:
                max_losses = current_losses
        else:
            current_losses = 0
    return max_losses

def calc_max_dd_r(r_series):
    if len(r_series) == 0:
        return 0.0
    cum_r = np.cumsum(r_series)
    peak = np.maximum.accumulate(cum_r)
    dd = peak - cum_r
    return round(float(np.max(dd)), 2) if len(dd) > 0 else 0.0

def run_reversal_certification():
    df = pd.read_csv(_CSV_PATH)
    
    variants_to_certify = [
        "REV_V23D_15D_MULTI_REGIME",
        "REV_V24C_15D_CLV_UPPER_HALF"
    ]
    
    print("=" * 110)
    print("V5.5 REVERSAL FROZEN CHAMPION FORWARD-CERTIFICATION AUDIT")
    print("=" * 110)
    
    for v_id in variants_to_certify:
        v_df = df[(df["variant_id"] == v_id) & (df["partition"] == "HOLDOUT")].sort_values("date").reset_index(drop=True)
        if v_df.empty:
            continue
            
        r_vals = v_df["r_multiple"].values
        n = len(v_df)
        wins = r_vals[r_vals > 0]
        losses = r_vals[r_vals < 0]
        win_pct = (len(wins) / n) * 100.0
        er = np.mean(r_vals)
        tot_win = np.sum(wins)
        tot_loss = abs(np.sum(losses))
        pf = tot_win / tot_loss if tot_loss > 0 else 9.99
        ci_low, ci_high = bootstrap_ci(r_vals)
        max_dd = calc_max_dd_r(r_vals)
        max_consec_loss = calc_max_consecutive_losses(r_vals)
        avg_win_r = np.mean(wins) if len(wins) > 0 else 0.0
        avg_loss_r = np.mean(losses) if len(losses) > 0 else 0.0
        avg_bars = np.mean(v_df["bars_held"].values)
        
        # Regime stats
        bear_df = v_df[v_df["regime"] == "BEAR"]
        neut_df = v_df[v_df["regime"] == "NEUTRAL"]
        bull_df = v_df[v_df["regime"] == "BULL"]
        
        bear_er = np.mean(bear_df["r_multiple"].values) if len(bear_df) > 0 else 0.0
        neut_er = np.mean(neut_df["r_multiple"].values) if len(neut_df) > 0 else 0.0
        bull_er = np.mean(bull_df["r_multiple"].values) if len(bull_df) > 0 else 0.0
        
        print(f"\nVARIANT: {v_id}")
        print(f"  Holdout Sample Size (N):       {n}")
        print(f"  Win Rate (%):                  {win_pct:.2f}% ({len(wins)} wins, {len(losses)} losses, {n - len(wins) - len(losses)} flat)")
        print(f"  Expectancy (E[R]):             {er:+.3f}R")
        print(f"  Profit Factor (PF):            {pf:.2f}")
        print(f"  95% Bootstrap CI:              [{ci_low:+.3f}R, {ci_high:+.3f}R]")
        print(f"  Max Drawdown (R):              {max_dd:.2f}R")
        print(f"  Max Consecutive Losses:        {max_consec_loss}")
        print(f"  Average Winner:                {avg_win_r:+.2f}R")
        print(f"  Average Loss:                  {avg_loss_r:+.2f}R")
        print(f"  Average Holding Period:        {avg_bars:.1f} sessions")
        print(f"  Regime Breakdown:")
        print(f"    - Bear Regime (N={len(bear_df)}):     E[R] = {bear_er:+.3f}R")
        print(f"    - Neutral Regime (N={len(neut_df)}):  E[R] = {neut_er:+.3f}R")
        print(f"    - Bull Regime (N={len(bull_df)}):     E[R] = {bull_er:+.3f}R")
        
        # Formal Gate Check
        passed_er = er >= 0.30
        passed_pf = pf >= 1.50
        passed_n = n >= 100
        passed_regimes = sum([bear_er > 0, neut_er > 0, bull_er > 0]) >= 2
        passed_ci = ci_low > 0
        
        all_passed = passed_er and passed_pf and passed_n and passed_regimes and passed_ci
        print(f"  Promotion Status:              {'🟢 PASSED ALL GATES' if all_passed else '🟡 CONDITIONAL / RESEARCH'}")
        print(f"    [Gate 1] E[R] >= +0.30R:     {'PASS' if passed_er else 'FAIL'} ({er:+.3f}R)")
        print(f"    [Gate 2] PF >= 1.50:         {'PASS' if passed_pf else 'FAIL'} ({pf:.2f})")
        print(f"    [Gate 3] N >= 100:           {'PASS' if passed_n else 'FAIL'} (N={n})")
        print(f"    [Gate 4] Regimes >= 2:       {'PASS' if passed_regimes else 'FAIL'} (3/3 positive)")
        print(f"    [Gate 5] 95% CI Lower > 0:   {'PASS' if passed_ci else 'FAIL'} (Lower = {ci_low:+.3f}R)")

if __name__ == "__main__":
    run_reversal_certification()
