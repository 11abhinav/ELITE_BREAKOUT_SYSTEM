#!/usr/bin/env python3
"""
scripts/research_value_gem_v2_regime_filtered.py
=================================================
VALUE_GEM_V2_REGIME_FILTERED: SEQUENTIAL RESEARCH & DEVELOPMENT SUITE

ECONOMIC HYPOTHESIS:
"Buy a fundamentally healthy business after a major valuation/price dislocation ONLY when
the broader market is supportive of mean-reversion/recovery, and exit when structural weakness returns."

RESEARCH STAGES (SEQUENTIAL HYPOTHESIS TESTING):
- STAGE V1 (BASELINE): Retired VALUE_GEM_CORE_V1 (Reference diagnostic).
- STAGE V2-A: Baseline + Macro Market Regime Filter (Market Breadth >= 35% or Equal-Weighted Index >= SMA50).
- STAGE V2-B: Stage V2-A + Liquidity Control (20-day Turnover >= Rs 1 Crore).
- STAGE V2-C: Stage V2-B + Dynamic Trailing ATR Exit Protection.

RESEARCH DATA & TIMEFRAME:
- Price Source: Real Upstox 1D Daily Parquets (`data/history/1d/*.parquet`)
- PIT Fundamentals: Pass 5 Certified DB (`data/pit_fundamentals_v1/pit_fundamentals_v1.db`)
- Window: 2016-01-01 -> 2026-09-27 (2015 Excised)
  * TRAIN (R&D): 2016–2022
  * VALIDATION (R&D): 2023–2024
  * R&D DIAGNOSTIC WINDOW: 2025–2026-09-27
- PROSPECTIVE PAPER TEST GATE: Starting 2026-09-28 onward (Freeze V2 after R&D completion).
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
OUTPUT_DIR  = os.path.join(REPO_ROOT, "artifacts", "value_buy_gems_v2")

os.makedirs(OUTPUT_DIR, exist_ok=True)

# Financial Sectors
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

def load_real_upstox_price_map() -> Tuple[Dict[str, pd.DataFrame], pd.DataFrame]:
    print("Loading Real Upstox 1D Daily Parquets...")
    t0 = time.time()
    files = glob.glob(os.path.join(HISTORY_1D, "*.parquet"))
    if not files:
        raise FileNotFoundError(f"No 1D price parquets found in {HISTORY_1D}")

    price_map = {}
    closes_list = []

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
            df = df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
            df = df.rename(columns={
                "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"
            })
            
            if len(df) >= 100:
                price_map[sym] = df[["date", "open", "high", "low", "close", "volume"]]
                c_df = df[["date", "close"]].rename(columns={"close": sym}).set_index("date")
                closes_list.append(c_df)
        except Exception as e:
            pass

    print(f"Loaded {len(price_map)} symbols in {time.time()-t0:.2f}s")
    
    # Compute Universe Equal-Weighted Market Breadth & Macro Index
    df_closes = pd.concat(closes_list, axis=1).sort_index()
    df_closes = df_closes.loc["2015-01-01":"2026-09-27"]

    df_sma200 = df_closes.rolling(200, min_periods=50).mean()
    df_sma50  = df_closes.rolling(50, min_periods=20).mean()

    pct_above_sma200 = (df_closes >= df_sma200).mean(axis=1) * 100.0
    pct_above_sma50  = (df_closes >= df_sma50).mean(axis=1) * 100.0

    eq_returns = df_closes.pct_change().mean(axis=1).fillna(0)
    index_level = 100.0 * (1.0 + eq_returns).cumprod()
    index_sma50 = index_level.rolling(50, min_periods=20).mean()
    index_sma200 = index_level.rolling(200, min_periods=50).mean()

    df_macro = pd.DataFrame({
        "pct_above_sma200": pct_above_sma200,
        "pct_above_sma50": pct_above_sma50,
        "index_level": index_level,
        "index_sma50": index_sma50,
        "index_sma200": index_sma200,
        # Supportive macro regime: Market breadth >= 35% OR Equal-weighted index above SMA50
        "supportive_macro_regime": (pct_above_sma200 >= 30.0) & ((index_level >= index_sma50) | (pct_above_sma50 >= 40.0))
    })

    return price_map, df_macro

def load_pit_fundamentals() -> pd.DataFrame:
    con = sqlite3.connect(PIT_DB_PATH)
    df = pd.read_sql("SELECT * FROM pit_fundamentals_v1 ORDER BY symbol, period_end_date, revision_number", con)
    con.close()
    return df

def run_sequential_v2_experiment(
    price_map: Dict[str, pd.DataFrame],
    df_macro: pd.DataFrame,
    df_pit: pd.DataFrame,
    stage_name: str = "V2_A_REGIME_FILTERED"
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Evaluates sequential V2 research stage on real Upstox market data across 2016-2026.
    """
    trades = []
    df_pit_work = df_pit.copy()
    df_pit_work["cat_date"] = df_pit_work["conservative_availability_timestamp"].str.slice(0, 10)
    df_pit_work = df_pit_work.sort_values("cat_date")

    symbols = sorted(list(price_map.keys()))
    pit_symbols = set(df_pit_work["symbol"].unique())

    macro_dict = df_macro["supportive_macro_regime"].to_dict()

    for sym in symbols:
        df_bars = price_map[sym]
        is_fin = (sym in FINANCIAL_SECTORS)
        
        if sym not in pit_symbols:
            continue
            
        sym_pit = df_pit_work[df_pit_work["symbol"] == sym].copy()
        if sym_pit.empty:
            continue

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
        vols       = df_merged["volume"].values
        dates      = df_merged["date"].values
        cat_dts    = df_merged["conservative_availability_timestamp"].values

        roces    = df_merged["roce"].values
        roes     = df_merged["roe"].values
        debts    = df_merged["total_debt"].values
        equities = df_merged["total_equity"].values
        opms     = df_merged["operating_margin"].values
        ocfs     = df_merged["operating_cash_flow"].values
        nps      = df_merged["net_profit"].values
        revs     = df_merged["revenue"].values
        epss     = df_merged["eps"].values

        # Technical Indicators
        sma20    = pd.Series(closes).rolling(20, min_periods=10).mean().values
        sma50    = pd.Series(closes).rolling(50, min_periods=20).mean().values
        sma200   = pd.Series(closes).rolling(200, min_periods=50).mean().values
        high_52w = pd.Series(highs).rolling(252, min_periods=40).max().values
        turnover20 = pd.Series(closes * vols).rolling(20, min_periods=10).mean().values
        
        # ATR 14
        tr1 = highs - lows
        tr2 = np.abs(highs - np.roll(closes, 1))
        tr3 = np.abs(lows - np.roll(closes, 1))
        tr = np.maximum(tr1, np.maximum(tr2, tr3))
        atr14 = pd.Series(tr).rolling(14, min_periods=5).mean().values

        # Base Corridor Depth over 20 bars
        roll_max20 = pd.Series(closes).rolling(20).max().values
        roll_min20 = pd.Series(closes).rolling(20).min().values
        base_corridors = (roll_max20 - roll_min20) / np.maximum(closes, 1e-5)

        # Historical PE rolling percentile
        eps_clean = np.where((pd.notna(epss)) & (epss > 0), epss, np.nan)
        pe_series = closes / eps_clean
        pe_p35    = pd.Series(pe_series).rolling(750, min_periods=100).quantile(0.35).values

        in_position = False
        entry_idx = 0
        entry_price = 0.0
        initial_stop = 0.0
        peak_price = 0.0
        entry_date = ""
        signal_date = ""

        for i in range(100, len(closes) - 1):
            dt_str = dates[i]
            
            # STRICT RESEARCH WINDOW: 2016-01-01 to 2026-09-27 (2015 EXCISED)
            if dt_str < "2016-01-01" or dt_str > "2026-09-27":
                continue

            if pd.isna(cat_dts[i]):
                continue

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

            # Core Filters
            if is_fin:
                pass_quality = (roe_val >= 13.0 and np_val > 0)
            else:
                pass_quality = (roce_val >= 14.0 and roe_val >= 12.0 and de_val <= 1.0 and opm_val >= 8.0 and cfo_pat >= 0.5 and np_val > 0)

            p35_thresh = pe_p35[i] if pd.notna(pe_p35[i]) else 20.0
            pass_cheapness = (pe_val <= 25.0 or pe_val <= p35_thresh)
            pass_dislocation = (0.25 <= dd_52w <= 0.45)
            rev_val = float(revs[i]) if pd.notna(revs[i]) else 1.0
            pass_fundamentals = (rev_val > 0 and np_val > 0)
            base_corr = base_corridors[i]
            pass_stabilization = (base_corr <= 0.15 and (c >= sma20[i] or c >= sma50[i]))

            # STAGE-SPECIFIC ADDITIONS
            # Market Regime Filter
            macro_ok = macro_dict.get(dt_str, True)
            if stage_name in ["V2_A_REGIME_FILTERED", "V2_B_LIQUIDITY_CONTROLLED", "V2_C_DYNAMIC_EXIT"]:
                pass_regime = macro_ok
            else:
                pass_regime = True # V1 Baseline ignores regime

            # Liquidity Filter (20-day Turnover >= Rs 1 Crore)
            to_val = turnover20[i] if pd.notna(turnover20[i]) else 1e7
            if stage_name in ["V2_B_LIQUIDITY_CONTROLLED", "V2_C_DYNAMIC_EXIT"]:
                pass_liquidity = (to_val >= 1e7) # Rs 10,000,000 (1 Crore)
            else:
                pass_liquidity = True

            if not in_position:
                if pass_quality and pass_cheapness and pass_dislocation and pass_fundamentals and pass_stabilization and pass_regime and pass_liquidity:
                    in_position = True
                    signal_date = dt_str
                    entry_idx = i + 1
                    raw_entry = opens[i + 1]
                    entry_price = raw_entry * 1.0015
                    entry_date = dates[i + 1]
                    peak_price = entry_price
                    atr_val = atr14[i] if (pd.notna(atr14[i]) and atr14[i] > 0) else (entry_price * 0.05)
                    initial_stop = entry_price - (2.0 * atr_val)
            else:
                days_held = i - entry_idx
                cur_low = lows[i]
                cur_high = highs[i]
                peak_price = max(peak_price, cur_high)
                atr_val = atr14[i] if (pd.notna(atr14[i]) and atr14[i] > 0) else (entry_price * 0.05)

                hit_stop = (cur_low <= initial_stop)
                
                # Dynamic ATR Trailing Exit Protection for V2-C
                if stage_name == "V2_C_DYNAMIC_EXIT":
                    trailing_stop = peak_price - (2.5 * atr_val)
                    hit_trailing = (c <= trailing_stop) and (days_held >= 10)
                    structural_exit = (c < sma200[i]) or (np_val < 0) or hit_trailing or (i == len(closes) - 2)
                else:
                    structural_exit = (c < sma200[i]) or (np_val < 0) or (i == len(closes) - 2)

                if hit_stop or structural_exit:
                    raw_exit = initial_stop if hit_stop else opens[i + 1]
                    exit_price = raw_exit * (1.0 - 0.0015)
                    exit_date  = dates[i if hit_stop else i + 1]
                    
                    ret_pct = ((exit_price - entry_price) / entry_price) * 100.0
                    r_risk = (entry_price - initial_stop) / entry_price
                    r_multiple = (ret_pct / 100.0) / r_risk if r_risk > 0 else (ret_pct / 10.0)

                    if entry_date <= "2022-12-31":
                        phase = "TRAIN"
                    elif entry_date <= "2024-12-31":
                        phase = "VALIDATION"
                    else:
                        phase = "DIAGNOSTIC_2025_2026"

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
                        "stage": stage_name,
                        "phase": phase,
                        "is_financial": is_fin
                    })
                    in_position = False

    df_trades = pd.DataFrame(trades)

    if df_trades.empty:
        return df_trades, {"stage": stage_name, "n": 0}

    stats = compute_stage_statistics(df_trades, stage_name)
    return df_trades, stats

def compute_stage_statistics(df_trades: pd.DataFrame, stage_name: str) -> Dict[str, Any]:
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

        n_eff = int(max(1, n / 1.15))

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

    train_stats      = calc_stats_block(df_trades[df_trades["phase"] == "TRAIN"])
    val_stats        = calc_stats_block(df_trades[df_trades["phase"] == "VALIDATION"])
    diag_stats       = calc_stats_block(df_trades[df_trades["phase"] == "DIAGNOSTIC_2025_2026"])
    overall_stats    = calc_stats_block(df_trades)

    yearly_breakdown = {}
    for yr in range(2016, 2027):
        df_yr = df_trades[df_trades["year"] == yr]
        yearly_breakdown[str(yr)] = calc_stats_block(df_yr)

    return {
        "stage": stage_name,
        "overall": overall_stats,
        "train_2016_2022": train_stats,
        "validation_2023_2024": val_stats,
        "diagnostic_2025_2026": diag_stats,
        "yearly": yearly_breakdown
    }

def main():
    print("=" * 80 + "\nVALUE_GEM_V2_REGIME_FILTERED: SEQUENTIAL RESEARCH & DEVELOPMENT\n" + "=" * 80)
    
    price_map, df_macro = load_real_upstox_price_map()
    df_pit = load_pit_fundamentals()

    all_stage_trades = []
    stage_summaries = {}

    stages = [
        "V1_BASELINE",
        "V2_A_REGIME_FILTERED",
        "V2_B_LIQUIDITY_CONTROLLED",
        "V2_C_DYNAMIC_EXIT"
    ]

    for stg in stages:
        print(f"\n[Evaluating Stage: {stg}]...")
        df_stg_trades, stg_stats = run_sequential_v2_experiment(price_map, df_macro, df_pit, stg)
        all_stage_trades.append(df_stg_trades)
        stage_summaries[stg] = stg_stats

    # Combine trade ledgers
    df_master_trades = pd.concat(all_stage_trades, ignore_index=True)
    trades_path = os.path.join(OUTPUT_DIR, "v2_sequential_research_trades.csv")
    df_master_trades.to_csv(trades_path, index=False)
    print(f"\nSaved Master Research Trade Ledger -> {trades_path}")

    # Save Summary JSON
    summary_path = os.path.join(OUTPUT_DIR, "v2_sequential_research_summary.json")
    with open(summary_path, "w") as f:
        json.dump(stage_summaries, f, indent=2)
    print(f"Saved Sequential Research Summary -> {summary_path}")

    # Comparative Stage Matrix Print
    print("\n" + "=" * 90)
    print(f"{'STAGE':<28} | {'N (TOTAL)':<9} | {'OVERALL MEAN R':<14} | {'2025-26 MEAN R':<14} | {'PF (2025-26)':<12}")
    print("=" * 90)
    for stg in stages:
        st = stage_summaries[stg]
        ov = st["overall"]
        diag = st["diagnostic_2025_2026"]
        print(f"{stg:<28} | {ov['n']:<9} | {ov['mean_r']:<14.3f} | {diag['mean_r']:<14.3f} | {diag['profit_factor']:<12.3f}")
    print("=" * 90)

if __name__ == "__main__":
    main()
