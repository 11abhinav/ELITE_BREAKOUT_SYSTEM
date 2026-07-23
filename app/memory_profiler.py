import gc
import os
import sys
import time
from datetime import datetime
import logging
import psutil
import tracemalloc
import pandas as pd

import numpy as np
from config import MEMORY_PROFILER_CONFIG

# [VERSION: MEMORY_RECALIBRATION_v1.0] Recalibrated threshold breach triggers to start at 500 MB (post-boot baseline ~400 MB)
TARGET_THRESHOLDS = [500, 600, 700, 800, 900]

def configure_glibc_mmap_tuning():
    """
    Configures glibc allocator via mallopt(M_MMAP_THRESHOLD, 64KB) and
    mallopt(M_TRIM_THRESHOLD, 64KB) on Linux.
    Force-routes pandas/numpy/arrow memory allocations through mmap so that
    munmap() instantly surrenders physical RAM pages back to OS RSS when
    DataFrames are freed, preventing glibc arena heap fragmentation.
    """
    if sys.platform.startswith("linux"):
        try:
            import ctypes
            libc = ctypes.CDLL("libc.so.6")
            # M_MMAP_THRESHOLD = -3 (64 KB)
            # M_TRIM_THRESHOLD = -1 (64 KB)
            # M_TOP_PAD = -2 (0)
            libc.mallopt(-3, 64 * 1024)
            libc.mallopt(-1, 64 * 1024)
            libc.mallopt(-2, 0)
            logging.getLogger(__name__).info("⚡ [GLIBC ALLOCATOR TUNER] M_MMAP_THRESHOLD=64KB, M_TRIM_THRESHOLD=64KB enabled for instant RSS release.")
        except Exception as e:
            logging.getLogger(__name__).debug(f"Glibc mallopt tuning skipped: {e}")

# Run glibc tuning on module load
configure_glibc_mmap_tuning()


class ProfilerState:
    session_start_rss = None
    loop_count = 0
    last_deep_diagnostic_time = 0
    consecutive_anomalies = 0
    last_snapshot = None
    last_rss = None
    rolling_gains = []
    crossed_rss_thresholds = set()
    
    @classmethod
    def init_session(cls):
        if cls.session_start_rss is None:
            current_rss = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
            cls.session_start_rss = current_rss
            cls.last_rss = current_rss
            
    @classmethod
    def increment_loop(cls):
        cls.loop_count += 1
        
    @classmethod
    def get_session_stats(cls):
        cls.init_session()
        current_rss = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
        
        loop_gain = current_rss - cls.last_rss
        cls.last_rss = current_rss
        
        cls.rolling_gains.append(loop_gain)
        if len(cls.rolling_gains) > 10:
            cls.rolling_gains.pop(0)
            
        gain = current_rss - cls.session_start_rss
        rolling_gain_per_loop = sum(cls.rolling_gains) / len(cls.rolling_gains) if cls.rolling_gains else 0
        return gain, rolling_gain_per_loop


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
            
        from telemetry_manager import telemetry
        rss_mb = self.start_rss / (1024 * 1024)
        vms_mb = self.process.memory_info().vms / (1024 * 1024)
        
        logger.info("========== MEMORY SNAPSHOT ==========")
        logger.info(f"Time: {time.strftime('%H:%M:%S')}")
        logger.info(f"Scanner: {self.stage_name}")
        logger.info(f"RSS Memory: {rss_mb:.1f} MB")
        logger.info(f"VMS Memory: {vms_mb:.1f} MB")
        logger.info(f"Historical Cache: {len(telemetry._timers)} Symbols")
        logger.info(f"Shared Cache Objects: {telemetry.cache_stats.entries}")
        logger.info("=====================================")
            
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.force_gc_cleanup:
            collected = gc.collect()
            logger.info(f"[MEMORY GC] Stage: {self.stage_name} | Reclaimed: {collected} objects")
            
            try:
                import sys
                if sys.platform == "linux":
                    import ctypes
                    trim_rss_before = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
                    ctypes.CDLL("libc.so.6").malloc_trim(0)
                    trim_rss_after = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
                    if trim_rss_before - trim_rss_after > 5.0:
                        logger.warning(f"🧹 [MALLOC_TRIM] Reclaimed {(trim_rss_before - trim_rss_after):.1f} MB of native arena fragmentation in {self.stage_name}!")
            except Exception as e:
                pass

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
        
        from telemetry_manager import telemetry
        timer = telemetry.get_timer(self.stage_name.split()[0])
        timer.stages[self.stage_name] = elapsed
        
        logger.info("========== SCANNER COMPLETE ==========")
        logger.info(f"Scanner: {self.stage_name}")
        logger.info(f"Execution Time: {elapsed:.2f}s")
        logger.info(f"Memory Before: {start_mb:.1f} MB")
        logger.info(f"Memory After: {current_mb:.1f} MB")
        logger.info(f"Memory Delta: {delta_str} MB")
        logger.info(f"Peak Memory: {peak_mb:.1f} MB")
        logger.info("======================================")
        logger.debug(f"  Transient Alloc : {transient_mb:.1f} MB (Peak - After)")
        
        if ENABLE_PROFILING and self.df_start:
            df_end = get_dataframe_inventory()
            peak_alloc_mb = tracemalloc.get_traced_memory()[1] / (1024 * 1024)
            logger.debug(f"  Tracemalloc Peak: {peak_alloc_mb:.1f} MB")
            logger.debug(f"  Live DF Count   : {self.df_start['count']} -> {df_end['count']} (Delta: {df_end['count'] - self.df_start['count']:+d})")
            logger.debug(f"  Total DF Memory : {self.df_start['memory_mb']:.1f} MB -> {df_end['memory_mb']:.1f} MB")
            if df_end['largest_mb'] > 0:
                logger.debug(f"  Largest DF      : {df_end['largest_mb']:.1f} MB (id={df_end['largest_id']}, {df_end['largest_rows']} rows, {df_end['largest_cols']} cols)")
                
            df_delta_mb = df_end['memory_mb'] - self.df_start['memory_mb']
            _trigger_deep_diagnostic(delta_mb, df_delta_mb, self.stage_name, peak_alloc_mb)
            
            # Session Stats
            ProfilerState.increment_loop()
            session_gain, gain_per_loop = ProfilerState.get_session_stats()
            logger.debug(f"  Session Gain    : {session_gain:+.1f} MB")
            logger.debug(f"  Rolling Gain/Lp : {gain_per_loop:+.2f} MB/loop (last 10)")
                
        logger.debug("=================================")
        
        # Budget Alerts disabled during profiling mode


def start_tracemalloc():
    """Starts tracemalloc for object tracking."""
    if not tracemalloc.is_tracing():
        tracemalloc.start(10)

def log_hourly_memory():
    """Logs the current raw RSS memory footprint."""
    from telemetry_manager import telemetry
    telemetry.log_memory_snapshot(context="HOURLY TICK")

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
            
            try:
                import sys
                if sys.platform == "linux":
                    import ctypes
                    trim_rss_before = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
                    ctypes.CDLL("libc.so.6").malloc_trim(0)
                    trim_rss_after = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
                    if trim_rss_before - trim_rss_after > 5.0:
                        logger.warning(f"🧹 [MALLOC_TRIM] Reclaimed {(trim_rss_before - trim_rss_after):.1f} MB of native arena fragmentation in Batch {self.batch_num}!")
            except Exception as e:
                pass

        rss_after_cleanup = self.process.memory_info().rss / (1024 * 1024)
        elapsed = time.monotonic() - self.start_time
        
        # If fetch was never explicitly marked, just use cleanup memory for both
        if self.rss_after_fetch == 0:
            self.rss_after_fetch = rss_after_cleanup
            
        _check_rss_thresholds(self.rss_before, rss_after_cleanup)
            
        logger.info(
            f"[{self.stage_name} Batch {self.batch_num}/{self.total_batches}] "
            f"Symbols: {self.item_count} | Rows: {self.row_count} | "
            f"RSS Before: {self.rss_before:.1f} MB | "
            f"RSS After Fetch: {self.rss_after_fetch:.1f} MB | "
            f"RSS After Cleanup: {rss_after_cleanup:.1f} MB | "
            f"Time: {elapsed:.2f}s"
        )

# =======================================================
# NEW V3 PRODUCTION PROFILING FEATURES
# =======================================================

ENABLE_PROFILING = os.getenv("ENABLE_PROFILING", "True").lower() in ("true", "1", "yes")

def run_purge_with_telemetry(stage_name: str) -> float:
    """
    Executes a 4-step defensive memory purge and logs stage-by-stage telemetry.
    Step 1: Measure RSS Before
    Step 2: Clear Application Caches
    Step 3: Force gc.collect()
    Step 4: Execute malloc_trim(0)
    """
    # Profiling mode active: Do not forcefully purge memory.
    return 0.0
    
    proc = psutil.Process(os.getpid())
    rss_start = proc.memory_info().rss / (1024 * 1024)
    
    # 1. Clear application caches
    cache_released = 0.0
    pc_before, pc_after = {"keys": 0, "entries": 0, "memory_mb": 0.0}, {"keys": 0, "entries": 0, "memory_mb": 0.0}
    try:
        from price_cache import clear_price_cache
        res = clear_price_cache()
        if res:
            pc_before, pc_after = res
    except Exception as e:
        logger.debug(f"Price cache clear skipped: {e}")
        
    for mod_name, attr_name in [
        ("delivery_data", "_delivery_cache"),
        ("watchlist_cache", "_watchlist_cache"),
        ("surveillance", "_surveillance_cache"),
        ("surveillance", "_blacklist_cache"),
        ("block_deal_detector", "_CACHE"),
        ("dashboard_server", "_wealth_cache"),
        ("dashboard_server", "_indices_cache"),
        ("dashboard_server", "_news_cache")
    ]:
        try:
            mod = sys.modules.get(mod_name)
            if mod and hasattr(mod, attr_name):
                obj = getattr(mod, attr_name)
                if isinstance(obj, dict):
                    obj.clear()
                elif isinstance(obj, set):
                    obj.clear()
                elif isinstance(obj, list):
                    obj.clear()
        except Exception:
            pass
            
    rss_after_cache = proc.memory_info().rss / (1024 * 1024)
    cache_released = max(0.0, rss_start - rss_after_cache)
    
    # 2. Force GC collect
    collected = gc.collect()
    rss_after_gc = proc.memory_info().rss / (1024 * 1024)
    gc_released = max(0.0, rss_after_cache - rss_after_gc)

    # 2b. PyArrow C++ Memory Pool Release
    pyarrow_released = 0.0
    try:
        import pyarrow as pa
        pool = pa.default_memory_pool()
        bytes_before = pool.bytes_allocated()
        pool.release_unused()
        bytes_after = pool.bytes_allocated()
        pyarrow_released = max(0.0, (bytes_before - bytes_after) / (1024 * 1024))
    except Exception:
        pass

    # 3. Native arena trim
    trim_released = 0.0
    try:
        if sys.platform.startswith("linux"):
            import ctypes
            ctypes.CDLL("libc.so.6").malloc_trim(0)
            rss_after_trim = proc.memory_info().rss / (1024 * 1024)
            trim_released = max(0.0, rss_after_gc - rss_after_trim)
        else:
            rss_after_trim = rss_after_gc
    except Exception:
        rss_after_trim = rss_after_gc

    total_released = max(0.0, rss_start - rss_after_trim)

    arrow_allocated_mb = 0.0
    try:
        import pyarrow as pa
        arrow_allocated_mb = pa.default_memory_pool().bytes_allocated() / (1024 * 1024)
    except Exception:
        pass

    df_inv = get_dataframe_inventory()
    py_tracemalloc_mb = (tracemalloc.get_traced_memory()[0] / (1024 * 1024)) if tracemalloc.is_tracing() else 0.0

    logger.info(
        f"📊 [NATIVE MEMORY METRICS] Stage: {stage_name:<25} | "
        f"RSS: {rss_after_trim:.1f} MB | "
        f"Arrow Pool: {arrow_allocated_mb:.1f} MB | "
        f"Arrow Released: {pyarrow_released:.1f} MB | "
        f"Glibc Trim: {trim_released:.1f} MB | "
        f"Cache: {pc_after['memory_mb']:.1f} MB ({pc_after['keys']} keys) | "
        f"PyHeap: {py_tracemalloc_mb:.1f} MB | "
        f"DFs: {df_inv['memory_mb']:.1f} MB ({df_inv['count']} DFs)"
    )

    logger.info(f"  PRICE_CACHE After       : keys={pc_after['keys']} | entries={pc_after['entries']} | memory={pc_after['memory_mb']:.1f} MB")
    logger.info(f"  RSS Before Purge        : {rss_start:>7.1f} MB")
    logger.info(f"  After Cache Clear       : {rss_after_cache:>7.1f} MB (Released: {cache_released:>5.1f} MB)")
    logger.info(f"  After gc.collect()      : {rss_after_gc:>7.1f} MB (Released: {gc_released:>5.1f} MB, Objects: {collected})")
    logger.info(f"  After PyArrow Release   : Released {pyarrow_released:>5.1f} MB")
    logger.info(f"  After malloc_trim()     : {rss_after_trim:>7.1f} MB (Released: {trim_released:>5.1f} MB)")
    logger.info(f"  TOTAL MEMORY RELEASED   : {total_released:>7.1f} MB")
    logger.info("=" * 60)




    # If memory remains above 500 MB after purge, trigger global object inspection
    if rss_after_trim > 500.0:
        inspect_largest_global_objects(top_n=10)

    return rss_after_trim

def inspect_largest_global_objects(top_n: int = 10):
    """Inspects all imported module global variables to identify largest retained objects."""
    logger.info("=== [INVENTORY] Top Global Object Sizing ===")
    records = []
    
    # Modules in the application namespace
    target_mods = [m for name, m in sys.modules.items() if m and name and (name.startswith("app") or name in [
        "price_cache", "delivery_data", "surveillance", "watchlist_cache", "block_deal_detector",
        "dashboard_server", "eod_scanner", "reversal_scanner", "pullback_pipeline", "scoring_engine",
        "sl_target_helper", "breakout_engine", "technical_indicators", "constituent_service"
    ])]
    
    for mod in target_mods:
        mod_name = getattr(mod, "__name__", "unknown")
        for var_name, obj in getattr(mod, "__dict__", {}).items():
            if var_name.startswith("__") or callable(obj) or isinstance(obj, type) or type(obj).__name__ == "module":
                continue
                
            obj_type = type(obj).__name__
            mem_mb = 0.0
            length = 0
            
            if isinstance(obj, pd.DataFrame):
                try:
                    mem_mb = obj.memory_usage(deep=True).sum() / (1024 * 1024)
                    length = len(obj)
                except Exception:
                    pass
            elif isinstance(obj, (dict, list, set, tuple)):
                try:
                    length = len(obj)
                    mem_mb = sys.getsizeof(obj) / (1024 * 1024)
                except Exception:
                    pass
                    
            if mem_mb > 0.1 or length > 500:
                records.append({
                    "name": f"{mod_name}.{var_name}",
                    "type": obj_type,
                    "length": length,
                    "mem_mb": mem_mb
                })

    records.sort(key=lambda x: (x["mem_mb"], x["length"]), reverse=True)
    
    if not records:
        logger.info("  No large global objects (>0.1 MB or >500 items) detected in module namespaces.")
    else:
        for r in records[:top_n]:
            logger.info(f"  {r['name']:<45} | Type: {r['type']:<12} | Items: {r['length']:>6} | Size: {r['mem_mb']:>6.2f} MB")
    logger.info("==========================================")

class StageTimelineTracker:
    """
    Context manager to track execution time, RSS memory delta, and top object growth
    for major pipeline stages (e.g. Universe Build, Pre-Scan, Batch Fetch, Technical Scanner, DB Persist).
    """
    def __init__(self, pipeline_name: str, stage_name: str, inspect_objects: bool = True):
        self.pipeline_name = pipeline_name
        self.stage_name = stage_name
        self.inspect_objects = inspect_objects
        self.proc = psutil.Process(os.getpid())
        self.start_time = 0.0
        self.rss_before = 0.0

    def __enter__(self):
        self.start_time = time.monotonic()
        self.rss_before = self.proc.memory_info().rss / (1024 * 1024)
        logger.info(f"▶️ [{self.pipeline_name} TIMELINE] START Stage: {self.stage_name} | Starting RSS: {self.rss_before:.1f} MB")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed = time.monotonic() - self.start_time
        rss_after = self.proc.memory_info().rss / (1024 * 1024)
        delta = rss_after - self.rss_before
        
        logger.info("=" * 70)
        logger.info(f"📊 [{self.pipeline_name} TIMELINE] Stage: {self.stage_name}")
        logger.info(f"   Time Taken   : {elapsed:>7.2f}s")
        logger.info(f"   RSS Before   : {self.rss_before:>7.1f} MB")
        logger.info(f"   RSS After    : {rss_after:>7.1f} MB (Delta: {delta:>+6.1f} MB)")
        logger.info("=" * 70)
        
        try:
            from forensics import forensics
            forensics.take_snapshot(f"{self.pipeline_name}:{self.stage_name}", {
                "duration_sec": round(elapsed, 2),
                "rss_delta_mb": round(delta, 2)
            })
        except Exception:
            pass
        
        if self.inspect_objects and (delta > 20.0 or rss_after > 500.0):
            inspect_largest_global_objects(top_n=5)



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

def _trigger_deep_diagnostic(rss_delta_mb: float, df_delta_mb: float, stage_name: str, tracemalloc_peak_mb: float):
    now = time.monotonic()
    rate_limit = MEMORY_PROFILER_CONFIG.get("RATE_LIMIT_MINUTES", 30) * 60
    if now - ProfilerState.last_deep_diagnostic_time < rate_limit:
        return # Silently rate limit
        
    # Check bounds
    target_rss_delta = MEMORY_PROFILER_CONFIG.get("DEEP_DIAGNOSTIC_RSS_MB", 5.0)
    if "startup" in stage_name.lower() or "init" in stage_name.lower():
        target_rss_delta = 120.0  # Allow module imports and initial C-extension loading during boot

    target_df_delta = MEMORY_PROFILER_CONFIG.get("MIN_DF_DELTA_MB", 1.0)
    target_peak = MEMORY_PROFILER_CONFIG.get("MAX_TRACEMALLOC_PEAK_MB", 20.0)
    
    # Trigger if RSS spikes but DataFrame/Tracemalloc stay low
    if rss_delta_mb > target_rss_delta and df_delta_mb < target_df_delta and tracemalloc_peak_mb < target_peak:

        ProfilerState.consecutive_anomalies += 1
        ProfilerState.last_deep_diagnostic_time = now
        
        logger.warning(f"🚨 [DEEP MEMORY DIAGNOSTIC] Triggered for '{stage_name}' (Anomaly #{ProfilerState.consecutive_anomalies})")
        logger.warning(f"🚨 RSS grew by {rss_delta_mb:.1f} MB, DF changed by {df_delta_mb:+.1f} MB, Tracemalloc Peak: {tracemalloc_peak_mb:.1f} MB.")
        
        logger.warning("=== Python Object Population (gc.get_objects) ===")
        import collections
        import numpy as np
        objs = gc.get_objects()
        
        type_counts = collections.Counter(type(o).__name__ for o in objs)
        top_types = type_counts.most_common(10)
        for obj_type, count in top_types:
            logger.warning(f"  {obj_type}: {count} instances")
            
        logger.warning("=== NumPy Array Diagnostics ===")
        try:
            arrays = [o for o in objs if isinstance(o, np.ndarray)]
            count = len(arrays)
            total_bytes = sum(arr.nbytes for arr in arrays)
            logger.warning(f"  Live ndarrays: {count}")
            logger.warning(f"  Total memory : {total_bytes / (1024*1024):.2f} MB")
            
            if arrays:
                largest = sorted(arrays, key=lambda x: x.nbytes)[-5:]
                logger.warning("  Largest 5 arrays:")
                for arr in largest:
                    logger.warning(f"    Shape: {arr.shape}, dtype: {arr.dtype}, Size: {arr.nbytes / 1024:.1f} KB")
        except Exception as e:
            logger.warning(f"  Could not track NumPy arrays: {e}")
            
        # LEVEL 1: Tracemalloc and Memory Maps
        if tracemalloc.is_tracing():
            snapshot = tracemalloc.take_snapshot()
            if ProfilerState.last_snapshot is not None:
                top_stats = snapshot.compare_to(ProfilerState.last_snapshot, 'lineno')
                logger.warning("=== Top 5 Python Allocations (Delta since last snapshot) ===")
                for stat in top_stats[:5]:
                    logger.warning(f"  {stat}")
            else:
                top_stats = snapshot.statistics('lineno')
                logger.warning("=== Top 5 Python Allocations (Absolute) ===")
                for stat in top_stats[:5]:
                    logger.warning(f"  {stat}")
            ProfilerState.last_snapshot = snapshot
            
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
            logger.warning(f"Could not read memory maps: {e}")
        except Exception as e:
            logger.warning(f"  Native memory map unavailable: {e}")
            
        # LEVEL 2: GC Objects
        if ProfilerState.consecutive_anomalies >= MEMORY_PROFILER_CONFIG.get("CONSECUTIVE_TRIGGER_COUNT", 3):
            logger.warning("🚨 [LEVEL 2 DIAGNOSTIC] Repeated anomalies detected. Running GC Object Histogram...")
            try:
                from collections import Counter
                logger.warning("=== GC Object Type Counts ===")
                objs = gc.get_objects()
                type_counts = Counter(type(o).__name__ for o in objs)
                for t_name, count in type_counts.most_common(5):
                    logger.warning(f"  {t_name}: {count} objects")
            except Exception as e:
                logger.warning(f"  Failed to get GC object counts: {e}")
                
            ProfilerState.consecutive_anomalies = 0 # Reset after level 2
            
        logger.warning("==================================================")
    else:
        # Reset if the anomaly is broken
        if ProfilerState.consecutive_anomalies > 0:
            ProfilerState.consecutive_anomalies = 0



def _check_rss_thresholds(previous_rss_mb: float, current_rss_mb: float):
    # Threshold breach warnings disabled during profiling phase
    return

def _dump_threshold_snapshot(threshold: int, current_rss_mb: float):
    import json
    import threading
    from datetime import datetime
    import collections
    from config import DATA_DIR
    
    # 1. Process Memory
    process = psutil.Process(os.getpid())
    mem = process.memory_info()
    heap = 0
    anon = 0
    lib_rss = {}
    try:
        maps = process.memory_maps()
        for m in maps:
            path = m.path
            base = os.path.basename(path) if path else "[anonymous]"
            lib_rss[base] = lib_rss.get(base, 0) + m.rss
            if "[heap]" in path:
                heap += m.rss
            elif "[anon]" in path or not path:
                anon += m.rss
    except Exception:
        pass

    # 2. Python Allocations
    tracemalloc_stats = []
    tracemalloc_peak_mb = 0
    if tracemalloc.is_tracing():
        tracemalloc_peak_mb = tracemalloc.get_traced_memory()[1] / (1024 * 1024)
        snapshot = tracemalloc.take_snapshot()
        top_stats = snapshot.statistics('lineno')
        tracemalloc_stats = [str(stat) for stat in top_stats[:10]]

    # 3. DataFrames & NumPy
    df_inventory = get_dataframe_inventory()
    
    objs = gc.get_objects()
    np_arrays = [o for o in objs if isinstance(o, np.ndarray)]
    np_count = len(np_arrays)
    np_bytes = sum(arr.nbytes for arr in np_arrays) if np_arrays else 0
    
    # 4. GC Objects
    type_counts = collections.Counter(type(o).__name__ for o in objs)
    top_gc = {k: v for k, v in type_counts.most_common(10)}
    
    # 5. Thread Context
    threads = []
    for t in threading.enumerate():
        threads.append({"name": t.name, "is_daemon": t.daemon, "is_alive": t.is_alive()})
        
    # 6. Cache Statistics
    cache_stats = {}
    try:
        import constituent_service
        cache_stats["ConstituentService"] = {
            "symbol_count": constituent_service.ConstituentService.symbol_count,
            "hits": constituent_service.ConstituentService.hits,
            "misses": constituent_service.ConstituentService.misses
        }
    except Exception as e:
        cache_stats["ConstituentService_Error"] = str(e)
        
    snapshot_data = {
        "timestamp": datetime.now().isoformat(),
        "threshold_mb": threshold,
        "rss_mb": current_rss_mb,
        "heap_mb": heap / (1024 * 1024),
        "anonymous_mb": anon / (1024 * 1024),
        "tracemalloc_peak_mb": tracemalloc_peak_mb,
        "tracemalloc_top_10": tracemalloc_stats,
        "dataframes": df_inventory,
        "numpy_arrays": {
            "count": np_count,
            "memory_mb": np_bytes / (1024 * 1024)
        },
        "gc_objects_top_10": top_gc,
        "threads": threads,
        "cache_stats": cache_stats,
        "memory_maps": {k: v / (1024 * 1024) for k, v in sorted(lib_rss.items(), key=lambda x: x[1], reverse=True)[:15]}
    }
    
    os.makedirs(DATA_DIR, exist_ok=True)
    filepath = os.path.join(DATA_DIR, f"memory_snapshot_{threshold}.json")
    try:
        with open(filepath, 'w') as f:
            json.dump(snapshot_data, f, indent=4)
        logger.warning(f"✅ Saved deep diagnostic snapshot to {filepath}")
    except Exception as e:
        logger.error(f"Failed to save threshold snapshot: {e}")

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
                
                _check_rss_thresholds(start_rss, end_rss)
                
                if hasattr(mem, 'peak_wset'):
                    sys_peak = mem.peak_wset
                else:
                    sys_peak = max(start_peak, mem.rss)
                
                peak_rss = sys_peak / (1024 * 1024)
                transient_mb = max(0, peak_rss - end_rss)
                
                df_end = get_dataframe_inventory()
                
                peak_alloc_bytes = tracemalloc.get_traced_memory()[1]
                peak_alloc_mb = peak_alloc_bytes / (1024 * 1024)
                
                logger.debug(f"=== [PROFILE] {stage_name} ===")
                logger.debug(f"  Time            : {elapsed:.2f}s")
                logger.debug(f"  RSS Before      : {start_rss:.1f} MB")
                logger.debug(f"  RSS After       : {end_rss:.1f} MB")
                logger.debug(f"  RSS Delta       : {end_rss - start_rss:+.1f} MB")
                logger.debug(f"  RSS Peak        : {peak_rss:.1f} MB")
                logger.debug(f"  Transient Alloc : {transient_mb:.1f} MB (Peak - After)")
                
                
                from telemetry_manager import telemetry
                timer = telemetry.get_timer(stage_name.split()[0])
                timer.stages[stage_name] = elapsed
                logger.info("========== SCANNER COMPLETE ==========")
                logger.info(f"Scanner: {stage_name}")
                logger.info(f"Execution Time: {elapsed:.1f} sec")
                logger.info(f"Memory Before: {start_rss:.1f} MB")
                logger.info(f"Memory After: {end_rss:.1f} MB")
                logger.info(f"Memory Delta: {end_rss - start_rss:+.1f} MB")
                if hasattr(mem, 'peak_wset'):
                    logger.info(f"Peak Memory: {peak_rss:.1f} MB")
                logger.info("======================================")
                
                logger.debug(f"  Live DF Count   : {df_start['count']} -> {df_end['count']} (Delta: {df_end['count'] - df_start['count']:+d})")
                logger.debug(f"  Total DF Memory : {df_start['memory_mb']:.1f} MB -> {df_end['memory_mb']:.1f} MB")
                
                if df_end['largest_mb'] > 0:
                    logger.debug(f"  Largest DF      : {df_end['largest_mb']:.1f} MB (id={df_end['largest_id']}, {df_end['largest_rows']} rows, {df_end['largest_cols']} cols)")
                    
                rss_delta_mb = end_rss - start_rss
                df_delta_mb = df_end['memory_mb'] - df_start['memory_mb']
                _trigger_deep_diagnostic(rss_delta_mb, df_delta_mb, stage_name, peak_alloc_mb)
                
                # Session Stats
                ProfilerState.increment_loop()
                session_gain, gain_per_loop = ProfilerState.get_session_stats()
                logger.debug(f"  Session Gain    : {session_gain:+.1f} MB")
                logger.debug(f"  Rolling Gain/Lp : {gain_per_loop:+.2f} MB/loop (last 10)")
                    
                logger.debug("=================================")
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
