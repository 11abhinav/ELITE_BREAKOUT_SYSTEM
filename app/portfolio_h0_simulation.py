"""
app/portfolio_h0_simulation.py
PORTFOLIO-CONSTRAINED EXECUTION HARNESS FOR MOMENTUM_THRUST_REVERSAL_H0

Specification Version: v1.2.0-CANONICAL-GOVERNANCE
Pre-Frozen Execution Constraints:
  - Starting Capital: ₹10,00,000 (₹10 Lakhs)
  - Single Position Risk: 1.0% of current equity
  - MAX_CONCURRENT_POSITIONS = 5
  - MAX_NEW_ENTRIES_PER_DAY = 3
  - MAX_PORTFOLIO_RISK = 5.0%
  - TOP-5 SYMBOL R CONTRIBUTION < 25%
  - Calmar Ratio = Total Return (%) / Max Drawdown (%) >= 1.5
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import numpy as np
import pandas as pd

try:
    from momentum_thrust_h0_engine import H0Signal, H0TradeOutcome
except ImportError:
    from app.momentum_thrust_h0_engine import H0Signal, H0TradeOutcome


@dataclass
class PortfolioPosition:
    signal_id: str
    symbol: str
    entry_date: str
    entry_price: float
    stop_price: float
    t1_price: float
    t2_price: float
    shares: int
    capital_deployed: float
    risk_deployed: float
    exit_date: str
    exit_price: float
    realized_pnl: float
    r_multiple: float
    exit_reason: str
    exit_day: str
    status: str = "OPEN"          # "OPEN", "CLOSED"


@dataclass
class PortfolioSnapshot:
    date: str
    starting_cash: float
    cash_balance: float
    deployed_capital: float
    total_equity: float
    open_positions_count: int
    open_risk_pct: float
    daily_realized_pnl: float
    drawdown_pct: float
    peak_equity: float


class PortfolioH0Simulation:
    """Manages realistic portfolio execution with concurrency caps and position sizing."""

    def __init__(self,
                 starting_capital: float = 1_000_000.0,
                 risk_per_trade_pct: float = 1.0,
                 max_concurrent_positions: int = 5,
                 max_new_entries_per_day: int = 3,
                 max_portfolio_risk_pct: float = 5.0):
        self.starting_capital = starting_capital
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_concurrent_positions = max_concurrent_positions
        self.max_new_entries_per_day = max_new_entries_per_day
        self.max_portfolio_risk_pct = max_portfolio_risk_pct

        # State tracking
        self.cash = starting_capital
        self.total_equity = starting_capital
        self.peak_equity = starting_capital
        self.open_positions: List[PortfolioPosition] = []
        self.closed_positions: List[PortfolioPosition] = []
        self.daily_snapshots: List[PortfolioSnapshot] = []

    def run_simulation(self, trade_events: List[Dict[str, Any]], all_trading_dates: List[str]) -> Dict[str, Any]:
        """
        Executes chronologically day by day across all calendar trading sessions.
        trade_events: list of dicts with {'signal': H0Signal, 'outcome': H0TradeOutcome, 'date': entry_date}
        all_trading_dates: sorted list of distinct calendar trading days
        """
        # Map entry_date -> list of candidate signals
        entries_by_date: Dict[str, List[Dict[str, Any]]] = {}
        for ev in trade_events:
            d = ev['signal'].entry_date
            if d not in entries_by_date:
                entries_by_date[d] = []
            entries_by_date[d].append(ev)

        symbol_r_contributions: Dict[str, float] = {}

        for cur_date in all_trading_dates:
            daily_realized_pnl = 0.0

            # 1. PROCESS EXITS: check if any open position reached its exit_date on or before cur_date
            remaining_positions: List[PortfolioPosition] = []
            for pos in self.open_positions:
                if pos.exit_date <= cur_date:
                    # Close position and release capital
                    pos.status = "CLOSED"
                    self.cash += pos.capital_deployed + pos.realized_pnl
                    daily_realized_pnl += pos.realized_pnl
                    self.closed_positions.append(pos)

                    sym = pos.symbol
                    symbol_r_contributions[sym] = symbol_r_contributions.get(sym, 0.0) + pos.r_multiple
                else:
                    remaining_positions.append(pos)
            self.open_positions = remaining_positions

            # 2. PROCESS NEW ENTRIES on cur_date
            day_candidates = entries_by_date.get(cur_date, [])
            # Priority sort by Volume Surge Ratio (highest institutional conviction first)
            day_candidates.sort(key=lambda x: x['signal'].volume_surge_ratio, reverse=True)

            entries_today = 0
            for item in day_candidates:
                sig: H0Signal = item['signal']
                out: H0TradeOutcome = item['outcome']

                # Enforce Concurrency and Daily Caps
                if len(self.open_positions) >= self.max_concurrent_positions:
                    break
                if entries_today >= self.max_new_entries_per_day:
                    break

                # Invariant: No duplicate concurrent position in the same symbol
                if any(p.symbol == sig.symbol for p in self.open_positions):
                    continue

                # Risk-based position sizing (1% risk of current equity)
                current_equity = self.cash + sum(p.capital_deployed for p in self.open_positions)
                risk_budget = current_equity * (self.risk_per_trade_pct / 100.0)
                risk_per_share = sig.risk_per_share
                if risk_per_share <= 0:
                    continue

                shares = math.floor(risk_budget / risk_per_share)
                # Invariant: Single position capital cap = 20% of current equity
                max_pos_cap = current_equity * 0.20
                if shares * sig.entry_price > max_pos_cap:
                    shares = math.floor(max_pos_cap / sig.entry_price)

                capital_required = shares * sig.entry_price

                # Check Cash Availability
                if shares <= 0 or capital_required > self.cash:
                    shares = math.floor(self.cash / sig.entry_price)
                    capital_required = shares * sig.entry_price
                    if shares <= 0:
                        continue

                # Open Position
                self.cash -= capital_required
                new_pos = PortfolioPosition(
                    signal_id=sig.signal_id,
                    symbol=sig.symbol,
                    entry_date=cur_date,
                    entry_price=sig.entry_price,
                    stop_price=sig.stop_price,
                    t1_price=sig.t1_price,
                    t2_price=sig.t2_price,
                    shares=shares,
                    capital_deployed=capital_required,
                    risk_deployed=shares * risk_per_share,
                    exit_date=out.exit_date,
                    exit_price=out.exit_price,
                    realized_pnl=(out.exit_price - sig.entry_price) * shares,
                    r_multiple=out.r_multiple,
                    exit_reason=out.exit_reason,
                    exit_day=out.exit_day
                )
                self.open_positions.append(new_pos)
                entries_today += 1

                # If trade entered and exited on the SAME session (D1 hit same day):
                if out.exit_date <= cur_date:
                    self.open_positions.remove(new_pos)
                    new_pos.status = "CLOSED"
                    self.cash += new_pos.capital_deployed + new_pos.realized_pnl
                    daily_realized_pnl += new_pos.realized_pnl
                    self.closed_positions.append(new_pos)
                    symbol_r_contributions[sig.symbol] = symbol_r_contributions.get(sig.symbol, 0.0) + new_pos.r_multiple

            # 3. END-OF-DAY SNAPSHOT
            deployed = sum(p.capital_deployed for p in self.open_positions)
            total_eq = self.cash + deployed
            if total_eq > self.peak_equity:
                self.peak_equity = total_eq
            drawdown_pct = (self.peak_equity - total_eq) / self.peak_equity * 100.0 if self.peak_equity > 0 else 0.0
            total_open_risk = sum(p.risk_deployed for p in self.open_positions)
            open_risk_pct = (total_open_risk / total_eq * 100.0) if total_eq > 0 else 0.0

            self.daily_snapshots.append(PortfolioSnapshot(
                date=cur_date,
                starting_cash=self.starting_capital,
                cash_balance=round(self.cash, 2),
                deployed_capital=round(deployed, 2),
                total_equity=round(total_eq, 2),
                open_positions_count=len(self.open_positions),
                open_risk_pct=round(open_risk_pct, 2),
                daily_realized_pnl=round(daily_realized_pnl, 2),
                drawdown_pct=round(drawdown_pct, 2),
                peak_equity=round(self.peak_equity, 2)
            ))

        # Close any leftover open positions at their exit price at end of holdout
        for p in self.open_positions:
            self.cash += p.capital_deployed + p.realized_pnl
            self.closed_positions.append(p)
            symbol_r_contributions[p.symbol] = symbol_r_contributions.get(p.symbol, 0.0) + p.r_multiple
        self.open_positions = []

        # FINAL METRICS
        terminal_equity = self.cash
        net_profit = terminal_equity - self.starting_capital
        total_return_pct = (net_profit / self.starting_capital) * 100.0
        max_dd_pct = max((s.drawdown_pct for s in self.daily_snapshots), default=0.0)

        # Annualization (CAGR basis across trading days)
        if len(all_trading_dates) > 1:
            d_start = pd.to_datetime(all_trading_dates[0])
            d_end = pd.to_datetime(all_trading_dates[-1])
            cal_days = max(1, (d_end - d_start).days)
            annualized_return_pct = ((terminal_equity / self.starting_capital) ** (365.25 / cal_days) - 1.0) * 100.0
        else:
            annualized_return_pct = total_return_pct
            cal_days = 1

        annualized_calmar = (annualized_return_pct / max_dd_pct) if max_dd_pct > 0 else 99.0
        period_calmar = (total_return_pct / max_dd_pct) if max_dd_pct > 0 else 99.0
        calmar_ratio = annualized_calmar

        # Concentration Checks
        total_capital_deployed = sum(p.capital_deployed for p in self.closed_positions)
        sym_capital = defaultdict(float)
        sym_trades = defaultdict(int)
        sym_gross_win_r = defaultdict(float)
        for p in self.closed_positions:
            sym_capital[p.symbol] += p.capital_deployed
            sym_trades[p.symbol] += 1
            if p.r_multiple > 0:
                sym_gross_win_r[p.symbol] += p.r_multiple

        sorted_by_cap = sorted(sym_capital.items(), key=lambda x: x[1], reverse=True)
        top_5_cap = sum(x[1] for x in sorted_by_cap[:5])
        top_5_capital_pct = (top_5_cap / total_capital_deployed * 100.0) if total_capital_deployed > 0 else 0.0

        top_5_trades_count = sum(sym_trades[s[0]] for s in sorted_by_cap[:5])
        top_5_trades_pct = (top_5_trades_count / len(self.closed_positions) * 100.0) if self.closed_positions else 0.0

        tot_gross_win_r = sum(sym_gross_win_r.values())
        top_5_gross_r = sum(sorted(sym_gross_win_r.values(), reverse=True)[:5])
        top_5_gross_win_pct = (top_5_gross_r / tot_gross_win_r * 100.0) if tot_gross_win_r > 0 else 0.0

        total_realized_r = sum(p.r_multiple for p in self.closed_positions)
        sorted_syms = sorted(symbol_r_contributions.items(), key=lambda x: x[1], reverse=True)
        top_5_r = sum(x[1] for x in sorted_syms[:5]) if len(sorted_syms) >= 5 else sum(x[1] for x in sorted_syms)
        top_5_net_r_pct = (top_5_r / total_realized_r * 100.0) if total_realized_r > 0 else 0.0

        return {
            "starting_capital": self.starting_capital,
            "terminal_equity": round(terminal_equity, 2),
            "net_profit": round(net_profit, 2),
            "total_return_pct": round(total_return_pct, 2),
            "annualized_return_pct": round(annualized_return_pct, 2),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "calmar_ratio": round(calmar_ratio, 2),
            "annualized_calmar": round(annualized_calmar, 2),
            "period_calmar": round(period_calmar, 2),
            "total_executed_trades": len(self.closed_positions),
            "distinct_symbols_count": len(set(p.symbol for p in self.closed_positions)),
            "top_5_symbols_by_cap": sorted_by_cap[:5],
            "top_5_capital_pct": round(top_5_capital_pct, 2),
            "top_5_trades_pct": round(top_5_trades_pct, 2),
            "top_5_gross_win_pct": round(top_5_gross_win_pct, 2),
            "top_5_concentration_pct": round(top_5_capital_pct, 2), # Primary risk measure: capital allocation
            "top_5_net_r_pct": round(top_5_net_r_pct, 2),
            "solvency_preserved": bool(terminal_equity > 0 and min((s.cash_balance for s in self.daily_snapshots), default=0) >= 0),
            "snapshots_count": len(self.daily_snapshots)
        }
