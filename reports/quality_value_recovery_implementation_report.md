# QUALITY_VALUE_RECOVERY — Implementation & Governance Certification Report

**Strategy ID:** `QUALITY_VALUE_RECOVERY`  
**Legacy Strategy Alias:** `QUALITY_VALUE_RECOVERY_WEALTH_V1`  
**Execution Mode:** Production Live Watchlist (Alert-Only Decision Support)  
**Broker Trading:** `AUTOMATIC_BROKER_TRADING = DISABLED`  
**Date:** 2026-10-03  
**Status:** `CERTIFIED_FOR_PRODUCTION`

---

## 1. Executive Summary

This document certifies the canonical production implementation, database integration, health registry binding, telemetry logging, and exit monitor routing for the frozen strategy **`QUALITY_VALUE_RECOVERY`**.

All research entry filters (Model D: 5Y ROCE $\ge 15\%$, 5Y Sales CAGR $\ge 10\%$, 5Y PAT CAGR $\ge 10\%$, 5Y CFO/PAT $\ge 0.80$, D/E $\le 0.50$, EV/EBITDA discount $\ge 25\%$, Sector relative drawdown dislocation $\le 10\%$) and exit thresholds (Model E3: trailing 3-quarter PAT deceleration, 3Y median EV/EBITDA valuation mean-reversion re-rating, 10-session maximum holding window) remain 100% frozen.

---

## 2. Canonical Strategy Rename & Identification Governance

1. **Canonical Identifier:** `QUALITY_VALUE_RECOVERY`
2. **Normalized Aliases:**
   - `QUALITY_VALUE_RECOVERY_WEALTH_V1` $\rightarrow$ `QUALITY_VALUE_RECOVERY`
   - `RECOVERY_WEALTH_V1` $\rightarrow$ `QUALITY_VALUE_RECOVERY`
   - `RECOVERY` $\rightarrow$ `QUALITY_VALUE_RECOVERY`
3. **Database Integration (`app/database.py`):**
   - `schedule_map` updated: `"QUALITY_VALUE_RECOVERY": "Daily 17:15 IST (Quality Value Recovery · ALL Regimes)"`
   - `normalize_scanner_name()` maps all legacy aliases cleanly to `QUALITY_VALUE_RECOVERY`.
   - All new alert records insert `scanner = "QUALITY_VALUE_RECOVERY"`.
4. **Governance Registry (`engine/production/governance_registry.py`):**
   - Registered under `CERTIFIED_PRODUCTION_SCANNERS` and `SCANNER_REGIME_HEALTH_METADATA`.
5. **Lock Utils (`app/lock_utils.py`):**
   - `SCANNER_CONFIG` bound with `db_name: "QUALITY_VALUE_RECOVERY"`.

---

## 3. Mandatory Implementation Invariants

| Invariant | Specification | Governance Status |
| :--- | :--- | :--- |
| **Broker Trading** | `AUTOMATIC_BROKER_TRADING = DISABLED` | **ENFORCED** (Zero order placement) |
| **Sequentiality** | Shared `global_scanner_lock` serialization | **ENFORCED** (Non-blocking acquire in try/finally) |
| **Data Integrity** | Zero synthetic fallbacks; fail-closed `DATA_BLOCKED` | **ENFORCED** (Authoritative Upstox PIT data only) |
| **Telemetry** | Every fetch step logs `[SCANNER: QUALITY_VALUE_RECOVERY] [FETCH_DATA]` | **ENFORCED** (Full transparency across loader & pre-recovery) |
| **Exit Monitor** | `CanonicalRecoveryE3ExitEvaluator` routed at 15:15 & 18:30 IST | **ENFORCED** (Model E3 fundamental exit evaluation) |

---

## 4. Test Results Matrix

| Test Module | Description | Result |
| :--- | :--- | :--- |
| `test_01_scanner_name_normalization` | Verifies resolution of legacy aliases to `QUALITY_VALUE_RECOVERY` | **PASS** |
| `test_02_health_card_registration` | Verifies strategy appearance in Health Scanner cards | **PASS** |
| `test_03_global_lock_sequentiality` | Verifies ProcessLock acquisition and try/finally release | **PASS** |
| `test_04_controlled_failure_lock_release` | Verifies clean lock release and health logging under contention | **PASS** |
| `test_05_health_state_transitions` | Verifies `RUNNING` $\rightarrow$ `OK` health state transitions | **PASS** |

---

## 5. Certification Sign-off

**Final Status:** `QUALITY_VALUE_RECOVERY` is fully implemented, integrated, certified, and ready for automated daily scheduling at **17:15 IST**.
