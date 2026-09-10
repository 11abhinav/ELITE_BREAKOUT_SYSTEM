#!/usr/bin/env python3
# =============================================================================
# scripts/simulate_v57_portfolio_governance.py
# V5.7 MASTER 11-SCANNER PORTFOLIO ALLOCATION & FRESH FORWARD SIMULATOR
# =============================================================================
# Evaluates 4 portfolio allocation strategies across DEV -> VAL -> LOCKED HOLDOUT:
# 1. Strategy 1: Flat Equal Risk (1.0R across all 11 scanners)
# 2. Strategy 2: Staged Tier Governance (Tier 1 = 1.0R, Tier 2 = 0.6R, Tier 3 = 0.0R)
# 3. Strategy 3: Certified Champion Rollout (Tier 1+Reversal = 1.0R, Tier 2 = 0.6R, Tier 3 = 0.3R)
# 4. Strategy 4: Dynamic Regime-Adaptive Allocation (Bull/Bear/Neutral Specialized)
# =============================================================================

import json
import os
import sys
import numpy as np
import pandas as pd

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_REPORTS_DIR = os.path.join(_REPO_ROOT, "reports")
os.makedirs(_REPORTS_DIR, exist_ok=True)

from audit_11_scanner_overlap_and_interaction import SCANNER_CONFIGS, standardize_df

ALLOCATION_STRATEGIES = {
    "Strategy_1_Flat_Equal_Risk_11": {
        "type": "STATIC",
        "weights": {
            "WEALTH": 1.0, "PULLBACK_V2": 1.0, "ACCUMULATION_VCP": 1.0, "EOD_BREAKOUT": 1.0,
            "MULTITF_5M": 1.0, "TECHNICAL_AHAT": 1.0, "DAILY_BUILDER": 1.0, "REVERSAL": 1.0,
            "MULTITF_1H": 1.0, "SHORT_COVERING_EOD": 1.0, "MULTIBAGGER": 1.0
        }
    },
    "Strategy_2_Staged_Tier_Governance": {
        "type": "STATIC",
        "weights": {
            "WEALTH": 1.0, "PULLBACK_V2": 1.0, "ACCUMULATION_VCP": 1.0, "EOD_BREAKOUT": 1.0,
            "MULTITF_5M": 1.0, "TECHNICAL_AHAT": 0.60, "DAILY_BUILDER": 0.60, "REVERSAL": 0.0,
            "MULTITF_1H": 0.0, "SHORT_COVERING_EOD": 0.0, "MULTIBAGGER": 0.0
        }
    },
    "Strategy_3_Certified_Champion_Rollout": {
        "type": "STATIC",
        "weights": {
            "WEALTH": 1.0, "PULLBACK_V2": 1.0, "ACCUMULATION_VCP": 1.0, "EOD_BREAKOUT": 1.0,
            "MULTITF_5M": 1.0, "REVERSAL": 1.0, "TECHNICAL_AHAT": 0.60, "DAILY_BUILDER": 0.60,
            "SHORT_COVERING_EOD": 0.35, "MULTIBAGGER": 0.35, "MULTITF_1H": 0.20
        }
    },
    "Strategy_4_Regime_Adaptive_Dynamic": {
        "type": "DYNAMIC_REGIME",
        "regime_weights": {
            "BULL": {
                "WEALTH": 1.0, "PULLBACK_V2": 0.85, "ACCUMULATION_VCP": 1.0, "EOD_BREAKOUT": 1.0,
                "MULTITF_5M": 0.80, "TECHNICAL_AHAT": 0.60, "DAILY_BUILDER": 0.60, "REVERSAL": 0.40,
                "MULTIBAGGER": 0.80, "MULTITF_1H": 0.40, "SHORT_COVERING_EOD": 0.0
            },
            "NEUTRAL": {
                "WEALTH": 0.80, "PULLBACK_V2": 1.0, "ACCUMULATION_VCP": 0.80, "EOD_BREAKOUT": 0.80,
                "MULTITF_5M": 0.80, "TECHNICAL_AHAT": 0.60, "DAILY_BUILDER": 0.50, "REVERSAL": 0.80,
                "MULTIBAGGER": 0.50, "MULTITF_1H": 0.30, "SHORT_COVERING_EOD": 0.60
            },
            "BEAR": {
                "REVERSAL": 1.20, "SHORT_COVERING_EOD": 1.00, "MULTITF_5M": 0.40, "PULLBACK_V2": 0.30,
                "WEALTH": 0.20, "DAILY_BUILDER": 0.20, "TECHNICAL_AHAT": 0.20, "ACCUMULATION_VCP": 0.0,
                "EOD_BREAKOUT": 0.0, "MULTIBAGGER": 0.0, "MULTITF_1H": 0.0
            }
        }
    }
}

def calc_strategy_metrics(daily_r_series):
    if len(daily_r_series) == 0:
        return {"total_r": 0.0, "mean_r": 0.0, "std_r": 0.0, "sharpe": 0.0, "sortino": 0.0, "max_dd_r": 0.0, "calmar": 0.0}
    r = np.array(daily_r_series)
    tot_r = float(np.sum(r))
    mean_r = float(np.mean(r))
    std_r = float(np.std(r))
    sharpe = float(mean_r / std_r * np.sqrt(252)) if std_r > 0 else 0.0

    neg_r = r[r < 0]
    downside_std = float(np.std(neg_r)) if len(neg_r) > 0 else 1e-6
    sortino = float(mean_r / downside_std * np.sqrt(252)) if downside_std > 0 else 0.0

    cum_r = np.cumsum(r)
    peak = np.maximum.accumulate(cum_r)
    max_dd = float(np.max(peak - cum_r)) if len(cum_r) > 0 else 0.0
    calmar = float(tot_r / max_dd) if max_dd > 0 else 0.0

    return {
        "total_r": round(tot_r, 2),
        "mean_r": round(mean_r, 3),
        "std_r": round(std_r, 3),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "max_dd_r": round(max_dd, 1),
        "calmar": round(calmar, 2)
    }

def run_portfolio_simulation():
    print("=" * 115)
    print("V5.7 MASTER 11-SCANNER PORTFOLIO ALLOCATION & RISK SIMULATION")
    print("=" * 115)

    loaded_dfs = {}
    scanner_names = list(SCANNER_CONFIGS.keys())

    for name, cfg in SCANNER_CONFIGS.items():
        f_path = os.path.join(_REPORTS_DIR, cfg["file"])
        if not os.path.exists(f_path):
            continue
        raw_df = pd.read_csv(f_path)
        target_v = cfg["variant"]
        if "variant_id" in raw_df.columns:
            if target_v in raw_df["variant_id"].values:
                df_sub = raw_df[raw_df["variant_id"] == target_v].copy()
            else:
                avail = raw_df["variant_id"].unique()
                df_sub = raw_df[raw_df["variant_id"] == avail[0]].copy()
        else:
            df_sub = raw_df.copy()

        std_df = standardize_df(df_sub)
        std_df["scanner"] = name
        loaded_dfs[name] = std_df

    all_dates = sorted(list(set.union(*[set(df["date"].values) for df in loaded_dfs.values() if not df.empty])))
    print(f"Total Unique Trading Dates: {len(all_dates)} | Scanners: {len(loaded_dfs)}")

    # Aggregate daily returns per scanner and detect macro regime per date
    daily_matrix = pd.DataFrame(index=all_dates)
    daily_regimes = {}

    for name in scanner_names:
        if name in loaded_dfs:
            df = loaded_dfs[name]
            daily_agg = df.groupby("date")["r_multiple"].sum()
            daily_matrix[name] = daily_agg

            # Map date to regime (mode regime of trades on that date)
            for d, grp in df.groupby("date"):
                if d not in daily_regimes and "regime" in grp.columns:
                    daily_regimes[d] = grp["regime"].mode()[0] if not grp["regime"].empty else "NEUTRAL"
        else:
            daily_matrix[name] = 0.0

    daily_matrix = daily_matrix.fillna(0.0)

    # ── Simulate All 4 Strategies Across Partitions ──────────────────────────
    results = {}

    for strat_name, strat_cfg in ALLOCATION_STRATEGIES.items():
        stype = strat_cfg["type"]
        strat_daily_r = []

        for d in all_dates:
            reg = daily_regimes.get(d, "NEUTRAL")
            if stype == "STATIC":
                w_dict = strat_cfg["weights"]
            else:
                w_dict = strat_cfg["regime_weights"].get(reg, strat_cfg["regime_weights"]["NEUTRAL"])

            day_r = 0.0
            for name in scanner_names:
                w = w_dict.get(name, 0.0)
                r_val = daily_matrix.loc[d, name]
                day_r += (w * r_val)
            strat_daily_r.append(day_r)

        strat_df = pd.DataFrame({
            "date": all_dates,
            "portfolio_r": strat_daily_r
        })
        strat_df["partition"] = np.where(strat_df["date"] < "2026-01-01", "DEV",
                                np.where(strat_df["date"] < "2026-06-01", "VAL", "HOLDOUT"))

        # Compute metrics across partitions
        strat_metrics = {}
        for part in ["DEV", "VAL", "HOLDOUT", "FULL"]:
            p_df = strat_df if part == "FULL" else strat_df[strat_df["partition"] == part]
            m = calc_strategy_metrics(p_df["portfolio_r"].values)
            strat_metrics[part] = m

        results[strat_name] = strat_metrics

    # ── Print Strategy Comparison Table ──────────────────────────────────────
    print("\n" + "=" * 120)
    print(f"{'ALLOCATION STRATEGY':<38} | {'PARTITION':<8} | {'TOTAL R':>9} | {'MEAN R/DAY':>10} | {'SHARPE':>7} | {'SORTINO':>8} | {'MAX DD':>8} | {'CALMAR':>7}")
    print("=" * 120)

    for s_name, s_res in results.items():
        for part in ["DEV", "VAL", "HOLDOUT", "FULL"]:
            m = s_res[part]
            highlight = "🟢" if (part == "HOLDOUT" and s_name == "Strategy_4_Regime_Adaptive_Dynamic") else "  "
            print(f"{highlight}{s_name:<36} | {part:<8} | {m['total_r']:>+8.1f}R | {m['mean_r']:>+9.2f}R | {m['sharpe']:>7.2f} | {m['sortino']:>8.2f} | {m['max_dd_r']:>7.1f}R | {m['calmar']:>7.2f}")
        print("-" * 120)

    out_json = os.path.join(_REPORTS_DIR, "v57_portfolio_simulation_results.json")
    with open(out_json, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved portfolio simulation results to {out_json}")

    return results

if __name__ == "__main__":
    run_portfolio_simulation()
