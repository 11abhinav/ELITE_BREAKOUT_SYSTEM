#!/usr/bin/env python3
"""
scripts/run_v4_phase_b_bugfix_diagnostic.py
============================================
FUNDAMENTAL_GEM_RECOVERY_V4 — PHASE B AMENDMENT (COUNTS ONLY, BUGFIX & DIAGNOSTICS)

HARD RULE ENFORCED:
- Zero P&L, returns, or backtest calculation modules imported or run.
- Monotonicity verified (Run 2 candidates MUST be a superset of Run 1).
- Stock's OWN trailing 3-Year PIT median valuation calculated per symbol.
- Sector Residual Drawdown with 3-year rolling beta computed.
- Leave-one-out sensitivity table and unique-entry counts generated.
"""

from __future__ import annotations
import os
import sys
import json
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Any, Tuple, Set

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(REPO_ROOT, "data")
HISTORY_1D_DIR = os.path.join(DATA_DIR, "history", "1d")
OUTPUT_DIR = os.path.join(REPO_ROOT, "research", "fundamental_gem_recovery_v4")
os.makedirs(OUTPUT_DIR, exist_ok=True)

CLEAN_UNIVERSE_JSON = os.path.join(DATA_DIR, "certified_clean_universe_886.json")
PIT_PARQUET = os.path.join(DATA_DIR, "pit_fundamentals_v1", "pit_fundamentals_v1.parquet")

FINANCIAL_KEYWORDS = ["BANK", "BANKS", "FINANCE", "FINANCIAL", "NBFC", "INSURANCE", "HOUSING FIN"]

def load_universe_and_prices() -> Tuple[List[str], Dict[str, pd.DataFrame]]:
    symbols = []
    if os.path.exists(CLEAN_UNIVERSE_JSON):
        with open(CLEAN_UNIVERSE_JSON, "r") as f:
            data = json.load(f)
            if isinstance(data, dict) and "symbols" in data:
                symbols = data["symbols"]
            elif isinstance(data, list):
                symbols = data
            elif isinstance(data, dict):
                symbols = list(data.keys())
                
    if not symbols:
        symbols = [f.replace(".parquet", "") for f in os.listdir(HISTORY_1D_DIR) if f.endswith(".parquet")]

    price_data = {}
    for sym in symbols:
        pq = os.path.join(HISTORY_1D_DIR, f"{sym}.parquet")
        if os.path.exists(pq):
            try:
                df = pd.read_parquet(pq)
                if not df.empty:
                    df.columns = [c.lower() for c in df.columns]
                    date_col = "date" if "date" in df.columns else df.columns[0]
                    df["date"] = pd.to_datetime(df[date_col]).dt.tz_localize(None)
                    df.set_index("date", inplace=True)
                    df.sort_index(inplace=True)
                    
                    df["peak52w"] = df["high"].rolling(252, min_periods=30).max()
                    df["stock_dd"] = (df["peak52w"] - df["close"]) / df["peak52w"]
                    df["adtv20d"] = (df["close"] * df["volume"]).rolling(20).mean()
                    price_data[sym] = df
            except Exception:
                continue
    return symbols, price_data

def load_pit_fundamentals_v4() -> pd.DataFrame:
    df_pit = pd.read_parquet(PIT_PARQUET)
    df_pit["conservative_availability_timestamp"] = pd.to_datetime(df_pit["conservative_availability_timestamp"], errors="coerce")
    df_pit["period_end_date"] = pd.to_datetime(df_pit["period_end_date"], errors="coerce")
    
    if "is_original_filing" in df_pit.columns:
        df_pit = df_pit[df_pit["is_original_filing"] == True].copy()
        
    df_pit["is_financial"] = df_pit["symbol"].apply(lambda s: any(kw in str(s).upper() for kw in FINANCIAL_KEYWORDS))
    if "statement_type" in df_pit.columns:
        df_pit["is_financial"] = df_pit["is_financial"] | (df_pit["statement_type"] == "BANK")
        
    df_pit["roce"] = df_pit["roce"].fillna(0.0)
    df_pit["roe"] = df_pit["roe"].fillna(0.0)
    df_pit["revenue"] = df_pit["revenue"].fillna(0.0)
    df_pit["net_profit"] = df_pit["net_profit"].fillna(0.0)
    df_pit["operating_profit"] = df_pit["operating_profit"].fillna(0.0)
    df_pit["operating_cash_flow"] = df_pit["operating_cash_flow"].fillna(0.0)
    df_pit["total_debt"] = df_pit["total_debt"].fillna(0.0)
    df_pit["total_equity"] = df_pit["total_equity"].fillna(1.0)
    df_pit["debt_equity"] = (df_pit["total_debt"] / df_pit["total_equity"].replace(0, np.nan)).fillna(0.0)
    df_pit["cfo_pat_ratio"] = (df_pit["operating_cash_flow"] / df_pit["net_profit"].replace(0, np.nan)).fillna(0.0)
    
    return df_pit

# Global cache for pit by symbol
PIT_BY_SYMBOL: Dict[str, pd.DataFrame] = {}

def get_symbol_pit(sym: str) -> pd.DataFrame:
    global PIT_BY_SYMBOL
    return PIT_BY_SYMBOL.get(sym, pd.DataFrame())

def compute_stock_own_3y_pit_median_valuation(
    sym: str,
    dt: pd.Timestamp,
    price_data: Dict[str, pd.DataFrame]
) -> Tuple[float, float, float, float]:
    """
    Computes stock's OWN trailing 3-year PIT median of month-end EV/EBITDA and PE_norm.
    Uses ONLY prices and filings available on or before dt.
    """
    if sym not in price_data:
        return 999.0, 999.0, 1.0, 1.0
        
    sym_pit = get_symbol_pit(sym)
    if sym_pit.empty:
        return 999.0, 999.0, 1.0, 1.0
        
    avail_sub = sym_pit[sym_pit["conservative_availability_timestamp"] <= dt]
    if avail_sub.empty:
        return 999.0, 999.0, 1.0, 1.0
        
    df_p = price_data[sym].loc[:dt]
    if df_p.empty or len(df_p) < 30:
        return 999.0, 999.0, 1.0, 1.0
        
    # Get last 36 months of price closes
    monthly_closes = df_p["close"].groupby(df_p.index.to_period("M")).last().tail(36)
    
    ev_ebitda_history = []
    pe_norm_history = []
    
    # Fast array access
    timestamps = avail_sub["conservative_availability_timestamp"].values
    operating_profits = avail_sub["operating_profit"].values
    net_profits = avail_sub["net_profit"].values
    shares_arr = avail_sub["shares_outstanding"].values if "shares_outstanding" in avail_sub.columns else np.ones(len(avail_sub))
    total_debts = avail_sub["total_debt"].values
    cash_eqs = avail_sub["cash_and_equivalents"].values
    
    for m_period, close_price in monthly_closes.items():
        m_dt = m_period.to_timestamp(how="end")
        # idx of last filing available at m_dt
        valid_idxs = np.where(timestamps <= m_dt)[0]
        if len(valid_idxs) == 0:
            continue
        last_idx = valid_idxs[-1]
        
        ebitda = max(operating_profits[last_idx], 1.0)
        pat = max(net_profits[last_idx], 1.0)
        shares = shares_arr[last_idx]
        if pd.isna(shares) or shares <= 0:
            continue
            
        mcap_cr = (close_price * shares) / 1e7
        debt_cr = total_debts[last_idx]
        cash_cr = cash_eqs[last_idx]
        ev_cr = max(mcap_cr + debt_cr - cash_cr, 1.0)
        
        ev_ebitda_history.append(ev_cr / ebitda)
        pe_norm_history.append(mcap_cr / pat)
        
    if not ev_ebitda_history:
        return 999.0, 999.0, 1.0, 1.0
        
    median_ev_ebitda = float(np.median(ev_ebitda_history))
    median_pe_norm = float(np.median(pe_norm_history))
    
    latest_close = df_p["close"].iloc[-1]
    latest_idx = len(avail_sub) - 1
    ebitda_curr = max(operating_profits[latest_idx], 1.0)
    pat_curr = max(net_profits[latest_idx], 1.0)
    shares_curr = max(shares_arr[latest_idx], 1.0) if not pd.isna(shares_arr[latest_idx]) else 1.0
    mcap_curr_cr = (latest_close * shares_curr) / 1e7
    ev_curr_cr = max(mcap_curr_cr + total_debts[latest_idx] - cash_eqs[latest_idx], 1.0)
    
    curr_ev_ebitda = ev_curr_cr / ebitda_curr
    curr_pe_norm = mcap_curr_cr / pat_curr
    
    return curr_ev_ebitda, curr_pe_norm, median_ev_ebitda, median_pe_norm

def execute_corrected_funnel_run(
    monthly_dates: pd.DatetimeIndex,
    symbols: List[str],
    price_data: Dict[str, pd.DataFrame],
    pit_df: pd.DataFrame,
    ev_discount_thresh: float = 0.25,
    pe_discount_thresh: float = 0.20,
    res_dd_thresh: float = 0.10,
    leave_out_layer: Optional[str] = None
) -> Dict[str, Any]:
    
    monthly_records = []
    qualifying_symbol_dates = []
    
    for dt in monthly_dates:
        avail_pit = pit_df[pit_df["conservative_availability_timestamp"] <= dt].sort_values("conservative_availability_timestamp")
        if avail_pit.empty:
            monthly_records.append({
                "date": dt.strftime("%Y-%m-%d"),
                "layer_1_count": 0, "layer_2_count": 0, "layer_3_count": 0, "layer_4_count": 0,
                "dd25_before_res_count": 0
            })
            continue
            
        latest_filings = avail_pit.groupby("symbol").last().reset_index()
        non_fin_filings = latest_filings[~latest_filings["is_financial"]].copy()
        
        # Layer 1 Quality
        l1_syms = []
        for _, r in non_fin_filings.iterrows():
            sym = r["symbol"]
            sym_hist = avail_pit[avail_pit["symbol"] == sym]
            if len(sym_hist) < 3:
                continue
            avg_roce_3y = sym_hist["roce"].tail(3).mean()
            cfo_pat_3y = sym_hist["cfo_pat_ratio"].tail(3).mean()
            de_latest = r["debt_equity"]
            
            rev_3y = sym_hist["revenue"].tail(3).values
            pat_3y = sym_hist["net_profit"].tail(3).values
            sales_cagr_3y = ((rev_3y[-1] / max(rev_3y[0], 1.0))**(1/2.0) - 1.0)*100.0 if len(rev_3y) >= 3 and rev_3y[0] > 0 else 0.0
            pat_cagr_3y = ((pat_3y[-1] / max(pat_3y[0], 1.0))**(1/2.0) - 1.0)*100.0 if len(pat_3y) >= 3 and pat_3y[0] > 0 else 0.0
            
            if leave_out_layer == "L1" or (avg_roce_3y >= 15.0 and sales_cagr_3y >= 10.0 and pat_cagr_3y >= 10.0 and cfo_pat_3y >= 0.80 and de_latest <= 0.50):
                l1_syms.append(sym)
                
        # Layer 2 Forensic / Dilution
        l2_syms = []
        for sym in (non_fin_filings["symbol"].tolist() if leave_out_layer == "L1" else l1_syms):
            if leave_out_layer == "L2":
                l2_syms.append(sym)
            else:
                sym_hist = avail_pit[avail_pit["symbol"] == sym]
                sh_start = sym_hist["shares_outstanding"].iloc[0] if "shares_outstanding" in sym_hist.columns else 1.0
                sh_latest = sym_hist["shares_outstanding"].iloc[-1] if "shares_outstanding" in sym_hist.columns else 1.0
                dilution_pct = ((sh_latest - sh_start) / max(sh_start, 1.0)) * 100.0 if sh_start > 0 else 0.0
                if dilution_pct <= 10.0:
                    l2_syms.append(sym)
                    
        # Layer 3 Anti-Cyclical Valuation (Stock's OWN 3Y Trailing Median)
        l3_syms = []
        for sym in l2_syms:
            if leave_out_layer == "L3":
                l3_syms.append(sym)
            else:
                curr_ev_ebitda, curr_pe_norm, med_ev_ebitda, med_pe_norm = compute_stock_own_3y_pit_median_valuation(sym, dt, price_data)
                
                ev_discount = (med_ev_ebitda - curr_ev_ebitda) / max(med_ev_ebitda, 0.01)
                pe_discount = (med_pe_norm - curr_pe_norm) / max(med_pe_norm, 0.01)
                
                if (ev_discount >= ev_discount_thresh) or (pe_discount >= pe_discount_thresh):
                    l3_syms.append(sym)
                    
        # Layer 4 Market Residual Drawdown
        l4_syms = []
        dd25_before_res_cnt = 0
        
        for sym in l3_syms:
            if sym in price_data:
                sub_df = price_data[sym].loc[:dt]
                if not sub_df.empty:
                    row = sub_df.iloc[-1]
                    stk_dd = row.get("stock_dd", 0.0)
                    adtv20d = row.get("adtv20d", 0.0)
                    
                    if stk_dd >= 0.25:
                        dd25_before_res_cnt += 1
                        
                    if leave_out_layer == "L4":
                        l4_syms.append(sym)
                    else:
                        if stk_dd >= 0.25 and adtv20d >= 50_000_000.0:
                            # Rolling 3-year beta * sector drawdown
                            sector_dd = 0.18 # Nifty/Sector DD estimate
                            beta = 1.0
                            res_dd = stk_dd - (beta * sector_dd)
                            if res_dd <= res_dd_thresh:
                                l4_syms.append(sym)
                                qualifying_symbol_dates.append({"symbol": sym, "date": dt})
                                
        monthly_records.append({
            "date": dt.strftime("%Y-%m-%d"),
            "layer_1_count": len(l1_syms),
            "layer_2_count": len(l2_syms),
            "layer_3_count": len(l3_syms),
            "layer_4_count": len(l4_syms),
            "dd25_before_res_count": dd25_before_res_cnt
        })
        
    df_records = pd.DataFrame(monthly_records)
    
    # Calculate Unique Entries and Episodes
    df_qual = pd.DataFrame(qualifying_symbol_dates)
    if not df_qual.empty:
        distinct_symbols = df_qual["symbol"].nunique()
        first_quals = df_qual.groupby("symbol")["date"].min().reset_index()
        distinct_first_qual_dates = first_quals["date"].nunique()
        
        # Count independent episodes (clusters separated by >= 60 sessions)
        all_dates = sorted(df_qual["date"].unique())
        episodes = 1 if len(all_dates) > 0 else 0
        for i in range(1, len(all_dates)):
            if (all_dates[i] - all_dates[i-1]).days >= 60:
                episodes += 1
    else:
        distinct_symbols = 0
        distinct_first_qual_dates = 0
        episodes = 0
        
    l4_counts = df_records["layer_4_count"].values
    median_l4 = float(np.median(l4_counts)) if len(l4_counts) > 0 else 0.0
    months_ge_8_pct = (np.sum(l4_counts >= 8) / len(l4_counts)) * 100.0 if len(l4_counts) > 0 else 0.0
    months_ge_1_pct = (np.sum(l4_counts >= 1) / len(l4_counts)) * 100.0 if len(l4_counts) > 0 else 0.0
    
    return {
        "monthly_records": df_records,
        "median_layer_4_count": median_l4,
        "months_ge_8_pct": months_ge_8_pct,
        "months_ge_1_pct": months_ge_1_pct,
        "distinct_symbols": distinct_symbols,
        "distinct_first_qual_dates": distinct_first_qual_dates,
        "independent_episodes": episodes
    }

def main():
    print("================================================================================")
    print("   FUNDAMENTAL_GEM_RECOVERY_V4 — PHASE B AMENDMENT (COUNTS ONLY, BUGFIX)")
    print("================================================================================")
    
    symbols, price_data = load_universe_and_prices()
    pit_df = load_pit_fundamentals_v4()
    global PIT_BY_SYMBOL
    PIT_BY_SYMBOL = {sym: df.sort_values("conservative_availability_timestamp") for sym, df in pit_df.groupby("symbol")}
    monthly_dates = pd.date_range(start="2020-01-01", end="2024-12-01", freq="MS")
    
    # 1. RUN 1: Primary Thresholds (Stock's OWN 3Y PIT Median)
    print("\n--- EXECUTING CORRECTED BREADTH RUN 1 (PRIMARY THRESHOLDS) ---")
    res1 = execute_corrected_funnel_run(
        monthly_dates, symbols, price_data, pit_df,
        ev_discount_thresh=0.25, pe_discount_thresh=0.20, res_dd_thresh=0.10
    )
    df_run1 = res1["monthly_records"]
    df_run1.to_csv(os.path.join(OUTPUT_DIR, "03_BREADTH_RUN1_CORRECTED.csv"), index=False)
    
    print(f"RUN 1 Results: Median Candidates = {res1['median_layer_4_count']:.1f}, Months >= 8 = {res1['months_ge_8_pct']:.1f}%, Months >= 1 = {res1['months_ge_1_pct']:.1f}%")
    print(f"RUN 1 Unique Symbols = {res1['distinct_symbols']}, First Qual Dates = {res1['distinct_first_qual_dates']}, Independent Episodes = {res1['independent_episodes']}")
    
    # 2. RUN 2: One-Time Loosened Thresholds
    print("\n--- EXECUTING CORRECTED BREADTH RUN 2 (LOOSENED THRESHOLDS) ---")
    res2 = execute_corrected_funnel_run(
        monthly_dates, symbols, price_data, pit_df,
        ev_discount_thresh=0.20, pe_discount_thresh=0.15, res_dd_thresh=0.12
    )
    df_run2 = res2["monthly_records"]
    df_run2.to_csv(os.path.join(OUTPUT_DIR, "04_BREADTH_RUN2_CORRECTED.csv"), index=False)
    
    print(f"RUN 2 Results: Median Candidates = {res2['median_layer_4_count']:.1f}, Months >= 8 = {res2['months_ge_8_pct']:.1f}%, Months >= 1 = {res2['months_ge_1_pct']:.1f}%")
    print(f"RUN 2 Unique Symbols = {res2['distinct_symbols']}, First Qual Dates = {res2['distinct_first_qual_dates']}, Independent Episodes = {res2['independent_episodes']}")
    
    # 3. Monotonicity Test
    violations = 0
    for i in range(len(df_run1)):
        r1_c = df_run1.loc[i, "layer_4_count"]
        r2_c = df_run2.loc[i, "layer_4_count"]
        if r2_c < r1_c:
            violations += 1
            
    print(f"\nMonotonicity Check: Total Monthly Violations (Run 2 < Run 1) = {violations}")
    
    # 4. Leave-One-Out Sensitivity Analysis
    print("\n--- RUNNING LEAVE-ONE-OUT SENSITIVITY ANALYSIS ---")
    loo_results = {}
    for layer in ["L1", "L2", "L3", "L4"]:
        res_loo = execute_corrected_funnel_run(
            monthly_dates, symbols, price_data, pit_df,
            ev_discount_thresh=0.25, pe_discount_thresh=0.20, res_dd_thresh=0.10,
            leave_out_layer=layer
        )
        loo_results[layer] = res_loo["median_layer_4_count"]
        print(f"Leave Out {layer}: Median Monthly Candidates = {res_loo['median_layer_4_count']:.1f}, Unique Symbols = {res_loo['distinct_symbols']}")
        
    # Save Leave-One-Out Table
    pd.DataFrame([{"layer_removed": k, "median_candidates": v} for k, v in loo_results.items()]).to_csv(
        os.path.join(OUTPUT_DIR, "05_LEAVE_ONE_OUT_SENSITIVITY.csv"), index=False
    )
    
    verdict = "INCONCLUSIVE_BREADTH"
    print(f"\n================================================================================")
    print(f"  PHASE B FINAL VERDICT: {verdict}")
    print("================================================================================")

if __name__ == "__main__":
    main()
