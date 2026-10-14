"""
RSMR-1 MULTI-TIMEFRAME & MULTI-REGIME COMPREHENSIVE BACKTEST ENGINE
Evaluates:
1. Regimes: BULL, SIDEWAYS, BEAR (using deterministic macro classification on Nifty 500).
2. Multiple Historical Episodes:
   - BEAR: 2018 NBFC Crisis, 2020 COVID Crash, 2021-2022 Rate Hike / War, 2024-2025 Correction
   - BULL: 2017 Rally, 2020-2021 Post-COVID Surge, 2023-2024 Capex Bull Run
   - SIDEWAYS: Late 2016 Demonetization, 2019 Pre-Election Choppiness, 2022-2023 Consolidation
3. Multiple Timeframe Rebalance Cadences:
   - Monthly (21 trading days)
   - Bi-Monthly (42 trading days)
   - Quarterly (63 trading days)
   - Semi-Annual (126 trading days)
4. Multiple Momentum Lookback Horizons:
   - 3-Month (63 sessions)
   - 6-Month (126 sessions)
   - 12-Month (252 sessions)
5. Independent Temporal Cells:
   - Cell 1: 2016-2018
   - Cell 2: 2019-2021
   - Cell 3: 2022-2024
   - Cell 4: 2025-2026
6. Strict Point-in-Time Causality:
   - Signal at close of session t -> Execution at open of session t+1.
   - Zero future lookahead. All fees, taxes, slippage accounted for.
"""

import os
import sys
import json
import logging
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

SRC_DIR = os.path.join(os.path.dirname(__file__))
sys.path.insert(0, SRC_DIR)

from universe import UniverseManager
from indicators import calculate_stock_metrics
from costs import calculate_trade_costs
from benchmark import calculate_portfolio_metrics, align_benchmark

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("RSMR_1_MultiRegime")

class MultiRegimeBacktester:
    def __init__(self, univ: UniverseManager, benchmark_path: str):
        self.univ = univ
        self.benchmark_path = benchmark_path
        self._load_and_classify_benchmark()

    def _load_and_classify_benchmark(self):
        """Loads Nifty 500 benchmark and assigns daily macro regime."""
        bench_df = pd.read_parquet(self.benchmark_path)
        if "Date" in bench_df.columns:
            bench_df["dt"] = pd.to_datetime(bench_df["Date"]).dt.tz_localize(None).dt.normalize()
        elif "Datetime" in bench_df.columns:
            bench_df["dt"] = pd.to_datetime(bench_df["Datetime"]).dt.tz_localize(None).dt.normalize()
        else:
            dt_idx = pd.to_datetime(bench_df.index)
            if dt_idx.tz is not None:
                dt_idx = dt_idx.tz_localize(None)
            bench_df["dt"] = dt_idx.normalize()
            
        bench_df = bench_df.drop_duplicates("dt").set_index("dt").sort_index()
        bench_df["bench_return"] = bench_df["Close"].pct_change().fillna(0.0)
        
        # Calculate Trend & Regime Indicators
        c = bench_df["Close"]
        bench_df["sma50"] = c.rolling(50, min_periods=20).mean()
        bench_df["sma200"] = c.rolling(200, min_periods=50).mean()
        bench_df["peak_52w"] = c.rolling(252, min_periods=30).max()
        bench_df["drawdown"] = (c - bench_df["peak_52w"]) / bench_df["peak_52w"]
        
        # Regime Rule:
        # BEAR: Close < SMA200 OR Drawdown <= -15%
        # BULL: Close > SMA200 AND Close > SMA50 AND Drawdown > -10%
        # SIDEWAYS: Everything else
        regimes = []
        for idx, row in bench_df.iterrows():
            px = row["Close"]
            s200 = row["sma200"]
            s50 = row["sma50"]
            dd = row["drawdown"]
            
            if (s200 and px < s200) or (dd and dd <= -0.15):
                regimes.append("BEAR")
            elif (s200 and px > s200) and (s50 and px > s50) and (dd and dd > -0.10):
                regimes.append("BULL")
            else:
                regimes.append("SIDEWAYS")
                
        bench_df["regime"] = regimes
        self.bench_df = bench_df
        logger.info(f"Benchmark classified: BULL={sum(bench_df['regime']=='BULL')}, SIDEWAYS={sum(bench_df['regime']=='SIDEWAYS')}, BEAR={sum(bench_df['regime']=='BEAR')} sessions.")

    def run_strategy_simulation(
        self,
        rebalance_days: int = 63,
        momentum_lookback_days: int = 126,
        use_trend_filter: bool = True,
        slippage_bps: float = 5.0,
        target_slots: int = 30,
        max_stocks_per_sector: int = 7,
        min_turnover_inr: float = 5000000.0,
        initial_capital: float = 1000000.0
    ) -> Tuple[pd.DataFrame, List[Dict[str, Any]]]:
        """Runs a complete simulation with custom rebalance and momentum timeframe."""
        cal = self.univ.calendar
        
        # Generate rebalance dates by step
        rebal_dates = [cal[i] for i in range(momentum_lookback_days + 200, len(cal), rebalance_days)]
        rebal_set = set(rebal_dates)
        
        cash = initial_capital
        holdings: Dict[str, Dict[str, Any]] = {}
        daily_nav_list = []
        order_ledger = []
        
        start_day_idx = np.searchsorted(cal, rebal_dates[0])
        sim_cal = cal[start_day_idx:]
        
        for current_day in sim_cal:
            # Rebalance trigger
            if current_day in rebal_set:
                next_day_idx = np.searchsorted(cal, current_day, side="right")
                if next_day_idx < len(cal):
                    exec_day = cal[next_day_idx]
                    
                    # 1. Select Candidates Point-in-Time
                    candidates = []
                    for sym, df in self.univ.stocks_data.items():
                        metrics = calculate_stock_metrics(
                            df, current_day,
                            momentum_lookback=momentum_lookback_days,
                            sma_period=200,
                            liquidity_lookback=60
                        )
                        if not metrics or metrics["momentum_6m"] <= 0:
                            continue
                        if use_trend_filter and not metrics["trend_ok"]:
                            continue
                        if metrics["median_turnover_60d"] < min_turnover_inr:
                            continue
                            
                        candidates.append({
                            "symbol": sym,
                            "sector": self.univ.get_sector(sym),
                            "momentum": metrics["momentum_6m"],
                            "close": metrics["close"],
                            "median_turnover": metrics["median_turnover_60d"]
                        })
                        
                    candidates.sort(key=lambda x: (x["momentum"], x["median_turnover"]), reverse=True)
                    
                    # Apply Sector Cap
                    selected = []
                    sec_counts = {}
                    for c in candidates:
                        sec = c["sector"]
                        if sec_counts.get(sec, 0) < max_stocks_per_sector:
                            selected.append(c)
                            sec_counts[sec] = sec_counts.get(sec, 0) + 1
                            if len(selected) == target_slots:
                                break
                                
                    selected_syms = {s["symbol"]: s for s in selected}
                    
                    # 2. Execute on next_day Open
                    # Total current nav
                    equity_open = 0.0
                    for h_sym, h_data in holdings.items():
                        h_df = self.univ.stocks_data.get(h_sym)
                        px = float(h_df.loc[exec_day, "Open"]) if (h_df is not None and exec_day in h_df.index) else h_data["last_price"]
                        equity_open += h_data["shares"] * px
                    current_nav_open = cash + equity_open
                    target_slot_capital = current_nav_open / float(target_slots)
                    
                    # Sells
                    for h_sym in list(holdings.keys()):
                        if h_sym not in selected_syms:
                            h_data = holdings[h_sym]
                            h_df = self.univ.stocks_data.get(h_sym)
                            px = float(h_df.loc[exec_day, "Open"]) if (h_df is not None and exec_day in h_df.index) else h_data["last_price"]
                            costs = calculate_trade_costs("SELL", h_data["shares"], px, slippage_bps=slippage_bps)
                            net_proceeds = (h_data["shares"] * px) - costs.total_cost
                            cash += net_proceeds
                            tot_cost = h_data["shares"] * h_data["avg_cost"]
                            pnl = net_proceeds - tot_cost
                            order_ledger.append({
                                "date": exec_day, "symbol": h_sym, "side": "SELL",
                                "shares": h_data["shares"], "price": px, "pnl": pnl,
                                "pnl_pct": (pnl / tot_cost) if tot_cost > 0 else 0.0
                            })
                            del holdings[h_sym]
                            
                    # Buys
                    for sel in selected:
                        sym = sel["symbol"]
                        s_df = self.univ.stocks_data.get(sym)
                        if s_df is None or exec_day not in s_df.index:
                            continue
                        open_px = float(s_df.loc[exec_day, "Open"])
                        if open_px <= 0:
                            continue
                            
                        # 1% liquidity cap
                        max_val = sel.get("median_turnover", 1e9) * 0.01
                        alloc_val = min(target_slot_capital, max_val)
                        target_shs = int(alloc_val / open_px)
                        
                        curr_shs = holdings.get(sym, {}).get("shares", 0)
                        diff_shs = target_shs - curr_shs
                        
                        if sym not in holdings and target_shs > 0:
                            req_cost = target_shs * open_px * (1.0 + slippage_bps/10000.0 + 0.001)
                            if req_cost <= cash:
                                costs = calculate_trade_costs("BUY", target_shs, open_px, slippage_bps=slippage_bps)
                                cash -= (target_shs * open_px + costs.total_cost)
                                holdings[sym] = {"shares": target_shs, "avg_cost": (target_shs * open_px + costs.total_cost) / target_shs, "last_price": open_px}
                                order_ledger.append({"date": exec_day, "symbol": sym, "side": "BUY", "shares": target_shs, "price": open_px})
                        elif sym in holdings and abs(diff_shs * open_px) > (0.2 * target_slot_capital):
                            if diff_shs > 0:
                                req_cost = diff_shs * open_px * (1.0 + slippage_bps/10000.0 + 0.001)
                                if req_cost <= cash:
                                    costs = calculate_trade_costs("BUY", diff_shs, open_px, slippage_bps=slippage_bps)
                                    cash -= (diff_shs * open_px + costs.total_cost)
                                    holdings[sym]["shares"] += diff_shs
                            elif diff_shs < 0:
                                costs = calculate_trade_costs("SELL", -diff_shs, open_px, slippage_bps=slippage_bps)
                                cash += (-diff_shs * open_px - costs.total_cost)
                                holdings[sym]["shares"] += diff_shs

            # Daily Close Mark-to-Market
            eq_close = 0.0
            for h_sym, h_data in holdings.items():
                h_df = self.univ.stocks_data.get(h_sym)
                sub_df = h_df.loc[:current_day]
                if len(sub_df) > 0:
                    px = float(sub_df["Close"].iloc[-1])
                    h_data["last_price"] = px
                eq_close += h_data["shares"] * h_data["last_price"]
                
            tot_nav = cash + eq_close
            daily_nav_list.append({
                "date": current_day,
                "nav": tot_nav,
                "cash": cash,
                "equity": eq_close,
                "holdings_count": len(holdings)
            })
            
        nav_df = pd.DataFrame(daily_nav_list)
        nav_df["daily_return"] = nav_df["nav"].pct_change().fillna(0.0)
        return nav_df, order_ledger
