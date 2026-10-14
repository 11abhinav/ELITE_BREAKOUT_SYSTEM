# RSMR-1 Prospective Paper-Trading Runbook (Phase K)

**Strategy**: RSMR-1 (Relative-Strength Momentum Rotation)  
**Governance State**: `GO_TO_PAPER_TRADING`  
**Duration**: Minimum 6 Calendar Months & 2 Completed Quarterly Rebalance Events  
**Namespace**: `research/strategies/rsmr_1/`  

---

## 1. Operating Rules for Paper-Trading Execution

1. **Strict Research Isolation**:
   - The paper-trading daemon must run in an isolated scheduled runner.
   - It is strictly forbidden from placing real broker orders to Upstox, Zerodha, Fyers, Dhan, or any API endpoint.
   - It must never alter production database tables (`buy_alerts_journal`) or trigger alerts on admin/user dashboards.
2. **Rebalance Workflow**:
   - **Signal Generation**: At 15:35 IST on the final trading day of the calendar quarter (e.g., 31 March, 30 June, 30 September, 31 December), run the ranking script using official market closing prices.
   - **Candidate Selection**: Rank top 30 stocks with $Close > SMA_{200}$, $M_{6m} > 0$, median turnover $\ge ₹50\text{L}$, applying the 25% sector ceiling ($\le 7$ stocks per sector).
   - **Order Journaling**: Record simulated orders (target shares, modeled prices, expected statutory taxes) to `research/strategies/rsmr_1/data/paper_orders.json`.
   - **Simulated Execution**: At 09:16 IST on the next trading session, capture the official NSE opening print. Record the observed execution price, modeled slippage (5 bps), and statutory charges.
3. **Daily Mark-to-Market Monitoring**:
   - Record daily closing NAV, cash balance, sector weights, and individual stock weights.
   - Track tracking error against Nifty 500 TRI and Nifty 200 Momentum 30 ETF.
   - Log any corporate actions (dividends, splits, bonuses) and adjust position shares and cash accurately.

---

## 2. Invalidation & Circuit-Breaker Criteria
During the 6-month paper-trading window, the strategy shall be placed on `RESEARCH_HOLD` if any of the following occur:
1. **Excess Drawdown Discrepancy**: Observed paper-trading drawdown exceeds the modeled 10-year historical maximum drawdown (-31.32%) by more than 5.0% (-36.32%).
2. **Execution Slippage Divergence**: Actual observed market spread/slippage on next-session opens persistently exceeds 25 bps.
3. **Data Availability Failure**: Upstox 1D historical candle feed suffers an outage exceeding 48 hours without recovery.
