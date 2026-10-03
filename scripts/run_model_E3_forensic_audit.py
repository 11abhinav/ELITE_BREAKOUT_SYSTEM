#!/usr/bin/env python3
"""
scripts/run_model_E3_forensic_audit.py

Perform deep forensic inspection of the E3 Fundamental Exit Model before locking.
1. Breakdown of 173 E3 Exits by precise trigger.
2. Forensic reconstruction of the 3 prematurely killed 5x/10x winners.
3. Quality of the 42 Value Trap exits (loss avoided, trough reached).
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
REPORT_PATH = os.path.join(REPO_ROOT, "reports", "quality_value_recovery_model_E3_forensic_audit.md")

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
    
    # Identify triggers
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

def run_forensic_audit():
    if not os.path.exists(TRADES_IN_PATH):
        return
        
    df_trades = pd.read_csv(TRADES_IN_PATH)
    df_trades['event_date'] = pd.to_datetime(df_trades['event_date'])
    df_val = df_trades[df_trades['has_val_compression'] == True].copy()
    
    symbols = list(df_val['symbol'].unique())
    df_fund_raw = fetch_quarterly_fundamentals()
    df_fund = prepare_fundamentals(df_fund_raw)
    
    results = []
    chunk_size = 30
    
    for i in range(0, len(symbols), chunk_size):
        chunk_syms = symbols[i:i+chunk_size]
        df_px = fetch_prices(chunk_syms)
        if df_px.empty: continue
            
        for idx, trade in df_val[df_val['symbol'].isin(chunk_syms)].iterrows():
            sym = trade['symbol']
            entry_date = trade['event_date']
            
            sym_px = df_px[df_px['symbol'] == sym].sort_values('date').copy()
            future_px = sym_px[sym_px['date'] > entry_date].copy()
            if future_px.empty: continue
            
            entry_price = future_px.iloc[0]['open']
            max_high = future_px['high'].max()
            max_high_date = future_px.loc[future_px['high'].idxmax()]['date']
            
            bnh_mfe = max_high / entry_price
            bnh_final_price = future_px.iloc[-1]['close']
            bnh_ret = bnh_final_price / entry_price
            
            sym_fund = df_fund[(df_fund['symbol'] == sym) & (df_fund['pub_date'] > entry_date)].copy()
            cond_e3 = sym_fund['trig_e3']
            
            if not cond_e3.any():
                continue
                
            trigger_row = sym_fund[cond_e3].iloc[0]
            exit_pub_date = trigger_row['pub_date']
            
            # Record exact trigger breakdown
            is_margin = trigger_row['trig_margin']
            is_debt = trigger_row['trig_debt']
            is_prof = trigger_row['trig_prof']
            
            exit_dt, exit_px = get_next_open_price(sym_px, exit_pub_date)
            if not exit_dt: continue
            
            hold_px = future_px[future_px['date'] <= exit_dt]
            post_exit_px = future_px[future_px['date'] > exit_dt]
            
            e3_ret = exit_px / entry_price
            e3_mfe = hold_px['high'].max() / entry_price if not hold_px.empty else 1.0
            
            post_exit_trough = post_exit_px['low'].min() if not post_exit_px.empty else exit_px
            post_exit_trough_date = post_exit_px.loc[post_exit_px['low'].idxmin()]['date'] if not post_exit_px.empty else exit_dt
            
            is_trap = (bnh_mfe < 2.0) and (bnh_ret < 1.0)
            is_5x_winner = bnh_mfe >= 5.0
            is_10x_winner = bnh_mfe >= 10.0
            
            results.append({
                'symbol': sym,
                'entry_date': entry_date,
                'entry_price': entry_price,
                'exit_date': exit_dt,
                'exit_price': exit_px,
                'e3_ret': e3_ret,
                'bnh_mfe': bnh_mfe,
                'max_high_date': max_high_date,
                'bnh_ret': bnh_ret,
                'is_margin': is_margin,
                'is_debt': is_debt,
                'is_prof': is_prof,
                'post_trough_px': post_exit_trough,
                'post_trough_date': post_exit_trough_date,
                'is_trap': is_trap,
                'is_5x': is_5x_winner,
                'is_10x': is_10x_winner,
                'e3_mfe': e3_mfe
            })
            
    df_res = pd.DataFrame(results)
    
    # Breakdown of Triggers
    df_res['trigger_type'] = df_res.apply(lambda r: 
        'Multiple' if (r['is_margin'] + r['is_debt'] + r['is_prof']) > 1 
        else 'Margin' if r['is_margin'] 
        else 'Debt' if r['is_debt'] 
        else 'Profit', axis=1)
        
    trigger_counts = df_res['trigger_type'].value_counts()
    
    # 5X / 10X Winners Killed
    killed = df_res[(df_res['is_5x'] == True) & (df_res['e3_mfe'] < 5.0)].copy()
    
    killed_report = []
    for _, r in killed.iterrows():
        cat = "10x" if r['is_10x'] else "5x"
        rec = (f"- **{r['symbol']} ({cat} winner)**: Entered on {r['entry_date'].strftime('%Y-%m-%d')} at ₹{r['entry_price']:.1f}. "
               f"Exited on {r['exit_date'].strftime('%Y-%m-%d')} at ₹{r['exit_price']:.1f} (Return: {r['e3_ret']:.2f}x). "
               f"**Trigger:** {r['trigger_type']}. "
               f"Eventual MFE: {r['bnh_mfe']:.1f}x reached on {r['max_high_date'].strftime('%Y-%m-%d')}.")
        killed_report.append(rec)
        
    # Value Traps Exited Analysis
    traps = df_res[(df_res['is_trap'] == True) & (df_res['e3_ret'] > df_res['bnh_ret'])].copy()
    traps['loss_avoided_pct'] = ((traps['exit_price'] - traps['post_trough_px']) / traps['exit_price']) * 100
    traps['days_to_trough'] = (pd.to_datetime(traps['post_trough_date']) - pd.to_datetime(traps['exit_date'])).dt.days
    
    avg_loss_avoided = traps['loss_avoided_pct'].mean()
    median_days_trough = traps['days_to_trough'].median()
    
    report = [
        "# E3 Severe Forensic Audit",
        f"**Run Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 1. Trigger Attribution",
        "Out of 173 E3 exits, here is the exact structural failure that triggered the exit:",
        "| Trigger Type | Count |",
        "|---|---|",
    ]
    for k, v in trigger_counts.items():
        report.append(f"| {k} | {v} |")
        
    report.extend([
        "",
        "## 2. Forensic Reconstruction: Compounder False Positives",
        f"Total prematurely exited 5x/10x winners: {len(killed)}",
        ""
    ])
    report.extend(killed_report)
    
    report.extend([
        "",
        "## 3. Value Trap Quality Analysis",
        f"**Total Traps successfully exited:** {len(traps)}",
        f"- **Average additional loss avoided:** {avg_loss_avoided:.1f}%",
        f"- **Median time from exit to absolute trough:** {median_days_trough} days",
        "",
        "### Sample Value Trap Executions (Top 5 by Loss Avoided)",
        "| Symbol | Entry Date | Exit Date | Exit Price | Post-Exit Trough | Loss Avoided |",
        "|---|---|---|---|---|---|"
    ])
    
    top_traps = traps.sort_values('loss_avoided_pct', ascending=False).head(5)
    for _, r in top_traps.iterrows():
        report.append(f"| {r['symbol']} | {r['entry_date'].strftime('%Y-%m-%d')} | {r['exit_date'].strftime('%Y-%m-%d')} | ₹{r['exit_price']:.1f} | ₹{r['post_trough_px']:.1f} | {r['loss_avoided_pct']:.1f}% |")
        
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(report))
        
    print(f"✅ Forensic audit complete! Output at {REPORT_PATH}")

if __name__ == "__main__":
    run_forensic_audit()
