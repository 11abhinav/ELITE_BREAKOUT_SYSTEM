# RSMR-1 Investment Theory & Frozen Strategy Rules

**Strategy Identifier**: `RSMR_1`  
**Full Name**: Relative-Strength Momentum Rotation  
**Specification File**: [research/strategies/rsmr_1/config/rsmr_1_frozen_rules.yaml](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/research/strategies/rsmr_1/config/rsmr_1_frozen_rules.yaml)  

---

## 1. Investment Theory & Core Hypotheses

### Hypothesis A — Momentum Persistence
Stocks exhibiting stronger adjusted total returns over the trailing six-month period (approximately 126 trading days) continue to outperform broad-market indices over subsequent holding horizons due to investor underreaction and institutional capital re-allocation.

### Hypothesis B — Trend Confirmation (200-SMA Filter)
Requiring candidate equities to trade strictly above their 200-day simple moving average (`Close > SMA_200`) eliminates persistent macro downtrends, value traps, and structural deterioration.

### Hypothesis C — Diversified Systematic Leadership
Constructing a portfolio of up to 30 high-relative-strength leaders with strict sector caps (maximum 25% per sector / 7 stocks per sector) and stock caps (maximum 5% per stock) captures upside momentum while curtailing idiosyncratic failure risk.

---

## 2. Frozen Operational Rules

| Rule | Specification |
| :--- | :--- |
| **Universe** | Point-in-time eligible NSE ordinary equities with $\ge 200$ historical bars |
| **Momentum Signal** | 6-month adjusted total return: $M_{6m,t} = \frac{Close_t}{Close_{t-126}} - 1$ |
| **Trend Filter** | $Close_t > SMA_{200,t}$ using prior sessions only |
| **Liquidity Filter** | Preceding 60-day median daily turnover $\ge ₹50\text{ Lakh}$; Max 1% participation rate |
| **Selection Count** | Top 30 qualifying equities ranked descending by 6-month momentum |
| **Tie-Breaker** | Higher 60-day median daily turnover |
| **Sector Allocation Limit** | Maximum 25% per sector ($\le 7$ stocks per sector for a 30-slot target) |
| **Individual Stock Limit** | Target weight $\frac{1}{30}$ (3.333%); Hard ceiling 5.0% |
| **Unfilled Slots Policy** | Retained strictly as CASH. Zero redistribution to active holdings |
| **Rebalance Cadence** | Quarterly (last valid trading session of calendar quarter close) |
| **Execution Timing** | Next eligible trading session OPEN price |
| **Exit Policy** | Quarterly rotation exit only. Zero intraday discretionary stop-losses or profit targets |
| **Slippage & Costs** | Base 5 bps slippage + full Indian statutory fees (STT, GST, Exchange, SEBI, Stamp, DP) |

---

## 3. Strict Point-in-Time Causality
- **Signal Date**: Rebalance calculations occur strictly using data up to the close of session $t$.
- **Execution Date**: Trades execute strictly at the opening price of session $t+1$.
- **Zero Lookahead**: Future bars, future index constituent lists, and future financial restatements are strictly inaccessible to the ranking engine.
