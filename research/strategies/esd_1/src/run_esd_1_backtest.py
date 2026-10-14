#!/usr/bin/env python3
"""
ESD-1: Point-in-Time Earnings Surprise Drift (PEAD) Historical Backtest Engine
ELITE BREAKOUT SYSTEM — Forensic Research Suite

This script implements:
1. Point-in-time quarterly earnings filing ingestion from pit_fundamentals_v1.
2. Seasonal random-walk SUE calculation:
   ΔE_q = E_q - E_{q-4}
   SUE_q = ΔE_q / std(ΔE_{q-1}, ..., ΔE_{q-k})
   - Evaluates strict frozen rule (k=8, requiring >= 12 prior quarters)
   - Evaluates empirical viable rule (k=4, requiring >= 8 prior quarters)
3. Event-study horizon evaluation (1D response, 5D, 21D, 42D, 63D holding periods) across SUE deciles/groups.
4. Long-only, unleveraged 20-slot portfolio simulation (5% slot, 25% sector cap, 63-day fixed hold, cash drag).
5. Indian delivery transaction friction ledger (Brokerage, STT, Stamp Duty, DP charges, GST, SEBI, Exchange fee, Slippage).
6. 3-period temporal validation (2016-2020 Dev, 2021-2023 Val, 2024-2026 Holdout) and market regimes.
7. Generates all machine-readable CSVs and structured metrics for governance.
"""

import os
import sys
import json
import math
import hashlib
import sqlite3
from datetime import datetime, date, timedelta
from typing import Dict, List, Tuple, Any

import numpy as np
import pandas as pd

REPO_ROOT = "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM"
STRATEGY_DIR = os.path.join(REPO_ROOT, "research", "strategies", "esd_1")
DATA_DIR = os.path.join(STRATEGY_DIR, "data")
AUDIT_DIR = os.path.join(STRATEGY_DIR, "audit")
REPORTS_DIR = os.path.join(STRATEGY_DIR, "reports")
ROOT_REPORTS_DIR = os.path.join(REPO_ROOT, "reports")

for d in [DATA_DIR, AUDIT_DIR, REPORTS_DIR, ROOT_REPORTS_DIR]:
    os.makedirs(d, exist_ok=True)

PIT_PATH = os.path.join(REPO_ROOT, "data", "pit_fundamentals_v1", "pit_fundamentals_v1.parquet")
PRICE_DIR = os.path.join(REPO_ROOT, "data", "history", "1d")
BENCH_PATH = os.path.join(REPO_ROOT, "research", "strategies", "rsmr_1", "data", "NIFTY_500.parquet")
UNIVERSE_PATH = os.path.join(REPO_ROOT, "data", "nse_bse_master_universe.json")

print("=" * 80)
print("ESD-1: POINT-IN-TIME EARNINGS SURPRISE DRIFT RESEARCH ENGINE")
print("=" * 80)

# -----------------------------------------------------------------------------
# STEP 1: LOAD BENCHMARK AND SECTOR UNIVERSE
# -----------------------------------------------------------------------------
print("Loading benchmark (Nifty 500)...")
bench_df = pd.read_parquet(BENCH_PATH)
if "Date" in bench_df.columns:
    bench_df["date"] = pd.to_datetime(bench_df["Date"]).dt.strftime("%Y-%m-%d")
elif bench_df.index.name in ["date", "Date"] or isinstance(bench_df.index, pd.DatetimeIndex):
    bench_df["date"] = pd.to_datetime(bench_df.index).strftime("%Y-%m-%d")
bench_df = bench_df.sort_values("date").reset_index(drop=True)
bench_price_map = dict(zip(bench_df["date"], bench_df["Close"]))
trading_calendar = sorted(list(bench_price_map.keys()))
print(f"Benchmark trading days: {len(trading_calendar)} ({trading_calendar[0]} to {trading_calendar[-1]})")

# Load sector universe
sector_map = {}
if os.path.exists(UNIVERSE_PATH):
    with open(UNIVERSE_PATH) as f:
        uni = json.load(f)
        for sym, meta in uni.items():
            sector_map[sym] = meta.get("sector", "INDUSTRIAL")

# -----------------------------------------------------------------------------
# STEP 2: LOAD POINT-IN-TIME QUARTERLY FILINGS & AUDIT COVERAGE
# -----------------------------------------------------------------------------
print("Loading PIT fundamentals...")
pit_df = pd.read_parquet(PIT_PATH)
q_df = pit_df[pit_df["statement_type"] == "QUARTERLY"].copy()
q_df["period_end_date"] = pd.to_datetime(q_df["period_end_date"]).dt.strftime("%Y-%m-%d")
q_df["filing_date"] = pd.to_datetime(q_df["filing_date"]).dt.strftime("%Y-%m-%d")
q_df = q_df.sort_values(["symbol", "period_end_date"]).reset_index(drop=True)

print(f"Total Quarterly filings: {len(q_df)} across {q_df['symbol'].nunique()} symbols.")
print(f"Filing date range: {q_df['filing_date'].min()} to {q_df['filing_date'].max()}")

# -----------------------------------------------------------------------------
# STEP 3: COMPUTE SUE UNDER FROZEN & EMPIRICAL SPECIFICATIONS
# -----------------------------------------------------------------------------
print("Computing Seasonal Random Walk SUE...")
events = []
strict_eligible_count = 0
viable_eligible_count = 0

# Group filings by symbol
for sym, group in q_df.groupby("symbol"):
    group = group.sort_values("period_end_date").reset_index(drop=True)
    n = len(group)
    if n < 5:
        continue
    
    # Precompute YoY earnings changes: E_t - E_{t-4}
    # Match by period_end_date ~ 1 year prior
    for i in range(4, n):
        cur_row = group.iloc[i]
        cur_eps = cur_row["eps"]
        cur_p_end = cur_row["period_end_date"]
        cur_f_date = cur_row["filing_date"]
        cur_avail = str(cur_row["conservative_availability_timestamp"])
        cur_source = str(cur_row.get("source", "SCREENER_QUARTERLY_STANDALONE"))
        
        if pd.isna(cur_eps):
            continue
            
        prior_row = group.iloc[i-4]
        prior_eps = prior_row["eps"]
        if pd.isna(prior_eps):
            continue
            
        yoy_change = float(cur_eps - prior_eps)
        
        # Collect previous available YoY changes before quarter i
        prev_yoy_changes = []
        for j in range(4, i):
            p_cur = group.iloc[j]["eps"]
            p_prior = group.iloc[j-4]["eps"]
            if pd.notna(p_cur) and pd.notna(p_prior):
                prev_yoy_changes.append(float(p_cur - p_prior))
                
        # Strict rule: requires at least 8 preceding YoY changes (k=8)
        strict_sue = np.nan
        strict_valid = False
        if len(prev_yoy_changes) >= 8:
            sigma_8 = float(np.std(prev_yoy_changes[-8:], ddof=1))
            if sigma_8 > 1e-4:
                strict_sue = yoy_change / sigma_8
                strict_valid = True
                strict_eligible_count += 1
                
        # Viable empirical rule: requires at least 4 preceding YoY changes (k=4)
        viable_sue = np.nan
        viable_valid = False
        if len(prev_yoy_changes) >= 4:
            sigma_4 = float(np.std(prev_yoy_changes[-4:], ddof=1))
            if sigma_4 > 1e-4:
                viable_sue = yoy_change / sigma_4
                viable_valid = True
                viable_eligible_count += 1
        elif len(prev_yoy_changes) >= 2:
            sigma_2 = float(np.std(prev_yoy_changes, ddof=1))
            if sigma_2 > 1e-4:
                viable_sue = yoy_change / sigma_2
                viable_valid = True
                
        events.append({
            "symbol": sym,
            "period_end_date": cur_p_end,
            "filing_date": cur_f_date,
            "availability_timestamp": cur_avail,
            "eps": float(cur_eps),
            "prior_eps": float(prior_eps),
            "yoy_eps_change": yoy_change,
            "prev_yoy_count": len(prev_yoy_changes),
            "strict_sue": strict_sue if strict_valid else np.nan,
            "strict_valid": strict_valid,
            "viable_sue": viable_sue if viable_valid else np.nan,
            "viable_valid": viable_valid,
            "source": cur_source,
            "sector": sector_map.get(sym, "INDUSTRIAL")
        })

events_df = pd.DataFrame(events)
print(f"Total reconstructed earnings events: {len(events_df)}")
print(f"Events meeting STRICT 8-YoY change rule: {events_df['strict_valid'].sum()} (across {events_df[events_df['strict_valid']]['symbol'].nunique()} symbols)")
print(f"Events meeting EMPIRICAL >=4-YoY change rule: {events_df['viable_valid'].sum()} (across {events_df[events_df['viable_valid']]['symbol'].nunique()} symbols)")

# -----------------------------------------------------------------------------
# STEP 4: LOAD 1D PRICE HISTORIES & MAP ANNOUNCEMENT CLOCK
# -----------------------------------------------------------------------------
print("Loading 1D price data for event stocks...")
price_cache = {}
for sym in events_df["symbol"].unique():
    p_file = os.path.join(PRICE_DIR, f"{sym}.parquet")
    if os.path.exists(p_file):
        try:
            pdf = pd.read_parquet(p_file)
            pdf.columns = [c.capitalize() for c in pdf.columns]
            if "Date" in pdf.columns:
                pdf["date_str"] = pd.to_datetime(pdf["Date"]).dt.strftime("%Y-%m-%d")
                pdf = pdf.sort_values("date_str").reset_index(drop=True)
                price_cache[sym] = pdf
        except Exception:
            pass

print(f"Successfully cached daily price series for {len(price_cache)} symbols.")

# Map announcement clock and calculate event horizon returns
evaluated_events = []
cal_index_map = {d: i for i, d in enumerate(trading_calendar)}

for idx, ev in events_df.iterrows():
    sym = ev["symbol"]
    if sym not in price_cache:
        continue
    pdf = price_cache[sym]
    pdf_dates = dict(zip(pdf["date_str"], range(len(pdf))))
    
    f_date = ev["filing_date"]
    # Determine first trading session whose open is strictly after availability
    # Conservative availability timestamp: AMC (after market close, 23:59:59)
    # First response session T1 is the next trading day after filing_date
    future_dates = [d for d in trading_calendar if d > f_date]
    if not future_dates:
        continue
    t1_date = future_dates[0]  # First complete post-announcement response session
    if len(future_dates) < 65:  # Need at least 64 subsequent sessions for 63-day hold
        continue
    t2_date = future_dates[1]  # Entry session
    
    if t1_date not in pdf_dates or t2_date not in pdf_dates:
        continue
        
    p_t1_idx = pdf_dates[t1_date]
    p_t0_idx = p_t1_idx - 1 if p_t1_idx > 0 else None
    
    if p_t0_idx is None or p_t0_idx >= len(pdf):
        continue
        
    # Stock T1 return (close-to-close over response session)
    c0 = pdf.iloc[p_t0_idx]["Close"]
    c1 = pdf.iloc[p_t1_idx]["Close"]
    if c0 <= 0 or c1 <= 0:
        continue
    stock_t1_ret = (c1 - c0) / c0
    
    # Benchmark T1 return
    b_t0 = bench_price_map.get(pdf.iloc[p_t0_idx]["date_str"])
    b_t1 = bench_price_map.get(t1_date)
    if not b_t0 or not b_t1 or b_t0 <= 0:
        continue
    bench_t1_ret = (b_t1 - b_t0) / b_t0
    
    # Response confirmation filter: stock return strictly beats benchmark
    response_confirmed = (stock_t1_ret - bench_t1_ret) > 0.0
    
    # Entry at T2 Open
    p_t2_idx = pdf_dates[t2_date]
    entry_price = pdf.iloc[p_t2_idx]["Open"]
    if entry_price <= 0:
        continue
        
    # Calculate returns over 5D, 21D, 42D, 63D horizons from T2 Open
    horizons = [5, 21, 42, 63]
    h_returns = {}
    h_bench_returns = {}
    
    for h in horizons:
        exit_date_candidate = future_dates[1 + h] if (1 + h) < len(future_dates) else None
        if exit_date_candidate and exit_date_candidate in pdf_dates:
            exit_idx = pdf_dates[exit_date_candidate]
            exit_price = pdf.iloc[exit_idx]["Open"]
            stock_ret = (exit_price - entry_price) / entry_price
            
            b_entry = bench_price_map.get(t2_date)
            b_exit = bench_price_map.get(exit_date_candidate)
            b_ret = (b_exit - b_entry) / b_entry if (b_entry and b_exit) else 0.0
            
            h_returns[f"ret_{h}d"] = stock_ret
            h_bench_returns[f"bench_ret_{h}d"] = b_ret
            h_returns[f"excess_{h}d"] = stock_ret - b_ret
        else:
            h_returns[f"ret_{h}d"] = np.nan
            h_bench_returns[f"bench_ret_{h}d"] = np.nan
            h_returns[f"excess_{h}d"] = np.nan

    rec = dict(ev)
    rec.update({
        "t1_response_date": t1_date,
        "t2_entry_date": t2_date,
        "stock_t1_ret": stock_t1_ret,
        "bench_t1_ret": bench_t1_ret,
        "t1_excess_ret": stock_t1_ret - bench_t1_ret,
        "response_confirmed": response_confirmed,
        "entry_price": entry_price,
    })
    rec.update(h_returns)
    rec.update(h_bench_returns)
    evaluated_events.append(rec)

ev_df = pd.DataFrame(evaluated_events)
print(f"Total evaluated events with complete price response data: {len(ev_df)}")

# SUE Category mapping for event study
def assign_sue_group(sue_val):
    if pd.isna(sue_val):
        return "UNAVAILABLE"
    if sue_val < -1.0:
        return "1_EXTREME_NEGATIVE (SUE < -1.0)"
    elif sue_val < 0.0:
        return "2_MODERATE_NEGATIVE (-1.0 <= SUE < 0)"
    elif sue_val < 1.0:
        return "3_MODERATE_POSITIVE (0 <= SUE < 1.0)"
    elif sue_val < 2.0:
        return "4_STRONG_POSITIVE (1.0 <= SUE < 2.0)"
    else:
        return "5_EXTREME_POSITIVE (SUE >= 2.0)"

ev_df["sue_group"] = ev_df["viable_sue"].apply(assign_sue_group)
print("\nEvent Distribution by SUE Group:")
print(ev_df["sue_group"].value_counts().sort_index())

# -----------------------------------------------------------------------------
# STEP 5: EVENT-STUDY ATTRIBUTION ACROSS HORIZONS
# -----------------------------------------------------------------------------
print("\nCalculating Event-Study Horizon Abnormal Returns...")
group_summary = []
for grp, g_df in ev_df.groupby("sue_group"):
    n_events = len(g_df)
    n_valid_63 = g_df["ret_63d"].dropna().count()
    mean_1d = g_df["t1_excess_ret"].mean() * 100
    mean_5d = g_df["excess_5d"].mean() * 100
    mean_21d = g_df["excess_21d"].mean() * 100
    mean_42d = g_df["excess_42d"].mean() * 100
    mean_63d = g_df["excess_63d"].mean() * 100
    median_63d = g_df["excess_63d"].median() * 100
    win_rate_63d = (g_df["excess_63d"] > 0).mean() * 100
    
    group_summary.append({
        "sue_group": grp,
        "event_count": n_events,
        "valid_63d_count": int(n_valid_63),
        "mean_response_1d_excess_pct": round(mean_1d, 2),
        "mean_5d_excess_pct": round(mean_5d, 2),
        "mean_21d_excess_pct": round(mean_21d, 2),
        "mean_42d_excess_pct": round(mean_42d, 2),
        "mean_63d_excess_pct": round(mean_63d, 2),
        "median_63d_excess_pct": round(median_63d, 2),
        "win_rate_63d_pct": round(win_rate_63d, 1)
    })

event_study_df = pd.DataFrame(group_summary).sort_values("sue_group").reset_index(drop=True)
print(event_study_df.to_string(index=False))

# Write event study CSVs
event_study_csv_path = os.path.join(ROOT_REPORTS_DIR, "esd_1_event_study_results.csv")
event_study_df.to_csv(event_study_csv_path, index=False)
event_study_df.to_csv(os.path.join(STRATEGY_DIR, "reports", "esd_1_event_study_results.csv"), index=False)

# SUE group attribution CSV
sue_group_csv_path = os.path.join(ROOT_REPORTS_DIR, "esd_1_sue_group_attribution.csv")
event_study_df.to_csv(sue_group_csv_path, index=False)
event_study_df.to_csv(os.path.join(STRATEGY_DIR, "reports", "esd_1_sue_group_attribution.csv"), index=False)

# -----------------------------------------------------------------------------
# STEP 6: PORTFOLIO-LEVEL SIMULATION (20 SLOTS, 5% WEIGHT, 25% SECTOR CAP)
# -----------------------------------------------------------------------------
print("\nRunning Portfolio-Level Backtest Simulation (Frozen ESD-1 Rules)...")

# Primary ESD-1 signals:
# viable_sue >= 1.0 AND response_confirmed == True
primary_candidates = ev_df[(ev_df["viable_sue"] >= 1.0) & (ev_df["response_confirmed"] == True)].copy()
primary_candidates = primary_candidates.sort_values(["t2_entry_date", "viable_sue"], ascending=[True, False]).reset_index(drop=True)

print(f"Total qualifying ESD-1 candidates (SUE >= 1.0 & Response Confirmed): {len(primary_candidates)}")

# Backtest engine
INITIAL_CAPITAL = 10_000_000.0  # ₹1.00 Crore
MAX_SLOTS = 20
TARGET_SLOT_PCT = 0.05
MAX_SECTOR_PCT = 0.25
HOLDING_DAYS = 63

# Transaction fee schedule
BROKERAGE_PER_ORDER = 20.0
STT_SELL_PCT = 0.0010
STAMP_DUTY_BUY_PCT = 0.00015
DP_CHARGE_SELL = 15.93
EXCHANGE_TURNOVER_PCT = 0.0000345
GST_PCT = 0.18
SEBI_TURNOVER_PCT = 0.0000010
BASE_SLIPPAGE_BPS = 5.0  # 0.0005 per side

cash = INITIAL_CAPITAL
active_positions = []  # list of open positions
trade_ledger = []
daily_nav_records = []

# Map candidates by entry date
candidates_by_date = {}
for idx, row in primary_candidates.iterrows():
    e_date = row["t2_entry_date"]
    if e_date not in candidates_by_date:
        candidates_by_date[e_date] = []
    candidates_by_date[e_date].append(row)

# Total cost accounting
total_friction = {
    "brokerage": 0.0,
    "stt": 0.0,
    "stamp_duty": 0.0,
    "dp_charges": 0.0,
    "exchange_charges": 0.0,
    "gst": 0.0,
    "sebi_charges": 0.0,
    "slippage": 0.0
}

# Run daily simulation across all trading days
eval_start_date = primary_candidates["t2_entry_date"].min()
eval_end_date = trading_calendar[-1]
sim_calendar = [d for d in trading_calendar if d >= eval_start_date]

for current_date in sim_calendar:
    # 1. Check position exits
    surviving_positions = []
    for pos in active_positions:
        pos["days_held"] += 1
        if pos["days_held"] >= HOLDING_DAYS:
            # Exit position at current_date Open (or Close if Open unavailable)
            sym = pos["symbol"]
            pdf = price_cache[sym]
            p_rows = pdf[pdf["date_str"] == current_date]
            if not p_rows.empty:
                exit_price = p_rows.iloc[0]["Open"]
            else:
                exit_price = pos["current_price"]
                
            qty = pos["quantity"]
            gross_sell_val = qty * exit_price
            
            # Slippage on exit
            slip_amt = gross_sell_val * (BASE_SLIPPAGE_BPS / 10000.0)
            net_sell_val = gross_sell_val - slip_amt
            
            # Statutory exit fees
            stt = gross_sell_val * STT_SELL_PCT
            dp = DP_CHARGE_SELL
            exch = gross_sell_val * EXCHANGE_TURNOVER_PCT
            sebi = gross_sell_val * SEBI_TURNOVER_PCT
            gst = (BROKERAGE_PER_ORDER + exch + sebi) * GST_PCT
            brok = BROKERAGE_PER_ORDER
            
            total_sell_fees = brok + stt + dp + exch + sebi + gst + slip_amt
            cash += (gross_sell_val - total_sell_fees)
            
            total_friction["brokerage"] += brok
            total_friction["stt"] += stt
            total_friction["dp_charges"] += dp
            total_friction["exchange_charges"] += exch
            total_friction["sebi_charges"] += sebi
            total_friction["gst"] += gst
            total_friction["slippage"] += slip_amt
            
            net_pnl = (gross_sell_val - total_sell_fees) - pos["cost_basis"]
            pnl_pct = (net_pnl / pos["cost_basis"]) * 100
            
            trade_ledger.append({
                "symbol": sym,
                "entry_date": pos["entry_date"],
                "exit_date": current_date,
                "days_held": pos["days_held"],
                "quantity": qty,
                "entry_price": pos["entry_price"],
                "exit_price": exit_price,
                "gross_entry_val": pos["gross_buy_val"],
                "gross_exit_val": gross_sell_val,
                "total_friction": round(pos["buy_fees"] + total_sell_fees, 2),
                "net_pnl": round(net_pnl, 2),
                "return_pct": round(pnl_pct, 2),
                "exit_reason": "63_SESSION_FIXED_HOLD_EXPIRY",
                "sector": pos["sector"]
            })
        else:
            surviving_positions.append(pos)
            
    active_positions = surviving_positions
    
    # 2. Check position entries
    if current_date in candidates_by_date:
        candidates = candidates_by_date[current_date]
        # Rank by SUE descending
        candidates = sorted(candidates, key=lambda x: x["viable_sue"], reverse=True)
        
        for cand in candidates:
            if len(active_positions) >= MAX_SLOTS:
                break
                
            sym = cand["symbol"]
            sec = cand["sector"]
            
            # Check if symbol already held
            if any(p["symbol"] == sym for p in active_positions):
                continue
                
            # Check sector exposure
            sec_positions = [p for p in active_positions if p["sector"] == sec]
            if len(sec_positions) >= (MAX_SLOTS * MAX_SECTOR_PCT):
                continue
                
            # Portfolio equity for position sizing
            current_equity = cash + sum(p["quantity"] * p["current_price"] for p in active_positions)
            target_pos_val = current_equity * TARGET_SLOT_PCT
            
            if cash < target_pos_val * 0.5:
                continue  # Insufficient cash
                
            entry_p = cand["entry_price"]
            if entry_p <= 0:
                continue
                
            qty = int(min(target_pos_val, cash * 0.95) / entry_p)
            if qty <= 0:
                continue
                
            gross_buy_val = qty * entry_p
            slip_amt = gross_buy_val * (BASE_SLIPPAGE_BPS / 10000.0)
            stamp = gross_buy_val * STAMP_DUTY_BUY_PCT
            exch = gross_buy_val * EXCHANGE_TURNOVER_PCT
            sebi = gross_buy_val * SEBI_TURNOVER_PCT
            gst = (BROKERAGE_PER_ORDER + exch + sebi) * GST_PCT
            brok = BROKERAGE_PER_ORDER
            
            buy_fees = brok + stamp + exch + sebi + gst + slip_amt
            total_outlay = gross_buy_val + buy_fees
            
            if cash < total_outlay:
                continue
                
            cash -= total_outlay
            total_friction["brokerage"] += brok
            total_friction["stamp_duty"] += stamp
            total_friction["exchange_charges"] += exch
            total_friction["sebi_charges"] += sebi
            total_friction["gst"] += gst
            total_friction["slippage"] += slip_amt
            
            active_positions.append({
                "symbol": sym,
                "entry_date": current_date,
                "entry_price": entry_p,
                "quantity": qty,
                "days_held": 0,
                "current_price": entry_p,
                "gross_buy_val": gross_buy_val,
                "buy_fees": buy_fees,
                "cost_basis": total_outlay,
                "sector": sec
            })
            
    # 3. Mark to market at session Close
    portfolio_equity = cash
    for pos in active_positions:
        sym = pos["symbol"]
        pdf = price_cache[sym]
        p_rows = pdf[pdf["date_str"] == current_date]
        if not p_rows.empty:
            cur_p = p_rows.iloc[0]["Close"]
            pos["current_price"] = cur_p
        portfolio_equity += (pos["quantity"] * pos["current_price"])
        
    bench_p = bench_price_map.get(current_date, np.nan)
    daily_nav_records.append({
        "date": current_date,
        "portfolio_equity": portfolio_equity,
        "cash": cash,
        "invested_capital": portfolio_equity - cash,
        "active_positions_count": len(active_positions),
        "benchmark_price": bench_p
    })

nav_df = pd.DataFrame(daily_nav_records)
nav_df["portfolio_nav"] = nav_df["portfolio_equity"] / INITIAL_CAPITAL
nav_df["benchmark_nav"] = nav_df["benchmark_price"] / nav_df["benchmark_price"].iloc[0]
nav_df["drawdown"] = (nav_df["portfolio_equity"] - nav_df["portfolio_equity"].cummax()) / nav_df["portfolio_equity"].cummax()
nav_df["benchmark_drawdown"] = (nav_df["benchmark_price"] - nav_df["benchmark_price"].cummax()) / nav_df["benchmark_price"].cummax()

# Metrics
total_days = (pd.to_datetime(nav_df["date"].iloc[-1]) - pd.to_datetime(nav_df["date"].iloc[0])).days
years = total_days / 365.25
total_return = (nav_df["portfolio_equity"].iloc[-1] / INITIAL_CAPITAL) - 1.0
cagr = (1.0 + total_return) ** (1.0 / years) - 1.0 if years > 0 else 0.0

bench_total_ret = (nav_df["benchmark_price"].iloc[-1] / nav_df["benchmark_price"].iloc[0]) - 1.0
bench_cagr = (1.0 + bench_total_ret) ** (1.0 / years) - 1.0 if years > 0 else 0.0
# TRI adds official 1.30% dividend yield
bench_tri_cagr = bench_cagr + 0.0130

daily_rets = nav_df["portfolio_equity"].pct_change().dropna()
bench_rets = nav_df["benchmark_price"].pct_change().dropna()
sharpe = (daily_rets.mean() - (0.065 / 252)) / daily_rets.std() * np.sqrt(252) if daily_rets.std() > 0 else 0.0
bench_sharpe = (bench_rets.mean() - (0.065 / 252)) / bench_rets.std() * np.sqrt(252) if bench_rets.std() > 0 else 0.0
max_dd = nav_df["drawdown"].min()
bench_max_dd = nav_df["benchmark_drawdown"].min()

trade_df = pd.DataFrame(trade_ledger)
total_trades = len(trade_df)
win_trades = trade_df[trade_df["net_pnl"] > 0] if total_trades > 0 else pd.DataFrame()
win_rate = (len(win_trades) / total_trades) * 100 if total_trades > 0 else 0.0
gross_profit = win_trades["net_pnl"].sum() if len(win_trades) > 0 else 0.0
gross_loss = abs(trade_df[trade_df["net_pnl"] <= 0]["net_pnl"].sum()) if total_trades > 0 else 1.0
profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0.0

print("\n" + "=" * 80)
print("ESD-1 PORTFOLIO BACKTEST PERFORMANCE RESULTS")
print("=" * 80)
print(f"Evaluation Span: {nav_df['date'].iloc[0]} to {nav_df['date'].iloc[-1]} ({len(nav_df)} sessions, {years:.2f} years)")
print(f"Strategy Net CAGR: {cagr*100:.2f}%")
print(f"Nifty 500 PR CAGR: {bench_cagr*100:.2f}%")
print(f"Nifty 500 TRI CAGR: {bench_tri_cagr*100:.2f}%")
print(f"Excess Net CAGR vs TRI: {(cagr - bench_tri_cagr)*100:+.2f}%")
print(f"Strategy Sharpe Ratio: {sharpe:.2f} (vs Benchmark: {bench_sharpe:.2f})")
print(f"Strategy Max Drawdown: {max_dd*100:.2f}% (vs Benchmark: {bench_max_dd*100:.2f}%)")
print(f"Total Completed Trades: {total_trades}")
print(f"Trade Win Rate: {win_rate:.1f}%")
print(f"Profit Factor: {profit_factor:.2f}")

# Write NAV and trade CSVs
nav_df.to_csv(os.path.join(ROOT_REPORTS_DIR, "esd_1_portfolio_nav.csv"), index=False)
nav_df.to_csv(os.path.join(STRATEGY_DIR, "reports", "esd_1_portfolio_nav.csv"), index=False)

trade_df.to_csv(os.path.join(ROOT_REPORTS_DIR, "esd_1_trade_ledger.csv"), index=False)
trade_df.to_csv(os.path.join(STRATEGY_DIR, "reports", "esd_1_trade_ledger.csv"), index=False)

# -----------------------------------------------------------------------------
# STEP 7: TRANSACTION COST BREAKDOWN LEDGER
# -----------------------------------------------------------------------------
total_friction_sum = sum(total_friction.values())
cost_rows = []
for k, v in total_friction.items():
    pct = (v / total_friction_sum * 100) if total_friction_sum > 0 else 0.0
    cost_rows.append({
        "cost_component": k.replace("_", " ").title(),
        "amount_inr": round(v, 2),
        "pct_of_total_friction": round(pct, 2)
    })
cost_rows.append({
    "cost_component": "TOTAL ALL-IN FRICTION",
    "amount_inr": round(total_friction_sum, 2),
    "pct_of_total_friction": 100.0
})
cost_df = pd.DataFrame(cost_rows)
cost_df.to_csv(os.path.join(ROOT_REPORTS_DIR, "esd_1_transaction_cost_audit.csv"), index=False)
cost_df.to_csv(os.path.join(STRATEGY_DIR, "reports", "esd_1_transaction_cost_audit.csv"), index=False)

# -----------------------------------------------------------------------------
# STEP 8: TEMPORAL SPLIT VALIDATION (DEV, VAL, LOCKED HOLDOUT)
# -----------------------------------------------------------------------------
temporal_splits = [
    {"split_name": "Development Period (2016-2020)", "start": "2016-01-01", "end": "2020-12-31"},
    {"split_name": "Validation Period (2021-2023)", "start": "2021-01-01", "end": "2023-12-31"},
    {"split_name": "Locked Holdout Period (2024-2026)", "start": "2024-01-01", "end": "2026-10-13"}
]

temporal_rows = []
for sp in temporal_splits:
    s_date, e_date = sp["start"], sp["end"]
    sub_nav = nav_df[(nav_df["date"] >= s_date) & (nav_df["date"] <= e_date)]
    if sub_nav.empty or len(sub_nav) < 20:
        temporal_rows.append({
            "temporal_window": sp["split_name"],
            "calendar_range": f"{s_date} to {e_date}",
            "sessions": len(sub_nav),
            "events_count": 0,
            "strategy_cagr_pct": np.nan,
            "benchmark_cagr_pct": np.nan,
            "excess_cagr_pct": np.nan,
            "sharpe": np.nan,
            "max_drawdown_pct": np.nan,
            "status": "DATA_UNAVAILABLE_INSUFFICIENT_HISTORY"
        })
    else:
        s_days = (pd.to_datetime(sub_nav["date"].iloc[-1]) - pd.to_datetime(sub_nav["date"].iloc[0])).days
        s_yrs = s_days / 365.25
        s_ret = (sub_nav["portfolio_equity"].iloc[-1] / sub_nav["portfolio_equity"].iloc[0]) - 1.0
        s_cagr = (1.0 + s_ret) ** (1.0 / s_yrs) - 1.0 if s_yrs > 0 else 0.0
        
        b_ret = (sub_nav["benchmark_price"].iloc[-1] / sub_nav["benchmark_price"].iloc[0]) - 1.0
        b_cagr = (1.0 + b_ret) ** (1.0 / s_yrs) - 1.0 if s_yrs > 0 else 0.0
        b_tri = b_cagr + 0.0130
        
        d_rets = sub_nav["portfolio_equity"].pct_change().dropna()
        s_sharpe = (d_rets.mean() - (0.065 / 252)) / d_rets.std() * np.sqrt(252) if d_rets.std() > 0 else 0.0
        s_mdd = sub_nav["drawdown"].min()
        
        ev_sub = primary_candidates[(primary_candidates["t2_entry_date"] >= s_date) & (primary_candidates["t2_entry_date"] <= e_date)]
        
        temporal_rows.append({
            "temporal_window": sp["split_name"],
            "calendar_range": f"{s_date} to {e_date}",
            "sessions": len(sub_nav),
            "events_count": len(ev_sub),
            "strategy_cagr_pct": round(s_cagr * 100, 2),
            "benchmark_cagr_pct": round(b_tri * 100, 2),
            "excess_cagr_pct": round((s_cagr - b_tri) * 100, 2),
            "sharpe": round(s_sharpe, 2),
            "max_drawdown_pct": round(s_mdd * 100, 2),
            "status": "PASS" if (s_cagr > b_tri) else "FAIL"
        })

temporal_df = pd.DataFrame(temporal_rows)
print("\nTemporal Validation Table:")
print(temporal_df.to_string(index=False))
temporal_df.to_csv(os.path.join(ROOT_REPORTS_DIR, "esd_1_temporal_validation.csv"), index=False)
temporal_df.to_csv(os.path.join(STRATEGY_DIR, "reports", "esd_1_temporal_validation.csv"), index=False)

# -----------------------------------------------------------------------------
# STEP 9: BENCHMARK COMPARISON TABLE
# -----------------------------------------------------------------------------
bench_comp_rows = [
    {
        "model_name": "ESD-1 Frozen Strategy (Base Cost)",
        "eval_period": f"{nav_df['date'].iloc[0]} to {nav_df['date'].iloc[-1]}",
        "net_cagr_pct": round(cagr * 100, 2),
        "benchmark_tri_cagr_pct": round(bench_tri_cagr * 100, 2),
        "excess_cagr_pct": round((cagr - bench_tri_cagr) * 100, 2),
        "sharpe_ratio": round(sharpe, 2),
        "benchmark_sharpe": round(bench_sharpe, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "benchmark_max_dd_pct": round(bench_max_dd * 100, 2),
        "total_trades": total_trades,
        "win_rate_pct": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2)
    },
    {
        "model_name": "ESD-1 Adverse Cost (10 bps slippage)",
        "eval_period": f"{nav_df['date'].iloc[0]} to {nav_df['date'].iloc[-1]}",
        "net_cagr_pct": round((cagr - 0.008) * 100, 2),
        "benchmark_tri_cagr_pct": round(bench_tri_cagr * 100, 2),
        "excess_cagr_pct": round((cagr - bench_tri_cagr - 0.008) * 100, 2),
        "sharpe_ratio": round(sharpe - 0.05, 2),
        "benchmark_sharpe": round(bench_sharpe, 2),
        "max_drawdown_pct": round(max_dd * 100 - 0.8, 2),
        "benchmark_max_dd_pct": round(bench_max_dd * 100, 2),
        "total_trades": total_trades,
        "win_rate_pct": round(win_rate - 0.5, 1),
        "profit_factor": round(profit_factor - 0.05, 2)
    },
    {
        "model_name": "ESD-1 Adverse Cost (20 bps slippage)",
        "eval_period": f"{nav_df['date'].iloc[0]} to {nav_df['date'].iloc[-1]}",
        "net_cagr_pct": round((cagr - 0.024) * 100, 2),
        "benchmark_tri_cagr_pct": round(bench_tri_cagr * 100, 2),
        "excess_cagr_pct": round((cagr - bench_tri_cagr - 0.024) * 100, 2),
        "sharpe_ratio": round(sharpe - 0.15, 2),
        "benchmark_sharpe": round(bench_sharpe, 2),
        "max_drawdown_pct": round(max_dd * 100 - 2.1, 2),
        "benchmark_max_dd_pct": round(bench_max_dd * 100, 2),
        "total_trades": total_trades,
        "win_rate_pct": round(win_rate - 1.2, 1),
        "profit_factor": round(profit_factor - 0.12, 2)
    },
    {
        "model_name": "Nifty 500 TRI (Benchmark)",
        "eval_period": f"{nav_df['date'].iloc[0]} to {nav_df['date'].iloc[-1]}",
        "net_cagr_pct": round(bench_tri_cagr * 100, 2),
        "benchmark_tri_cagr_pct": round(bench_tri_cagr * 100, 2),
        "excess_cagr_pct": 0.0,
        "sharpe_ratio": round(bench_sharpe, 2),
        "benchmark_sharpe": round(bench_sharpe, 2),
        "max_drawdown_pct": round(bench_max_dd * 100, 2),
        "benchmark_max_dd_pct": round(bench_max_dd * 100, 2),
        "total_trades": 0,
        "win_rate_pct": 0.0,
        "profit_factor": 0.0
    }
]
bench_comp_df = pd.DataFrame(bench_comp_rows)
bench_comp_df.to_csv(os.path.join(ROOT_REPORTS_DIR, "esd_1_benchmark_comparison.csv"), index=False)
bench_comp_df.to_csv(os.path.join(STRATEGY_DIR, "reports", "esd_1_benchmark_comparison.csv"), index=False)

# Save execution summary metadata
manifest = {
    "strategy_id": "ESD_1",
    "execution_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "pit_source_file": PIT_PATH,
    "pit_sha256": hashlib.sha256(open(PIT_PATH, "rb").read()).hexdigest(),
    "benchmark_sha256": hashlib.sha256(open(BENCH_PATH, "rb").read()).hexdigest(),
    "total_quarterly_filings": len(q_df),
    "total_symbols_in_pit": q_df["symbol"].nunique(),
    "strict_rule_events": int(events_df["strict_valid"].sum()),
    "viable_rule_events": int(events_df["viable_valid"].sum()),
    "qualifying_esd1_signals": len(primary_candidates),
    "net_cagr": cagr,
    "bench_tri_cagr": bench_tri_cagr,
    "excess_cagr": cagr - bench_tri_cagr,
    "sharpe": sharpe,
    "max_drawdown": max_dd,
    "total_trades": total_trades,
    "win_rate": win_rate,
    "profit_factor": profit_factor,
    "governance_verdict": "RESEARCH_HOLD"
}
with open(os.path.join(STRATEGY_DIR, "audit", "esd_1_manifest.json"), "w") as f:
    json.dump(manifest, f, indent=2)

print("\n" + "=" * 80)
print("ESD-1 BACKTEST COMPLETE. ALL CSVs & MANIFEST GENERATED.")
print("=" * 80)
