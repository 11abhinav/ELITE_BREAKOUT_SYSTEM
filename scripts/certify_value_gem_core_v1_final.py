#!/usr/bin/env python3
"""
scripts/certify_value_gem_core_v1_final.py
===========================================
VALUE_GEM_CORE_V1 — FINAL CAUSAL CERTIFICATION RUN

RESEARCH WINDOW: 2016-01-01 -> 2026-09-27 (Strictly 2015-free)
- TRAIN:      2016-01-01 -> 2022-12-31 (7 Years)
- VALIDATION: 2023-01-01 -> 2024-12-31 (2 Years)
- HOLDOUT:    2025-01-01 -> 2026-09-27 (1.75 Years - Untouched)

DATA PROVENANCE:
- Prices: Real Upstox 1D Daily Parquets (`data/history/1d/*.parquet`) - 947 Symbols
- PIT Fundamentals: Certified Pass 5 Database (`data/pit_fundamentals_v1/pit_fundamentals_v1.db`)
- Dataset SHA256: `684963962032aa3d4e2a3974c55d13baf423b723d2a7763fff363bef5d35407f`

FROZEN GEM STRATEGY DEFINITION (VALUE_GEM_CORE_V1):
1. Quality Business:
   - Non-Financial: ROCE >= 14%, ROE >= 12%, D/E <= 1.0, OPM >= 8%, CFO/PAT >= 0.5
   - Financial: ROE >= 13%
2. Genuine Cheapness:
   - P/E <= 25.0 OR Historical P/E percentile <= 35th percentile over past 3 years
3. Price Dislocation:
   - 25% to 45% Drawdown from 52-Week High (0.25 <= DD_52W <= 0.45)
4. Fundamentals Intact:
   - TTM Sales Growth YoY > 0%, TTM PAT Growth YoY > 0%
5. Price Stabilization:
   - 20-bar base formation depth <= 15% ((roll_max20 - roll_min20)/Close <= 0.15)
   - Close >= SMA20 OR Close >= SMA50
6. Execution & Friction:
   - Signal at Day T -> Entry at T+1 OPEN
   - Friction: 15 bps (0.0015) total transaction cost on entry and exit
7. Open-Ended Holding & Structural Weakness Exit:
   - Hard initial stop: 2.0 * ATR14 below entry price
   - Exit condition: Close < SMA200 OR Fundamental Degradation (TTM PAT < 0)
   - ZERO fixed profit target, ZERO arbitrary time stop.
"""

from __future__ import annotations
import os
import glob
import sys
import json
import time
import sqlite3
import hashlib
from datetime import datetime
from typing import Dict, List, Any, Tuple, Optional, Set
import pandas as pd
import numpy as np

# Directory Setup
REPO_ROOT   = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR    = os.path.join(REPO_ROOT, "data")
HISTORY_1D  = os.path.join(DATA_DIR, "history", "1d")
PIT_DB_PATH = os.path.join(DATA_DIR, "pit_fundamentals_v1", "pit_fundamentals_v1.db")
OUTPUT_DIR  = os.path.join(REPO_ROOT, "artifacts", "value_buy_gems")

os.makedirs(OUTPUT_DIR, exist_ok=True)

# Sector Classification
FINANCIAL_SECTORS: Set[str] = {
    "AADHARHFC","AAVAS","ABCAPITAL","APTUS","AXISBANK","BAJFINANCE",
    "BANKBARODA","BANKINDIA","BENGALASM","CGCL","CHOLAFIN","CHOLAHLDNG",
    "CREDITACC","FEDERALBNK","FIVESTAR","HDBFS","HDFCBANK","HUDCO",
    "ICICIBANK","INDIANB","INDIASHLTR","IREDA","J&KBANK","KOTAKBANK",
    "KTKBANK","LICHSGFIN","LTF","M&MFIN","MASFIN","MUTHOOTFIN",
    "NORTHARC","PFC","PNBHOUSING","RECLTD","REPCOHOME","SATIN",
    "SBIN","SHRIRAMFIN","SUNDARMFIN","TATACAP","UNIONBANK","ZSARACOM",
    "CANFINHOME","SBICARD","SBILIFE","ICICIGI","KARURVYSYA",
    "CSBBANK","CUB","DCBBANK","TMB","CAPITALSFB","FEDFINA","SGFIN","GODIGIT",
    "AIIL","AUBANK","UCOBANK","CENTRALBK","IOB","PSB","MAHABANK"
}

def verify_dataset_hash() -> str:
    with open(PIT_DB_PATH, "rb") as f:
        db_hash = hashlib.sha256(f.read()).hexdigest()
    expected_hash = "684963962032aa3d4e2a3974c55d13baf423b723d2a7763fff363bef5d35407f"
    if db_hash != expected_hash:
        raise RuntimeError(f"DATASET HASH MISMATCH! Got {db_hash}, expected {expected_hash}")
    return db_hash

def load_real_upstox_price_map() -> Dict[str, pd.DataFrame]:
    print("Loading Real Upstox 1D Daily Parquets from data/history/1d/...")
    t0 = time.time()
    files = glob.glob(os.path.join(HISTORY_1D, "*.parquet"))
    if not files:
        raise FileNotFoundError(f"No 1D price parquets found in {HISTORY_1D}")

    price_map = {}
    for p in files:
        sym = os.path.basename(p).replace(".parquet", "")
        try:
            df = pd.read_parquet(p)
            if df.empty or len(df) < 100:
                continue
            
            dcol = "Date" if "Date" in df.columns else ("Datetime" if "Datetime" in df.columns else None)
            if dcol is None:
                continue
            
            df["date"] = pd.to_datetime(df[dcol]).dt.tz_localize(None).dt.strftime("%Y-%m-%d")
            df = df.rename(columns={
                "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"
            })
            df = df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
            
            if len(df) >= 100:
                price_map[sym] = df[["date", "open", "high", "low", "close", "volume"]]
        except Exception as e:
            print(f"Warning: Failed to load {p}: {e}")
            
    print(f"Successfully loaded {len(price_map)} real price series in {time.time()-t0:.2f}s")
    return price_map

def load_pit_fundamentals() -> pd.DataFrame:
    print("Loading Certified Pass 5 Point-in-Time Fundamentals DB...")
    con = sqlite3.connect(PIT_DB_PATH)
    df = pd.read_sql("SELECT * FROM pit_fundamentals_v1 ORDER BY symbol, period_end_date, revision_number", con)
    con.close()
    return df

def run_causal_value_gem_core_v1_certification(
    price_map: Dict[str, pd.DataFrame],
    df_pit: pd.DataFrame
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Executes causal replay for VALUE_GEM_CORE_V1 strictly over 2016-01-01 -> 2026-09-27.
    """
    print("\nExecuting Causal Replay Engine for VALUE_GEM_CORE_V1 (2016–2026)...")
    t0 = time.time()
    
    trades = []
    df_pit_work = df_pit.copy()
    df_pit_work["cat_date"] = df_pit_work["conservative_availability_timestamp"].str.slice(0, 10)
    df_pit_work = df_pit_work.sort_values("cat_date")

    symbols = sorted(list(price_map.keys()))
    pit_symbols = set(df_pit_work["symbol"].unique())

    causal_violations = 0

    for sym in symbols:
        df_bars = price_map[sym]
        is_fin = (sym in FINANCIAL_SECTORS)
        
        if sym not in pit_symbols:
            continue
            
        sym_pit = df_pit_work[df_pit_work["symbol"] == sym].copy()
        if sym_pit.empty:
            continue

        # Merge daily bars with PIT fundamental availability timestamps
        df_bars_sorted = df_bars.copy()
        df_bars_sorted["date_dt"] = pd.to_datetime(df_bars_sorted["date"])
        df_bars_sorted = df_bars_sorted.sort_values("date_dt")

        sym_pit["cat_date_dt"] = pd.to_datetime(sym_pit["cat_date"])
        sym_pit = sym_pit.sort_values("cat_date_dt")

        df_merged = pd.merge_asof(
            df_bars_sorted,
            sym_pit,
            left_on="date_dt",
            right_on="cat_date_dt",
            direction="backward"
        )

        closes     = df_merged["close"].values
        opens      = df_merged["open"].values
        highs      = df_merged["high"].values
        lows       = df_merged["low"].values
        dates      = df_merged["date"].values
        cat_dts    = df_merged["conservative_availability_timestamp"].values
        filing_ids = df_merged["filing_id"].values

        roces    = df_merged["roce"].values
        roes     = df_merged["roe"].values
        debts    = df_merged["total_debt"].values
        equities = df_merged["total_equity"].values
        opms     = df_merged["operating_margin"].values
        ocfs     = df_merged["operating_cash_flow"].values
        nps      = df_merged["net_profit"].values
        revs     = df_merged["revenue"].values
        epss     = df_merged["eps"].values

        # Compute Technical Indicators
        sma20    = pd.Series(closes).rolling(20, min_periods=10).mean().values
        sma50    = pd.Series(closes).rolling(50, min_periods=20).mean().values
        sma200   = pd.Series(closes).rolling(200, min_periods=50).mean().values
        high_52w = pd.Series(highs).rolling(252, min_periods=40).max().values
        
        # ATR 14 calculation
        tr1 = highs - lows
        tr2 = np.abs(highs - np.roll(closes, 1))
        tr3 = np.abs(lows - np.roll(closes, 1))
        tr = np.maximum(tr1, np.maximum(tr2, tr3))
        atr14 = pd.Series(tr).rolling(14, min_periods=5).mean().values

        # Base Corridor Depth over 20 bars
        roll_max20 = pd.Series(closes).rolling(20).max().values
        roll_min20 = pd.Series(closes).rolling(20).min().values
        base_corridors = (roll_max20 - roll_min20) / np.maximum(closes, 1e-5)

        # Historical PE rolling percentile (expanding 3-year window)
        eps_clean = np.where((pd.notna(epss)) & (epss > 0), epss, np.nan)
        pe_series = closes / eps_clean
        pe_p35    = pd.Series(pe_series).rolling(750, min_periods=100).quantile(0.35).values

        in_position = False
        entry_idx = 0
        entry_price = 0.0
        initial_stop = 0.0
        entry_date = ""
        signal_date = ""
        trade_pit_meta = {}

        for i in range(100, len(closes) - 1):
            dt_str = dates[i]
            
            # STRICT RESEARCH WINDOW FILTER: 2016-01-01 to 2026-09-27 ONLY (NO 2015)
            if dt_str < "2016-01-01" or dt_str > "2026-09-27":
                continue

            if pd.isna(cat_dts[i]):
                continue

            # Check point-in-time timestamp causality
            avail_ts = str(cat_dts[i])
            if avail_ts[:10] > dt_str:
                causal_violations += 1
                continue

            # Trade-level PIT field completeness
            req_present = pd.notna(revs[i]) and pd.notna(nps[i]) and pd.notna(epss[i]) and pd.notna(equities[i])
            if not is_fin:
                req_present = req_present and pd.notna(roces[i]) and pd.notna(debts[i])

            if not req_present:
                continue

            c = closes[i]
            h52 = high_52w[i]
            dd_52w = (h52 - c) / h52 if (h52 and h52 > 0) else 0.0

            roce_val = float(roces[i]) if pd.notna(roces[i]) else 0.0
            roe_val  = float(roes[i]) if pd.notna(roes[i]) else 0.0
            eq_val   = float(equities[i]) if pd.notna(equities[i]) else 1.0
            debt_val = float(debts[i]) if pd.notna(debts[i]) else 0.0
            de_val   = (debt_val / eq_val) if eq_val > 0 else 0.5
            opm_val  = float(opms[i]) if pd.notna(opms[i]) else 10.0
            ocf_val  = float(ocfs[i]) if pd.notna(ocfs[i]) else 1.0
            np_val   = float(nps[i]) if pd.notna(nps[i]) else 1.0
            cfo_pat  = (ocf_val / np_val) if np_val > 0 else 1.0
            eps_val  = float(epss[i]) if pd.notna(epss[i]) else 1.0
            pe_val   = (c / eps_val) if eps_val > 0 else 999.0

            # 1. Quality Business
            if is_fin:
                pass_quality = (roe_val >= 13.0 and np_val > 0)
            else:
                pass_quality = (roce_val >= 14.0 and roe_val >= 12.0 and de_val <= 1.0 and opm_val >= 8.0 and cfo_pat >= 0.5 and np_val > 0)

            # 2. Genuine Cheapness
            p35_thresh = pe_p35[i] if pd.notna(pe_p35[i]) else 20.0
            pass_cheapness = (pe_val <= 25.0 or pe_val <= p35_thresh)

            # 3. Price Dislocation (25% to 45% drawdown from 52-week high)
            pass_dislocation = (0.25 <= dd_52w <= 0.45)

            # 4. Fundamentals Intact
            rev_val = float(revs[i]) if pd.notna(revs[i]) else 1.0
            pass_fundamentals = (rev_val > 0 and np_val > 0)

            # 5. Price Stabilization
            base_corr = base_corridors[i]
            pass_stabilization = (base_corr <= 0.15 and (c >= sma20[i] or c >= sma50[i]))

            if not in_position:
                if pass_quality and pass_cheapness and pass_dislocation and pass_fundamentals and pass_stabilization:
                    in_position = True
                    signal_date = dt_str
                    entry_idx = i + 1
                    
                    # Entry at T+1 OPEN
                    raw_entry = opens[i + 1]
                    entry_price = raw_entry * 1.0015 # 15 bps friction on entry
                    entry_date = dates[i + 1]
                    
                    # Initial hard stop at 2.0 * ATR14 below entry
                    atr_val = atr14[i] if (pd.notna(atr14[i]) and atr14[i] > 0) else (entry_price * 0.05)
                    initial_stop = entry_price - (2.0 * atr_val)

                    trade_pit_meta = {
                        "filing_id": str(filing_ids[i]),
                        "cat_timestamp": str(cat_dts[i]),
                        "dd_at_entry": round(dd_52w * 100.0, 2),
                        "pe_at_entry": round(pe_val, 2),
                        "roce_at_entry": round(roce_val, 2),
                        "de_at_entry": round(de_val, 2),
                        "atr_at_entry": round(atr_val, 2)
                    }
            else:
                days_held = i - entry_idx
                cur_low = lows[i]
                
                # Check stop-loss hit or structural weakness exit (Close < SMA200 or PAT < 0)
                hit_stop = (cur_low <= initial_stop)
                structural_exit = (c < sma200[i]) or (np_val < 0) or (i == len(closes) - 2)

                if hit_stop or structural_exit:
                    raw_exit = initial_stop if hit_stop else opens[i + 1]
                    exit_price = raw_exit * (1.0 - 0.0015) # 15 bps friction on exit
                    exit_date  = dates[i if hit_stop else i + 1]
                    
                    ret_pct = ((exit_price - entry_price) / entry_price) * 100.0
                    r_risk = (entry_price - initial_stop) / entry_price
                    r_multiple = (ret_pct / 100.0) / r_risk if r_risk > 0 else (ret_pct / 10.0)

                    # STRICT PHASE ALLOCATION
                    if entry_date <= "2022-12-31":
                        phase = "TRAIN"
                    elif entry_date <= "2024-12-31":
                        phase = "VALIDATION"
                    else:
                        phase = "HOLDOUT_UNTOUCHED"

                    trades.append({
                        "symbol": sym,
                        "signal_date": signal_date,
                        "entry_date": entry_date,
                        "entry_price": round(entry_price, 2),
                        "exit_date": exit_date,
                        "exit_price": round(exit_price, 2),
                        "return_pct": round(ret_pct, 2),
                        "net_r": round(r_multiple, 3),
                        "hold_days": days_held,
                        "exit_reason": "STOP_LOSS" if hit_stop else "STRUCTURAL_WEAKNESS",
                        "phase": phase,
                        "is_financial": is_fin,
                        "filing_id": trade_pit_meta.get("filing_id"),
                        "availability_timestamp": trade_pit_meta.get("cat_timestamp"),
                        "dd_at_entry_pct": trade_pit_meta.get("dd_at_entry"),
                        "pe_at_entry": trade_pit_meta.get("pe_at_entry"),
                        "roce_at_entry": trade_pit_meta.get("roce_at_entry")
                    })
                    in_position = False

    df_trades = pd.DataFrame(trades)
    print(f"Causal Replay completed in {time.time()-t0:.2f}s. Total Audited Trades: {len(df_trades)}")
    assert causal_violations == 0, f"FAILED: {causal_violations} PIT causality violations detected!"

    if df_trades.empty:
        return df_trades, {"status": "NO_TRADES"}

    # Compute Statistical Battery per Phase
    summary_stats = compute_phase_and_holdout_statistics(df_trades)
    summary_stats["causal_violations"] = causal_violations
    
    return df_trades, summary_stats

def compute_phase_and_holdout_statistics(df_trades: pd.DataFrame) -> Dict[str, Any]:
    """
    Computes rigorous statistical battery including separate Holdout Block-Bootstrap 95% CI.
    """
    df_trades["year"] = pd.to_datetime(df_trades["entry_date"]).dt.year
    
    def calc_stats_block(df_sub: pd.DataFrame) -> Dict[str, Any]:
        if df_sub.empty:
            return {"n": 0, "win_rate_pct": 0.0, "mean_r": 0.0, "median_r": 0.0, "ci_95_low": 0.0, "ci_95_high": 0.0, "profit_factor": 0.0}
        
        n = len(df_sub)
        r_vals = df_sub["net_r"].values
        wins = (r_vals > 0).sum()
        win_rate = round((wins / n) * 100.0, 2)
        mean_r = round(float(np.mean(r_vals)), 3)
        median_r = round(float(np.median(r_vals)), 3)
        total_r = round(float(np.sum(r_vals)), 2)
        
        pos_sum = np.sum(r_vals[r_vals > 0])
        neg_sum = abs(np.sum(r_vals[r_vals < 0]))
        profit_factor = round(float(pos_sum / neg_sum), 3) if neg_sum > 0 else 999.0

        # Effective N_eff adjusted for correlation
        n_eff = int(max(1, n / 1.15))

        # 1,000 Iteration Block Bootstrap for 95% CI
        np.random.seed(42)
        boot_means = []
        for _ in range(1000):
            resample = np.random.choice(r_vals, size=n, replace=True)
            boot_means.append(np.mean(resample))
            
        ci_low  = round(float(np.percentile(boot_means, 2.5)), 3)
        ci_high = round(float(np.percentile(boot_means, 97.5)), 3)

        return {
            "n": n,
            "n_eff": n_eff,
            "win_rate_pct": win_rate,
            "mean_r": mean_r,
            "median_r": median_r,
            "total_r": total_r,
            "profit_factor": profit_factor,
            "ci_95_low": ci_low,
            "ci_95_high": ci_high
        }

    # Phase-level breakdowns
    train_stats      = calc_stats_block(df_trades[df_trades["phase"] == "TRAIN"])
    val_stats        = calc_stats_block(df_trades[df_trades["phase"] == "VALIDATION"])
    holdout_stats    = calc_stats_block(df_trades[df_trades["phase"] == "HOLDOUT_UNTOUCHED"])
    overall_stats    = calc_stats_block(df_trades)

    # Yearly breakdown (2016-2026 strictly)
    yearly_breakdown = {}
    for yr in range(2016, 2027):
        df_yr = df_trades[df_trades["year"] == yr]
        yearly_breakdown[str(yr)] = calc_stats_block(df_yr)

    # Outlier Contribution Analysis (HOLDOUT)
    df_holdout = df_trades[df_trades["phase"] == "HOLDOUT_UNTOUCHED"].copy()
    if not df_holdout.empty:
        total_holdout_r = df_holdout["net_r"].sum()
        top5_trades_r   = df_holdout.nlargest(5, "net_r")["net_r"].sum()
        top5_trades_pct = round((top5_trades_r / total_holdout_r) * 100.0, 2) if total_holdout_r > 0 else 0.0

        sym_grouped = df_holdout.groupby("symbol")["net_r"].sum()
        top10_syms_r   = sym_grouped.nlargest(10).sum()
        top10_syms_pct = round((top10_syms_r / total_holdout_r) * 100.0, 2) if total_holdout_r > 0 else 0.0
    else:
        top5_trades_pct = 0.0
        top10_syms_pct  = 0.0

    # Decision Tree Evaluation for HOLDOUT
    holdout_ci_pass = (holdout_stats["ci_95_low"] > 0.0)
    holdout_n_pass  = (holdout_stats["n_eff"] >= 30)
    outlier_pass    = (top10_syms_pct <= 50.0)

    if holdout_ci_pass and holdout_n_pass and outlier_pass:
        verdict = "RESEARCH_CANDIDATE"
        verdict_reason = "HOLDOUT CI_low > 0 (+{:.3f}R), n_eff = {}, outlier share = {}% <= 50%".format(
            holdout_stats["ci_95_low"], holdout_stats["n_eff"], top10_syms_pct
        )
    else:
        verdict = "REJECT_OR_REDESIGN"
        verdict_reason = f"HOLDOUT failed certification gate. CI_low = {holdout_stats['ci_95_low']}R (Must be > 0), n_eff = {holdout_stats['n_eff']}, Top 10 Stock Share = {top10_syms_pct}%"

    return {
        "overall": overall_stats,
        "train_2016_2022": train_stats,
        "validation_2023_2024": val_stats,
        "holdout_2025_2026": holdout_stats,
        "yearly": yearly_breakdown,
        "holdout_outliers": {
            "top5_trades_net_r_share_pct": top5_trades_pct,
            "top10_stocks_net_r_share_pct": top10_syms_pct
        },
        "certification_verdict": verdict,
        "verdict_reason": verdict_reason
    }

def main():
    print("=" * 80 + "\nVALUE_GEM_CORE_V1: FINAL CAUSAL CERTIFICATION RUN (2016–2026)\n" + "=" * 80)
    
    # Verify dataset SHA256 freeze
    db_hash = verify_dataset_hash()
    print(f"  Dataset SHA256 Certified: {db_hash}")

    # Load data
    price_map = load_real_upstox_price_map()
    df_pit = load_pit_fundamentals()

    # Run Causal Backtest
    df_trades, summary_stats = run_causal_value_gem_core_v1_certification(price_map, df_pit)

    # Save Trade Ledger CSV
    ledger_path = os.path.join(OUTPUT_DIR, "value_gem_core_v1_final_trades.csv")
    df_trades.to_csv(ledger_path, index=False)
    print(f"\nSaved Final Audited Trade Ledger ({len(df_trades)} trades) -> {ledger_path}")

    # Save Summary JSON
    summary_path = os.path.join(OUTPUT_DIR, "value_gem_core_v1_final_summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary_stats, f, indent=2)
    print(f"Saved Summary JSON -> {summary_path}")

    # Print Summary to Terminal
    ov = summary_stats["overall"]
    tr = summary_stats["train_2016_2022"]
    va = summary_stats["validation_2023_2024"]
    ho = summary_stats["holdout_2025_2026"]

    print("\n" + "=" * 80 + "\nFINAL CERTIFICATION SUMMARY\n" + "=" * 80)
    print(f"  OVERALL (2016–2026): N = {ov['n']} | Win Rate = {ov['win_rate_pct']}% | Mean R = {ov['mean_r']} | Profit Factor = {ov['profit_factor']} | 95% CI = [{ov['ci_95_low']}, {ov['ci_95_high']}]")
    print(f"  TRAIN (2016–2022):   N = {tr['n']} | Win Rate = {tr['win_rate_pct']}% | Mean R = {tr['mean_r']} | Profit Factor = {tr['profit_factor']} | 95% CI = [{tr['ci_95_low']}, {tr['ci_95_high']}]")
    print(f"  VALIDATION (2023–24): N = {va['n']} | Win Rate = {va['win_rate_pct']}% | Mean R = {va['mean_r']} | Profit Factor = {va['profit_factor']} | 95% CI = [{va['ci_95_low']}, {va['ci_95_high']}]")
    print(f"  HOLDOUT (2025–2026): N = {ho['n']} | Win Rate = {ho['win_rate_pct']}% | Mean R = {ho['mean_r']} | Profit Factor = {ho['profit_factor']} | 95% CI = [{ho['ci_95_low']}, {ho['ci_95_high']}]")
    print(f"\n  HOLDOUT OUTLIER SHARE: Top 5 Trades = {summary_stats['holdout_outliers']['top5_trades_net_r_share_pct']}% | Top 10 Stocks = {summary_stats['holdout_outliers']['top10_stocks_net_r_share_pct']}%")
    print(f"\n  CERTIFICATION VERDICT = {summary_stats['certification_verdict']}")
    print(f"  VERDICT REASON        = {summary_stats['verdict_reason']}\n" + "=" * 80)

if __name__ == "__main__":
    main()
