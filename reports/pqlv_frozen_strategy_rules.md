# PQLV-1 — Formal Mathematical & Operational Strategy Specification

**Candidate ID**: `PQLV_1`  
**Strategy Name**: Profitable Quality + Low Volatility Leaders  
**Specification Status**: **FROZEN (ZERO MID-RESEARCH RETUNING)**  
**Version**: `1.0.0`

---

## 1. Mathematical Architecture & Selection Workflow

```
ELIGIBLE NSE UNIVERSE (Non-Financials, MCap >= ₹1,000 Cr, 60d Turnover >= ₹5 Cr)
                             │
                             ▼
     [GATE 1: HARD FUNDAMENTAL QUALITY FILTERS]
     • 5 Consecutive Fiscal Years Consolidated PAT > 0
     • Positive 3-Year Revenue CAGR (Revenue_Y / Revenue_Y-3 - 1 > 0)
     • Operating Margin >= Industry Group Median (Contemporaneous)
     • Latest ROCE >= 15% AND 3-Year Average ROCE >= 15%
     • 3-Year Cumulative CFO / Cumulative PAT >= 0.8
                             │
                             ▼
     [GATE 2: TRAILING REALIZED VOLATILITY ESTIMATION]
     • Trailing 252 Valid Trading Sessions Log Returns
     • Volatility = Standard Deviation of Log Returns * sqrt(252)
     • Rank Eligible Quality Stocks in Ascending Order of Volatility
                             │
                             ▼
     [PORTFOLIO ALLOCATION & SECTOR CAP ENFORCEMENT]
     • Select Up to 30 Least Volatile Stocks
     • Fixed Target Weight: 1/30 (3.333% of NAV) per Slot
     • Sector Cap: Maximum 25% of Portfolio NAV per Sector
     • Any Candidates Exceeding Sector Cap are Skipped
     • Unallocated Slots Remain in Cash (Yielding 0%)
                             │
                             ▼
     [SEMIANNUAL REBALANCING & CASUAL EXECUTION]
     • Rebalance on Last Trading Session of June and December
     • Signal Evaluated at Market Close; Execution at Next Open
     • No Moving-Average Exits
     • Fundamental Exit: Immediate Liquidation on Reported Annual Loss
```

---

## 2. Mathematical Formulations

### A. Quality Filter Definitions
Let $t$ be the semiannual rebalance date. Let $Y_t$ be the latest completed financial year publicly available as of $t$ (+75-day publication lag):

1. **5-Year Profitability**:
   $$\forall k \in \{0, 1, 2, 3, 4\}: \quad \text{PAT}_{Y_t - k} > 0$$

2. **3-Year Revenue Growth**:
   $$\text{Revenue CAGR}_{3Y} = \left(\frac{\text{Revenue}_{Y_t}}{\text{Revenue}_{Y_t - 3}}\right)^{1/3} - 1 > 0$$

3. **Industry-Relative Margin**:
   $$\text{Operating Margin}_{i, Y_t} \ge \text{Median}\left(\text{Operating Margin}_{j, Y_t}\right)_{j \in \text{Industry}(i)}$$

4. **Capital Efficiency (ROCE)**:
   $$\text{ROCE}_{i, Y_t} \ge 0.15 \quad \text{AND} \quad \frac{1}{3}\sum_{k=0}^2 \text{ROCE}_{i, Y_t - k} \ge 0.15$$

5. **Cash Conversion**:
   $$\frac{\sum_{k=0}^2 \text{CFO}_{i, Y_t - k}}{\sum_{k=0}^2 \text{PAT}_{i, Y_t - k}} \ge 0.80$$

---

### B. Realised Volatility Estimation
For each eligible equity $i$ across trading sessions $\tau \in \{t - 251, \dots, t\}$:
$$R_{i, \tau} = \ln\left(\frac{P_{i, \tau}}{P_{i, \tau - 1}}\right)$$
$$\bar{R}_i = \frac{1}{252}\sum_{\tau=t-251}^t R_{i, \tau}$$
$$\sigma_{i, 252}(t) = \sqrt{\frac{1}{251}\sum_{\tau=t-251}^t \left(R_{i, \tau} - \bar{R}_i\right)^2} \times \sqrt{252}$$

All passing stocks are sorted such that:
$$\sigma_{(1), 252} \le \sigma_{(2), 252} \le \dots \le \sigma_{(N), 252}$$

---

### C. Portfolio Construction & Constraints
1. **Target Slots**: $N_{\text{target}} = 30$.
2. **Target Position Size**: $W_i = \frac{1}{30} \approx 3.333\%$ of portfolio equity.
3. **Sector Constraint**: $\sum_{i \in \text{Sector } S} W_i \le 25.0\%$.
4. **Execution Protocol**: Signals evaluated on session $t$ at close; filled on session $t+1$ at Open price.
5. **Cash Drag**: If fewer than 30 stocks qualify or if sector caps prevent filling all 30 slots, unallocated funds remain as cash earning 0%.
6. **Tie-Breaker**: Higher 60-day median turnover breaks any exact volatility tie.

---

### D. Exit Rules
1. **Scheduled Semiannual Rebalance**: Existing holdings not ranking in the top 30 least volatile passing stocks at the next reconstitution are sold at the next open.
2. **Fundamental Failure Exit**: If a newly published annual statement reports a negative consolidated PAT, the position is immediately liquidated at the next session open.
3. **Zero Moving-Average Exits**: Unlike prior failed momentum/trend strategies, PQLV-1 does not use 50 SMA or 200 SMA stops.
