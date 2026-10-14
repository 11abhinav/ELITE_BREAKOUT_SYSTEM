"""
RSMR-1 Portfolio Simulator & Execution Engine.
Strictly point-in-time accounting with realistic statutory fees, whole shares, and cash conservation.
"""

from typing import Dict, List, Optional, Any, Set, Tuple
import pandas as pd
import numpy as np
import logging

from universe import UniverseManager
from indicators import calculate_stock_metrics
from costs import calculate_trade_costs, CostBreakdown

logger = logging.getLogger("RSMR_1_Simulator")

class PortfolioSimulator:
    def __init__(
        self,
        universe: UniverseManager,
        initial_capital: float = 1000000.0, # ₹10 Lakh default
        slippage_bps: float = 5.0,
        use_trend_filter: bool = True,
        min_turnover_inr: float = 5000000.0,
        max_participation_rate: float = 0.01,
        target_slots: int = 30,
        max_stocks_per_sector: int = 7
    ):
        self.univ = universe
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.slippage_bps = slippage_bps
        self.use_trend_filter = use_trend_filter
        self.min_turnover_inr = min_turnover_inr
        self.max_participation_rate = max_participation_rate
        self.target_slots = target_slots
        self.max_stocks_per_sector = max_stocks_per_sector
        
        # Portfolio state: symbol -> {"shares": int, "avg_cost": float, "last_price": float}
        self.holdings: Dict[str, Dict[str, Any]] = {}
        
        # History ledgers
        self.daily_nav_history: List[Dict[str, Any]] = []
        self.order_ledger: List[Dict[str, Any]] = []
        self.rebalance_history: List[Dict[str, Any]] = []
        self.holdings_history: List[Dict[str, Any]] = []
        self.total_costs_paid = 0.0

    def select_portfolio(self, as_of_date: pd.Timestamp) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Calculates rankings as of decision session close.
        Returns (selected_candidates, all_ranked_candidates).
        """
        candidates = []
        for sym, df in self.univ.stocks_data.items():
            metrics = calculate_stock_metrics(
                df, as_of_date,
                momentum_lookback=126,
                sma_period=200,
                liquidity_lookback=60
            )
            if not metrics:
                continue
                
            # Filter criteria:
            # 1. Positive 6-month momentum
            if metrics["momentum_6m"] <= 0:
                continue
                
            # 2. Trend filter (optional ablation)
            if self.use_trend_filter and not metrics["trend_ok"]:
                continue
                
            # 3. Minimum median daily turnover
            if metrics["median_turnover_60d"] < self.min_turnover_inr:
                continue
                
            sector = self.univ.get_sector(sym)
            candidates.append({
                "symbol": sym,
                "sector": sector,
                "momentum_6m": metrics["momentum_6m"],
                "close": metrics["close"],
                "sma_200": metrics["sma_200"],
                "median_turnover": metrics["median_turnover_60d"]
            })
            
        # Rank descending by momentum_6m, secondary tie-break by higher median_turnover
        candidates.sort(key=lambda x: (x["momentum_6m"], x["median_turnover"]), reverse=True)
        
        # Apply Sector Cap: max_stocks_per_sector
        selected = []
        sector_counts: Dict[str, int] = {}
        
        for cand in candidates:
            sec = cand["sector"]
            current_sec_count = sector_counts.get(sec, 0)
            if current_sec_count < self.max_stocks_per_sector:
                selected.append(cand)
                sector_counts[sec] = current_sec_count + 1
                if len(selected) == self.target_slots:
                    break
                    
        return selected, candidates

    def run_simulation(
        self,
        start_date: Optional[pd.Timestamp] = None,
        end_date: Optional[pd.Timestamp] = None
    ) -> pd.DataFrame:
        """
        Executes quarterly rebalances and records daily portfolio mark-to-market.
        """
        rebal_dates = self.univ.get_quarterly_rebalance_dates()
        
        if start_date:
            rebal_dates = [d for d in rebal_dates if d >= start_date]
        if end_date:
            rebal_dates = [d for d in rebal_dates if d <= end_date]
            
        if not rebal_dates:
            raise RuntimeError("No rebalance dates found within the specified date range.")

        # Determine full trading day range from first rebalance to end
        all_cal = [d for d in self.univ.calendar if d >= rebal_dates[0]]
        if end_date:
            all_cal = [d for d in all_cal if d <= end_date]
            
        rebal_set = set(rebal_dates)
        
        logger.info(f"Running simulation: {len(rebal_dates)} quarterly rebalances across {len(all_cal)} trading days.")

        for current_day in all_cal:
            # Check if today is a rebalance decision day
            if current_day in rebal_set:
                exec_day = self.univ.get_next_trading_day(current_day)
                if exec_day and (end_date is None or exec_day <= end_date):
                    selected, all_cands = self.select_portfolio(current_day)
                    
                    self.rebalance_history.append({
                        "decision_date": current_day,
                        "exec_date": exec_day,
                        "eligible_count": len(all_cands),
                        "selected_count": len(selected),
                        "portfolio_nav": self._calculate_current_nav(current_day)
                    })
                    
                    # Execute Rebalance on next day open
                    self._execute_rebalance(current_day, exec_day, selected)

            # Record daily NAV at day close
            nav = self._calculate_current_nav(current_day)
            equity_val = nav - self.cash
            
            self.daily_nav_history.append({
                "date": current_day,
                "nav": nav,
                "cash": self.cash,
                "equity_value": equity_val,
                "holdings_count": len(self.holdings),
                "invested_pct": (equity_val / nav) if nav > 0 else 0.0
            })
            
        nav_df = pd.DataFrame(self.daily_nav_history)
        nav_df["daily_return"] = nav_df["nav"].pct_change().fillna(0.0)
        nav_df["cum_return"] = (nav_df["nav"] / self.initial_capital) - 1.0
        return nav_df

    def _execute_rebalance(
        self,
        decision_date: pd.Timestamp,
        exec_date: pd.Timestamp,
        selected_candidates: List[Dict[str, Any]]
    ):
        """Executes sells then buys on exec_date at OPEN prices."""
        current_nav = self._calculate_current_nav(exec_date, price_col="Open")
        target_slot_capital = current_nav / float(self.target_slots)
        
        selected_syms = {c["symbol"]: c for c in selected_candidates}
        current_held_syms = set(self.holdings.keys())
        
        # 1. SELLS: Liquidate stocks no longer selected
        for sym in list(current_held_syms):
            if sym not in selected_syms:
                self._sell_stock(sym, exec_date, reason="ROTATION_EXIT")
                
        # 2. BUYS: Establish or rebalance positions for selected stocks
        for sym, cand in selected_syms.items():
            df = self.univ.stocks_data.get(sym)
            if df is None or exec_date not in df.index:
                # Execution price unavailable on exec_date - skip
                continue
                
            open_price = float(df.loc[exec_date, "Open"])
            if open_price <= 0:
                continue
                
            # Liquidity Capacity Limit: Max 1% of median daily volume
            med_turnover = cand.get("median_turnover", 1e9)
            max_permitted_trade_val = med_turnover * self.max_participation_rate
            allocated_val = min(target_slot_capital, max_permitted_trade_val)
            
            target_shares = int(allocated_val / open_price)
            current_shares = self.holdings.get(sym, {}).get("shares", 0)
            
            if sym not in self.holdings:
                # New Position
                if target_shares > 0:
                    self._buy_stock(sym, target_shares, open_price, exec_date, cand["sector"], cand["momentum_6m"])
            else:
                # Existing Position: Check if rebalance adjustment is needed (>20% drift)
                diff_shares = target_shares - current_shares
                if diff_shares > 0 and (diff_shares * open_price) > (0.2 * target_slot_capital):
                    self._buy_stock(sym, diff_shares, open_price, exec_date, cand["sector"], cand["momentum_6m"])
                elif diff_shares < 0 and (-diff_shares * open_price) > (0.2 * target_slot_capital):
                    self._sell_stock(sym, exec_date, shares_to_sell=-diff_shares, reason="REBALANCE_TRIM")

        # Record holdings snapshot after rebalance
        for sym, h in self.holdings.items():
            self.holdings_history.append({
                "date": exec_date,
                "symbol": sym,
                "shares": h["shares"],
                "price": h["last_price"],
                "market_value": h["shares"] * h["last_price"],
                "sector": h.get("sector", "OTHERS")
            })

    def _buy_stock(self, sym: str, shares: int, price: float, dt: pd.Timestamp, sector: str, momentum: float):
        costs = calculate_trade_costs("BUY", shares, price, slippage_bps=self.slippage_bps)
        gross_cost = shares * price
        total_required = gross_cost + costs.total_cost
        
        # If insufficient cash, reduce shares to available cash
        if total_required > self.cash:
            if self.cash <= 1000:
                return
            shares = int((self.cash - 100) / (price * (1 + self.slippage_bps/10000.0 + 0.001)))
            if shares <= 0:
                return
            costs = calculate_trade_costs("BUY", shares, price, slippage_bps=self.slippage_bps)
            gross_cost = shares * price
            total_required = gross_cost + costs.total_cost
            
        self.cash -= total_required
        self.total_costs_paid += costs.total_cost
        
        if sym in self.holdings:
            prev_shares = self.holdings[sym]["shares"]
            prev_cost = self.holdings[sym]["avg_cost"]
            new_shares = prev_shares + shares
            new_avg = ((prev_shares * prev_cost) + total_required) / new_shares
            self.holdings[sym]["shares"] = new_shares
            self.holdings[sym]["avg_cost"] = new_avg
            self.holdings[sym]["last_price"] = price
        else:
            self.holdings[sym] = {
                "shares": shares,
                "avg_cost": total_required / shares,
                "last_price": price,
                "sector": sector,
                "entry_date": dt,
                "entry_momentum": momentum
            }
            
        self.order_ledger.append({
            "date": dt,
            "symbol": sym,
            "side": "BUY",
            "shares": shares,
            "fill_price": price,
            "gross_amount": gross_cost,
            "costs": costs.total_cost,
            "net_amount": total_required,
            "cash_after": self.cash
        })

    def _sell_stock(self, sym: str, dt: pd.Timestamp, shares_to_sell: Optional[int] = None, reason: str = "ROTATION_EXIT"):
        if sym not in self.holdings:
            return
            
        held = self.holdings[sym]
        tot_shares = held["shares"]
        shares = tot_shares if shares_to_sell is None else min(shares_to_sell, tot_shares)
        if shares <= 0:
            return
            
        df = self.univ.stocks_data.get(sym)
        price = float(df.loc[dt, "Open"]) if (df is not None and dt in df.index) else held["last_price"]
        
        costs = calculate_trade_costs("SELL", shares, price, slippage_bps=self.slippage_bps)
        gross_proceeds = shares * price
        net_proceeds = gross_proceeds - costs.total_cost
        
        self.cash += net_proceeds
        self.total_costs_paid += costs.total_cost
        
        realized_pnl = net_proceeds - (shares * held["avg_cost"])
        
        self.order_ledger.append({
            "date": dt,
            "symbol": sym,
            "side": "SELL",
            "shares": shares,
            "fill_price": price,
            "gross_amount": gross_proceeds,
            "costs": costs.total_cost,
            "net_amount": net_proceeds,
            "realized_pnl": realized_pnl,
            "reason": reason,
            "cash_after": self.cash
        })
        
        if shares == tot_shares:
            del self.holdings[sym]
        else:
            self.holdings[sym]["shares"] -= shares
            self.holdings[sym]["last_price"] = price

    def _calculate_current_nav(self, dt: pd.Timestamp, price_col: str = "Close") -> float:
        """Mark-to-market total portfolio net asset value."""
        equity_val = 0.0
        for sym, h in self.holdings.items():
            df = self.univ.stocks_data.get(sym)
            if df is not None:
                # Find latest available price up to dt
                sub = df.loc[:dt]
                if len(sub) > 0:
                    px = float(sub[price_col].iloc[-1]) if price_col in sub.columns else float(sub["Close"].iloc[-1])
                    h["last_price"] = px
            equity_val += h["shares"] * h["last_price"]
        return self.cash + equity_val
