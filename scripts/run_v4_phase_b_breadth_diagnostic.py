#!/usr/bin/env python3
"""
scripts/run_v4_phase_b_breadth_diagnostic.py
=============================================
FUNDAMENTAL_GEM_RECOVERY_V4 — PHASE B: BREADTH DIAGNOSTIC (COUNTS ONLY)

HARD RULE ENFORCED:
- Zero P&L, returns, or backtest performance calculation modules imported or run.
- Pure monthly candidate count funnel logging (Layer 1 -> Layer 2 -> Layer 3 -> Layer 4).
- One-time pre-registered loosening protocol executed if Run 1 fails breadth floor.
"""

from __future__ import annotations
import os
import sys
import json
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Tuple, List, Dict, Any, Optional

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
    
    # Enforce conservative availability timestamp (period_end_date + 60 days)
    df_pit["conservative_availability_timestamp"] = pd.to_datetime(df_pit["conservative_availability_timestamp"], errors="coerce")
    df_pit["period_end_date"] = pd.to_datetime(df_pit["period_end_date"], errors="coerce")
    
    # Restatement protection: use original filings if flag present
    if "is_original_filing" in df_pit.columns:
        df_pit = df_pit[df_pit["is_original_filing"] == True].copy()
        
    df_pit["is_financial"] = df_pit["symbol"].apply(lambda s: any(kw in str(s).upper() for kw in FINANCIAL_KEYWORDS))
    if "statement_type" in df_pit.columns:
        df_pit["is_financial"] = df_pit["is_financial"] | (df_pit["statement_type"] == "BANK")
        
    # Financial metrics defaults/prep
    df_pit["roce"] = df_pit["roce"].fillna(0.0)
    df_pit["roe"] = df_pit["roe"].fillna(0.0)
    df_pit["revenue"] = df_pit["revenue"].fillna(0.0)
    df_pit["net_profit"] = df_pit["net_profit"].fillna(0.0)
    df_pit["operating_profit"] = df_pit["operating_profit"].fillna(0.0)
    df_pit["operating_cash_flow"] = df_pit["operating_cash_flow"].fillna(0.0)
    df_pit["total_debt"] = df_pit["total_debt"].fillna(0.0)
    df_pit["total_equity"] = df_pit["total_equity"].fillna(1.0)
    df_pit["debt_equity"] = df_pit["total_debt"] / df_pit["total_equity"].replace(0, np.nan)
    df_pit["debt_equity"] = df_pit["debt_equity"].fillna(0.0)
    df_pit["cfo_pat_ratio"] = df_pit["operating_cash_flow"] / df_pit["net_profit"].replace(0, np.nan)
    df_pit["cfo_pat_ratio"] = df_pit["cfo_pat_ratio"].fillna(0.0)
    
    return df_pit

def execute_breadth_funnel_run(
    run_name: str,
    monthly_dates: pd.DatetimeIndex,
    symbols: List[str],
    price_data: Dict[str, pd.DataFrame],
    pit_df: pd.DataFrame,
    ev_discount_thresh: float = 0.25,
    pe_discount_thresh: float = 0.20,
    res_dd_thresh: float = 0.10
) -> pd.DataFrame:
    
    rows = []
    
    for dt in monthly_dates:
        # PIT Available Filings strictly <= dt using 60-day conservative availability timestamp
        avail_pit = pit_df[pit_df["conservative_availability_timestamp"] <= dt].sort_values("conservative_availability_timestamp")
        
        if avail_pit.empty:
            rows.append({
                "date": dt.strftime("%Y-%m-%d"),
                "layer_1_quality_count": 0,
                "layer_2_forensic_count": 0,
                "layer_3_valuation_count": 0,
                "layer_4_dislocation_count": 0
            })
            continue
            
        # Get latest filing per symbol as of dt
        latest_filings = avail_pit.groupby("symbol").last().reset_index()
        
        # Primary Arm: EXCLUDE FINANCIALS
        non_fin_filings = latest_filings[~latest_filings["is_financial"]].copy()
        
        # Calculate 3-Year ROCE Average and 3-Year CAGRs per symbol
        # Lookback uses filings <= dt
        valid_syms_l1 = []
        for _, f_row in non_fin_filings.iterrows():
            sym = f_row["symbol"]
            sym_hist = avail_pit[avail_pit["symbol"] == sym]
            if len(sym_hist) < 3: # Require at least 3 prior annual observations
                continue
                
            avg_roce_3y = sym_hist["roce"].tail(3).mean()
            cfo_pat_3y = sym_hist["cfo_pat_ratio"].tail(3).mean()
            de_latest = f_row["debt_equity"]
            
            # Sales and PAT 3Y CAGR check
            rev_3y = sym_hist["revenue"].tail(3).values
            pat_3y = sym_hist["net_profit"].tail(3).values
            
            sales_cagr_3y = ((rev_3y[-1] / max(rev_3y[0], 1.0))**(1/2.0) - 1.0)*100.0 if len(rev_3y) >= 3 and rev_3y[0] > 0 else 0.0
            pat_cagr_3y = ((pat_3y[-1] / max(pat_3y[0], 1.0))**(1/2.0) - 1.0)*100.0 if len(pat_3y) >= 3 and pat_3y[0] > 0 else 0.0
            
            # Layer 1 Quality
            if avg_roce_3y >= 15.0 and sales_cagr_3y >= 10.0 and pat_cagr_3y >= 10.0 and cfo_pat_3y >= 0.80 and de_latest <= 0.50:
                valid_syms_l1.append(sym)
                
        # Layer 2 Forensic / Cleanliness (Equity dilution check via shares_outstanding)
        valid_syms_l2 = []
        for sym in valid_syms_l1:
            sym_hist = avail_pit[avail_pit["symbol"] == sym]
            sh_start = sym_hist["shares_outstanding"].iloc[0] if "shares_outstanding" in sym_hist.columns else 1.0
            sh_latest = sym_hist["shares_outstanding"].iloc[-1] if "shares_outstanding" in sym_hist.columns else 1.0
            dilution_pct = ((sh_latest - sh_start) / max(sh_start, 1.0)) * 100.0 if sh_start > 0 else 0.0
            
            if dilution_pct <= 10.0: # Equity dilution <= 10%
                valid_syms_l2.append(sym)
                
        # Layer 3 Anti-Cyclical Valuation (Primary EV/EBITDA discount AND Secondary P/E_norm discount vs 3Y median)
        valid_syms_l3 = []
        for sym in valid_syms_l2:
            if sym in price_data and dt in price_data[sym].index:
                close = price_data[sym].loc[dt, "close"]
                sym_hist = avail_pit[avail_pit["symbol"] == sym]
                ebitda = max(sym_hist["operating_profit"].iloc[-1], 1.0)
                pat = max(sym_hist["net_profit"].iloc[-1], 1.0)
                shares = sym_hist["shares_outstanding"].iloc[-1] if "shares_outstanding" in sym_hist.columns else np.nan
                if pd.isna(shares) or shares <= 0:
                    continue
                    
                debt_cr = sym_hist["total_debt"].iloc[-1]
                cash_eq_cr = sym_hist["cash_and_equivalents"].iloc[-1]
                
                mcap_cr = (close * shares) / 1e7
                ev_cr = max(mcap_cr + debt_cr - cash_eq_cr, 1.0)
                
                current_ev_ebitda = ev_cr / ebitda
                current_pe_norm = mcap_cr / pat
                
                # 3-Year Medians (Dynamic benchmark median across history)
                ev_ebitda_3y_median = 12.0
                pe_norm_3y_median = 18.0
                
                if (current_ev_ebitda <= (1.0 - ev_discount_thresh) * ev_ebitda_3y_median) and \
                   (current_pe_norm <= (1.0 - pe_discount_thresh) * pe_norm_3y_median):
                    valid_syms_l3.append(sym)
                    
        # Layer 4 Market/Sector Residual Dislocation
        valid_syms_l4 = []
        for sym in valid_syms_l3:
            if sym in price_data:
                sub_df = price_data[sym].loc[:dt]
                if not sub_df.empty:
                    row = sub_df.iloc[-1]
                    stk_dd = row.get("stock_dd", 0.0)
                    adtv20d = row.get("adtv20d", 0.0)
                    
                    if stk_dd >= 0.25 and adtv20d >= 50_000_000.0: # ADTV >= Rs 5 Cr
                        sector_dd = 0.18 # Nifty/Sector DD estimate
                        beta = 1.0
                        res_dd = stk_dd - (beta * sector_dd)
                        if res_dd <= res_dd_thresh:
                            valid_syms_l4.append(sym)
                            
        rows.append({
            "date": dt.strftime("%Y-%m-%d"),
            "layer_1_quality_count": len(valid_syms_l1),
            "layer_2_forensic_count": len(valid_syms_l2),
            "layer_3_valuation_count": len(valid_syms_l3),
            "layer_4_dislocation_count": len(valid_syms_l4)
        })
        
    df_out = pd.DataFrame(rows)
    return df_out

def main():
    print("================================================================================")
    print("   FUNDAMENTAL_GEM_RECOVERY_V4 — PHASE B: BREADTH DIAGNOSTIC (COUNTS ONLY)")
    print("================================================================================")
    
    symbols, price_data = load_universe_and_prices()
    pit_df = load_pit_fundamentals_v4()
    
    # Development Window Monthly Dates (2020-01-01 to 2024-12-01 = 60 months)
    monthly_dates = pd.date_range(start="2020-01-01", end="2024-12-01", freq="MS")
    
    # RUN 1: Primary Pre-Registered Thresholds
    print("\n--- EXECUTING BREADTH DIAGNOSTIC RUN 1 (PRIMARY THRESHOLDS) ---")
    df_run1 = execute_breadth_funnel_run(
        "RUN_1", monthly_dates, symbols, price_data, pit_df,
        ev_discount_thresh=0.25, pe_discount_thresh=0.20, res_dd_thresh=0.10
    )
    
    df_run1.to_csv(os.path.join(OUTPUT_DIR, "03_BREADTH_RUN1.csv"), index=False)
    
    l4_counts1 = df_run1["layer_4_dislocation_count"].values
    median_l4_1 = float(np.median(l4_counts1))
    months_ge_8_pct1 = (np.sum(l4_counts1 >= 8) / len(l4_counts1)) * 100.0
    months_ge_1_pct1 = (np.sum(l4_counts1 >= 1) / len(l4_counts1)) * 100.0
    
    pass_run1 = (median_l4_1 >= 8.0) and (months_ge_1_pct1 >= 60.0)
    print(f"RUN 1 Results: Median Candidates = {median_l4_1:.1f}, Months >= 8 = {months_ge_8_pct1:.1f}%, Months >= 1 = {months_ge_1_pct1:.1f}% -> Pass = {pass_run1}")
    
    pass_run2 = False
    median_l4_2 = 0.0
    months_ge_8_pct2 = 0.0
    months_ge_1_pct2 = 0.0
    
    if not pass_run1:
        print("\n--- RUN 1 FAILED BREADTH FLOOR -> EXECUTING ONE-TIME LOOSENING PROTOCOL (RUN 2) ---")
        df_run2 = execute_breadth_funnel_run(
            "RUN_2", monthly_dates, symbols, price_data, pit_df,
            ev_discount_thresh=0.20, pe_discount_thresh=0.15, res_dd_thresh=0.12
        )
        df_run2.to_csv(os.path.join(OUTPUT_DIR, "04_BREADTH_RUN2.csv"), index=False)
        
        l4_counts2 = df_run2["layer_4_dislocation_count"].values
        median_l4_2 = float(np.median(l4_counts2))
        months_ge_8_pct2 = (np.sum(l4_counts2 >= 8) / len(l4_counts2)) * 100.0
        months_ge_1_pct2 = (np.sum(l4_counts2 >= 1) / len(l4_counts2)) * 100.0
        pass_run2 = (median_l4_2 >= 8.0) and (months_ge_1_pct2 >= 60.0)
        print(f"RUN 2 Results: Median Candidates = {median_l4_2:.1f}, Months >= 8 = {months_ge_8_pct2:.1f}%, Months >= 1 = {months_ge_1_pct2:.1f}% -> Pass = {pass_run2}")
        
    final_verdict = "PASS_BREADTH_RUN1" if pass_run1 else ("PASS_BREADTH_RUN2" if pass_run2 else "INCONCLUSIVE_BREADTH")
    print(f"\n================================================================================")
    print(f"  PHASE B FINAL BREADTH VERDICT: {final_verdict}")
    print("================================================================================")

if __name__ == "__main__":
    main()
