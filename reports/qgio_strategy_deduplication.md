# QGIO-1 — Strategy Deduplication & Lineage Audit

**Candidate ID**: `QGIO_1`  
**Strategy Name**: Quality Growth + Verified Ownership Accumulation  
**Audit Purpose**: Verify that QGIO-1 represents a genuinely distinct, unstudied investment hypothesis rather than a duplicate or rebranded version of previously rejected research.

---

## 1. Strategy Deduplication Matrix

| Strategy Dimension | QGIO-1 (Proposed Candidate) | QPF-1 (Quality & Payout) | Quality Compounder Value V2 | Bear Quality Recovery V1 | Fundamental Gem Recovery V1-V5 | RSMR-1 (Momentum) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Core Source of Edge** | **Quality Growth + Disclosed Net Accumulation + Trend** | Cross-sectional Quality + Shareholder Payout | Quality + Deep Valuation Re-rating | Severe Market Correction Rebound | Turnaround & Deep Value Screen | Price Trend Persistence (12M Momentum) |
| **Market Regime Focus**| **All Regimes (Trend-Confirmed)** | All Regimes (Semi-annual passive) | Bull & Sideways | Bear Market Only (Crash Recovery) | Bear / Sideways (Correction) | Bull Market (Trend Following) |
| **Valuation Filter** | **None** (Growth at quality price) | None (Factor Z-score) | $\ge 25\%$ EV/EBITDA discount vs 3Y median | Price down $\ge 20\%$ from 52W high | Low PEG ($\le 1.5$), P/E discount | None (Pure price relative strength) |
| **Shareholder Payout** | **None** (Excludes dividend yield) | Mandatory (20% score weight on yield & payout) | Incidental | None | None | None |
| **Ownership Accumulation**| **Mandatory Gate 2** (Disclosed on-market buying $\ge 0.05\%$ float) | None | None | None | None | None |
| **Trend Confirmation** | **Mandatory Gate 3** (Close > 50 SMA > 200 SMA, 200 SMA rising) | None (Rebalances regardless of trend) | Open-ended trend hold | None (Oversold reversal) | None (Oversold value) | Top decile 12-month return ranking |
| **Exit Mechanism** | **3 closes below 200 SMA** OR 50/200 SMA Death Cross | Semi-annual rank rebalance | Close below 50 EMA / Fundamental fail | Fixed holding period / profit target | Profit target / stop loss | Quarterly/semi-annual momentum rebalance |
| **Sector Restrictions** | Non-financials only (Excludes BFSI) | Broad (Financials handled via separate rules) | Broad (Includes Banks/NBFCs) | Broad | Manufacturing & Cyclicals | Top 500 Equities |
| **Portfolio Architecture**| Max 20 positions, 5% target weight | 50 positions, 2% target weight | 15–20 positions | 5–10 positions | 10 positions | 20–30 positions |

---

## 2. Key Architectural Differences

### 1. Independence from Valuation Multiples
Unlike `QUALITY_COMPOUNDER_VALUE_V2` and `Bear Quality Recovery`, which strictly required stocks to trade at a $\ge 25\%$ discount to their 3-year median EV/EBITDA, QGIO-1 does not impose a valuation discount screen. It recognizes that elite high-ROE growth compounders rarely trade at deep valuation discounts during bull markets.

### 2. Elimination of the "Payout Factor Drag"
In the preceding `QPF-1` backtest, rewarding high dividend yields introduced a structural drag of **-5.36% Net CAGR** vs Nifty 500 TRI because high dividend yields in India are concentrated in mature PSUs and low-reinvestment legacy businesses. QGIO-1 completely removes shareholder payout from its scoring model, focusing exclusively on **reinvestment compounding** (Sales CAGR $\ge 10\%$, ROCE $\ge 15\%$, CFO/PAT $\ge 0.8$).

### 3. Novelty of the Institutional Ownership Gate
None of the previously tested strategies (`RSMR-1`, `ESD-1`, `QPF-1`, `BMRC`, or `Quality Compounder`) required verified transaction-level institutional or promoter net buying. QGIO-1 is the first strategy in the repository to hypothesize that **insider/institutional transaction-level accumulation acts as an alpha catalyst** for quality growth stocks.

### 4. Moving Average Trend Alignment (Gate 3)
Unlike passive factor portfolios that rebalance on fixed calendar dates regardless of market direction (such as QPF-1), QGIO-1 enforces a trend gate (Close > 50 SMA > 200 SMA, 200 SMA rising). This prevents buying fundamentally sound companies that are in severe structural downtrends.

---

## 3. Deduplication Verdict

**`GENUINELY DISTINCT HYPOTHESIS`**

QGIO-1 does not rehash any prior strategy. It introduces:
1. Pure growth-at-reasonable-price financial screening without valuation discounts or dividend payout penalties.
2. An institutional/promoter transaction accumulation gate.
3. A systematic 50/200 SMA trend alignment filter and 200 SMA invalidation exit.

The strategy qualifies as an original research proposal under repository governance.
