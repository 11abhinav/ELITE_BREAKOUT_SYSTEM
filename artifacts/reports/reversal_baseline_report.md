# Baseline Report: REVERSAL (Mean Reversion & Exhaustion)

**Report Generated:** 2026-08-30 20:05:56 IST  
**Strategy Family:** REVERSAL (Mean Reversion & Exhaustion)  
**Semantic Scope:** `ACTIONABLE_TRADE_ALERT`  
**Dataset Version:** `1.0.0_ALL_SCANNER`  

---

## 1. Population & Telemetry Summary

| Metric | Count / Value | Description |
|---|---|---|
| **Total Telemetry Records** | **29** | Total candidate alert records ingested |
| **Production-Valid Outcomes** | **1** | Strictly clean non-zero geometry replays |
| **Excluded Invalid Records** | **28** | Excluded due to mock levels / zero targets |
| **Unique Telemetry Symbols** | **3** | Total unique symbols in raw telemetry |
| **Unique Valid Symbols** | **1** | Unique symbols with verified clean geometry |
| **Unique Trading Days** | **3** | Calendar trading sessions covered |

---

## 2. Production-Valid Trading Baseline Metrics

| Performance Metric | Baseline Value | Standard |
|---|---|---|
| **Baseline Mean Net Expected R** | **-1.050R** | Post-friction ($0.05\text{R}$ transaction cost) |
| **Baseline Gross Realized R** | **-1.000R** | Pre-friction raw payoff |
| **Baseline Median Realized R** | **-1.050R** | Distribution median |
| **Mean Maximum Favorable Excursion (MFE)** | **+0.00R** | Average peak in-trade expansion |
| **Mean Maximum Adverse Excursion (MAE)** | **16.46R** | Average peak in-trade adverse excursion |
| **Target 1 Hit Rate (Win Rate)** | **0.0%** | Binary T1 hit percentage |

> [!NOTE]
> **Baseline Lineage & Context:** Mechanics verified (PASS), sample size n=1 (-1.05R). Forward market events required.