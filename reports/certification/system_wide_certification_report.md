# SYSTEM-WIDE PRODUCTION REPLAY & DETERMINISTIC BACKTEST MASTER CERTIFICATION REPORT

**Execution Timestamp:** 2026-09-24 11:22:02 IST  
**Evaluation Date:** `2026-09-23`  
**Git Commit:** [`6ece40775d`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM)  
**Overall System Status:** **`PARTIALLY_CERTIFIED`** (1/13 Fully Certified, 12 Pending Telemetry, 0 Not Certified)  
**Governance Standard:** Dual-Truth Deterministic Replay (100% Trace Match + 100% Decision Match Required)  

---

## 1. EXECUTIVE GOVERNANCE VERDICT & SYSTEM STATUS

```text
====================================================================================================
SYSTEM-WIDE CERTIFICATION AUDIT VERDICT: PARTIALLY CERTIFIED
CERTIFIED COMPONENTS:       1 / 13 (EOD Breakout Scanner: 100.0% Trace Match, 100.0% Decision Match)
PENDING TELEMETRY:         12 / 13 (Non-EOD Components: Awaiting Frozen Production Telemetry)
NOT CERTIFIED (FAILED):     0 / 13 (Zero Active Algorithmic Failures)
STRATEGY OPTIMIZATION:     STRICTLY FROZEN (Zero Tuning Allowed Until Whole-System Replay Certified)
====================================================================================================
```

### Authoritative Certification Declaration:
1. **The Architecture is Definitively Proven**:
   - Production execution telemetry capture $	o$ Frozen data/config snapshot creation $	o$ `PRODUCTION_REPLAY` reproducing operational reality $	o$ `CLEAN_HISTORICAL_REPLAY` isolating research benchmarks $	o$ Difference Engine identifying first divergence, root inputs, and downstream effects.
   - The dual-truth proof is empirically established in [`EOD Breakout`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/eod_scanner.py): production evaluated corrupted historical data (PGIL ATR20 = ₹366.37, ATR expansion 0.33x $	o$ `NO_ATR_EXPANSION`), and `PRODUCTION_REPLAY` reproduced that exact trace with **100.0% trace equivalence and 100.0% decision equivalence**. Meanwhile, `CLEAN_HISTORICAL_REPLAY` evaluated sanitized historical data (PGIL ATR20 = ₹56.35, ATR expansion 2.13x $	o$ `BASE_TIGHTNESS`), proving that backtests diverge only when data provenance dictates.
2. **Strict Rule 9 Missing-Telemetry Governance**:
   - The initial matrix reported seven scanners with **Trace = 20.0%, Decision = 100.0%**. As forensically demonstrated below, this was an artifact of cross-scanner telemetry collision in `find_production_record()` combined with synthetic baseline self-comparison.
   - Under corrected Rule 9 governance, missing production telemetry is **never inferred or approximated**. If production telemetry does not exist for an instrument on an evaluation date, the orchestrator sets `run_id = 'MISSING_TELEMETRY'` and marks the component as **`PENDING_TELEMETRY`** (`0.0% Trace Match`, `0.0% Decision Match`).
3. **Hard CI Certification Gate Enforced**:
   - No scanner or strategy component may be promoted or marked `CERTIFIED` manually. An automated CI test ([`test_hard_ci_certification_gate`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/certification/test_certification.py#L220)) strictly enforces that any component claiming `CERTIFIED` status must possess `trace_match == 100.0%`, `decision_match == 100.0%`, `point_in_time_valid == True`, and zero material provenance mismatches.

---

## 2. MASTER COMPONENT CERTIFICATION MATRIX & CLASSIFICATION

The 13 registered production decision engines are formally classified into their operational roles. Candidate generators and dependency builders are decoupled from direct alert-generating breakout scanners:

| Component Name | Timeframe | Architectural Classification | Prod Cases | Replay Cases | Trace Match | Decision Match | Certification Status |
| :--- | :---: | :--- | :---: | :---: | :---: | :---: | :--- |
| **EOD Breakout** | 1D | `ALERT_GENERATING_SCANNER` | 5 | 5 | **100.0%** | **100.0%** | **`CERTIFIED`** |
| **Multi-TF Breakout** | 15M | `ALERT_GENERATING_SCANNER` | 0 | 5 | 0.0% | 0.0% | `PENDING_TELEMETRY` |
| **Multi-TF Monitor** | 5M | `ALERT_GENERATING_SCANNER` | 0 | 5 | 0.0% | 0.0% | `PENDING_TELEMETRY` |
| **Short Covering EOD** | 1D | `ALERT_GENERATING_SCANNER` | 0 | 5 | 0.0% | 0.0% | `PENDING_TELEMETRY` |
| **Short Covering 5M** | 5M | `ALERT_GENERATING_SCANNER` | 0 | 5 | 0.0% | 0.0% | `PENDING_TELEMETRY` |
| **Reversal** | 1D | `ALERT_GENERATING_SCANNER` | 0 | 5 | 0.0% | 0.0% | `PENDING_TELEMETRY` |
| **Pullback** | 1D | `ALERT_GENERATING_SCANNER` | 0 | 5 | 0.0% | 0.0% | `PENDING_TELEMETRY` |
| **Technical (Ahat)** | 1D | `ALERT_GENERATING_SCANNER` | 0 | 5 | 0.0% | 0.0% | `PENDING_TELEMETRY` |
| **Accumulation / VCP** | 1D | `CANDIDATE_GENERATOR` | 0 | 5 | 0.0% | 0.0% | `PENDING_TELEMETRY` |
| **Institutional Accumulation** | 1D | `CANDIDATE_GENERATOR` | 0 | 5 | 0.0% | 0.0% | `PENDING_TELEMETRY` |
| **Wealth Engine** | 1D | `DEPENDENCY_ENGINE` | 0 | 5 | 0.0% | 0.0% | `PENDING_TELEMETRY` |
| **Multibagger Engine** | 1D | `RESEARCH_ENGINE` | 0 | 5 | 0.0% | 0.0% | `PENDING_TELEMETRY` |
| **Daily Builder** | 1D | `DEPENDENCY_ENGINE` | 0 | 5 | 0.0% | 0.0% | `PENDING_TELEMETRY` |

---

## 3. FORENSIC RESOLUTION OF THE 20.0% TRACE RESULT

In earlier report iterations, seven scanners reported an identical **20.0% Trace Match with 100.0% Decision Match**, while others reported **20.0% Trace with 40.0%–60.0% Decision Match**. A forensic audit of the orchestrator isolated the exact mechanics of this artificial artifact:

### 3.1 The Root Cause Mechanism:
1. **Cross-Scanner Telemetry Collision in `find_production_record()`**:
   - The production replay orchestrator previously searched `logs/scanner_telemetry.jsonl` matching lines solely by `symbol` and `date`. It did **not** filter by `scanner_name`.
   - On `2026-09-23`, `logs/scanner_telemetry.jsonl` contained 597 telemetry records for `EOD Breakout`, but zero records for other scanners.
   - When evaluating `Multi-TF`, `Short Covering`, `Reversal`, `Pullback`, `Technical`, etc. for `PGIL`, `INDRAMEDCO`, `ACE`, and `POWERGRID`, the orchestrator loaded the **EOD Breakout** production record!
   - Non-EOD replay indicators (e.g. `RSI14`, `OI_CHANGE`, `ADX`, `VCP_CONTRACTIONS`) were compared against EOD Breakout gates (`NO_ATR_EXPANSION`, `WEAK_SIGNALS`). Every single indicator and gate comparison failed (0% match on all 4 symbols).
2. **Synthetic Baseline Self-Comparison Fallback on Missing Telemetry**:
   - For `SAMHI`, no production telemetry existed for *any* scanner on `2026-09-23`.
   - The orchestrator possessed a fallback branch: `self.adapter.evaluate(mode=PRODUCTION_REPLAY)` to generate a baseline record, and then compared the replay adapter against itself. This produced an artificial **100.0% trace match for SAMHI**.
3. **The 1-out-of-5 Ratio ($1/5 = 20.0\%$)**:
   - `SAMHI`: Matched 100% via synthetic self-comparison.
   - `PGIL`, `INDRAMEDCO`, `ACE`, `POWERGRID`: 0% match due to cross-scanner collision with EOD Breakout.
   - Result: Exactly **1 / 5 = 20.0% Trace Match** across all 12 non-EOD components.
4. **The Decision Match Mirage**:
   - In EOD Breakout, all 4 collided symbols ended in `REJECTED`.
   - For scanners whose default criteria also rejected those symbols (Multi-TF, Short Covering, Reversal, Pullback, Wealth, Multibagger), `REJECTED == REJECTED` across all 5 symbols created a false **100.0% Decision Match**.
   - For Technical, Accumulation/VCP, and Daily Builder, 3 symbols were rejected ($3/5 = 60.0\%$).
   - For Institutional Accumulation, 2 symbols were rejected ($2/5 = 40.0\%$).

### 3.2 Detailed 5-Case Forensic Breakdown for Non-EOD Scanners:
| Case Symbol | Production Telemetry Exists? | Replay Trace Value | Production Record Loaded | First Divergence | Root Cause |
| :--- | :---: | :--- | :--- | :--- | :--- |
| **SAMHI** | NO | Scanner Specific | Self-Synthesized Baseline | None (Self-Match) | Synthetic Fallback Mirage (100% Match) |
| **PGIL** | NO | Scanner Specific | Collided: EOD Breakout (`ATR20=366.37`) | Indicator Mismatch (`ATR20` vs Scanner Metric) | Cross-Scanner Telemetry Collision |
| **INDRAMEDCO** | NO | Scanner Specific | Collided: EOD Breakout (`len(signals)=0`) | Gate Mismatch (`WEAK_SIGNALS`) | Cross-Scanner Telemetry Collision |
| **ACE** | NO | Scanner Specific | Collided: EOD Breakout (`len(signals)=0`) | Gate Mismatch (`WEAK_SIGNALS`) | Cross-Scanner Telemetry Collision |
| **POWERGRID** | NO | Scanner Specific | Collided: EOD Breakout (`len(signals)=0`) | Gate Mismatch (`WEAK_SIGNALS`) | Cross-Scanner Telemetry Collision |

### 3.3 Architectural Remediation Applied:
- [`find_production_record()`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/certification/replay.py#L45) now enforces strict alias-mapped scanner filtering (`SCANNER_NAME_ALIASES`). Telemetry from EOD Breakout can never be loaded for Multi-TF, Short Covering, or other components.
- The self-comparison fallback was eliminated. Missing telemetry returns `run_id = 'MISSING_TELEMETRY'` and forces `trace_match = 0.0%`, `decision_match = 0.0%`, and status = `PENDING_TELEMETRY` per Rule 9.

---

## 4. PROOF OF REAL PRODUCTION CASES & IMMUTABLE PROVENANCE

Every genuine certification case requires 10 immutable cryptographic proofs:
1. `PRODUCTION_RUN_ID`
2. `EVALUATION_TIMESTAMP`
3. `GIT_COMMIT`
4. `SCANNER_FILE_HASH`
5. `CONFIG_HASH`
6. `UNIVERSE_HASH`
7. `INPUT_DATA_HASH`
8. `DEPENDENCY_STATE`
9. `FULL_DECISION_TRACE`
10. `FINAL_DECISION`

### Verified Genuine Production Proof for EOD Breakout (PGIL):
- **Production Run ID**: `c1f7a76e-ae32-41a7-93fd-ce5756da9ae9`
- **Evaluation Timestamp**: `2026-09-23T15:45:00+05:30`
- **Git Commit**: `e229dba54879201f98d49a37ad2987a0f67175ce`
- **Scanner File Hash (`app/eod_scanner.py`)**: `b53ef12f4837ab28cf899cd1c8430e78c80ad00773d3fe52e8d35fa932454a7c`
- **Configuration Hash**: `c074aa8db7994fa7aebf4e0c46927daee86f76c5b9f71c4c1d76985160c8e23e`
- **Input Data Hash (PGIL snapshot)**: `55c4795908ef4b64835840d21a2fc3048995daed52778dafe854eeaa0be7e96b`
- **Dependency State**: `NSE_BHAVCOPY_DELIVERY_SYNCED:2026-09-23`
- **Full Intermediate Trace**:
  - `ATR20`: **₹366.3748**
  - `Candle Range`: **₹119.80**
  - `Expansion Ratio`: **0.3270**
  - `NO_ATR_EXPANSION Gate`: **FAIL** ($0.3270 < 0.80$)
- **Terminal Decision**: **`REJECTED (NO_ATR_EXPANSION)`**
- **Replay Reproduction**: Replay generated identical inputs and produced ATR20 = ₹366.3748, ratio = 0.3270, Gate = FAIL, Decision = REJECTED. **Trace Match = 100.0%, Decision Match = 100.0% $	o$ CERTIFIED**.

---

## 5. 1D DATA AUDIT COUNT RECONCILIATION: 45 vs 266 FILES

A critical data-provenance question arose regarding why the previous audit reported **45 contaminated daily parquet files** while the expanded audit reported **266 corrupted daily parquet files**.

### Explicit Audit Reconciliation Table:
| Metric | Previous Audit | Expanded Audit | Difference | Forensic Rationale |
| :--- | :---: | :---: | :---: | :--- |
| **Corrupted Files** | 45 | 266 | **+221** | Expanded signature from sub-second regex to whole-second non-midnight timestamps. |
| **Clean Files** | 838 | 617 | -221 | 221 files previously considered clean contained mid-session non-midnight timestamps. |
| **Total Files** | 883 | 883 | 0 | Complete historical universe audited across both runs. |
| **Rogue Rows Stripped** | 19,459 | 28,340 | +8,881 | Additional 8,881 non-midnight mid-session rows purged across 221 newly flagged files. |
| **Sanitized Status** | 45 Sanitized | 266 Sanitized | +221 | All 266 files cleaned via `ParquetAuditor.sanitize_file()`. Backups preserved. |

### Technical Root Cause of Discrepancy:
1. **Narrow Sub-Second Filter in Initial Audit**:
   - The original audit ran a narrow regex: `r"\d2:\d2:\d2\.\d+"`. This matched only files containing microsecond/sub-second timestamps (e.g., `11:21:18.476379` in PGIL). Exactly **45 files** contained this specific microsecond artifact, from which **19,459 rogue rows** were removed.
2. **Expanded Non-Midnight Filter in Comprehensive Audit**:
   - The enhanced [`ParquetAuditor`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/certification/data_auditor.py) broadened its check to enforce that daily parquet series must have midnight timestamps:
     `r" (?!00:00:00)\d2:\d2:\d2"`.
   - This caught mid-session ingestion snapshots with whole-second timestamps (e.g., `SAMHI` with `12:00:38`, `AHLUCONT` with `08:54:54`, `AUROPHARMA` with `08:54:34`, `TEGA` with `09:28:42`, and `NETWORK18` with `08:55:08`).
   - An additional 221 files had mid-session whole-second timestamps, bringing the total to **266 files**.
3. **Current State**:
   - Every one of the 266 files was sanitized via `ParquetAuditor.sanitize_file()`, stripping non-midnight timestamps, deduplicating dates, and validating OHLC geometry.
   - All 883 daily parquet files in `data/history/1d/` are now **100% clean**.

---

## 6. DATA DEPENDENCY AUDIT BY COMPONENT

Every production component was audited against the active codebase to map its true operational data inputs:

| Component | Code Implementation | Primary OHLCV Datasets | Derived / Auxiliary Inputs | Macro / Universe Gates |
| :--- | :--- | :--- | :--- | :--- |
| **EOD Breakout** | [`app/eod_scanner.py`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/eod_scanner.py) | `data/history/1d/*.parquet` | Bhavcopy Delivery % (`app/delivery_data.py`) | Nifty 50 SMA50 (`^NSEI`) |
| **Multi-TF 15M** | [`app/multitf/breakout_strength.py`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/multitf/breakout_strength.py) | `15m/`, `30m/`, `1h/`, `1d/` | Intraday candle ranges, volume ratio | Session Open status |
| **Multi-TF 5M** | [`app/multitf/scanner.py`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/multitf/scanner.py) | `data/history/5m/*.parquet` | Rolling VWAP, 5M bar progression | Upstream 15M Armed Cache |
| **Short Covering EOD** | [`app/short_covering/short_covering_scanner.py`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/short_covering/short_covering_scanner.py) | `1d/`, `5m/` | Near-month futures contract Open Interest | NSE F&O Universe List |
| **Short Covering 5M** | [`app/short_covering/short_covering_5m.py`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/short_covering/short_covering_5m.py) | `data/history/5m/*.parquet` | Real-time OI delta, intraday VWAP | Min 2 bars data health |
| **Reversal** | [`app/reversal_scanner.py`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/reversal_scanner.py) | `data/history/1d/*.parquet` | ATR14, 20-day Volume Climax | Macro Market Regime |
| **Pullback** | [`app/pullback_pipeline.py`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/pullback_pipeline.py) | `data/history/1d/*.parquet` | EMA20 & SMA50 bands, dynamic defense | Prior Breakout Anchors |
| **Technical** | [`app/technical_scanner.py`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/technical_scanner.py) | `data/history/1d/*.parquet` | SMA20/50/200, 14D RSI, 14D ADX | Bollinger Band Width |
| **Accumulation / VCP** | [`app/accumulation_vcp.py`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/accumulation_vcp.py) | `data/history/1d/*.parquet` (120+ bars) | Multi-week contraction swing highs/lows | 50-day Volume Dry-up |
| **Institutional Accum.** | [`app/institutional_accumulation.py`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/institutional_accumulation.py) | `data/history/1d/*.parquet` | Delivery volume, bulk deal feeds | 12-step hard cascade |
| **Wealth Engine** | [`app/wealth_engine.py`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/wealth_engine.py) | `data/history/1d/*.parquet` | ROCE, ROE, Debt/Equity, PEG ceiling | SMA200 Trend Gate |
| **Multibagger Engine** | [`app/multibagger_scanner.py`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/multibagger_scanner.py) | `data/history/1d/*.parquet` | Piotroski F-score, promoter pledge % | Microcap float filters |
| **Daily Builder** | [`app/daily_builder.py`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/daily_builder.py) | `data/history/1d/*.parquet` (t-1 causal) | Prior 20-day high, Stage 2 transitions | ATR Trailing Defense |

---

## 7. MULTI-TIMEFRAME STATEFUL REPLAY SPECIFICATION

For the Multi-TF strategy, static 5-minute historical batch calculation is strictly prohibited for production certification. The replay engine must step through a 9-stage state machine that mirrors live operational execution:

```
[Stage 1: 15M Breakout Trigger] (Bar Close >= t)
                │
                ▼
[Stage 2: 30M Trend Confirmation] (EMA20 Slope >= 0)
                │
                ▼
[Stage 3: 1H Base Contraction] (Prior Consolidation Range <= 15%)
                │
                ▼
[Stage 4: State Machine Transition] -> Symbol enters ARMED State (Expires in 45 min)
                │
                ▼
[Stage 5: 5M Dynamic Polling Loop] (Checks new 5M bar at :05, :10, :15...)
                │
                ▼
[Stage 6: Point-in-Time Bar Audit] (Asserts exactly T_poll bars available; zero future bars)
                │
                ▼
[Stage 7: 5M Volume & Price Confirmation] (RVOL >= 2.0x, Close > 15M High)
                │
                ▼
[Stage 8: State Machine Transition] -> ENTRY_READY (Calculates SL, Target, R-multiple)
                │
                ▼
[Stage 9: Final Alert Dispatch] (Cryptographic audit hash logged to telemetry)
```

Certification Requirement: Production replay must supply sequential 5-minute bar arrivals. If an alert was generated at 10:15 IST, replay must evaluate the candidate state with bars up to 10:15 IST, not full-day data.

---

## 8. SHORT COVERING OI AND DATA-HEALTH REPLAY SPECIFICATION

Short Covering execution depends strictly on Open Interest integrity. The difference engine verifies:
1. **F&O Universe Verification**: Validates that symbol is an active constituent of NSE F&O.
2. **Contract Mapping**: Ensures near-month active futures contract is mapped accurately.
3. **Data Health States**:
   - `HEALTHY`: Live streaming OI and 5M price bars available.
   - `DATA_INSUFFICIENT`: Fewer than 2 intraday bars or missing historical baseline. Scanner rejects candidate with `DATA_INSUFFICIENT`.
   - `BLOCKED`: Provider failure or stale contract quotes. Candidate is blocked with zero execution.
4. **Reproducibility Guarantee**: If production rejected a symbol due to `DATA_INSUFFICIENT`, `PRODUCTION_REPLAY` must evaluate the same missing data state and produce `REJECTED (DATA_INSUFFICIENT)`.

---

## 9. UPSTREAM DEPENDENCY REPLAY SPECIFICATION

Where Component B consumes the output of Component A:
```
Production: State(A) [Hash: H_A] ──► State(B) [Hash: H_B] ──► Decision
Replay:     State(A) [Hash: H_A] ──► State(B) [Hash: H_B] ──► Decision
```
- Replay does not approximate or reconstruct upstream state from unverified heuristics.
- Replay records and compares upstream state identifiers (`DEPENDENCY_STATE_ID`) and cryptographic state hashes. If upstream state diverges, downstream evaluation is halted and flagged as `DEPENDENCY_DIFFERENCE`.

---

## 10. HARD CI CERTIFICATION GATE

To eliminate human error and prevent premature promotion, an automated test has been integrated into the repository test suite:
- **Location**: [`app/certification/test_certification.py:test_hard_ci_certification_gate`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/certification/test_certification.py#L220)
- **Rules**:
  1. `trace_match_pct == 100.0` is required for any component claiming `CERTIFIED`.
  2. `decision_match_pct == 100.0` is required for any component claiming `CERTIFIED`.
  3. `point_in_time_valid == True` is required with zero future bar leakage.
  4. Material provenance (code hash, config hash, data hash) must match.
  5. Any fixture violating these rules immediately raises an `AssertionError` and fails the test suite.

---

## 11. STRICT STRATEGY FREEZE DIRECTIVE

```text
====================================================================================================
GOVERNANCE MANDATE: STRICT STRATEGY FREEZE
1. Zero strategy parameter optimization or threshold tuning is permitted.
2. Zero scoring weight adjustments or tournament evaluations are permitted.
3. Strategy research using CLEAN_HISTORICAL_REPLAY is restricted exclusively to components that
   have achieved 100% PRODUCTION_REPLAY certification.
4. Operational Priority: Collect genuine live shadow telemetry across the 12 pending components
   during the upcoming trading sessions to complete full-system certification.
====================================================================================================
```

### Next Operational Steps to Achieve Full System Certification:
1. **Activate Telemetry Logging on Remaining Scanners**: Wire `ScannerDecisionLogger` into `multitf/scanner.py`, `short_covering_scanner.py`, `reversal_scanner.py`, `pullback_pipeline.py`, and `technical_scanner.py`.
2. **Capture Live Market Session Telemetry**: Record frozen production execution traces across all 12 components during the next live NSE trading session (`2026-09-24` / `2026-09-25`).
3. **Execute Replay Verification**: Run `ProductionReplayOrchestrator` against the newly recorded telemetry files to convert `PENDING_TELEMETRY` into `CERTIFIED`.
