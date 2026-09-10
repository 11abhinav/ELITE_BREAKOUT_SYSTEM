#!/usr/bin/env python3
# =============================================================================
# tests/simulate_portfolio_allocation.py
# MASTER 5-SCANNER PORTFOLIO RISK & ALLOCATION SIMULATOR
# =============================================================================
#
# RULE 67 CHANGE-RATIONALE:
# Empirically evaluates multi-scanner portfolio risk dynamics across all 14 months
# (July 2025 -> September 2026):
#   1. Cross-scanner correlation matrix across realized daily R-returns
#   2. Alert overlap & same-symbol/same-day co-alert consolidation
#   3. Concurrent position limits & portfolio-level risk capacity
#   4. Multi-regime portfolio drawdown curves & Sharpe/Sortino comparison
#   5. Evaluates whether 50/25/25 governance tiers outperform Flat Risk & Regime-Adaptive
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_REPORTS_DIR = os.path.join(_REPO_ROOT, "reports")
_OUTPUT_JSON = os.path.join(_REPORTS_DIR, "portfolio_simulation_results.json")

# Candidate best-known variants to include from all 11 scanner families
ACTIVE_VARIANTS = {
    "EOD_BREAKOUT": "EOD_CHALL_G_SWEET_SPOT",
    "PULLBACK": "PULLBACK_CHALL_G_VOL_DRY_HYBRID",
    "ACCUMULATION_VCP": "VCP_CHALL_G_RUNNER_EXPANSION",
    "MULTITF": "MULTITF_V3B_TIGHT_VCP",
    "REVERSAL": "REV_V23D_15D_MULTI_REGIME",
    "WEALTH": "WEALTH_CHALL_A_QUALITY_MOMENTUM",
    "MULTIBAGGER": "MBAG_V2A_CONVEX_CAT_90D_T4R",
    "DAILY_BUILDER": "DAILY_BUILDER_CHALL_A_PRISTINE_BASE",
    "SHORT_COVERING_EOD": "SHORT_COVERING_CHALL_A_SQUEEZE_CONFIRMED",
    "MULTI_TF_5M": "MULTITF_5M_CHALL_A_TIGHT_COIL_IGNITION",
    "TECHNICAL": "TECHNICAL_CHALL_A_HIGH_CONFLUENCE",
}

# Allocation Strategies across all 11 scanners: {Strategy_Name: {Family: risk_weight_in_R}}
ALLOCATION_STRATEGIES = {
    "Strategy_1_Flat_Equal_Risk_11": {
        "EOD_BREAKOUT": 1.0,
        "PULLBACK": 1.0,
        "ACCUMULATION_VCP": 1.0,
        "MULTITF": 1.0,
        "REVERSAL": 1.0,
        "WEALTH": 1.0,
        "MULTIBAGGER": 1.0,
        "DAILY_BUILDER": 1.0,
        "SHORT_COVERING_EOD": 1.0,
        "MULTI_TF_5M": 1.0,
        "TECHNICAL": 1.0,
    },
    "Strategy_2_Staged_Governance_11": {
        "EOD_BREAKOUT": 1.0,          # Tier 1: Core Production
        "ACCUMULATION_VCP": 0.80,      # Tier 2: Validated Edge
        "PULLBACK": 0.75,             # Tier 2: Validated Edge
        "WEALTH": 0.75,               # Tier 2: Multi-Month Compounding
        "SHORT_COVERING_EOD": 0.50,   # Tier 3: Restricted / Opportunistic
        "TECHNICAL": 0.50,            # Tier 3: Pattern Confluence
        "DAILY_BUILDER": 0.50,        # Tier 3: Pre-Filter Setup
        "MULTITF": 0.0,               # Tier 3: Candidate Research (Paper trade)
        "REVERSAL": 0.0,              # Tier 3: Candidate Research (Paper trade)
        "MULTIBAGGER": 0.0,           # Tier 3: Candidate Research (Paper trade)
        "MULTI_TF_5M": 0.0,           # Tier 3: Candidate Research (Paper trade)
    },
    "Strategy_3_Core_Multi_Horizon_Blend": {
        "EOD_BREAKOUT": 1.0,
        "ACCUMULATION_VCP": 0.80,
        "PULLBACK": 0.75,
        "WEALTH": 0.80,
        "SHORT_COVERING_EOD": 0.60,
        "TECHNICAL": 0.60,
        "DAILY_BUILDER": 0.50,
        "MULTITF": 0.0,
        "REVERSAL": 0.0,
        "MULTIBAGGER": 0.0,
        "MULTI_TF_5M": 0.0,
    },
    "Strategy_4_Regime_Adaptive_11": {
        "BULL": {
            "EOD_BREAKOUT": 1.0,
            "ACCUMULATION_VCP": 0.90,
            "PULLBACK": 0.70,
            "WEALTH": 1.0,
            "MULTIBAGGER": 0.50,
            "MULTITF": 0.50,
            "MULTI_TF_5M": 0.40,
            "TECHNICAL": 0.70,
            "DAILY_BUILDER": 0.60,
            "SHORT_COVERING_EOD": 0.30,
            "REVERSAL": 0.20,
        },
        "NEUTRAL": {
            "EOD_BREAKOUT": 0.50,
            "ACCUMULATION_VCP": 0.60,
            "PULLBACK": 0.80,
            "WEALTH": 0.70,
            "MULTIBAGGER": 0.0,
            "MULTITF": 0.0,
            "MULTI_TF_5M": 0.0,
            "TECHNICAL": 0.50,
            "DAILY_BUILDER": 0.50,
            "SHORT_COVERING_EOD": 0.70,
            "REVERSAL": 0.60,
        },
        "BEAR": {
            "EOD_BREAKOUT": 0.0,
            "ACCUMULATION_VCP": 0.0,
            "PULLBACK": 0.0,
            "WEALTH": 0.20,
            "MULTIBAGGER": 0.0,
            "MULTITF": 0.0,
            "MULTI_TF_5M": 0.0,
            "TECHNICAL": 0.0,
            "DAILY_BUILDER": 0.0,
            "SHORT_COVERING_EOD": 0.60,
            "REVERSAL": 0.30,
        },
    },
}


def load_all_scanner_outcomes() -> pd.DataFrame:
    """Loads and standardizes outcomes across all 11 scanner outcome CSVs."""
    dfs = []

    file_scanner_mapping = [
        ("EOD_BREAKOUT", ["eod_breakout_outcomes.csv"]),
        ("PULLBACK", ["pullback_v2_ablation_outcomes.csv", "pullback_outcomes.csv"]),
        ("ACCUMULATION_VCP", ["vcp_outcomes.csv"]),
        ("MULTITF", ["multitf_v3_outcomes.csv", "multitf_1h_expansion_outcomes.csv", "multitf_outcomes.csv"]),
        ("REVERSAL", ["reversal_expansion_outcomes.csv", "reversal_v1_vs_v2_outcomes.csv"]),
        ("WEALTH", ["wealth_outcomes.csv"]),
        ("MULTIBAGGER", ["multibagger_expansion_outcomes.csv", "multibagger_outcomes.csv"]),
        ("DAILY_BUILDER", ["daily_builder_outcomes.csv"]),
        ("SHORT_COVERING_EOD", ["short_covering_outcomes.csv"]),
        ("MULTI_TF_5M", ["multitf_5m_outcomes.csv"]),
        ("TECHNICAL", ["technical_outcomes.csv"]),
    ]

    for sc_name, fnames in file_scanner_mapping:
        loaded = False
        for fname in fnames:
            fpath = os.path.join(_REPORTS_DIR, fname)
            if not os.path.exists(fpath):
                continue
            try:
                df = pd.read_csv(fpath)
                target_vid = ACTIVE_VARIANTS.get(sc_name)
                if target_vid in df["variant_id"].values:
                    sub = df[df["variant_id"] == target_vid].copy()
                    sub["scanner"] = sc_name
                    dfs.append(sub)
                    loaded = True
                    break
                elif not loaded and fname == fnames[-1]:
                    sub = df[df["variant_id"] == df["variant_id"].iloc[0]].copy()
                    sub["scanner"] = sc_name
                    dfs.append(sub)
                    loaded = True
                    break
            except Exception as e:
                print(f"Warning: failed to load {fname}: {e}")

    if not dfs:
        raise RuntimeError("No scanner outcome CSVs found in reports directory!")

    combined = pd.concat(dfs, ignore_index=True)
    combined["scan_date"] = pd.to_datetime(combined["scan_date"]).dt.strftime("%Y-%m-%d")
    combined = combined.sort_values("scan_date").reset_index(drop=True)
    return combined


def simulate_portfolio(
    alerts_df: pd.DataFrame,
    strategy_name: str,
    max_concurrent_positions: int = 10,
    max_portfolio_risk_r: float = 5.0,
    consolidate_same_day_co_alerts: bool = True,
) -> Dict[str, Any]:
    """
    Simulates chronological portfolio execution across time:
    - Tracks active positions, realizes exits, applies risk weighting,
    - Enforces co-alert deduplication (EOD + VCP on same symbol/day consolidated into 1 trade),
    - Enforces max concurrent position and risk limits.
    """
    alerts_by_date = defaultdict(list)
    for row in alerts_df.to_dict("records"):
        alerts_by_date[row["scan_date"]].append(row)
    dates = sorted(alerts_by_date.keys())
    open_positions: List[Dict[str, Any]] = []
    closed_trades: List[Dict[str, Any]] = []
    daily_equity_curve: List[Dict[str, Any]] = []

    cumulative_realized_r = 0.0
    peak_r = 0.0
    max_drawdown_r = 0.0
    co_alert_dedup_count = 0

    strategy_weights = ALLOCATION_STRATEGIES[strategy_name]

    for d in dates:
        d_str = str(d)
        day_records = alerts_by_date[d]

        # 1. Update Open Positions & Process Exits
        still_open = []
        for pos in open_positions:
            # Check if position has reached its actual exit date
            if pos["actual_exit_date"] <= d_str:
                # Position closed
                realized_trade_r = pos["realized_rr"] * pos["risk_r"]
                cumulative_realized_r += realized_trade_r
                closed_trades.append({
                    **pos,
                    "sim_exit_date": d_str,
                    "final_realized_r": round(realized_trade_r, 3),
                })
            else:
                still_open.append(pos)
        open_positions = still_open

        # 2. Process New Alerts on Date d
        # Group by symbol to detect co-alerts
        alerts_by_symbol = defaultdict(list)
        for alert in day_records:
            alerts_by_symbol[alert["symbol"]].append(alert)

        for sym, sym_alerts in alerts_by_symbol.items():
            scanners_present = [a["scanner"] for a in sym_alerts]

            # Determine risk weight
            if "Strategy_4_Regime_Adaptive" in strategy_name:
                regime = sym_alerts[0].get("regime", "NEUTRAL")
                weights_table = strategy_weights.get(regime, strategy_weights["NEUTRAL"])
            else:
                weights_table = strategy_weights

            if len(sym_alerts) > 1 and consolidate_same_day_co_alerts:
                # Same-day co-alert (e.g. EOD + VCP)
                co_alert_dedup_count += 1
                # Risk consolidation rule: Take the highest allocated risk among co-alerts, capped at 1.0R
                max_risk = max(weights_table.get(sc, 0.0) for sc in scanners_present)
                allocated_risk = min(1.0, max_risk)
                primary_alert = sym_alerts[0]  # Representative alert
            else:
                primary_alert = sym_alerts[0]
                sc = primary_alert["scanner"]
                allocated_risk = weights_table.get(sc, 0.0)

            if allocated_risk <= 0.0:
                continue  # 0% capital allocation (paper trade or filtered)

            # Check concurrent position and portfolio risk capacity
            current_total_risk = sum(p["risk_r"] for p in open_positions)
            if len(open_positions) >= max_concurrent_positions:
                continue  # Slot capacity full
            if current_total_risk + allocated_risk > max_portfolio_risk_r:
                allocated_risk = max(0.0, max_portfolio_risk_r - current_total_risk)
                if allocated_risk < 0.10:
                    continue  # Inadequate risk headroom

            # Enter position
            holding_b = max(1, int(primary_alert.get("holding_period_bars", 10)))
            if "exit_date" in primary_alert and pd.notna(primary_alert["exit_date"]):
                exit_dt = str(pd.to_datetime(primary_alert["exit_date"]).date())
            else:
                exit_dt = str((pd.to_datetime(d) + pd.Timedelta(days=int(holding_b * 1.45))).date())

            open_positions.append({
                "symbol": sym,
                "entry_date": d_str,
                "actual_exit_date": exit_dt,
                "scanner": primary_alert["scanner"],
                "all_scanners": "+".join(scanners_present),
                "variant_id": primary_alert["variant_id"],
                "risk_r": allocated_risk,
                "realized_rr": float(primary_alert.get("realized_rr", 0.0)),
                "holding_bars": holding_b,
                "regime": primary_alert.get("regime", "NEUTRAL"),
                "partition": primary_alert.get("partition", "OOS"),
            })

        # 3. Track Daily Portfolio State & Drawdown
        if cumulative_realized_r > peak_r:
            peak_r = cumulative_realized_r
        current_dd_r = peak_r - cumulative_realized_r
        if current_dd_r > max_drawdown_r:
            max_drawdown_r = current_dd_r

        daily_equity_curve.append({
            "date": d_str,
            "cumulative_r": round(cumulative_realized_r, 3),
            "open_positions": len(open_positions),
            "open_risk_r": round(sum(p["risk_r"] for p in open_positions), 2),
            "drawdown_r": round(current_dd_r, 3),
        })

    # Close remaining open positions at simulation end
    for pos in open_positions:
        realized_trade_r = pos["realized_rr"] * pos["risk_r"]
        cumulative_realized_r += realized_trade_r
        closed_trades.append({
            **pos,
            "exit_date": "END_OF_SIM",
            "final_realized_r": round(realized_trade_r, 3),
        })

    # Compute Aggregate Metrics
    df_trades = pd.DataFrame(closed_trades)
    n_trades = len(df_trades)
    if n_trades == 0:
        return {"strategy_name": strategy_name, "total_r": 0.0, "trades": 0}

    total_r = float(df_trades["final_realized_r"].sum())
    wins = df_trades[df_trades["final_realized_r"] > 0]["final_realized_r"]
    losses = abs(df_trades[df_trades["final_realized_r"] < 0]["final_realized_r"])

    gross_profit = float(wins.sum()) if len(wins) > 0 else 0.0
    gross_loss = float(losses.sum()) if len(losses) > 0 else 0.0
    pf = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
    win_rate = (len(wins) / n_trades) * 100.0

    # Partition breakdowns
    holdout_trades = df_trades[df_trades["partition"] == "HOLDOUT"]
    hld_n = len(holdout_trades)
    hld_r = float(holdout_trades["final_realized_r"].sum()) if hld_n > 0 else 0.0
    hld_wins = holdout_trades[holdout_trades["final_realized_r"] > 0]["final_realized_r"]
    hld_losses = abs(holdout_trades[holdout_trades["final_realized_r"] < 0]["final_realized_r"])
    hld_pf = (float(hld_wins.sum()) / float(hld_losses.sum())) if float(hld_losses.sum()) > 0 else (99.0 if float(hld_wins.sum()) > 0 else 0.0)

    val_trades = df_trades[df_trades["partition"] == "VAL"]
    val_n = len(val_trades)
    val_r = float(val_trades["final_realized_r"].sum()) if val_n > 0 else 0.0
    val_wins = val_trades[val_trades["final_realized_r"] > 0]["final_realized_r"]
    val_losses = abs(val_trades[val_trades["final_realized_r"] < 0]["final_realized_r"])
    val_pf = (float(val_wins.sum()) / float(val_losses.sum())) if float(val_losses.sum()) > 0 else (99.0 if float(val_wins.sum()) > 0 else 0.0)

    # Calmar-like R ratio: Total R / Max Drawdown R
    r_calmar = (total_r / max_drawdown_r) if max_drawdown_r > 0 else total_r

    return {
        "strategy_name": strategy_name,
        "total_trades": n_trades,
        "total_realized_r": round(total_r, 2),
        "profit_factor": round(pf, 2),
        "win_rate_pct": round(win_rate, 1),
        "max_drawdown_r": round(max_drawdown_r, 2),
        "return_to_drawdown_ratio": round(r_calmar, 2),
        "val_trades": val_n,
        "val_realized_r": round(val_r, 2),
        "val_profit_factor": round(val_pf, 2),
        "holdout_trades": hld_n,
        "holdout_realized_r": round(hld_r, 2),
        "holdout_profit_factor": round(hld_pf, 2),
        "co_alerts_consolidated": co_alert_dedup_count,
        "avg_trade_r": round(total_r / n_trades, 3),
    }


def compute_cross_scanner_correlation(alerts_df: pd.DataFrame) -> pd.DataFrame:
    """Computes daily realized R-return correlation across scanner families."""
    # Pivot to date x scanner daily sum of realized R
    daily_scanner_pnl = alerts_df.groupby(["scan_date", "scanner"])["realized_rr"].sum().unstack(fill_value=0.0)
    corr_matrix = daily_scanner_pnl.corr().round(3)
    return corr_matrix


def run_simulation():
    print("=" * 110)
    print("MASTER 11-SCANNER THREE-WAY HOLDOUT PORTFOLIO RISK & ALLOCATION SIMULATION")
    print("=" * 110)

    alerts_df = load_all_scanner_outcomes()
    print(f"Loaded {len(alerts_df)} total alert outcomes across all 11 scanner families.")
    print(f"Date range: {alerts_df['scan_date'].min().date()} -> {alerts_df['scan_date'].max().date()}")

    # 1. Scanner Cross-Correlation Matrix
    print("\n" + "-" * 110)
    print("1. CROSS-SCANNER REALIZED R CORRELATION MATRIX (11 x 11)")
    print("-" * 110)
    corr_matrix = compute_cross_scanner_correlation(alerts_df)
    print(corr_matrix.to_string())

    # 2. Simulate Allocation Strategies
    print("\n" + "-" * 110)
    print("2. PORTFOLIO STRATEGY SIMULATION RESULTS (DEV / VAL / LOCKED HOLDOUT)")
    print("-" * 110)

    results = {}
    hdr = f"  {'Strategy Name':<35} {'Trades':>6} {'Tot R':>8} {'PF':>6} {'MaxDD(R)':>8} {'VAL R':>8} {'VAL PF':>7} {'HLD R':>8} {'HLD PF':>7}"
    print(hdr)
    print("  " + "-" * 105)

    for strat in ALLOCATION_STRATEGIES:
        res = simulate_portfolio(alerts_df, strategy_name=strat)
        results[strat] = res
        print(f"  {strat:<35} {res['total_trades']:>6} {res['total_realized_r']:>+8.2f} {res['profit_factor']:>6.2f} {res['max_drawdown_r']:>8.2f} {res['val_realized_r']:>+8.2f} {res['val_profit_factor']:>7.2f} {res['holdout_realized_r']:>+8.2f} {res['holdout_profit_factor']:>7.2f}")

    # Save to JSON
    with open(_OUTPUT_JSON, "w") as f:
        json.dump({
            "correlation_matrix": corr_matrix.to_dict(),
            "simulation_results": results,
        }, f, indent=2)
    print(f"\nSaved portfolio simulation output to {_OUTPUT_JSON}")


if __name__ == "__main__":
    run_simulation()
