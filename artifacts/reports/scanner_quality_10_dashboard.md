# Master Progress Dashboard: Scanner Alert Quality 10/10

**Governing Charter:** Master Specification v5.1.0 (FROZEN)  
**Evaluated At:** 2026-08-30 23:05:30 IST  
**Market Session State:** **CLOSED (Sunday Non-Trading Session — Awaiting Monday 09:15 IST Market Open)**  
**Compilation Status:** **PASSED (python -m compileall app engine exited with code 0)**  
**Test Suite Status:** **28 / 28 PASSED (100% Pass Rate)**  
**Model Registry SHA256:** `6100ff83460c242f5c8e30347e1d075d4d209b3879e4c33c7de92ddf678ee8cb`  

---

## 1. Authoritative Model Registry & Fidelity Matrix

```
┌─────────────────┬──────────────────────┬──────────────────────────────┬───────────┬──────────────────────────────────────────┐
│ Scanner Engine  │ Candidate Model ID   │ Model Family                 │ Version   │ Source Parameters & Status               │
├─────────────────┼──────────────────────┼──────────────────────────────┼───────────┼──────────────────────────────────────────┤
│ **`EOD`**       │ `AQS_EOD_v1`         │ REGULARIZED_RIDGE_LINEAR     │ 1.0.0     │ `artifacts/scanner_quality_model_registry`│
│ **`MULTIBAG`**  │ `AQS_ACCUM_v1`       │ LINEAR_STANDARDIZED_SCORE    │ 1.0.0     │ `artifacts/scanner_quality_model_registry`│
│ **`PULLBACK`**  │ `AQS_PULLBACK_v1`    │ DEPTH_REBOUND_SCORE          │ 1.0.0     │ `artifacts/scanner_quality_model_registry`│
│ **`DAILY_BLD`** │ `AQS_DAILY_BUILDER_v1` ORB_SURGE_SCORE              │ 1.0.0     │ `artifacts/scanner_quality_model_registry`│
│ **`MULTI_TF`**  │ `AQS_MULTI_TF_v1`    │ TREND_ALIGNMENT_SCORE        │ 1.0.0     │ `artifacts/scanner_quality_model_registry`│
│ **`REVERSAL`**  │ `AQS_REVERSAL_v3`    │ DISCOVERY_ACTIVE             │ Discovery │ DISCOVERY_ONLY (Observation-Only)        │
│ **`WEALTH`**    │ `AQS_WEALTH_v1`      │ MULTI_FACTOR_FUNDAMENTAL     │ 1.0.0     │ `artifacts/scanner_quality_model_registry`│
└─────────────────┴──────────────────────┴──────────────────────────────┴───────────┴──────────────────────────────────────────┘
```

---

## 2. Scanner Execution Policy & Friction Standard

```
┌─────────────────┬───────────┬──────────────┬─────────────────────────┬──────────────────────┬────────────────────────────────┐
│ Scanner Engine  │ Timeframe │ Max Horizon  │ Intraday Session Bounded│ Intrabar Collision   │ Round-Trip Friction Model      │
├─────────────────┼───────────┼──────────────┼─────────────────────────┼──────────────────────┼────────────────────────────────┤
│ **`EOD`**       │ 1D        │ 20 Bars      │ NO                      │ CONSERVATIVE (SL 1st)│ Exactly 10.0 bps Round-Trip    │
│ **`MULTIBAG`**  │ 1D        │ 60 Bars      │ NO                      │ CONSERVATIVE (SL 1st)│ Exactly 10.0 bps Round-Trip    │
│ **`PULLBACK`**  │ 1D        │ 15 Bars      │ NO                      │ CONSERVATIVE (SL 1st)│ Exactly 10.0 bps Round-Trip    │
│ **`DAILY_BLD`** │ 15m       │ 25 Bars (1d) │ YES (Day Close Squareoff│ CONSERVATIVE (SL 1st)│ Exactly 10.0 bps Round-Trip    │
│ **`MULTI_TF`**  │ 5m        │ 75 Bars (2d) │ NO                      │ CONSERVATIVE (SL 1st)│ Exactly 10.0 bps Round-Trip    │
│ **`REVERSAL`**  │ 1D        │ 10 Bars      │ NO                      │ CONSERVATIVE (SL 1st)│ Exactly 10.0 bps Round-Trip    │
│ **`WEALTH`**    │ 1D        │ 90 Bars (Qtr)│ NO                      │ CONSERVATIVE (SL 1st)│ Exactly 10.0 bps Round-Trip    │
└─────────────────┴───────────┴──────────────┴─────────────────────────┴──────────────────────┴────────────────────────────────┘
```
