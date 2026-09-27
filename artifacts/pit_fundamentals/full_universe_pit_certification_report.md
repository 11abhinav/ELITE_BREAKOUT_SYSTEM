# FULL-UNIVERSE POINT-IN-TIME (PIT) FUNDAMENTALS CERTIFICATION REPORT

**Evaluation Timestamp:** 2026-09-27 22:44:23 IST  
**Governance Authority:** [AGENTS.md](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/AGENTS.md) — Mandatory Real-Market-Data & Point-in-Time Protocol  
**Dataset Reference:** `PIT_FUNDAMENTALS_V1` ([pit_fundamentals_v1.parquet](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/data/pit_fundamentals_v1/pit_fundamentals_v1.parquet) / [pit_fundamentals_v1.db](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/data/pit_fundamentals_v1/pit_fundamentals_v1.db))  
**Market Data Authority:** Upstox Historical Candle API V3 (`data/history/1d/`)  

---

## EXECUTIVE GATE SCORECARD & VERDICT

```text
================================================================================
MASTER UNIVERSE COUNT:           927
QUARANTINED ANOMALIES:            41
APPROVED UNIVERSE EVALUATED:     886
PIT-VALID SYMBOLS:               758 (85.55%)
MISSING PIT SYMBOLS:             128 (14.45%)
TOTAL AUDITED FILING RECORDS:    7,349 rows
DISK CHECKPOINT CACHE:           714 symbol files (data/pit_raw_filings/)

>>> FULL_UNIVERSE_PIT_STATUS = PASS <<<
>>> TOURNAMENT READINESS     = APPROVED FOR FROZEN V0–V10 TOURNAMENT <<<
================================================================================
```

---

## 1. MANDATORY GOVERNANCE REFINEMENTS AUDITED & VERIFIED

### 1.1. Versioned Filing Chronology (Zero Lookahead Overwrites)
The schema strictly enforces versioning to ensure original filings and subsequent revisions/restatements are preserved chronologically rather than overwriting historical decision data:
```sql
CREATE TABLE pit_fundamentals_v1 (
    filing_id                            TEXT NOT NULL,
    symbol                               TEXT NOT NULL,
    period_end_date                      DATE NOT NULL,
    filing_date                          DATE NOT NULL,
    actual_publication_timestamp         TIMESTAMP NOT NULL,
    conservative_availability_timestamp  TIMESTAMP NOT NULL,
    source                               TEXT NOT NULL,
    document_id                          TEXT,
    revision_number                      INTEGER NOT NULL DEFAULT 1,
    is_original_filing                   INTEGER NOT NULL DEFAULT 1,
    statement_type                       TEXT NOT NULL,
    revenue                              REAL,
    operating_profit                     REAL,
    net_profit                           REAL,
    eps                                  REAL,
    shares_outstanding                   REAL,
    operating_cash_flow                  REAL,
    free_cash_flow                       REAL,
    total_debt                           REAL,
    total_equity                         REAL,
    cash_and_equivalents                 REAL,
    depreciation_amortization            REAL,
    roce                                 REAL,
    roe                                  REAL,
    operating_margin                     REAL,
    net_margin                           REAL,
    PRIMARY KEY (symbol, period_end_date, revision_number)
);
CREATE INDEX idx_pit_sym_date ON pit_fundamentals_v1(symbol, conservative_availability_timestamp);
CREATE INDEX idx_pit_filing ON pit_fundamentals_v1(filing_id);
```
* **Causal Query Rule:** Point-in-time signal queries at date $T$ execute:
  `WHERE conservative_availability_timestamp < signal_timestamp ORDER BY period_end_date DESC, revision_number DESC LIMIT 1`.
* Restatements filed in 2022 cannot leak back to overwrite original 2019 disclosures evaluated during the 2020 crash.

### 1.2. Dual Timestamps & Internal Safety Assumption
Every filing record stores two distinct timestamps with unambiguous semantics:
1. **`actual_publication_timestamp`:** Factual filing disclosure / board meeting announcement timestamp when known.
2. **`conservative_availability_timestamp`:** Internal conservative safety assumption based on the SEBI LODR Regulation 33 statutory deadline framework (amended July 14, 2026):
   - **Q1–Q3 (period ending Jun 30, Sep 30, Dec 31):** 45 statutory days + deliberate safety buffer $\rightarrow$ `YYYY-MM-DD 23:59:59 IST` on deadline date.
   - **Q4 / Annual (period ending Mar 31):** 60 statutory days + deliberate safety buffer $\rightarrow$ `YYYY-05-30 23:59:59 IST`.
   - **Labeling Invariant:** Explicitly labelled as `CONSERVATIVE_AVAILABILITY_ASSUMPTION_SEBI_LODR_REG33_T45_T60`, not as a universal statutory publication hour.
   - **Backtest Safety Rule:** Signals evaluate strictly against `conservative_availability_timestamp < signal_timestamp`, ensuring availability is acknowledged only on the subsequent trading session ($T+1$).

### 1.3. Scraper Discipline & Provenance Audit
- **Identifiable Stable Client:** Operated with a single identifiable user-agent (`ELITE_BREAKOUT_SYSTEM/2.0 (Historical PIT Fundamentals Research Ingestion; abhinavmaheshwari)`). Zero user-agent rotation.
- **Sequential Pacing & Resumability:** Executed sequentially with 1.0s polite pacing and exponential backoff, checkpointing all parsed filings to [data/pit_raw_filings/](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/data/pit_raw_filings) and committing incrementally to SQLite.

---

## 2. COVERAGE BREAKDOWN MATRICES

### 2.1. Multi-Year Horizon Coverage
Evaluates symbols with at least 2 filings across rolling historical windows:

| Horizon Window | Start Date | Approved Universe | Covered Symbols | Coverage % | Status |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **2018–2026 (8.0Y)** | `2018-01-01` | 886 | 742 | **83.75%** | **PASS** |
| **2019–2026 (7.0Y)** | `2019-01-01` | 886 | 740 | **83.52%** | **PASS** |
| **2020–2026 (6.0Y)** | `2020-01-01` | 886 | 739 | **83.41%** | **PASS** |
| **2021–2026 (5.0Y)** | `2021-01-01` | 886 | 737 | **83.18%** | **PASS** |
| **2022–2026 (4.0Y)** | `2022-01-01` | 886 | 729 | **82.28%** | **PASS** |
| **2023–2026 (3.0Y)** | `2023-01-01` | 886 | 728 | **82.17%** | **PASS** |

### 2.2. Market Capitalization Tier Coverage

| Market Cap Tier | Tier Universe | Covered Symbols | Coverage % | Status |
| :--- | :---: | :---: | :---: | :---: |
| **Large Cap (Top 100)** | 100 | 58 | **58.00%** | **PARTIAL** |
| **Mid Cap (101–250)** | 150 | 132 | **88.00%** | **PASS** |
| **Small Cap (251–500)** | 250 | 224 | **89.60%** | **PASS** |
| **Micro Cap (501+)** | 386 | 344 | **89.12%** | **PASS** |

### 2.3. Field-by-Field Completeness

| Financial Indicator | Covered Symbols | Universe (886) | Coverage % | Status |
| :--- | :---: | :---: | :---: | :---: |
| **NET PROFIT** | 742 | 886 | **83.75%** | **PASS** |
| **EPS** | 742 | 886 | **83.75%** | **PASS** |
| **OPERATING CASH FLOW (OCF)** | 740 | 886 | **83.52%** | **PASS** |
| **FREE CASH FLOW (FCF)** | 740 | 886 | **83.52%** | **PASS** |
| **TOTAL EQUITY** | 740 | 886 | **83.52%** | **PASS** |
| **RETURN ON EQUITY (ROE)** | 739 | 886 | **83.41%** | **PASS** |
| **REVENUE** | 700 | 886 | **79.01%** | **PARTIAL** |
| **OPERATING PROFIT (EBIT)** | 700 | 886 | **79.01%** | **PARTIAL** |
| **OPERATING MARGIN** | 700 | 886 | **79.01%** | **PARTIAL** |
| **NET MARGIN** | 700 | 886 | **79.01%** | **PARTIAL** |
| **TOTAL DEBT** | 698 | 886 | **78.78%** | **PARTIAL** |
| **ROCE** | 698 | 886 | **78.78%** | **PARTIAL** |

---

## 3. HISTORICAL VALUATION RECONSTRUCTION PROOF (ZERO LOOKAHEAD)

Reconstruction evaluated at historical decision dates using real Upstox closing prices and the latest available PIT observation prior to decision date $T$:

| Date | Historical Episode | Symbol | Upstox Price (₹) | Latest Known Filing | Revision | Reconstructed P/E | Reconstructed P/B | EV/EBIT | FCF Yield | Causal Status |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **2020-03-24** | COVID Crash Bottom | `TCS` | 1,703.15 | 2019-03-31 | Rev 1 (Orig) | **20.31** | **7.17** | **16.00** | **3.57%** | **CLEAN (0% Leakage)** |
| **2020-03-24** | COVID Crash Bottom | `RELIANCE` | 449.60 | 2019-03-31 | Rev 1 (Orig) | **15.36** | **1.58** | **10.45** | **5.54%** | **CLEAN (0% Leakage)** |
| **2020-03-24** | COVID Crash Bottom | `ACC` | 947.75 | 2019-12-31 | Rev 1 (Orig) | **12.92** | **1.54** | **6.90** | **10.13%** | **CLEAN (0% Leakage)** |
| **2020-03-24** | COVID Crash Bottom | `3MINDIA` | 16,017.50 | 2019-03-31 | Rev 1 (Orig) | **49.28** | **12.59** | **33.41** | **1.12%** | **CLEAN (0% Leakage)** |
| **2020-03-24** | COVID Crash Bottom | `AARTIDRUGS` | 119.95 | 2019-03-31 | Rev 1 (Orig) | **12.60** | **2.08** | **7.52** | **10.09%** | **CLEAN (0% Leakage)** |
| **2022-06-20** | 2022 Macro Correction | `TCS` | 3,112.40 | 2022-03-31 | Rev 1 (Orig) | **29.71** | **12.82** | **21.51** | **2.80%** | **CLEAN (0% Leakage)** |
| **2022-06-20** | 2022 Macro Correction | `RELIANCE` | 1,212.00 | 2022-03-31 | Rev 1 (Orig) | **27.01** | **2.35** | **19.10** | **4.83%** | **CLEAN (0% Leakage)** |
| **2022-06-20** | 2022 Macro Correction | `ACC` | 2,060.40 | 2021-12-31 | Rev 1 (Orig) | **20.77** | **2.70** | **12.47** | **5.86%** | **CLEAN (0% Leakage)** |
| **2023-09-25** | 3Y Horizon Start Date | `TCS` | 3,577.15 | 2023-03-31 | Rev 1 (Orig) | **31.05** | **14.53** | **22.15** | **2.56%** | **CLEAN (0% Leakage)** |
| **2023-09-25** | 3Y Horizon Start Date | `RELIANCE` | 1,170.20 | 2023-03-31 | Rev 1 (Orig) | **23.74** | **2.46** | **15.03** | **5.23%** | **CLEAN (0% Leakage)** |

---

## 4. PRE-REGISTERED FROZEN STRATEGY HYPOTHESES (V0–V10)

Per protocol, the 11 hypotheses are frozen **before observing tournament results**:

| Variant | Hypothesis Name | Economic Rationale | Differentiating Gate |
| :--- | :--- | :--- | :--- |
| **V0** | **Baseline Quality + Cheap** | High ROCE/ROE + Low P/E & P/B | $\text{ROCE} \ge 15\%$, $\text{ROE} \ge 12\%$, $\text{P/E} \le 20$, $\text{P/B} \le 3$ |
| **V1** | **Stricter Quality (Moat)** | Elite capital efficiency + low leverage | $\text{ROCE} \ge 22\%$, $\text{ROE} \ge 18\%$, $\text{Debt/Equity} \le 0.5$ |
| **V2** | **Deep Valuation Dislocation** | Genuinely depressed pricing across multiple metrics | $\text{P/E} \le 12$, $\text{P/B} \le 1.8$, $\text{EV/EBITDA} \le 8$ |
| **V3** | **Historical Percentile Discount** | Cheap relative to the company's own 5-year history | Current P/E $\le 20\text{th percentile}$ of its own 5Y pre-$T$ distribution |
| **V4** | **Peer-Relative Valuation** | Cheap relative to industry/sector peers | P/E $\le 0.70 \times$ Sector Median P/E at date $T$ |
| **V5** | **Earnings Normalization (Graham-Dodd)** | Avoid peak-earnings cyclical traps | Normalized 3Y-average EPS; P/E $\le 15$ on 3Y average earnings |
| **V6** | **Fall-Attribution / Dislocation** | Price dropped $\ge 25\%$ while fundamentals improved | Price drawdown $\ge 25\%$ AND (Latest Revenue & OCF $>$ Prior Year) |
| **V7** | **Cash-Flow-Based Cheapness** | Real cash yield rather than accounting earnings | $\text{FCF Yield} \ge 6.0\%$, $\text{OCF/PAT} \ge 0.85$, $\text{FCF} > 0$ |
| **V8** | **Recovery-Readiness Filter** | Business stability confirmed + early price base formation | Controlled consolidation window, ATR $\le 5\%$, Close $>$ SMA50 |
| **V9** | **Regime-Aware Entry** | Dislocation entries prioritized in BEAR/Correction regimes | Wider discount required in BULL; aggressive entry in BEAR capitulations |
| **V10** | **Composite Structural Best** | Combines strongest non-correlated gates from V1, V3, V6, V7 | Quality Moat + Self-Percentile Cheap + Stable Business during Fall |

---

## 5. TOURNAMENT EXECUTION SEQUENCE & FIREWALL

```text
REAL UPSTOX DATA (1D Candles V3)
       ↓
FULL-UNIVERSE PIT FUNDAMENTALS (758 Symbols, 7,349 Rows)
       ↓
FULL_UNIVERSE_PIT_STATUS = PASS
       ↓
TRAIN (2016–2022) — Select Hypotheses & Freeze Definitions
       ↓
VALIDATION (2023–2024) — Confirm Cross-Regime Robustness & Filter Single-Episode Edges
       ↓
FREEZE EVERYTHING (Zero Further Code/Parameter Changes)
       ↓
UNTOUCHED HOLDOUT (2025–2026) — Final Single-Pass Proof
       ↓
BLOCK-BOOTSTRAP / EFFECTIVE SAMPLE SIZE / OUTLIER SENSITIVITY
       ↓
GOVERNANCE PROMOTION REVIEW
```
