#!/usr/bin/env python3
"""
SFP-1 Simulation & Historical Backtest Engine
Strategy: Size Premium with Financial Survival Filters
Universe: Point-in-Time Smallcap 250 (Rank 251-500 by Market Cap)
"""

import os
import sys
import json
import glob
import math
import hashlib
import logging
from datetime import datetime
import pandas as pd
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BASE_DIR = "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM"
STRAT_DIR = os.path.join(BASE_DIR, "research/strategies/sfp_1")
CONFIG_PATH = os.path.join(STRAT_DIR, "config/sfp_1_frozen_config.json")
DATA_DIR = os.path.join(BASE_DIR, "data")
PRICE_DIR = os.path.join(DATA_DIR, "history/1d")
PIT_PATH = os.path.join(DATA_DIR, "pit_fundamentals_v1/pit_fundamentals_v1.parquet")
BFSI_PATH = os.path.join(DATA_DIR, "canonical_pit_rebuilt.parquet")
SECTOR_PATH = os.path.join(DATA_DIR, "nse_bse_master_universe.json")
BENCH_PATH = os.path.join(BASE_DIR, "research/strategies/rsmr_1/data/NIFTY_500.parquet")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
STRAT_REPORTS_DIR = os.path.join(STRAT_DIR, "reports")
AUDIT_DIR = os.path.join(STRAT_DIR, "audit")

os.makedirs(REPORTS_DIR, exist_ok=True)
os.makedirs(STRAT_REPORTS_DIR, exist_ok=True)
os.makedirs(AUDIT_DIR, exist_ok=True)

with open(CONFIG_PATH, "r") as f:
    CONFIG = json.load(f)

SURVIVORSHIP_HAIRCUT_CAGR = CONFIG["benchmarks"]["survivorship_haircut_cagr_pct"]

def load_environment():
    logging.info("Loading fundamentals, BFSI mappings, and sector master...")
    pit_df = pd.read_parquet(PIT_PATH)
    pit_df["period_end_date"] = pd.to_datetime(pit_df["period_end_date"])
    pit_df["conservative_availability_timestamp"] = pd.to_datetime(pit_df["conservative_availability_timestamp"])
    
    bfsi_symbols = set()
    if os.path.exists(BFSI_PATH):
        try:
            b_df = pd.read_parquet(BFSI_PATH)
            if "is_bfsi" in b_df.columns:
                bfsi_symbols = set(b_df[b_df["is_bfsi"] == True]["symbol"].unique())
        except Exception as e:
            logging.warning(f"Failed to read BFSI: {e}")

    sector_map = {}
    if os.path.exists(SECTOR_PATH):
        try:
            with open(SECTOR_PATH, "r") as f:
                s_data = json.load(f)
                for sym, v in s_data.items():
                    if isinstance(v, dict):
                        sector_map[sym] = v.get("sector", "OTHERS")
                    else:
                        sector_map[sym] = "OTHERS"
        except Exception as e:
            logging.warning(f"Failed to load sector map: {e}")

    return pit_df, bfsi_symbols, sector_map

def load_daily_prices():
    logging.info("Loading daily price cache...")
    price_cache = {}
    files = glob.glob(os.path.join(PRICE_DIR, "*.parquet"))
    for f in files:
        sym = os.path.basename(f).replace(".parquet", "")
        if sym in ["NIFTY 50", "NIFTY 500", "BANKNIFTY"]:
            continue
        try:
            df = pd.read_parquet(f)
            if "Date" in df.columns:
                df["Date"] = pd.to_datetime(df["Date"])
                if df["Date"].dt.tz is not None:
                    df["Date"] = df["Date"].dt.tz_localize(None)
                df = df.sort_values("Date").reset_index(drop=True)
                if len(df) >= 60 and "Close" in df.columns and "Volume" in df.columns:
                    price_cache[sym] = df
        except Exception:
            continue
    logging.info(f"Loaded {len(price_cache)} valid symbol daily price series.")
    return price_cache

def load_benchmark():
    logging.info("Loading Nifty 500 benchmark...")
    b_df = pd.read_parquet(BENCH_PATH)
    if "Date" not in b_df.columns:
        b_df = b_df.reset_index()
    date_col = next((c for c in b_df.columns if c.lower() == "date"), None)
    if date_col:
        b_df["Date"] = pd.to_datetime(b_df[date_col])
        if b_df["Date"].dt.tz is not None:
            b_df["Date"] = b_df["Date"].dt.tz_localize(None)
    b_df = b_df.sort_values("Date").reset_index(drop=True)
    return b_df

def get_rebalance_dates(bench_df):
    bench_dates = bench_df["Date"].tolist()
    rebalance_dates = []
    bench_df_temp = bench_df.copy()
    bench_df_temp["year"] = bench_df_temp["Date"].dt.year
    bench_df_temp["month"] = bench_df_temp["Date"].dt.month
    for year in range(2018, 2027):
        for month in [6, 12]:
            sub = bench_df_temp[(bench_df_temp["year"] == year) & (bench_df_temp["month"] == month)]
            if not sub.empty:
                last_dt = sub["Date"].max()
                rebalance_dates.append(last_dt)
    return [d for d in rebalance_dates if d <= bench_dates[-1]]

def get_next_trading_day(current_dt, bench_dates):
    for d in bench_dates:
        if d > current_dt:
            return d
    return None

def compute_market_cap_and_rank(as_of_date, price_cache, pit_df, bfsi_symbols, sector_map):
    """
    Computes point-in-time full market capitalization across all symbols,
    ranks them descending (Rank 1 = largest), and filters to Rank 251-500.
    """
    records = []
    for sym, pdf in price_cache.items():
        sub_p = pdf[pdf["Date"] <= as_of_date]
        if len(sub_p) < 60:
            continue
        last_row = sub_p.iloc[-1]
        close_p = float(last_row["Close"])
        if close_p <= 0:
            continue
        
        # 60-day median daily turnover
        last_60 = sub_p.tail(60)
        daily_turnover_inr = (last_60["Volume"] * last_60["Close"]).median()
        daily_turnover_cr = daily_turnover_inr / 10000000.0

        # PIT Fundamentals
        sub_f = pit_df[(pit_df["symbol"] == sym) & (pit_df["conservative_availability_timestamp"] <= as_of_date)]
        if sub_f.empty:
            continue
        latest_f = sub_f.sort_values("period_end_date").iloc[-1]
        
        shares = latest_f.get("shares_outstanding", np.nan)
        if pd.isna(shares) or shares <= 0:
            continue
        
        mcap_cr = (close_p * shares) / 10000000.0
        net_profit = latest_f.get("net_profit", np.nan)
        cfo = latest_f.get("operating_cash_flow", np.nan)
        equity = latest_f.get("total_equity", np.nan)

        records.append({
            "symbol": sym,
            "close": close_p,
            "mcap_cr": mcap_cr,
            "daily_turnover_cr": daily_turnover_cr,
            "net_profit": net_profit,
            "cfo": cfo,
            "equity": equity,
            "is_bfsi": sym in bfsi_symbols,
            "sector": sector_map.get(sym, "OTHERS")
        })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    # Rank all stocks by Market Cap descending (1 = Largest, e.g. Reliance, TCS)
    df["market_cap_rank"] = df["mcap_cr"].rank(ascending=False, method="min")
    return df

def simulate_portfolio(model_type, slippage_bps, price_cache, pit_df, bfsi_symbols, sector_map, bench_df, rebalance_dates):
    bench_dates = bench_df["Date"].tolist()
    initial_nav = 1000000.0
    cash = initial_nav
    positions = {} # sym -> {qty, entry_price, entry_date}
    
    daily_nav_history = []
    trade_ledger = []
    signals_history = []
    cost_ledger = {
        "brokerage": 0.0,
        "stt": 0.0,
        "stamp_duty": 0.0,
        "exchange_turnover": 0.0,
        "gst": 0.0,
        "dp_charges": 0.0,
        "slippage": 0.0
    }

    target_slots = 30
    slot_weight = 1.0 / target_slots # 3.333%
    sector_cap = 0.25
    rebal_idx = 0

    calendar_start = rebalance_dates[0]
    exec_start = get_next_trading_day(calendar_start, bench_dates)
    active_dates = [d for d in bench_dates if d >= exec_start]

    for d in active_dates:
        # Check if today is an execution date corresponding to rebalance_dates[rebal_idx]
        if rebal_idx < len(rebalance_dates):
            reb_dt = rebalance_dates[rebal_idx]
            exec_dt = get_next_trading_day(reb_dt, bench_dates)
            
            if d == exec_dt:
                # Execute Rebalance
                universe_df = compute_market_cap_and_rank(reb_dt, price_cache, pit_df, bfsi_symbols, sector_map)
                
                # Filter to Smallcap Rank 251-500
                sc_df = universe_df[(universe_df["market_cap_rank"] >= 251) & (universe_df["market_cap_rank"] <= 500)].copy()
                
                # Liquidity Filter (>= 5 Cr median daily turnover)
                sc_df = sc_df[sc_df["daily_turnover_cr"] >= 5.0]

                if model_type == "SFP_1_FULL":
                    # Exclude BFSI
                    sc_df = sc_df[sc_df["is_bfsi"] == False]
                    # Financial Survival Filters
                    sc_df = sc_df[
                        (sc_df["net_profit"] > 0) & 
                        (sc_df["cfo"] > 0) & 
                        (sc_df["equity"] > 0)
                    ]
                elif model_type == "SMALLCAP_NO_FILTERS":
                    # No financial survival filters, only smallcap rank 251-500 and liquidity
                    pass

                # Selection: Rank by Smaller Market Cap ascending (smallest first)
                selected_df = sc_df.sort_values("mcap_cr", ascending=True)

                # Sector cap enforcement (max 25% NAV per sector)
                portfolio_candidates = []
                sector_counts = {}
                max_per_sector = int(target_slots * sector_cap) # 30 * 0.25 = 7 stocks max

                for _, r in selected_df.iterrows():
                    sec = r["sector"]
                    if sector_counts.get(sec, 0) < max_per_sector:
                        portfolio_candidates.append(r["symbol"])
                        sector_counts[sec] = sector_counts.get(sec, 0) + 1
                    if len(portfolio_candidates) >= target_slots:
                        break

                for cand in portfolio_candidates:
                    signals_history.append({
                        "rebalance_date": reb_dt.strftime("%Y-%m-%d"),
                        "execution_date": exec_dt.strftime("%Y-%m-%d"),
                        "model": model_type,
                        "symbol": cand,
                        "target_weight_pct": round(slot_weight * 100, 2)
                    })

                # Calculate Current NAV before rebalance
                current_eod_val = cash
                for sym, pos in positions.items():
                    p_sub = price_cache[sym][price_cache[sym]["Date"] == d]
                    px = float(p_sub["Open"].iloc[0]) if not p_sub.empty else pos["entry_price"]
                    current_eod_val += pos["qty"] * px

                # Close positions not in portfolio_candidates
                closed_syms = [s for s in positions.keys() if s not in portfolio_candidates]
                for sym in closed_syms:
                    pos = positions.pop(sym)
                    p_sub = price_cache[sym][price_cache[sym]["Date"] == d]
                    exec_px = float(p_sub["Open"].iloc[0]) if not p_sub.empty else pos["entry_price"]
                    
                    # Deduct sell slippage
                    realized_px = exec_px * (1.0 - slippage_bps / 10000.0)
                    gross_val = pos["qty"] * realized_px
                    
                    # Deduct Sell Friction
                    stt = gross_val * 0.001
                    brok = 20.0
                    turn = gross_val * 0.0000345
                    gst = (brok + turn) * 0.18
                    dp = 15.93
                    tot_fee = stt + brok + turn + gst + dp
                    net_proceeds = gross_val - tot_fee

                    cost_ledger["stt"] += stt
                    cost_ledger["brokerage"] += brok
                    cost_ledger["exchange_turnover"] += turn
                    cost_ledger["gst"] += gst
                    cost_ledger["dp_charges"] += dp
                    cost_ledger["slippage"] += (exec_px - realized_px) * pos["qty"]

                    cash += net_proceeds
                    trade_pnl = net_proceeds - (pos["qty"] * pos["entry_price"])
                    trade_ret = (net_proceeds / (pos["qty"] * pos["entry_price"]) - 1.0) * 100

                    trade_ledger.append({
                        "symbol": sym,
                        "entry_date": pos["entry_date"].strftime("%Y-%m-%d"),
                        "exit_date": d.strftime("%Y-%m-%d"),
                        "qty": pos["qty"],
                        "entry_price": pos["entry_price"],
                        "exit_price": exec_px,
                        "net_pnl": trade_pnl,
                        "return_pct": trade_ret
                    })

                # Buy new candidates
                target_per_slot = current_eod_val * slot_weight
                for sym in portfolio_candidates:
                    if sym not in positions:
                        p_sub = price_cache[sym][price_cache[sym]["Date"] == d]
                        if not p_sub.empty and cash > target_per_slot * 0.5:
                            open_px = float(p_sub["Open"].iloc[0])
                            realized_px = open_px * (1.0 + slippage_bps / 10000.0)
                            alloc = min(target_per_slot, cash * 0.95)
                            qty = int(alloc // realized_px)
                            if qty > 0:
                                gross_buy = qty * realized_px
                                stamp = gross_buy * 0.00015
                                brok = 20.0
                                turn = gross_buy * 0.0000345
                                gst = (brok + turn) * 0.18
                                tot_fee = stamp + brok + turn + gst
                                net_cost = gross_buy + tot_fee

                                if cash >= net_cost:
                                    cash -= net_cost
                                    cost_ledger["stamp_duty"] += stamp
                                    cost_ledger["brokerage"] += brok
                                    cost_ledger["exchange_turnover"] += turn
                                    cost_ledger["gst"] += gst
                                    cost_ledger["slippage"] += (realized_px - open_px) * qty

                                    positions[sym] = {
                                        "qty": qty,
                                        "entry_price": realized_px,
                                        "entry_date": d
                                    }

                rebal_idx += 1

        # Mark-to-market EOD NAV
        eod_equity_val = 0.0
        for sym, pos in positions.items():
            p_sub = price_cache[sym][price_cache[sym]["Date"] == d]
            if not p_sub.empty:
                c_px = float(p_sub["Close"].iloc[0])
            else:
                c_px = pos["entry_price"]
            eod_equity_val += pos["qty"] * c_px

        total_nav = cash + eod_equity_val
        daily_nav_history.append({
            "date": d.strftime("%Y-%m-%d"),
            "nav": total_nav,
            "cash": cash,
            "invested": eod_equity_val,
            "holdings_count": len(positions)
        })

    # Close remaining positions at end of backtest
    final_dt = active_dates[-1]
    for sym, pos in positions.items():
        p_sub = price_cache[sym][price_cache[sym]["Date"] == final_dt]
        c_px = float(p_sub["Close"].iloc[0]) if not p_sub.empty else pos["entry_price"]
        pnl = (pos["qty"] * c_px) - (pos["qty"] * pos["entry_price"])
        trade_ledger.append({
            "symbol": sym,
            "entry_date": pos["entry_date"].strftime("%Y-%m-%d"),
            "exit_date": final_dt.strftime("%Y-%m-%d"),
            "qty": pos["qty"],
            "entry_price": pos["entry_price"],
            "exit_price": c_px,
            "net_pnl": pnl,
            "return_pct": (c_px / pos["entry_price"] - 1.0) * 100
        })

    nav_df = pd.DataFrame(daily_nav_history)
    t_df = pd.DataFrame(trade_ledger)
    sig_df = pd.DataFrame(signals_history)

    return nav_df, t_df, sig_df, cost_ledger

def compute_metrics(nav_df, t_df, initial_nav=1000000.0, risk_free_rate=0.06):
    if nav_df.empty:
        return {}
    
    nav_df["date"] = pd.to_datetime(nav_df["date"])
    start_nav = nav_df["nav"].iloc[0]
    end_nav = nav_df["nav"].iloc[-1]
    days = (nav_df["date"].iloc[-1] - nav_df["date"].iloc[0]).days
    years = days / 365.25

    total_return_pct = (end_nav / initial_nav - 1.0) * 100
    cagr_pct = ((end_nav / initial_nav) ** (1.0 / years) - 1.0) * 100 if years > 0 and end_nav > 0 else 0.0

    # Drawdown
    nav_df["peak"] = nav_df["nav"].cummax()
    nav_df["drawdown"] = (nav_df["nav"] - nav_df["peak"]) / nav_df["peak"]
    max_dd_pct = nav_df["drawdown"].min() * 100

    # Daily returns & Sharpe
    nav_df["daily_ret"] = nav_df["nav"].pct_change().fillna(0.0)
    mean_ret = nav_df["daily_ret"].mean() * 252
    vol = nav_df["daily_ret"].std() * math.sqrt(252)
    sharpe = (mean_ret - risk_free_rate) / vol if vol > 0 else 0.0

    # Trades
    total_trades = len(t_df)
    win_rate = 0.0
    profit_factor = 0.0
    if total_trades > 0:
        wins = t_df[t_df["net_pnl"] > 0]
        losses = t_df[t_df["net_pnl"] < 0]
        win_rate = (len(wins) / total_trades) * 100
        gross_win = wins["net_pnl"].sum()
        gross_loss = abs(losses["net_pnl"].sum())
        profit_factor = gross_win / gross_loss if gross_loss > 0 else (gross_win if gross_win > 0 else 1.0)

    return {
        "net_cagr_pct": round(cagr_pct, 2),
        "total_return_pct": round(total_return_pct, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "total_trades": total_trades,
        "win_rate_pct": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2)
    }

def compute_temporal_windows(nav_df, bench_df):
    bench_df = bench_df.copy()
    bench_df["Date"] = pd.to_datetime(bench_df["Date"])
    nav_df = nav_df.copy()
    nav_df["date"] = pd.to_datetime(nav_df["date"])

    windows = [
        ("Development Period (2018-2020)", "2018-07-02", "2020-12-31"),
        ("Validation Period (2021-2023)", "2021-01-01", "2023-12-31"),
        ("Locked Holdout Period (2024-2026)", "2024-01-01", "2026-10-12")
    ]

    out = []
    for name, s_dt, e_dt in windows:
        sub_n = nav_df[(nav_df["date"] >= s_dt) & (nav_df["date"] <= e_dt)].copy()
        sub_b = bench_df[(bench_df["Date"] >= s_dt) & (bench_df["Date"] <= e_dt)].copy()

        if sub_n.empty or sub_b.empty:
            continue

        n_years = (sub_n["date"].iloc[-1] - sub_n["date"].iloc[0]).days / 365.25
        b_years = (sub_b["Date"].iloc[-1] - sub_b["Date"].iloc[0]).days / 365.25

        n_cagr = ((sub_n["nav"].iloc[-1] / sub_n["nav"].iloc[0]) ** (1.0 / n_years) - 1.0) * 100 if n_years > 0 else 0.0
        b_cagr = ((sub_b["Close"].iloc[-1] / sub_b["Close"].iloc[0]) ** (1.0 / b_years) - 1.0) * 100 if b_years > 0 else 0.0

        excess_vs_market = n_cagr - b_cagr

        # Survivorship-Adjusted Excess
        adj_cagr = n_cagr - SURVIVORSHIP_HAIRCUT_CAGR
        adj_excess = adj_cagr - b_cagr

        # Max DD
        sub_n["peak"] = sub_n["nav"].cummax()
        max_dd = ((sub_n["nav"] - sub_n["peak"]) / sub_n["peak"]).min() * 100

        # Daily Sharpe
        sub_n["ret"] = sub_n["nav"].pct_change().fillna(0.0)
        vol = sub_n["ret"].std() * math.sqrt(252)
        sharpe = ((sub_n["ret"].mean() * 252) - 0.06) / vol if vol > 0 else 0.0

        status = "PASS" if excess_vs_market > 0 else "FAIL"

        out.append({
            "temporal_window": name,
            "calendar_range": f"{s_dt} to {e_dt}",
            "sessions": len(sub_n),
            "raw_sfp1_cagr_pct": round(n_cagr, 2),
            "survivor_adj_cagr_pct": round(adj_cagr, 2),
            "nifty500_tri_cagr_pct": round(b_cagr, 2),
            "raw_excess_vs_nifty500": round(excess_vs_market, 2),
            "adj_excess_vs_nifty500": round(adj_excess, 2),
            "sharpe": round(sharpe, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "status": status
        })

    return pd.DataFrame(out)

def main():
    logging.info("=== Starting SFP-1 Simulation & Historical Backtest Engine ===")
    pit_df, bfsi_symbols, sector_map = load_environment()
    price_cache = load_daily_prices()
    bench_df = load_benchmark()
    rebalance_dates = get_rebalance_dates(bench_df)
    logging.info(f"Semiannual rebalance dates: {len(rebalance_dates)} dates ({rebalance_dates[0].strftime('%Y-%m-%d')} to {rebalance_dates[-1].strftime('%Y-%m-%d')})")

    # Run Tournament Simulations
    models = [
        ("Model 1: SFP-1 Full Strategy (Base Cost)", "SFP_1_FULL", 5.0),
        ("Model 1: SFP-1 Adverse Cost (10 bps slippage)", "SFP_1_FULL", 10.0),
        ("Model 1: SFP-1 Adverse Cost (20 bps slippage)", "SFP_1_FULL", 20.0),
        ("Model 2: Smallcap Unfiltered Control (No Survival Gates)", "SMALLCAP_NO_FILTERS", 5.0)
    ]

    results = []
    primary_nav_df = None
    primary_t_df = None
    primary_sig_df = None
    primary_cost = None

    for label, m_type, slip in models:
        logging.info(f"Running simulation: {label}")
        nav_df, t_df, sig_df, cost_led = simulate_portfolio(
            m_type, slip, price_cache, pit_df, bfsi_symbols, sector_map, bench_df, rebalance_dates
        )
        met = compute_metrics(nav_df, t_df)
        met["model_name"] = label
        results.append(met)

        if label == "Model 1: SFP-1 Full Strategy (Base Cost)":
            primary_nav_df = nav_df
            primary_t_df = t_df
            primary_sig_df = sig_df
            primary_cost = cost_led

    # Benchmark metrics
    bench_df_sub = bench_df[bench_df["Date"] >= pd.to_datetime(primary_nav_df["date"].iloc[0])].copy()
    b_years = (bench_df_sub["Date"].iloc[-1] - bench_df_sub["Date"].iloc[0]).days / 365.25
    b_cagr = ((bench_df_sub["Close"].iloc[-1] / bench_df_sub["Close"].iloc[0]) ** (1.0 / b_years) - 1.0) * 100
    b_tot = (bench_df_sub["Close"].iloc[-1] / bench_df_sub["Close"].iloc[0] - 1.0) * 100
    bench_df_sub["peak"] = bench_df_sub["Close"].cummax()
    b_dd = ((bench_df_sub["Close"] - bench_df_sub["peak"]) / bench_df_sub["peak"]).min() * 100
    bench_df_sub["ret"] = bench_df_sub["Close"].pct_change().fillna(0.0)
    b_vol = bench_df_sub["ret"].std() * math.sqrt(252)
    b_sharpe = ((bench_df_sub["ret"].mean() * 252) - 0.06) / b_vol if b_vol > 0 else 0.0

    results.append({
        "model_name": "Nifty 500 TRI (Broad Market Benchmark)",
        "net_cagr_pct": round(b_cagr, 2),
        "total_return_pct": round(b_tot, 2),
        "sharpe_ratio": round(b_sharpe, 2),
        "max_drawdown_pct": round(b_dd, 2),
        "total_trades": 0,
        "win_rate_pct": 0.0,
        "profit_factor": 0.0
    })

    # Add Survivorship-Adjusted SFP-1 row
    sfp1_base = next(r for r in results if r["model_name"] == "Model 1: SFP-1 Full Strategy (Base Cost)")
    adj_cagr = round(sfp1_base["net_cagr_pct"] - SURVIVORSHIP_HAIRCUT_CAGR, 2)
    results.append({
        "model_name": "Model 1: SFP-1 (Survivorship-Adjusted Net CAGR)",
        "net_cagr_pct": adj_cagr,
        "total_return_pct": round(((1.0 + adj_cagr / 100.0) ** b_years - 1.0) * 100, 2),
        "sharpe_ratio": round(sfp1_base["sharpe_ratio"] - 0.25, 2),
        "max_drawdown_pct": round(sfp1_base["max_drawdown_pct"] - 5.0, 2),
        "total_trades": sfp1_base["total_trades"],
        "win_rate_pct": sfp1_base["win_rate_pct"],
        "profit_factor": sfp1_base["profit_factor"]
    })

    bench_comp_df = pd.DataFrame(results)
    col_order = ["model_name", "net_cagr_pct", "total_return_pct", "sharpe_ratio", "max_drawdown_pct", "total_trades", "win_rate_pct", "profit_factor"]
    bench_comp_df = bench_comp_df[col_order]

    # Temporal Validation
    temp_val_df = compute_temporal_windows(primary_nav_df, bench_df)

    # Cost breakdown table
    cost_df = pd.DataFrame([
        {"cost_component": "Total Brokerage (₹20/order)", "amount_inr": round(primary_cost["brokerage"], 2)},
        {"cost_component": "Securities Transaction Tax (STT 0.1% on Sell)", "amount_inr": round(primary_cost["stt"], 2)},
        {"cost_component": "Execution Slippage (5 bps per side)", "amount_inr": round(primary_cost["slippage"], 2)},
        {"cost_component": "Stamp Duty (0.015% on Buy)", "amount_inr": round(primary_cost["stamp_duty"], 2)},
        {"cost_component": "Depository Charges (DP ₹15.93/sell)", "amount_inr": round(primary_cost["dp_charges"], 2)},
        {"cost_component": "Exchange & SEBI Turnover Fees", "amount_inr": round(primary_cost["exchange_turnover"], 2)},
        {"cost_component": "GST (18% on Statutory Fees)", "amount_inr": round(primary_cost["gst"], 2)},
        {"cost_component": "TOTAL TRANSACTION FRICTION", "amount_inr": round(sum(primary_cost.values()), 2)}
    ])

    # Save CSVs
    logging.info("Writing output CSVs to reports/ and research/strategies/sfp_1/reports/...")
    csv_mappings = [
        ("sfp_benchmark_comparison.csv", bench_comp_df),
        ("sfp_temporal_validation.csv", temp_val_df),
        ("sfp_portfolio_nav.csv", primary_nav_df),
        ("sfp_trade_ledger.csv", primary_t_df),
        ("sfp_signal_history.csv", primary_sig_df),
        ("sfp_transaction_cost_audit.csv", cost_df)
    ]

    for fname, df in csv_mappings:
        df.to_csv(os.path.join(REPORTS_DIR, fname), index=False)
        df.to_csv(os.path.join(STRAT_REPORTS_DIR, fname), index=False)

    # Manifest
    manifest = {
        "strategy": "SFP-1",
        "timestamp_utc": datetime.utcnow().isoformat(),
        "models_evaluated": [r["model_name"] for r in results],
        "survivorship_limitation": "CONFIRMED_UPWARD_BIAS_SURVIVING_SAMPLE",
        "survivorship_haircut_cagr_pct": SURVIVORSHIP_HAIRCUT_CAGR,
        "files_generated": [fname for fname, _ in csv_mappings]
    }
    with open(os.path.join(AUDIT_DIR, "sfp_1_manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    with open(os.path.join(REPORTS_DIR, "sfp_1_manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    print("\n" + "="*80)
    print("SFP-1 BENCHMARK COMPARISON MATRIX")
    print("="*80)
    print(bench_comp_df.to_string(index=False))

    print("\n" + "="*80)
    print("TEMPORAL VALIDATION TABLE (SFP-1)")
    print("="*80)
    print(temp_val_df.to_string(index=False))

    logging.info("SFP-1 Simulation Engine Completed Successfully.")

if __name__ == "__main__":
    main()
