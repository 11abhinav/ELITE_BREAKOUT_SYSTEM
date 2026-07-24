# Changelog

All notable changes to the Elite Breakout System architecture and capabilities will be documented in this file.

## [v8.4.2] - Line-by-Line Scanner Audit, Intraday Cache Struct & Resilient Memory Release (2026-07-24)
### Fixed & Improved
*   **EOD Scanner Per-Batch Truncation Fix (`app/eod_scanner.py`)**: Moved candidate accumulation, `SCANNER_MAX_ALERTS` ranking, and DB persistence outside the 50-stock batch loop (`chunk_iterable`). Enables candidate evaluation across the entire watchlist universe before truncating to top-10 alerts.
*   **Reversal Scanner Live Price Stitching Fix (`app/reversal_scanner.py`)**: Corrected conditional logic during intraday snapshot stitching. Prevents setting `Close = None` and dropping today's trading candle when intraday snapshots are missing or empty.
*   **Multi-TF ProviderResult Guard (`app/multi_tf_scanner.py`)**: Added explicit `isinstance(df, pd.DataFrame)` guards before calling `.empty` or `.iloc[-1]` on `data_15m` and `data_30m` entries, eliminating `AttributeError` crashes when providers return `ProviderResult` status enums.
*   **Pullback Pipeline Cooldown Deduplication (`app/pullback_pipeline.py`)**: Added missing `cooldown_alerts` check (`(symbol, "PULLBACK") in cooldown_alerts`) inside the candidate loop, preventing identical duplicate pullback alerts on consecutive days.
*   **Intraday Cache `KeyError: 'ts'` Fix (`app/price_cache.py`)**: Updated `get_intraday_snapshot()` to inspect symbol-level dictionary entries (`_cache[cache_key][symbol]["ts"]`) instead of querying top-level `'ts'`, restoring fast intraday snapshot retrieval for Wealth Engine and Reversal Scanner.
*   **Permanent `NON_MONOTONIC_TIMESTAMPS` Resolution (`app/price_cache.py`)**: Enforced `pd.to_datetime(..., errors='coerce')`, NaN removal, deduplication, and chronological sorting inside `validate_ohlcv_structure()` and `_download_all_robust()`. Solved lexicographical string sorting mismatches on provider timestamps (e.g., Fyers/Yahoo `^NSEI` 15m candles).
*   **Multibagger Candidate Truncation (`app/multibagger.py`)**: Enforced `SCANNER_MAX_ALERTS.get("MULTIBAGGER", 10)` ranking and truncation prior to live price fetching and DB persistence.
*   **Test Suite & Registry Isolation (`tests/`)**: Updated `test_scanner_runtime.py`, `test_fault_injection.py`, `test_architecture_verification_suite.py`, and `test_institutional_scoring.py` with mock isolation to prevent connection leaks and preserve `DatasetRegistry` schema definitions.

## [v8.4.1] - Per-Symbol Granular Cache & Single-Pass Bulk Pre-Fetch Release (2026-07-24)
### Fixed & Improved
*   **Per-Symbol Granular RAM Cache Architecture (`app/price_cache.py`)**: Refactored `_cache` from chunk-overwriting dictionary pointers to an explicit 3-tier per-symbol structure: `_cache[(interval, period)][symbol] = {"data": df, "ts": time.monotonic(), "data_as_of": dt, "schema_version": "v8.4.0"}`. Enables independent per-symbol freshness tracking, partial symbol refreshes, and eliminates cache destruction across chunked scanner runs.
*   **Single-Pass Bulk Pre-Fetch Model (`app/multi_tf_scanner.py`)**: Converted Phase A in `multi_tf_scanner.py` to pre-fetch the entire 295-symbol watchlist in a single logical request before entering calculation loops. Provider-level batching (30 symbols/chunk) is encapsulated cleanly inside `PriceCache` / `_download_all_robust`.
*   **Empirical Performance Speedup**: Benchmarked 100% warm RAM cache hits executing in **0.014s (14.2 ms)** versus 8.92s cold start (**628x empirical speedup**). Eliminated the 14-minute execution delay on scheduled 15-minute Multi-TF ticks.

## [v8.4.0] - Multi-TF 1H Bar Count, Fyers 99-Day Range Cap & Wealth Hybrid Cadence Release (2026-07-24)
### Fixed
*   **Multi-TF 1H Bar Count Fix (`app/multi_tf_scanner.py`)**: Updated 1H candle fetch period from `1mo` to `3mo`. Provides ~437 bars so `SMA200` calculates with 100% non-NaN precision for all 314 symbols, allowing qualifying breakout stocks to enter the Hourly Passed (1H) dashboard table.
*   **Fyers API Intraday 99-Day Range Cap (`app/data_providers/fyers_fetcher.py`)**: Enforced a strict 99-day cap for intraday/hourly resolutions (`1m` to `240m` / `1h`). Fixes Fyers API error `-50` (`range_to cannot be 100 days greater than range_from`) while providing 437 1H bars for Phase A `SMA200`.
*   **Wealth Engine CMP Patch Overwrite Fix (`app/wealth_engine.py`)**: Fixed live price patching conditional logic so intraday CMP is patched into `hist_df` only when `snap_df` contains a valid close, preserving `hist_df` untouched when `snap_df` is missing.
*   **FyersFetcher `_get_date_range()` Return Restoration (`app/data_providers/fyers_fetcher.py`)**: Restored `return range_from, range_to` in `_get_date_range()` to fix `TypeError: cannot unpack non-iterable NoneType object`.
*   **Wealth Engine Top-Level Database Import (`app/wealth_engine.py`)**: Added top-level `import database` to prevent `NameError: name 'database' is not defined` during intraday portfolio updates.
*   **Multibagger Timezone Reference Fix (`app/multibagger.py`)**: Fixed `IST_ZONE` -> `IST` reference on line 1692 to prevent `NameError` at end of scan execution.

### Added
*   **Wealth Engine Hybrid 2-Tier Schedule (`app/main.py`)**: Configured `safe_run_wealth_market_hours()` to run full 308-stock BUY alert scans every 15 minutes during market hours and fast CMP position exit updates (<3.0s) every 5 minutes.
*   **Scanner Rejection Telemetry Standardization (`app/funnel_telemetry.py` & All 6 Scanners)**: Standardized `log_funnel_metrics()` and explicit `📊 Final Rejections: ...` logging across all 6 scanners for server log visibility.

---

## [v8.3.0] - High Performance Dashboard, Connection Pool Resilience & Robustness Release (2026-07-24)
### Added
*   **Gzip Response Compression Middleware (`app/dashboard_server.py`)**: Added native `after_request` gzip compression for all HTTP responses exceeding 500 bytes. Reduces Admin Dashboard HTML transfer from **260KB to ~30KB** and JSON performance payload from **10MB to ~500KB**.
*   **Session Check In-Memory Cache (`app/dashboard_server.py`)**: Added `_cached_check_session()` with a 60-second TTL for `@login_required` and `@admin_required` decorators, eliminating 90%+ per-request PostgreSQL session validity queries.
*   **Single-Query Batch Scanner Status (`app/database.py` & `app/dashboard_server.py`)**: Implemented `get_all_scanners_today_trades()` to replace N+1 sequential SQL loop in `/api/scanner_status` with a single SQL query.
*   **Scanner Execution Duration Telemetry Across All Scanners (`app/wealth_engine.py`, `app/multibagger.py`, `app/main.py`)**: Instrumented `Wealth Engine`, `MULTIBAGGER`, `DAILY_BUILDER`, and `PERFORMANCE_TRACKER` to capture `start_time` and pass `duration_seconds` to `upsert_scanner_health()`, ensuring DB scanner run time updates after every execution.
*   **Alerts Table `exit_reason` Column Migration (`app/database.py` & `app/multibagger.py`)**: Added `exit_reason TEXT` column to `alerts` table migration in `init_db()` and updated `multibagger.py` exit monitor to populate both `exit_signal` and `exit_reason`, preventing `psycopg2.errors.UndefinedColumn` errors on missing price data review alerts.
*   **AI Analyzer Markdown Code Fence Stripping (`app/ai_analyzer.py`)**: Added markdown code block fence stripping (` ```json ` and ` ``` `) in `_try_gemini_model()` prior to `json.loads()` to prevent `JSONDecodeError` crashes on Gemini response text.
*   **Trade Ranking Engine NaN/Inf Protection (`app/trade_ranking_engine.py`)**: Added `_safe_float()` helper with `math.isnan()` and `math.isinf()` safeguards in `TradeRankingEngine` to prevent NaN/Inf propagation during candidate sorting.
*   **Global Flask API Error Handlers (`app/dashboard_server.py`)**: Added `@app.errorhandler(500)` and `@app.errorhandler(404)` handlers to ensure structured JSON responses (`{"status": "error", "error": "Internal Server Error"}`) for `/api/*` routes.

### Changed
*   **PostgreSQL Connection Pool Capacity & Timeout (`app/database.py`)**: Increased default `DB_MAXCONN` fallback from 30 to **50** and extended `get_connection()` acquire timeout from 5s to **15s** to handle spike API traffic and concurrent background daemons.
*   **Session Validity Timeout Fallback (`app/database.py`)**: Updated `check_session_validity()` to return `True` (preserving valid active sessions) when connection pool timeouts occur, preventing 500 HTTP errors during transient DB load spikes.
*   **Pledge Worker DB Connection Batching (`app/pledge_worker.py`)**: Consolidated DB cache writes in `process_symbol()` using a single `save_pledge_cache()` helper, eliminating 900+ connection checkouts per scraping cycle.
*   **Parallel Frontend Data Fetching (`app/admin_dashboard.html` & `app/user_dashboard.html`)**: Parallelized initial dashboard data fetches via `Promise.all()` and removed sequential 4-URL fallback loops.

---

## [v8.2.1] - Comprehensive System Audit & Stabilization Release (2026-07-24)
### Fixed
*   **ProcessLock Distributed Advisory Lock Exception Handling (`app/lock_utils.py`)**: Fixed `acquire()` method so exceptions during PostgreSQL advisory lock acquisition or DB connection clean up local handles, release thread locks, and return `False` (not `True`). Prevents concurrent scanner execution across Railway containers during DB connection glitches (`[VERSION: PROCESS_LOCK_EXC_FIX_v1.0]`).
*   **UnifiedFetcher Live Quote Batch `KeyError` Crash (`app/data_providers/unified_fetcher.py`)**: Replaced `pending.remove(...)` with `pending.discard(...)` across quote fetchers to safely handle duplicate or missing input symbols without raising fatal `KeyError` crashes (`[VERSION: UNIFIED_FETCHER_KEYERROR_FIX_v1.0]`).
*   **UnifiedFetcher Yahoo MultiIndex Column Extraction (`app/data_providers/unified_fetcher.py`)**: Updated quote price parsing to dynamically inspect both level 0 and level 1 MultiIndex hierarchies regardless of batch chunk size (`[VERSION: UNIFIED_FETCHER_MULTIINDEX_FIX_v1.1]`).
*   **IndicatorManager Dynamic History-Aware Computations (`app/indicator_manager.py`)**: Replaced single 200-bar minimum requirement with indicator-specific history thresholds (14 bars for ATR/RSI, 20 for EMA20/SMA20, 50 for EMA50/SMA50, 200 for EMA200/SMA200). Added auto-registration for dynamic `indicator_{symbol}` keys in `DatasetRegistry` to prevent `ValueError` logs (`[VERSION: INDICATOR_REGULAR_HISTORY_v1.0]`).
*   **Delivery Data Series Prioritization (`app/delivery_data.py`)**: Added series rank sorting (`EQ` > `BE` > `SM` > `BZ`) prior to dictionary extraction to preserve primary equity delivery percentages when a symbol appears under multiple series (`[VERSION: BHAVCOPY_SERIES_PRIORITY_v1.0]`).
*   **Audit Regression Test Suite (`tests/test_audit_fixes.py`)**: Added 5 dedicated regression unit tests covering `ProcessLock` exception behavior, `UnifiedFetcher` duplicate symbol safety, MultiIndex column extraction, `IndicatorManager` history levels, and Bhavcopy series prioritization.

## [v8.2.0] - Scheduler Correctness, Lock Telemetry & Invariants Release (2026-07-24)
### Added
*   **Scheduler Production Contract (`force=True`)**: `_run_eod_with_retries`, `_run_reversal_with_retries`, and `_run_pullback_with_retries` in `app/main.py` pass `force=True` when invoking scanner entrypoints. The scheduler owns the decision of when to execute (after Bhavcopy verification), ensuring scanners do not enter `test_mode` and discard alerts when running before 21:00 IST.
*   **Candle-Aligned Multi-TF Cadence**: Replaced the once-at-15:00 trigger with a recurring 15-minute candle-aligned schedule (`:00`, `:15`, `:30`, `:45`) during market hours (09:30 AM to 14:45 PM IST). Cycles stop launching after 14:59 to guarantee at least 45 minutes before market close for Phase D trade execution and risk management.
*   **Scanner Run Duration Telemetry**: Instrumented all core scanners (`EOD`, `REVERSAL`, `PULLBACK`, `PERFORMANCE_TRACKER`, Admin Manual Triggers) to capture `duration_seconds` in `scanner_health` DB table. Served via `/api/scanner-health` (`last_duration`, `last_duration_formatted`) and displayed visually on the Admin Dashboard.
*   **InstrumentedLock & Contention Monitoring**: Replaced standard `threading.Lock()` for `scanner_execution_lock` with `InstrumentedLock`. Tracks `acquisitions_count`, `total_wait_seconds`, `max_wait_seconds`, `total_hold_seconds`, `max_hold_seconds`, and `contention_events_count`. Added `/api/lock-stats` endpoint and threshold warnings (`LOCK_WAIT_WARNING_SECONDS = 5.0`, `LOCK_HOLD_WARNING_SECONDS = 60.0`).
*   **De-nested Evening Scanner Locks**: Split the global evening lock so EOD, Reversal, and Pullback individually acquire/release `scanner_execution_lock` around their execution blocks, with `time.sleep(15)` moved outside lock contexts to prevent holding mutexes during passive waiting.
*   **Centralized Trade Structure Validator**: Added `TradeStructureValidator` in `sl_target_helper.py` to enforce mathematical invariants across all engines: `entry > 0`, `raw_sl < entry`, `risk > 0`, `target_1 >= entry`, `entry <= target_1 <= target_2 <= target_3` (ordered structural targets), `RR >= min_rr`, and explicit `INVALID_STOP_PLACEMENT` rejection when `raw_sl >= entry`.

### Changed / Deprecated
*   ~~**21:00 Time-Gate on `already_ran` Check**~~: *(Replaced on 2026-07-24 by `SCHEDULER_CORRECTNESS_v1.0`)* Removed the `if ls_dt.time() >= start_time:` constraint. Any successful run today (`status == 'OK'` starting with today's date) is counted as today's authoritative production run.
*   ~~**One-shot 15:00 Multi-TF Trigger**~~: *(Replaced on 2026-07-24 by candle-aligned market-hours cadence)* Removed `multi_tf_ran` state variable in favor of 15-minute slot tracking (`last_multi_tf`).

---

## [v6.3.0] - Architecture Governance Release
### Added
*   **Dataset Provenance**: Injected `df.attrs` at runtime to establish a permanent audit trail of data acquisition sources.
*   **Dataset Registry**: Centralized dataset ownership to enforce deterministic memory and lifecycle management.
*   **Unified Fetcher & Provider Selector**: Established a single I/O boundary to enforce provider selection policy and safe fallback chains.
*   **Indicator Manager**: Centralized technical indicator computation (SMA, EMA, RSI, ATR) to eliminate redundant execution across multiple scanners.
*   **ScraperAPI Integration**: Formalized the residential proxy layer specifically for NSE data routes (Bhavcopy, Delivery, Promoter Pledge) to prevent automated IP bans.
*   **Granular Locking**: Implemented resource-scoped network locks to preserve parallel CPU execution for scanners.

### Removed
*   All unmanaged mutable globals containing business logic data.
*   All direct, untracked `fyers.quotes()` and `yf.download()` calls embedded inside scanner or application logic.
