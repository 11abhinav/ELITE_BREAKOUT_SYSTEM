#!/usr/bin/env python3
# =============================================================================
# scripts/v59_fresh_forward_certification_engine.py
# V5.9 MASTER 11-SCANNER FRESH FORWARD NET CERTIFICATION & GOVERNANCE ENGINE
# =============================================================================
# Implements the final pre-live governance framework:
# 1. Epistemological partition correction:
#    - DEV (2025-07-24 -> 2025-12-31): Calibration & Discovery
#    - VAL (2026-01-01 -> 2026-05-31): Tuning & Parameter Selection
#    - LOCKED HISTORICAL REPRODUCTION (2026-06-01 -> 2026-09-04): Historical Certification Set
#    - FRESH FORWARD DEPLOYMENT (Post-2026-09-04): Genuinely Unseen Live Forward Period
# 2. 3-Tiered Live Governance Bucketing:
#    - CORE LIVE (1.0R): Reversal, Pullback V2, VCP, EOD, MultiTF 5M
#    - REDUCED LIVE / OBSERVE (0.25R - 0.50R): Wealth, Technical Ahat, Daily Builder
#    - SHADOW PAPER (0.25R Shadow): MultiTF 1H, Short Covering, Multibagger
# 3. Comprehensive Net-of-Friction, Slippage, Gap-Stop, and Hard Risk Governor Telemetry
# =============================================================================

import hashlib
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
    FRICTION_PARAMETERS,
    SECTOR_MAP,
    get_sector,
    compute_file_sha256,
    standardize_df,
    apply_realistic_friction,
    calc_performance_summary
)

# ── 1. V5.9 THREE-TIERED GOVERNANCE CLASSIFICATION ───────────────────────────
V59_GOVERNANCE_TIERS = {
    # ── BUCKET 1: CORE LIVE (Proven Robust Net Economics) ────────────────────
    "REVERSAL": {
        "tier_bucket": "CORE_LIVE",
        "live_weight": 1.00,
        "shadow_weight": 0.00,
        "rationale": "Strongest net alpha (+0.295R, PF 1.45), CI [+0.09, +0.49] strictly positive, Bear shock absorber."
    },
    "ACCUMULATION_VCP": {
        "tier_bucket": "CORE_LIVE",
        "live_weight": 1.00,
        "shadow_weight": 0.00,
        "rationale": "High-win-rate precision breakout (+0.148R net, PF 1.37, 51.8% net WR), lowest drawdown (10.5R)."
    },
    "EOD_BREAKOUT": {
        "tier_bucket": "CORE_LIVE",
        "live_weight": 1.00,
        "shadow_weight": 0.00,
        "rationale": "Quality daily shelf expansion (+0.132R net, PF 1.28, 51.6% net WR), low drawdown (8.5R)."
    },
    "MULTITF_5M": {
        "tier_bucket": "CORE_LIVE",
        "live_weight": 1.00,
        "shadow_weight": 0.00,
        "rationale": "Intraday momentum (+0.132R net, PF 1.30, 50.4% net WR), positive across all regimes."
    },
    "PULLBACK_V2": {
        "tier_bucket": "CORE_LIVE",
        "live_weight": 1.00,
        "shadow_weight": 0.00,
        "rationale": "High-capacity swing flagship (+0.126R net, PF 1.22, N=1,805), CI [+0.06, +0.19] strictly positive."
    },

    # ── BUCKET 2: REDUCED LIVE / OBSERVE (Positive but Friction-Thin) ────────
    "WEALTH": {
        "tier_bucket": "REDUCED_LIVE_OBSERVE",
        "live_weight": 0.50,
        "shadow_weight": 0.00,
        "rationale": "Multi-year compounder (+0.090R net, PF 1.12, N=5,037), positive CI [+0.04, +0.14], but reduced to 0.50R due to friction compression."
    },
    "TECHNICAL_AHAT": {
        "tier_bucket": "REDUCED_LIVE_OBSERVE",
        "live_weight": 0.25,
        "shadow_weight": 0.00,
        "rationale": "Friction-neutral (+0.018R net, PF 1.03), CI [-0.21, +0.25] crosses zero. Reduced to 0.25R observation."
    },
    "DAILY_BUILDER": {
        "tier_bucket": "REDUCED_LIVE_OBSERVE",
        "live_weight": 0.25,
        "shadow_weight": 0.00,
        "rationale": "Essentially friction-neutral (+0.005R net, PF 1.01), CI [-0.04, +0.05] crosses zero. Reduced to 0.25R & Wealth priority."
    },

    # ── BUCKET 3: SHADOW PAPER (Incubation & Paper Tracking Only) ────────────
    "MULTIBAGGER": {
        "tier_bucket": "SHADOW_PAPER",
        "live_weight": 0.00,
        "shadow_weight": 0.25,
        "rationale": "Convexity engine (+0.254R net, PF 1.40, 9.0% 5R+ rate). Shadow paper tracking; gate requires ≥50-100 trades with ≥7.0% 5R+ frequency."
    },
    "SHORT_COVERING_EOD": {
        "tier_bucket": "SHADOW_PAPER",
        "live_weight": 0.00,
        "shadow_weight": 0.25,
        "rationale": "Bear specialist (+0.153R net, PF 1.22, Bear Net +0.285R). Shadow paper tracking; gate requires ≥50 trades with Bear net ER ≥+0.30R."
    },
    "MULTITF_1H": {
        "tier_bucket": "SHADOW_PAPER",
        "live_weight": 0.00,
        "shadow_weight": 0.25,
        "rationale": "Hourly breakout (+0.032R net, PF 1.05, CI crosses zero). Shadow paper tracking; not production ready."
    }
}

def calc_consecutive_losses(r_series):
    max_streak = 0
    curr_streak = 0
    for val in r_series:
        if val <= 0:
            curr_streak += 1
            max_streak = max(max_streak, curr_streak)
        else:
            curr_streak = 0
    return max_streak

def run_v59_certification():
    print("=" * 125)
    print("V5.9 FRESH FORWARD NET CERTIFICATION & THREE-TIERED PRODUCTION GOVERNANCE ENGINE")
    print("=" * 125)

    loaded_trades = {}
    scanner_stats = {}

    for name, cfg in FROZEN_REGISTRY.items():
        fpath = os.path.join(_REPORTS_DIR, cfg["file"])
        f_hash = compute_file_sha256(fpath)

        raw_df = pd.read_csv(fpath)
        v_target = cfg["variant_id"]
        if "variant_id" in raw_df.columns and v_target in raw_df["variant_id"].values:
            sub_df = raw_df[raw_df["variant_id"] == v_target].copy()
        else:
            sub_df = raw_df.copy()

        std_df = standardize_df(sub_df)
        std_df["scanner"] = name
        std_df["holding_type"] = cfg["holding_type"]
        std_df["sector"] = std_df["symbol"].apply(get_sector)
        std_df["net_r"] = std_df.apply(lambda r: apply_realistic_friction(r, cfg["holding_type"]), axis=1)

        # Relabel partitions strictly per V5.9 epistemology
        std_df["partition_v59"] = np.where(std_df["date"] < "2026-01-01", "CALIBRATION_DEV",
                                  np.where(std_df["date"] < "2026-06-01", "TUNING_VAL",
                                           "LOCKED_REPRODUCTION_HISTORICAL"))

        loaded_trades[name] = std_df

        # Compute Detailed Telemetry on the Locked Historical Reproduction Dataset
        h_df = std_df[std_df["partition_v59"] == "LOCKED_REPRODUCTION_HISTORICAL"]
        g_perf = calc_performance_summary(h_df["r_multiple"].values)
        n_perf = calc_performance_summary(h_df["net_r"].values)
        max_loss_streak = calc_consecutive_losses(h_df["net_r"].values)
        ci_crosses_zero = (n_perf["ci_low"] <= 0.0)

        gov_info = V59_GOVERNANCE_TIERS[name]

        scanner_stats[name] = {
            "variant_id": cfg["variant_id"],
            "sha256": f_hash,
            "governance_bucket": gov_info["tier_bucket"],
            "live_weight": gov_info["live_weight"],
            "shadow_weight": gov_info["shadow_weight"],
            "rationale": gov_info["rationale"],
            "n": n_perf["n"],
            "gross_wr": g_perf["win_pct"],
            "net_wr": n_perf["win_pct"],
            "gross_er": g_perf["er"],
            "net_er": n_perf["er"],
            "gross_pf": g_perf["pf"],
            "net_pf": n_perf["pf"],
            "net_ci_low": n_perf["ci_low"],
            "net_ci_high": n_perf["ci_high"],
            "ci_crosses_zero": ci_crosses_zero,
            "net_max_dd": n_perf["max_dd_r"],
            "max_consecutive_losses": max_loss_streak
        }

    # ── PRINT THREE-TIERED GOVERNANCE TABLE ──────────────────────────────────
    print("\n" + "=" * 135)
    print(f"{'SCANNER':<19} | {'V5.9 GOVERNANCE BUCKET':<21} | {'WEIGHT':>6} | {'N':>4} | {'GROSS ER':>9} | {'NET ER':>8} | {'GROSS PF':>8} | {'NET PF':>7} | {'NET 95% CI':>16} | {'ZERO?':>5} | {'NET DD':>7} | {'MAX STREAK':>10}")
    print("=" * 135)

    for name, s in scanner_stats.items():
        tag = s["governance_bucket"]
        w = s["live_weight"] if s["live_weight"] > 0 else s["shadow_weight"]
        w_str = f"{w:.2f}R" + (" (Live)" if s["live_weight"] > 0 else " (Shdw)")
        zero_flag = "⚠️ YES" if s["ci_crosses_zero"] else "  NO "
        print(f"{name:<19} | {tag:<21} | {w_str:>12} | {s['n']:>4} | {s['gross_er']:>+8.3f}R | {s['net_er']:>+7.3f}R | {s['gross_pf']:>8.2f} | {s['net_pf']:>7.2f} | [{s['net_ci_low']:>+5.2f}, {s['net_ci_high']:>+5.2f}] | {zero_flag:<5} | {s['net_max_dd']:>6.1f}R | {s['max_consecutive_losses']:>10}")

    # ── SIMULATE V5.9 THREE-TIERED RISK-CONTROLLED PORTFOLIO ─────────────────
    print("\n" + "=" * 125)
    print("V5.9 THREE-TIERED RISK-CONTROLLED PORTFOLIO SIMULATION ACROSS EPISTEMOLOGICAL PARTITIONS")
    print("=" * 125)

    all_dates = sorted(list(set.union(*[set(df["date"].values) for df in loaded_trades.values() if not df.empty])))

    # Chronological trade stream with V5.9 weights
    all_trade_records = []
    for name, df in loaded_trades.items():
        w = V59_GOVERNANCE_TIERS[name]["live_weight"]
        if w > 0:
            for _, row in df.iterrows():
                all_trade_records.append({
                    "date": row["date"],
                    "scanner": name,
                    "symbol": row["symbol"],
                    "sector": row["sector"],
                    "gross_r": row["r_multiple"] * w,
                    "net_r": row["net_r"] * w,
                    "weight": w,
                    "holding_type": row["holding_type"],
                    "partition_v59": row["partition_v59"]
                })

    trade_stream = pd.DataFrame(all_trade_records).sort_values(by=["date", "scanner"]).reset_index(drop=True)

    rejections_by_reason = {
        "SYMBOL_CAP": 0,
        "SECTOR_CAP": 0,
        "SCANNER_POS_CAP": 0,
        "WEALTH_PRIORITY_BUILDER_BLOCK": 0,
        "CIRCUIT_BREAKER": 0
    }

    executed_trades = []
    daily_v59_returns = {d: 0.0 for d in all_dates}

    for d, day_trades in trade_stream.groupby("date"):
        day_symbol_exposure = {}
        day_sector_exposure = {}
        day_scanner_counts = {}
        day_realized_loss = 0.0
        circuit_tripped = False
        wealth_symbols_today = set(day_trades[day_trades["scanner"] == "WEALTH"]["symbol"].values)

        for _, tr in day_trades.iterrows():
            sym = tr["symbol"]
            sec = tr["sector"]
            scn = tr["scanner"]
            w = tr["weight"]
            net_r = tr["net_r"]

            # Circuit Breaker Check (-3.0R daily realized loss)
            if circuit_tripped:
                rejections_by_reason["CIRCUIT_BREAKER"] += 1
                continue

            # Check 1: Wealth Priority Rule (Block Daily Builder if Wealth already alerts on same symbol)
            if scn == "DAILY_BUILDER" and sym in wealth_symbols_today:
                rejections_by_reason["WEALTH_PRIORITY_BUILDER_BLOCK"] += 1
                continue

            # Check 2: Per-Symbol Cap (Max 1.5R active on same symbol)
            if day_symbol_exposure.get(sym, 0.0) + w > 1.5:
                rejections_by_reason["SYMBOL_CAP"] += 1
                continue

            # Check 3: Per-Sector Cap (Max 4.0R active in same sector)
            if day_sector_exposure.get(sec, 0.0) + w > 4.0:
                rejections_by_reason["SECTOR_CAP"] += 1
                continue

            # Check 4: Scanner Active Positions Cap (Max 5 per day)
            if day_scanner_counts.get(scn, 0) >= 5:
                rejections_by_reason["SCANNER_POS_CAP"] += 1
                continue

            # Trade Approved and Executed
            day_symbol_exposure[sym] = day_symbol_exposure.get(sym, 0.0) + w
            day_sector_exposure[sec] = day_sector_exposure.get(sec, 0.0) + w
            day_scanner_counts[scn] = day_scanner_counts.get(scn, 0) + 1

            executed_trades.append(tr)
            daily_v59_returns[d] += net_r

            if net_r < 0:
                day_realized_loss += net_r
                if day_realized_loss <= -3.0:
                    circuit_tripped = True

    portfolio_df = pd.DataFrame({
        "date": all_dates,
        "net_r": [daily_v59_returns[d] for d in all_dates]
    })
    portfolio_df["partition_v59"] = np.where(portfolio_df["date"] < "2026-01-01", "CALIBRATION_DEV",
                                    np.where(portfolio_df["date"] < "2026-06-01", "TUNING_VAL",
                                             "LOCKED_REPRODUCTION_HISTORICAL"))

    print(f"Total Evaluated Signals: {len(trade_stream)}")
    print(f"Executed Risk-Controlled Trades: {len(executed_trades)} ({len(executed_trades)/len(trade_stream)*100:.1f}% Acceptance)")
    print("\nTrade Rejection Breakdown by Hard Risk Rule:")
    for reason, count in rejections_by_reason.items():
        print(f"  • {reason:<32}: {count:>5} trades blocked ({count/len(trade_stream)*100:.2f}%)")

    print("\n" + "=" * 125)
    print(f"{'EPISTEMOLOGICAL PARTITION':<35} | {'TOTAL NET R':>12} | {'MEAN R/DAY':>11} | {'SHARPE':>7} | {'SORTINO':>8} | {'MAX DD':>8} | {'CALMAR':>7}")
    print("-" * 105)

    v59_partition_perf = {}
    for part in ["CALIBRATION_DEV", "TUNING_VAL", "LOCKED_REPRODUCTION_HISTORICAL", "FULL_SAMPLE"]:
        p_sub = portfolio_df if part == "FULL_SAMPLE" else portfolio_df[portfolio_df["partition_v59"] == part]
        p_res = calc_performance_summary(p_sub["net_r"].values)
        tot_r = round(float(p_sub["net_r"].sum()), 1)
        calmar = round(tot_r / max(p_res["max_dd_r"], 1.0), 2)

        v59_partition_perf[part] = {
            "total_net_r": tot_r,
            "calmar": calmar,
            **p_res
        }

        print(f"{part:<35} | {tot_r:>+11.1f}R | {p_res['er']:>+10.2f}R | {p_res['sharpe']:>7.2f} | {p_res['sortino']:>8.2f} | {p_res['max_dd_r']:>7.1f}R | {calmar:>7.2f}")

    v59_master_output = {
        "system_status": "PRE-LIVE ENGINEERING CERTIFIED -> AWAITING FRESH FORWARD NET CERTIFICATION",
        "scanner_certification": scanner_stats,
        "portfolio_performance": v59_partition_perf,
        "rejections": rejections_by_reason
    }

    out_file = os.path.join(_REPORTS_DIR, "v59_fresh_forward_certification_results.json")
    with open(out_file, "w") as f:
        json.dump(v59_master_output, f, indent=2)
    print(f"\nSaved V5.9 Fresh Forward Certification results to: {out_file}")

if __name__ == "__main__":
    run_v59_certification()
