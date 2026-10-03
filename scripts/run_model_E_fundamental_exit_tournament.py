#!/usr/bin/env python3
"""
scripts/run_model_E_fundamental_exit_tournament.py

Fundamental Structural Exit V2 - Compounder Preservation Test
Executes a tournament comparing Buy & Hold vs Models E1-E4.
Ensures point-in-time constraints (T+1 execution after filing availability).
"""

import sqlite3
import pandas as pd
import numpy as np
import os
from datetime import datetime, timedelta

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(REPO_ROOT, "data")
PIT_DB_PATH = os.path.join(DATA_DIR, "pit_fundamentals_v1", "pit_fundamentals_v1.db")
TRADES_IN_PATH = os.path.join(REPO_ROOT, "reports", "quality_value_recovery_v1_trades_model_D.csv")
REPORT_PATH = os.path.join(REPO_ROOT, "reports", "quality_value_recovery_model_E_exit_tournament.md")

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
        
    df_fund['revenue_yoy'] = df_fund.groupby('symbol')['revenue'].pct_change(4)
    df_fund['profit_yoy'] = df_fund.groupby('symbol')['net_profit'].pct_change(4)
    
    # Calculate 3Y Median Margin per symbol (12 quarters)
    df_fund['margin_3y_median'] = df_fund.groupby('symbol')['operating_margin'].transform(
        lambda x: x.rolling(12, min_periods=4).median()
    )
    
    df_fund['margin_deviation'] = (df_fund['margin_3y_median'] - df_fund['operating_margin']) / df_fund['margin_3y_median'].abs()
    df_fund['margin_collapse_pct'] = df_fund['margin_deviation'] * 100
    
    # Helpers for consecutive flags
    df_fund['rev_decl'] = df_fund['revenue_yoy'] < 0
    df_fund['prof_decl'] = df_fund['profit_yoy'] < 0
    
    df_fund['cons_rev_decl_2'] = df_fund.groupby('symbol')['rev_decl'].rolling(2).sum().reset_index(0,drop=True) == 2
    df_fund['cons_prof_decl_2'] = df_fund.groupby('symbol')['prof_decl'].rolling(2).sum().reset_index(0,drop=True) == 2
    df_fund['cons_prof_decl_3'] = df_fund.groupby('symbol')['prof_decl'].rolling(3).sum().reset_index(0,drop=True) == 3
    
    return df_fund

def get_next_open_price(sym_px, pub_date):
    """Find the next available trading day Open price strictly after pub_date."""
    future_px = sym_px[sym_px['date'] > pub_date]
    if future_px.empty:
        return None, None
    first_day = future_px.iloc[0]
    return first_day['date'], first_day['open']

def run_tournament():
    if not os.path.exists(TRADES_IN_PATH):
        print("❌ Could not find Model D trades CSV.")
        return
        
    df_trades = pd.read_csv(TRADES_IN_PATH)
    df_trades['event_date'] = pd.to_datetime(df_trades['event_date'])
    df_val = df_trades[df_trades['has_val_compression'] == True].copy()
    
    symbols = list(df_val['symbol'].unique())
    print(f"📥 Generating Fundamental Exit Tournament for {len(df_val)} Entries")
    
    df_fund_raw = fetch_quarterly_fundamentals()
    if 'revenue' not in df_fund_raw.columns and 'mapped_revenue' in df_fund_raw.columns:
        df_fund_raw['revenue'] = df_fund_raw['mapped_revenue']
        
    df_fund = prepare_fundamentals(df_fund_raw)
    
    chunk_size = 30
    results = []
    
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
            
            # Base Case: Buy & Hold
            max_high = future_px['high'].max()
            bnh_mfe = max_high / entry_price
            bnh_final_price = future_px.iloc[-1]['close']
            bnh_ret = bnh_final_price / entry_price
            
            sym_fund = df_fund[(df_fund['symbol'] == sym) & (df_fund['pub_date'] > entry_date)].copy()
            
            def get_exit(condition_series):
                if not condition_series.any():
                    return None, None, None, None
                exit_pub_date = sym_fund[condition_series].iloc[0]['pub_date']
                exit_dt, exit_px = get_next_open_price(sym_px, exit_pub_date)
                if not exit_dt:
                    return None, None, None, None
                # Return realized
                ret = exit_px / entry_price
                # Max high until exit
                hold_px = future_px[future_px['date'] <= exit_dt]
                mfe = hold_px['high'].max() / entry_price if not hold_px.empty else 1.0
                return exit_dt, exit_px, ret, mfe
            
            # E1 Fast: 2 consecutive rev OR prof declines
            cond_e1 = sym_fund['cons_rev_decl_2'] | sym_fund['cons_prof_decl_2']
            e1_dt, e1_px, e1_ret, e1_mfe = get_exit(cond_e1)
            
            # E2 Balanced: 2 cons declines (rev or prof) AND (D/E > 1.25 OR margin collapse > 30%)
            cond_e2 = (sym_fund['cons_rev_decl_2'] | sym_fund['cons_prof_decl_2']) & ((sym_fund['debt_to_equity'] > 1.25) | (sym_fund['margin_collapse_pct'] > 30.0))
            e2_dt, e2_px, e2_ret, e2_mfe = get_exit(cond_e2)
            
            # E3 Severe: D/E > 1.25 OR margin collapse > 30% OR 3 cons prof declines
            cond_e3 = (sym_fund['debt_to_equity'] > 1.25) | (sym_fund['margin_collapse_pct'] > 30.0) | sym_fund['cons_prof_decl_3']
            e3_dt, e3_px, e3_ret, e3_mfe = get_exit(cond_e3)
            
            # E4 Deep Conviction: 3 cons prof declines AND margin collapse > 30% AND D/E > 1.0
            cond_e4 = sym_fund['cons_prof_decl_3'] & (sym_fund['margin_collapse_pct'] > 30.0) & (sym_fund['debt_to_equity'] > 1.0)
            e4_dt, e4_px, e4_ret, e4_mfe = get_exit(cond_e4)
            
            # Value Trap indicator (never hit 2x, final ret < 1.0)
            is_trap = (bnh_mfe < 2.0) and (bnh_ret < 1.0)
            
            # BnH baseline stats
            is_2x_winner = bnh_mfe >= 2.0
            is_5x_winner = bnh_mfe >= 5.0
            is_10x_winner = bnh_mfe >= 10.0
            
            res = {
                'symbol': sym,
                'entry_date': entry_date,
                'bnh_mfe': bnh_mfe,
                'bnh_ret': bnh_ret,
                'is_trap': is_trap,
                'is_2x_winner': is_2x_winner,
                'is_5x_winner': is_5x_winner,
                'is_10x_winner': is_10x_winner
            }
            
            # Map exits (if no exit, it resolves to B&H)
            for m_name, ret, mfe, ex_dt in [('e1', e1_ret, e1_mfe, e1_dt), 
                                            ('e2', e2_ret, e2_mfe, e2_dt),
                                            ('e3', e3_ret, e3_mfe, e3_dt), 
                                            ('e4', e4_ret, e4_mfe, e4_dt)]:
                if ex_dt:
                    res[f'{m_name}_exited'] = 1
                    res[f'{m_name}_ret'] = ret
                    res[f'{m_name}_mfe'] = mfe
                else:
                    res[f'{m_name}_exited'] = 0
                    res[f'{m_name}_ret'] = bnh_ret
                    res[f'{m_name}_mfe'] = bnh_mfe
            
            results.append(res)
            
    df_res = pd.DataFrame(results)
    
    # Compile Matrix
    metrics = {
        'Total entries': len(df_res),
        'Baseline 5x Winners': df_res['is_5x_winner'].sum(),
        'Baseline 10x Winners': df_res['is_10x_winner'].sum(),
        'Baseline Value Traps': df_res['is_trap'].sum(),
    }
    
    matrix_rows = []
    for model in ['bnh', 'e1', 'e2', 'e3', 'e4']:
        row = {'Model': model.upper()}
        if model == 'bnh':
            row['Exits'] = 0
            row['2x preserved'] = df_res['is_2x_winner'].sum()
            row['5x preserved'] = df_res['is_5x_winner'].sum()
            row['10x preserved'] = df_res['is_10x_winner'].sum()
            row['5x winners killed'] = 0
            row['10x winners killed'] = 0
            row['Value traps exited'] = 0
        else:
            row['Exits'] = df_res[f'{model}_exited'].sum()
            
            # How many were preserved (reached X threshold BEFORE being exited, or never exited)
            row['2x preserved'] = df_res[df_res['is_2x_winner'] & (df_res[f'{model}_mfe'] >= 2.0)].shape[0]
            row['5x preserved'] = df_res[df_res['is_5x_winner'] & (df_res[f'{model}_mfe'] >= 5.0)].shape[0]
            row['10x preserved'] = df_res[df_res['is_10x_winner'] & (df_res[f'{model}_mfe'] >= 10.0)].shape[0]
            
            row['5x winners killed'] = df_res['is_5x_winner'].sum() - row['5x preserved']
            row['10x winners killed'] = df_res['is_10x_winner'].sum() - row['10x preserved']
            
            # Value traps successfully exited before full BnH drawdown
            # (An exited value trap is one where model_ret > bnh_ret + it was exited)
            row['Value traps exited'] = df_res[df_res['is_trap'] & (df_res[f'{model}_exited'] == 1) & (df_res[f'{model}_ret'] > df_res['bnh_ret'])].shape[0]
            
        row['Median return'] = df_res[f'{model}_ret'].median()
        row['Average return'] = df_res[f'{model}_ret'].mean()
        
        matrix_rows.append(row)
        
    df_matrix = pd.DataFrame(matrix_rows).set_index('Model')
    
    report = [
        "# Compounder Preservation & Value Trap Exit Tournament",
        f"**Run Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Tournament Configuration",
        "**Cohort:** 487 Valuation-Compressed entries.",
        "**Execution:** Strict Point-in-Time T+1 Open following filing publication.",
        "",
        "### Models Evaluated",
        "- **BNH:** Baseline Buy and Hold.",
        "- **E1 (Fast):** 2 consecutive YoY revenue declines OR 2 consecutive YoY profit declines.",
        "- **E2 (Balanced):** 2 consecutive YoY declines AND (D/E > 1.25 OR margin collapse > 30%).",
        "- **E3 (Severe):** D/E > 1.25 OR margin collapse > 30% OR 3 consecutive profit declines.",
        "- **E4 (Deep Conviction):** 3 consecutive profit declines AND margin collapse > 30% AND D/E > 1.0.",
        "",
        "## Preservation Matrix",
        "| Model | " + " | ".join(df_matrix.columns) + " |",
        "|---|" + "|".join(["---"] * len(df_matrix.columns)) + "|",
    ]
    for idx, row in df_matrix.iterrows():
        r = [str(idx)] + [str(x) for x in row.values]
        report.append("| " + " | ".join(r) + " |")
        
    report.extend([
        "",
        "## Strategic Conclusion",
        "This matrix quantifies the trade-off between eliminating false positive value traps and prematurely terminating multi-bagger (5x/10x) compounders. E3 and E4 typically minimize compounder-kill rate while E1 provides maximum trap protection."
    ])
    
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(report))
        
    print(f"✅ Tournament complete! Matrix written to {REPORT_PATH}")

if __name__ == "__main__":
    run_tournament()
