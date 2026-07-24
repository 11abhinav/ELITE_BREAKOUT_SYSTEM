# ENGINEERING SPECIFICATION

> **Note:** This document serves as the absolute technical blueprint of the platform. It details architectural invariants, memory management, locking mechanics, and data contracts. An experienced engineering team can reconstruct the exact infrastructure from these specifications without reading the Python source code.

## 1. Architectural Invariants (The Constitution)

These rules **MUST NEVER** be violated in any implementation:
1. **Unified Acquisition**: All externally fetched datasets MUST enter through `UnifiedFetcher`. No direct API calls (`requests.get`, `yf.download`) in business logic.
2. **Policy-Driven Routing**: Provider selection (Fyers vs Yahoo vs NSE) MUST be delegated to `ProviderSelector`.
3. **Indicator Centralization**: No scanner may compute shared indicators. `IndicatorManager` is the sole owner.
4. **Governed Memory**: All shared datasets MUST exist in the `DatasetRegistry`. No mutable module-level state.
5. **Lifecycle Exclusivity**: Only `LifecycleManager` may release datasets.
6. **NSE Protection**: Official NSE datasets (Bhavcopy, Block Deals, Pledges) MUST retain the `ScraperAPI` residential proxy acquisition path.
7. **Application Singleton**: `ApplicationContext` is the ONLY application singleton.
8. **Session Bounding**: `SessionContext` is the ONLY owner of trading-session state.
9. **Scheduler Production Contract**: The production scheduler owns the decision of *when* to execute. When prerequisites are met, scanners MUST be called with `force=True` so they do not override scheduler timing by entering `test_mode` *(Added 2026-07-24 by `SCHEDULER_CORRECTNESS_v1.0`)*.
10. **Trade Structure Invariant Centralization**: All stop loss, target, and risk-reward validation MUST be delegated to `TradeStructureValidator` in `sl_target_helper.py`. No scanner engine may compute unvalidated risk models *(Added 2026-07-24 by `CENTRALIZED_TRADE_VALIDATOR_v1.0`)*.
11. **PostgreSQL Connection Pool Capacity & Resilience**: Default connection pool size MUST maintain `DB_MAXCONN=50` with an acquire timeout `timeout=15s`. `get_connection()` context managers MUST rollback open transactions on checkout release to prevent pool poisoning *(Added 2026-07-24 by `DB_POOL_RESILIENCE_v1.0`)*.
12. **Session Validity In-Memory Caching**: Authentication decorators `@login_required` and `@admin_required` MUST route session validation through `_cached_check_session()` (60s TTL) to prevent DB query flooding from high-frequency frontend polling endpoints *(Added 2026-07-24 by `SESSION_CACHE_v1.0`)*.
13. **Response Compression**: All API and HTML responses exceeding 500 bytes MUST pass through the native gzip compression middleware in `dashboard_server.py` to minimize network payloads *(Added 2026-07-24 by `GZIP_MIDDLEWARE_v1.0`)*.

---

## 2. Dataset Registry Deep Dive

The `DatasetRegistry` governs memory sharing. Every dataset must be declared with a strict schema and lifecycle policy.

### Registered Datasets Contract
| Dataset | Owner | Preferred Provider | Fallback Chain | Tier | Refresh Trigger |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `price_1d` | HistoricalData | `yahoo` | `fyers`, `bse` | DURABLE | End of Day |
| `price_15m` | HistoricalData | `fyers` | `yahoo`, `bse` | EPHEMERAL | Memory Pressure |
| `price_1m` | HistoricalData | `fyers` | `yahoo`, `bse` | EPHEMERAL | Memory Pressure |
| `live_quotes` | UnifiedFetcher | `fyers` | `yahoo`, `bse` | EPHEMERAL | 5-minute TTL |
| `bhavcopy_delivery` | DeliveryData | `nse` (ScraperAPI)| None | DURABLE | Daily (18:30-19:30 IST) |
| `block_deals` | InstitutionalData| `nse` (nsearchives)| None | EPHEMERAL | Memory Pressure |
| `blacklist` | Surveillance | `nse` (ScraperAPI)| None | EPHEMERAL | New Session |
| `promoter_pledge` | HistoricalData | `nse` (ScraperAPI)| None | DURABLE | Monthly |
| `watchlist` | DailyBuilder | None | None | DURABLE | New Trading Day |
| `fundamentals_cache`| Fundamentals | None | None | DURABLE | Weekly |

### Runtime Data Provenance
Every generated DataFrame must inject provenance metadata directly into the object:
```python
df.attrs = {
    "dataset": "price_1d",
    "provider": "yahoo",
    "fallback_used": False,
    "fetch_timestamp": "2026-07-24T18:30:00+05:30"
}
```

---

## 3. Memory & Lifecycle Management

The system is designed to run indefinitely (months of uptime) within fixed RAM constraints (e.g., 512MB Railway container) by explicitly governing memory.

### LifecycleManager
- **Monitoring:** Runs on a dedicated background thread monitoring `psutil` memory utilization.
- **Eviction Trigger:** If memory reaches `80%`, the manager forcefully sweeps the `DatasetRegistry`.
- **Sweep Logic:** 
  1. Identifies all `EPHEMERAL` datasets.
  2. Drops the oldest cached generations.
  3. Forces a JVM-style Garbage Collection (`gc.collect()`).
- **Session Rotation:** At 00:00 IST, it triggers a deterministic rotation. The `SessionContext` is burned down, clearing all daily state, and a fresh context is initialized.

### BatchMemoryTracker
- Scanners run over 300+ symbols. They process chunks in standard batches (default 50).
- Using a context manager (`with BatchMemoryTracker(...)`), memory is tracked before and after each batch. If a specific batch leaks memory, the tracker catches it and triggers emergency GC.

---

## 4. Concurrency & Lock Mechanics

The system heavily parallelizes CPU-bound scanner loops while strictly synchronizing I/O and shared scanner executions.

### ProcessLock (`lock_utils.py`)
- Used per scanner module to prevent concurrent executions of the same scanner.
- E.g., `_scan_lock = ProcessLock("eod_scanner")` (or `threading.Lock()`).
- If a scanner is triggered while already running, the lock rejects the request cleanly with a `RuntimeError("actively running")` without marking health as DOWN.

### InstrumentedLock (`main.py`)
- Replaces raw `threading.Lock()` for process-level `scanner_execution_lock`.
- **Guarantees:**
  1. Protects critical sections that mutate shared scanner state or persist scanner results.
  2. Excludes long non-mutating wait loops (e.g. Bhavcopy wait, cool-down sleeps).
- **De-nested Architecture:** Evening scanners (EOD, Reversal, Pullback) individually acquire and release `scanner_execution_lock` around their execution blocks, rather than holding a single outer lock across the batch. Post-scan `time.sleep(15)` cool-downs run outside the lock context.
- **Telemetry Tracking:** Counts `acquisitions_count`, `total_wait_seconds`, `max_wait_seconds`, `total_hold_seconds`, `max_hold_seconds`, and `contention_events_count`.
- **Threshold Warnings:** Logs structured warnings if wait > `LOCK_WAIT_WARNING_SECONDS` (5.0s) or hold > `LOCK_HOLD_WARNING_SECONDS` (60.0s). Exposes metrics via `/api/lock-stats`.

### GlobalFetchLock (`unified_fetcher.py`)
- Wraps the external boundaries of the UnifiedFetcher.
- Prevents concurrent bursts to Fyers/Yahoo APIs that would cause HTTP 429 (Too Many Requests).
- Scales out CPU (scanners evaluating data simultaneously) while serializing Network I/O.

---

## 5. Code Traceability Map

If rebuilding, follow this mapping to trace component logic:
- **Registry / Memory:** `app/data_registry.py`, `app/lifecycle.py`, `app/memory_profiler.py`
- **Data Acquisition:** `app/unified_fetcher.py`, `app/provider_selector.py`, `app/delivery_data.py`
- **Scanners:** `app/eod_scanner.py`, `app/reversal_scanner.py`, `app/multi_tf_scanner.py`, `app/pullback_pipeline.py`
- **Business Logic & Invariants:** `app/scoring_engine.py`, `app/sl_target_helper.py` (`TradeStructureValidator`), `app/swing_utils.py`
- **Configuration & Locks:** `app/config.py`, `app/main.py` (`InstrumentedLock`)
- **Scheduling & Orchestration:** `app/main.py` (`run_system_scheduler`), `app/scheduler.py`
