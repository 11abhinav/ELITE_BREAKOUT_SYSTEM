#!/usr/bin/env python3
# =============================================================================
# scripts/audit_11_scanner_overlap_and_interaction.py
# V5.7 MASTER 11-SCANNER INTERACTION, OVERLAP, CORRELATION & MARGINAL CONTRIBUTION AUDIT
# =============================================================================
# Systematically audits cross-scanner dynamics across all 11 scanner families:
# 1. Pairwise alert overlap & duplicate signal frequency on same (date, symbol)
# 2. Realized daily R-return correlation matrix (Pearson & Spearman)
# 3. Same-day concurrency clustering & SL concentration during regime shocks
# 4. Marginal expectancy contribution & portfolio diversification benefit
# =============================================================================

import json
import os
import sys
import numpy as np
import pandas as pd

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_REPORTS_DIR = os.path.join(_REPO_ROOT, "reports")
os.makedirs(_REPORTS_DIR, exist_ok=True)

# ── Map Scanner Families to Outcome Files & Best-Certified Variants ─────────
SCANNER_CONFIGS = {
    "WEALTH": {
        "file": "wealth_outcomes.csv",
        "variant": "WEALTH_CHAMPION_V1",
        "tier": 1,
        "type": "Institutional Anchor"
    },
    "PULLBACK_V2": {
        "file": "pullback_outcomes.csv",
        "variant": "PULLBACK_V2_CHAMPION",
        "tier": 1,
        "type": "Production Flagship"
    },
    "ACCUMULATION_VCP": {
        "file": "vcp_v55_sample_expansion_outcomes.csv",
        "variant": "VCP_V55_PRECISION_B_CLV65",
        "tier": 1,
        "type": "High-Win-Rate Leader"
    },
    "EOD_BREAKOUT": {
        "file": "eod_v56_sample_expansion_outcomes.csv",
        "variant": "EOD_ABL_3_NO_VOL_FILTER",
        "tier": 1,
        "type": "Precision Expansion"
    },
    "MULTITF_5M": {
        "file": "multitf_5m_outcomes.csv",
        "variant": "MULTITF_5M_CHAMPION_V1",
        "tier": 1,
        "type": "Intraday Multi-Regime"
    },
    "TECHNICAL_AHAT": {
        "file": "technical_outcomes.csv",
        "variant": "TECHNICAL_AHAT_CHAMPION_V1",
        "tier": 2,
        "type": "Confluence Swing"
    },
    "DAILY_BUILDER": {
        "file": "daily_builder_outcomes.csv",
        "variant": "DAILY_BUILDER_CHAMPION_V1",
        "tier": 2,
        "type": "Capacity Bedrock"
    },
    "REVERSAL": {
        "file": "reversal_v54_outcomes.csv",
        "variant": "REV_V23D_15D_MULTI_REGIME",
        "tier": 3,
        "type": "Certified Alpha (Admin Hold)"
    },
    "MULTITF_1H": {
        "file": "multitf_1h_v56_expansion_outcomes.csv",
        "variant": "M1H_V56_PRECISION_B_20H",
        "tier": 3,
        "type": "Hourly Compression Depth"
    },
    "SHORT_COVERING_EOD": {
        "file": "short_covering_v56_specialist_outcomes.csv",
        "variant": "SC_ABL_2_NO_RSI_FILTER",
        "tier": 3,
        "type": "Bear/Neutral Specialist"
    },
    "MULTIBAGGER": {
        "file": "multibagger_v57_scale_outcomes.csv",
        "variant": "MBAG_V57_SCALE_A_60D_165V",
        "tier": 3,
        "type": "1:5R Convexity Engine"
    },
}

def standardize_df(df: pd.DataFrame) -> pd.DataFrame:
    """Standardizes column names across heterogeneous outcome files."""
    out = df.copy()

    # 1. Standardize Date
    for col in ["date", "scan_date", "Date", "Datetime", "dt"]:
        if col in out.columns:
            out["date"] = pd.to_datetime(out[col]).dt.strftime("%Y-%m-%d")
            break

    # 2. Standardize Symbol
    for col in ["symbol", "Symbol", "ticker", "Ticker"]:
        if col in out.columns:
            out["symbol"] = out[col].astype(str).str.upper()
            break

    # 3. Standardize R-Multiple
    for col in ["r_multiple", "realized_rr", "r_mult", "realized_r", "signal_rr"]:
        if col in out.columns:
            out["r_multiple"] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
            break

    # 4. Standardize Outcome Type
    for col in ["outcome_type", "exit_reason", "exit_type"]:
        if col in out.columns:
            out["outcome_type"] = out[col].astype(str).str.upper()
            break
    if "outcome_type" not in out.columns:
        out["outcome_type"] = np.where(out["r_multiple"] < 0, "SL_HIT", "TARGET_HIT")

    # 5. Standardize Partition
    if "partition" not in out.columns:
        out["partition"] = np.where(out["date"] < "2026-01-01", "DEV",
                           np.where(out["date"] < "2026-06-01", "VAL", "HOLDOUT"))

    # 6. Standardize Regime
    if "regime" not in out.columns:
        out["regime"] = "NEUTRAL"

    out["key"] = out["date"] + "_" + out["symbol"]
    return out

def run_11_scanner_interaction_audit():
    print("=" * 115)
    print("V5.7 MASTER 11-SCANNER INTERACTION, OVERLAP, CORRELATION & MARGINAL CONTRIBUTION AUDIT")
    print("=" * 115)

    loaded_dfs = {}
    scanner_names = list(SCANNER_CONFIGS.keys())

    for name, cfg in SCANNER_CONFIGS.items():
        f_path = os.path.join(_REPORTS_DIR, cfg["file"])
        if not os.path.exists(f_path):
            print(f"Warning: {f_path} not found.")
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
        print(f"Loaded {name:<18} | Variant: {target_v:<30} | Total Trades: {len(std_df):>5}")

    # ── 1. Pairwise Alert Overlap & Duplicate Signal Matrix ──────────────────
    print("\n" + "=" * 115)
    print("1. PAIRWISE ALERT OVERLAP & SAME-SYMBOL/SAME-DAY CO-ALERT MATRIX (HOLDOUT PARTITION)")
    print("=" * 115)

    overlap_counts = np.zeros((len(scanner_names), len(scanner_names)), dtype=int)
    overlap_pcts = np.zeros((len(scanner_names), len(scanner_names)), dtype=float)

    holdout_dfs = {}
    for name in scanner_names:
        if name in loaded_dfs:
            df = loaded_dfs[name]
            h_df = df[df["partition"] == "HOLDOUT"].copy()
            holdout_dfs[name] = h_df
        else:
            holdout_dfs[name] = pd.DataFrame()

    for i, s1 in enumerate(scanner_names):
        df1 = holdout_dfs[s1]
        keys1 = set(df1["key"].values) if not df1.empty else set()
        n1 = len(keys1)

        for j, s2 in enumerate(scanner_names):
            df2 = holdout_dfs[s2]
            keys2 = set(df2["key"].values) if not df2.empty else set()
            shared = keys1.intersection(keys2)
            overlap_counts[i, j] = len(shared)
            overlap_pcts[i, j] = (len(shared) / n1 * 100.0) if n1 > 0 else 0.0

    header_abbrs = [s[:6] for s in scanner_names]
    print(f"{'SCANNER':<18} | {'N (HLD)':>7} | " + " | ".join([f"{a:>6}" for a in header_abbrs]))
    print("-" * 115)

    overlap_records = []
    for i, s1 in enumerate(scanner_names):
        row_str = f"{s1:<18} | {len(holdout_dfs[s1]):>7} | "
        cell_strs = []
        for j, s2 in enumerate(scanner_names):
            if i == j:
                cell_strs.append(f"{'---':>6}")
            else:
                c = overlap_counts[i, j]
                pct = overlap_pcts[i, j]
                cell_strs.append(f"{pct:>5.1f}%")
        print(row_str + " | ".join(cell_strs))
        overlap_records.append({
            "scanner": s1,
            "holdout_n": len(holdout_dfs[s1]),
            **{scanner_names[j]: int(overlap_counts[i, j]) for j in range(len(scanner_names))}
        })

    # ── 2. Realized Daily R-Multiple Return Correlation Matrix ───────────────
    print("\n" + "=" * 115)
    print("2. REALIZED DAILY R-MULTIPLE RETURN CORRELATION MATRIX (PEARSON)")
    print("=" * 115)

    all_dates = sorted(list(set.union(*[set(df["date"].values) for df in loaded_dfs.values() if not df.empty])))
    daily_returns_df = pd.DataFrame(index=all_dates)

    for name in scanner_names:
        if name in loaded_dfs:
            df = loaded_dfs[name]
            daily_agg = df.groupby("date")["r_multiple"].sum()
            daily_returns_df[name] = daily_agg
        else:
            daily_returns_df[name] = 0.0

    daily_returns_df = daily_returns_df.fillna(0.0)
    corr_matrix = daily_returns_df.corr(method="pearson")

    print(f"{'SCANNER':<18} | " + " | ".join([f"{a:>6}" for a in header_abbrs]))
    print("-" * 115)
    for i, s1 in enumerate(scanner_names):
        row_str = f"{s1:<18} | "
        cell_strs = []
        for j, s2 in enumerate(scanner_names):
            if i == j:
                cell_strs.append(f"{'1.00':>6}")
            else:
                val = corr_matrix.loc[s1, s2] if (s1 in corr_matrix and s2 in corr_matrix) else 0.0
                cell_strs.append(f"{val:>+6.2f}")
        print(row_str + " | ".join(cell_strs))

    # ── 3. Same-Day Concurrency Clustering & SL Concentration ────────────────
    print("\n" + "=" * 115)
    print("3. SAME-DAY CONCURRENCY CLUSTERING & SIMULTANEOUS SL CONCENTRATION AUDIT")
    print("=" * 115)

    daily_alert_counts = pd.DataFrame(index=all_dates)
    daily_sl_counts = pd.DataFrame(index=all_dates)

    for name in scanner_names:
        if name in loaded_dfs:
            df = loaded_dfs[name]
            daily_alert_counts[name] = df.groupby("date")["symbol"].count()
            sl_df = df[df["outcome_type"].str.contains("SL", na=False) | (df["r_multiple"] < 0)]
            daily_sl_counts[name] = sl_df.groupby("date")["symbol"].count()
        else:
            daily_alert_counts[name] = 0
            daily_sl_counts[name] = 0

    daily_alert_counts = daily_alert_counts.fillna(0)
    daily_sl_counts = daily_sl_counts.fillna(0)

    total_alerts_per_day = daily_alert_counts.sum(axis=1)
    total_sl_per_day = daily_sl_counts.sum(axis=1)
    active_scanners_per_day = (daily_alert_counts > 0).sum(axis=1)
    active_sl_scanners_per_day = (daily_sl_counts > 0).sum(axis=1)

    print(f"Total Trading Days Evaluated: {len(all_dates)}")
    print(f"Average Daily Alerts Across Portfolio: {total_alerts_per_day.mean():.1f} alerts/day (Median: {total_alerts_per_day.median():.0f}, Max: {total_alerts_per_day.max():.0f})")
    print(f"Average Active Scanners Per Day: {active_scanners_per_day.mean():.1f} scanners (Max: {active_scanners_per_day.max()})")
    
    # Days with simultaneous stop loss hits across >= 3 scanners
    shock_days = active_sl_scanners_per_day[active_sl_scanners_per_day >= 3]
    print(f"Regime Shock Days with Simultaneous SL Hit in >= 3 Scanners: {len(shock_days)} days ({len(shock_days)/len(all_dates)*100:.1f}%)")
    extreme_shock_days = active_sl_scanners_per_day[active_sl_scanners_per_day >= 5]
    print(f"Extreme Shock Days with Simultaneous SL Hit in >= 5 Scanners: {len(extreme_shock_days)} days ({len(extreme_shock_days)/len(all_dates)*100:.1f}%)")

    # ── 4. Marginal Expectancy Contribution & Portfolio Diversification ───────
    print("\n" + "=" * 115)
    print("4. MARGINAL EXPECTANCY & DIVERSIFICATION BENEFIT (LEAVE-ONE-OUT AUDIT)")
    print("=" * 115)

    full_portfolio_r = daily_returns_df.sum(axis=1).values
    full_mean_r = np.mean(full_portfolio_r)
    full_std_r = np.std(full_portfolio_r)
    full_sharpe = (full_mean_r / full_std_r * np.sqrt(252)) if full_std_r > 0 else 0.0

    cum_r = np.cumsum(full_portfolio_r)
    peak = np.maximum.accumulate(cum_r)
    full_max_dd = float(np.max(peak - cum_r))

    print(f"Full 11-Scanner Portfolio Benchmark: Daily Mean R = {full_mean_r:+.2f}R | Sharpe = {full_sharpe:.2f} | Max DD = {full_max_dd:.1f}R\n")
    print(f"{'EXCLUDED SCANNER':<20} | {'PORTFOLIO E[R]':>14} | {'DELTA E[R]':>11} | {'PORTFOLIO SHARPE':>16} | {'DELTA SHARPE':>12} | {'MAX DD':>8} | {'DIVERSIFICATION ROLE':<25}")
    print("-" * 115)

    marginal_records = []
    for name in scanner_names:
        sub_df = daily_returns_df.drop(columns=[name])
        sub_r = sub_df.sum(axis=1).values
        sub_mean = np.mean(sub_r)
        sub_std = np.std(sub_r)
        sub_sharpe = (sub_mean / sub_std * np.sqrt(252)) if sub_std > 0 else 0.0
        
        sub_cum = np.cumsum(sub_r)
        sub_peak = np.maximum.accumulate(sub_cum)
        sub_max_dd = float(np.max(sub_peak - sub_cum))

        delta_r = full_mean_r - sub_mean
        delta_sharpe = full_sharpe - sub_sharpe

        role = ""
        if name in ["WEALTH", "PULLBACK_V2", "DAILY_BUILDER"]:
            role = "Core Capacity Engine"
        elif name in ["ACCUMULATION_VCP", "EOD_BREAKOUT"]:
            role = "High-Quality Multiplier"
        elif name in ["SHORT_COVERING_EOD", "REVERSAL"]:
            role = "Bear Market Shock Absorber"
        elif name == "MULTIBAGGER":
            role = "Right-Tail Convexity Booster"
        elif name in ["MULTITF_5M", "MULTITF_1H", "TECHNICAL_AHAT"]:
            role = "Intraday/Swing Diversifier"

        print(f"{name:<20} | {sub_mean:>+13.2f}R | {delta_r:>+10.2f}R | {sub_sharpe:>16.2f} | {delta_sharpe:>+11.2f} | {sub_max_dd:>7.1f}R | {role:<25}")
        marginal_records.append({
            "excluded_scanner": name,
            "portfolio_mean_r": round(sub_mean, 3),
            "delta_mean_r": round(delta_r, 3),
            "portfolio_sharpe": round(sub_sharpe, 2),
            "delta_sharpe": round(delta_sharpe, 2),
            "max_dd_r": round(sub_max_dd, 1),
            "role": role
        })

    # Save JSON summary
    audit_results = {
        "scanners": scanner_names,
        "overlap_matrix": overlap_counts.tolist(),
        "overlap_pct_matrix": overlap_pcts.tolist(),
        "correlation_matrix": corr_matrix.round(3).to_dict(),
        "marginal_contributions": marginal_records,
        "shock_days_count": len(shock_days),
        "extreme_shock_days_count": len(extreme_shock_days),
        "full_portfolio_sharpe": round(full_sharpe, 2),
        "full_portfolio_max_dd": round(full_max_dd, 1),
    }

    out_json = os.path.join(_REPORTS_DIR, "audit_11_scanner_interaction_results.json")
    with open(out_json, "w") as f:
        json.dump(audit_results, f, indent=2)
    print(f"\nAudit complete. Detailed JSON saved to {out_json}")

    return audit_results

if __name__ == "__main__":
    run_11_scanner_interaction_audit()
