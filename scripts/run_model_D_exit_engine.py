#!/usr/bin/env python3
"""
scripts/run_model_D_exit_engine.py

Model D Strategy Simulation with Portfolio Exit Engine
------------------------------------------------------
Executes the true strategy holding loop for every Valuation-Compressed event.
Simulates daily from Entry Date (T+1 Open) until an exit condition is triggered.

Exit Conditions:
1. Fundamental Structural Break:
   ROCE < 10% or Net Profit <= 0.
   Strictly enforced using `conservative_availability_timestamp` (T+1 execution after filing).
2. Persistent Technical Breakdown:
   5 consecutive trading days closing below the 200-day SMA.
   Executes on the Open of the 6th day.
   
Calculates:
- Entry/Exit Prices & Dates
- Exit Reason
- Holding Period
- Trade Return, MFE, MAE (Max Drawdown during trade)
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
REPORT_PATH = os.path.join(REPO_ROOT, "reports", "quality_value_recovery_model_D_exit_engine_report.md")

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

def run_simulation():
    if not os.path.exists(TRADES_IN_PATH):
        print("❌ Could not find Model D trades CSV.")
        return
        
    df_trades = pd.read_csv(TRADES_IN_PATH)
    df_trades['event_date'] = pd.to_datetime(df_trades['event_date'])
    
    # We strictly simulate only the Valuation-Compressed entries
    df_val = df_trades[df_trades['has_val_compression'] == True].copy()
    symbols = list(df_val['symbol'].unique())
    print(f"📥 Simulating Exit Engine for {len(df_val)} Trades across {len(symbols)} symbols...")
    
    print("📥 Loading PIT Exit Condition Fundamentals...")
    df_fund = fetch_pit_exits()
    
    print("📥 Loading Daily Prices & Running Execution Engine...")
    chunk_size = 30
    simulated_trades = []
    
    for i in range(0, len(symbols), chunk_size):
        chunk_syms = symbols[i:i+chunk_size]
        df_px = fetch_prices(chunk_syms)
        if df_px.empty: continue
            
        for idx, trade in df_val[df_val['symbol'].isin(chunk_syms)].iterrows():
            sym = trade['symbol']
            event_date = trade['event_date']
            
            sym_px = df_px[df_px['symbol'] == sym].sort_values('date').copy()
            sym_px['sma_200'] = sym_px['close'].rolling(200).mean()
            
            # Start execution looking for T+1 Open
            future_px = sym_px[sym_px['date'] > event_date].copy()
            if future_px.empty: continue
            
            entry_row = future_px.iloc[0]
            entry_date = entry_row['date']
            entry_price = entry_row['open']
            
            # Filter price action to holding period
            hold_px = future_px.copy()
            hold_px['below_sma200'] = hold_px['close'] < hold_px['sma_200']
            
            # 5 consecutive days below SMA200 persistence check
            hold_px['streak_below'] = hold_px['below_sma200'].groupby((~hold_px['below_sma200']).cumsum()).cumsum()
            tech_exit_candidates = hold_px[hold_px['streak_below'] >= 5]
            
            tech_exit_date = None
            if not tech_exit_candidates.empty:
                # The trigger happens on the 5th day, we execute on the Open of the 6th day
                trigger_idx = tech_exit_candidates.index[0]
                trigger_loc = hold_px.index.get_loc(trigger_idx)
                if trigger_loc + 1 < len(hold_px):
                    tech_exit_date = hold_px.iloc[trigger_loc + 1]['date']
            
            # Fundamental Exit Check (PIT logic)
            sym_fund = df_fund[df_fund['symbol'] == sym].copy()
            sym_fund = sym_fund[sym_fund['pub_date'] > entry_date]
            
            fund_exit_date = None
            if not sym_fund.empty:
                bad_funds = sym_fund[(sym_fund['roce'] < 10.0) | (sym_fund['net_profit'] <= 0)]
                if not bad_funds.empty:
                    first_bad_pub = bad_funds.iloc[0]['pub_date']
                    # Execute on next available trading day after publication
                    exec_days = hold_px[hold_px['date'] > first_bad_pub]
                    if not exec_days.empty:
                        fund_exit_date = exec_days.iloc[0]['date']
            
            # Determine First Exit Trigger
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
                exit_price = hold_px.iloc[-1]['close'] # Mark-to-market
                exit_date = hold_px.iloc[-1]['date']
                
            holding_period_days = (exit_date - entry_date).days
            ret_pct = (exit_price / entry_price - 1) * 100
            
            mfe_price = actual_hold['high'].max()
            mae_price = actual_hold['low'].min()
            
            mfe_pct = (mfe_price / entry_price - 1) * 100 if pd.notna(mfe_price) else 0
            mae_pct = (mae_price / entry_price - 1) * 100 if pd.notna(mae_price) else 0
            
            simulated_trades.append({
                'symbol': sym,
                'entry_date': entry_date,
                'entry_price': entry_price,
                'exit_date': exit_date,
                'exit_price': exit_price,
                'exit_reason': exit_reason,
                'holding_period_days': holding_period_days,
                'return_pct': ret_pct,
                'mfe_pct': mfe_pct,
                'mae_pct': mae_pct
            })
            
    df_sim = pd.DataFrame(simulated_trades)
    if df_sim.empty:
        print("❌ No trades simulated.")
        return
        
    print(f"\n✅ Simulated {len(df_sim)} Strategy Trades with Exits.")
    
    # Portfolio Analysis
    closed_trades = df_sim[df_sim['exit_reason'] != "ACTIVE_HOLD"]
    win_rate = (closed_trades['return_pct'] > 0).mean() * 100 if not closed_trades.empty else 0
    median_ret = closed_trades['return_pct'].median()
    mean_ret = closed_trades['return_pct'].mean()
    median_hold = closed_trades['holding_period_days'].median()
    
    reasons = df_sim['exit_reason'].value_counts(normalize=True) * 100
    
    # Winner Concentration (Top 10%)
    if not closed_trades.empty:
        sorted_rets = closed_trades['return_pct'].sort_values(ascending=False)
        top_10_pct = sorted_rets.head(int(len(sorted_rets) * 0.10)).sum()
        total_ret = sorted_rets.sum()
        winner_concentration = (top_10_pct / total_ret * 100) if total_ret > 0 else 0
    else:
        winner_concentration = 0
        
    report = [
        "# Model D Strategy Simulation & Exit Engine",
        f"**Run Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Execution Logic",
        "- **Entry:** T+1 Open execution on Quality + Valuation Compressed setup.",
        "- **Technical Exit:** Open of day following 5 consecutive closes below SMA200.",
        "- **Fundamental Exit:** T+1 Open following filing date containing ROCE < 10% or Net Profit <= 0.",
        "",
        "## Portfolio Metrics (Closed Trades)",
        f"- Total Executed Trades: {len(df_sim)}",
        f"- Closed Trades: {len(closed_trades)}",
        f"- Active Mark-to-Market: {len(df_sim) - len(closed_trades)}",
        f"- Win Rate: {win_rate:.1f}%",
        f"- Median Trade Return: {median_ret:.1f}%",
        f"- Average Trade Return: {mean_ret:.1f}%",
        f"- Median Holding Period: {median_hold:.0f} days (~{median_hold/365:.1f} years)",
        f"- Median Max Adverse Excursion (MAE / DD): {closed_trades['mae_pct'].median():.1f}%",
        f"- Median Max Favorable Excursion (MFE): {closed_trades['mfe_pct'].median():.1f}%",
        f"- Winner Concentration (Top 10% of trades drive {winner_concentration:.1f}% of total gross returns)",
        "",
        "## Exit Forensics",
        f"- Technical Breakdown (SMA200): {reasons.get('TECHNICAL_BREAKDOWN', 0):.1f}%",
        f"- Fundamental Deterioration: {reasons.get('FUNDAMENTAL_DETERIORATION', 0):.1f}%",
        f"- Still Active (Never Exited): {reasons.get('ACTIVE_HOLD', 0):.1f}%"
    ]
    
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(report))
        
    print(f"📊 Exit Engine report generated at {REPORT_PATH}")

if __name__ == "__main__":
    run_simulation()
