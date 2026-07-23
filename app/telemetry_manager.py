import gc
import logging
import os
import sys
import tracemalloc
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional

import psutil

IST = timezone(timedelta(hours=5, minutes=30))

# Configure logger if not already configured in main app
logger = logging.getLogger("Telemetry")
if not logger.handlers:
    ch = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s')
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    logger.setLevel(logging.INFO)


# ── Canonical dataset identifiers (shared with Phase 2 registry) ─────────────
# str+Enum mixin: Python 3.9-compatible equivalent of StrEnum (added in 3.11).
# Existing callers are unaffected — values remain plain strings in all contexts.
# Benefits over plain class: type safety, iteration, membership checks,
# IDE autocompletion, and structural impossibility of string drift.
class DatasetID(str, Enum):
    OHLCV_1Y       = "OHLCV_1Y"       # HistoricalDataManager.DailyStore
    OHLCV_INTRADAY = "OHLCV_INTRADAY" # HistoricalDataManager.IntradayStore
    INDICATORS     = "INDICATORS"      # IndicatorManager
    WATCHLIST      = "WATCHLIST"       # WatchlistBuilder
    DELIVERY       = "DELIVERY"        # HistoricalDataManager.DeliveryStore
    BHAVCOPY       = "BHAVCOPY"        # BhavcopyStore
    FINANCIALS     = "FINANCIALS"      # FundamentalCache
    NIFTY_CACHE    = "NIFTY_CACHE"     # MarketData
    LIVE_SNAPSHOTS = "LIVE_SNAPSHOTS"  # MarketData

    def __str__(self) -> str:          # Ensures f-string / str() returns raw value
        return self.value


# ── Phase 1 helpers ──────────────────────────────────────────────────────────

def _get_python_heap_mb() -> float:
    """Return current Python heap allocation via tracemalloc (MB). Returns 0 if not tracing."""
    if tracemalloc.is_tracing():
        current, _ = tracemalloc.get_traced_memory()
        return current / (1024 * 1024)
    return 0.0


def _count_live_dataframes() -> int:
    """
    Count live pandas DataFrames currently reachable by the GC.

    WARNING: This walks the entire Python object graph (~20-100ms depending on
    heap size). Only call from log_dataframe_snapshot(), which is called
    explicitly at phase boundaries — NOT from log_phase_start/end.
    """
    try:
        import pandas as pd
        return sum(1 for obj in gc.get_objects() if isinstance(obj, pd.DataFrame))
    except Exception:
        return -1


def _run_gc_and_measure() -> float:
    """Run gc.collect() and return elapsed time in seconds."""
    import time
    t0 = time.perf_counter()
    gc.collect()
    return time.perf_counter() - t0


# ─────────────────────────────────────────────────────────────────────────────


class TelemetryManager:
    """
    STRICTLY PASSIVE OBSERVABILITY SUBSYSTEM  (Phase 1 — Full Metric Set)

    This singleton must NEVER:
    - Clear caches
    - Trigger GC (it only measures GC when explicitly asked via log_gc_run)
    - Decide ownership
    - Release memory
    - Influence scheduling

    It only records, measures, and reports system state.

    Phase 1 metrics collected:
      - RSS (psutil)
      - Python heap (tracemalloc)
      - Live DataFrame count (gc.get_objects)
      - Dataset load count per phase
      - Cache hits / misses
      - Fetch source (DURABLE / SESSION / API)
      - Fetch latency per dataset
      - Phase duration
      - Scanner duration
      - GC duration
      - Largest datasets (estimated MB)
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_state()
        return cls._instance

    def _init_state(self):
        self.daily_metrics = {
            # RSS
            "peak_rss": 0,
            "min_rss": float('inf'),
            "avg_rss_sum": 0,
            "rss_samples": 0,
            # Dataset
            "largest_dataset": {"name": None, "size_mb": 0},
            "largest_temp_alloc": {"name": None, "size_mb": 0},
            "most_reused": {"name": None, "count": 0},
            "dataset_reuse_counts": {},
            # Fetch
            "longest_fetch": {"name": None, "time": 0},
            "cache_hits": 0,
            "cache_misses": 0,
            "duplicate_fetches": 0,
            # Fetch sources (Phase 1)
            "fetch_source_counts": {"DURABLE": 0, "SESSION": 0, "API": 0},
            # Scanners
            "fastest_scanner": {"name": None, "time": float('inf')},
            "slowest_scanner": {"name": None, "time": 0},
            "max_scheduler_drift": 0,
            # Memory / GC (Phase 1)
            "memory_recovered_mb": 0,
            "memory_not_recovered_mb": 0,
            "leak_candidates": set(),
            "peak_heap_mb": 0.0,
            "total_gc_time_sec": 0.0,
            "gc_runs": 0,
            # DataFrames (Phase 1)
            "peak_dataframe_count": 0,
            # Phases (Phase 1)
            "_phase_timers": {},          # {phase_name: start_perf_counter}
            "phase_durations_sec": {},    # {phase_name: elapsed_sec}
        }

    # ── RSS helpers ──────────────────────────────────────────────────────────

    def _record_rss(self, rss_mb: float):
        if rss_mb > self.daily_metrics["peak_rss"]:
            self.daily_metrics["peak_rss"] = rss_mb
        if rss_mb < self.daily_metrics["min_rss"]:
            self.daily_metrics["min_rss"] = rss_mb
        self.daily_metrics["avg_rss_sum"] += rss_mb
        self.daily_metrics["rss_samples"] += 1

    def get_current_rss_mb(self) -> float:
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)

    # ── Phase 1 — New observability methods ─────────────────────────────────

    def start_tracemalloc(self):
        """Start tracemalloc heap tracing. Call once at process startup."""
        if not tracemalloc.is_tracing():
            tracemalloc.start()
            logger.info("[TELEMETRY] tracemalloc heap tracing started")

    def log_phase_start(self, phase: str):
        """
        Record phase entry timestamp, RSS, and Python heap.

        NOTE: Does NOT call _count_live_dataframes() — that walks the full GC
        object graph (~20-100ms). Use log_dataframe_snapshot() explicitly at
        key boundaries where the DF count is meaningful and the cost is worth it.
        """
        import time
        self.daily_metrics["_phase_timers"][phase] = time.perf_counter()
        rss = self.get_current_rss_mb()
        heap = _get_python_heap_mb()
        self._record_rss(rss)
        logger.info(
            f"\n[PHASE START] {phase}\n"
            f"RSS           : {rss:.1f} MB\n"
            f"Python Heap   : {heap:.1f} MB\n"
        )

    def log_phase_end(self, phase: str):
        """
        Record phase exit, compute duration, RSS, and Python heap.

        NOTE: Does NOT call _count_live_dataframes() — see log_phase_start note.
        """
        import time
        start = self.daily_metrics["_phase_timers"].pop(phase, None)
        elapsed = (time.perf_counter() - start) if start is not None else 0.0
        self.daily_metrics["phase_durations_sec"][phase] = elapsed
        rss = self.get_current_rss_mb()
        heap = _get_python_heap_mb()
        self._record_rss(rss)
        if heap > self.daily_metrics["peak_heap_mb"]:
            self.daily_metrics["peak_heap_mb"] = heap
        logger.info(
            f"\n[PHASE END] {phase}\n"
            f"Duration      : {elapsed:.1f} sec\n"
            f"RSS           : {rss:.1f} MB\n"
            f"Python Heap   : {heap:.1f} MB\n"
        )

    def log_gc_run(self, trigger: str):
        """
        Measure and record a GC collection triggered externally.

        IMPORTANT: Telemetry does NOT decide when to GC.
        This method is called BY the lifecycle manager AFTER it has already
        decided to collect, purely so we can measure elapsed time.
        """
        elapsed = _run_gc_and_measure()
        self.daily_metrics["total_gc_time_sec"] += elapsed
        self.daily_metrics["gc_runs"] += 1
        rss_after = self.get_current_rss_mb()
        self._record_rss(rss_after)
        logger.info(
            f"\n[GC RUN] {trigger}\n"
            f"GC Duration   : {elapsed:.3f} sec\n"
            f"RSS After     : {rss_after:.1f} MB\n"
        )

    def log_dataframe_snapshot(self, context: str):
        """Record a point-in-time live DataFrame count and heap size."""
        df_count = _count_live_dataframes()
        heap = _get_python_heap_mb()
        if df_count > self.daily_metrics["peak_dataframe_count"]:
            self.daily_metrics["peak_dataframe_count"] = df_count
        if heap > self.daily_metrics["peak_heap_mb"]:
            self.daily_metrics["peak_heap_mb"] = heap
        logger.info(
            f"\n[DF SNAPSHOT] {context}\n"
            f"Live DFs      : {df_count}\n"
            f"Python Heap   : {heap:.1f} MB\n"
        )

    def log_fetch_source(self, dataset: str, source: str, latency_sec: float = 0.0, caller: str = ""):
        """
        Log where a dataset was served from: DURABLE, SESSION, or API.
        Called by FetchGuard on every fetch.

        Args:
            dataset:      Dataset identifier (e.g. "1Y_OHLCV")
            source:       One of "DURABLE", "SESSION", "API"
            latency_sec:  Time taken to complete the fetch
            caller:       Scanner/component requesting the data
        """
        source = source.upper()
        counts = self.daily_metrics["fetch_source_counts"]
        counts[source] = counts.get(source, 0) + 1

        if source == "API":
            self.daily_metrics["cache_misses"] += 1
            if latency_sec > self.daily_metrics["longest_fetch"]["time"]:
                self.daily_metrics["longest_fetch"] = {
                    "name": f"{dataset} via API",
                    "time": latency_sec,
                }
        else:
            self.daily_metrics["cache_hits"] += 1

        logger.info(
            f"\n[FETCH SOURCE]\n"
            f"Dataset       : {dataset}\n"
            f"Source        : {source}\n"
            f"Latency       : {latency_sec:.3f} sec\n"
            f"Caller        : {caller or 'unknown'}\n"
        )

    def log_purge_result(self, dataset: str, rss_before_mb: float, rss_after_mb: float):
        """
        Record the result of a memory purge.
        Emits PURGE_INEFFECTIVE if RSS did not drop meaningfully (< 5 MB).
        Called by Lifecycle Manager after gc.collect() + malloc_trim().
        """
        delta = rss_before_mb - rss_after_mb
        if delta > 0:
            self.daily_metrics["memory_recovered_mb"] += delta
        else:
            self.daily_metrics["memory_not_recovered_mb"] += abs(delta)

        effective = delta > 5.0
        tag = "PURGE_OK" if effective else "PURGE_INEFFECTIVE"
        logger.info(
            f"\n[{tag}] {dataset}\n"
            f"RSS Before    : {rss_before_mb:.1f} MB\n"
            f"RSS After     : {rss_after_mb:.1f} MB\n"
            f"Recovered     : {delta:.1f} MB\n"
        )
        if not effective:
            logger.warning(
                f"[PURGE_INEFFECTIVE] {dataset} — RSS did not drop after purge. "
                f"Another reference is likely still alive."
            )
            self.daily_metrics["leak_candidates"].add(dataset)

    # ── Existing methods (signatures preserved for backward compatibility) ────

    def log_fetch(self, dataset: str, interval: str, period: str, provider: str,
                  reason: str, cache_status: str, fetch_time: float, memory_mb: float,
                  consumers: list, fetch_count: int, reuse_count: int):
        if cache_status.upper() == "HIT":
            self.daily_metrics["cache_hits"] += 1
        else:
            self.daily_metrics["cache_misses"] += 1
            if fetch_time > self.daily_metrics["longest_fetch"]["time"]:
                self.daily_metrics["longest_fetch"] = {
                    "name": f"{dataset} via {provider}",
                    "time": fetch_time,
                }
        self._record_rss(memory_mb)
        consumers_str = ",".join(consumers) if consumers else "None"
        logger.info(
            f"\n[ROUTING]\n"
            f"Dataset          : {dataset}\n"
            f"Interval         : {interval}\n"
            f"Period           : {period}\n"
            f"Provider         : {provider}\n"
            f"Reason           : {reason}\n"
            f"Cache Status     : {cache_status}\n"
            f"Fetch Time       : {fetch_time:.2f} sec\n"
            f"Memory           : {memory_mb:.1f} MB\n"
            f"Consumers        : {consumers_str}\n"
            f"Fetch Count      : {fetch_count} Today\n"
            f"Reuse Count      : {reuse_count}\n"
        )

    def log_memory_snapshot(self, operation: str, memory_before_mb: float,
                             memory_after_mb: float, dataset_size_mb: float):
        delta = memory_after_mb - memory_before_mb
        logger.info(
            f"\n[MEMORY SNAPSHOT] {operation}\n"
            f"Memory Before Fetch : {memory_before_mb:.1f} MB\n"
            f"Memory After Fetch  : {memory_after_mb:.1f} MB\n"
            f"Dataset Size        : {dataset_size_mb:.1f} MB\n"
            f"RSS Delta           : {delta:.1f} MB\n"
        )
        self._record_rss(memory_after_mb)
        if dataset_size_mb > self.daily_metrics["largest_dataset"]["size_mb"]:
            self.daily_metrics["largest_dataset"] = {"name": operation, "size_mb": dataset_size_mb}
        if delta > self.daily_metrics["largest_temp_alloc"]["size_mb"]:
            self.daily_metrics["largest_temp_alloc"] = {"name": operation, "size_mb": delta}

    def log_cache_event(self, dataset_id: str, state: str, owner: str = None,
                        consumers: list = None, size_mb: float = None,
                        consumer: str = None, reason: str = None,
                        recovered_mb: float = None):
        log_str = f"\n[CACHE]\nDataset ID:\n{dataset_id}\nState:\n{state}\n"
        if owner:
            log_str += f"Owner:\n{owner}\n"
        if consumers:
            log_str += f"Consumers:\n{chr(10).join(consumers)}\n"
        if consumer:
            log_str += f"Consumer:\n{consumer}\n"
        if state == "REUSED":
            curr_reuses = self.daily_metrics["dataset_reuse_counts"].get(dataset_id, 0) + 1
            self.daily_metrics["dataset_reuse_counts"][dataset_id] = curr_reuses
            if curr_reuses > self.daily_metrics["most_reused"]["count"]:
                self.daily_metrics["most_reused"] = {"name": dataset_id, "count": curr_reuses}
        if size_mb is not None:
            log_str += f"Size:\n{size_mb:.1f} MB\n"
            if size_mb > self.daily_metrics["largest_dataset"]["size_mb"]:
                self.daily_metrics["largest_dataset"] = {"name": dataset_id, "size_mb": size_mb}
        if reason:
            log_str += f"Reason:\n{reason}\n"
        if recovered_mb is not None:
            log_str += f"Recovered:\n{recovered_mb:.1f} MB\n"
            self.daily_metrics["memory_recovered_mb"] += recovered_mb
        logger.info(log_str)

    def log_dataset_size(self, dataset: str, rows: int, columns: int, estimated_mb: float):
        logger.info(
            f"\n[DATASET SIZE]\n"
            f"Dataset          : {dataset}\n"
            f"Rows             : {rows}\n"
            f"Columns          : {columns}\n"
            f"Estimated Size   : {estimated_mb:.1f} MB\n"
        )

    def log_scanner_dependency(self, scanner: str, consumes: list, produces: list):
        logger.info(
            f"\n[SCANNER DEPENDENCY]\n"
            f"Scanner\n{scanner}\n"
            f"Consumes\n{chr(10).join(consumes) if consumes else 'None'}\n"
            f"Produces\n{chr(10).join(produces) if produces else 'None'}\n"
        )

    def log_scheduler_event(self, name: str, event_type: str, error: str = None):
        msg = f"[{event_type}] Scheduler: {name}"
        if error:
            msg += f" (Error: {error})"
        logger.debug(msg)

    def log_scheduler(self, name: str, expected_time: str, started_time: str,
                      delay_sec: float, runtime_sec: float, next_scheduled: str):
        logger.info(
            f"\n[SCHEDULER] {name}\n"
            f"Expected       : {expected_time}\n"
            f"Started        : {started_time}\n"
            f"Delay          : {delay_sec:.1f} sec\n"
            f"Runtime        : {runtime_sec:.1f} sec\n"
            f"Next Scheduled : {next_scheduled}\n"
        )
        if delay_sec > self.daily_metrics["max_scheduler_drift"]:
            self.daily_metrics["max_scheduler_drift"] = delay_sec
        if runtime_sec > 0:
            if runtime_sec < self.daily_metrics["fastest_scanner"]["time"]:
                self.daily_metrics["fastest_scanner"] = {"name": name, "time": runtime_sec}
            if runtime_sec > self.daily_metrics["slowest_scanner"]["time"]:
                self.daily_metrics["slowest_scanner"] = {"name": name, "time": runtime_sec}

    def log_session_timeline(self, time_str: str, event_desc: str):
        logger.info(f"\n[SESSION TIMELINE]\n{time_str}\n{event_desc}\n")

    # ── Daily Summary (Phase 1 — expanded) ──────────────────────────────────

    def generate_daily_summary(self, end_of_day_rss_mb: float = 0.0):
        """
        Generate and log the end-of-day telemetry summary.

        Args:
            end_of_day_rss_mb: Final RSS reading at session close, used to compute
                               Memory Recovery %. Pass 0 if not available (derivation skipped).
        """
        m = self.daily_metrics
        avg_rss = m["avg_rss_sum"] / m["rss_samples"] if m["rss_samples"] > 0 else 0
        min_rss = m["min_rss"] if m["min_rss"] != float('inf') else 0

        fastest = m["fastest_scanner"]
        slowest = m["slowest_scanner"]
        fastest_disp  = f"{fastest['name']} ({fastest['time']:.1f}s)" if fastest['name'] else "N/A"
        slowest_disp  = f"{slowest['name']} ({slowest['time']:.1f}s)" if slowest['name'] else "N/A"
        largest_ds    = f"{m['largest_dataset']['name']} ({m['largest_dataset']['size_mb']:.1f} MB)" if m['largest_dataset']['name'] else "N/A"
        most_reused   = f"{m['most_reused']['name']} ({m['most_reused']['count']} Uses)" if m['most_reused']['name'] else "N/A"
        longest_fetch = f"{m['longest_fetch']['name']} ({m['longest_fetch']['time']:.1f}s)" if m['longest_fetch']['name'] else "N/A"
        largest_temp  = f"{m['largest_temp_alloc']['name']} ({m['largest_temp_alloc']['size_mb']:.1f} MB)" if m['largest_temp_alloc']['name'] else "N/A"
        leak_cands    = ",".join(m['leak_candidates']) if m['leak_candidates'] else "None"

        # ── Derived metric 1: Cache Hit Rate ─────────────────────────────────
        # Cache Hit Rate = (Session + Durable) / Total Fetches
        src = m["fetch_source_counts"]
        total_fetches = src.get("DURABLE", 0) + src.get("SESSION", 0) + src.get("API", 0)
        cache_hit_rate = round(
            100.0 * (src.get("DURABLE", 0) + src.get("SESSION", 0)) / total_fetches, 1
        ) if total_fetches else 0.0

        # ── Derived metric 2: Memory Recovery % ──────────────────────────────
        # Memory Recovery % = (Peak RSS - End-of-Day RSS) / Peak RSS * 100
        # Becomes meaningful once Phase 10 purging is enabled.
        if m["peak_rss"] > 0 and end_of_day_rss_mb > 0:
            mem_recovery_pct = round(
                100.0 * (m["peak_rss"] - end_of_day_rss_mb) / m["peak_rss"], 1
            )
        else:
            mem_recovery_pct = None  # Not yet available (purging not enabled)

        phase_lines = "\n".join(
            f"  {ph:<24}: {dur:.1f} sec"
            for ph, dur in m["phase_durations_sec"].items()
        ) or "  (none recorded)"

        mem_recovery_str = (
            f"{mem_recovery_pct}%  (Peak {m['peak_rss']:.1f} MB → EoD {end_of_day_rss_mb:.1f} MB)"
            if mem_recovery_pct is not None else "N/A  (purging not yet enabled)"
        )

        summary = (
            f"\n{'='*42}\n"
            f"Daily Telemetry Summary\n"
            f"{'='*42}\n"
            f"── Memory ──────────────────────────────\n"
            f"Peak RSS             : {m['peak_rss']:.1f} MB\n"
            f"Average RSS          : {avg_rss:.1f} MB\n"
            f"Minimum RSS          : {min_rss:.1f} MB\n"
            f"Peak Python Heap     : {m['peak_heap_mb']:.1f} MB\n"
            f"Peak Live DataFrames : {m['peak_dataframe_count']}\n"
            f"Memory Recovery %    : {mem_recovery_str}\n"
            f"── GC ──────────────────────────────────\n"
            f"GC Runs              : {m['gc_runs']}\n"
            f"Total GC Time        : {m['total_gc_time_sec']:.2f} sec\n"
            f"── Datasets ────────────────────────────\n"
            f"Largest Dataset      : {largest_ds}\n"
            f"Largest Temp Alloc   : {largest_temp}\n"
            f"Most Reused Dataset  : {most_reused}\n"
            f"── Fetch Sources ───────────────────────\n"
            f"Fetches from Durable : {src.get('DURABLE', 0)}\n"
            f"Fetches from Session : {src.get('SESSION', 0)}\n"
            f"Fetches from API     : {src.get('API', 0)}\n"
            f"Cache Hit Rate       : {cache_hit_rate}%  (Session+Durable / Total)\n"
            f"Cache Hits           : {m['cache_hits']}\n"
            f"Cache Misses         : {m['cache_misses']}\n"
            f"Duplicate Fetches    : {m['duplicate_fetches']}\n"
            f"Longest Fetch        : {longest_fetch}\n"
            f"── Scanners ────────────────────────────\n"
            f"Slowest Scanner      : {slowest_disp}\n"
            f"Fastest Scanner      : {fastest_disp}\n"
            f"Scheduler Drift      : Max {m['max_scheduler_drift']:.1f} sec\n"
            f"── Phase Durations ─────────────────────\n"
            f"{phase_lines}\n"
            f"── Purge ───────────────────────────────\n"
            f"Memory Recovered     : {m['memory_recovered_mb']:.1f} MB\n"
            f"Memory Not Recovered : {m['memory_not_recovered_mb']:.1f} MB\n"
            f"Leak Candidates      : {leak_cands}\n"
            f"{'='*42}\n"
        )
        logger.info(summary)


# Singleton Instance
telemetry = TelemetryManager()
