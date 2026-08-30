# PULLBACK Baseline Expansion & Lineage Audit Report

**Report Generated:** 2026-08-30 20:36:45 IST  
**Engine Scope:** `PULLBACK` (Trend Retracement & Continuation)  
**Lineage Purpose:** Explicit audit trail and re-computation of candidate mechanism performance following baseline expansion from exploratory $N=50$ to authoritative $N=499$.  
**Production Code Status:** **100% UNTOUCHED (Zero Mutations)**  

---

## 1. Baseline Expansion Lineage & Versioning

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ PULLBACK BASELINE POPULATION VERSIONING                                                               │
├───────────────────────────────┬───────────────────────────────┬────────────────────────────────────────┤
│ Dimension                     │ Baseline v1.0 (Exploratory)   │ Baseline v2.0 (Authoritative Expanded) │
├───────────────────────────────┼───────────────────────────────┼────────────────────────────────────────┤
│ Valid Simulated Sample (N)    │ 50 Trades                     │ **499 Trades** (+449 Additional Trades)│
│ Unique Equities               │ 50 Symbols                    │ **313 Unique Symbols**                 │
│ Ingestion Source              │ First 50 candidate triggers   │ Full batch candidate trigger stream    │
│ Underlying Market Bar Source  │ data/history/1d/*.parquet     │ data/history/1d/*.parquet              │
│ Date Range                    │ 2026-08-19                    │ 2026-08-19 to 2026-08-27               │
│ Baseline Gross Mean E[R]      │ +0.258R                       │ **+0.110R**                            │
│ Baseline Net Mean E[R]        │ +0.208R                       │ **+0.060R** (Post-0.05R Friction)      │
│ Realized Target Hit Rate (T1) │ 22.0%                         │ **12.8%**                              │
│ Mean MFE / MAE                │ 1.13R / 0.52R                 │ **0.84R / 0.50R**                      │
└───────────────────────────────┴───────────────────────────────┴────────────────────────────────────────┘
```

> [!IMPORTANT]
> **Governance Rule Applied:**  
> Whenever baseline $N$ expands materially, the baseline is formally versioned (`v1.0` $\to$ `v2.0`) and candidate quality mechanisms are re-computed against the **identical expanded population**.

---

## 2. Re-Computation of `AQS_PULLBACK_v1` on Expanded Baseline ($N = 499$)

**Frozen Mechanism:** $\text{AQS\_PULLBACK\_v1} = 50 + 15 \cdot \left[ 0.6 \cdot \text{Depth\_Fit} + 0.4 \cdot z(\text{Volume\_Rebound}) \right]$

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ AQS_PULLBACK_v1 RE-COMPUTATION ACROSS EXPANDED N = 499 POPULATION                                      │
├───────────────────────────────┬──────────────┬──────────────┬──────────────┬───────────────────────────┤
│ Tier Subpopulation            │ Sample Size  │ Net Mean E[R]│ Hit Rate %   │ Economic Assessment       │
├───────────────────────────────┼──────────────┼──────────────┼──────────────┼───────────────────────────┤
│ **Retained Top 67% (Tiers 1&2)** 334 Trades  │ **+0.138R**  │ 14.7%        │ **Alpha Expansion (+0.078R)**
│ **Filtered Bottom 33% (Tier 3)** 165 Trades  │ **-0.098R**  │ 9.1%         │ **NEGATIVE EXPECTANCY**   │
├───────────────────────────────┼──────────────┼──────────────┼──────────────┼───────────────────────────┤
│ **Full Expanded Baseline v2.0** 499 Trades   │ **+0.060R**  │ 12.8%        │ **Authoritative Baseline** │
└───────────────────────────────┴──────────────┴──────────────┴──────────────┴───────────────────────────┘
```

### Key Statistical Confirmations:
1. **Durable Alpha Retention:** On the expanded 499-trade / 313-symbol population, `AQS_PULLBACK_v1` retains a positive economic delta ($\Delta = \mathbf{+0.078\text{R}}$ Net over baseline).
2. **Negative Expectancy Isolation:** The filtered bottom-third continues to isolate a strictly negative-expectancy subpopulation ($-0.098\text{R}$ Net).
3. **Status:** `AQS_PULLBACK_v1` remains **FROZEN** and actively logging forward production alerts toward the $N \ge 50$ gate.
