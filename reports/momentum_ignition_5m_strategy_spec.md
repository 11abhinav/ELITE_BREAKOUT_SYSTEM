# MOMENTUM_IGNITION_5M — CANDIDATE STRATEGY SPECIFICATION

**Strategy Name:** `MOMENTUM_IGNITION_5M`  
**Version:** V1.0  
**Current Governance Status:** `RESEARCH ONLY`  
**Decommissioned Legacy:** `~~SHORT_COVERING_5M~~`  

---

## 1. Strategy Identity & Core Hypothesis
A liquid F&O stock trading above its intraday VWAP produces an executable continuation impulse when:
1. Local price resistance breaks (20-bar rolling local high).
2. Participation expands significantly (RVOL20 $\ge 2.0\text{x}$).
3. Entry is not excessively extended from institutional fair value (Distance from VWAP $\le 1.20\%$).

**Explicit Exclusions**:
* No mandatory Open Interest contraction.
* No RSI, MACD, or multi-indicator score threshold.
* No short-position accumulation or short-buildup assumptions.

---

## 2. Trading Instrument Alignment (Cash vs Futures)
* **Code Audit Result**: The production execution path (`app/short_covering/oi_data_service.py:333-356`) retrieves near-month FUTSTK contracts (`NSE_FO|...`) from Upstox V3.
* **Specification Requirement**: 
  - If executed in **NSE Stock Futures**: Signal and fill must both use the active near-month FUTSTK contract.
  - If executed in **NSE Cash Equity**: Signal must use `NSE_EQ` 5M OHLCV candles, and execution must occur on cash equity.
  - Zero cross-segment mixing permitted without explicit spread adjustment.

---

## 3. Strict Point-in-Time Causality & Executable Fill
* **Signal Bar**: Evaluated on completed 5-minute bar (e.g., 11:55:00 bar, ending 11:59:59.999).
* **Earliest Fill**: Next Bar Open (**T+1 Open at 12:05:00**) + **5 bps adverse slippage**.
* **Zero Bar-Close Fill**: Same-candle close fills are strictly prohibited as unfillable lookahead.

---

## 4. Frozen Parameter Specification
| Parameter | Certified Specification | Purpose |
| :--- | :--- | :--- |
| **Structural Lookback** | 20 bars (100 minutes) | Break of local consolidation high |
| **Volume Ignition (RVOL)** | $\ge 2.0\text{x}$ vs 20-bar SMA | Confirms institutional participation surge |
| **VWAP Proximity Gate** | $0.0\% \le \text{Dist} \le 1.20\%$ | Prevents buying extended, exhausted thrusts |
| **Candle Structure** | CLV $\ge 0.65$, Upper Wick $\le 30\%$ | Ensures strong close near bar high |
| **Initial Stop Loss** | $1.0 \times \text{ATR}_{14}$ below Entry | Volatility-normalized structural risk |
| **Stage 1 Target** | $+1.50\text{R}$ (50% position) | Targets empirical MFE peak zone |
| **Stage 2 Exit / Trail** | 60-Minute Time Stop / 1.0R Trail | Exits non-igniting stagnation |
| **Position Limit** | Exactly 1 active position per symbol | Prevents correlated cluster pyramiding |
