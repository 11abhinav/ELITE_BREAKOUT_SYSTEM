#!/usr/bin/env python3
"""
scripts/run_model_F_E3_OOS_certification.py

E3 Exit Engine - Final OOS Certification
Tests the formally locked E3 Fundamental Exit Engine on the 2019-2023 Out-of-Sample (OOS) cohort.
No parameter optimization is allowed.
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
REPORT_PATH = os.path.join(REPO_ROOT, "reports", "quality_value_recovery_model_F_OOS_certification.md")

def get_db_connection():
    conn = sqlite3.connect(PIT_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def fetch_quarterly_fundamentals():
    conn = get_db_connection()
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

def prepare_fundamentals(df_fund):
    df_fund = df_fund.sort_values(['symbol', 'pub_date']).copy()
    
    if 'total_debt' in df_fund.columns and 'total_equity' in df_fund.columns:
        df_fund['debt_to_equity'] = df_fund['total_debt'] / df_fund['total_equity'].replace(0, np.nan)
    else:
        df_fund['debt_to_equity'] = 0.0
        
    df_fund['profit_yoy'] = df_fund.groupby('symbol')['net_profit'].pct_change(4)
    df_fund['margin_3y_median'] = df_fund.groupby('symbol')['operating_margin'].transform(
        lambda x: x.rolling(12, min_periods=4).median()
    )
    df_fund['margin_deviation'] = (df_fund['margin_3y_median'] - df_fund['operating_margin']) / df_fund['margin_3y_median'].abs()
    df_fund['margin_collapse_pct'] = df_fund['margin_deviation'] * 100
    df_fund['prof_decl'] = df_fund['profit_yoy'] < 0
    df_fund['cons_prof_decl_3'] = df_fund.groupby('symbol')['prof_decl'].rolling(3).sum().reset_index(0,drop=True) == 3
    
    # E3 LOCKED TRIGGERS
    df_fund['trig_debt'] = df_fund['debt_to_equity'] > 1.25
    df_fund['trig_margin'] = df_fund['margin_collapse_pct'] > 30.0
    df_fund['trig_prof'] = df_fund['cons_prof_decl_3']
    df_fund['trig_e3'] = df_fund['trig_debt'] | df_fund['trig_margin'] | df_fund['trig_prof']
    
    return df_fund

def get_next_open_price(sym_px, pub_date):
    future_px = sym_px[sym_px['date'] > pub_date]
    if future_px.empty:
        return None, None
    first_day = future_px.iloc[0]
    return first_day['date'], first_day['open']

def run_oos_certification():
    if not os.path.exists(TRADES_IN_PATH):
        print("❌ Could not find Model D trades CSV.")
        return
        
    df_trades = pd.read_csv(TRADES_IN_PATH)
    df_trades['event_date'] = pd.to_datetime(df_trades['event_date'])
    df_val = df_trades[df_trades['has_val_compression'] == True].copy()
    
    # ISOLATE OUT OF SAMPLE (OOS) EPOCH: 2019-2023
    df_oos = df_val[(df_val['event_date'].dt.year >= 2019) & (df_val['event_date'].dt.year <= 2023)].copy()
    
    symbols = list(df_oos['symbol'].unique())
    print(f"📥 Generating E3 OOS Certification for {len(df_oos)} Entries (2019-2023)")
    
    df_fund_raw = fetch_quarterly_fundamentals()
    df_fund = prepare_fundamentals(df_fund_raw)
    
    results = []
    chunk_size = 30
    
    for i in range(0, len(symbols), chunk_size):
        chunk_syms = symbols[i:i+chunk_size]
        df_px = fetch_prices(chunk_syms)
        if df_px.empty: continue
            
        for idx, trade in df_oos[df_oos['symbol'].isin(chunk_syms)].iterrows():
            sym = trade['symbol']
            entry_date = trade['event_date']
            
            sym_px = df_px[df_px['symbol'] == sym].sort_values('date').copy()
            future_px = sym_px[sym_px['date'] > entry_date].copy()
            if future_px.empty: continue
            
            entry_price = future_px.iloc[0]['open']
            max_high = future_px['high'].max()
            
            bnh_mfe = max_high / entry_price
            bnh_final_price = future_px.iloc[-1]['close']
            bnh_ret = bnh_final_price / entry_price
            
            sym_fund = df_fund[(df_fund['symbol'] == sym) & (df_fund['pub_date'] > entry_date)].copy()
            cond_e3 = sym_fund['trig_e3']
            
            e3_ret = bnh_ret
            e3_mfe = bnh_mfe
            exited = 0
            
            if cond_e3.any():
                exit_pub_date = sym_fund[cond_e3].iloc[0]['pub_date']
                exit_dt, exit_px = get_next_open_price(sym_px, exit_pub_date)
                
                if exit_dt:
                    e3_ret = exit_px / entry_price
                    hold_px = future_px[future_px['date'] <= exit_dt]
                    e3_mfe = hold_px['high'].max() / entry_price if not hold_px.empty else 1.0
                    exited = 1
                    
            is_trap = (bnh_mfe < 2.0) and (bnh_ret < 1.0)
            is_5x_winner = bnh_mfe >= 5.0
            is_10x_winner = bnh_mfe >= 10.0
            
            results.append({
                'symbol': sym,
                'entry_date': entry_date,
                'bnh_mfe': bnh_mfe,
                'bnh_ret': bnh_ret,
                'e3_ret': e3_ret,
                'e3_mfe': e3_mfe,
                'exited': exited,
                'is_trap': is_trap,
                'is_5x': is_5x_winner,
                'is_10x': is_10x_winner
            })
            
    df_res = pd.DataFrame(results)
    
    # Calculate OOS Matrix metrics
    metrics = {
        'Total entries': len(df_res),
        'BNH 5x Winners': df_res['is_5x'].sum(),
        'BNH 10x Winners': df_res['is_10x'].sum(),
        'BNH Value Traps': df_res['is_trap'].sum(),
        'BNH Median Ret': df_res['bnh_ret'].median(),
        
        'E3 Exits': df_res['exited'].sum(),
        'E3 5x Preserved': df_res[df_res['is_5x'] & (df_res['e3_mfe'] >= 5.0)].shape[0],
        'E3 10x Preserved': df_res[df_res['is_10x'] & (df_res['e3_mfe'] >= 10.0)].shape[0],
        'E3 Traps Exited': df_res[df_res['is_trap'] & (df_res['exited'] == 1) & (df_res['e3_ret'] > df_res['bnh_ret'])].shape[0],
        'E3 Median Ret': df_res['e3_ret'].median()
    }
    
    killed_5x = metrics['BNH 5x Winners'] - metrics['E3 5x Preserved']
    killed_10x = metrics['BNH 10x Winners'] - metrics['E3 10x Preserved']
    
    report = [
        "# Fundamental Exit Engine - Final OOS Certification",
        f"**Run Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Certification Scope",
        "- **Epoch:** 2019-2023 (Out of Sample)",
        f"- **Entries:** {metrics['Total entries']}",
        "- **Locked Logic:** E3 (Margin < -30% OR D/E > 1.25 OR 3 YoY Profit Declines)",
        "- **Execution:** T+1 Open post-filing availability",
        "",
        "## Performance Integrity & Preservation",
        "| Metric | Buy & Hold | E3 Exit Engine |",
        "|---|---|---|",
        f"| Median Return | {metrics['BNH Median Ret']:.2f}x | {metrics['E3 Median Ret']:.2f}x |",
        f"| 5x Winners Preserved | {metrics['BNH 5x Winners']} | {metrics['E3 5x Preserved']} |",
        f"| 10x Winners Preserved | {metrics['BNH 10x Winners']} | {metrics['E3 10x Preserved']} |",
        f"| Value Traps Handled | 0 | {metrics['E3 Traps Exited']} exited securely |",
        f"| False Positives (Kills) | N/A | {killed_5x} (5x) / {killed_10x} (10x) |",
        "",
        "## Certification Status",
        "If OOS matches the DEV expectations (high preservation, meaningful trap exit), E3 is CERTIFIED for production."
    ]
    
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(report))
        
    print(f"✅ OOS Certification complete! Output at {REPORT_PATH}")

if __name__ == "__main__":
    run_oos_certification()
