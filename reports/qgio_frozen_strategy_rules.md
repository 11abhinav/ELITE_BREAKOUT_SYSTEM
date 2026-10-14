# QGIO-1 — Formal Mathematical & Operational Strategy Specification

**Candidate ID**: `QGIO_1`  
**Strategy Name**: Quality Growth + Verified Ownership Accumulation  
**Specification Status**: **FROZEN (ZERO MID-RESEARCH RETUNING)**  
**Version**: `1.0.0`

---

## 1. Mathematical Architecture & Three-Gate Filter Cascade

```
ELIGIBLE NSE UNIVERSE (Non-Financials, MCap >= ₹1,000 Cr, Median Turnover >= ₹5 Cr)
                            │
                            ▼
     [GATE 1: FUNDAMENTAL QUALITY GROWTH CASCADE]
     • 5 Consecutive Fiscal Years Consolidated PAT > 0
     • 3-Year Annual Revenue Monotonically Increasing & Sales CAGR >= 10%
     • Latest ROCE >= 15% AND 3-Year Average ROCE >= 15%
     • 3-Year Cumulative CFO / Cumulative PAT >= 0.8
     • Net Debt / EBITDA <= 1.0 (or Net Cash)
                            │
                            ▼
     [GATE 2: DISCLOSED OWNERSHIP ACCUMULATION CASCADE]
     • 90-Day Disclosed On-Market Net Buying: Shares Bought - Shares Sold > 0
     • Net Buy Value >= 0.05% of Free-Float Market Cap
     • Deduct all Disclosed Sells; Exclude Gifts, Inter-Se, Restructuring
     • Requires Verified Investor Category (Promoter, FII, DII)
                            │
                            ▼
     [GATE 3: LONG-TERM PRICE-TREND CONFIRMATION]
     • Close_t > SMA_50(t)
     • Close_t > SMA_200(t)
     • SMA_50(t) > SMA_200(t) (Golden Cross Alignment)
     • SMA_200(t) > SMA_200(t - 20) (Rising 200 SMA Slope)
                            │
                            ▼
     [PORTFOLIO ALLOCATION & SYSTEMATIC EXECUTION]
     • Max 20 Holdings, Equal 5% Target Weight, Max 25% Sector Cap
     • Rank by Composite Quality Percentile (ROCE, Sales, Margin, Cash, Lev)
     • Next-Day Market Open Fill
                            │
                            ▼
     [SYSTEMATIC TREND-FAILURE EXIT]
     • 3 Consecutive Closes Below 200 SMA OR 50/200 SMA Death Cross
     • Fundamental Failure (Annual Loss, Solvency Concern)
     • Exit Executed on Next Session Open
```

---

## 2. Gate 1: Fundamental Quality Growth Definitions

Let $t$ be the decision date. Let $Y_t$ be the latest completed financial year publicly available as of $t$ (strictly applying a $+75$-day publication lag from fiscal year end).

1. **5-Year Profitability**:
   $$\forall k \in \{0, 1, 2, 3, 4\}: \quad \text{PAT}_{Y_t - k} > 0$$

2. **Revenue Compounding**:
   $$\text{Revenue}_{Y_t} > \text{Revenue}_{Y_t - 1} > \text{Revenue}_{Y_t - 2} > \text{Revenue}_{Y_t - 3}$$
   $$\text{Sales CAGR}_{3Y} = \left(\frac{\text{Revenue}_{Y_t}}{\text{Revenue}_{Y_t - 3}}\right)^{1/3} - 1 \ge 0.10$$

3. **Capital Efficiency (ROCE)**:
   $$\text{ROCE}_{Y_t} \ge 0.15 \quad \text{AND} \quad \frac{1}{3}\sum_{k=0}^2 \text{ROCE}_{Y_t - k} \ge 0.15$$

4. **Cash Flow Conversion**:
   $$\frac{\sum_{k=0}^2 \text{CFO}_{Y_t - k}}{\sum_{k=0}^2 \text{PAT}_{Y_t - k}} \ge 0.80$$

5. **Balance Sheet Safety**:
   $$\frac{\text{Total Debt}_{Y_t} - \text{Cash}_{Y_t}}{\text{EBITDA}_{Y_t}} \le 1.00$$
   *(If Total Debt $\le$ Cash, the company is net cash and automatically satisfies this gate).*

---

## 3. Gate 2: Verified Ownership Accumulation Definitions

For each qualifying investor entity $i \in \{\text{Promoter}, \text{FII}, \text{DII}\}$ in symbol $s$ over window $[t - 90\text{d}, t]$:
$$\text{Net Buy Shares}_{s} = \sum \text{Buy Shares}_{\text{on-market}} - \sum \text{Sell Shares}_{\text{on-market}}$$
$$\text{Net Buy Value}_{s} = \sum \text{Buy Value}_{\text{on-market}} - \sum \text{Sell Value}_{\text{on-market}}$$

Requirement:
$$\text{Net Buy Shares}_{s} > 0 \quad \text{AND} \quad \frac{\text{Net Buy Value}_{s}}{\text{Free Float MCap}_{s, t}} \ge 0.0005$$

**Exclusions**: Any trade marked as inter-se transfer, gift, inheritance, rights/bonus allotment, pledge invocation, or court restructuring is strictly excluded from `Buy Shares`. If disclosure records are absent or cannot be verified, the stock is marked `NO_CONFIRMED_DISCLOSED_NET_BUY`.

---

## 4. Gate 3: Technical Trend Confirmation

Using daily closing prices:
$$\text{Close}_t > \text{SMA}_{50}(t) > \text{SMA}_{200}(t)$$
$$\text{SMA}_{200}(t) - \text{SMA}_{200}(t - 20) > 0$$

---

## 5. Portfolio Construction & Execution Rules

1. **Capacity & Slots**: Maximum 20 simultaneous positions.
2. **Position Sizing**: Target 5.0% allocation per slot based on total portfolio equity.
3. **Sector Cap**: Maximum 25.0% exposure to any individual industry sector.
4. **Execution Protocol**: Signals generated on day $t$ post-close; filled on day $t+1$ at Open price.
5. **Cash Drag**: Any unallocated slots remain in cash yielding 0.0% interest.
6. **Tie-Breaking Rank**: Composite Quality Percentile:
   $$\text{Rank Score} = \frac{1}{5}\left(P_{\text{ROCE}} + P_{\text{Sales CAGR}} + P_{\text{Margin}} + P_{\text{Cash Conv}} + P_{\text{Inv Leverage}}\right)$$

---

## 6. Systematic Exit Rule

An existing position is liquidated at next day's open when:
1. $\text{Close}_\tau < \text{SMA}_{200}(\tau)$ for 3 consecutive trading sessions ($\tau \in \{t-2, t-1, t\}$), OR
2. $\text{SMA}_{50}(t) < \text{SMA}_{200}(t)$ (Death Cross).
