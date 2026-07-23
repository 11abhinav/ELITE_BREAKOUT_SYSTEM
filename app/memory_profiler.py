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
        
        # Check if this is a top-level scanner for log filtering
        name_upper = self.stage_name.upper()
        self.is_top_level = any(kw in name_upper for kw in [
            "EOD", "REVERSAL", "WEALTH", "PULLBACK", 
            "MULTI_TF", "MULTIBAGGER", "PERFORMANCE", "STARTUP"
        ])
        
        log_func = logger.info if self.is_top_level else logger.debug
        log_func(f"[MEMORY] 🟢 STARTING: {self.stage_name:<15} | Current RSS: {current_mb:>6.1f} MB")
        
        if ENABLE_PROFILING:
            self.df_start = get_dataframe_inventory()
            if not tracemalloc.is_tracing():
                tracemalloc.start()
            tracemalloc.clear_traces()
            
        rss_mb = self.start_rss / (1024 * 1024)
        vms_mb = self.process.memory_info().vms / (1024 * 1024)
        
        log_func("========== MEMORY SNAPSHOT ==========")
        log_func(f"Time: {time.strftime('%H:%M:%S')}")
        log_func(f"Scanner: {self.stage_name}")
        log_func(f"RSS Memory: {rss_mb:.1f} MB")
        log_func(f"VMS Memory: {vms_mb:.1f} MB")
        log_func("=====================================")
            
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        log_func = logger.info if self.is_top_level else logger.debug
        
        if self.force_gc_cleanup:
            collected = gc.collect()
            log_func(f"[MEMORY GC] Stage: {self.stage_name} | Reclaimed: {collected} objects")
            
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
        
        
        log_func("========== SCANNER COMPLETE ==========")
        log_func(f"Scanner: {self.stage_name}")
        log_func(f"Execution Time: {elapsed:.2f}s")
        log_func(f"Memory Before: {start_mb:.1f} MB")
        log_func(f"Memory After: {current_mb:.1f} MB")
        log_func(f"Memory Delta: {delta_str} MB")
        log_func(f"Peak Memory: {peak_mb:.1f} MB")
        log_func("======================================")
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
    pass

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
                # Use deep=False for O(1) shallow memory tracking instead of O(N) deep introspection
                total_pd_bytes += obj.memory_usage(deep=False).sum()
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
ENABLE_DEEP_INVENTORY = os.getenv("ENABLE_DEEP_INVENTORY", "False").lower() in ("true", "1", "yes")

def run_purge_with_telemetry(stage_name: str) -> float:
    # Removed as per Phase 4 LifecycleManager Memory Thresholds
    return 0.0

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
                    mem_mb = obj.memory_usage(deep=False).sum() / (1024 * 1024)
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
    
    if not ENABLE_PROFILING or not ENABLE_DEEP_INVENTORY:
        return {"count": 0, "rows": 0, "cols": 0, "memory_mb": 0.0, "largest_mb": 0.0, "largest_rows": 0, "largest_cols": 0, "largest_id": None}

    for obj in gc.get_objects():
        if isinstance(obj, pd.DataFrame):
            try:
                mem = obj.memory_usage(deep=False).sum()
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
        
        log_lines = []
        log_lines.append(f"🚨 [DEEP MEMORY DIAGNOSTIC] Triggered for '{stage_name}' (Anomaly #{ProfilerState.consecutive_anomalies})")
        log_lines.append(f"🚨 RSS grew by {rss_delta_mb:.1f} MB, DF changed by {df_delta_mb:+.1f} MB, Tracemalloc Peak: {tracemalloc_peak_mb:.1f} MB.")
        
        log_lines.append("=== Python Object Population (gc.get_objects) ===")
        import collections
        import numpy as np
        objs = gc.get_objects()
        
        type_counts = collections.Counter(type(o).__name__ for o in objs)
        top_types = type_counts.most_common(10)
        for obj_type, count in top_types:
            log_lines.append(f"  {obj_type}: {count} instances")
            
        log_lines.append("=== NumPy Array Diagnostics ===")
        try:
            arrays = [o for o in objs if isinstance(o, np.ndarray)]
            count = len(arrays)
            total_bytes = sum(arr.nbytes for arr in arrays)
            log_lines.append(f"  Live ndarrays: {count}")
            log_lines.append(f"  Total memory : {total_bytes / (1024*1024):.2f} MB")
            
            if arrays:
                largest = sorted(arrays, key=lambda x: x.nbytes)[-5:]
                log_lines.append("  Largest 5 arrays:")
                for arr in largest:
                    log_lines.append(f"    Shape: {arr.shape}, dtype: {arr.dtype}, Size: {arr.nbytes / 1024:.1f} KB")
        except Exception as e:
            log_lines.append(f"  Could not track NumPy arrays: {e}")
            
        # LEVEL 1: Tracemalloc and Memory Maps
        if tracemalloc.is_tracing():
            snapshot = tracemalloc.take_snapshot()
            if ProfilerState.last_snapshot is not None:
                top_stats = snapshot.compare_to(ProfilerState.last_snapshot, 'lineno')
                log_lines.append("=== Top 5 Python Allocations (Delta since last snapshot) ===")
                for stat in top_stats[:5]:
                    log_lines.append(f"  {stat}")
            else:
                top_stats = snapshot.statistics('lineno')
                log_lines.append("=== Top 5 Python Allocations (Absolute) ===")
                for stat in top_stats[:5]:
                    log_lines.append(f"  {stat}")
            ProfilerState.last_snapshot = snapshot
            
        try:
            log_lines.append("=== Process Memory Maps (Native Shared Libraries) ===")
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
                    log_lines.append(f"  {base}: {rss_mb:.1f} MB")
        except Exception as e:
            log_lines.append(f"Could not read memory maps: {e}")
        except Exception as e:
            log_lines.append(f"  Native memory map unavailable: {e}")
            
        # LEVEL 2: GC Objects
        if ProfilerState.consecutive_anomalies >= MEMORY_PROFILER_CONFIG.get("CONSECUTIVE_TRIGGER_COUNT", 3):
            log_lines.append("🚨 [LEVEL 2 DIAGNOSTIC] Repeated anomalies detected. Running GC Object Histogram...")
            try:
                from collections import Counter
                log_lines.append("=== GC Object Type Counts ===")
                objs = gc.get_objects()
                type_counts = Counter(type(o).__name__ for o in objs)
                for t_name, count in type_counts.most_common(5):
                    log_lines.append(f"  {t_name}: {count} objects")
            except Exception as e:
                log_lines.append(f"  Failed to get GC object counts: {e}")
                
            ProfilerState.consecutive_anomalies = 0 # Reset after level 2
            
        log_lines.append("==================================================")
        
        # Write to file instead of terminal
        try:
            os.makedirs("logs", exist_ok=True)
            with open("logs/deep_memory_diagnostics.log", "a") as f:
                f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Memory Anomaly Detected\n")
                f.write("\n".join(log_lines) + "\n")
        except Exception:
            pass
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
                
                
                logger.debug("========== PROFILER BLOCK COMPLETE ==========")
                logger.debug(f"Block: {stage_name}")
                logger.debug(f"Execution Time: {elapsed:.1f} sec")
                logger.debug(f"Memory Before: {start_rss:.1f} MB")
                logger.debug(f"Memory After: {end_rss:.1f} MB")
                logger.debug(f"Memory Delta: {end_rss - start_rss:+.1f} MB")
                if hasattr(mem, 'peak_wset'):
                    logger.debug(f"Peak Memory: {peak_rss:.1f} MB")
                logger.debug("======================================")
                
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


# =====================================================================================
# DATASET LIFECYCLE REGISTRY — Phase A: Observation Mode
# Tracks every expensive dataset fetch, reuse, and release eligibility.
# No cleanup is performed here — this is pure telemetry.
# =====================================================================================
import threading as _tlc_threading
from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class DatasetLifecycleEntry:
    dataset_name: str
    fetched_by: str
    source: str
    symbols: int = 0
    rows: int = 0
    download_time_sec: float = 0.0
    memory_mb: float = 0.0
    consumers: List[str] = field(default_factory=list)
    fetch_count_today: int = 1
    reuse_count: int = 0
    released: bool = False
    eligible_release_time: Optional[str] = None
    eligible_release_reason: Optional[str] = None
    first_fetch_time: str = ""


class DatasetLifecycleRegistry:
    """
    Thread-safe global registry for tracking dataset fetch/reuse/release lifecycle.
    Phase A: Observation only — no cleanup triggered from here.
    """
    _lock = _tlc_threading.Lock()
    _registry: dict = {}          # dataset_name → DatasetLifecycleEntry
    _scanner_reports: list = []   # (scanner_name, rss_before, rss_after, elapsed_sec)

    @classmethod
    def record_fetch(
        cls,
        dataset_name: str,
        fetched_by: str,
        source: str,
        symbols: int = 0,
        rows: int = 0,
        download_time_sec: float = 0.0,
        memory_mb: float = 0.0,
    ) -> None:
        """Called immediately after an expensive dataset is downloaded/computed."""
        from zoneinfo import ZoneInfo
        from datetime import datetime as _dt
        IST = ZoneInfo("Asia/Kolkata")
        now_str = _dt.now(IST).strftime("%H:%M:%S IST")

        with cls._lock:
            existing = cls._registry.get(dataset_name)
            if existing:
                existing.fetch_count_today += 1
                existing.symbols = symbols or existing.symbols
                existing.rows = rows or existing.rows
                existing.download_time_sec = download_time_sec or existing.download_time_sec
                existing.memory_mb = memory_mb or existing.memory_mb
                entry = existing
            else:
                entry = DatasetLifecycleEntry(
                    dataset_name=dataset_name,
                    fetched_by=fetched_by,
                    source=source,
                    symbols=symbols,
                    rows=rows,
                    download_time_sec=download_time_sec,
                    memory_mb=memory_mb,
                    first_fetch_time=now_str,
                )
                cls._registry[dataset_name] = entry

        logger.info(
            f"\n========================================\n"
            f"Dataset : {dataset_name}\n\n"
            f"Fetched By      : {fetched_by}\n"
            f"Source          : {source}\n"
            f"Symbols         : {symbols}\n"
            f"Rows/Symbol     : {rows}\n"
            f"Download Time   : {download_time_sec:.1f} sec\n"
            f"Memory          : {memory_mb:.1f} MB\n\n"
            f"Consumers       : {entry.consumers or ['(none yet)']  }\n"
            f"Fetch Count     : {entry.fetch_count_today}\n"
            f"Reused          : {entry.reuse_count}\n"
            f"Released        : {'YES' if entry.released else 'NO'}\n"
            f"Reason          : {entry.eligible_release_reason or 'In use / observation mode'}\n"
            f"========================================"
        )

    @classmethod
    def record_reuse(cls, dataset_name: str, reused_by: str) -> None:
        """Called when a scanner reads a dataset from cache (not a fresh fetch)."""
        with cls._lock:
            entry = cls._registry.get(dataset_name)
            if entry:
                entry.reuse_count += 1
                if reused_by not in entry.consumers:
                    entry.consumers.append(reused_by)
            else:
                # Dataset was cached before registry was initialized — create stub
                cls._registry[dataset_name] = DatasetLifecycleEntry(
                    dataset_name=dataset_name,
                    fetched_by="Unknown (pre-registry)",
                    source="Unknown",
                    consumers=[reused_by],
                    reuse_count=1,
                )

    @classmethod
    def record_eligible_release(
        cls,
        dataset_name: str,
        reason: str,
    ) -> None:
        """Called when a dataset's last verified consumer has completed.
        Phase A: Logs the eligibility only. Does NOT release memory."""
        from zoneinfo import ZoneInfo
        from datetime import datetime as _dt
        IST = ZoneInfo("Asia/Kolkata")
        now_str = _dt.now(IST).strftime("%H:%M:%S IST")

        with cls._lock:
            entry = cls._registry.get(dataset_name)
            if entry:
                entry.eligible_release_time = now_str
                entry.eligible_release_reason = reason

        logger.info(
            f"📋 [LIFECYCLE] '{dataset_name}' is now ELIGIBLE FOR RELEASE at {now_str}\n"
            f"   Reason: {reason}\n"
            f"   [Phase A] Memory NOT cleared — observation mode only."
        )

    @classmethod
    def record_scanner_run(
        cls,
        scanner_name: str,
        rss_before_mb: float,
        rss_after_mb: float,
        elapsed_sec: float,
        peak_mb: float = 0.0,
    ) -> None:
        """Called after each scanner completes to accumulate the EOD report data."""
        with cls._lock:
            cls._scanner_reports.append({
                "scanner": scanner_name,
                "rss_before": rss_before_mb,
                "rss_after": rss_after_mb,
                "delta": rss_after_mb - rss_before_mb,
                "peak": peak_mb or rss_after_mb,
                "elapsed_sec": elapsed_sec,
            })

    @classmethod
    def reset_daily(cls) -> None:
        """Clears the registry at start of each trading day (called at midnight)."""
        with cls._lock:
            cls._registry.clear()
            cls._scanner_reports.clear()

    @classmethod
    def get_dataset_table(cls) -> list:
        """Returns list of dicts for the EOD report dataset table."""
        with cls._lock:
            rows = []
            for name, e in cls._registry.items():
                rows.append({
                    "dataset": name,
                    "fetches": e.fetch_count_today,
                    "reuses": e.reuse_count,
                    "peak_mb": e.memory_mb,
                    "released": "Yes" if e.released else "No",
                    "eligible_at": e.eligible_release_time or "—",
                })
            return rows


def generate_eod_memory_report() -> None:
    """
    Generates and writes the end-of-day memory report to logs/eod_memory_report_YYYY-MM-DD.log.
    Called from main.py after pb_thread.join() (all evening scanners complete).
    Phase A: Pure observation — no cleanup actions taken.
    """
    from zoneinfo import ZoneInfo
    from datetime import datetime as _dt
    import os
    IST = ZoneInfo("Asia/Kolkata")
    now = _dt.now(IST)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S IST")

    # Gather RSS snapshot
    proc = psutil.Process(os.getpid())
    current_rss = proc.memory_info().rss / (1024 * 1024)

    # Gather scanner reports
    scanner_rows = DatasetLifecycleRegistry._scanner_reports

    # Gather dataset lifecycle table
    dataset_rows = DatasetLifecycleRegistry.get_dataset_table()

    # Gather cache stats from telemetry if available
    cache_hits = cache_misses = 0
    cache_keys = cache_entries = cache_mem_mb = 0
    try:
        from telemetry_manager import telemetry
        cache_hits = 0
        cache_misses = 0
    except Exception:
        pass
    try:
        from price_cache import get_price_cache_stats
        stats = get_price_cache_stats()
        cache_keys = stats.get('keys', 0)
        cache_entries = stats.get('entries', 0)
        cache_mem_mb = stats.get('memory_mb', 0.0)
    except Exception:
        pass

    # Build report string
    lines = [
        f"{'=' * 60}",
        f"  END-OF-DAY MEMORY REPORT — {date_str} @ {time_str}",
        f"  Phase A: Observation Mode — No cache clearing performed",
        f"{'=' * 60}",
        "",
        "SYSTEM RSS SUMMARY",
        f"  Current RSS at Report Time : {current_rss:>7.1f} MB",
        "",
    ]

    if scanner_rows:
        lines += [
            "SCANNER MEMORY SUMMARY",
            f"  {'Scanner':<25} {'Time':>8}  {'Before':>8}  {'After':>8}  {'Delta':>7}  {'Peak':>8}",
            f"  {'-'*25} {'-'*8}  {'-'*8}  {'-'*8}  {'-'*7}  {'-'*8}",
        ]
        all_rss = [r['rss_before'] for r in scanner_rows] + [r['rss_after'] for r in scanner_rows]
        for r in scanner_rows:
            mins = int(r['elapsed_sec'] // 60)
            secs = r['elapsed_sec'] % 60
            time_fmt = f"{mins}m {secs:.0f}s" if mins else f"{secs:.1f}s"
            delta_str = f"{r['delta']:+.1f}"
            lines.append(
                f"  {r['scanner']:<25} {time_fmt:>8}  {r['rss_before']:>7.1f}M  "
                f"{r['rss_after']:>7.1f}M  {delta_str:>7}M  {r['peak']:>7.1f}M"
            )
        if all_rss:
            lines += [
                "",
                f"  Peak RSS (all scanners)   : {max(r['peak'] for r in scanner_rows):>7.1f} MB",
                f"  Min  RSS (all scanners)   : {min(all_rss):>7.1f} MB",
            ]
        lines.append("")

    if dataset_rows:
        lines += [
            "DATASET LIFECYCLE TABLE",
            f"  {'Dataset':<22} {'Fetches':>7}  {'Reuses':>6}  {'Peak MB':>7}  {'Released':>8}  {'Eligible At':>12}",
            f"  {'-'*22} {'-'*7}  {'-'*6}  {'-'*7}  {'-'*8}  {'-'*12}",
        ]
        for r in dataset_rows:
            lines.append(
                f"  {r['dataset']:<22} {r['fetches']:>7}  {r['reuses']:>6}  "
                f"{r['peak_mb']:>7.1f}  {r['released']:>8}  {r['eligible_at']:>12}"
            )
        lines.append("")

    total_cache_calls = cache_hits + cache_misses
    hit_pct = (cache_hits / total_cache_calls * 100) if total_cache_calls > 0 else 0.0
    lines += [
        "PRICE CACHE STATISTICS",
        f"  Keys     : {cache_keys}",
        f"  Entries  : {cache_entries}",
        f"  Memory   : {cache_mem_mb:.1f} MB",
        f"  Hits     : {cache_hits}  |  Misses: {cache_misses}  |  Hit Rate: {hit_pct:.1f}%",
        "",
        "NOTES",
        "  Phase A — Observation Mode",
        "  No cache clearing has been performed in this deployment.",
        "  Review 'Eligible At' column to identify safe release windows.",
        "  Enable targeted cleanup in Phase B after 2-3 session validation.",
        f"{'=' * 60}",
    ]

    report = "\n".join(lines)

    # Log to standard logger
    logger.info("\n" + report)

    # Write to file
    try:
        log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
        os.makedirs(log_dir, exist_ok=True)
        report_path = os.path.join(log_dir, f"eod_memory_report_{date_str}.log")
        with open(report_path, "a", encoding="utf-8") as f:
            f.write(report + "\n\n")
        logger.info(f"📊 [EOD REPORT] Written to {report_path}")
    except Exception as e:
        logger.warning(f"⚠️ [EOD REPORT] Could not write to file: {e}")

