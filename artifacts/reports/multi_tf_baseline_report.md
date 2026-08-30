# Baseline Report: MULTI_TF (Multi-Timeframe Confluence)

**Report Generated:** 2026-08-30 20:05:56 IST  
**Strategy Family:** MULTI_TF (Multi-Timeframe Confluence)  
**Semantic Scope:** `ACTIONABLE_TRADE_ALERT`  
**Dataset Version:** `1.0.0_ALL_SCANNER`  

---

## 1. Population & Telemetry Summary

| Metric | Count / Value | Description |
|---|---|---|
| **Total Telemetry Records** | **29** | Total candidate alert records ingested |
| **Production-Valid Outcomes** | **0** | Strictly clean non-zero geometry replays |
| **Excluded Invalid Records** | **29** | Excluded due to mock levels / zero targets |
| **Unique Telemetry Symbols** | **5** | Total unique symbols in raw telemetry |
| **Unique Valid Symbols** | **0** | Unique symbols with verified clean geometry |
| **Unique Trading Days** | **2** | Calendar trading sessions covered |

---

## 2. Production-Valid Trading Baseline Metrics

> [!WARNING]
> **Zero Valid Production Outcomes Available:**
> This scanner currently has 0 valid replayable outcomes due to uninitialized targets or mock execution levels.
> **Primary Blocker:** 100% of historical records recorded mock entry levels (₹129.50) and zero target distance. Replay repair P0.
> **Next Step:** Re-hydrate telemetry pipeline and simulate forward price paths before establishing a numerical baseline.