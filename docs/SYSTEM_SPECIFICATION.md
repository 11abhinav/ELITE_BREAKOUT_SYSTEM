# ELITE BREAKOUT SYSTEM — SYSTEM SPECIFICATION & OPERATIONAL GUIDE

> **Document Class:** User & Admin Operational Manual
> **Status:** Canonical Master Operational Guide for system functionality, strategy specifications, trading rules, and dashboard operations.
> **Target File:** `docs/SYSTEM_SPECIFICATION.md`
> **Last Synchronized:** 2026-10-04 (Master Consolidation — Active Production Scanners Only, Authoritative Outbox Pattern, Quality Data Availability Auditor, Broker Integrations, Corporate Action Event Framework)

---

# 1. EXECUTIVE OVERVIEW & SYSTEM PURPOSE

The **Elite Breakout System** is an autonomous, quantitative trading platform engineered specifically for the Indian Equity Markets (NSE & BSE). The system systematically scans, ranks, filters, monitors, and manages high-probability momentum breakouts, long-term fundamental quality compounders, and multi-year structural leaders.

All legacy and decommissioned scanners (`EOD Breakout`, `Multi-TF Intraday`, `Reversal`, `Pullback Pipeline`, `Short Covering`, `5M Breakout`, `Momentum Ignition`, `Accumulation`) have been permanently decommissioned and purged following empirical temporal replication and regime robustness certification.

## Core Capabilities
- **Certified Quantitative Scanning Engines**:
  - `DAILY_BUILDER`: Autonomous daily universe construction, liquidity filtering, circuit filtering, and promoter pledge scraping.
  - `TECHNICAL`: High-Conviction Technical Momentum Breakout Engine (active in `BULL` regime).
  - `QUALITY_COMPOUNDER` / `WEALTH_ENGINE`: Fundamental Wealth Engine & Live Fundamental Scanner targeting 4 fundamental buckets (Core Compounder, Growth Multiplier, Quality-On-Sale, Opportunistic).
  - `QUALITY_VALUE_RECOVERY`: Valuation-based recovery scanner targeting certified high-quality compounders trading at steep valuation discounts.
  - `MULTIBAGGER_ENGINE`: Multi-year compounder evaluation combining fundamental quality, low promoter pledge ($\le 10\%$), capital efficiency, and technical momentum.
- **Data Availability Auditor & Recovery-Diagnostics**: Pre-recovery diagnostic layer enforcing a strict 3-tier hierarchy (Tier 1 Production-Authoritative, Tier 2 FYERS API v3 Approved Broker Check, Tier 3 Screener.in Forensic Oracle Only). Emits discrete per-stock per-field alerts to the Admin Dashboard Bell Icon.
- **Authoritative Outbox Persistence & Crash Recovery**: PostgreSQL Transactional Outbox (`buy_alerts_journal`) as single source of truth, with idempotent Parquet materialization and deterministic bidirectional crash recovery (`reconcile_alerts_outbox_materialization`).
- **Dynamic Risk & Target Management**: Automated initial stop loss calculation, structural resistance placement, trailing stop loss management, multi-target profit booking (T1, T2, T3, T4), and exit alerting.
- **Corporate Action Event Framework**: Decoupled, priority-ranked corporate event badges (`E` Earnings, `D` Dividends, `S` Splits, `B` Bonuses) with trading-day calendar calculation and touch-friendly overflow pills.
- **Real-Time Dashboards**: User Dashboard, Admin Dashboard (with notification bell and real-time SSE stream), and Performance Tracker.
- **Omnichannel Notification Engine**: Real-time signal delivery via Telegram Channels, Web Push Notifications (VAPID), and In-App Portal Alerts.
- **Autonomous 24/7 Execution**: Self-healing scheduler operating around NSE trading hours (09:15 to 15:30 IST), post-market earnings calendar window (15:30 to 18:00 IST), and early morning build cycles.

---

# 2. PRODUCTION SCANNER SUITE & STRATEGY SPECIFICATIONS

The system operates strictly certified scanning engines reading configuration directly from `config.py`:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        DAILY WATCHLIST BUILDER                         │
│       Universe Scrape → Liquidity Filter → Circuit Filter → Pledge      │
│                     (Produces: data/watchlist.parquet)                 │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
         ┌──────────────────────────┴──────────────────────────┐
         ▼                                                     ▼
┌─────────────────────────────────┐   ┌──────────────────────────────────┐
│   TECHNICAL BREAKOUT SCANNER    │   │   QUALITY COMPOUNDER / WEALTH    │
│  • 20D High Momentum Breakout   │   │  • 4 Fundamental Buckets         │
│  • Active in BULL Regime        │   │  • Never-Downgrade Gate          │
│  • Dynamic SL & Resistance T1-T4│   │  • Source Freshness Fence        │
└─────────────────────────────────┘   └─────────────────┬────────────────┘
                                                        │
                                                        ▼
                                      ┌──────────────────────────────────┐
                                      │   MULTIBAGGER CONVICTION ENGINE  │
                                      │  • Prime Multibagger (Piotroski) │
                                      │  • High Quality Conviction Tier  │
                                      │  • Strict Non-Null Pledge Gates  │
                                      └──────────────────────────────────┘
```

## 2.1 Daily Watchlist Builder (`app/daily_builder.py`)
- **Market Objective**: Autonomous generation and validation of the daily trading universe across listed NSE & BSE equities.
- **Execution Schedule**: **01:00 AM IST** daily (prior to market open).
- **Universe Filtering Cascade**:
  1. **TradingView & Exchange Universe Ingestion**: Pulls active listed NSE/BSE stocks (~940+ symbols).
  2. **Liquidity & Price Floor Gate**:
     - `Close >= ₹100.0` (`MIN_STOCK_PRICE`)
     - Daily Turnover $\ge ₹1.0\text{ Cr}$
     - 20-day Average Daily Volume $\ge 100,000$ shares
     - Minimum historical daily bars: $\ge 50$ bars (accommodating recent high-momentum IPOs).
  3. **Circuit Filter & Surveillance Guard**: Excludes ASM/GSM stage securities and symbols with frequent $\le 5\%$ circuit limits.
  4. **Promoter Pledge Scraper (`nse_pledge_fetcher.py`)**: Fetches official NSE promoter pledge filings and enforces a strict pledge ceiling ($\le 15\%$ maximum allowed, $\le 10\%$ for Prime Multibaggers).
  5. **Output**: Authoritative daily watchlist saved to `data/watchlist.parquet` and mirrored in PostgreSQL database `build_manifest`.

## 2.2 Technical Momentum Breakout Scanner (`app/technical_scanner.py`)
- **Market Objective**: Identifies high-conviction momentum breakouts from consolidation bases in stocks exhibiting institutional accumulation.
- **Regime Constraint**: Certified and actively alerting strictly during **`BULL`** market regimes.
- **Key Eligibility & Quality Gates**:
  1. **Price Floor**: `Close >= ₹100.0`.
  2. **Data History**: $\ge 50$ historical daily bars.
  3. **20-Day High Breakout**: `Close > Prior_20D_High`.
  4. **Distance from 52-Week High**: Within 15.0% of 52W High (`MAX_DISTANCE_FROM_52W_HIGH_PCT`).
  5. **Candle Geometry Quality**:
     - Bullish candle: `Close > Open`
     - Body Ratio: $\ge 45\%$ of total candle range
     - Close Position: $\ge 65\%$ of candle range
     - Upper Wick: $\le 35\%$ of candle range
  6. **Volume Surge**: Volume $\ge 1.8\text{x}$ vs 20-day median volume baseline.
  7. **ATR Volatility Expansion**: Candle Range / 20-day ATR $\ge 0.9$ (`MIN_ATR_EXPANSION_RATIO`).
  8. **Non-Extended Extension Gate**: $(\text{Close} - \text{EMA}_{20}) / \text{ATR}_{20} \le 1.5$.
  9. **Moving Average Stack**: $\text{Close} > \text{EMA}_{20} > \text{SMA}_{50} > \text{SMA}_{200}$ (with reduced trend gate for symbols with 50–199 bars).
  10. **Composite Score Threshold**: Minimum score $\ge 82$ out of 100+.
  11. **Natural Risk-Reward**: Minimum $R:R \ge 2.0R$ to Target 1.

## 2.3 Quality Compounder & Fundamental Wealth Engine (`app/wealth_engine.py`, `app/live_fundamental_scanner.py`)
- **Market Objective**: Screens and allocates capital to high-conviction fundamental compounders across 4 deterministic buckets, enforcing strict Point-in-Time (PIT) integrity.
- **Four Deterministic Fundamental Buckets**:
  1. **Core Compounder**:
     - Fundamental Score $\ge 65$, Market Cap $\ge ₹10,000\text{ Cr}$.
     - Non-Financials: $\text{ROCE} \ge 20.0\%$, $\text{ROE} \ge 15.0\%$, $\text{Debt/Equity} \le 0.50$.
     - Financials: $\text{ROE} \ge 15.0\%$, $\text{GNPA} \le 5.0\%$.
  2. **Growth Multiplier**:
     - Fundamental Score $\ge 60$, Market Cap $\ge ₹2,000\text{ Cr}$.
     - YoY Sales CAGR $\ge 20.0\%$, YoY Profit CAGR $\ge 20.0\%$.
     - Relative Strength vs Nifty ($RS_{6m}$) $\ge 0$, Distance to 52W High $\le 15.0\%$.
  3. **Quality-On-Sale**:
     - Fundamental Score $\ge 50$, Distance from 52W High $\ge 10.0\%$ (discounted quality).
     - Non-Financials: $\text{ROCE} \ge 15.0\%$, $\text{Debt/Equity} \le 1.0$.
     - Financials: $\text{ROE} \ge 15.0\%$.
  4. **Opportunistic**:
     - Fundamental Score $\ge 55$, YoY Profit CAGR $\ge 40.0\%$, $RS_{6m} \ge 15.0\%$.
- **Hard-Kill Valuation Ceiling**:
  - $\text{PEG} \le 3.0$ ceiling: Immediate disqualification for bubble valuations.
- **Timing Gate**:
  - Fundamental Quality Score $\ge 55$, Technical Momentum Score $\ge 25$, and $\text{Price} > \text{SMA}_{200}$.
- **Never-Downgrade Gate & Source Freshness Fence**:
  - Never allow older or lower-quality data to overwrite certified canonical PIT records.
  - Pre-BUY Filing Freshness Fence enforces that no BUY alert may be committed if a newer filing was published on the exchange before transaction commit.
- **Single-Point Provenance Certification Gate Invariant (Rule 65 / Rule 67 Clean Architecture)**:
  - `prov_valid` starts strictly initialized to `False` (`prov_valid = False`).
  - No earlier branch (shared canonical snapshot gate, initial cache defaults, or intermediate PIT recovery branches) is permitted to certify provenance or set `prov_valid = True`.
  - Exactly ONE closed Boolean certification gate exists in the entire fundamental scanner pipeline:
    ```python
    prov_valid = False
    # ... snapshot integration / PIT recovery / field derivations / normalization ...
    prov_valid = compute_fundamental_provenance_valid(funds, is_data_stale=is_data_stale)
    ```
  - `compute_fundamental_provenance_valid` evaluates the final post-recovery candidate state against 5 mandatory criteria:
    1. Complete 4-field quality gate inputs (`roce`, `roe`, `debt_equity`, `operating_cash_flow`).
    2. Strict provider-provenance pair authorization (`VALID_PROVIDER_PROVENANCE_COMBINATIONS`).
    3. Annual statement basis verification (`quality_source_basis == 'ANNUAL'` and `annual_filing_present is True`).
    4. PIT snapshot freshness and non-staleness (`snapshot_status in {'FRESH', 'CERTIFIED'}` and `not is_data_stale`).
    5. Verifiable PIT filing/period metadata.
  - Zero premature certifications, zero if/elif branch state preservation, zero fallback leakage. Auditing requires inspecting only that single call site.
- **Execution Schedule**:
  - Pre-market sweep at **02:00 AM IST**.
  - Intraday 15-minute BUY alert scan during market hours (`09:15` to `15:30` IST).
  - Fast 5-minute CMP exit updates (<3.0s runtime).

## 2.4 Quality-Value Recovery Scanner (`app/fundamental_wealth_engine.py`)
- **Market Objective**: Evaluates certified fundamental compounders that have undergone severe market pullbacks to identify high-margin-of-safety value entries.
- **Eligibility Gates**:
  - Verified Tier-1 fundamental certification (ROCE $\ge 15\%$, positive CFO/PAT).
  - Drawdown from 52-week high between 15% and 35%.
  - Technical stabilization: RSI divergence or bullish candle reclaim of $\text{EMA}_{20}$.

## 2.5 Multibagger Engine (`app/multibagger.py`)
- **Market Objective**: Screens multi-year compounders combining capital efficiency, promoter skin-in-the-game, and financial health.
- **Unified Conviction Tiers**:
  - **🚀 Prime Multibagger**:
    - Composite Score $\ge 75$, Quality $\ge 65$, Valuation $\ge 50$, Trend $\ge 10.0$.
    - **Piotroski F-Score $\ge 7$**.
    - Promoter Pledge $\le 10.0\%$.
    - Full capital allocation ($₹100,000$).
  - **💎 High Quality Multibagger**:
    - Composite Score $\ge 65$, Quality $\ge 60$, Trend $\ge 10.0$.
    - Promoter Pledge $\le 15.0\%$.
    - Standard capital allocation ($₹50,000$).
  - **🟡 Watchlist Tier (Score 50–64)**:
    - Non-alerting display tier tracked for fundamental monitoring. Strictly blocked from generating active BUY alerts.
- **Strict Non-Null Integrity**:
  - Missing promoter pledge or Piotroski scores are never populated with synthetic proxies; missing data fails closed.
- **Execution Schedule**: **04:00 AM IST** Cold Start, **19:00 PM IST** Post-Market Scan, and **15-minute** intraday exit monitor.

## 2.6 Result / Earnings Calendar Pipeline (`app/earnings_calendar.py`)
- **Market Objective**: Autonomous tracking of upcoming and declared quarterly financial results.
- **Schedule**: Post-market close window (**15:30 to 18:00 IST**).
- **Priority Execution Logic**:
  - **Priority 1**: Stocks scheduled for results **TODAY** (`earnings_date = TODAY`) are queried first immediately after 15:30 IST to capture newly declared numbers.
  - **Priority 2**: Rest of universe (skipped if known date cached within **45 days**, or missing date retried after **7-day** cooldown).
- **Resilience**: Managed via `safe_yf_call` gateway with exponential backoff and circuit breaker on HTTP 429.

---

# 3. CORPORATE ACTION EVENT FRAMEWORK & WARNING BADGES

The **Corporate Action Event Framework** (`app/corporate_events.py`) provides a decoupled, stateless, and extensible architecture for decorating stock objects with priority-ranked event badges across all backend API endpoints and frontend dashboards.

```
    CorporateEventRepository           TradingCalendar
  (DB Query / Data Access Layer)     (Cross-Cutting Trading Days)
               │                                │
               └───────────────┬────────────────┘
                               │
                               ▼
                      CorporateEventCache
              (Cache Lifecycle, TTL & Fallbacks)
                               │
                               ▼
                     CorporateEventPipeline
             (Pluggable Contributor Registry)
                               │
                               ▼
                decorate_events(stocks) -> Pure Function
                               │
                               ▼
                 Versioned Semantic JSON Payload
                               │
                               ▼
         shared_ui.js -> renderEventBadges(event_badges)
       (Client-Side CSS Styling, Tooltips & +N Overflow)
```

## 3.1 Components & Architecture
1. **`TradingCalendar` (`app/trading_calendar.py`)**:
   - Computes actual trading sessions between dates (`days_between()`), skipping weekends and official NSE market holidays.
2. **`CorporateEventRepository`**: Isolated database data access layer (`fetch_all_events()`).
3. **`CorporateEventCache`**: Manages cache lifecycle, 1-hour TTL, and fallback to stale snapshots on failure.
4. **`EventContributor`**: Abstract provider class (`EarningsContributor`, `DividendContributor`, `SplitContributor`, `BonusContributor`).
5. **`CorporateEventPipeline`**: Registry aggregating event contributors.
6. **`decorate_events()`**: Stateless, pure functional transformer returning immutable copies decorated with `event_badges`.

## 3.2 Standardized JSON Schema Contract (`schema_version: 1`)
```json
{
  "symbol": "TATAMOTORS",
  "company_name": "Tata Motors Limited",
  "schema_version": 1,
  "event_badges": [
    {
      "type": "earnings",
      "label": "E in 3d",
      "priority": 100,
      "status": "UPCOMING",
      "metadata": {
        "date": "2026-10-07",
        "days": 3,
        "date_status": "CONFIRMED"
      }
    }
  ]
}
```

### Event Priority Hierarchy (`EventPriority`):
| Event Type | Priority Value | Label | Status Classification |
|---|---|---|---|
| **`EARNINGS`** | **100** | `E in 3d` / `E 2d ago` | `UPCOMING` (`0 <= days <= 7`) / `RECENT` (`-7 <= days < 0`) |
| **`DIVIDEND`** | **80** | `D` | `UPCOMING` / `RECENT` |
| **`SPLIT`** | **70** | `S` | `UPCOMING` / `RECENT` |
| **`BONUS`** | **60** | `B` | `UPCOMING` / `RECENT` |

## 3.3 Event Badging Policy (No Hard-Block Policy)
Upcoming earnings announcements or corporate action windows do **NOT** hard-block scanner trade generation. Valid technical and fundamental breakouts continue to fire normally. Event risk is visually badged in the UI to give operators complete situational awareness:
- `🔴 RESULTS TODAY`: Earnings expected today (0 days).
- `🟠 RESULTS IN 1D / 2D`: Earnings expected in 1 to 2 days.
- `🟡 RESULTS IN 3D–5D`: Earnings expected in 3 to 5 days.
- `⚠️ UNVERIFIED`: Missing or unverified calendar date.

Client-side rendering (`static/shared_ui.js`):
- Sorts badges by `priority` descending.
- Displays up to `maxDisplay` badges (default = 2).
- Renders a touch-friendly and accessible `+N` overflow pill for remaining events with hover/tap popover tooltips.

---

# 4. TRADE EXECUTION, SIGNAL DELIVERY & PERSISTENCE ARCHITECTURE

## 4.1 Authoritative Outbox Pattern & Crash Recovery
The system strictly prevents state drift between PostgreSQL and Parquet files using the **Authoritative Transactional Outbox Pattern**:

```
      PRE-COMMIT DECISION GATE
      (Snapshot SHA + Source Freshness Fence)
                 │
                 ▼
     [1] POSTGRESQL OUTBOX COMMIT
      (buy_alerts_journal: status='COMMITTED',
       materialized_to_parquet=0)
                 │
                 ▼
     [2] IDEMPOTENT PARQUET MATERIALIZATION
      (atomic write to .parquet sidecar)
                 │
                 ▼
     [3] POSTGRESQL COMPLETION MARKER
      (materialized_to_parquet=1, materialized_at=NOW())
```

### Crash-Recovery Invariants (`reconcile_alerts_outbox_materialization`):
1. **Crash before DB commit**: Rollback occurs; 0 rows in DB, 0 in Parquet.
2. **Crash after DB commit before Parquet write**: Reconciler detects `materialized_to_parquet = 0`, materializes pending records into Parquet, and sets flag to `1`.
3. **Crash after Parquet write before DB marker**: Reconciler detects row already in Parquet, skips duplicate insertion idempotently, and sets marker to `1`.
4. **Parquet file deletion or corruption**: Reconciler fully reconstructs Parquet from the authoritative database journal.

## 4.2 Alert Payload Structure
Every alert contains complete structural parameters:
- **`symbol`**: Official NSE/BSE ticker (e.g. `RELIANCE`, `TCS`, `TATAMOTORS`).
- **`scanner`**: `TECHNICAL`, `WEALTH`, `MULTIBAGGER`, or `QUALITY_VALUE_RECOVERY`.
- **`entry_price`**: Recommended execution price (₹).
- **`initial_stop_loss`**: Structural stop loss calculated at signal generation time (**Immutable**).
- **`trailing_stop_loss`**: Active trailing stop loss updated as targets are hit.
- **`targets (T1, T2, T3, T4)`**:
  - Dynamically calculated via `ClusterEngine` scanning for structural resistance nodes (prior swing highs, volume nodes, moving averages, Fibonacci extensions).
  - Ascending order of resistance intensity.
- **`score`**: Composite Technical & Fundamental Quality Score (0–100+).
- **`conviction_tier`**: `PRIME`, `HIGH_QUALITY`, or `STANDARD`.

## 4.3 Profit Booking & Trailing Stop Loss Management
- **Target Liquidation Profile (`EXIT_PROFILES`)**:
  - **Target 1 (T1)**: Book 30% of position size. Trail `stop_loss` to Breakeven (`entry_price`).
  - **Target 2 (T2)**: Book 40% of position size. Trail `stop_loss` to Target 1 (`target_1`).
  - **Target 3 (T3)**: Book remaining 30% of position size. Position is 100% liquidated.
  - **Target 4 (T4)**: **Informational Structural Runner Target** (0% position allocation; tracked for analytical quality scoring and extended runner tracking).
- **Terminal Immutability**:
  - When remaining shares reach 0 (at T3, SL hit, or Expiry), the trade reaches terminal status (`WIN`, `PARTIAL_WIN`, `LOSS`, `EXPIRED`). All columns are frozen permanently.
- **Conservative Intrabar Precedence**:
  - If a single candle touches both Stop Loss (Low $\le$ SL) and Target (High $\ge$ Target), **Stop Loss (`LOSS`) takes conservative precedence**.

---

# 5. QUALITY DATA AVAILABILITY AUDITOR & GOVERNANCE

Architectural separation between quantitative strategy scanners and upstream data availability/recovery diagnostics:

| Component | Responsibility | Permitted Actions | Prohibited Actions |
|---|---|---|---|
| **Quality Scanner** (`live_fundamental_scanner.py`) | *"Given certified data, does this stock pass strategy rules?"* | Evaluates certified PIT data, filters candidates | Network recovery calls, third-party oracle lookups |
| **Primary Recovery Engine** (`fundamental_pre_recovery.py` & `fundamental_source_router.py`) | *"Can we obtain and certify required data from authoritative sources?"* | Fetches Upstox, NSE XBRL, and Fyers approved feeds; updates canonical PIT staging | Writing uncertified data, skipping Never-Downgrade Gate |
| **Data Availability Auditor** (`data_availability_auditor.py`) | *"When authoritative sources report missing data, does an independent source indicate it exists?"* | Diagnostic checks against Fyers & Screener; raises admin alerts and logs discrepancies | Writing to PIT, writing production metrics, enabling BUY |

## 5.1 Strict 3-Tier Data Source Hierarchy
- **Tier 1 — Production-Authoritative**:
  - Upstox Fundamentals API (Annual Statements, Income, Balance Sheet, Cash Flow)
  - Upstox Key Ratios API (EV/EBITDA, P/E, Debt/Equity)
  - NSE Corporate Integrated Filing XBRL
  - Cryptographically audited local PIT filing cache (`data/pit_raw_filings/`)
- **Tier 2 — Approved Diagnostic Broker Source**:
  - **FYERS API v3** ([myapi.fyers.in/docsv3](https://myapi.fyers.in/docsv3))
  - **Capability Boundary**:
    ```text
    FYERS PUBLIC API V3
    ├── Market Quotes (/api/v3/quotes) → machine-readable diagnostic source
    └── Public REST Fundamentals → NOT AVAILABLE / DOCUMENTED
    FYERS WEB/PLATFORM FUNDAMENTALS
    └── EV/EBITDA, ROCE, ROE, etc. → reference evidence only unless an explicitly authorized machine-readable interface exists
    ```
  - Approved diagnostic broker source strictly for the capabilities exposed by its public API v3 (`/data/quotes`, `lp`, volume, depth, OHLC). Public REST fundamentals / key-ratios are NOT AVAILABLE / DOCUMENTED.
  - Web/platform fundamentals (EV/EBITDA, ROCE, ROE) serve as reference evidence only unless an explicitly authorized machine-readable interface exists.
- **Tier 3 — Forensic Reference Only**:
  - **Screener.in**: Strictly an offline diagnostic oracle (`FORENSIC_REFERENCE_ONLY`).
  - Mandatory invariant: `canonical_pit_write = FALSE`, `production_metric_write = FALSE`, `buy_decision = BLOCKED`.

## 5.2 Mandatory Governance Diagnostic Conditions
1. **DATA PROVIDER DISCREPANCY (`PRIMARY_RECOVERY_FAILURE_DATA_EXISTS_ELSEWHERE`)**:
   - Upstox = Missing, NSE = Missing, FYERS = Available, Screener = Available.
   - High-severity defect: Primary pipeline failed to acquire data that exists in an approved secondary source.
2. **SCREENER-ONLY DATA FOUND (`SCREENER_ONLY_DATA_SOURCE`)**:
   - Upstox = Missing, NSE = Missing, PIT = Missing, FYERS = Missing/Unavailable, Screener = Available.
   - **Semantic Classification Meaning**: *"Only verified/accessible reference source found"*. Specifically, among the sources we are authorized and able to verify programmatically in our audit layer, only Screener currently exposes this reference information. It highlights an upstream data ingestion gap in our primary pipeline, but does NOT assert that the data is absent from other non-public screens or web platform UI views.
   - Emits **discrete per-stock per-field** notification directly to `global_notifications` for the Admin Bell Icon (`#notif-badge`, `#notif-list`).
   - Format:
     ```text
     🚨 DATA SOURCE NOTICE: {symbol} — {field} found on Screener.in but unavailable from primary authoritative providers (Upstox/NSE/Exchange). This data will NOT be used for trading decisions. Potential upstream ingestion gap flagged for review.
     ```
   - Strictly enforces `canonical_pit_write = FALSE`, `production_metric_write = FALSE`, and `buy_decision = BLOCKED`.
3. **PARSER OR FIELD MAPPING FAILURE (`PARSER_OR_FIELD_MAPPING_FAILURE`)**:
   - HTTP 200 raw filings returned, raw records present, but target metric unextracted (0 usable fields extracted) despite sufficient historical filing depth.
   - **Strict Classification Precedence**: The auditor evaluates `INSUFFICIENT_HISTORICAL_DEPTH` (e.g. <5 annual statements for 5Y CAGR), `HISTORICAL_FILING_GAP`, and `INVALID_CAGR_BASE` BEFORE considering a parser/mapping bug. Stocks with only 3–4 annual filings (e.g. KRSNAA, MARSONS) are classified as historical depth limits, never parser failures.
4. **GENUINELY UNAVAILABLE (`DATA_UNAVAILABLE_VERIFIED`)**:
   - Missing across Upstox, NSE, PIT, FYERS, and Screener. Confirms legitimate public data absence.

## 5.3 Granular Diagnostic Categories & REST Endpoints
- `GET /api/admin/data_availability/counts`: Real-time counts across all 11 categories (`verified_data_missing`, `provider_discrepancies`, `fyers_only_data_found`, `screener_only_data_found`, `genuinely_unavailable`, `insufficient_historical_depth`, `stale_pit`, `unprocessed_filing`, `parser_mapping_failure`, `calculation_failure`, `structural_ineligible`).
- `GET /api/admin/data_availability/audits`: Stock-by-stock audit records containing complete 17-field provenance metadata.

## 5.4 Data Pipeline Architectural Invariants
1. **Dataset Schema Identity Gate (`DATASET_SCHEMA_IDENTITY`)**:
   - Strictly enforces column schema verification for `daily_builder_master_v2`, `canonical_pit_rebuilt`, `pit_fundamentals_v1`, and `pit_recovery_status` prior to database writes. Cross-dataset overwrites (e.g. uploading raw PIT statements as builder master) are permanently blocked in <1ms.
2. **Network-Free Scheduled Scanner**:
   - Scheduled/cron executions run with `allow_live_refresh = False`. Zero HTTP recovery calls or broker fetches occur inside the scanner evaluation loop. Missing data fails closed immediately (0ms). Warm scan SLA is <5.0 seconds (measured at 4.66s for 886 symbols).
3. **Durable Negative Availability Cache**:
   - `pit_recovery_status` is persisted to disk and synchronized with PostgreSQL to ensure negative knowledge survives container reboots without polluting canonical filing data.
4. **Funnel Reconciliation & Health Integrity**:
   - Suppression of alerts by Pre-BUY data integrity gates is tracked as `PRE_BUY_INTEGRITY_BLOCKED`.
   - Strict reconciliation: `buy_eligible == buy_alerts_created + buy_alerts_suppressed`.
   - If telemetry or reconciliation fails, health is reported as `DEGRADED`. It is strictly prohibited to override failed integrity checks to `OK`.

---

# 6. DASHBOARD SUITE & OPERATIONAL WORKFLOWS

## 6.1 User Dashboard (`/`)
- **Real-time Telemetry KPI Cards**: Active Positions, Total Closed Trades, Cumulative Win Rate (%), Net PnL (₹ and %), Active Viewers, VAPID Web Push Toggle.
- **Scanner Filter Tabs**: Instant filtering by active scanner engines: **ALL**, **TECHNICAL**, **WEALTH**, **MULTIBAGGER**, **QUALITY_RECOVERY**.
- **Active Signals Table & Live CMP Polling**: Real-time ticker prices polled from Upstox/Fyers with direct TradingView chart links, color-coded status badges, trailing stops, and target progress.
- **Corporate Action Badges**: Inline event chips (`E in 3d`, `D`, `S`, `B`) with priority sorting and `+N` overflow tooltips.

## 6.2 Admin Dashboard (`/admin`)
- **Active Scanner Health Grid**:
  - Live status cards for **DAILY_BUILDER**, **TECHNICAL**, **FUNDAMENTAL**, **QUALITY_COMPOUNDER**, and **QUALITY_VALUE_RECOVERY**.
  - Status Pills: `OK` (green), `RUNNING` (blue), `QUEUED` (yellow), `DEGRADED` (purple), `DOWN` (red).
  - Manual "Run Scanner Now" triggers with sliding queue tracking (`QUEUED-1`, `QUEUED-2`).
- **Admin Notification Bell Icon (`#notif-badge`, `#notif-list`)**:
  - Connected via SSE stream (`/api/notifications/stream`) and `/api/notifications`.
  - Displays discrete stock-level data source notices, parser mapping failures, and provider discrepancy alerts.
- **Counterfactual Shadow Tracking**: Monitors rejected candidates (`is_rejected = TRUE`) to track hypothetical performance (`SHADOW_WIN`, `SHADOW_LOSS`, `SHADOW_EXPIRED`) and calculate True/False Negative rates.
- **Process Lock Telemetry (`/api/lock-stats`)**: Displays active lock acquisitions, wait times, hold times, and contention events.
- **Memory Profiler Timeline**: Stage timeline breakdown and heap usage profiling.

## 6.3 Performance Tracker (`/performance`)
- **Cumulative Equity Curve**: Chart.js visual tracking of realized returns vs Nifty 50 benchmark.
- **Quantitative Performance Metrics**: Profit factor, win rate, average win/loss ratio, max drawdown, average holding days.
- **Monthly Return Heatmap Matrix**: Calendar grid of realized returns by month and year.
- **Historical Trade Audit Log**: Searchable, sortable audit table of every historical trade with full entry/exit parameters.

## 6.4 "Analyse Your Watchlist" Diagnostic System (`app/stock_analyzer.py`)
- **Master Ticker Validation**: 5-stage verification against master dictionary, BSE mappings, DB `symbol_mappings`, and Yahoo search.
- **Active Scanner Evaluators**: Evaluates candidate stocks against active production evaluators:
  - *Stage 1 (Daily Builder)*: `evaluate_daily_builder_symbol()`
  - *Stage 2 (Technical Breakout)*: `evaluate_technical_symbol()`
  - *Stage 3 (Wealth Engine)*: `evaluate_wealth_symbol()`
  - *Stage 4 (Multibagger Engine)*: `evaluate_multibagger_symbol()`
- **Inline Main Screen Diagnostic & Watchlist Layout**: Inline evaluation panel and personal monitored watchlist (`user_watchlists`) rendered directly on the main screen with click-to-add/toggle.

---

# 7. SYSTEM SCHEDULE & 24/7 EXECUTION TIMELINE

```
00:00 IST ── Midnight Rotation
              └─ Reset SessionContext, release daily caches, force gc.collect()
01:00 IST ── Daily Watchlist Builder Run (app/daily_builder.py)
              └─ Scrape exchange universe -> Liquidity & pledge filters -> data/watchlist.parquet
02:00 IST ── Wealth Engine Initial Pre-Market Sweep (app/wealth_engine.py)
              └─ Pre-calculate fundamental buckets and momentum scores for approved universe
04:00 IST ── Multibagger Engine Cold Start (app/multibagger.py)
              └─ Initial conviction tier evaluation on fresh daily watchlist
07:00 IST ── Master Symbols Database Registry Sync (/api/v1/admin/master_symbols/refresh)
              └─ Refresh PostgreSQL master_symbols table
08:30 IST ── Readiness Verification Check
              └─ Verify watchlist freshness, DB schema health, and data provider availability
09:14 IST ── Pre-Market Warmup (09:14:30 IST)
              └─ Pre-fetch intraday data to eliminate 09:15:00 market open tick lag
09:15 IST ── Market Open (SessionContext -> MARKET_OPEN)
              ├─ Every 5 min:  Wealth Engine CMP Exit Updates + Performance Tracker (<3s)
              ├─ Every 15 min: Technical Breakout Scan + Wealth BUY Scan + Multibagger Exit Monitor
              └─ Continuous:   Authoritative Outbox Parquet Reconciler
15:30 IST ── Market Close (SessionContext -> POST_MARKET)
15:30 IST ── Post-Market Earnings Calendar Refresh (app/earnings_calendar.py)
              ├─ Priority 1: Stocks with results expected TODAY re-checked immediately
              └─ Priority 2: Rest of universe (45d TTL known, 7d TTL missing)
19:00 IST ── Multibagger Daily Post-Market Scan (app/multibagger.py)
              └─ End-of-day conviction tier ranking and portfolio review
```

## 7.1 Standardized Scanner Execution Banners (`[VERSION: SCANNER_LOCK_BANNERS_v1.0]`)
Every active scanner and background monitor emits prominent log banners at `INFO` level upon lock acquisition and release:
- **Start Banner**: `********************* Starting <Scanner Name> Scanner at YYYY-MM-DD HH:MM:SS IST *********************`
- **Completion Banner**: `********************* <Scanner Name> Scanner completed at YYYY-MM-DD HH:MM:SS IST *********************`

---
*End of System Specification & Operational Guide — `docs/SYSTEM_SPECIFICATION.md`*
