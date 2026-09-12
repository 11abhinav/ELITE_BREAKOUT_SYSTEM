# Final Daily Builder Promotion Preflight Snapshot

**Timestamp**: `2026-09-11T21:30:00+05:30`  
**Git Commit**: `bf4da25fd10028cc034d6f9fa610cefb9dd62a0e`  
**Git Branch**: `main`  
**Git Status**: Working tree active (Shadow logs and telemetry tracking)  

---

## 1. Parameter Version Snapshot (`data/production_parameters.db`)

* **Active Real-Money Production**: `V5.25_PRODUCTION` (Bound to `PARAM_CLV_V1_PROD`, `PARAM_EXTENSION_V1_PROD`, etc.)
* **Frozen Baseline Shadow**: `V5.28_DB_SHADOW` (`PARAM_DB_STRUCTURE_SCORE_V2_SHADOW`, `PARAM_DB_TIMING_SCORE_V2_SHADOW`)
* **Active 30m Live Shadow**: `V5.29_SHADOW` (`PARAM_DB_MODEL_G_SCORE_FLOOR_V1_CERTIFIED`, `PARAM_DB_EXEC_30M_HOD_TRIGGER_V1_CERTIFIED`, `PARAM_DB_EXP_FRESHNESS_LAMBDA_V1_CERTIFIED`)
* **Active 45m Challenger Shadow**: `V5.30_DB_SHADOW`
  * `PARAM_DB_45M_HOD_TRIGGER_V1_CERTIFIED` (Value: `45.0` minutes)
  * `PARAM_DB_FOCUSED_MODEL_G_CLV_WEIGHT_V1_CERTIFIED` (Value: `1.5x` CLV weight)
  * `PARAM_DB_FOCUSED_MODEL_G_COMP_WEIGHT_V1_CERTIFIED` (Value: `1.5x` Compression weight)
  * `PARAM_DB_FAILURE_VETO_REGIME_ONLY_V1_CERTIFIED` (Value: `1.0` Regime Veto)
  * `PARAM_DB_REGIME_DYNAMIC_CAPACITY_V1_CERTIFIED` (Value: `1.0` Dynamic Capacity)

---

## 2. Telemetry Row Counts (`data/shadow_telemetry.db`)

* `v529_shadow_alert_telemetry`: **7** records
* `v529_shadow_trigger_telemetry`: **5** records
* `v530_shadow_alert_telemetry`: **7** records
* `v530_shadow_trigger_telemetry`: **5** records
* `v530_vs_v529_disagreement_telemetry`: **7** paired records
* **Total Resolved Live Disagreements ($N_{\text{resolved}}$)**: **2** (`POLYCAB`, `BHARTIARTL`)

---

## 3. Preflight Governance Verdict
* **Live Disagreements**: $N_{\text{resolved}} = 2 < 100$ (Required milestone: $N \ge 100$, preferred $200–300$).
* **Preflight Gate**: **`HOLD V5.30 IN LIVE SHADOW`** — Production promotion strictly blocked pending sample accumulation.
