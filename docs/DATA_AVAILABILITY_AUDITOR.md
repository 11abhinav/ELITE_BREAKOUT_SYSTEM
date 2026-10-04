# Quality Data Availability Auditor & Recovery-Diagnostics Layer

## 1. Architectural Purpose & Separation of Concerns

The **Elite Breakout System** enforces a strict architectural boundary between **Data Availability / Recovery-Diagnostics** and **Trading Strategy Execution**:

| Component | Core Responsibility | Permitted Actions | Prohibited Actions |
|---|---|---|---|
| **Quality Scanner** (`live_fundamental_scanner.py`) | *"Given certified data, does this stock satisfy the strategy?"* | Evaluates certified PIT data, filters candidates | Network recovery calls, third-party oracle lookups |
| **Primary Recovery Engine** (`fundamental_pre_recovery.py` & `fundamental_source_router.py`) | *"Can we obtain and certify required data from authoritative sources?"* | Fetches Upstox, NSE XBRL, and Fyers approved feeds; updates canonical PIT staging | Writing uncertified data, skipping Never-Downgrade Gate |
| **Data Availability Auditor** (`data_availability_auditor.py`) | *"When authoritative sources report missing data, does an independent source indicate it exists?"* | Diagnostic checks against Fyers & Screener; raises admin alerts and logs discrepancies | Writing to PIT, writing production metrics, enabling BUY |

---

## 2. Source Hierarchy & Authority Tiers

```text
886 APPROVED STOCK UNIVERSE
             │
             ▼
    CANONICAL PIT LOAD
             │
      Completeness Check
             │
     ┌───────┴───────┐
     │               │
  COMPLETE        MISSING
     │               │
     │               ▼
     │      PRIMARY RECOVERY ENGINE
     │      ┌───────────────┬───────────────┐
     │      │               │               │
     │   Upstox          NSE XBRL         FYERS
     │  (Tier 1)         (Tier 1)        (Tier 2 Approved)
     │      └───────────────┴───────────────┘
     │               │
     │         Still Missing?
     │               │
     │               ▼
     │      DATA AVAILABILITY AUDITOR
     │      ┌───────────────┴───────────────┐
     │      │                               │
     │    FYERS                          Screener
     │  (Tier 2 Diagnostic)            (Tier 3 Forensic Only)
     │      └───────────────┬───────────────┘
     │               │
     │         Discrepancy?
     │          /         \
     │        YES          NO
     │         │            │
     │   ADMIN ERROR    DATA_UNAVAILABLE_VERIFIED
     │   (Alert Raised) (Confirmed Hard Block)
     │         │            │
     └─────────┼────────────┘
               ▼
     CERTIFIED PIT SNAPSHOT
               │
               ▼
        QUALITY SCANNER
               │
               ▼
       PRE-BUY INTEGRITY GATE
          /          \
       PASS          BLOCK
        │              │
     SAVE ALERT     SUPPRESS (e.g. UPDATE_PENDING / Staleness)
```

### Authority Tiers Defined:
- **Tier 1 — Production-Authoritative**:
  - Upstox Fundamentals API (Annual Statements, Income, Balance Sheet, Cash Flow)
  - Upstox Key Ratios API (EV/EBITDA, P/E, Debt/Equity)
  - NSE Corporate Integrated Filing XBRL
  - Cryptographically audited local PIT filing cache (`data/pit_raw_filings/`)
- **Tier 2 — Independent Diagnostic & Approved Broker Source**:
  - **FYERS API v3** ([myapi.fyers.in/docsv3](https://myapi.fyers.in/docsv3))
  - **Proven API Capabilities & Provenance Boundary**:
    - `fyers_client.quotes({"symbols": ...})` queries the Quotes API endpoint (`https://api-t1.fyers.in/data/quotes`) returning real-time market data (`lp`, `open_price`, `high_price`, `low_price`, `prev_close_price`, `volume`, `ch`, `chp`, `tt`).
    - FYERS API v3 does **NOT** expose a public documented REST fundamentals / key-ratios endpoint for annual balance sheets, P&L, or cash flow ratios (ROCE, ROE, Sales CAGR, PAT CAGR, EV/EBITDA).
    - Therefore, for fundamental valuation ratios, FYERS API v3 is classified as `UNSUPPORTED_IN_PUBLIC_REST_API_V3` / `NOT_AVAILABLE`.
    - Tier 2 diagnostic evidence for fundamental ratios requires an authorized machine-readable interface or verified operator attestation (`data/reference_availability/fyers_availability.csv`).
- **Tier 3 — Forensic Reference Only**:
  - **Screener.in**: Strictly an offline diagnostic oracle.
  - Mandatory rule: `canonical_pit_write = FALSE`, `production_metric_write = FALSE`, `buy_decision = BLOCKED`.

---

## 3. Mandatory Governance Diagnostic Conditions

The auditor distinguishes between four critical availability states to eliminate ambiguous `DATA_INSUFFICIENT` logs:

### Condition 1: DATA PROVIDER DISCREPANCY
* **Status Pattern**:
  ```text
  Upstox   = Missing
  NSE      = Missing
  FYERS    = Available
  Screener = Available
  ```
* **Classification**: `PRIMARY_RECOVERY_FAILURE_DATA_EXISTS_ELSEWHERE`
* **Severity**: `CRITICAL`
* **Meaning**: An authoritative or independent source has the data, but our primary automated pipeline failed to acquire it.
* **Administrator Message**:
  ```text
  🚨 DATA PROVIDER DISCREPANCY — <STOCK> — <FIELD>

  Required Field: <FIELD>

  Primary/Verified Sources: DATA NOT RECOVERED
  FYERS: ✅ DATA FOUND
  Screener: ✅ DATA FOUND

  Likely Issue:
  provider ingestion / parser / field mapping / normalization / PIT integration

  Production:
  BLOCKED
  ```

---

### Condition 2: SCREENER-ONLY DATA FOUND
* **Status Pattern**:
  ```text
  Upstox   = Missing
  NSE      = Missing
  PIT      = Missing
  FYERS    = Missing / Unavailable
  Screener = Available
  ```
* **Classification**: `SCREENER_ONLY_DATA_SOURCE`
* **Severity**: `HIGH`
* **Meaning**: The required metric appears on public company disclosures (as indexed by Screener), but could not be certified from an approved production source.
* **Discrete Notification Rule**: Emitted **per-field and per-stock** (never lumped into an aggregate count).
* **Administrator Notification Format & Delivery**:
  Surfaced directly on the Admin Dashboard Bell Icon (`#notif-badge`, `#notif-list`) and persisted in `global_notifications`:
  ```text
  🚨 DATA SOURCE NOTICE: <STOCK> — <FIELD> found on Screener.in but unavailable from primary authoritative providers (Upstox/NSE/Exchange). This data will NOT be used for trading decisions. Potential upstream ingestion gap flagged for review.

  Missing Field:
  <FIELD>

  Verified Production Sources:
  ❌ Upstox Fundamentals: Not Available
  ❌ Upstox Key Ratios: Not Available
  ❌ NSE/XBRL: Not Available
  ❌ Exchange/PIT Filings: Not Available
  ❌ Local Verified Filing Cache: Not Available
  ⚠️ FYERS: Not Available / Unsupported in API v3 REST
  ✅ Screener: Data Found (Forensic Reference Only)

  Classification:
  SCREENER_ONLY_DATA_SOURCE

  Production Value:
  NOT WRITTEN (NULL)

  BUY Decision:
  BLOCKED

  Required Admin Action:
  Investigate why the value available in Screener cannot be recovered
  from an authorized verified production source.

  IMPORTANT:
  Screener data must NOT be promoted to production truth.
  ```

---

### Condition 3: GENUINELY UNAVAILABLE
* **Status Pattern**:
  ```text
  Upstox   = Missing
  NSE      = Missing
  PIT      = Missing
  FYERS    = Missing
  Screener = Missing
  ```
* **Classification**: `DATA_UNAVAILABLE_VERIFIED`
* **Severity**: `INFO`
* **Meaning**: Confirmed across all primary and independent secondary sources that the company lacks public data for this period (e.g. recent IPO, reporting exemptions). Legitimate fail-closed hard block.

---

### Condition 4: PARSER OR FIELD MAPPING FAILURE
* **Status Pattern**:
  ```text
  HTTP Status     = 200 OK
  Raw Records     = > 0 (filing rows present)
  Usable Fields   = 0 (target metric unextracted / missing)
  ```
* **Classification**: `PARSER_OR_FIELD_MAPPING_FAILURE`
* **Severity**: `HIGH`
* **Meaning**: The provider successfully returned raw filings (HTTP 200), but the parser or field mapping failed to extract usable metrics due to XBRL taxonomy changes, standalone vs consolidated mismatch, or regex parser errors.
* **Administrator Message**:
  ```text
  🚨 PARSER OR FIELD MAPPING FAILURE: <STOCK> — <FIELD>

  HTTP 200 raw filings were received and parsed, but target metric <FIELD> could not be extracted (0 usable fields extracted).

  Likely Issue:
  official taxonomy tag change, standalone vs consolidated divergence, or regex parsing failure.

  Required Action:
  Inspect raw JSON/XML filings for <STOCK> in data/pit_raw_filings/<STOCK>.json and update tag mapping in nse_xbrl_provider.py or upstox_fundamentals_provider.py.

  Production:
  BLOCKED
  ```

---

## 4. Audit Record Schema & Database Persistence

Audit records are persisted to PostgreSQL table `data_availability_audit` and exported to `data/reports/data_availability_audit_latest.json` and `.csv`:

```sql
CREATE TABLE IF NOT EXISTS data_availability_audit (
    audit_date DATE NOT NULL,
    symbol TEXT NOT NULL,
    isin TEXT,
    scanner TEXT NOT NULL,
    field TEXT NOT NULL,
    required_for_gate TEXT,
    upstox_status TEXT,
    nse_status TEXT,
    exchange_filing_status TEXT,
    pit_status TEXT,
    local_cache_status TEXT,
    fyers_status TEXT,
    screener_status TEXT,
    classification TEXT NOT NULL,
    severity TEXT NOT NULL,
    upstox_key_ratios TEXT,
    admin_action TEXT,
    production_value_written BOOLEAN NOT NULL DEFAULT FALSE
        CHECK (production_value_written = FALSE),
    buy_allowed BOOLEAN NOT NULL DEFAULT FALSE
        CHECK (buy_allowed = FALSE),
    admin_alert_generated BOOLEAN NOT NULL DEFAULT FALSE,
    run_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (audit_date, symbol, field)
);
```

---

## 5. Renamed Telemetry & Reporting Terminology

To eliminate ambiguity between stocks passing strategy filters vs stocks clearing live integrity gates, the system uses strictly disjoint telemetry labels:

```text
📊 CANONICAL POPULATION REPORT
  • Scanned Universe (Approved)    : 886
  • Fully Evaluable                : 856 (96.6%)
    ├─ Strategy Candidates Produced: 53   (Satisfied Quality & Valuation filters)
    │  ├─ Pre-BUY Eligible         : 51   (Passed Freshness & PIT Integrity Gate)
    │  └─ Pre-BUY Blocked          : 2    (MANYAVAR, VIMTALABS — suppressed due to UPDATE_PENDING)
    └─ Filter Rejections           : 803  (Failed strategy metric thresholds)
  • Incomplete (Data Failures)     : 19  (2.1%)
  • Structural Ineligible          : 11  (1.2%)
```

---

## 6. Admin Dashboard REST Endpoints

1. `GET /api/admin/data_availability/counts`
   - Returns counts for all 11 categories:
     - `verified_data_missing`
     - `provider_discrepancies`
     - `fyers_only_data_found`
     - `screener_only_data_found`
     - `genuinely_unavailable`
     - `insufficient_historical_depth`
     - `stale_pit`
     - `unprocessed_filing`
     - `parser_mapping_failure`
     - `calculation_failure`
     - `structural_ineligible`
2. `GET /api/admin/data_availability/audits`
   - Returns symbol-by-symbol 17-field audit records for inspection and targeted upstream parser investigation.
