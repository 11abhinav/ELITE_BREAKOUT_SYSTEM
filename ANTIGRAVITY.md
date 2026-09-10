# ANTIGRAVITY — ELITE BREAKOUT SYSTEM ARCHITECTURAL MANDATE & DIRECTORY MAP

> **Document Class:** Root Architectural Summary & System Map
> **Target File:** `ANTIGRAVITY.md`
> **Last Synchronized:** 2026-07-25 (v8.4.3+)

---

## 1. STRICT TWO-DOCUMENT DOCUMENTATION MANDATE

Per system mandate, the repository documentation under `docs/` is consolidated into **EXACTLY TWO MASTER CANONICAL DOCUMENTS**:

1. **User & Admin Functional Specification**: [SYSTEM_SPECIFICATION.md](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/docs/SYSTEM_SPECIFICATION.md)
   - **Target Audience**: Users, Traders, Portfolio Managers, System Administrators.
   - **Scope**: Explains **WHAT** the system does and **HOW TO USE IT**.
   - **Contents**: Full functional specifications for all 6 scanning engines (EOD Breakout, Multi-TF Intraday, Reversal, Pullback Pipeline, Wealth Engine, Multibagger Engine), trade signal entry/exit rules, initial & trailing stop-loss mechanics, multi-target profit taking (T1..T4), exit alerting, operational user workflows for all 3 dashboards (User Dashboard, Admin Dashboard, Performance Tracker Dashboard), notification channels (Telegram, Web Push, Portal), and 24-hour system operating timelines.

2. **Developer & AI Technical Architecture & Reconstruction Blueprint**: [SYSTEM_ARCHITECTURE.md](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/docs/SYSTEM_ARCHITECTURE.md)
   - **Target Audience**: Core Systems Engineers, Software Developers, AI Coding Models.
   - **Scope**: Explains **HOW THE SYSTEM IS DESIGNED & WORKS INTERNALLY**.
   - **Contents**: Complete technical blueprint containing all mathematical & quantitative algorithms (RSI, ADX, EMA, ATR, `FM_Score`, `ScoringEngine` 0-100 breakdown, `compute_sl_and_target`, `TradeStructureValidator`, regime policies, forensic risk tiers), step-by-step scanner code flows, full-universe candidate accumulation and top-10 truncation, data acquisition routing (`ProviderSelector`), data provider fallback chains (Fyers API -> YFinance -> BSE), 3-tier per-symbol `_cache` RAM topology, timestamp normalization, BSE mapping state machine, Bhavcopy 0-to-4 day lookback fallback, complete PostgreSQL DDLs (all 42 tables, indexes, constraints, pool settings), mutex lock hierarchies (`InstrumentedLock`, `ProcessLock` advisory locks), REST API specs with payload JSON schemas, 88+ module directory map, 17 Production Deployment Gates, and V9 clean architecture blueprint with deprecation log (`~~old rule~~` + annotations).

---

## 2. CORE SYSTEM INVARIANTS

1. **IST Timezone Rule**: All timing, trading schedules, candle calculations, and database timestamps MUST be evaluated in **IST (UTC+5:30)**.
2. **RS Currency Rule**: All financial amounts, target gains, and values MUST be denominated in **Indian Rupees (₹ / RS)**.
3. **Strict 2-Document Rule (Rule 58)**: No other documentation files may exist under `docs/`. All operational details live in `docs/SYSTEM_SPECIFICATION.md` and all technical/architectural reconstruction details live in `docs/SYSTEM_ARCHITECTURE.md`.
4. **Per-Symbol Granular RAM Cache (ADR-003)**: `_cache[(interval, period)][symbol] = {"data": df, "ts": monotonic_ts}` with per-symbol TTL tracking.
5. **Full-Universe Candidate Accumulation (ADR-005)**: Scanners accumulate candidate setups across all universe chunks before executing global score sorting and `SCANNER_MAX_ALERTS` (top 10) truncation.
6. **Data Provider Selector Boundary (ADR-006)**: Data acquisition routing is strictly delegated to `ProviderSelector` in `app/data_providers/provider_selector.py`.
7. **Un-nested Candidate Verification Locks**: Summary reporting, DB alert verification, `upsert_scanner_health()`, and memory purges run un-nested at function scope.
8. **Cache-First Fundamentals & Bounded Hydration (ADR-007)**: Never bypass warm fundamentals cache (`force_refresh=False`). All secondary enrichment steps must enforce strict per-symbol ($\le$ 4.0s) and stage timeouts ($\le$ 15.0s via `as_completed(futures, timeout=15.0)`) with graceful fallback to baseline metrics and safe mathematical derivations (`total_equity = mcap / pb`). External scraping must never hold a scanner hostage.
9. **No Single-Symbol Network Calls in Loops (Invariant 4 Circuit Breaker)**: Bulk pre-fetch all live quotes (`get_live_prices(symbols)`), promoter pledge, and peer medians *before* candidate evaluation loops. Single-symbol requests inside loops are blocked and served from RAM cache in 0ms.
10. **Corporate Actions & Splits in RAM (ADR-008)**: Corporate action split verification during portfolio evaluation must use RAM-cached factors (`get_bulk_split_factor`) rather than calling synchronous `yf.Ticker(sym).splits` per position.
11. **Alert Recalculate & Replay Parity (ADR-009)**: The candle-by-candle (5m/tick) replay engine must maintain 100% parity with real-time exit monitoring, including state initialization, market-hours live tick bridge, and sequential multi-target trailing SL progression (T1 $\to$ Breakeven + buffer, T2 $\to$ T1, T3 $\to$ Full exit).
12. **Lock Hierarchy & Non-Interfering Intraday Scanners (ADR-010)**: Heavy full-universe scans acquire `ProcessLock("global_scanner_lock")` sequentially; Multi-TF 15m/5m monitors use dedicated non-interfering locks (`multitf_scanner_lock`, `multitf_scanner_5m_lock`) allowing parallel execution without blocking the global scanner queue.
13. **Real-Time Web Alerts & SSE Stream Invariant (ADR-011)**: Whenever an alert is saved (`save_alert_if_new`) or notification created (`_insert_notification_sync`), broadcast a real-time event via Server-Sent Events (`notify_stream_clients("alert"|"notification", ...)`). Web dashboards react immediately (bell badge update, sound ding, pre-fetching trade details) to match mobile notification speed with 0ms delay.
14. **Direct-DB Fallback for Notification Clicks & Symbol Search (ADR-012)**: When a user clicks a notification or searches a symbol (`filterSymbol`), the web frontend must NEVER display an empty/stale state if the frontend cache is cold. It must execute a direct real-time PostgreSQL check (`GET /api/alert/by_symbol/<symbol>`) to fetch the full trade record (CMP, SL, Targets, Status, P&L) and prepend/hydrate it directly into `ALL_TRADES` in memory.
15. **High-Performance History & Error APIs Indexing (ADR-013)**: Unacknowledged error queries (`fetch_errors`, `system_logs`) must use partial indexes (`WHERE is_acknowledged = FALSE`) to execute in $<1$ms. All history, error, and alert endpoints must explicitly declare `Cache-Control: no-cache, no-store, must-revalidate` and invalidate on every status change.
16. **Pre-Push Continuous Codebase Integrity Invariant (ADR-014)**: Before any git commit or push to production, the repository MUST pass automated static analysis (`test_codebase_integrity.py` via AST & Pyflakes) asserting 0 undefined variables, 0 unassigned locals, clean import syntax across all files, and 100% schema column alignment on database queries (e.g. `scanner_candidates` using `candidate_id`, `trigger_level`, `last_seen_volume`).

---
*End of Root System Specification — `ANTIGRAVITY.md`*
