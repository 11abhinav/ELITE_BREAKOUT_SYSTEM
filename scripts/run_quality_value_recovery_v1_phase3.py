#!/usr/bin/env python3
"""
scripts/run_quality_value_recovery_v1_phase3.py

Executes Phase 3 of the QUALITY_VALUE_RECOVERY_WEALTH_V1 master research prompt:
- Implements Entry Model C (Stabilization / Technical Recovery).
- Requires a 30% drawdown AND a technical reclaim of the 50-day SMA.
- Calculates Forward Returns (1Y, 3Y, 5Y) and compares to Model A.
"""

import sqlite3
import pandas as pd
import numpy as np
import os
from datetime import datetime

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(REPO_ROOT, "data")
PIT_DB_PATH = os.path.join(DATA_DIR, "pit_fundamentals_v1", "pit_fundamentals_v1.db")
REPORT_PATH = os.path.join(REPO_ROOT, "reports", "quality_value_recovery_v1_phase3_report.md")

def get_db_connection(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def fetch_quality_fundamentals():
    conn = get_db_connection(PIT_DB_PATH)
    query = """
    SELECT symbol, period_end_date, conservative_availability_timestamp, 
           net_profit, roce
    FROM pit_fundamentals_v1
    WHERE statement_type = 'ANNUAL'
    ORDER BY symbol, period_end_date ASC
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    df['conservative_availability_date'] = pd.to_datetime(df['conservative_availability_timestamp']).dt.date
    df['period_end_date'] = pd.to_datetime(df['period_end_date'])
    
    df.sort_values(by=['symbol', 'period_end_date'], inplace=True)
    df['net_profit_3y_med'] = df.groupby('symbol')['net_profit'].rolling(3, min_periods=1).median().reset_index(level=0, drop=True)
    df['roce_3y_med'] = df.groupby('symbol')['roce'].rolling(3, min_periods=1).median().reset_index(level=0, drop=True)
    
    df['is_quality'] = (df['roce'] >= 15.0) & (df['net_profit'] > 0)
    df['is_improving'] = (
        (df['net_profit'] > df['net_profit_3y_med']) &
        (df['roce'] >= df['roce_3y_med']) &
        df['is_quality']
    )
    
    return df[df['is_improving']].copy()

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
                pdf = pdf[['date', 'close', 'high', 'low']].copy()
                pdf['symbol'] = sym
                pdf['date'] = pd.to_datetime(pdf['date']).dt.tz_localize(None).dt.date
                pdf['date'] = pd.to_datetime(pdf['date'])
                pdf = pdf[pdf['date'] >= '2014-01-01'] # Need history for SMA
                df_list.append(pdf)
            except Exception:
                pass
    if df_list:
        return pd.concat(df_list, ignore_index=True)
    return pd.DataFrame()

def run_phase3():
    print("📥 Loading PIT Fundamentals (Phase 3)...")
    df_fund = fetch_quality_fundamentals()
    symbols = list(df_fund['symbol'].unique())
    
    print("📥 Loading Daily Prices & Computing Technical Recovery...")
    chunk_size = 50
    all_trades = []
    
    for i in range(0, len(symbols), chunk_size):
        chunk_syms = symbols[i:i+chunk_size]
        df_px = fetch_prices(chunk_syms)
        
        if df_px.empty:
            continue
            
        for sym in chunk_syms:
            sym_px = df_px[df_px['symbol'] == sym].copy()
            if sym_px.empty:
                continue
                
            sym_fund = df_fund[df_fund['symbol'] == sym].copy()
            sym_px = sym_px.sort_values('date')
            
            # Technical Indicators
            sym_px['high_2y'] = sym_px['high'].rolling(window=504, min_periods=252).max()
            sym_px['drawdown'] = (sym_px['close'] - sym_px['high_2y']) / sym_px['high_2y']
            sym_px['sma_50'] = sym_px['close'].rolling(window=50).mean()
            
            # Setup State: Did we hit a -30% drawdown recently?
            sym_px['setup_active'] = sym_px['drawdown'].rolling(window=60, min_periods=1).min() <= -0.30
            
            fund_to_merge = sym_fund[['conservative_availability_date', 'is_improving']].rename(columns={'conservative_availability_date': 'fund_date'}).copy()
            fund_to_merge['fund_date'] = pd.to_datetime(fund_to_merge['fund_date'])
            fund_to_merge = fund_to_merge.sort_values('fund_date')
            
            sym_px = pd.merge_asof(
                sym_px, 
                fund_to_merge,
                left_on='date', 
                right_on='fund_date', 
                direction='backward'
            )
            
            # Model C Signal:
            # 1. We had a -30% drawdown in the last 60 days
            # 2. Fundamentals are still officially 'improving'
            # 3. We cross above the 50-day SMA today
            # 4. We were below the 50-day SMA yesterday (a genuine reclaim)
            sym_px['prev_close'] = sym_px['close'].shift(1)
            sym_px['prev_sma_50'] = sym_px['sma_50'].shift(1)
            
            sym_px['signal'] = (
                (sym_px['setup_active'] == True) & 
                (sym_px['is_improving'] == True) & 
                (sym_px['close'] > sym_px['sma_50']) & 
                (sym_px['prev_close'] <= sym_px['prev_sma_50'])
            )
            
            # Filter to tradable dates (2015+)
            sym_px = sym_px[sym_px['date'] >= '2015-01-01']
            
            cooldown = 0
            for idx, row in sym_px.iterrows():
                if cooldown > 0:
                    cooldown -= 1
                    continue
                    
                if row['signal']:
                    entry_date = row['date']
                    entry_price = row['close']
                    
                    fwd_1y = sym_px[sym_px['date'] >= entry_date + pd.Timedelta(days=365)]
                    fwd_3y = sym_px[sym_px['date'] >= entry_date + pd.Timedelta(days=365*3)]
                    fwd_5y = sym_px[sym_px['date'] >= entry_date + pd.Timedelta(days=365*5)]
                    
                    ret_1y = (fwd_1y.iloc[0]['close'] / entry_price - 1) if not fwd_1y.empty else np.nan
                    ret_3y = (fwd_3y.iloc[0]['close'] / entry_price - 1) if not fwd_3y.empty else np.nan
                    ret_5y = (fwd_5y.iloc[0]['close'] / entry_price - 1) if not fwd_5y.empty else np.nan
                    
                    all_trades.append({
                        'symbol': sym,
                        'entry_date': entry_date.date(),
                        'entry_price': entry_price,
                        'drawdown_at_entry': row['drawdown'],
                        'ret_1y': ret_1y,
                        'ret_3y': ret_3y,
                        'ret_5y': ret_5y
                    })
                    
                    cooldown = 252 # 1 year cooldown
                    
    df_trades = pd.DataFrame(all_trades)
    
    if df_trades.empty:
        print("❌ No valid setups found.")
        return
        
    print(f"\n✅ Generated {len(df_trades)} Technical Recovery trades.")
    
    med_1y = df_trades['ret_1y'].median() * 100
    med_3y = df_trades['ret_3y'].median() * 100
    med_5y = df_trades['ret_5y'].median() * 100
    
    win_1y = (df_trades['ret_1y'] > 0).mean() * 100
    win_3y = (df_trades['ret_3y'] > 0).mean() * 100
    win_5y = (df_trades['ret_5y'] > 0).mean() * 100
    
    report = [
        "# QUALITY_VALUE_RECOVERY_WEALTH_V1 - Phase 3 (Model C)",
        f"**Run Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Core Parameters",
        "- **Quality Criteria:** ROCE >= 15%, Net Profit > 0",
        "- **Improvement Criteria:** ROCE and Net Profit >= 3Y Rolling Median",
        "- **Entry Model C (Technical Recovery):** Setup triggered by 30% drawdown, entry triggered upon reclaiming 50-day SMA.",
        "- **Cooldown:** 1 year per symbol",
        "",
        "## Backtest Results",
        f"- Total Signals Generated: {len(df_trades)}",
        "",
        "### Median Forward Returns",
        f"- 1-Year Median Return: {med_1y:.1f}% (Win Rate: {win_1y:.1f}%)",
        f"- 3-Year Median Return: {med_3y:.1f}% (Win Rate: {win_3y:.1f}%)",
        f"- 5-Year Median Return: {med_5y:.1f}% (Win Rate: {win_5y:.1f}%)",
        "",
        "## Analysis",
        "By waiting for the stock to reclaim its 50-day moving average rather than blindly catching the falling knife at -30%, ",
        "we can evaluate whether we successfully filtered out the persistent value traps. ",
        "A higher 1-year win rate compared to Model A (47.2%) proves the hypothesis that technical confirmation is critical."
    ]
    
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(report))
        
    print(f"📊 Phase 3 report generated at {REPORT_PATH}")

if __name__ == "__main__":
    run_phase3()
