import gc
import os
import sys
import time
import logging
import psutil
import tracemalloc
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

class MemoryProfiler:
    """
    A context manager to profile memory and execution time for a block of code.
    Logs Current RSS, Peak RSS, Transient Alloc, Tracemalloc Peak, and GC statistics.
    """
    def __init__(self, stage_name: str, force_gc_cleanup: bool = False):
        self.stage_name = stage_name
        self.force_gc_cleanup = force_gc_cleanup
        self.process = psutil.Process(os.getpid())
        
        self.start_time = 0
        self.start_rss = 0
        self.start_peak = 0
        self.df_start = None
        
    def __enter__(self):
        self.start_time = time.monotonic()
        mem = self.process.memory_info()
        self.start_rss = mem.rss
        
        if hasattr(mem, 'peak_wset'):
            self.start_peak = mem.peak_wset
        else:
            self.start_peak = self.start_rss
            
        current_mb = self.start_rss / (1024 * 1024)
        logger.info(f"[MEMORY] 🟢 STARTING: {self.stage_name:<15} | Current RSS: {current_mb:>6.1f} MB")
        
        if ENABLE_PROFILING:
            self.df_start = get_dataframe_inventory()
            if not tracemalloc.is_tracing():
                tracemalloc.start()
            tracemalloc.clear_traces()
            
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.force_gc_cleanup:
            collected = gc.collect()
            logger.info(f"[MEMORY GC] Stage: {self.stage_name} | Reclaimed: {collected} objects")

        mem = self.process.memory_info()
        end_rss = mem.rss
        
        if hasattr(mem, 'peak_wset'):
            peak_rss = mem.peak_wset
        else:
            peak_rss = max(self.start_peak, end_rss)
            
        elapsed = time.monotonic() - self.start_time
        delta_rss = end_rss - self.start_rss
        transient_mb = max(0, (peak_rss - end_rss) / (1024 * 1024))
        
        current_mb = end_rss / (1024 * 1024)
        peak_mb = peak_rss / (1024 * 1024)
        delta_mb = delta_rss / (1024 * 1024)
        start_mb = self.start_rss / (1024 * 1024)
        
        delta_str = f"+{delta_mb:.1f}" if delta_mb >= 0 else f"{delta_mb:.1f}"
        
        logger.info(f"=== [PROFILE] {self.stage_name} ===")
        logger.info(f"  Time            : {elapsed:.2f}s")
        logger.info(f"  RSS Before      : {start_mb:.1f} MB")
        logger.info(f"  RSS After       : {current_mb:.1f} MB")
        logger.info(f"  RSS Delta       : {delta_str} MB")
        logger.info(f"  RSS Peak        : {peak_mb:.1f} MB")
        logger.info(f"  Transient Alloc : {transient_mb:.1f} MB (Peak - After)")
        
        if ENABLE_PROFILING and self.df_start:
            df_end = get_dataframe_inventory()
            peak_alloc_mb = tracemalloc.get_traced_memory()[1] / (1024 * 1024)
            logger.info(f"  Tracemalloc Peak: {peak_alloc_mb:.1f} MB")
            logger.info(f"  Live DF Count   : {self.df_start['count']} -> {df_end['count']} (Delta: {df_end['count'] - self.df_start['count']:+d})")
            logger.info(f"  Total DF Memory : {self.df_start['memory_mb']:.1f} MB -> {df_end['memory_mb']:.1f} MB")
            if df_end['largest_mb'] > 0:
                logger.info(f"  Largest DF      : {df_end['largest_mb']:.1f} MB (id={df_end['largest_id']}, {df_end['largest_rows']} rows, {df_end['largest_cols']} cols)")
                
            df_delta_mb = df_end['memory_mb'] - self.df_start['memory_mb']
            if delta_mb > 5.0 and df_delta_mb < 1.0:
                _trigger_deep_diagnostic(delta_mb, df_delta_mb, self.stage_name)
                
        logger.info("=================================")
        
        # Budget Alerts
        current_gb = current_mb / 1024
        if current_gb >= 3.0:
            logger.error(f"🚨 CRITICAL MEMORY USAGE: {current_gb:.2f} GB! Threshold (3 GB) exceeded.")
        elif current_gb >= 2.0:
            logger.warning(f"⚠️ WARNING MEMORY USAGE: {current_gb:.2f} GB! Threshold (2 GB) exceeded.")


def start_tracemalloc():
    """Starts tracemalloc for object tracking."""
    if not tracemalloc.is_tracing():
        tracemalloc.start(10)

def log_hourly_memory():
    """Logs the current raw RSS memory footprint."""
    process = psutil.Process(os.getpid())
    current_mb = process.memory_info().rss / (1024 * 1024)
    logger.info(f"[MEMORY] 🕒 HOURLY TICK | Current RSS: {current_mb:.1f} MB")

def log_object_inventory():
    """
    Logs top tracemalloc lines and specifically sizes all pandas/numpy objects in memory.
    """
    if not tracemalloc.is_tracing():
        logger.warning("[INVENTORY] tracemalloc is not running.")
        return
        
    snapshot = tracemalloc.take_snapshot()
    top_stats = snapshot.statistics('lineno')

    logger.info("=== [INVENTORY] Top 5 Python Allocations (tracemalloc) ===")
    for stat in top_stats[:5]:
        logger.info(f"  {stat}")

    logger.info("=== [INVENTORY] Environment & GC State ===")
    arena_max = os.environ.get('MALLOC_ARENA_MAX', 'Not Set')
    logger.info(f"  MALLOC_ARENA_MAX: {arena_max}")
    logger.info(f"  GC Thresholds: {gc.get_threshold()}")
    logger.info(f"  GC Object Counts: {gc.get_count()}")

    # Explicitly calculate Pandas / NumPy sizes which tracemalloc misses
    logger.info("=== [INVENTORY] Deep Native Sizing ===")
    
    total_pd_bytes = 0
    total_np_bytes = 0
    pd_count = 0
    np_count = 0
    
    # gc.get_objects() is extremely slow and pauses the world. Only run occasionally.
    objects = gc.get_objects()
    for obj in objects:
        if isinstance(obj, pd.DataFrame):
            try:
                # deep=True ensures we count strings/objects properly
                total_pd_bytes += obj.memory_usage(deep=True).sum()
                pd_count += 1
            except Exception:
                pass
        elif isinstance(obj, np.ndarray):
            try:
                total_np_bytes += obj.nbytes
                np_count += 1
            except Exception:
                pass
                
    pd_mb = total_pd_bytes / (1024 * 1024)
    np_mb = total_np_bytes / (1024 * 1024)
    
    logger.info(f"[INVENTORY] Pandas: {pd_mb:.1f} MB ({pd_count} objects)")
    logger.info(f"[INVENTORY] NumPy: {np_mb:.1f} MB ({np_count} objects)")
    logger.info("=======================================")

def _inventory_worker():
    while True:
        time.sleep(3600)  # Log every 1 hour
        try:
            log_hourly_memory()
            # We skip log_object_inventory() hourly because gc.get_objects() causes massive latency spikes.
            # It can be called manually during debugging.
        except Exception as e:
            logger.warning(f"Failed to log hourly memory: {e}")

# Start tracemalloc immediately on module load
start_tracemalloc()

import threading
threading.Thread(target=_inventory_worker, name="MemoryInventory", daemon=True).start()

def chunk_iterable(iterable, batch_size):
    """Safely chunks a list or pandas DataFrame."""
    total_len = len(iterable)
    for i in range(0, total_len, batch_size):
        if isinstance(iterable, pd.DataFrame):
            yield iterable.iloc[i:i + batch_size]
        else:
            yield iterable[i:i + batch_size]

class BatchMemoryTracker:
    """
    Context manager to track memory through a typical Fetch -> Process -> Cleanup batch lifecycle.
    """
    def __init__(self, stage_name: str, batch_num: int, total_batches: int, item_count: int, collect_gc: bool = False):
        self.stage_name = stage_name
        self.batch_num = batch_num
        self.total_batches = total_batches
        self.item_count = item_count
        self.collect_gc = collect_gc
        self.process = psutil.Process(os.getpid())
        
        self.start_time = 0
        self.rss_before = 0
        self.rss_after_fetch = 0
        self.row_count = 0

    def __enter__(self):
        self.start_time = time.monotonic()
        self.rss_before = self.process.memory_info().rss / (1024 * 1024)
        return self

    def mark_fetch_complete(self, row_count: int = 0):
        self.row_count = row_count
        self.rss_after_fetch = self.process.memory_info().rss / (1024 * 1024)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.collect_gc:
            gc.collect()
            
        rss_after_cleanup = self.process.memory_info().rss / (1024 * 1024)
        elapsed = time.monotonic() - self.start_time
        
        # If fetch was never explicitly marked, just use cleanup memory for both
        if self.rss_after_fetch == 0:
            self.rss_after_fetch = rss_after_cleanup
            
        logger.info(
            f"[{self.stage_name} Batch {self.batch_num}/{self.total_batches}] "
            f"Symbols: {self.item_count} | Rows: {self.row_count} | "
            f"RSS Before: {self.rss_before:.1f} MB | "
            f"RSS After Fetch: {self.rss_after_fetch:.1f} MB | "
            f"RSS After Cleanup: {rss_after_cleanup:.1f} MB | "
            f"Elapsed: {elapsed:.1f}s"
        )

# =======================================================
# NEW V3 PRODUCTION PROFILING FEATURES
# =======================================================

ENABLE_PROFILING = os.getenv("ENABLE_PROFILING", "True").lower() in ("true", "1", "yes")

def get_dataframe_inventory():
    """Counts live DataFrames, rows, cols, and memory."""
    total_bytes = 0
    total_rows = 0
    total_cols = 0
    count = 0
    largest_df_mem = 0
    largest_df_rows = 0
    largest_df_cols = 0
    largest_df_id = None
    
    if not ENABLE_PROFILING:
        return {"count": 0, "rows": 0, "cols": 0, "memory_mb": 0.0, "largest_mb": 0.0, "largest_rows": 0, "largest_cols": 0, "largest_id": None}

    for obj in gc.get_objects():
        if isinstance(obj, pd.DataFrame):
            try:
                mem = obj.memory_usage(deep=True).sum()
                r = len(obj)
                c = len(obj.columns)
                total_bytes += mem
                total_rows += r
                total_cols += c
                count += 1
                if mem > largest_df_mem:
                    largest_df_mem = mem
                    largest_df_rows = r
                    largest_df_cols = c
                    largest_df_id = id(obj)
            except Exception:
                pass
                
    return {
        "count": count,
        "rows": total_rows,
        "cols": total_cols,
        "memory_mb": total_bytes / (1024 * 1024),
        "largest_mb": largest_df_mem / (1024 * 1024),
        "largest_rows": largest_df_rows,
        "largest_cols": largest_df_cols,
        "largest_id": largest_df_id
    }

def _trigger_deep_diagnostic(rss_delta_mb: float, df_delta_mb: float, stage_name: str):
    logger.warning(f"🚨 [DEEP MEMORY DIAGNOSTIC] Triggered for '{stage_name}'")
    logger.warning(f"🚨 RSS grew by {rss_delta_mb:.1f} MB but DF memory changed by {df_delta_mb:+.1f} MB.")
    
    if tracemalloc.is_tracing():
        snapshot = tracemalloc.take_snapshot()
        top_stats = snapshot.statistics('lineno')
        logger.warning("=== Top 5 Python Allocations (tracemalloc) ===")
        for stat in top_stats[:5]:
            logger.warning(f"  {stat}")
            
    try:
        from collections import Counter
        logger.warning("=== GC Object Type Counts ===")
        objs = gc.get_objects()
        type_counts = Counter(type(o).__name__ for o in objs)
        for t_name, count in type_counts.most_common(5):
            logger.warning(f"  {t_name}: {count} objects")
    except Exception as e:
        logger.warning(f"  Failed to get GC object counts: {e}")
        
    try:
        logger.warning("=== Process Memory Maps (Native Shared Libraries) ===")
        process = psutil.Process(os.getpid())
        maps = process.memory_maps()
        lib_rss = {}
        for m in maps:
            path = m.path
            base = os.path.basename(path) if path else "[anonymous]"
            lib_rss[base] = lib_rss.get(base, 0) + m.rss
            
        sorted_libs = sorted(lib_rss.items(), key=lambda x: x[1], reverse=True)
        for base, rss_bytes in sorted_libs[:10]:
            rss_mb = rss_bytes / (1024 * 1024)
            if rss_mb > 1.0:
                logger.warning(f"  {base}: {rss_mb:.1f} MB")
    except Exception as e:
        logger.warning(f"  Failed to read memory_maps (possibly restricted in container): {e}")
        
    logger.warning("==================================================")

def profile_function(stage_name: str, budget_mb: float = None):
    """Decorator for function-level profiling (tracemalloc, RSS, DF counts)."""
    from functools import wraps
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not ENABLE_PROFILING:
                return func(*args, **kwargs)
                
            process = psutil.Process(os.getpid())
            
            # Start peak tracker if available, otherwise just use RSS
            if hasattr(process.memory_info(), 'peak_wset'):
                start_peak = process.memory_info().peak_wset
            else:
                start_peak = process.memory_info().rss
                
            start_rss = process.memory_info().rss / (1024 * 1024)
            df_start = get_dataframe_inventory()
            
            if not tracemalloc.is_tracing():
                tracemalloc.start()
            
            tracemalloc.clear_traces()
            start_time = time.monotonic()
            
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                elapsed = time.monotonic() - start_time
                mem = process.memory_info()
                end_rss = mem.rss / (1024 * 1024)
                
                if hasattr(mem, 'peak_wset'):
                    sys_peak = mem.peak_wset
                else:
                    sys_peak = max(start_peak, mem.rss)
                
                peak_rss = sys_peak / (1024 * 1024)
                transient_mb = max(0, peak_rss - end_rss)
                
                df_end = get_dataframe_inventory()
                
                peak_alloc_bytes = tracemalloc.get_traced_memory()[1]
                peak_alloc_mb = peak_alloc_bytes / (1024 * 1024)
                
                logger.info(f"=== [PROFILE] {stage_name} ===")
                logger.info(f"  Time            : {elapsed:.2f}s")
                logger.info(f"  RSS Before      : {start_rss:.1f} MB")
                logger.info(f"  RSS After       : {end_rss:.1f} MB")
                logger.info(f"  RSS Delta       : {end_rss - start_rss:+.1f} MB")
                logger.info(f"  RSS Peak        : {peak_rss:.1f} MB")
                logger.info(f"  Transient Alloc : {transient_mb:.1f} MB (Peak - After)")
                
                if budget_mb and end_rss > budget_mb:
                    logger.warning(f"  ⚠️ BUDGET EXCEEDED: {end_rss:.1f} MB > {budget_mb:.1f} MB")
                
                logger.info(f"  Tracemalloc Peak: {peak_alloc_mb:.1f} MB")
                
                logger.info(f"  Live DF Count   : {df_start['count']} -> {df_end['count']} (Delta: {df_end['count'] - df_start['count']:+d})")
                logger.info(f"  Total DF Memory : {df_start['memory_mb']:.1f} MB -> {df_end['memory_mb']:.1f} MB")
                
                if df_end['largest_mb'] > 0:
                    logger.info(f"  Largest DF      : {df_end['largest_mb']:.1f} MB (id={df_end['largest_id']}, {df_end['largest_rows']} rows, {df_end['largest_cols']} cols)")
                    
                rss_delta_mb = end_rss - start_rss
                df_delta_mb = df_end['memory_mb'] - df_start['memory_mb']
                if rss_delta_mb > 5.0 and df_delta_mb < 1.0:
                    _trigger_deep_diagnostic(rss_delta_mb, df_delta_mb, stage_name)
                    
                logger.info("=================================")
        return wrapper
    return decorator

class CacheProfiler:
    """Singleton tracker for cache efficiency."""
    hits = 0
    misses = 0
    evictions = 0
    current_items = 0
    cache_memory_mb = 0.0
    
    @classmethod
    def record_hit(cls):
        cls.hits += 1
        
    @classmethod
    def record_miss(cls):
        cls.misses += 1
        
    @classmethod
    def record_eviction(cls):
        cls.evictions += 1
        
    @classmethod
    def update_inventory(cls, items: int, memory_mb: float):
        cls.current_items = items
        cls.cache_memory_mb = memory_mb
        
    @classmethod
    def log_stats(cls, cache_name: str, largest_symbol: str = "N/A", oldest_entry: str = "N/A", newest_entry: str = "N/A"):
        total = cls.hits + cls.misses
        hit_pct = (cls.hits / total * 100) if total > 0 else 0
        logger.info(f"[{cache_name} CACHE INVENTORY]")
        logger.info(f"  Items: {cls.current_items} | Memory: {cls.cache_memory_mb:.1f} MB")
        logger.info(f"  Largest Symbol: {largest_symbol}")
        logger.info(f"  Oldest: {oldest_entry} | Newest: {newest_entry}")
        logger.info(f"  Hits: {cls.hits} | Misses: {cls.misses} | Hit%: {hit_pct:.1f}% | Evictions: {cls.evictions}")
        logger.info("----------------------------------")
