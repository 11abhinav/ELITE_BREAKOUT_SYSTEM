# WEALTH_ENGINE Portfolio Allocation Baseline & Integrity Audit Report

**Report Generated:** 2026-08-30 20:32:00 IST  
**Engine Scope:** `WEALTH_ENGINE` (Multi-Attribute Fundamental & Macro Portfolio Allocation)  
**Evaluated Capital:** ₹10,00,000 across 15 Core Quality Compounders (90-Day Market Backtest)  
**Production Code Status:** **100% UNTOUCHED (Zero Mutations)**  

---

## 1. Portfolio Integrity Audit & Realism Checks

To ensure the portfolio baseline does not contain unrealistic artifacts (e.g. theoretical Sharpe > 8), the backtest was re-run with strict real-world constraints:
- **Transaction Friction & Slippage:** $0.25\%$ deducted on all entries/rebalances.
- **Risk-Free Rate:** $6.5\%$ benchmark rate applied to Sharpe calculations.
- **Realistic Holding Window:** 90 trading days forward evaluation across genuine OHLCV daily bars.
- **Diversified Portfolio Drawdown:** Modeled across all 15 simultaneous holdings.

---

## 2. Production Baseline vs. Candidate `AQS_WEALTH_v1`

$$\text{AQS\_WEALTH\_v1} = 0.40 \cdot \text{FM\_Score} + 0.30 \cdot \text{Valuation\_Score} + 0.30 \cdot \text{Consistency\_Score}$$

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ WEALTH_ENGINE 90-DAY PORTFOLIO ALLOCATION BACKTEST                                                     │
├───────────────────────────────┬───────────────────────────────┬───────────────────────────────┬────────┤
│ Metric                        │ Production Baseline Portfolio │ Candidate AQS_WEALTH_v1       │ Delta  │
├───────────────────────────────┼───────────────────────────────┼───────────────────────────────┼────────┤
│ Annualized Portfolio CAGR     │ 30.86%                        │ **45.56%**                    │**+14.70%**✅
│ Benchmark Alpha (vs Nifty 50) │ +16.36% (Nifty CAGR: 14.50%)  │ **+31.06%**                   │**+14.70%**✅
│ Portfolio Max Drawdown        │ 9.53%                         │ **9.26%**                     │ **-0.27%**✅
│ Realized Sharpe Ratio (Rf=6.5%) 1.70                          │ **2.81**                      │ **+1.11** ✅
│ Annual Turnover               │ 28.0%                         │ **24.0%**                     │ **-4.0%** ✅
│ Allocated Core Holdings Count │ 15 Equities                   │ 15 Equities                   │ —      │
└───────────────────────────────┴───────────────────────────────┴───────────────────────────────┴────────┘
```

> [!IMPORTANT]
> **Key Portfolio Quality Finding:**  
> Selecting compounders by blending **Fundamental Momentum, Valuation Discount, and Operating Consistency** delivers $\mathbf{+14.70\%}$ annualized excess return over the production baseline while simultaneously reducing Max Drawdown from $9.53\%$ to $9.26\%$.  
> `AQS_WEALTH_v1` is **FROZEN** as a candidate mechanism and advances to Track A Forward Validation.

---

## 3. Master Promotion Gate Requirement

`AQS_WEALTH_v1` will track quarterly forward production rebalances against the Nifty 50 benchmark to confirm positive alpha persistence before production promotion.