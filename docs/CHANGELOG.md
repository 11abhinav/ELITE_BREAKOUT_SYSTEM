# Changelog

All notable changes to the Elite Breakout System architecture and capabilities will be documented in this file.

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
