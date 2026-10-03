#!/usr/bin/env python3
"""
scripts/run_model_D_fundamental_exit_v2.py

Fundamental Structural Exit V2 + Compounder Preservation Test
-------------------------------------------------------------
1. Freezes the 487 original Valuation-Compressed entries.
2. Epoch Splitting: Dev (2010-2018), OOS (2019-2023), Holdout (2024-2026).
3. Compounder Preservation Matrix: Evaluates if our fundamental exits prematurely cut off 5x/10x winners.
"""

import sqlite3
import pandas as pd
import numpy as np
import os
from datetime import datetime

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(REPO_ROOT, "data")
PIT_DB_PATH = os.path.join(DATA_DIR, "pit_fundamentals_v1", "pit_fundamentals_v1.db")
TRADES_IN_PATH = os.path.join(REPO_ROOT, "reports", "quality_value_recovery_v1_trades_model_D.csv")
REPORT_PATH = os.path.join(REPO_ROOT, "reports", "quality_value_recovery_model_D_fundamental_exit_v2.md")

def get_db_connection():
    conn = sqlite3.connect(PIT_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def fetch_quarterly_fundamentals():
    conn = get_db_connection()
    # Safely query available fields. Assuming net_profit, revenue/sales exist.
    # We will just fetch all columns for QUARTERLY
    query = """
    SELECT *
    FROM pit_fundamentals_v1
    WHERE statement_type = 'QUARTERLY'
    ORDER BY symbol, conservative_availability_timestamp ASC
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if 'conservative_availability_timestamp' in df.columns:
        df['pub_date'] = pd.to_datetime(df['conservative_availability_timestamp']).dt.date
        df['pub_date'] = pd.to_datetime(df['pub_date'])
    return df

def fetch_prices(symbols):
    df_list = []
    for sym in symbols:
        path = os.path.join(DATA_DIR, "history", "1d", f"{sym}.parquet")
        if not os.path.exists(path):
            path = os.path.join(DATA_DIR, "history", "1d", f"{sym}.NS.parquet")
        if os.path.exists(path):
            try:
                pdf = pd.read_parquet(path)
                pdf.columns = [c.lower() for c in pdf.columns]
                pdf = pdf[['date', 'open', 'close', 'high', 'low']].copy()
                pdf['symbol'] = sym
                pdf['date'] = pd.to_datetime(pdf['date']).dt.tz_localize(None).dt.date
                pdf['date'] = pd.to_datetime(pdf['date'])
                df_list.append(pdf)
            except Exception:
                pass
    if df_list:
        return pd.concat(df_list, ignore_index=True)
    return pd.DataFrame()

def extract_fundamental_metrics(df_fund, symbol, entry_date):
    # Filter for symbol and quarters available before or at evaluation point
    sym_fund = df_fund[df_fund['symbol'] == symbol].copy()
    sym_fund = sym_fund.sort_values('pub_date')
    
    # Debt/Equity
    if 'total_debt' in sym_fund.columns and 'total_equity' in sym_fund.columns:
        sym_fund['debt_to_equity'] = sym_fund['total_debt'] / sym_fund['total_equity'].replace(0, np.nan)
    else:
        sym_fund['debt_to_equity'] = 0.0
        
    # We need YoY growth for Revenue and Profit
    # Since it's quarterly data, YoY is shift(4) if continuous, but it's better to match by quarter if possible.
    # For simplicity, assuming ordered quarterly data, we can just use shift(4)
    sym_fund['revenue_yoy'] = sym_fund['revenue'].pct_change(4)
    sym_fund['profit_yoy'] = sym_fund['net_profit'].pct_change(4)
    
    # 3Y Margin Median (3 years = 12 quarters)
    sym_fund['margin_3y_median'] = sym_fund['operating_margin'].rolling(12, min_periods=4).median()
    
    return sym_fund

def run_experiment():
    if not os.path.exists(TRADES_IN_PATH):
        print("❌ Could not find Model D trades CSV.")
        return
        
    df_trades = pd.read_csv(TRADES_IN_PATH)
    df_trades['event_date'] = pd.to_datetime(df_trades['event_date'])
    df_val = df_trades[df_trades['has_val_compression'] == True].copy()
    
    def assign_epoch(d):
        y = d.year
        if y <= 2018: return 'DEV'
        elif y <= 2023: return 'OOS'
        else: return 'HOLDOUT'
        
    df_val['epoch'] = df_val['event_date'].apply(assign_epoch)
    symbols = list(df_val['symbol'].unique())
    print(f"📥 Generating Fundamental Exit Matrix for {len(df_val)} Trades (DEV: {sum(df_val['epoch']=='DEV')}, OOS: {sum(df_val['epoch']=='OOS')}, HOLDOUT: {sum(df_val['epoch']=='HOLDOUT')})")
    
    df_fund = fetch_quarterly_fundamentals()
    if 'revenue' not in df_fund.columns and 'mapped_revenue' in df_fund.columns:
        df_fund['revenue'] = df_fund['mapped_revenue']
        
    df_dev = df_val[df_val['epoch'] == 'DEV'].copy()
    print("⏳ Processing prices and identifying Buy & Hold Compounders...")
    chunk_size = 30
    bnh_results = []
    
    for i in range(0, len(symbols), chunk_size):
        chunk_syms = symbols[i:i+chunk_size]
        df_px = fetch_prices(chunk_syms)
        if df_px.empty: continue
            
        for idx, trade in df_val[df_val['symbol'].isin(chunk_syms)].iterrows():
            sym = trade['symbol']
            event_date = trade['event_date']
            epoch = trade['epoch']
            
            sym_px = df_px[df_px['symbol'] == sym].sort_values('date').copy()
            future_px = sym_px[sym_px['date'] > event_date].copy()
            if future_px.empty: continue
            
            entry_price = future_px.iloc[0]['open']
            max_high = future_px['high'].max()
            mfe_mult = max_high / entry_price
            
            bnh_results.append({
                'symbol': sym,
                'event_date': event_date,
                'epoch': epoch,
                'entry_price': entry_price,
                'max_high': max_high,
                'mfe_mult': mfe_mult
            })
            
    df_bnh = pd.DataFrame(bnh_results)
    c_5x = df_bnh[df_bnh['mfe_mult'] >= 5.0].copy()
    c_10x = df_bnh[df_bnh['mfe_mult'] >= 10.0].copy()
    print(f"✅ Identified {len(c_5x)} 5x compounders and {len(c_10x)} 10x compounders in the full cohort.")
    
    print("🔬 Processing Fundamental YoY Analytics for DEV epoch (Threshold Optimization)...")
    
    dev_results = []
    for idx, trade in df_dev.iterrows():
        sym = trade['symbol']
        entry_date = trade['event_date']
        
        sym_fund = extract_fundamental_metrics(df_fund, sym, entry_date)
        post_entry = sym_fund[sym_fund['pub_date'] > entry_date].copy()
        
        if post_entry.empty:
            continue
            
        # Check metrics across the entire post-entry lifespan for threshold tuning
        # e.g., max consecutive revenue declines, profit declines, margin deviations
        
        # Count consecutive negative YoY quarters
        rev_yoy = post_entry['revenue_yoy'] < 0
        prof_yoy = post_entry['profit_yoy'] < 0
        
        def max_consecutive_trues(s):
            return (s.groupby((~s).cumsum()).sum()).max() if s.any() else 0
            
        max_cons_rev_decl = max_consecutive_trues(rev_yoy)
        max_cons_prof_decl = max_consecutive_trues(prof_yoy)
        
        # Max Margin Deviation from 3Y Median
        post_entry['margin_deviation'] = (post_entry['margin_3y_median'] - post_entry['operating_margin']) / post_entry['margin_3y_median'].abs()
        max_margin_collapse = post_entry['margin_deviation'].max() * 100 # In percentage
        
        max_de = post_entry['debt_to_equity'].max()
        
        # Did this DEV event produce a 5x or 10x winner?
        is_5x = 1 if sym in c_5x['symbol'].values else 0
        is_10x = 1 if sym in c_10x['symbol'].values else 0
        
        dev_results.append({
            'symbol': sym,
            'event_date': entry_date,
            'max_cons_rev_decl': max_cons_rev_decl,
            'max_cons_prof_decl': max_cons_prof_decl,
            'max_margin_collapse_pct': max_margin_collapse,
            'max_debt_equity': max_de,
            'is_5x': is_5x,
            'is_10x': is_10x
        })
        
    df_dev_res = pd.DataFrame(dev_results)
    
    report = [
        "# Fundamental Structural Exit V2: Architecture & Development",
        f"**Run Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 1. Cohort Freeze & Epoch Split",
        f"- **Development (2010-2018):** {sum(df_val['epoch']=='DEV')} events",
        f"- **OOS (2019-2023):** {sum(df_val['epoch']=='OOS')} events",
        f"- **Forward Holdout (2024-2026):** {sum(df_val['epoch']=='HOLDOUT')} events",
        "",
        "## 2. Buy & Hold True Wealth Generation Baseline",
        f"- **5x Winners Total:** {len(c_5x)}",
        f"- **10x Winners Total:** {len(c_10x)}",
        "",
        "## 3. DEV Epoch Threshold Analytics",
        "Distribution of extreme fundamental decay events across DEV candidates:",
        "",
        "### Max Consecutive YoY Revenue Declines",
        df_dev_res['max_cons_rev_decl'].value_counts().sort_index().to_markdown(),
        "",
        "### Max Consecutive YoY Profit Declines",
        df_dev_res['max_cons_prof_decl'].value_counts().sort_index().to_markdown(),
        "",
        "### Max Debt/Equity Observed",
        pd.cut(df_dev_res['max_debt_equity'], bins=[-1, 1, 1.25, 1.5, 2.0, 100]).value_counts().sort_index().to_markdown(),
        "",
        "### Max Margin Collapse Below 3Y Median",
        pd.cut(df_dev_res['max_margin_collapse_pct'], bins=[-1000, 20, 25, 30, 35, 1000]).value_counts().sort_index().to_markdown(),
        "",
        "## 4. Fundamental Models Scheduled",
        "- **E1 (Aggressive):** ANY single structural flag",
        "- **E2 (Balanced):** 2 independent flags simultaneously",
        "- **E3 (Severe):** 1 severe flag (e.g. D/E > 2.0 or Margin collapse > 35%)",
        "- **E4 (Persistent):** Multi-quarter deterioration across metrics",
        ""
    ]
    
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(report))
        
    print(f"📊 Development Analytics complete at {REPORT_PATH}")

if __name__ == "__main__":
    run_experiment()
