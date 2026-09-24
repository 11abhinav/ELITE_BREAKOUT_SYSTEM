# SYSTEM-WIDE SCANNER CERTIFICATION — FORENSIC MISMATCH REPORT

**Generated:** 2026-09-24 10:49:13 IST  
**Git Commit:** `b5c8d42cf0b34ed3612441d8a0eba88912c961ba`  
**Evaluation Date:** `2026-09-23`  
**Governance Status:** **PARTIALLY CERTIFIED** (1/13 Certified, 12 Pending / Telemetry Required)  

---

## 1. EXECUTIVE SUMMARY & FORENSIC DIAGNOSIS OF THE 20% TRACE PATTERN

In the initial Master Scanner Certification Matrix, seven scanners reported an identical **20.0% Trace Match with 100.0% Decision Match**, while others showed **20.0% Trace with 40.0%–60.0% Decision Match**. As anticipated by the governance framework, this 20.0% pattern did **not** represent partial algorithmic parity; it represented a systemic **cross-scanner telemetry collision** coupled with an **artificial self-comparison fallback**.

### The Anatomy of the 20.0% Artifact:
1. **Cross-Scanner Telemetry Collision in `find_production_record()`**:
   `ProductionReplayOrchestrator.find_production_record()` previously matched any line in `logs/scanner_telemetry.jsonl` containing the requested symbol and date without filtering by `scanner_name`. When evaluating Multi-TF, Short Covering, Reversal, Pullback, Technical, and Accumulation scanners for `PGIL`, `INDRAMEDCO`, `ACE`, and `POWERGRID`, the orchestrator matched the **EOD Breakout** production record! The replay indicators and gates of these non-EOD scanners were compared against EOD Breakout gates (`NO_ATR_EXPANSION`, `WEAK_SIGNALS`), which naturally failed 100% of trace comparisons.
2. **Synthesized Self-Comparison on Missing Telemetry**:
   For `SAMHI`, zero production telemetry existed for any scanner on 2026-09-23. The orchestrator's fallback logic called `self.adapter.evaluate(mode=PRODUCTION_REPLAY)` to synthesize an artificial reference record and then evaluated the replay adapter against itself. This produced a 100% trace match for `SAMHI` across all scanners.
3. **The 1-out-of-5 Ratio**:
   Exactly 1 symbol (`SAMHI`) passed (via artificial self-comparison), and 4 symbols failed (via cross-scanner collision with EOD Breakout). This produced **exactly 1 / 5 = 20.0% trace match** across all 12 non-EOD scanners!
4. **Decision Match Mirage (100%, 60%, 40%)**:
   In EOD Breakout, all 5 symbols were rejected (`REJECTED`). For scanners whose evaluation also happened to reject all 5 symbols (Multi-TF, Short Covering, Reversal, Pullback, Wealth, Multibagger), REJECTED == REJECTED produced **100.0% Decision Match**. For scanners where 3 symbols were rejected (Technical, Accumulation/VCP, Daily Builder), 3/5 produced **60.0% Decision Match**. For Institutional Accumulation, 2/5 produced **40.0% Decision Match**. All decision matches were completely coincidental.

### The Architectural Correction:
- **Strict Scanner Name Filtering**: `find_production_record()` now enforces alias-mapped scanner matching (`allowed_scanners`). Cross-scanner collisions are completely eliminated.
- **Mandatory Rule 9 Governance**: When production telemetry is absent, the orchestrator **never** synthesizes an artificial baseline. It emits `run_id = 'MISSING_TELEMETRY'` and marks the scanner as `STATUS = PENDING / TELEMETRY_REQUIRED` with `0.0% Trace Match` and `0.0% Decision Match`.

---

## 2. RECONCILED MASTER SCANNER CERTIFICATION MATRIX

| Scanner Family | Evaluation Mode | Prod Cases | Replay Cases | Trace Match | Decision Match | Governance Status |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| **EOD Breakout** | 1D | 5 | 5 | 100.0% | 100.0% | `CERTIFIED` |
| **Multi-TF 15M** | 15M | 5 | 5 | 0.0% | 0.0% | `PENDING / TELEMETRY_REQUIRED` |
| **Multi-TF 5M** | 5M | 5 | 5 | 0.0% | 0.0% | `PENDING / TELEMETRY_REQUIRED` |
| **Short Covering EOD** | 1D | 5 | 5 | 0.0% | 0.0% | `PENDING / TELEMETRY_REQUIRED` |
| **Short Covering 5M** | 5M | 5 | 5 | 0.0% | 0.0% | `PENDING / TELEMETRY_REQUIRED` |
| **Reversal** | 1D | 5 | 5 | 0.0% | 0.0% | `PENDING / TELEMETRY_REQUIRED` |
| **Pullback** | 1D | 5 | 5 | 0.0% | 0.0% | `PENDING / TELEMETRY_REQUIRED` |
| **Technical** | 1D | 5 | 5 | 0.0% | 0.0% | `PENDING / TELEMETRY_REQUIRED` |
| **Accumulation / VCP** | 1D | 5 | 5 | 0.0% | 0.0% | `PENDING / TELEMETRY_REQUIRED` |
| **Institutional Accumulation** | 1D | 5 | 5 | 0.0% | 0.0% | `PENDING / TELEMETRY_REQUIRED` |
| **Wealth Engine** | 1D | 5 | 5 | 0.0% | 0.0% | `PENDING / TELEMETRY_REQUIRED` |
| **Multibagger Engine** | 1D | 5 | 5 | 0.0% | 0.0% | `PENDING / TELEMETRY_REQUIRED` |
| **Daily Builder** | 1D | 5 | 5 | 0.0% | 0.0% | `PENDING / TELEMETRY_REQUIRED` |


---

## 3. PRIMARY MISMATCH TAXONOMY CLASSIFICATION

Every test case across the system is strictly classified into exactly one of the 15 standard categories without exception:

| Code | Category Name | Count | Impacted Scanners | Description |
| :--- | :--- | :---: | :--- | :--- |
| **A** | `DATA_DIFFERENCE` | 1 | EOD Breakout (Clean Replay) | Historical parquet contamination (45 rogue duplicate bars in PGIL) causing ATR expansion divergence (0.33x vs 2.13x). |
| **B** | `DATA_FRESHNESS` | 0 | None | Stale timestamp divergence. |
| **C** | `DATA_PROVIDER_DIFFERENCE` | 0 | None | Upstox vs Yahoo vs NSE Bhavcopy divergence. |
| **D** | `SYMBOL_MAPPING_DIFFERENCE` | 0 | None | Cash vs F&O futures contract mapping divergence. |
| **E** | `TIMEZONE_DIFFERENCE` | 0 | None | UTC vs Asia/Kolkata timezone divergence. |
| **F** | `POINT_IN_TIME_VIOLATION` | 0 | None | Future data lookahead ($T > t$). All parquets validated PIT-clean. |
| **G** | `CONFIG_DIFFERENCE` | 0 | None | Parameter or threshold drift. |
| **H** | `CODE_VERSION_DIFFERENCE` | 0 | None | Git commit or file SHA mismatch. |
| **I** | `CALCULATION_DIFFERENCE` | 0 | None | Mathematical indicator formula difference with identical data. |
| **J** | `GATE_LOGIC_DIFFERENCE` | 0 | None | Gate threshold or operator mismatch. |
| **K** | `STATE_DIFFERENCE` | 0 | None | Watchlist or state machine phase mismatch. |
| **L** | `DEPENDENCY_DIFFERENCE` | 0 | None | Upstream scanner output mismatch. |
| **M** | `POLLING/LIFECYCLE_DIFFERENCE` | 0 | None | Polling timestamp or lifecycle progression mismatch. |
| **N** | `MISSING_TELEMETRY` | 60 | 12 Non-EOD Scanner Families | Zero production telemetry records logged in `logs/scanner_telemetry.jsonl` for evaluation date. |
| **O** | `UNKNOWN` | 0 | None | Insufficient telemetry to categorize. |

---

## 4. DEEP FORENSIC INVESTIGATION BY SCANNER FAMILY

### 4.1 EOD Breakout Scanner (CERTIFIED — 100% Trace / 100% Decision)
- **Production Run ID**: `c1f7a76e-ae32-41a7-93fd-ce5756da9ae9`
- **Verified Symbols**: `PGIL`, `HBLENGINE`, `INDRAMEDCO`, `ACE`, `POWERGRID`
- **Findings**:
  - In `PRODUCTION_REPLAY` mode, using the historical parquet backup (`data/history/1d/PGIL.parquet.corrupt_bak`), replay reproduces ATR20 = ₹366.3748, candle range = ₹119.80, and ATR expansion = 0.327. Gate `NO_ATR_EXPANSION` fails with identical reason `NO_ATR_EXPANSION_FAIL`. Trace Match = 100%, Decision Match = 100%.
  - For `HBLENGINE`, `INDRAMEDCO`, `ACE`, and `POWERGRID`, ATR expansion >= 0.80 passes, but `detect_breakouts()` returns 0 signals (`len(signals) < 1`), failing gate `WEAK_SIGNALS` with identical reason `WEAK_SIGNALS`. Trace Match = 100%, Decision Match = 100%.
  - In `CLEAN_HISTORICAL_REPLAY` mode, PGIL evaluates against sanitized historical data where ATR20 = ₹56.35. ATR expansion becomes 2.126x (> 0.80 PASS), and the symbol advances to `BASE_TIGHTNESS`, proving that clean backtesting correctly diverges from corrupted production execution.

### 4.2 Multi-Timeframe Scanners (Multi-TF 15M & Multi-TF 5M)
- **Status**: `PENDING / TELEMETRY_REQUIRED`
- **Primary Category**: `MISSING_TELEMETRY`
- **Findings**:
  - Multi-TF adapter models the complete intraday progression: 15M trigger -> 30M trend -> 1H base -> candidate state -> 5M polling -> 5M confirmation -> ENTRY_READY -> SL -> Target -> R -> final alert.
  - Audit of `logs/scanner_telemetry.jsonl` revealed that `MULTI_TF` only contains 45 historical records for test symbols (`TEST_WEEKEND`, `TEST_EXHAUSTION`, `TEST_CLEAN_VOL`) logged on 2026-09-09 and 2026-09-10. Zero production records exist for live symbols on 2026-09-23.
  - Required Action: Run Multi-TF in shadow mode during market hours (or next trading session) with `ScannerDecisionLogger('MULTI_TF', ...)` active across all ladder stages to populate baseline telemetry.

### 4.3 Short Covering Scanners (Short Covering EOD & Short Covering 5M)
- **Status**: `PENDING / TELEMETRY_REQUIRED`
- **Primary Category**: `MISSING_TELEMETRY`
- **Findings**:
  - Adapter verifies F&O universe eligibility, futures contract mapping, OI health, and data sufficiency (`DATA_INSUFFICIENT` / `BLOCKED` states).
  - Zero production telemetry records exist in `logs/scanner_telemetry.jsonl` for `SHORT_COVERING_EOD` or `SHORT_COVERING_5M`.
  - Required Action: Wire `ScannerDecisionLogger` into `short_covering_scanner.py` and `short_covering_5m.py` to record exact contract snapshots and OI history.

### 4.4 Reversal & Pullback Scanners
- **Status**: `PENDING / TELEMETRY_REQUIRED`
- **Primary Category**: `MISSING_TELEMETRY`
- **Findings**:
  - While `reversal_scanner.py` and `pullback_pipeline.py` import `ScannerDecisionLogger`, no scan runs were committed to `logs/scanner_telemetry.jsonl` for date 2026-09-23.
  - Replay adapters correctly consume upstream states and evaluate RSI recovery, exhaustion volume, and EMA20 value zones, but cannot certify against non-existent production logs.

### 4.5 Technical, Accumulation/VCP, Institutional Accumulation, Wealth, Multibagger, Daily Builder
- **Status**: `PENDING / TELEMETRY_REQUIRED`
- **Primary Category**: `MISSING_TELEMETRY`
- **Findings**:
  - In the previous report, these scanners falsely reported 40%–60% decision matches due to comparing against EOD Breakout telemetry. With cross-scanner filtering strictly enforced, the difference engine truthfully flags `MISSING_TELEMETRY`.
  - `MULTIBAGGER` and `WEALTH_ENGINE` have production records for earlier dates (2026-09-05), but lack comprehensive multi-gate traces for the 2026-09-23 target session.

---

## 5. ADAPTER EXECUTION INTEGRITY PROOF

To satisfy Section 8 of the certification mandate, every scanner adapter's execution binding has been verified from code provenance:

| Scanner Family | Adapter Module | Production Target Function | Source File | Git Commit | File SHA256 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **EOD Breakout** | `certification.adapters.eod_scanner_adapter` | `eod_scanner.evaluate_symbol / _check_eod_conditions` | `app/eod_scanner.py` | `b5c8d42c` | `b8b209e00bcb73a3...` |
| **EOD Breakout** | `certification.adapters.eod_scanner_adapter` | `eod_scanner.evaluate_symbol / _check_eod_conditions` | `app/eod_scanner.py` | `b5c8d42c` | `b8b209e00bcb73a3...` |
| **EOD Breakout** | `certification.adapters.eod_scanner_adapter` | `eod_scanner.evaluate_symbol / _check_eod_conditions` | `app/eod_scanner.py` | `b5c8d42c` | `b8b209e00bcb73a3...` |
| **EOD Breakout** | `certification.adapters.eod_scanner_adapter` | `eod_scanner.evaluate_symbol / _check_eod_conditions` | `app/eod_scanner.py` | `b5c8d42c` | `b8b209e00bcb73a3...` |
| **EOD Breakout** | `certification.adapters.eod_scanner_adapter` | `eod_scanner.evaluate_symbol / _check_eod_conditions` | `app/eod_scanner.py` | `b5c8d42c` | `b8b209e00bcb73a3...` |
| **Multi-TF 15M** | `certification.adapters.multi_tf_scanner_adapter` | `multi_tf_scanner.run_scanner_cycle / evaluate_multitf` | `app/multi_tf_scanner.py` | `b5c8d42c` | `24ffc365520a15ca...` |
| **Multi-TF 15M** | `certification.adapters.multi_tf_scanner_adapter` | `multi_tf_scanner.run_scanner_cycle / evaluate_multitf` | `app/multi_tf_scanner.py` | `b5c8d42c` | `24ffc365520a15ca...` |
| **Multi-TF 15M** | `certification.adapters.multi_tf_scanner_adapter` | `multi_tf_scanner.run_scanner_cycle / evaluate_multitf` | `app/multi_tf_scanner.py` | `b5c8d42c` | `24ffc365520a15ca...` |
| **Multi-TF 15M** | `certification.adapters.multi_tf_scanner_adapter` | `multi_tf_scanner.run_scanner_cycle / evaluate_multitf` | `app/multi_tf_scanner.py` | `b5c8d42c` | `24ffc365520a15ca...` |
| **Multi-TF 15M** | `certification.adapters.multi_tf_scanner_adapter` | `multi_tf_scanner.run_scanner_cycle / evaluate_multitf` | `app/multi_tf_scanner.py` | `b5c8d42c` | `24ffc365520a15ca...` |
| **Multi-TF 5M** | `certification.adapters.multi_tf_scanner_adapter` | `multi_tf_scanner.poll_5m_confirmation` | `app/multi_tf_scanner.py` | `b5c8d42c` | `24ffc365520a15ca...` |
| **Multi-TF 5M** | `certification.adapters.multi_tf_scanner_adapter` | `multi_tf_scanner.poll_5m_confirmation` | `app/multi_tf_scanner.py` | `b5c8d42c` | `24ffc365520a15ca...` |
| **Multi-TF 5M** | `certification.adapters.multi_tf_scanner_adapter` | `multi_tf_scanner.poll_5m_confirmation` | `app/multi_tf_scanner.py` | `b5c8d42c` | `24ffc365520a15ca...` |


---

## 6. COMPLETE CASE-BY-CASE FORENSIC AUDIT LEDGER

### Case 1: EOD Breakout — PGIL (2026-09-23)
- **SCANNER**: `EOD`
- **MODE**: `1D`
- **SYMBOL**: `PGIL`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `c1f7a76e-ae32-41a7-93fd-ce5756da9ae9`
- **REPLAY RUN ID**: `replay_EOD_PGIL_2026-09-23`
- **FIRST DIVERGENCE**: `NONE`
- **ROOT INPUT DIVERGENCE**: `None (Full Trace Equivalence)`
- **DOWNSTREAM IMPACT**: `NONE`
- **FINAL DECISION**: `CERTIFIED`
- **REJECTION REASON**: `NO_ATR_EXPANSION_FAIL`
- **PRIMARY CATEGORY**: `NONE`
- **LIKELY ROOT CAUSE**: EXACT_REPRODUCIBILITY_VERIFIED: Production and replay execution traces, indicators, gates, and decisions are 100% equivalent.
- **EVIDENCE**: All 37 field comparisons matched within strict numerical and categorical tolerances. Replay exactly follows production.

### Case 2: EOD Breakout — HBLENGINE (2026-09-23)
- **SCANNER**: `EOD`
- **MODE**: `1D`
- **SYMBOL**: `HBLENGINE`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `c1f7a76e-ae32-41a7-93fd-ce5756da9ae9`
- **REPLAY RUN ID**: `replay_EOD_HBLENGINE_2026-09-23`
- **FIRST DIVERGENCE**: `NONE`
- **ROOT INPUT DIVERGENCE**: `None (Full Trace Equivalence)`
- **DOWNSTREAM IMPACT**: `NONE`
- **FINAL DECISION**: `CERTIFIED`
- **REJECTION REASON**: `WEAK_SIGNALS`
- **PRIMARY CATEGORY**: `NONE`
- **LIKELY ROOT CAUSE**: EXACT_REPRODUCIBILITY_VERIFIED: Production and replay execution traces, indicators, gates, and decisions are 100% equivalent.
- **EVIDENCE**: All 37 field comparisons matched within strict numerical and categorical tolerances. Replay exactly follows production.

### Case 3: EOD Breakout — INDRAMEDCO (2026-09-23)
- **SCANNER**: `EOD`
- **MODE**: `1D`
- **SYMBOL**: `INDRAMEDCO`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `c1f7a76e-ae32-41a7-93fd-ce5756da9ae9`
- **REPLAY RUN ID**: `replay_EOD_INDRAMEDCO_2026-09-23`
- **FIRST DIVERGENCE**: `NONE`
- **ROOT INPUT DIVERGENCE**: `None (Full Trace Equivalence)`
- **DOWNSTREAM IMPACT**: `NONE`
- **FINAL DECISION**: `CERTIFIED`
- **REJECTION REASON**: `WEAK_SIGNALS`
- **PRIMARY CATEGORY**: `NONE`
- **LIKELY ROOT CAUSE**: EXACT_REPRODUCIBILITY_VERIFIED: Production and replay execution traces, indicators, gates, and decisions are 100% equivalent.
- **EVIDENCE**: All 37 field comparisons matched within strict numerical and categorical tolerances. Replay exactly follows production.

### Case 4: EOD Breakout — ACE (2026-09-23)
- **SCANNER**: `EOD`
- **MODE**: `1D`
- **SYMBOL**: `ACE`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `c1f7a76e-ae32-41a7-93fd-ce5756da9ae9`
- **REPLAY RUN ID**: `replay_EOD_ACE_2026-09-23`
- **FIRST DIVERGENCE**: `NONE`
- **ROOT INPUT DIVERGENCE**: `None (Full Trace Equivalence)`
- **DOWNSTREAM IMPACT**: `NONE`
- **FINAL DECISION**: `CERTIFIED`
- **REJECTION REASON**: `WEAK_SIGNALS`
- **PRIMARY CATEGORY**: `NONE`
- **LIKELY ROOT CAUSE**: EXACT_REPRODUCIBILITY_VERIFIED: Production and replay execution traces, indicators, gates, and decisions are 100% equivalent.
- **EVIDENCE**: All 37 field comparisons matched within strict numerical and categorical tolerances. Replay exactly follows production.

### Case 5: EOD Breakout — POWERGRID (2026-09-23)
- **SCANNER**: `EOD`
- **MODE**: `1D`
- **SYMBOL**: `POWERGRID`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `c1f7a76e-ae32-41a7-93fd-ce5756da9ae9`
- **REPLAY RUN ID**: `replay_EOD_POWERGRID_2026-09-23`
- **FIRST DIVERGENCE**: `NONE`
- **ROOT INPUT DIVERGENCE**: `None (Full Trace Equivalence)`
- **DOWNSTREAM IMPACT**: `NONE`
- **FINAL DECISION**: `CERTIFIED`
- **REJECTION REASON**: `WEAK_SIGNALS`
- **PRIMARY CATEGORY**: `NONE`
- **LIKELY ROOT CAUSE**: EXACT_REPRODUCIBILITY_VERIFIED: Production and replay execution traces, indicators, gates, and decisions are 100% equivalent.
- **EVIDENCE**: All 37 field comparisons matched within strict numerical and categorical tolerances. Replay exactly follows production.

### Case 6: Multi-TF 15M — PGIL (2026-09-23)
- **SCANNER**: `MULTITF_15M`
- **MODE**: `15M`
- **SYMBOL**: `PGIL`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_MULTITF_15M_PGIL_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'MULTITF_15M' on symbol 'PGIL' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'MULTITF_15M' did not log production telemetry records for symbol 'PGIL' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['MULTI_TF', 'MULTITF_15M', 'MULTITF'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 7: Multi-TF 15M — SAMHI (2026-09-23)
- **SCANNER**: `MULTITF_15M`
- **MODE**: `15M`
- **SYMBOL**: `SAMHI`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_MULTITF_15M_SAMHI_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'MULTITF_15M' on symbol 'SAMHI' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'MULTITF_15M' did not log production telemetry records for symbol 'SAMHI' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['MULTI_TF', 'MULTITF_15M', 'MULTITF'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 8: Multi-TF 15M — INDRAMEDCO (2026-09-23)
- **SCANNER**: `MULTITF_15M`
- **MODE**: `15M`
- **SYMBOL**: `INDRAMEDCO`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_MULTITF_15M_INDRAMEDCO_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'MULTITF_15M' on symbol 'INDRAMEDCO' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'MULTITF_15M' did not log production telemetry records for symbol 'INDRAMEDCO' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['MULTI_TF', 'MULTITF_15M', 'MULTITF'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 9: Multi-TF 15M — ACE (2026-09-23)
- **SCANNER**: `MULTITF_15M`
- **MODE**: `15M`
- **SYMBOL**: `ACE`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_MULTITF_15M_ACE_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'MULTITF_15M' on symbol 'ACE' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'MULTITF_15M' did not log production telemetry records for symbol 'ACE' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['MULTI_TF', 'MULTITF_15M', 'MULTITF'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 10: Multi-TF 15M — POWERGRID (2026-09-23)
- **SCANNER**: `MULTITF_15M`
- **MODE**: `15M`
- **SYMBOL**: `POWERGRID`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_MULTITF_15M_POWERGRID_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'MULTITF_15M' on symbol 'POWERGRID' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'MULTITF_15M' did not log production telemetry records for symbol 'POWERGRID' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['MULTI_TF', 'MULTITF_15M', 'MULTITF'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 11: Multi-TF 5M — PGIL (2026-09-23)
- **SCANNER**: `MULTITF_5M`
- **MODE**: `5M`
- **SYMBOL**: `PGIL`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_MULTITF_5M_PGIL_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'MULTITF_5M' on symbol 'PGIL' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'MULTITF_5M' did not log production telemetry records for symbol 'PGIL' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['MULTI_TF', 'MULTITF_5M'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 12: Multi-TF 5M — SAMHI (2026-09-23)
- **SCANNER**: `MULTITF_5M`
- **MODE**: `5M`
- **SYMBOL**: `SAMHI`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_MULTITF_5M_SAMHI_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'MULTITF_5M' on symbol 'SAMHI' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'MULTITF_5M' did not log production telemetry records for symbol 'SAMHI' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['MULTI_TF', 'MULTITF_5M'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 13: Multi-TF 5M — INDRAMEDCO (2026-09-23)
- **SCANNER**: `MULTITF_5M`
- **MODE**: `5M`
- **SYMBOL**: `INDRAMEDCO`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_MULTITF_5M_INDRAMEDCO_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'MULTITF_5M' on symbol 'INDRAMEDCO' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'MULTITF_5M' did not log production telemetry records for symbol 'INDRAMEDCO' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['MULTI_TF', 'MULTITF_5M'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 14: Multi-TF 5M — ACE (2026-09-23)
- **SCANNER**: `MULTITF_5M`
- **MODE**: `5M`
- **SYMBOL**: `ACE`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_MULTITF_5M_ACE_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'MULTITF_5M' on symbol 'ACE' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'MULTITF_5M' did not log production telemetry records for symbol 'ACE' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['MULTI_TF', 'MULTITF_5M'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 15: Multi-TF 5M — POWERGRID (2026-09-23)
- **SCANNER**: `MULTITF_5M`
- **MODE**: `5M`
- **SYMBOL**: `POWERGRID`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_MULTITF_5M_POWERGRID_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'MULTITF_5M' on symbol 'POWERGRID' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'MULTITF_5M' did not log production telemetry records for symbol 'POWERGRID' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['MULTI_TF', 'MULTITF_5M'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 16: Short Covering EOD — PGIL (2026-09-23)
- **SCANNER**: `SHORT_COVERING_EOD`
- **MODE**: `1D`
- **SYMBOL**: `PGIL`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_SHORT_COVERING_EOD_PGIL_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'SHORT_COVERING_EOD' on symbol 'PGIL' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'SHORT_COVERING_EOD' did not log production telemetry records for symbol 'PGIL' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['SHORT_COVERING_EOD', 'SHORT_COVERING'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 17: Short Covering EOD — SAMHI (2026-09-23)
- **SCANNER**: `SHORT_COVERING_EOD`
- **MODE**: `1D`
- **SYMBOL**: `SAMHI`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_SHORT_COVERING_EOD_SAMHI_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'SHORT_COVERING_EOD' on symbol 'SAMHI' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'SHORT_COVERING_EOD' did not log production telemetry records for symbol 'SAMHI' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['SHORT_COVERING_EOD', 'SHORT_COVERING'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 18: Short Covering EOD — INDRAMEDCO (2026-09-23)
- **SCANNER**: `SHORT_COVERING_EOD`
- **MODE**: `1D`
- **SYMBOL**: `INDRAMEDCO`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_SHORT_COVERING_EOD_INDRAMEDCO_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'SHORT_COVERING_EOD' on symbol 'INDRAMEDCO' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'SHORT_COVERING_EOD' did not log production telemetry records for symbol 'INDRAMEDCO' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['SHORT_COVERING_EOD', 'SHORT_COVERING'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 19: Short Covering EOD — ACE (2026-09-23)
- **SCANNER**: `SHORT_COVERING_EOD`
- **MODE**: `1D`
- **SYMBOL**: `ACE`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_SHORT_COVERING_EOD_ACE_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'SHORT_COVERING_EOD' on symbol 'ACE' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'SHORT_COVERING_EOD' did not log production telemetry records for symbol 'ACE' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['SHORT_COVERING_EOD', 'SHORT_COVERING'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 20: Short Covering EOD — POWERGRID (2026-09-23)
- **SCANNER**: `SHORT_COVERING_EOD`
- **MODE**: `1D`
- **SYMBOL**: `POWERGRID`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_SHORT_COVERING_EOD_POWERGRID_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'SHORT_COVERING_EOD' on symbol 'POWERGRID' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'SHORT_COVERING_EOD' did not log production telemetry records for symbol 'POWERGRID' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['SHORT_COVERING_EOD', 'SHORT_COVERING'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 21: Short Covering 5M — PGIL (2026-09-23)
- **SCANNER**: `SHORT_COVERING_5M`
- **MODE**: `5M`
- **SYMBOL**: `PGIL`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_SHORT_COVERING_5M_PGIL_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'SHORT_COVERING_5M' on symbol 'PGIL' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'SHORT_COVERING_5M' did not log production telemetry records for symbol 'PGIL' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['SHORT_COVERING_5M'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 22: Short Covering 5M — SAMHI (2026-09-23)
- **SCANNER**: `SHORT_COVERING_5M`
- **MODE**: `5M`
- **SYMBOL**: `SAMHI`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_SHORT_COVERING_5M_SAMHI_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'SHORT_COVERING_5M' on symbol 'SAMHI' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'SHORT_COVERING_5M' did not log production telemetry records for symbol 'SAMHI' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['SHORT_COVERING_5M'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 23: Short Covering 5M — INDRAMEDCO (2026-09-23)
- **SCANNER**: `SHORT_COVERING_5M`
- **MODE**: `5M`
- **SYMBOL**: `INDRAMEDCO`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_SHORT_COVERING_5M_INDRAMEDCO_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'SHORT_COVERING_5M' on symbol 'INDRAMEDCO' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'SHORT_COVERING_5M' did not log production telemetry records for symbol 'INDRAMEDCO' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['SHORT_COVERING_5M'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 24: Short Covering 5M — ACE (2026-09-23)
- **SCANNER**: `SHORT_COVERING_5M`
- **MODE**: `5M`
- **SYMBOL**: `ACE`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_SHORT_COVERING_5M_ACE_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'SHORT_COVERING_5M' on symbol 'ACE' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'SHORT_COVERING_5M' did not log production telemetry records for symbol 'ACE' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['SHORT_COVERING_5M'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 25: Short Covering 5M — POWERGRID (2026-09-23)
- **SCANNER**: `SHORT_COVERING_5M`
- **MODE**: `5M`
- **SYMBOL**: `POWERGRID`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_SHORT_COVERING_5M_POWERGRID_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'SHORT_COVERING_5M' on symbol 'POWERGRID' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'SHORT_COVERING_5M' did not log production telemetry records for symbol 'POWERGRID' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['SHORT_COVERING_5M'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 26: Reversal — PGIL (2026-09-23)
- **SCANNER**: `REVERSAL`
- **MODE**: `1D`
- **SYMBOL**: `PGIL`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_REVERSAL_PGIL_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'REVERSAL' on symbol 'PGIL' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'REVERSAL' did not log production telemetry records for symbol 'PGIL' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['REVERSAL', 'REVERSAL_V2'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 27: Reversal — SAMHI (2026-09-23)
- **SCANNER**: `REVERSAL`
- **MODE**: `1D`
- **SYMBOL**: `SAMHI`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_REVERSAL_SAMHI_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'REVERSAL' on symbol 'SAMHI' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'REVERSAL' did not log production telemetry records for symbol 'SAMHI' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['REVERSAL', 'REVERSAL_V2'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 28: Reversal — INDRAMEDCO (2026-09-23)
- **SCANNER**: `REVERSAL`
- **MODE**: `1D`
- **SYMBOL**: `INDRAMEDCO`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_REVERSAL_INDRAMEDCO_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'REVERSAL' on symbol 'INDRAMEDCO' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'REVERSAL' did not log production telemetry records for symbol 'INDRAMEDCO' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['REVERSAL', 'REVERSAL_V2'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 29: Reversal — ACE (2026-09-23)
- **SCANNER**: `REVERSAL`
- **MODE**: `1D`
- **SYMBOL**: `ACE`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_REVERSAL_ACE_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'REVERSAL' on symbol 'ACE' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'REVERSAL' did not log production telemetry records for symbol 'ACE' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['REVERSAL', 'REVERSAL_V2'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 30: Reversal — POWERGRID (2026-09-23)
- **SCANNER**: `REVERSAL`
- **MODE**: `1D`
- **SYMBOL**: `POWERGRID`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_REVERSAL_POWERGRID_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'REVERSAL' on symbol 'POWERGRID' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'REVERSAL' did not log production telemetry records for symbol 'POWERGRID' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['REVERSAL', 'REVERSAL_V2'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 31: Pullback — PGIL (2026-09-23)
- **SCANNER**: `PULLBACK`
- **MODE**: `1D`
- **SYMBOL**: `PGIL`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_PULLBACK_PGIL_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'PULLBACK' on symbol 'PGIL' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'PULLBACK' did not log production telemetry records for symbol 'PGIL' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['PULLBACK'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 32: Pullback — SAMHI (2026-09-23)
- **SCANNER**: `PULLBACK`
- **MODE**: `1D`
- **SYMBOL**: `SAMHI`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_PULLBACK_SAMHI_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'PULLBACK' on symbol 'SAMHI' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'PULLBACK' did not log production telemetry records for symbol 'SAMHI' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['PULLBACK'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 33: Pullback — INDRAMEDCO (2026-09-23)
- **SCANNER**: `PULLBACK`
- **MODE**: `1D`
- **SYMBOL**: `INDRAMEDCO`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_PULLBACK_INDRAMEDCO_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'PULLBACK' on symbol 'INDRAMEDCO' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'PULLBACK' did not log production telemetry records for symbol 'INDRAMEDCO' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['PULLBACK'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 34: Pullback — ACE (2026-09-23)
- **SCANNER**: `PULLBACK`
- **MODE**: `1D`
- **SYMBOL**: `ACE`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_PULLBACK_ACE_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'PULLBACK' on symbol 'ACE' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'PULLBACK' did not log production telemetry records for symbol 'ACE' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['PULLBACK'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 35: Pullback — POWERGRID (2026-09-23)
- **SCANNER**: `PULLBACK`
- **MODE**: `1D`
- **SYMBOL**: `POWERGRID`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_PULLBACK_POWERGRID_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'PULLBACK' on symbol 'POWERGRID' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'PULLBACK' did not log production telemetry records for symbol 'POWERGRID' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['PULLBACK'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 36: Technical — PGIL (2026-09-23)
- **SCANNER**: `TECHNICAL`
- **MODE**: `1D`
- **SYMBOL**: `PGIL`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_TECHNICAL_PGIL_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'TECHNICAL' on symbol 'PGIL' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'TECHNICAL' did not log production telemetry records for symbol 'PGIL' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['TECHNICAL'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 37: Technical — SAMHI (2026-09-23)
- **SCANNER**: `TECHNICAL`
- **MODE**: `1D`
- **SYMBOL**: `SAMHI`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_TECHNICAL_SAMHI_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'TECHNICAL' on symbol 'SAMHI' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'TECHNICAL' did not log production telemetry records for symbol 'SAMHI' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['TECHNICAL'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 38: Technical — INDRAMEDCO (2026-09-23)
- **SCANNER**: `TECHNICAL`
- **MODE**: `1D`
- **SYMBOL**: `INDRAMEDCO`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_TECHNICAL_INDRAMEDCO_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'TECHNICAL' on symbol 'INDRAMEDCO' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'TECHNICAL' did not log production telemetry records for symbol 'INDRAMEDCO' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['TECHNICAL'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 39: Technical — ACE (2026-09-23)
- **SCANNER**: `TECHNICAL`
- **MODE**: `1D`
- **SYMBOL**: `ACE`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_TECHNICAL_ACE_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'TECHNICAL' on symbol 'ACE' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'TECHNICAL' did not log production telemetry records for symbol 'ACE' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['TECHNICAL'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 40: Technical — POWERGRID (2026-09-23)
- **SCANNER**: `TECHNICAL`
- **MODE**: `1D`
- **SYMBOL**: `POWERGRID`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_TECHNICAL_POWERGRID_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'TECHNICAL' on symbol 'POWERGRID' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'TECHNICAL' did not log production telemetry records for symbol 'POWERGRID' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['TECHNICAL'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 41: Accumulation / VCP — PGIL (2026-09-23)
- **SCANNER**: `ACCUMULATION_VCP`
- **MODE**: `1D`
- **SYMBOL**: `PGIL`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_ACCUMULATION_VCP_PGIL_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'ACCUMULATION_VCP' on symbol 'PGIL' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'ACCUMULATION_VCP' did not log production telemetry records for symbol 'PGIL' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['ACCUMULATION_VCP', 'VCP'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 42: Accumulation / VCP — SAMHI (2026-09-23)
- **SCANNER**: `ACCUMULATION_VCP`
- **MODE**: `1D`
- **SYMBOL**: `SAMHI`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_ACCUMULATION_VCP_SAMHI_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'ACCUMULATION_VCP' on symbol 'SAMHI' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'ACCUMULATION_VCP' did not log production telemetry records for symbol 'SAMHI' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['ACCUMULATION_VCP', 'VCP'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 43: Accumulation / VCP — INDRAMEDCO (2026-09-23)
- **SCANNER**: `ACCUMULATION_VCP`
- **MODE**: `1D`
- **SYMBOL**: `INDRAMEDCO`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_ACCUMULATION_VCP_INDRAMEDCO_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'ACCUMULATION_VCP' on symbol 'INDRAMEDCO' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'ACCUMULATION_VCP' did not log production telemetry records for symbol 'INDRAMEDCO' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['ACCUMULATION_VCP', 'VCP'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 44: Accumulation / VCP — ACE (2026-09-23)
- **SCANNER**: `ACCUMULATION_VCP`
- **MODE**: `1D`
- **SYMBOL**: `ACE`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_ACCUMULATION_VCP_ACE_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'ACCUMULATION_VCP' on symbol 'ACE' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'ACCUMULATION_VCP' did not log production telemetry records for symbol 'ACE' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['ACCUMULATION_VCP', 'VCP'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 45: Accumulation / VCP — POWERGRID (2026-09-23)
- **SCANNER**: `ACCUMULATION_VCP`
- **MODE**: `1D`
- **SYMBOL**: `POWERGRID`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_ACCUMULATION_VCP_POWERGRID_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'ACCUMULATION_VCP' on symbol 'POWERGRID' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'ACCUMULATION_VCP' did not log production telemetry records for symbol 'POWERGRID' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['ACCUMULATION_VCP', 'VCP'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 46: Institutional Accumulation — PGIL (2026-09-23)
- **SCANNER**: `INSTITUTIONAL_ACCUMULATION`
- **MODE**: `1D`
- **SYMBOL**: `PGIL`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_INSTITUTIONAL_ACCUMULATION_PGIL_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'INSTITUTIONAL_ACCUMULATION' on symbol 'PGIL' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'INSTITUTIONAL_ACCUMULATION' did not log production telemetry records for symbol 'PGIL' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['INSTITUTIONAL_ACCUMULATION', 'ACCUMULATION'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 47: Institutional Accumulation — SAMHI (2026-09-23)
- **SCANNER**: `INSTITUTIONAL_ACCUMULATION`
- **MODE**: `1D`
- **SYMBOL**: `SAMHI`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_INSTITUTIONAL_ACCUMULATION_SAMHI_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'INSTITUTIONAL_ACCUMULATION' on symbol 'SAMHI' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'INSTITUTIONAL_ACCUMULATION' did not log production telemetry records for symbol 'SAMHI' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['INSTITUTIONAL_ACCUMULATION', 'ACCUMULATION'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 48: Institutional Accumulation — INDRAMEDCO (2026-09-23)
- **SCANNER**: `INSTITUTIONAL_ACCUMULATION`
- **MODE**: `1D`
- **SYMBOL**: `INDRAMEDCO`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_INSTITUTIONAL_ACCUMULATION_INDRAMEDCO_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'INSTITUTIONAL_ACCUMULATION' on symbol 'INDRAMEDCO' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'INSTITUTIONAL_ACCUMULATION' did not log production telemetry records for symbol 'INDRAMEDCO' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['INSTITUTIONAL_ACCUMULATION', 'ACCUMULATION'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 49: Institutional Accumulation — ACE (2026-09-23)
- **SCANNER**: `INSTITUTIONAL_ACCUMULATION`
- **MODE**: `1D`
- **SYMBOL**: `ACE`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_INSTITUTIONAL_ACCUMULATION_ACE_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'INSTITUTIONAL_ACCUMULATION' on symbol 'ACE' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'INSTITUTIONAL_ACCUMULATION' did not log production telemetry records for symbol 'ACE' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['INSTITUTIONAL_ACCUMULATION', 'ACCUMULATION'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 50: Institutional Accumulation — POWERGRID (2026-09-23)
- **SCANNER**: `INSTITUTIONAL_ACCUMULATION`
- **MODE**: `1D`
- **SYMBOL**: `POWERGRID`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_INSTITUTIONAL_ACCUMULATION_POWERGRID_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'INSTITUTIONAL_ACCUMULATION' on symbol 'POWERGRID' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'INSTITUTIONAL_ACCUMULATION' did not log production telemetry records for symbol 'POWERGRID' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['INSTITUTIONAL_ACCUMULATION', 'ACCUMULATION'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 51: Wealth Engine — PGIL (2026-09-23)
- **SCANNER**: `WEALTH`
- **MODE**: `1D`
- **SYMBOL**: `PGIL`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_WEALTH_PGIL_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'WEALTH' on symbol 'PGIL' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'WEALTH' did not log production telemetry records for symbol 'PGIL' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['WEALTH_ENGINE', 'WEALTH'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 52: Wealth Engine — SAMHI (2026-09-23)
- **SCANNER**: `WEALTH`
- **MODE**: `1D`
- **SYMBOL**: `SAMHI`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_WEALTH_SAMHI_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'WEALTH' on symbol 'SAMHI' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'WEALTH' did not log production telemetry records for symbol 'SAMHI' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['WEALTH_ENGINE', 'WEALTH'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 53: Wealth Engine — INDRAMEDCO (2026-09-23)
- **SCANNER**: `WEALTH`
- **MODE**: `1D`
- **SYMBOL**: `INDRAMEDCO`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_WEALTH_INDRAMEDCO_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'WEALTH' on symbol 'INDRAMEDCO' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'WEALTH' did not log production telemetry records for symbol 'INDRAMEDCO' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['WEALTH_ENGINE', 'WEALTH'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 54: Wealth Engine — ACE (2026-09-23)
- **SCANNER**: `WEALTH`
- **MODE**: `1D`
- **SYMBOL**: `ACE`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_WEALTH_ACE_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'WEALTH' on symbol 'ACE' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'WEALTH' did not log production telemetry records for symbol 'ACE' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['WEALTH_ENGINE', 'WEALTH'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 55: Wealth Engine — POWERGRID (2026-09-23)
- **SCANNER**: `WEALTH`
- **MODE**: `1D`
- **SYMBOL**: `POWERGRID`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_WEALTH_POWERGRID_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'WEALTH' on symbol 'POWERGRID' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'WEALTH' did not log production telemetry records for symbol 'POWERGRID' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['WEALTH_ENGINE', 'WEALTH'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 56: Multibagger Engine — PGIL (2026-09-23)
- **SCANNER**: `MULTIBAGGER`
- **MODE**: `1D`
- **SYMBOL**: `PGIL`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_MULTIBAGGER_PGIL_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'MULTIBAGGER' on symbol 'PGIL' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'MULTIBAGGER' did not log production telemetry records for symbol 'PGIL' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['MULTIBAGGER'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 57: Multibagger Engine — SAMHI (2026-09-23)
- **SCANNER**: `MULTIBAGGER`
- **MODE**: `1D`
- **SYMBOL**: `SAMHI`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_MULTIBAGGER_SAMHI_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'MULTIBAGGER' on symbol 'SAMHI' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'MULTIBAGGER' did not log production telemetry records for symbol 'SAMHI' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['MULTIBAGGER'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 58: Multibagger Engine — INDRAMEDCO (2026-09-23)
- **SCANNER**: `MULTIBAGGER`
- **MODE**: `1D`
- **SYMBOL**: `INDRAMEDCO`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_MULTIBAGGER_INDRAMEDCO_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'MULTIBAGGER' on symbol 'INDRAMEDCO' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'MULTIBAGGER' did not log production telemetry records for symbol 'INDRAMEDCO' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['MULTIBAGGER'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 59: Multibagger Engine — ACE (2026-09-23)
- **SCANNER**: `MULTIBAGGER`
- **MODE**: `1D`
- **SYMBOL**: `ACE`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_MULTIBAGGER_ACE_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'MULTIBAGGER' on symbol 'ACE' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'MULTIBAGGER' did not log production telemetry records for symbol 'ACE' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['MULTIBAGGER'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 60: Multibagger Engine — POWERGRID (2026-09-23)
- **SCANNER**: `MULTIBAGGER`
- **MODE**: `1D`
- **SYMBOL**: `POWERGRID`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_MULTIBAGGER_POWERGRID_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'MULTIBAGGER' on symbol 'POWERGRID' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'MULTIBAGGER' did not log production telemetry records for symbol 'POWERGRID' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['MULTIBAGGER'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 61: Daily Builder — PGIL (2026-09-23)
- **SCANNER**: `DAILY_BUILDER`
- **MODE**: `1D`
- **SYMBOL**: `PGIL`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_DAILY_BUILDER_PGIL_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'DAILY_BUILDER' on symbol 'PGIL' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'DAILY_BUILDER' did not log production telemetry records for symbol 'PGIL' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['DAILY_BUILDER', 'BUILDER'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 62: Daily Builder — SAMHI (2026-09-23)
- **SCANNER**: `DAILY_BUILDER`
- **MODE**: `1D`
- **SYMBOL**: `SAMHI`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_DAILY_BUILDER_SAMHI_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'DAILY_BUILDER' on symbol 'SAMHI' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'DAILY_BUILDER' did not log production telemetry records for symbol 'SAMHI' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['DAILY_BUILDER', 'BUILDER'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 63: Daily Builder — INDRAMEDCO (2026-09-23)
- **SCANNER**: `DAILY_BUILDER`
- **MODE**: `1D`
- **SYMBOL**: `INDRAMEDCO`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_DAILY_BUILDER_INDRAMEDCO_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'DAILY_BUILDER' on symbol 'INDRAMEDCO' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'DAILY_BUILDER' did not log production telemetry records for symbol 'INDRAMEDCO' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['DAILY_BUILDER', 'BUILDER'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 64: Daily Builder — ACE (2026-09-23)
- **SCANNER**: `DAILY_BUILDER`
- **MODE**: `1D`
- **SYMBOL**: `ACE`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_DAILY_BUILDER_ACE_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'DAILY_BUILDER' on symbol 'ACE' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'DAILY_BUILDER' did not log production telemetry records for symbol 'ACE' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['DAILY_BUILDER', 'BUILDER'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 65: Daily Builder — POWERGRID (2026-09-23)
- **SCANNER**: `DAILY_BUILDER`
- **MODE**: `1D`
- **SYMBOL**: `POWERGRID`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `MISSING_TELEMETRY`
- **REPLAY RUN ID**: `replay_DAILY_BUILDER_POWERGRID_2026-09-23`
- **FIRST DIVERGENCE**: `PRODUCTION_TELEMETRY_MISSING`
- **ROOT INPUT DIVERGENCE**: `Zero production telemetry records found for scanner 'DAILY_BUILDER' on symbol 'POWERGRID' for date '2026-09-23' in logs/scanner_telemetry.jsonl`
- **DOWNSTREAM IMPACT**: `PRODUCTION_TELEMETRY_MISSING, EXECUTION_TRACE_UNVERIFIABLE, DECISION_UNVERIFIED`
- **FINAL DECISION**: `REJECTED_OR_UNVERIFIED`
- **REJECTION REASON**: `UNVERIFIED`
- **PRIMARY CATEGORY**: `MISSING_TELEMETRY`
- **LIKELY ROOT CAUSE**: MISSING_PRODUCTION_TELEMETRY: Scanner 'DAILY_BUILDER' did not log production telemetry records for symbol 'POWERGRID' on '2026-09-23' in logs/scanner_telemetry.jsonl.
- **EVIDENCE**: Queried logs/scanner_telemetry.jsonl for scanner tags ['DAILY_BUILDER', 'BUILDER'] on date 2026-09-23. Zero records matched. Under Rule 9, equivalence cannot be assumed; status is PENDING / TELEMETRY_REQUIRED.

### Case 66: EOD Breakout (Clean Historical Replay) — PGIL (2026-09-23)
- **SCANNER**: `EOD_CLEAN_REPLAY`
- **MODE**: `1D`
- **SYMBOL**: `PGIL`
- **DATE**: `2026-09-23`
- **PRODUCTION RUN ID**: `c1f7a76e-ae32-41a7-93fd-ce5756da9ae9`
- **REPLAY RUN ID**: `clean_historical_replay_PGIL_2026-09-23`
- **FIRST DIVERGENCE**: `DATA_SHA256`
- **ROOT INPUT DIVERGENCE**: `Input DataFrame mismatch: Row count (29 vs 243) or SHA256 (PROD_TELEMETRY_SNAPSHOT vs 5cb7cd9d8d8db0c1)`
- **DOWNSTREAM IMPACT**: `DATA_SHA256, GATE_EVALUATION`
- **FINAL DECISION**: `REJECTED`
- **REJECTION REASON**: `BASE_TIGHTNESS_FAIL (Replay) vs NO_ATR_EXPANSION_FAIL (Prod)`
- **PRIMARY CATEGORY**: `DATA_DIFFERENCE`
- **LIKELY ROOT CAUSE**: HISTORICAL_PARQUET_CONTAMINATION: Production evaluated before data sanitization, reading 45 rogue duplicate bars with ATR20=366.37 (ATR expansion 0.33 < 0.80 -> NO_ATR_EXPANSION). Clean historical data has ATR20=56.35 (ATR expansion 2.13 -> passes ATR expansion, fails BASE_TIGHTNESS).
- **EVIDENCE**: Raw data backup data/history/1d/PGIL.parquet.corrupt_bak reproduces ATR20=366.3748 exactly, while sanitized parquet yields ATR20=56.3500.

---

## 7. GOVERNANCE STATEMENT & CUTOVER PROTOCOL

> [!IMPORTANT]
> **SYSTEM GOVERNANCE STATEMENT**:
> - Overall Status: **PARTIALLY CERTIFIED** (1/13 Certified, 12 Pending / Telemetry Required).
> - **EOD Breakout Scanner** is **FULLY CERTIFIED** under `PRODUCTION_REPLAY` mode with 100% trace and 100% decision match across all 5 production cases.
> - The 12 remaining scanner families are categorized as `PENDING / TELEMETRY_REQUIRED` strictly per Rule 9. No synthetic or simulated equivalence is claimed.
> - Strategy optimization and parameters tournaments remain **FROZEN** until production telemetry is collected and verified for all 13 scanner families.
