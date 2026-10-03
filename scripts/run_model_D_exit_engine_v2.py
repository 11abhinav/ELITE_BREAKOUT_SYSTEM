#!/usr/bin/env python3
"""
scripts/run_model_D_exit_engine_v2.py

Model D Strategy Simulation with V2 Exit Engines
------------------------------------------------
V2-A: Reclaim → SMA200 armed → 5-close breakdown → exit
V2-B: Reclaim → SMA200 armed → 3-close breakdown → exit
V2-C: ATR-based structural lower-low exit
V2-Control: No technical exit (Fundamental exit only)

Fundamental Exit (All Variants):
- ROCE < 10% or Net Profit <= 0 (Using conservative_availability_timestamp).
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
REPORT_PATH = os.path.join(REPO_ROOT, "reports", "quality_value_recovery_model_D_exit_engine_v2_report.md")

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

def run_v2_simulation():
    if not os.path.exists(TRADES_IN_PATH):
        print("❌ Could not find Model D trades CSV.")
        return
        
    df_trades = pd.read_csv(TRADES_IN_PATH)
    df_trades['event_date'] = pd.to_datetime(df_trades['event_date'])
    df_val = df_trades[df_trades['has_val_compression'] == True].copy()
    symbols = list(df_val['symbol'].unique())
    print(f"📥 Simulating V2 Exit Engine for {len(df_val)} Trades across {len(symbols)} symbols...")
    
    df_fund = fetch_pit_exits()
    chunk_size = 30
    results = {'V2-A': [], 'V2-B': [], 'V2-C': [], 'V2-Control': []}
    
    for i in range(0, len(symbols), chunk_size):
        chunk_syms = symbols[i:i+chunk_size]
        df_px = fetch_prices(chunk_syms)
        if df_px.empty: continue
            
        for idx, trade in df_val[df_val['symbol'].isin(chunk_syms)].iterrows():
            sym = trade['symbol']
            event_date = trade['event_date']
            
            sym_px = df_px[df_px['symbol'] == sym].sort_values('date').copy()
            sym_px['sma_200'] = sym_px['close'].rolling(200).mean()
            sym_px['tr'] = np.maximum(
                sym_px['high'] - sym_px['low'],
                np.maximum(
                    np.abs(sym_px['high'] - sym_px['close'].shift(1)),
                    np.abs(sym_px['low'] - sym_px['close'].shift(1))
                )
            )
            sym_px['atr_14'] = sym_px['tr'].rolling(14).mean()
            
            future_px = sym_px[sym_px['date'] > event_date].copy()
            if future_px.empty: continue
            
            entry_row = future_px.iloc[0]
            entry_date = entry_row['date']
            entry_price = entry_row['open']
            entry_atr = entry_row['atr_14']
            if pd.isna(entry_atr): entry_atr = entry_price * 0.05
            
            hold_px = future_px.copy()
            hold_px['above_sma200'] = hold_px['close'] > hold_px['sma_200']
            hold_px['below_sma200'] = hold_px['close'] < hold_px['sma_200']
            
            # Cumulative armed state (once True, stays True)
            hold_px['armed'] = hold_px['above_sma200'].cumsum() > 0
            
            # V2-A/B Technical Checks
            hold_px['streak_below'] = hold_px['below_sma200'].groupby((~hold_px['below_sma200']).cumsum()).cumsum()
            
            # V2-C Technical Check (Lower low structural exit)
            atr_stop = entry_price - (3 * entry_atr)
            
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
            
            def get_exit(variant_type):
                tech_exit_date = None
                
                if variant_type in ['V2-A', 'V2-B']:
                    streak_req = 5 if variant_type == 'V2-A' else 3
                    valid_exits = hold_px[(hold_px['armed'] == True) & (hold_px['streak_below'] >= streak_req)]
                    if not valid_exits.empty:
                        trigger_idx = valid_exits.index[0]
                        trigger_loc = hold_px.index.get_loc(trigger_idx)
                        if trigger_loc + 1 < len(hold_px):
                            tech_exit_date = hold_px.iloc[trigger_loc + 1]['date']
                
                elif variant_type == 'V2-C':
                    valid_exits = hold_px[hold_px['close'] < atr_stop]
                    if not valid_exits.empty:
                        trigger_idx = valid_exits.index[0]
                        trigger_loc = hold_px.index.get_loc(trigger_idx)
                        if trigger_loc + 1 < len(hold_px):
                            tech_exit_date = hold_px.iloc[trigger_loc + 1]['date']
                
                exit_date = None
                exit_reason = "ACTIVE_HOLD"
                
                if tech_exit_date and fund_exit_date:
                    if tech_exit_date < fund_exit_date:
                        exit_date = tech_exit_date
                        exit_reason = "TECHNICAL_BREAKDOWN"
                    else:
                        exit_date = fund_exit_date
                        exit_reason = "FUNDAMENTAL_DETERIORATION"
                elif tech_exit_date:
                    exit_date = tech_exit_date
                    exit_reason = "TECHNICAL_BREAKDOWN"
                elif fund_exit_date:
                    exit_date = fund_exit_date
                    exit_reason = "FUNDAMENTAL_DETERIORATION"
                
                if exit_date:
                    actual_hold = hold_px[hold_px['date'] <= exit_date]
                    exit_price = hold_px[hold_px['date'] == exit_date].iloc[0]['open']
                else:
                    actual_hold = hold_px
                    exit_price = hold_px.iloc[-1]['close']
                    exit_date = hold_px.iloc[-1]['date']
                    
                ret_pct = (exit_price / entry_price - 1) * 100
                mfe_pct = (actual_hold['high'].max() / entry_price - 1) * 100
                mae_pct = (actual_hold['low'].min() / entry_price - 1) * 100
                hold_days = (exit_date - entry_date).days
                
                # Check absolute maximum drawdown
                # A proper MAE could be defined as lowest low relative to entry
                
                return {
                    'symbol': sym,
                    'entry_date': entry_date,
                    'exit_date': exit_date,
                    'exit_reason': exit_reason,
                    'holding_period_days': hold_days,
                    'return_pct': ret_pct,
                    'mfe_pct': mfe_pct,
                    'mae_pct': mae_pct
                }
            
            for v in ['V2-A', 'V2-B', 'V2-C', 'V2-Control']:
                results[v].append(get_exit(v))
                
    print("✅ All V2 variants simulated.")
    
    # Generate Output
    report = [
        "# Model D Strategy Simulation & Exit Engine V2",
        f"**Run Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ""
    ]
    
    for v in ['V2-A', 'V2-B', 'V2-C', 'V2-Control']:
        df_sim = pd.DataFrame(results[v])
        if df_sim.empty: continue
        
        closed = df_sim[df_sim['exit_reason'] != "ACTIVE_HOLD"]
        win_rate = (closed['return_pct'] > 0).mean() * 100 if not closed.empty else 0
        
        sorted_rets = df_sim['return_pct'].sort_values(ascending=False)
        top_10 = sorted_rets.head(int(len(sorted_rets) * 0.10)).sum()
        total_ret = sorted_rets.sum()
        winner_concentration = (top_10 / total_ret * 100) if total_ret > 0 else 0
        
        reasons = df_sim['exit_reason'].value_counts(normalize=True) * 100
        
        # Determine 1Y, 3Y, 5Y holding points internally based on actual hold time or exit return
        # Since these are exits, the exact forward 1y/3y isn't uniform. 
        # But we calculate annualized CAGR on the closed trades to see true wealth creation rate.
        valid_cagr_trades = closed[closed['holding_period_days'] > 90].copy()
        if not valid_cagr_trades.empty:
            valid_cagr_trades['cagr'] = ((1 + valid_cagr_trades['return_pct']/100) ** (365/valid_cagr_trades['holding_period_days']) - 1) * 100
            median_cagr = valid_cagr_trades['cagr'].median()
        else:
            median_cagr = 0
            
        report.extend([
            f"## {v} Cohort",
            f"- Total Trades: {len(df_sim)} (Closed: {len(closed)})",
            f"- Win Rate (Closed): {win_rate:.1f}%",
            f"- Median Return: {df_sim['return_pct'].median():.1f}%",
            f"- Average Return: {df_sim['return_pct'].mean():.1f}%",
            f"- Median CAGR (Trades >90d): {median_cagr:.1f}%",
            f"- Median Holding Period: {df_sim['holding_period_days'].median():.0f} days (~{df_sim['holding_period_days'].median()/365:.1f} years)",
            f"- Median MAE (Drawdown): {df_sim['mae_pct'].median():.1f}%",
            f"- Winner Concentration: Top 10% contributed {winner_concentration:.1f}% of total",
            f"- **Exit Forensics:**",
            f"  - Technical Breakdown: {reasons.get('TECHNICAL_BREAKDOWN', 0):.1f}%",
            f"  - Fundamental Deterioration: {reasons.get('FUNDAMENTAL_DETERIORATION', 0):.1f}%",
            f"  - Still Active: {reasons.get('ACTIVE_HOLD', 0):.1f}%",
            ""
        ])

    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(report))
        
    print(f"📊 V2 Exit Engine report generated at {REPORT_PATH}")

if __name__ == "__main__":
    run_v2_simulation()
