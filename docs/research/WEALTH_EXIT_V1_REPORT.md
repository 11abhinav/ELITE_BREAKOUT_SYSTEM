# WEALTH_EXIT_V1 — LONG-TERM WEALTH REPLAY AUDIT REPORT
**Evaluation Period:** 2016-11-21 to 2026-09-25 (9.83 Years)  
**Total Alerts Tested:** 85,581 Causal Breakouts  
**Tested Universe:** 927 Certified Equities  
**Normalized Trade Capital:** ₹1,00,000 per position (Residual cash preserved)  
**Normalized Portfolio:** ₹10,00,000 Starting Capital, Max 10 Concurrent Slots  
**Friction Invariant:** 10 bps round-trip applied to all arms  

---

### EXECUTIVE ANSWER TO CORE RESEARCH QUESTION
> **"If I had followed every historical 20D breakout alert with ₹1,00,000, entered at T+1 Open, and held until confirmed weakness instead of taking fixed profits, what wealth would I have ended with versus the existing trading exit and pure hold?"**

1. **Trade-Level Outcome (Average Return per ₹1,00,000 Alert):**
   - **Arm A (Existing 15D Trailing Exit):** Mean Return = **+0.25%** (Win Rate: 48.7%, Avg Hold: 4.5 days)
   - **Arm B (Wealth Exit V1):** Mean Return = **+6.87%** (Win Rate: 41.5%, Avg Hold: 40.2 days)
   - **Arm C (Pure-Hold Diagnostic):** Mean Return = **+349.89%** (Win Rate: 74.8%, Avg Hold: 1066.8 days)
   - **Delta B minus A:** **+6.62%**
   - **Delta C minus A:** **+349.63%**
   - **Delta B minus C:** **-343.01%**

2. **Portfolio-Level Outcome (₹10,00,000 Portfolio, 10 Slots):**
   - **Arm A Portfolio:** Final Wealth = **₹876,068.17** (CAGR: **-1.34%**, Invested: 4,901)
   - **Arm B Portfolio:** Final Wealth = **₹9,383,013.49** (CAGR: **25.58%**, Invested: 563)
   - **Arm C Portfolio:** Final Wealth = **₹9,732,403.03** (CAGR: **26.05%**, Invested: 18)

---

### POST-EXIT CORRECTNESS AUDIT (ARM B WEAKNESS EXITS)
- **Total Confirmed Weakness Exits:** 83,947
- **CORRECT Exits (Saved from further drop or max DD <= -15%):** 40,333 (48.0%)
- **EARLY Exits (Sold before >+15% rally or +25% gain):** 25,793 (30.7%)
- **NEUTRAL Exits (Choppy/Sideways within range):** 17,821 (21.2%)

---

### TEMPORAL CELL BREAKDOWN (ARM B vs ARM A vs ARM C)
| Temporal Cell | Trades (N) | Arm A Mean Ret | Arm B Mean Ret | Arm C Mean Ret | Delta (B - A) | Arm B Win Rate |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **2016-2018** | 12,486 | +0.23% | +2.39% | +414.14% | **+2.16%** | 38.1% |
| **2019-2021** | 23,849 | +0.28% | +11.47% | +859.86% | **+11.19%** | 43.7% |
| **2022-2024** | 31,326 | +0.31% | +7.52% | +125.52% | **+7.21%** | 43.4% |
| **2025-2026** | 17,920 | +0.13% | +2.74% | +18.63% | **+2.62%** | 37.8% |

---

### REGIME BREAKDOWN (BULL vs SIDEWAYS vs BEAR)
| Macro Regime | Trades (N) | Arm A Mean Ret | Arm B Mean Ret | Arm C Mean Ret | Delta (B - A) | Arm B Mean Hold |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **BEAR** | 18,210 | -0.07% | +5.92% | +516.67% | **+5.99%** | 36.2 days |
| **BULL** | 43,643 | +0.34% | +8.21% | +251.31% | **+7.87%** | 43.1 days |
| **SIDEWAYS** | 23,728 | +0.33% | +5.13% | +403.19% | **+4.80%** | 37.9 days |

---

### CALENDAR QUARTER BREAKDOWN
| Quarter | Trades (N) | Arm A Mean Ret | Arm B Mean Ret | Arm C Mean Ret | Delta (B - A) | Arm B Win Rate |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Q1** | 17,075 | -0.18% | +3.88% | +627.90% | **+4.06%** | 33.0% |
| **Q2** | 25,936 | +0.65% | +9.51% | +236.18% | **+8.86%** | 48.6% |
| **Q3** | 24,153 | +0.13% | +6.07% | +323.97% | **+5.94%** | 40.4% |
| **Q4** | 18,417 | +0.25% | +6.97% | +286.24% | **+6.73%** | 40.9% |

---
*Authored by Elite Breakout System Research Engine. Locked under AGENTS.md Invariants.*
