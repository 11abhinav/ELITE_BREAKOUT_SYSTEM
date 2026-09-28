# FUNDAMENTAL_GEM_RECOVERY_V5 — STUDY CLOSURE & FINAL VERDICT

> **FINAL STUDY VERDICT**: **`INCONCLUSIVE_BREADTH`**  
> **BACKTEST EXECUTION STATUS**: **CANCELLED / CLOSED** (Zero return/P&L calculations executed)  
> **FORWARD TRACKER STATUS**: **ACTIVE (Phase E Live Forward Watchlist)**  

---

### 1. Executive Summary & Final Verdict

The backtest study for `FUNDAMENTAL_GEM_RECOVERY_V5` is formally **CLOSED** with the pre-registered verdict of **`INCONCLUSIVE_BREADTH`**. 

Under the strict $L1 + L2 + L3_{\text{EV/EBITDA} \ge 25\%}$ rule (requiring stocks to trade $\ge 25\%$ below their own 3-year point-in-time median EV/EBITDA), the candidate universe failed two of the pre-registered Phase B breadth floor criteria:
1. **Frequency Floor**: **51.7%** of months achieved $\ge 8$ candidates (Required: $\ge 60.0\%$).
2. **Independent Episode Floor**: **1 episode** identified across 2020–2024 (Required: $\ge 3$ independent episodes separated by $\ge 60$ trading days).

Per system governance rules, a breadth floor failure triggers an immediate `INCONCLUSIVE_BREADTH` verdict with **zero further parameter loosening**. The backtest run was cancelled before calculating any strategy returns to preserve full statistical and procedural integrity.

---

### 2. Audit Log of Protocol Deviations & Amendments

#### **Protocol Deviation Log**
- **Drafting Blueprint Before Floor Pass**: `BLUEPRINT_V5.md` was drafted and hashed prior to confirming Phase B breadth floor compliance. This was contrary to protocol (which requires blueprint freeze only upon passing the breadth gate). `BLUEPRINT_V5.md` has been marked as **SUPERSEDED AND UNEXECUTED**.

#### **Amendments Log**
1. **Layer 3 Reversion to EV/EBITDA Gate**: Reverted Layer 3 from an `OR` combination (`EV/EBITDA ≥ 25% OR P/E ≥ 20%`) back to a strict single-metric gate: $\text{EV/EBITDA} \le 0.75 \times \text{Own 3Y Median}$. $P/E_{\text{norm}}$ discount was converted to a scored feature.
2. **$Res\_DD$ Benchmark Simplification**: Layer 4 residual drawdown ($Res\_DD$) is measured as drawdown relative to the broad market benchmark (**Nifty 500 TRI**), rather than using per-sector beta calculations ($\beta \times DD_{\text{sector}}$).
3. **5-Year Lookback Alignment for Scoring**: Scoring metrics use a 5-year average ROCE (rather than 10-year), aligning with the point-in-time data coverage window starting in 2015.
4. **Calendar Year Candidate Breakdown**: The gap-based episode metric is superseded by reporting candidate-month concentration by calendar year to accurately capture market regime dependency.

---

### 3. Recomputed Candidate Counts & Sensitivity Tables

#### **Primary Arm Candidate Counts ($L1 + L2 + L3_{\text{EV/EBITDA}}$)**
* **Evaluation Period**: 2020-01-01 to 2024-12-01 (60 Monthly Rebalance Dates)
* **Monthly Candidate Range**: Min = **1**, Median = **8.0**, Max = **40**
* **Distinct Qualified Symbols**: **105 symbols** (Required: $\ge 40$)
* **Months with $\ge 8$ Candidates**: **31 / 60 months (51.7%)** (Required: $\ge 60.0\%$)

#### **Leave-One-Out (LOO) Layer Sensitivity Table**
| Filter Configuration | Min Names/Mo | Median Names/Mo | Max Names/Mo | Distinct Symbols | % Months $\ge 8$ Names |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Baseline ($L1 + L2 + L3_{\text{EV/EBITDA}}$)** | **1** | **8.0** | **40** | **105** | **51.7%** |
| **Removing L1 (Quality Filters)** | 16 | 35.5 | 249 | 362 | 96.7% |
| **Removing L2 (Dilution Forensics)** | 1 | 10.0 | 49 | 136 | 63.3% |
| **Removing L3 (EV/EBITDA Gate)** | 42 | 74.0 | 110 | 289 | 100.0% |

* **Analysis**: Layer 3 (Valuation vs. Own History) and Layer 1 (Quality & Cash Flow) are the primary binding filters. Layer 2 (Share Dilution) filters high-dilution outliers while preserving the core candidate stream.

---

### 4. Candidate Distribution by Calendar Year

| Calendar Year | Rebalance Months | Total Candidate Instances | Monthly Range | Months with $\ge 8$ Candidates | % Months $\ge 8$ Candidates |
| :---: | :---: | :---: | :---: | :---: | :---: |
| **2020** | 12 | 233 | 6 to 40 | 10 | **83.3%** |
| **2021** | 12 | 55 | 1 to 7 | 0 | **0.0%** |
| **2022** | 12 | 114 | 3 to 20 | 7 | **58.3%** |
| **2023** | 12 | 162 | 6 to 23 | 11 | **91.7%** |
| **2024** | 12 | 57 | 2 to 9 | 3 | **25.0%** |
| **Total** | **60** | **621** | **1 to 40** | **31** | **51.7%** |

* **Key Observation**: Candidate availability is heavily concentrated in the 2020 market dislocation (83.3% pass rate) and the 2023 small/mid-cap valuation reset (91.7% pass rate). In strong bull markets (2021 and 2024), quality stocks rarely trade at a $\ge 25\%$ discount to their own 3-year median, causing candidate availability to drop to 0%–25%.

---

### 5. Research Findings & Lessons Learned

1. **Annual Data Frequency Limitations**: Point-in-time fundamentals sourced from annual filings mean a 3-year valuation lookback rests on approximately 3 distinct financial reports per company. This limits intra-year valuation sensitivity compared to quarterly feeds.
2. **Missing Quantitative Governance Fields**: Structured databases lack point-in-time tracking for promoter pledges, auditor resignations, related-party transactions (RPT), and CWIP ageing. Automated screens cannot substitute for qualitative governance verification.
3. **Macro Window Dependency**: Testing over 2020–2024 is dominated by the 2020 COVID crash and subsequent liquidity expansion. On annual historical data, a pure fundamental valuation funnel cannot produce enough non-overlapping, multi-regime sample points to achieve statistical certification.
4. **Methodological Conclusion**: Iterative redesigns on the same 5-year historical dataset risk overfitting to past price history. Further backtesting is halted, and research moves directly to real-time forward tracking.

---

### 6. Transition to Phase E Live Forward Tracker

The core deliverable for `FUNDAMENTAL_GEM_RECOVERY` is transitioned to the **Phase E Live Forward Tracker**:
- **Rules**: Frozen $L1 + L2 + L3_{\text{EV/EBITDA}}$ screening rules.
- **Classification**:
  - **Tier A**: Passes $L1+L2+L3$ AND $Res\_DD \le 10\%$ vs Nifty 500 TRI (Quality + Valuation + Market Dislocation).
  - **Tier B**: Passes $L1+L2+L3$ WITHOUT $Res\_DD \le 10\%$ (Quality + Valuation Only).
- **Execution**: Live monthly watchlist generator with append-only logging, tracking 1Y/2Y/3Y forward returns against Nifty 500 TRI and an equal-weighted $L1$ quality benchmark.
- **Pre-Buy Protocol**: Includes a mandatory 7-point manual checklist for qualitative governance verification.
