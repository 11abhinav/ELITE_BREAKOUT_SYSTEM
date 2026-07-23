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
| `bhavcopy_delivery` | DeliveryData | `nse` (ScraperAPI)| None | DURABLE | Daily (18:00 IST) |
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
    "fetch_timestamp": "2026-07-23T15:00:00+05:30"
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

The system heavily parallelizes CPU-bound scanner loops while strictly synchronizing I/O to avoid rate limits.

### ProcessLock (`lock_utils.py`)
- Used to ensure that only a single instance of a scanner is running globally across processes/threads.
- E.g., `_scan_lock = ProcessLock("eod_scanner")`.
- If a scanner is triggered while already running, the lock rejects the request cleanly.

### GlobalFetchLock (`unified_fetcher.py`)
- Wrapping the external boundaries of the UnifiedFetcher.
- Prevents concurrent bursts to the Fyers/Yahoo APIs that would result in HTTP 429 (Too Many Requests).
- Scales out CPU (many scanners evaluating data simultaneously) while serializing Network I/O.

---

## 5. Code Traceability Map

If rebuilding, follow this mapping to trace component logic:
- **Registry / Memory:** `app/data_registry.py`, `app/lifecycle.py`, `app/memory_profiler.py`
- **Data Acquisition:** `app/unified_fetcher.py`, `app/provider_selector.py`, `app/delivery_data.py`
- **Scanners:** `app/eod_scanner.py`, `app/reversal_scanner.py`, `app/multi_tf_scanner.py`, `app/pullback_pipeline.py`
- **Business Logic:** `app/scoring_engine.py`, `app/sl_target_helper.py`, `app/swing_utils.py`
- **Configuration:** `app/config.py`
- **Scheduling/Entry:** `app/main.py`, `app/scheduler.py`
