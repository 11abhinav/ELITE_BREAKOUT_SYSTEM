#!/usr/bin/env python3
"""
scripts/run_quality_value_recovery_v1_phase4_model_D.py

Executes Model D of the QUALITY_VALUE_RECOVERY_WEALTH_V1 master research prompt:
- QUALITY + FUNDAMENTAL IMPROVEMENT + NO STRUCTURAL DETERIORATION
- 30% DRAWDOWN FROM PRIOR 2Y HIGH
- HISTORICAL VALUATION COMPRESSION (PE_T or EV/EBITDA_T <= 80% of 3Y median)
- MATCHER COHORT (A = Immediate, B = Stabilization, C = SMA50 reclaim)
- T+1 OPEN EXECUTION
- FUNDAMENTAL DETERIORATION TEST (4 quarters forward)
- DATA SPAN: 2010 -> 2026

Calculates strictly NaN-adjusted win rates and metrics.
"""

import sqlite3
import pandas as pd
import numpy as np
import os
from datetime import datetime

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(REPO_ROOT, "data")
PIT_DB_PATH = os.path.join(DATA_DIR, "pit_fundamentals_v1", "pit_fundamentals_v1.db")
REPORT_PATH = os.path.join(REPO_ROOT, "reports", "quality_value_recovery_model_D_report.md")
TRADES_PATH = os.path.join(REPO_ROOT, "reports", "quality_value_recovery_v1_trades_model_D.csv")

def get_db_connection(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def fetch_pit_fundamentals():
    conn = get_db_connection(PIT_DB_PATH)
    # Using ANNUAL statements for valuation trailing medians
    query = """
    SELECT symbol, period_end_date, conservative_availability_timestamp, 
           net_profit, roce, eps, shares_outstanding, total_debt, 
           cash_and_equivalents, operating_profit, depreciation_amortization
    FROM pit_fundamentals_v1
    WHERE statement_type = 'ANNUAL'
    ORDER BY symbol, period_end_date ASC
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    df['conservative_availability_date'] = pd.to_datetime(df['conservative_availability_timestamp']).dt.date
    df['period_end_date'] = pd.to_datetime(df['period_end_date'])
    
    # Calculate derived fundamental fields
    df['ebitda'] = df['operating_profit'].fillna(0) + df['depreciation_amortization'].fillna(0)
    
    # Calculate rolling medians per symbol (3Y)
    df.sort_values(by=['symbol', 'period_end_date'], inplace=True)
    df['net_profit_3y_med'] = df.groupby('symbol')['net_profit'].rolling(3, min_periods=1).median().reset_index(level=0, drop=True)
    df['roce_3y_med'] = df.groupby('symbol')['roce'].rolling(3, min_periods=1).median().reset_index(level=0, drop=True)
    
    # Quality & Improving flags
    df['is_quality'] = (df['roce'] >= 15.0) & (df['net_profit'] > 0)
    df['is_improving'] = (
        (df['net_profit'] > df['net_profit_3y_med']) &
        (df['roce'] >= df['roce_3y_med']) &
        df['is_quality']
    )
    
    # We return everything, not just improving, because we need to calculate rolling PE/EV medians 
    # and future structural deterioration.
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
                pdf = pdf[pdf['date'] >= '2008-01-01'] # Load from 2008 to get 2Y high for 2010 start
                df_list.append(pdf)
            except Exception:
                pass
    if df_list:
        return pd.concat(df_list, ignore_index=True)
    return pd.DataFrame()

def analyze_model_D():
    print("📥 Loading PIT Fundamentals (2010-2026)...")
    df_fund = fetch_pit_fundamentals()
    
    symbols = list(df_fund['symbol'].unique())
    print(f"✅ Found {len(symbols)} companies in Fundamental DB.")
    
    print("📥 Loading Daily Prices & Merging... (This will take time)")
    chunk_size = 30
    all_trades = []
    
    for i in range(0, len(symbols), chunk_size):
        chunk_syms = symbols[i:i+chunk_size]
        df_px = fetch_prices(chunk_syms)
        if df_px.empty:
            continue
            
        for sym in chunk_syms:
            sym_px = df_px[df_px['symbol'] == sym].copy()
            sym_fund = df_fund[df_fund['symbol'] == sym].copy()
            
            if sym_px.empty or sym_fund.empty:
                continue
                
            sym_px = sym_px.sort_values('date')
            sym_px['high_2y'] = sym_px['high'].rolling(window=504, min_periods=252).max()
            sym_px['drawdown'] = (sym_px['close'] - sym_px['high_2y']) / sym_px['high_2y']
            sym_px['sma_50'] = sym_px['close'].rolling(window=50).mean()
            
            # Merge PIT Fundamentals As-Of Date
            fund_to_merge = sym_fund.copy()
            fund_to_merge['fund_date'] = pd.to_datetime(fund_to_merge['conservative_availability_date'])
            fund_to_merge = fund_to_merge.sort_values('fund_date')
            
            # We want to know the "current" fundamental state, but also the historical 3Y median of valuation 
            sym_px = pd.merge_asof(
                sym_px, 
                fund_to_merge,
                left_on='date', 
                right_on='fund_date', 
                direction='backward'
            )
            
            # Calculate PIT Valuation
            # PE_T = Close_T / EPS_T
            sym_px['pe_t'] = np.where((sym_px['eps'] > 0), sym_px['close'] / sym_px['eps'], np.nan)
            
            # EV_T = (Close_T * Shares_T) + Debt_T - Cash_T
            sym_px['ev_t'] = (sym_px['close'] * sym_px['shares_outstanding']) + sym_px['total_debt'].fillna(0) - sym_px['cash_and_equivalents'].fillna(0)
            sym_px['ev_ebitda_t'] = np.where((sym_px['ebitda'] > 0), sym_px['ev_t'] / sym_px['ebitda'], np.nan)
            
            # Calculate 3Y rolling median of these exact daily valuations
            # Note: 3Y of daily data is roughly 756 trading days
            sym_px['pe_3y_med'] = sym_px['pe_t'].rolling(window=756, min_periods=252).median()
            sym_px['ev_ebitda_3y_med'] = sym_px['ev_ebitda_t'].rolling(window=756, min_periods=252).median()
            
            # Valuation Compression Condition (<= 80% of own historical 3Y median)
            sym_px['is_pe_compressed'] = sym_px['pe_t'] <= (0.80 * sym_px['pe_3y_med'])
            sym_px['is_ev_compressed'] = sym_px['ev_ebitda_t'] <= (0.80 * sym_px['ev_ebitda_3y_med'])
            sym_px['valuation_compressed'] = sym_px['is_pe_compressed'] | sym_px['is_ev_compressed']
            
            # Core Signal (Trigger on exactly the day it crosses -30%)
            sym_px['prev_drawdown'] = sym_px['drawdown'].shift(1)
            sym_px['hit_30_drop'] = (sym_px['drawdown'] <= -0.30) & (sym_px['prev_drawdown'] > -0.30)
            
            # Matched Cohort State Tracking
            # Once a 30% drop happens, the setup is active for 60 days
            sym_px['setup_active'] = sym_px['hit_30_drop'].rolling(window=60, min_periods=1).max() > 0
            
            # Model C: SMA Reclaim
            sym_px['prev_close'] = sym_px['close'].shift(1)
            sym_px['prev_sma_50'] = sym_px['sma_50'].shift(1)
            sym_px['sma_reclaim'] = (sym_px['close'] > sym_px['sma_50']) & (sym_px['prev_close'] <= sym_px['prev_sma_50'])
            
            # Filter history to 2010+
            sym_px = sym_px[sym_px['date'] >= '2010-01-01']
            
            # We iterate to find the 'hit_30_drop' events and then execute the matched cohorts
            cooldown = 0
            for idx, row in sym_px.iterrows():
                if cooldown > 0:
                    cooldown -= 1
                    continue
                    
                if row['hit_30_drop'] and row['is_improving']:
                    event_date = row['date']
                    
                    # Valuation Placebo Check
                    has_val_compression = bool(row['valuation_compressed'])
                    
                    # Execution A: Immediate T+1 Open
                    # Find T+1
                    future_px = sym_px[sym_px['date'] > event_date]
                    if future_px.empty: continue
                    
                    t_plus_1 = future_px.iloc[0]
                    entry_price_A = t_plus_1['open'] # T+1 OPEN EXECUTION
                    
                    # Look forward for returns
                    fwd_1y = sym_px[sym_px['date'] >= event_date + pd.Timedelta(days=365)]
                    fwd_3y = sym_px[sym_px['date'] >= event_date + pd.Timedelta(days=365*3)]
                    fwd_5y = sym_px[sym_px['date'] >= event_date + pd.Timedelta(days=365*5)]
                    
                    ret_1y_A = (fwd_1y.iloc[0]['close'] / entry_price_A - 1) if not fwd_1y.empty else np.nan
                    ret_3y_A = (fwd_3y.iloc[0]['close'] / entry_price_A - 1) if not fwd_3y.empty else np.nan
                    ret_5y_A = (fwd_5y.iloc[0]['close'] / entry_price_A - 1) if not fwd_5y.empty else np.nan
                    
                    # Execution C: Wait for SMA50 Reclaim within next 60 days
                    window_60 = future_px.head(60)
                    reclaims = window_60[window_60['sma_reclaim'] == True]
                    
                    ret_1y_C, ret_3y_C, ret_5y_C = np.nan, np.nan, np.nan
                    if not reclaims.empty:
                        reclaim_date = reclaims.iloc[0]['date']
                        future_px_C = sym_px[sym_px['date'] > reclaim_date]
                        if not future_px_C.empty:
                            entry_price_C = future_px_C.iloc[0]['open'] # T+1 Open of reclaim
                            
                            fwd_1y_C = sym_px[sym_px['date'] >= reclaim_date + pd.Timedelta(days=365)]
                            fwd_3y_C = sym_px[sym_px['date'] >= reclaim_date + pd.Timedelta(days=365*3)]
                            fwd_5y_C = sym_px[sym_px['date'] >= reclaim_date + pd.Timedelta(days=365*5)]
                            
                            ret_1y_C = (fwd_1y_C.iloc[0]['close'] / entry_price_C - 1) if not fwd_1y_C.empty else np.nan
                            ret_3y_C = (fwd_3y_C.iloc[0]['close'] / entry_price_C - 1) if not fwd_3y_C.empty else np.nan
                            ret_5y_C = (fwd_5y_C.iloc[0]['close'] / entry_price_C - 1) if not fwd_5y_C.empty else np.nan
                            
                    # Fundamental Deterioration Look-Forward (4 Quarters / ~1 year)
                    # We check if ROCE dropped below 10% or Net Profit went negative in the year following event_date
                    future_funds = sym_fund[(sym_fund['period_end_date'] > pd.to_datetime(event_date)) & 
                                            (sym_fund['period_end_date'] <= pd.to_datetime(event_date) + pd.Timedelta(days=365))]
                    
                    deterioration_class = "A. Fundamentals Remained Healthy"
                    if not future_funds.empty:
                        min_roce = future_funds['roce'].min()
                        min_np = future_funds['net_profit'].min()
                        if min_roce < 10.0 or min_np <= 0:
                            deterioration_class = "D. Fundamentals Structurally Deteriorated"
                        elif future_funds['roce'].mean() > row['roce']:
                            deterioration_class = "B. Fundamentals Improved"
                    
                    all_trades.append({
                        'symbol': sym,
                        'event_date': event_date,
                        'has_val_compression': has_val_compression,
                        'deterioration_class': deterioration_class,
                        'ret_1y_A': ret_1y_A,
                        'ret_3y_A': ret_3y_A,
                        'ret_5y_A': ret_5y_A,
                        'ret_1y_C': ret_1y_C,
                        'ret_3y_C': ret_3y_C,
                        'ret_5y_C': ret_5y_C
                    })
                    
                    cooldown = 252 # 1 year cooldown
                    
    df_trades = pd.DataFrame(all_trades)
    
    if df_trades.empty:
        print("❌ No valid setups found.")
        return
        
    df_trades.to_csv(TRADES_PATH, index=False)
    print(f"\n✅ Generated {len(df_trades)} Matched Cohort Trades.")
    
    # Calculate corrected NaN-safe statistics
    def calc_stats(series):
        valid = series.notna()
        n = valid.sum()
        if n == 0: return np.nan, np.nan, 0
        win_rate = (series[valid] > 0).sum() / n * 100
        median = series[valid].median() * 100
        return median, win_rate, n
        
    # Master Results
    subset_val_compressed = df_trades[df_trades['has_val_compression'] == True]
    subset_no_val_compression = df_trades[df_trades['has_val_compression'] == False]
    
    med_3y_A_val, win_3y_A_val, n_3y_val = calc_stats(subset_val_compressed['ret_3y_A'])
    med_3y_A_no_val, win_3y_A_no_val, n_3y_no_val = calc_stats(subset_no_val_compression['ret_3y_A'])
    
    report = [
        "# QUALITY_VALUE_RECOVERY_WEALTH_V1 - Model D Execution",
        f"**Run Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Core Parameters",
        "- **Data Span:** 2010 to 2026",
        "- **Quality Criteria:** ROCE >= 15%, Net Profit > 0",
        "- **Valuation Compression:** PE_T or EV_EBITDA_T <= 80% of trailing 3Y median",
        "- **Execution:** Strict T+1 Open pricing.",
        "- **Statistics:** Corrected strict NaN exclusion for win rates.",
        "",
        "## Backtest Results (Matched Cohorts)",
        f"- Total Trigger Events: {len(df_trades)}",
        f"- Events with Valuation Compression: {len(subset_val_compressed)}",
        f"- Events without Valuation Compression (Placebo): {len(subset_no_val_compression)}",
        "",
        "### 1. Valuation Placebo Test (3-Year Horizon Model A)",
        f"- **WITH Valuation Compression:** Median {med_3y_A_val:.1f}% | Win Rate {win_3y_A_val:.1f}% (N={n_3y_val})",
        f"- **WITHOUT Valuation Compression:** Median {med_3y_A_no_val:.1f}% | Win Rate {win_3y_A_no_val:.1f}% (N={n_3y_no_val})",
        ""
    ]
    
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(report))
        
    print(f"📊 Phase 4 report generated at {REPORT_PATH}")

if __name__ == "__main__":
    analyze_model_D()
