# Final Daily Builder Production Promotion Report & Governance Audit

**Session Date**: `2026-09-11`  
**Current Production**: `V5.25_PRODUCTION`  
**Research Champion**: `V5.30` (45m confirmation + Focused Model G + Regime Veto + Dynamic Capacity)  
**Live Challenger**: `V5.30_DB_SHADOW`  
**Git Commit**: `bf4da25fd10028cc034d6f9fa610cefb9dd62a0e`  

---

## SECTION A: Current System State
* **`V5.25_PRODUCTION`**: 🟢 **LIVE REAL MONEY** (Order routing active, strictly untouched).
* **`V5.28_DB_SHADOW`**: 🟡 **FROZEN CONTROL** (Historical benchmark).
* **`V5.29_SHADOW`**: 🟢 **ACTIVE LIVE SHADOW** (30m benchmark stream).
* **`V5.30_DB_SHADOW`**: 🚀 **ACTIVE LIVE SHADOW** (45m challenger stream).

---

## SECTION B: Current Live Sample
Actual counts queried directly from `data/shadow_telemetry.db`:
* **`v529_shadow_alert_telemetry`**: `7` records
* **`v529_shadow_trigger_telemetry`**: `5` records
* **`v530_shadow_alert_telemetry`**: `7` records
* **`v530_shadow_trigger_telemetry`**: `5` records
* **`v530_vs_v529_disagreement_telemetry`**: `7` paired records
* **Live Resolved Disagreements ($N_{\text{resolved}}$)**: **`2`** (`POLYCAB`, `BHARTIARTL`)
* **Gate Milestone**: $N \ge 100$ (Preferred $200–300$)

---

## SECTION C: Historical Certification (Period D Fresh Holdout Reproduction)
Re-verified against the fresh, untouched 125-session / 4,320-event holdout:
* **V5.29 Baseline E[R]**: `+1.235R/trade` (WR: 78.9%, PF: 6.57, MaxDD: 3.86R)
* **V5.30 Candidate E[R]**: `+1.654R/trade` (WR: 94.8%, PF: 35.60, MaxDD: 1.18R)
* **Paired Advantage**: **`+0.412R/trade`**
* **95% Bootstrap CI**: **`[+0.235R, +0.581R]`** (Strictly positive)
* **Permutation Test**: **`p = 0.0000`** (Statistically superior)
* **LOO1 Robustness**: `+0.397R`

---

## SECTION D: Live Paired Analysis ($N = 2$)
* **Total Paired Evaluated**: `7` candidates
* **Concurring Confirmations**: `3` (`TRENT`, `KALYANKJIL`, `DIXON` — both confirmed)
* **Concurring Filters**: `2` (`RELIANCE`, `HDFCBANK` — both score-filtered)
* **Resolved Disagreements**: `2` (`POLYCAB`, `BHARTIARTL` — V5.29 confirmed at 30m; V5.30 45m trigger avoided morning traps at 10:00 AM)
* **Preliminary Live Paired Lift**: `+2.00R` cumulative loss avoided across initial live test.

---

## SECTION E: Root-Cause Decomposition
* **`45M_CONFIRMATION`**: `100.0%` of observed divergences ($2/2$ events).
* **`FOCUSED_MODEL_G`**: `0` divergences in current initial batch.
* **`REGIME_VETO`**: `0` divergences in current initial batch.
* **`DYNAMIC_CAPACITY`**: `0` divergences in current initial batch.

---

## SECTION F: Execution / Friction Analysis
* **Friction Model**: Standard $0.08R$ execution slippage applied to all confirmed 45m fills; $0.00R$ on unconfirmed traps.
* **Friction Verdict**: **`PASS`** (45m edge easily covers $0.08R$ execution friction).

---

## SECTION G: Regime Analysis
* Strong Bull: 5 slots allowed / 5 allocated
* Neutral Bull: 4 slots allowed
* Choppy Range: 2 slots allowed
* Neutral Bear: 1 slot allowed
* Sharp Selloff: 0 slots (complete safety gating)
* **Regime Durability Verdict**: **`PASS`**

---

## SECTION H: Capacity Validation
* Dynamic slot selection verified; zero future-leakage in regime detection.
* **Capacity Verdict**: **`PASS`**

---

## SECTION I: Parameter & Version Audit
* All 5 certified parameters present in `data/production_parameters.db` with status `SHADOW` bound to commit `bf4da25f`.
* **Parameter Parity Verdict**: **`PASS`**

---

## SECTION J: Weekend & Lookahead Hard Invariants
* **Saturday Candles**: `0`
* **Sunday Candles**: `0`
* **NSE Holiday Violations**: `0`
* **Lookahead Violations**: `0`
* **Duplicate Events**: `0`
* **Invariant Verdict**: **`PASS`**

---

## SECTION K: Production Isolation Audit
* `V5.25_PRODUCTION` tables & broker routing: **`0 MUTATIONS` (STRICTLY UNTOUCHED)**
* `V5.28_DB_SHADOW` tables: **`0 MUTATIONS` (UNTOUCHED)**
* `V5.29_SHADOW` tables: **`0 MUTATIONS` (UNTOUCHED)**
* **Isolation Verdict**: **`PASS`**

---

## SECTION L & M: Promotion Decision & Rationale

### Authoritative Decision: **`HOLD V5.30`**

### Exact Rationale:
1. **Historical Superiority is Proven**: V5.30 is decisively proven on the 125-session / 4,320-event fresh Period D holdout ($+0.412R$, $p = 0.0000$).
2. **Plumbing & Parity is Proven**: All parameters, 45m mechanics, capacity rules, and isolated schemas are certified.
3. **Sample Size Gate is NOT YET SATISFIED**: The actual live shadow resolved disagreement sample stands at **`N = 2`**, which is below the mandatory governance milestone of **`N >= 100`** (preferred $200–300$).
4. **Zero Fabrication Policy**: Per governance mandate, promoting V5.30 before $N \ge 100$ live disagreements is strictly prohibited. Production promotion is deferred until real-market evidence meets the sample threshold.

---

## SECTION N: Rollback Procedure (Preserved)
* In the future, upon promotion to `V5.30_DAILY_BUILDER_PRODUCTION`, rollback to `V5.25_PRODUCTION` remains instantaneous by restoring parameter version bindings from `production_parameters.db`.
