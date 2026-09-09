# CONTRIBUTING_FOR_AI

This document outlines the **non-negotiable rules** for any AI system or agent operating on the Elite Breakout System codebase. You must read and abide by these rules before generating code, refactoring logic, or running tests.

## 0. Test Integrity Policy (MANDATORY)
Tests are the specification of the system, not something to modify in order to make a build pass.

**An AI agent must never modify a test merely because it is failing.** A failing test is evidence that either the implementation is wrong or the business requirement has intentionally changed. The AI must not assume the latter without explicit user confirmation.

### Rules
1. **DO NOT modify, weaken, delete, skip, or disable any test solely to make a failing build pass.**
2. **DO NOT modify any file under `tests/`, `tests/fixtures/`, or `tests/golden/` without explicit user approval.**
3. If a test fails, **assume the production code is incorrect first.** Investigate the root cause and fix the implementation if the business behavior has not intentionally changed.
4. Tests may only be modified when **the user explicitly approves a business or architectural change** that changes the expected behavior.
5. If you believe a test is incorrect or outdated, **Do NOT change it automatically.** Explain why the test is failing, why you believe it is no longer valid, and which business rule has changed. Wait for explicit user approval before modifying the test.
6. **Golden snapshots, fixtures, and baseline files are immutable.** Never regenerate or overwrite them automatically. If they differ, produce a diff and explain the reason. Wait for user approval before accepting a new baseline.
7. Every production bug fix should include a new regression test or strengthen an existing one so the same bug cannot silently reappear.

### Decision Rule
When a test fails, follow this order:
1. Fix the production code.
2. Re-run the tests.
3. If the test still appears incorrect, stop and ask for approval before changing any test.

**Changing tests is the last resort, never the first.**

## 1. Business Rules and Thresholds
- **Never change business rule thresholds without explicit human approval.** (e.g., `MIN_SCORE = 72`, `vol_z_score >= 3.0`).
- **Never edit watchlist filtering logic** without approval.
- **Never modify stop-loss or target generation algorithms** without simultaneously updating the behavioral regression tests to match.

## 2. Data Contracts
- **Never rename dictionary keys or model fields.** (e.g., changing `entry` to `entry_price`, or `stop` to `stop_loss`). The frontend dashboard, database schemas, and multiple subsystems tightly couple to these explicit names.
- **Preserve public interfaces** unless a versioned change (e.g., schema v1 to v2) is explicitly intentional and approved.
- The `Opportunity` object is immutable in its key structure.
- The `regime_ctx` object must always contain `trend, biases, policy`.

## 3. Testing Environment (The "Fort Knox" Rules)
- **No Internet in Tests:** The test suite (`pytest`) operates under a strict Zero Network Policy. Tests must NEVER hit Yahoo Finance, NSE, BSE, or any external API. All test data must be loaded from `tests/fixtures/`.
- **No Randomness in Tests:** Tests must be perfectly deterministic. If utilizing randomness for property-based tests, strict seeds must be used.
- **Snapshot Immutability:** Never overwrite a versioned Golden Snapshot (e.g., `market_snapshot_v1`). If the strategy intentionally changes, generate a `v2` baseline and document the reason. Never update golden snapshots automatically; snapshot changes require explicit review.
- **Always Explain Diffs:** If an AI change causes a snapshot diff, the AI must halt and explicitly explain the diff (and why it is expected) to the user before proceeding.

## 4. UI and Frontend Policies (MANDATORY)
1. **No Disruptive Background Polling:** Never place `window.scrollTo`, `location.reload`, or focus-stealing logic inside `setInterval` or background data refresh loops (like `doRefresh`). 
2. **Preserve User State:** Background updates must mutate the DOM in-place (e.g., updating a table row or a metric) seamlessly without disrupting the user's current scroll position, selected text, or form inputs.
3. **Graceful Degradation:** If an API endpoint fails, the UI must gracefully log the error without causing infinite refresh loops or blinding the user with repeated error modals.

## 6. Scanner Performance & API Optimization Invariants (MANDATORY)

Any new scanner, pipeline modification, or system update must strictly adhere to these hard performance invariants:

1. **Cache-First Fundamentals & Zero Unconditional Bypass**:
   - Never set `force_refresh=True` by default in scanner loops or Pass 2 finalist hydrations.
   - Always check warm local/PostgreSQL caches first (<1ms). External web scraping (Screener.in, TradingView, NSE, Yahoo) is strictly a fallback when cache is missing.
2. **Hard Latency Budgets on Hydration**:
   - Any external enrichment step (e.g. Pass 2 deep balance sheets) must have a hard per-symbol timeout ($\le$ 4.0s) and an overall stage timeout ($\le$ 15.0s via `as_completed(futures, timeout=15.0)`).
   - If external enrichment times out or fails, gracefully fall back immediately: `cached data -> baseline TradingView metrics -> safe mathematical derivations (e.g. total_equity = mcap / pb) -> continue scoring`. External web scraping must **never** hold a scanner hostage.
3. **No Single-Symbol Network Calls in Loops (Invariant 4 Circuit Breaker)**:
   - Bulk pre-fetch all live quotes (`get_live_prices(symbols)`), promoter pledge (`promoter_pledge_cache`), peer medians, and corporate action splits *before* candidate evaluation loops.
   - Never call single-symbol network APIs (e.g., `requests.get`, `yf.Ticker`, `fetch_promoter_pledge`, `fetch_screener_fundamentals`) inside candidate evaluation loops. Single-symbol requests inside loops must hit RAM memory in 0ms.
4. **Corporate Actions & Splits in RAM**:
   - Corporate action split verification during portfolio evaluation must use RAM-cached factors (`get_bulk_split_factor`) rather than calling synchronous `yf.Ticker(sym).splits` per position.
5. **Smart TTL Cache Downloads**:
   - Never force complete database downloads (`download_parquet_from_db` / `force_db_sync=True`) on every scan run if the local file is fresh (< 20 min TTL).
6. **Alert Recalculate & Replay Parity**:
   - The candle-by-candle (5m/tick) replay engine in `performance_tracker.py` must maintain 100% parity with real-time exit monitoring:
     - Clear stale `SL_HIT`/`WIN` states and calculate shares defensively on reset.
     - Append live tick bridge during active market hours.
     - Sequential multi-target trailing SL: T1 Hit $\to$ trail SL to `max(sl, effective_entry * 1.003)` (Breakeven + buffer); T2 Hit $\to$ trail SL to `max(sl, t1)`; T3 Hit $\to$ full profit exit.
7. **Lock Hierarchy & Non-Interfering Intraday Scanners**:
   - Heavy full-universe scans (EOD, Reversal, Multibagger, Wealth Engine) acquire `ProcessLock("global_scanner_lock")` sequentially.
   - Multi-TF 15m/5m monitors use dedicated non-interfering locks (`multitf_scanner_lock`, `multitf_scanner_5m_lock`) allowing parallel execution without blocking the global scanner queue.
8. **Real-Time Web Alerts & SSE Stream Invariant**:
   - Whenever an alert is saved (`save_alert_if_new`) or notification created (`_insert_notification_sync`), immediately broadcast a real-time event via Server-Sent Events (`notify_stream_clients("alert"|"notification", ...)`).
   - Web dashboards must react immediately to SSE events (update bell badge, trigger alert sound, and pre-fetch fresh trade data) to match mobile notification speed with 0ms human-perceived delay.
9. **Zero-Cache Fallback for Notification Clicks & Symbol Search**:
   - When a user clicks a notification or searches a symbol (`filterSymbol`), the web application must NEVER display an empty/stale state if the frontend cache is cold.
   - It must execute a direct real-time PostgreSQL check (`GET /api/alert/by_symbol/<symbol>`) to fetch the full trade record (CMP, SL, Targets, Status, P&L) and prepend/hydrate it directly into `ALL_TRADES` in memory.
10. **High-Performance History & Error APIs Indexing**:
   - Unacknowledged error queries (`fetch_errors`, `system_logs`) must use partial indexes (`WHERE is_acknowledged = FALSE`) to execute in $<1$ms.
   - All history, error, and alert endpoints must explicitly declare `Cache-Control: no-cache, no-store, must-revalidate` and invalidate on every status change.
11. **V2 Master Orchestration & Screen Performance Invariant**:
    - All V2 master endpoints (`/api/v2/confirmed_signals`, `/api/v2/stocks_to_watch`, `/api/v2/investment_watch`, `/api/v2/portfolio_actions`, `/api/v2/confluence_breakdown`, `/api/v2/master_summary`) must execute in $<25\text{ms}$ by adhering to RAM-first resolution.
    - `_ensure_contract_keys` must never perform per-symbol disk scans (`pd.read_parquet`), single-item database queries, or heavy mapper initializations in request loops. If a CMP or TradingView symbol is already present or memoized in RAM, format it in $<0.01\text{ms}$.
    - Batch price resolution (`_batch_resolve_cmps`) must resolve missing symbols via a single bulk SQL round-trip to `stock_analysis_master`, populate `_FAST_CMP_MEMO`, and never trigger synchronous blocking network requests inside HTTP handlers.
    - Thread-safe micro-caching (5s TTL) with thundering-herd mutex protection ensures zero duplicate backend executions during concurrent tab switches, while instant cache invalidation flushes all cached data upon any trade alert mutation.

By enforcing these boundaries, we protect the production pipeline from silent regressions, performance degradation, and accidental feedback loops.
