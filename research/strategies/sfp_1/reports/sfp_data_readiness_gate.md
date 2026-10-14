# SFP-1 — FORMAL DATA READINESS GATE REPORT

**Strategy Identifier**: `SFP-1` (Size Premium with Financial Survival Filters)  
**Research Namespace**: `research/strategies/sfp_1/`  
**Evaluation Date**: 2026-10-13  

---

## 1. Formal Data Readiness Gate Evaluation

Before executing the backtest or freezing production parameters, every data dependency is audited against repository operating rules and point-in-time constraints:

| Check Item | Target Requirement | Repository Finding | Compliance Verdict |
| :--- | :--- | :--- | :---: |
| **1. Upstox Historical Prices** | Continuous 1D OHLCV (2016–2026) with zero lookahead. | 947 verified symbols in `data/history/1d/*.parquet` with 931 SHA-256 API manifests. | **PASS** |
| **2. Historical Shares Outstanding** | Point-in-time shares outstanding for market cap ranking. | Present for 858 companies in `pit_fundamentals_v1.parquet`, dated $\ge 90$d after FY close. | **PASS (Forensic Ref)** |
| **3. Financial Survival Metrics** | Positive TTM PAT and positive CFO in latest completed FY. | Available in audited Screener filings table `pit_fundamentals_v1.parquet`. | **PASS (Forensic Ref)** |
| **4. Liquidity Screen (₹5 Cr Median)** | Trailing 60-day median daily turnover $\ge ₹5\text{ Cr}$. | Deterministically calculable from Upstox native daily Volume and Close. | **PASS** |
| **5. Survivorship-Free Smallcap Universe** | Historical records for companies that failed or delisted. | **0 out of 12** distressed/delisted smallcap candidates exist in repo. | **FAIL** |
| **6. Official Benchmark Coverage** | Dual benchmarks: Nifty 500 TRI and Nifty Smallcap 250 TRI. | Nifty 500 TRI is present. **Nifty Smallcap 250 TRI is absent** from repository cache. | **FAIL** |

---

## 2. Gate Determination & Root Causes

### OVERALL DATA READINESS GATE VERDICT:
```text
================================================================================
DATA READINESS STATUS: RESEARCH_HOLD (DATA_INSUFFICIENT_FOR_CERTIFICATION)
================================================================================
```

### Explanatory Root Causes:

1. **Surviving-Constituents Distortion (+4.94% CAGR Bias)**:
   - In accordance with the user's warning and academic research (2025–2026 working paper on the Nifty Smallcap 250), running a historical small-cap backtest solely on surviving companies inflates annual returns by approximately **4.94 percentage points**.
   - Because the repository does not contain price records for bankrupt or delisted smallcaps (such as DHFL, RCOM, Sintex, Cox & Kings, Kwality, Rolta, Unitech), the simulation cannot observe the full extent of small-cap distress.
   - Under `AGENTS.md` (Mandatory Real-Market-Data Backtest Protocol, Section 7 & Section 9), the system must not certify a strategy where survivorship bias remains uncorrected.

2. **Absence of Dedicated Nifty Smallcap 250 TRI Parquet**:
   - The strategy specification requires dual benchmarks: Nifty 500 TRI and Nifty Smallcap 250 TRI.
   - While `NIFTY_500.parquet` is present in `research/strategies/rsmr_1/data/`, no official `NIFTY_SMALLCAP_250.parquet` exists in the local data lake.
   - Evaluating small-cap alpha purely against Nifty 500 conflates the general equity market return with the specific small-cap segment beta.

---

## 3. Path Forward: Exploratory Feasibility Protocol

While **formal certification for alert generation is placed on RESEARCH_HOLD**, the research protocol permits conducting an **exploratory feasibility study** to evaluate:
1. Does ranking by smaller market capitalization with financial survival filters (PAT > 0, CFO > 0) outperform a random or market-cap weighted basket on the available universe?
2. Does the strategy survive a **mandatory 4.94% survivorship bias haircut**?
   $$\text{Survivorship-Adjusted Net CAGR} = \text{Measured CAGR} - 4.94\%$$
3. What is the impact of realistic small-cap transaction friction and 10 bps / 20 bps adverse execution slippage on ₹5 Cr turnover small-caps?

This ensures that the economic mechanics of the hypothesis can be rigorously analyzed and documented without making unwarranted claims of production readiness.
