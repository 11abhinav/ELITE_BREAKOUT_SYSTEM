# Baseline Report: EOD (Daily Breakout)

**Report Generated:** 2026-08-30 20:05:56 IST  
**Strategy Family:** EOD (Daily Breakout)  
**Semantic Scope:** `ACTIONABLE_TRADE_ALERT`  
**Dataset Version:** `1.0.0_ALL_SCANNER`  

---

## 1. Population & Telemetry Summary

| Metric | Count / Value | Description |
|---|---|---|
| **Total Telemetry Records** | **5,234** | Total candidate alert records ingested |
| **Production-Valid Outcomes** | **26** | Strictly clean non-zero geometry replays |
| **Excluded Invalid Records** | **5,208** | Excluded due to mock levels / zero targets |
| **Unique Telemetry Symbols** | **310** | Total unique symbols in raw telemetry |
| **Unique Valid Symbols** | **1** | Unique symbols with verified clean geometry |
| **Unique Trading Days** | **2** | Calendar trading sessions covered |

---

## 2. Production-Valid Trading Baseline Metrics

| Performance Metric | Baseline Value | Standard |
|---|---|---|
| **Baseline Mean Net Expected R** | **+1.100R** | Post-friction ($0.05\text{R}$ transaction cost) |
| **Baseline Gross Realized R** | **+1.150R** | Pre-friction raw payoff |
| **Baseline Median Realized R** | **+1.100R** | Distribution median |
| **Mean Maximum Favorable Excursion (MFE)** | **+1.64R** | Average peak in-trade expansion |
| **Mean Maximum Adverse Excursion (MAE)** | **0.00R** | Average peak in-trade adverse excursion |
| **Target 1 Hit Rate (Win Rate)** | **0.0%** | Binary T1 hit percentage |

> [!NOTE]
> **Baseline Lineage & Context:** 26 clean non-zero geometry replays (all RELIANCE, +1.100R net). 44 mock zero-target records excluded.