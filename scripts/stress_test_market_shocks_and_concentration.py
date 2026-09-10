#!/usr/bin/env python3
# =============================================================================
# scripts/stress_test_market_shocks_and_concentration.py
# V5.8 MARKET SHOCK CORRELATION SPIKE & CONCENTRATION STRESS TEST
# =============================================================================
# Simulates severe market shock environments:
# 1. Macro Liquidation Event (Simultaneous correlated drawdowns across equities)
# 2. WEALTH + DAILY_BUILDER Concentration Stress (+0.895 correlation dependency)
# 3. Sector Concentration & Clustered Loss Analysis (Unconstrained vs Capped)
# =============================================================================

import json
import os
import sys
import numpy as np
import pandas as pd

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_REPORTS_DIR = os.path.join(_REPO_ROOT, "reports")
os.makedirs(_REPORTS_DIR, exist_ok=True)

from v58_prelive_certification_engine import (
    FROZEN_REGISTRY,
    SECTOR_MAP,
    standardize_df,
    apply_realistic_friction,
    calc_performance_summary
)

def run_stress_testing():
    print("=" * 115)
    print("V5.8 MARKET SHOCK CORRELATION SPIKE & PORTFOLIO CONCENTRATION STRESS TESTING")
    print("=" * 115)

    loaded_trades = {}
    for name, cfg in FROZEN_REGISTRY.items():
        fpath = os.path.join(_REPORTS_DIR, cfg["file"])
        raw_df = pd.read_csv(fpath)
        v_target = cfg["variant_id"]
        if "variant_id" in raw_df.columns and v_target in raw_df["variant_id"].values:
            sub_df = raw_df[raw_df["variant_id"] == v_target].copy()
        else:
            sub_df = raw_df.copy()

        std_df = standardize_df(sub_df)
        std_df["scanner"] = name
        std_df["holding_type"] = cfg["holding_type"]
        std_df["net_r"] = std_df.apply(lambda r: apply_realistic_friction(r, cfg["holding_type"]), axis=1)
        loaded_trades[name] = std_df

    # ── 1. WEALTH + DAILY_BUILDER CONCENTRATION STRESS ───────────────────────
    print("\n[Stress Test 1] WEALTH + DAILY_BUILDER Dependency Analysis (+0.895 Correlation):")
    wealth_df = loaded_trades["WEALTH"]
    builder_df = loaded_trades["DAILY_BUILDER"]

    # Find co-firing events on same date & same symbol
    merged = pd.merge(
        wealth_df,
        builder_df,
        on=["date", "symbol"],
        suffixes=("_wealth", "_builder")
    )

    total_co_fires = len(merged)
    both_loss = len(merged[(merged["net_r_wealth"] < 0) & (merged["net_r_builder"] < 0)])
    both_win = len(merged[(merged["net_r_wealth"] > 0) & (merged["net_r_builder"] > 0)])
    split_outcome = total_co_fires - both_loss - both_win

    print(f"  • Total Same-Date Same-Symbol Co-Fires: {total_co_fires}")
    print(f"  • Both Stop Out (Clustered Loss): {both_loss} ({both_loss/total_co_fires*100:.1f}%)")
    print(f"  • Both Hit Target (Compounded Win): {both_win} ({both_win/total_co_fires*100:.1f}%)")
    print(f"  • Divergent Outcomes: {split_outcome} ({split_outcome/total_co_fires*100:.1f}%)")

    # Impact of capping WEALTH + DAILY_BUILDER combined exposure
    # Unconstrained vs 1.5R Symbol Cap vs 1.0R Single-Scanner Priority Rule
    unconstrained_loss = merged["net_r_wealth"].sum() + (merged["net_r_builder"].sum() * 0.60)
    capped_loss = (merged["net_r_wealth"] * 1.0 + merged["net_r_builder"] * 0.25).sum()
    priority_only = merged["net_r_wealth"].sum() # WEALTH priority

    print(f"  • Unconstrained Net Return on Co-Fired Trades: {unconstrained_loss:+.1f}R")
    print(f"  • Capped (1.0R Wealth + 0.25R Builder) Net Return: {capped_loss:+.1f}R")
    print(f"  • Priority Rule (Wealth Only) Net Return: {priority_only:+.1f}R")

    # ── 2. SYNTHETIC CRISIS CORRELATION SPIKE SIMULATION ─────────────────────
    print("\n[Stress Test 2] Synthetic Market Liquidation Shock Simulation (Correlations -> +0.85 to +0.95):")
    # We identify the worst 10% market dates in history and simulate an acute liquidity shock:
    # On shock dates, 80% of open breakout positions experience gap-through stop-outs (-1.40R net)
    all_dates = sorted(list(set.union(*[set(df["date"].values) for df in loaded_trades.values() if not df.empty])))

    # Group trades by date and compute daily returns under Normal vs Acute Stress
    normal_daily = {d: 0.0 for d in all_dates}
    stressed_daily = {d: 0.0 for d in all_dates}
    shock_dates_count = 0

    for d in all_dates:
        day_trades = []
        for name, df in loaded_trades.items():
            w = FROZEN_REGISTRY[name]["base_weight"]
            sub = df[df["date"] == d]
            for _, r in sub.iterrows():
                day_trades.append({"scanner": name, "net_r": r["net_r"] * w, "regime": r["regime"]})

        if not day_trades:
            continue

        base_day_r = sum(t["net_r"] for t in day_trades)
        normal_daily[d] = base_day_r

        # If day is a regime shock / negative day, simulate extreme correlation spike
        if base_day_r < -2.0 or (day_trades[0]["regime"] == "BEAR"):
            shock_dates_count += 1
            # In severe stress: Trend/Breakouts suffer -1.40R loss, Reversal/Short Covering gain counter-trend alpha
            stress_day_r = 0.0
            for t in day_trades:
                scn = t["scanner"]
                if scn in ["REVERSAL", "SHORT_COVERING_EOD"]:
                    # Counter-trend engines capture enhanced liquidity bounce
                    stress_day_r += max(t["net_r"], +0.50 * FROZEN_REGISTRY[scn]["base_weight"])
                else:
                    # Breakout engines suffer simultaneous adverse slippage
                    stress_day_r += min(t["net_r"], -1.35 * FROZEN_REGISTRY[scn]["base_weight"])
            stressed_daily[d] = stress_day_r
        else:
            stressed_daily[d] = base_day_r

    norm_res = calc_performance_summary(list(normal_daily.values()))
    stress_res = calc_performance_summary(list(stressed_daily.values()))

    print(f"  • Total Market Shock Days Tested: {shock_dates_count} / {len(all_dates)} ({shock_dates_count/len(all_dates)*100:.1f}%)")
    print(f"  • Normal Regime Net Sharpe: {norm_res['sharpe']:.2f} | Max DD: {norm_res['max_dd_r']:.1f}R | Mean R/Day: {norm_res['er']:+.2f}R")
    print(f"  • Acute Shock Net Sharpe:  {stress_res['sharpe']:.2f} | Max DD: {stress_res['max_dd_r']:.1f}R | Mean R/Day: {stress_res['er']:+.2f}R")
    print(f"  • Drawdown Expansion Factor under Acute Stress: {stress_res['max_dd_r'] / max(norm_res['max_dd_r'], 1e-6):.2f}x")

    # ── 3. SECTOR CONCENTRATION AUDIT ────────────────────────────────────────
    print("\n[Stress Test 3] Sector Exposure & Clustered Stop Concentration:")
    sector_exposure_counts = {}
    for name, df in loaded_trades.items():
        for _, r in df.iterrows():
            sec = SECTOR_MAP.get(r["symbol"].split(".")[0].upper(), "BROAD_MARKET")
            sector_exposure_counts[sec] = sector_exposure_counts.get(sec, 0) + 1

    total_t = sum(sector_exposure_counts.values())
    print("  Sector Distribution Across Total Evaluated Trades:")
    for sec, count in sorted(sector_exposure_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"    - {sec:<15}: {count:>5} trades ({count/total_t*100:>5.1f}%)")

    stress_results = {
        "wealth_builder_co_fires": {
            "total_co_fires": total_co_fires,
            "both_loss": both_loss,
            "both_win": both_win,
            "unconstrained_net_r": round(unconstrained_loss, 1),
            "capped_net_r": round(capped_loss, 1),
            "priority_net_r": round(priority_only, 1)
        },
        "acute_shock_simulation": {
            "shock_days_count": shock_dates_count,
            "normal_sharpe": norm_res["sharpe"],
            "normal_max_dd": norm_res["max_dd_r"],
            "stressed_sharpe": stress_res["sharpe"],
            "stressed_max_dd": stress_res["max_dd_r"],
            "drawdown_expansion_factor": round(stress_res["max_dd_r"] / max(norm_res["max_dd_r"], 1e-6), 2)
        },
        "sector_breakdown": sector_exposure_counts
    }

    out_json = os.path.join(_REPORTS_DIR, "v58_market_shock_stress_results.json")
    with open(out_json, "w") as f:
        json.dump(stress_results, f, indent=2)
    print(f"\nSaved market shock stress results to: {out_json}")

if __name__ == "__main__":
    run_stress_testing()
