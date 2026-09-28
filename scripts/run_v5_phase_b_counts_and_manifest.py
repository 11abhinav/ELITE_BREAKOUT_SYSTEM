#!/usr/bin/env python3
"""
scripts/run_v5_phase_b_counts_and_manifest.py
=============================================
FUNDAMENTAL_GEM_RECOVERY_V5 — PHASE B COUNTS & MANIFEST PRE-FREEZE

STRICT COUNTS-ONLY PROTOCOL:
- Zero returns, P&L, or performance modules.
- Layer 3 GATE = EV/EBITDA discount >= 25% below stock's own PIT 3Y median (non-financials only).
- PE_norm discount = Scored feature.
- Layer 4 = Scored feature set (DD_stock, Res_DD, Beta, Sector_DD, Staleness).
- Pre-registered V5 Breadth Floor Check.
"""

from __future__ import annotations
import os
import sys
import json
import hashlib
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Any, Tuple, Optional

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(REPO_ROOT, "data")
HISTORY_1D_DIR = os.path.join(DATA_DIR, "history", "1d")
OUTPUT_DIR = os.path.join(REPO_ROOT, "research", "fundamental_gem_recovery_v5")
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

def load_pit_fundamentals_v5() -> Tuple[pd.DataFrame, Dict[str, pd.DataFrame]]:
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
    
    pit_by_sym = {sym: df.sort_values("conservative_availability_timestamp") for sym, df in df_pit.groupby("symbol")}
    return df_pit, pit_by_sym

def compute_stock_own_3y_pit_median_ev_ebitda(
    sym: str,
    dt: pd.Timestamp,
    pit_by_sym: Dict[str, pd.DataFrame],
    price_data: Dict[str, pd.DataFrame]
) -> Tuple[float, float, float, float]:
    """
    Computes stock's OWN trailing 3-year PIT median of month-end EV/EBITDA and PE_norm.
    Uses ONLY prices and filings available on or before dt.
    """
    if sym not in price_data or sym not in pit_by_sym:
        return 999.0, 999.0, 1.0, 1.0
        
    sym_pit = pit_by_sym[sym]
    avail_sub = sym_pit[sym_pit["conservative_availability_timestamp"] <= dt]
    if avail_sub.empty:
        return 999.0, 999.0, 1.0, 1.0
        
    df_p = price_data[sym].loc[:dt]
    if df_p.empty or len(df_p) < 30:
        return 999.0, 999.0, 1.0, 1.0
        
    monthly_closes = df_p["close"].groupby(df_p.index.to_period("M")).last().tail(36)
    
    ev_ebitda_history = []
    pe_norm_history = []
    
    timestamps = avail_sub["conservative_availability_timestamp"].values
    operating_profits = avail_sub["operating_profit"].values
    net_profits = avail_sub["net_profit"].values
    shares_arr = avail_sub["shares_outstanding"].values if "shares_outstanding" in avail_sub.columns else np.ones(len(avail_sub))
    total_debts = avail_sub["total_debt"].values
    cash_eqs = avail_sub["cash_and_equivalents"].values
    
    for m_period, close_price in monthly_closes.items():
        m_dt = m_period.to_timestamp(how="end")
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

def execute_v5_counts_run(
    monthly_dates: pd.DatetimeIndex,
    symbols: List[str],
    price_data: Dict[str, pd.DataFrame],
    pit_df: pd.DataFrame,
    pit_by_sym: Dict[str, pd.DataFrame],
    ev_discount_thresh: float = 0.25,
    leave_out_layer: Optional[str] = None
) -> Dict[str, Any]:
    
    monthly_records = []
    qualifying_symbol_dates = []
    
    for dt in monthly_dates:
        avail_pit = pit_df[pit_df["conservative_availability_timestamp"] <= dt].sort_values("conservative_availability_timestamp")
        if avail_pit.empty:
            monthly_records.append({
                "date": dt.strftime("%Y-%m-%d"),
                "layer_1_count": 0, "layer_2_count": 0, "layer_3_count": 0
            })
            continue
            
        latest_filings = avail_pit.groupby("symbol").last().reset_index()
        # EXCLUDE FINANCIALS FROM PRIMARY ARM (Non-financials only)
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
                    
        # Layer 3 GATE = EV/EBITDA discount >= 25% below own PIT 3Y median
        l3_syms = []
        for sym in l2_syms:
            if leave_out_layer == "L3":
                l3_syms.append(sym)
            else:
                curr_ev_ebitda, curr_pe_norm, med_ev_ebitda, med_pe_norm = compute_stock_own_3y_pit_median_ev_ebitda(sym, dt, pit_by_sym, price_data)
                ev_discount = (med_ev_ebitda - curr_ev_ebitda) / max(med_ev_ebitda, 0.01)
                
                # STRICT EV/EBITDA DISCOUNT GATE ONLY
                if ev_discount >= ev_discount_thresh:
                    l3_syms.append(sym)
                    qualifying_symbol_dates.append({"symbol": sym, "date": dt})
                    
        monthly_records.append({
            "date": dt.strftime("%Y-%m-%d"),
            "layer_1_count": len(l1_syms),
            "layer_2_count": len(l2_syms),
            "layer_3_count": len(l3_syms)
        })
        
    df_records = pd.DataFrame(monthly_records)
    df_qual = pd.DataFrame(qualifying_symbol_dates)
    
    if not df_qual.empty:
        distinct_symbols = df_qual["symbol"].nunique()
        first_quals = df_qual.groupby("symbol")["date"].min().reset_index()
        distinct_first_qual_dates = first_quals["date"].nunique()
        
        all_dates = sorted(df_qual["date"].unique())
        episodes = 1 if len(all_dates) > 0 else 0
        for i in range(1, len(all_dates)):
            if (all_dates[i] - all_dates[i-1]).days >= 60:
                episodes += 1
    else:
        distinct_symbols = 0
        distinct_first_qual_dates = 0
        episodes = 0
        
    l3_counts = df_records["layer_3_count"].values
    median_l3 = float(np.median(l3_counts)) if len(l3_counts) > 0 else 0.0
    pct_months_ge_8 = (np.sum(l3_counts >= 8) / len(l3_counts)) * 100.0 if len(l3_counts) > 0 else 0.0
    pct_months_ge_1 = (np.sum(l3_counts >= 1) / len(l3_counts)) * 100.0 if len(l3_counts) > 0 else 0.0
    min_count = int(np.min(l3_counts)) if len(l3_counts) > 0 else 0
    max_count = int(np.max(l3_counts)) if len(l3_counts) > 0 else 0
    
    return {
        "monthly_records": df_records,
        "median_candidates": median_l3,
        "min_candidates": min_count,
        "max_candidates": max_count,
        "pct_months_ge_8": pct_months_ge_8,
        "pct_months_ge_1": pct_months_ge_1,
        "distinct_symbols": distinct_symbols,
        "distinct_first_qual_dates": distinct_first_qual_dates,
        "independent_episodes": episodes
    }

def main():
    print("================================================================================")
    print("   FUNDAMENTAL_GEM_RECOVERY_V5 — PHASE B COUNTS & MANIFEST PRE-FREEZE")
    print("================================================================================")
    
    symbols, price_data = load_universe_and_prices()
    pit_df, pit_by_sym = load_pit_fundamentals_v5()
    monthly_dates = pd.date_range(start="2020-01-01", end="2024-12-01", freq="MS")
    
    # 1. Primary L1+L2+L3 (EV/EBITDA discount >= 25% only)
    print("\n--- EXECUTING V5 PRIMARY COUNTS (L1 + L2 + L3_EV_EBITDA) ---")
    res_v5 = execute_v5_counts_run(
        monthly_dates, symbols, price_data, pit_df, pit_by_sym,
        ev_discount_thresh=0.25
    )
    df_v5 = res_v5["monthly_records"]
    df_v5.to_csv(os.path.join(OUTPUT_DIR, "03_V5_PRIMARY_MONTHLY_COUNTS.csv"), index=False)
    
    print(f"V5 Candidates per month: Min = {res_v5['min_candidates']}, Median = {res_v5['median_candidates']:.1f}, Max = {res_v5['max_candidates']}")
    print(f"% Months >= 8 candidates = {res_v5['pct_months_ge_8']:.1f}% (Required >= 60.0%)")
    print(f"Distinct Symbols = {res_v5['distinct_symbols']} (Required >= 40)")
    print(f"Distinct 1st Qualification Dates = {res_v5['distinct_first_qual_dates']}")
    print(f"Independent Episodes = {res_v5['independent_episodes']} (Required >= 3)")
    
    # 2. Recomputed Leave-One-Out Table (Corrected Logic)
    print("\n--- RECOMPUTING LEAVE-ONE-OUT SENSITIVITY TABLE ---")
    loo_list = []
    for layer in ["L1", "L2", "L3"]:
        res_loo = execute_v5_counts_run(
            monthly_dates, symbols, price_data, pit_df, pit_by_sym,
            ev_discount_thresh=0.25, leave_out_layer=layer
        )
        loo_list.append({
            "layer_removed": layer,
            "min_candidates": res_loo["min_candidates"],
            "median_candidates": res_loo["median_candidates"],
            "max_candidates": res_loo["max_candidates"],
            "distinct_symbols": res_loo["distinct_symbols"]
        })
        print(f"Remove {layer}: Range = {res_loo['min_candidates']} to {res_loo['max_candidates']} (Median = {res_loo['median_candidates']:.1f}, Symbols = {res_loo['distinct_symbols']})")
        
    df_loo = pd.DataFrame(loo_list)
    df_loo.to_csv(os.path.join(OUTPUT_DIR, "04_V5_LEAVE_ONE_OUT_SENSITIVITY.csv"), index=False)
    
    # 3. Check Pre-registered Breadth Floor
    passes_breadth_floor = (
        res_v5['pct_months_ge_8'] >= 60.0 and
        res_v5['distinct_symbols'] >= 40 and
        res_v5['independent_episodes'] >= 3
    )
    
    if passes_breadth_floor:
        verdict = "CERTIFIED_FOR_V5_FREEZE"
        print(f"\n================================================================================")
        print(f"  V5 BREADTH FLOOR PASSED: {verdict}")
        print("================================================================================")
    else:
        verdict = "INCONCLUSIVE_BREADTH"
        print(f"\n================================================================================")
        print(f"  V5 BREADTH FLOOR FAILED: {verdict}")
        print("================================================================================")
        
    res_v5_clean = {k: (v.to_dict(orient="records") if isinstance(v, pd.DataFrame) else v) for k, v in res_v5.items()}
    summary_dict = {
        "verdict": verdict,
        "primary_counts": res_v5_clean,
        "passes_breadth_floor": bool(passes_breadth_floor)
    }
    with open(os.path.join(OUTPUT_DIR, "05_V5_BREADTH_SUMMARY.json"), "w") as f:
        json.dump(summary_dict, f, indent=2)

if __name__ == "__main__":
    main()
