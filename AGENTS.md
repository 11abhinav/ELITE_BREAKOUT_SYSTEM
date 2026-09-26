# ELITE BREAKOUT SYSTEM — AGENT OPERATING RULES

## MANDATORY REAL-MARKET-DATA BACKTEST PROTOCOL
This requirement applies to **every future backtest, certification, optimization study, regime study, scanner tournament, exit study, valuation study, and production-readiness test** in the Elite Breakout System.

### 1. Real Upstox Data Is Mandatory
Whenever historical market data is required, the backtest MUST use **real market data fetched from the Upstox API** or a previously certified local dataset whose complete provenance can be directly traced to Upstox raw API responses.

Preferred hierarchy:
```text
Upstox API
    ↓
Raw response
    ↓
Validation / normalization
    ↓
Certified local cache / Parquet
    ↓
Backtest
```
Do NOT substitute:
* synthetic data;
* simulated OHLCV;
* randomly generated prices;
* fabricated OI;
* interpolated market prices presented as actual observations;
* Yahoo Finance data;
* TradingView data;
* arbitrary third-party historical datasets;
* stale uncertified local files;
* manually constructed candles.

Third-party data may only be used for a separate cross-check and must never silently become the primary certification dataset.

### 2. Provenance Must Be Proven Before the Backtest
Before executing the first backtest calculation, verify and record:
* data provider = Upstox
* API version / endpoint
* instrument key
* symbol mapping
* exchange
* timeframe
* start date
* end date
* timezone
* number of rows
* available fields
* missing rows
* duplicate rows
* SHA256 / dataset fingerprint
* raw-source provenance

The certification run must fail immediately if provenance cannot be established:
```python
if provider != "UPSTOX":
    raise RuntimeError("CERTIFICATION BLOCKED: NON-UPSTOX DATA")

if not provenance_verified:
    raise RuntimeError("CERTIFICATION BLOCKED: DATA PROVENANCE FAILED")
```

### 3. Native Fields Must Be Used
Use the fields actually supplied by Upstox:
```text
timestamp, open, high, low, close, volume, open_interest
```
Do not create synthetic OI or infer OI from price/volume. Do not rename an inferred proxy as a native exchange field.

### 4. Correct Instrument Resolution Is Mandatory
Before downloading historical data:
```text
underlying → correct exchange instrument → correct contract → correct expiry → correct historical date
```
The resolver must respect applicable historical exchange contract rules for the date being replayed. The backtest must log the resolved instrument for auditability.

### 5. Point-in-Time Integrity
The backtest must use only information actually available at the historical decision timestamp:
```text
Historical trading signal → Only data known at signal time → Executable next-bar / next-session price → Future bars determine outcome
```
No future prices, future fundamentals, revised information, or future contract metadata may leak into the signal.

### 6. Cached Data Is Acceptable Only With Provenance
A local Parquet/database cache may be used to avoid repeated API downloads **only when**:
* cache → originally fetched from Upstox
* cache → provenance recorded
* cache → integrity/hash verified
* cache → coverage validated

The existence of a local file does NOT automatically make it certified. If provenance is unknown:
```text
DATA_STATUS = UNCERTIFIED
```
and certification must stop.

### 7. Missing / Stale Upstox Data
If required data is unavailable from Upstox:
```text
DO NOT SUBSTITUTE ANOTHER PROVIDER SILENTLY.
```
Instead report: `DATA_INSUFFICIENT`, `UPSTOX_UNAVAILABLE`, `PROVENANCE_FAILED` and stop or explicitly classify the study as non-certifiable.

### 8. Data-Source Audit Must Appear in Every Backtest Report
Every final report must contain a section:
```markdown
### DATA PROVENANCE
Provider: Upstox
API:
Exchange:
Universe:
Instrument resolution:
Timeframe:
Date range:
Timezone:
Rows:
Native fields:
Missing rows:
Duplicates:
Synthetic data:
Fallback providers:
Dataset hash:
Provenance status: PROVENANCE_STATUS = CERTIFIED
```
The report must explicitly state `PROVENANCE_STATUS = CERTIFIED` before performance results can be considered for governance.

### 9. Backtest Failure Rule
If any of: Upstox provenance unavailable, incorrect instrument mapping, incorrect expiry, missing required native field, uncertain timezone, unexplained missing bars, synthetic replacement, uncertified fallback data, future-information leakage occurs:
```text
BACKTEST_STATUS = INVALID / NON-CERTIFIABLE
```
Performance numbers must NOT be used for strategy promotion.

### 10. Required Instruction for Every Future Backtest Request
> **Use real historical market data sourced directly from Upstox. Verify provenance, instrument mapping, timezone, native fields, completeness, and point-in-time integrity before calculating any strategy result. Use a certified Upstox-derived local cache only when its provenance and integrity are verified. Never silently substitute Yahoo Finance, TradingView, synthetic data, inferred fields, or another provider. If Upstox data cannot be obtained or validated, mark the backtest non-certifiable rather than producing a substitute result.**

### 11. Execution Order (Data Gate First)
```text
1. Acquire / locate Upstox data
2. Verify provenance
3. Verify instrument mappings
4. Verify schema
5. Verify timezone
6. Verify completeness
7. Verify point-in-time causality
8. Freeze dataset
9. Hash dataset
10. Run backtest
11. Run statistical certification
12. Produce governance verdict
```

---

## MANDATORY TEMPORAL REPLICATION & REGIME ROBUSTNESS GATE
This protocol is mandatory for every:
* scanner backtest
* exit certification
* strategy tournament
* regime study
* feature ablation
* valuation study
* Fundamental Wealth study
* production-readiness certification

It operates **in addition to** the Mandatory Real-Market-Data / Upstox Protocol.

### 1. Core Principle (Anti-Pooled-Bias)
A strategy must NEVER be promoted or permanently locked because of one favorable pooled result.
A large aggregate:
```text
N = 20,000+
CI > 0
p < 0.05
```
is NOT sufficient evidence of robustness.
The system must demonstrate that the result is reproducible across:
```text
multiple years + multiple calendar periods + multiple independent market episodes + multiple samples within the same regime
```
The objective is to prove that the observed edge is structural rather than a random historical artifact.

### 2. Three Regimes Must Always Be Tested
Every surviving scanner must be evaluated independently in:
```text
BULL, SIDEWAYS, BEAR
```
Never assume a scanner is BULL-only, BEAR-only, or SIDEWAYS-only before the regime evidence has been measured. Do not suppress a regime before testing it. Final production routing must be derived strictly from empirical regime results.

### 3. Multi-Year Requirement
Every regime must contain observations from multiple calendar years wherever the historical dataset permits.
A regime result dominated by a single year must be explicitly classified:
```text
TEMPORALLY_CONCENTRATED
```
and cannot be permanently locked solely on that evidence.

### 4. Multiple Periods Within Each Year
Where sample size permits, evaluate:
```text
Q1, Q2, Q3, Q4
```
or equivalent non-overlapping calendar periods to detect seasonality or volatility episode concentration.

### 5. Multiple Independent Temporal Cells
Create independent, non-overlapping temporal cells defined **before evaluating performance**:
```text
Cell 1 = 2016–2018
Cell 2 = 2019–2021
Cell 3 = 2022–2024
Cell 4 = 2025–2026
```
The same structure must be applied independently to `BULL`, `SIDEWAYS`, and `BEAR`.

### 6. Multiple Market Episodes
Within each regime, identify multiple distinct historical episodes from the project's deterministic regime framework. Test whether the strategy survives different market environments **within the same declared regime**.

### 7. Same Regime, Multiple Independent Datasets
Require multiple independent periods (e.g. `SIDEWAYS-A`, `SIDEWAYS-B`, `SIDEWAYS-C`, `SIDEWAYS-D`) and evaluate each independently across all scanners.

### 8. No False Independence
Bootstrap resamples, permutation iterations, different random seeds, or different estimators on the same data are statistical checks, NOT new historical replications. A replication must contain genuinely different historical observations.

### 9. Same Frozen Strategy Across All Cells
Entry logic, exit logic, stop, target, holding period, ATR definition, friction, universe, and regime definitions must remain 100% identical across all cells. Zero cell-specific parameter tuning.

### 10. Data Provenance Per Cell
Every temporal cell must independently establish Upstox provenance (`CELL_PROVENANCE = CERTIFIED`). If unknown or uncertified: `CELL_RESULT = NON_CERTIFIABLE`.

### 11. Cell-Level Statistics Battery
For every `SCANNER × REGIME × TEMPORAL_CELL`, calculate:
```text
N, win rate, mean/median Net R, Arm A CI, Arm B CI, Delta CI, paired permutation p, MFE, MAE, max drawdown, portfolio Sharpe, effective N, symbol and calendar concentration.
```

### 12. Replication Consistency Test
Report:
```text
best cell, worst cell, median cell, mean cell, dispersion across cells, positive-cell count, negative-cell count, fraction of total pooled PnL contributed by top cell.
```
If one cell contributes a disproportionate share ($\ge 60\%$), flag: `CONCENTRATED_EDGE`.
If only one cell is positive, flag: `SINGLE_EPISODE_EDGE`.
If positive cells $< 50\%$, flag: `TEMPORALLY_INCONSISTENT`.

### 13. Permanent Lock Requirements
A scanner/regime combination may be permanently locked for production only when:
1. Data integrity passes (Upstox provenance, mapping, schema, timezone, completeness, causality).
2. End-to-end performance passes (Arm B holdout CI_low > 0, Delta holdout CI_low > 0, paired p < 0.05).
3. Temporal robustness passes (multiple years, multiple periods, positive evidence replicated across cells, no single historical episode explains the majority of the edge).

### 14. Underpowered / Inconsistent Results
* Positive pooled result + insufficient temporal replication $\rightarrow$ `UNDER_CERTIFICATION` (zero production alerts).
* Inconsistent results across independent cells $\rightarrow$ `TEMPORALLY_INCONSISTENT`.
* Positive in only one isolated cell $\rightarrow$ `SINGLE_EPISODE_EDGE`.

### 15. Decommission Rule
If a scanner/regime combination has sufficient independent evidence and repeatedly fails promotion conditions $\rightarrow$ `DECOMMISSIONED_FOR_REGIME` (zero production alerts). All research artifacts remain preserved.

### 16. No Shadow Mode
Only three states permitted:
```text
UNDER_CERTIFICATION (zero alerts)
CERTIFIED_FOR_PRODUCTION (live alerts only in certified regimes)
DECOMMISSIONED (zero alerts, zero scheduling, zero routing)
```

### 17. Final Permanent Rule
> **A trading edge is not considered proven merely because it is statistically significant on a large pooled dataset. It must be reproducible across multiple independent years, multiple time periods, and multiple independent historical episodes within the same market regime. Only a repeatedly replicated result may be permanently locked for future production use.**

Sequence:
```text
REAL UPSTOX DATA
       ↓
DATA CERTIFICATION
       ↓
CAUSAL BACKTEST
       ↓
BULL / SIDEWAYS / BEAR
       ↓
MULTIPLE YEARS
       ↓
MULTIPLE PERIODS
       ↓
MULTIPLE INDEPENDENT EPISODES
       ↓
END-TO-END HOLDOUT
       ↓
REPLICATION CONSISTENCY
       ↓
CERTIFIED_FOR_PRODUCTION
```

---

## SYSTEM INVARIANTS
- Timezone: Asia/Kolkata (IST).
- Currency: Indian Rupee (INR / ₹ / Rs).
- Calendar Invariants: Trading days only (Monday to Friday, excluding official NSE/BSE holidays). Saturday = 0, Sunday = 0.
- Governance: All production parameter promotions require dual-track registration and audit trail.

---

## MANDATORY PRE-PUSH CODE INTEGRITY RULES
1. **FULL IMPORT & VARIABLE SCOPE VALIDATION (ZERO UNBOUND / SHADOW VARIABLES)**:
   - All newly added functions, modified methods, and variables MUST have their imports and symbols fully declared at the proper scope level.
   - NEVER place partial or shadow imports (e.g. `from database import ...`) inside inner nested `try`, `except`, or `finally` blocks that shadow or conflict with outer-scope identifier usage.
2. **MANDATORY PRE-PUSH COMPILE & SYMBOL SANITY CHECK**:
   - Before ANY `git push`, the agent MUST run syntax verification (`python3 -m py_compile`) and test import execution on all modified files to ensure zero `SyntaxError`, `ImportError`, `NameError`, or `UnboundLocalError` at runtime.
