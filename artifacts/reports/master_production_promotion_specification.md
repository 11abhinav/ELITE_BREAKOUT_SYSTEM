# Master Production Promotion & Governance Specification (v5.1.0)

**Governing Charter:** Scanner Alert Quality 10/10 Master Program  
**Target State:** 7 / 7 Engines at `PRODUCTION_IMPROVED`  
**Current State:** 0 / 7 `PRODUCTION_IMPROVED` (6 Forward Validating, 1 Discovery Active)  
**Production Code Status:** **Live production code remains 100% untouched until an explicit, authorized promotion occurs.**  

---

## 1. The Governed 6-Step Promotion & Retirement Sequence

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ GOVERNED PRODUCTION PROMOTION & SHADOW RETIREMENT SEQUENCE                      │
├─────────────────────────────────────────────────────────────────────────────────┤
│ Step 1: Forward Gate & Incremental Dominance Audit                              │
│         Verify N >= 50, >= 15 symbols, >= 5 days, <= 20% conc, BCa CI > 0,      │
│         Delta MaxDD <= 0, and Delta Net E[R] > 0 vs same baseline.              │
│                                                                                 │
│ Step 2: Production Integration Package Construction                             │
│         Prepare minimal, surgical production integration patch into real        │
│         scanner engine (app/ or engine/).                                       │
│                                                                                 │
│ Step 3: Regression & Safety Test Suite                                          │
│         Execute full pytest regression suite ensuring zero breakage to live     │
│         order routing, execution geometry, and API contracts.                   │
│                                                                                 │
│ Step 4: Canary Verification & Live Shadow Telemetry Parity                      │
│         Verify candidate output against production output on identical live     │
│         market feed tick data.                                                  │
│                                                                                 │
│ Step 5: Explicit Promotion Authorization & Production Migration                 │
│         Obtain explicit promotion approval; apply surgical production patch;    │
│         update scorecard state to PRODUCTION_IMPROVED.                          │
│                                                                                 │
│ Step 6: Temporary Shadow Retirement                                             │
│         Decommission temporary shadow evaluator harnesses for that scanner.    │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Master All-Scanner Promotion Tracker (v5.1.0)

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ ALL 7 SCANNERS PRODUCTION PROMOTION & RETIREMENT TRACKER                                                                                               │
├───────────────────┬───────────────────────────────┬────────────────────────────┬─────────────────────────────┬──────────────────┬──────────────────────┤
│ Scanner Engine    │ Frozen Candidate Mechanism    │ Forward Gate Requirement   │ Current Forward State       │ Production Status│ Shadow Retirement    │
├───────────────────┼───────────────────────────────┼────────────────────────────┼─────────────────────────────┼──────────────────┼──────────────────────┤
│ **`EOD`**         │ `AQS_EOD_v1` (Pure Ranking)   │ N >= 50, >= 15 syms, CI > 0│ 0 / Accumulating Telemetry  │ LOCKED           │ ACTIVE_SHADOW        │
│ **`MULTIBAGGER`** │ `AQS_ACCUM_v1`                │ N >= 50, >= 15 syms, CI > 0│ 0 / Accumulating Telemetry  │ LOCKED           │ ACTIVE_SHADOW        │
│ **`PULLBACK`**    │ `AQS_PULLBACK_v1` (Ranking)   │ N >= 50, >= 15 syms, CI > 0│ 0 / Accumulating Telemetry  │ LOCKED           │ ACTIVE_SHADOW        │
│ **`DAILY_BUILDER`** `AQS_DAILY_BUILDER_v1`        │ N >= 50, >= 15 syms, CI > 0│ 0 / Accumulating Telemetry  │ LOCKED           │ ACTIVE_SHADOW        │
│ **`MULTI_TF`**    │ `AQS_MULTI_TF_v1`             │ N >= 50, >= 15 syms, CI > 0│ 0 / Accumulating Telemetry  │ LOCKED           │ ACTIVE_SHADOW        │
│ **`WEALTH_ENGINE`** `AQS_WEALTH_v1`               │ >= 4 Qtrs, Alpha >= +10%   │ 0 / Awaiting Q1 Cycle       │ LOCKED           │ ACTIVE_SHADOW        │
│ **`REVERSAL`**    │ `AQS_REVERSAL_v3` (Discovery) │ Survived Holdout -> Gate   │ Discovery Active            │ LOCKED           │ IN_DISCOVERY         │
└───────────────────┴───────────────────────────────┴────────────────────────────┴─────────────────────────────┴──────────────────┴──────────────────────┘
```

---

## 3. The 10/10 Finish Condition

The program terminates as **10/10 COMPLETE** only when all 7 scanners reach:
$$\text{Production Status: } \mathbf{PRODUCTION\_IMPROVED} \quad \text{AND} \quad \text{Shadow Retirement: } \mathbf{RETIRED}$$
