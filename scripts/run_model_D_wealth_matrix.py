#!/usr/bin/env python3
"""
scripts/run_model_D_wealth_matrix.py

Decisive Wealth Matrix for Model D
Evaluates exactly: V2-A, V2-B, V2-Control, Buy & Hold Control
Across fixed horizons: 1Y, 3Y, 5Y
Metrics: CAGR, Total Return, Max Drawdown, Multibagger frequency, etc.
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
REPORT_PATH = os.path.join(REPO_ROOT, "reports", "quality_value_recovery_model_D_wealth_matrix.md")

def get_db_connection(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def fetch_pit_exits():
    conn = get_db_connection(PIT_DB_PATH)
    query = """
    SELECT symbol, conservative_availability_timestamp, net_profit, roce
    FROM pit_fundamentals_v1
    WHERE statement_type = 'ANNUAL'
    ORDER BY symbol, conservative_availability_timestamp ASC
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
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

def run_wealth_matrix():
    if not os.path.exists(TRADES_IN_PATH):
        print("❌ Could not find Model D trades CSV.")
        return
        
    df_trades = pd.read_csv(TRADES_IN_PATH)
    df_trades['event_date'] = pd.to_datetime(df_trades['event_date'])
    df_val = df_trades[df_trades['has_val_compression'] == True].copy()
    symbols = list(df_val['symbol'].unique())
    print(f"📥 Generating Wealth Matrix for {len(df_val)} Trades across {len(symbols)} symbols...")
    
    df_fund = fetch_pit_exits()
    chunk_size = 30
    
    variants = ['V2-A', 'V2-B', 'V2-Control', 'Buy-and-Hold']
    results = {v: [] for v in variants}
    
    for i in range(0, len(symbols), chunk_size):
        chunk_syms = symbols[i:i+chunk_size]
        df_px = fetch_prices(chunk_syms)
        if df_px.empty: continue
            
        for idx, trade in df_val[df_val['symbol'].isin(chunk_syms)].iterrows():
            sym = trade['symbol']
            event_date = trade['event_date']
            
            sym_px = df_px[df_px['symbol'] == sym].sort_values('date').copy()
            sym_px['sma_200'] = sym_px['close'].rolling(200).mean()
            
            future_px = sym_px[sym_px['date'] > event_date].copy()
            if future_px.empty: continue
            
            entry_row = future_px.iloc[0]
            entry_date = entry_row['date']
            entry_price = entry_row['open']
            
            hold_px = future_px.copy()
            hold_px['above_sma200'] = hold_px['close'] > hold_px['sma_200']
            hold_px['below_sma200'] = hold_px['close'] < hold_px['sma_200']
            hold_px['armed'] = hold_px['above_sma200'].cumsum() > 0
            hold_px['streak_below'] = hold_px['below_sma200'].groupby((~hold_px['below_sma200']).cumsum()).cumsum()
            
            # Fundamental Exit Check
            sym_fund = df_fund[df_fund['symbol'] == sym].copy()
            sym_fund = sym_fund[sym_fund['pub_date'] > entry_date]
            fund_exit_date = None
            if not sym_fund.empty:
                bad_funds = sym_fund[(sym_fund['roce'] < 10.0) | (sym_fund['net_profit'] <= 0)]
                if not bad_funds.empty:
                    first_bad_pub = bad_funds.iloc[0]['pub_date']
                    exec_days = hold_px[hold_px['date'] > first_bad_pub]
                    if not exec_days.empty:
                        fund_exit_date = exec_days.iloc[0]['date']
            
            def process_variant(variant_type):
                tech_exit_date = None
                if variant_type in ['V2-A', 'V2-B']:
                    streak_req = 5 if variant_type == 'V2-A' else 3
                    valid_exits = hold_px[(hold_px['armed'] == True) & (hold_px['streak_below'] >= streak_req)]
                    if not valid_exits.empty:
                        trigger_idx = valid_exits.index[0]
                        trigger_loc = hold_px.index.get_loc(trigger_idx)
                        if trigger_loc + 1 < len(hold_px):
                            tech_exit_date = hold_px.iloc[trigger_loc + 1]['date']
                
                exit_date = None
                
                if variant_type == 'Buy-and-Hold':
                    exit_date = None
                elif tech_exit_date and fund_exit_date:
                    exit_date = min(tech_exit_date, fund_exit_date)
                elif tech_exit_date:
                    exit_date = tech_exit_date
                elif fund_exit_date:
                    exit_date = fund_exit_date
                
                # Analyze Horizons (1Y, 3Y, 5Y)
                horizons = {
                    '1Y': entry_date + pd.Timedelta(days=365),
                    '3Y': entry_date + pd.Timedelta(days=365*3),
                    '5Y': entry_date + pd.Timedelta(days=365*5)
                }
                
                h_results = {}
                for h_label, h_date in horizons.items():
                    # If trade exited before horizon, outcome is locked at exit
                    if exit_date and exit_date <= h_date:
                        actual_hold = hold_px[hold_px['date'] <= exit_date]
                        final_price = hold_px[hold_px['date'] == exit_date].iloc[0]['open']
                        active = False
                    else:
                        actual_hold = hold_px[hold_px['date'] <= h_date]
                        if actual_hold.empty:
                            h_results[h_label] = None
                            continue
                        final_price = actual_hold.iloc[-1]['close']
                        active = True
                        
                    ret_pct = (final_price / entry_price - 1) * 100
                    mfe = (actual_hold['high'].max() / entry_price - 1) * 100
                    mae = (actual_hold['low'].min() / entry_price - 1) * 100
                    
                    h_results[h_label] = {
                        'return_pct': ret_pct,
                        'mfe': mfe,
                        'mae': mae,
                        'active': active
                    }
                    
                # Full Lifecycle (for multibaggers)
                full_hold = hold_px[hold_px['date'] <= exit_date] if exit_date else hold_px
                full_mfe = (full_hold['high'].max() / entry_price)
                
                return {
                    'symbol': sym,
                    'entry_date': entry_date,
                    'exit_date': exit_date,
                    'full_mfe': full_mfe,
                    'horizons': h_results
                }
            
            for v in variants:
                results[v].append(process_variant(v))
                
    print("✅ All Variants Computed.")
    
    report = [
        "# Decisive Wealth Matrix - Model D",
        f"**Run Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ""
    ]
    
    for v in variants:
        v_data = results[v]
        report.append(f"## {v} Performance")
        
        # Multibaggers
        mb_2x = sum(1 for d in v_data if d['full_mfe'] >= 2.0)
        mb_3x = sum(1 for d in v_data if d['full_mfe'] >= 3.0)
        mb_5x = sum(1 for d in v_data if d['full_mfe'] >= 5.0)
        mb_10x = sum(1 for d in v_data if d['full_mfe'] >= 10.0)
        total_N = len(v_data)
        
        report.append(f"### Multibagger Lifecycle")
        report.append(f"- Reached 2x: {mb_2x} ({mb_2x/total_N*100:.1f}%)")
        report.append(f"- Reached 3x: {mb_3x} ({mb_3x/total_N*100:.1f}%)")
        report.append(f"- Reached 5x: {mb_5x} ({mb_5x/total_N*100:.1f}%)")
        report.append(f"- Reached 10x: {mb_10x} ({mb_10x/total_N*100:.1f}%)")
        report.append("")
        
        for h in ['1Y', '3Y', '5Y']:
            h_valid = [d['horizons'][h] for d in v_data if d['horizons'].get(h)]
            if not h_valid: continue
            
            rets = np.array([x['return_pct'] for x in h_valid])
            maes = np.array([x['mae'] for x in h_valid])
            active = sum(1 for x in h_valid if x['active'])
            
            med_ret = np.median(rets)
            avg_ret = np.mean(rets)
            med_mae = np.median(maes)
            
            # CAGR
            years = int(h[0])
            cagrs = ((1 + rets/100) ** (1/years) - 1) * 100
            med_cagr = np.median(cagrs)
            
            # Top Contribution
            sorted_rets = sorted(rets, reverse=True)
            top_10_sum = sum(sorted_rets[:int(len(rets)*0.10)])
            total_sum = sum(sorted_rets)
            conc = (top_10_sum / total_sum * 100) if total_sum > 0 else 0
            
            report.append(f"### Horizon: {h} (N={len(h_valid)})")
            report.append(f"- Still Active at {h}: {active/len(h_valid)*100:.1f}%")
            report.append(f"- Median Return: {med_ret:.1f}%")
            report.append(f"- Average Return: {avg_ret:.1f}%")
            report.append(f"- Median CAGR: {med_cagr:.1f}%")
            report.append(f"- Median Max Drawdown: {med_mae:.1f}%")
            report.append(f"- Winner Concentration (Top 10%): {conc:.1f}%")
            report.append("")

    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(report))
        
    print(f"📊 Wealth Matrix report generated at {REPORT_PATH}")

if __name__ == "__main__":
    run_wealth_matrix()
