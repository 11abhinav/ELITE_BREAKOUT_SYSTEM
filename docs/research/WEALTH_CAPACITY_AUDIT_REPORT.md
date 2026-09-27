# CAPACITY & SLOT-SENSITIVITY AUDIT: WEALTH EXIT V1 vs V2
**Evaluation Universe:** 886 Certified Clean Equities (81,653 Causal Breakout Alerts)  
**Evaluation Period:** 2016-11-21 to 2026-09-25 (9.83 Years)  
**Starting Capital:** ₹10,00,000 Portfolio  
**Tested Capacities:** 5 Slots, 10 Slots (Baseline), 20 Slots, 50 Slots, Unlimited (500 Slots)  
**Friction Invariant:** 10 bps round-trip applied symmetrically  

---

## 1. CAPACITY SENSITIVITY MATRIX (WEALTH EXIT V1 vs V2 vs ARM A)

| Capacity | V1 Final Wealth | V1 CAGR | V1 Invested (Rejection) | V2 Final Wealth | V2 CAGR | V2 Invested (Rejection) | Delta CAGR (V2 - V1) | Arm A CAGR (Wealth) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **5 Slots** | **₹116.10L** | **+28.33%** | 300 (99.6%) | **₹76.84L** | **+23.05%** | 184 (99.8%) | **-5.28%** | +1.05% (₹11.08L) |
| **10 Slots** | **₹119.78L** | **+28.74%** | 572 (99.3%) | **₹82.63L** | **+23.97%** | 337 (99.6%) | **-4.77%** | +0.16% (₹10.15L) |
| **20 Slots** | **₹100.10L** | **+26.41%** | 1,171 (98.6%) | **₹83.78L** | **+24.14%** | 628 (99.2%) | **-2.27%** | -0.17% (₹9.84L) |
| **50 Slots** | **₹76.49L** | **+23.00%** | 2,954 (96.4%) | **₹68.21L** | **+21.57%** | 1,578 (98.1%) | **-1.43%** | +4.33% (₹15.17L) |
| **Unlimited (500 Slots)** | **₹46.98L** | **+17.05%** | 27,598 (66.2%) | **₹53.24L** | **+18.54%** | 15,601 (80.9%) | **+1.49%** | +3.36% (₹13.84L) |

---

## 2. CORE SCIENTIFIC TAKEAWAYS FROM CAPACITY AUDIT

1. **Why V1 Wins Under Constrained Slots (5 to 10 Slots):**
   - At 10 slots, V1 compounds to **₹119.78L (+28.74% CAGR)** vs V2's **₹82.63L (+23.97% CAGR)**.
   - V1 has an average holding period of **40.2 days**, allowing it to enter **572 positions** (rejection rate: 99.3%).
   - V2 has an average holding period of **76.6 days**, allowing it to enter only **337 positions** (rejection rate: 99.6%).
   - Under tight slot constraints, **capital velocity dominates per-trade alpha**.

2. **The Capacity Cross-Over Effect:**
   - As portfolio capacity expands to **20 slots, 50 slots, and Unlimited**, the slot-locking bottleneck disappears.
   - Look at the CAGR delta column: as slots increase, V2's massive trade-level alpha (+15.32% vs +6.97%) asserts itself because fewer high-alpha breakout alerts are rejected!

3. **Governance Verdict:**
   - **Under the exact production invariant (₹10 Lakhs, 10 Concurrent Slots):** **WEALTH_EXIT_V1 remains the undisputed portfolio baseline (₹1.198 Crore / 28.74% CAGR).**
   - **WEALTH_EXIT_V2 is frozen as an advanced research candidate** that excels in higher-capacity or unconstrained portfolios.
   - As mandated by our governance rules: **Neither model will be tuned further.** Both are frozen prior to moving to Phase 3 (Historical Point-in-Time Fundamentals Ingestion).

---
*Authored by Elite Breakout System Research Engine. Locked under AGENTS.md Invariants.*
